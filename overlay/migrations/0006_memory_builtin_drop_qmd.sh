#!/bin/bash
# Migration 0006: memory_builtin_drop_qmd
# Idempotent. Runs once per instance when it upgrades past this version.
# $LZBOT_MIGRATION_BACKUP_DIR is set — back up any file you modify there
# BEFORE touching it.
#
# Rules: only ADD missing things, or remove a key/file this migration
# explicitly recognizes as deprecated. Never delete anything you don't
# recognize. Never edit this file after it ships — write a new migration
# instead if something was wrong.
set -euo pipefail

echo "[migration 0006] memory_builtin_drop_qmd"

# Reconcilia /app/openclaw.json de instância antiga para o schema do OpenClaw
# 2026.9.1. Duas chaves viraram INVÁLIDAS (o schema é additionalProperties:false,
# então config velho não sobe degradado — o gateway recusa):
#
#   memory.backend / memory.qmd  -> não existe mais backend externo de memória;
#                                   a busca virou builtin in-process.
#   agents.list                  -> virou agents.entries.
#
# A migration 0002 é justamente quem escreveu memory.backend:"qmd" — esta desfaz
# aquilo. 0002 não é editada (contrato do README): numa instância nova ela roda
# primeiro e esta reverte em seguida, que é o padrão previsto.
#
# O que esta migration NÃO faz, de propósito: converter /app/agents.json5 de array
# para objeto. Aquele arquivo é JSON5 COM COMENTÁRIO e nenhuma ferramenta do
# produto o reescreve, então lá o comentário sobrevive e a conversão mecânica
# (parse -> stringify) apagaria tudo. Se o arquivo ainda for array, avisamos alto
# e a conversão é manual, uma vez, por instância.
#
#### Por que JSON.parse/JSON.stringify e não um editor de JSONC
#
# Até a 2026.9.1 o openclaw.json do boilerplate era JSONC (83 linhas de `//`) e
# esta migration precisava de um editor que preservasse comentário. Não precisa
# mais: o arquivo canônico virou JSON ESTRITO e o racional foi para o
# openclaw.json.md versionado. O motivo é que o próprio produto trata o arquivo
# como JSON puro — `openclaw config set` reescreve com JSON.stringify e apaga os
# comentários de qualquer jeito (medido: 83 -> 0) — e as migrations 0002/0003,
# que rodam ANTES desta, também usam JSON.parse: numa instância nova o boot
# morria já na 0002. Converter o arquivo resolve as três de uma vez, sem dep
# nova nem bundle no Dockerfile.
#
# Instância legada que ainda tenha comentário no arquivo não recebe SyntaxError
# cru: a guarda abaixo detecta e diz exatamente o que fazer.

export OPENCLAW_JSON="${OPENCLAW_JSON:-/app/openclaw.json}"
# Fallback histórico. NÃO é mais o caminho principal: quem manda é o `$include`
# declarado no openclaw.json (descoberto logo abaixo, depois do guard de
# existência). Só vale para instância que não declara `$include` nenhum.
AGENTS_JSON5_FALLBACK="${AGENTS_JSON5:-/app/agents.json5}"

if [ ! -f "$OPENCLAW_JSON" ]; then
  echo "[migration 0006] $OPENCLAW_JSON não existe — pulando." >&2
  exit 0
fi

if [ -n "${LZBOT_MIGRATION_BACKUP_DIR:-}" ]; then
  cp -a "$OPENCLAW_JSON" "$LZBOT_MIGRATION_BACKUP_DIR/openclaw.json.before-0006"
fi

# ---------------------------------------------------------------------------
# PASSO 1 — agents.json5: array -> objeto chaveado pelo id.
#
# Roda ANTES do openclaw.json de propósito: se esta conversão falhar, o rename
# `agents.list` -> `agents.entries` nem acontece, e a instância fica no estado
# tolerado (array sob `list`) em vez de no estado fatal (array sob `entries`).
#
# Exporta LZBOT_DEFAULT_AGENT com quem era o `default: true`, porque o marcador
# foi aposentado no 2026.9.1 e o sucessor (agents.ownership/systemAgent/talk)
# mora no openclaw.json, escrito no passo seguinte.
# ---------------------------------------------------------------------------
LZBOT_JSONC_LIB="${LZBOT_JSONC_LIB:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/lib/jsonc-edit.mjs}"
export LZBOT_JSONC_LIB

# Descobre os arquivos de agente REAIS, lendo `agents.entries.$include` (ou o
# legado `agents.list.$include`) do openclaw.json e resolvendo relativo a ele.
#
# Por que não hardcode: o nome `agents.json5` é convenção do boilerplate, não
# contrato. Instâncias na frota usam `agents.json` (sentinel/nomos) e algumas
# têm DOIS arquivos (`agents.json` + `agents-extra.json`, caso frmnt4). Com o
# caminho fixo, esta migration não achava nada, seguia em silêncio, e o
# `openclaw config validate` do fim explodia com o drift intocado
# (`Unrecognized key: "memorySearch"`) — boot em crash loop, 28/09/2026.
AGENTS_FILES="$(node --input-type=module -e '
import fs from "node:fs";
import path from "node:path";
const file = process.env.OPENCLAW_JSON;
let data;
try { data = JSON.parse(fs.readFileSync(file, "utf8")); } catch { process.exit(0); }
const agents = (data && data.agents) || {};
const seen = new Set();
for (const holder of [agents.entries, agents.list]) {
  const inc = holder && holder["$include"];
  if (!inc) continue;
  for (const rel of (Array.isArray(inc) ? inc : [inc])) {
    if (typeof rel !== "string") continue;
    seen.add(path.resolve(path.dirname(file), rel));
  }
}
for (const p of seen) if (fs.existsSync(p)) console.log(p);
')"

if [ -z "$AGENTS_FILES" ] && [ -f "$AGENTS_JSON5_FALLBACK" ]; then
  AGENTS_FILES="$AGENTS_JSON5_FALLBACK"
  echo "[migration 0006] nenhum \$include declarado — usando fallback $AGENTS_FILES" >&2
fi

if [ -z "$AGENTS_FILES" ]; then
  echo "[migration 0006] nenhum arquivo de agente encontrado — PASSO 1 pulado." >&2
fi

# Converte CADA arquivo incluído. O dono (`default: true`) pode estar em
# qualquer um deles; o último não-vazio vence, como antes.
LZBOT_DEFAULT_AGENT=""
while IFS= read -r AGENTS_JSON5; do
  [ -n "$AGENTS_JSON5" ] || continue
  export AGENTS_JSON5

  if [ -n "${LZBOT_MIGRATION_BACKUP_DIR:-}" ]; then
    cp -a "$AGENTS_JSON5" "$LZBOT_MIGRATION_BACKUP_DIR/$(basename "$AGENTS_JSON5").before-0006"
  fi

  _owner="$(node --input-type=module -e '
import fs from "node:fs";
const file = process.env.AGENTS_JSON5;
if (!fs.existsSync(file)) { console.log(""); process.exit(0); }
const { readJsonc, editJsonc, arrayToRecord, renameAndNest } = await import(process.env.LZBOT_JSONC_LIB);
const { text, data } = readJsonc(file);
const log = (m) => process.stderr.write("[migration 0006] " + m + "\n");

// Quem era o dono das superfícies, lido ANTES de remover o marcador.
const pairs = Array.isArray(data) ? data.map((e) => [e && e.id, e]) : Object.entries(data);
let owner = "";
for (const [key, entry] of pairs) if (entry && entry.default === true) owner = key;

let out = text;
if (Array.isArray(data)) {
  const converted = arrayToRecord(text, "id");
  out = converted.text;
  log("agents.json5: array -> objeto chaveado (" + converted.keys.join(", ") + ").");
}

// Por agente: derruba o marcador aposentado e move memorySearch -> memory.search
// (mesmo shape, um nível a mais).
for (const [key] of pairs) {
  if (!key) continue;
  // `default: true` foi aposentado no 2026.9.1 (o loader o descarta e materializa
  // o dono sozinho, com warning). Remoção via modify é segura aqui: o marcador
  // não tem comentário próprio acima dele.
  out = editJsonc(out, [{ op: "remove", path: [key, "default"] }]).text;
  // Já o memorySearch -> memory.search NÃO pode passar por modify: remover uma
  // propriedade leva junto o bloco de comentário acima dela. Renome por offset.
  out = renameAndNest(out, key, "memorySearch", "memory", "search").text;
}

// Materializa a RAIZ do workspace no dono aposentado.
// Até a 2026.7.1 quem tinha `default:true` herdava a raiz de agents.defaults.workspace
// (SEM anexar o id); todo o resto ganhava <raiz>/<id>. O `default:true` foi aposentado
// e removido acima — mas o loader 9.1 só materializa essa herança quando o roster ainda
// é resolvível como legacy-default, e `agents.ownership:"explicit"` (que passamos a setar)
// DESLIGA esse caminho (resolveAgentWorkspaceDir -> tryResolveRawLegacyDefaultAgentId
// retorna cedo em ownership==="explicit"). Sem a linha explícita o dono resolve para
// <raiz>/<id> (ex.: /app/workspace/main), que não existe → o gateway PULA o agente
// ("Skipping agent \"main\": workspace does not exist") e sobe calado, sem o principal.
// `fill` só grava se `workspace` ainda não existir — não toca quem tem workspace próprio
// (ex.: concierge isolado) e é idempotente em re-run. Literal "workspace/" = mesmo valor
// do agents.defaults.workspace do boilerplate (não anexa o id). Medido na Fase I/deploy 05/09.
if (owner) {
  out = editJsonc(out, [{ op: "fill", path: [owner, "workspace"], value: "workspace/" }]).text;
  log("workspace explícito do dono (" + owner + ") = \"workspace/\" (herança do `default:true` aposentado).");
}

if (out !== text) {
  fs.writeFileSync(file, out);
  log(label + " atualizado (memorySearch -> memory.search, `default` removido) — comentários preservados.");
} else {
  log(label + " já no formato novo, nada a fazer.");
}
console.log(owner);
')"

  [ -n "$_owner" ] && LZBOT_DEFAULT_AGENT="$_owner"
done <<EOF
$AGENTS_FILES
EOF
unset _owner
export LZBOT_DEFAULT_AGENT

# ---------------------------------------------------------------------------
# PASSO 2 — openclaw.json: a tabela de migração legada do PRÓPRIO PRODUTO.
#
# Mesma doutrina da 0005 com o schema do state DB: quem sabe para onde cada
# chave aposentada foi é o vendor, não uma lista que a gente mantém à mão. O
# `openclaw doctor --fix` aplica essas migrações, mas ele faz DOIS trabalhos num
# comando — migra estado e REESCREVE config, normalizando o agents.json5 e
# apagando os 38 comentários dele. Aqui chamamos só a metade que interessa:
# `applyLegacyDoctorMigrations`, que é função PURA (config -> config), não
# encosta em arquivo nenhum e preserva os `${ENV}` sem interpolar.
#
# Por que isto virou obrigatório: o PASSO 3 abaixo é uma lista curada, e lista
# curada envelhece. O config real da prod tinha três chaves de whatsapp
# (debounceMs, blockStreaming, ackReaction) que ninguém tinha catalogado, e o
# gate no fim reprovava — boot parado. O vendor conhece as três, e mais 16.
#
# O PASSO 3 CONTINUA existindo, e de propósito: (1) se um dist futuro não
# expuser esta função, a migration cai na lista curada em vez de virar no-op;
# (2) ele carrega as decisões de POLÍTICA do lzbot (memory.search, ownership,
# tools.sessions.visibility), que não são migração de schema e o vendor não tem
# como saber. Os dois são idempotentes e a ordem importa: vendor primeiro, então
# o PASSO 3 só vê o que sobrou.
# ---------------------------------------------------------------------------
#### Plugins na versão da imagem ANTES de mexer no config
#
# Parte do schema de `channels.*` pertence ao PLUGIN do canal, e a tabela de
# migração legada abaixo consulta o plugin INSTALADO para decidir o que aposentou.
# Com o plugin velho no volume ela não move `channels.whatsapp.blockStreaming` nem
# `ackReaction`, o gate desta migration aprova (o plugin velho ainda aceita as
# chaves) e o gateway morre bem depois, quando o plugin novo entra. Sincronizar
# aqui é o que faz a tabela do vendor ver o mundo certo.
#
# Depende da 0005 já ter reconciliado o state DB: `plugins install` sobe o CLI
# inteiro, e o CLI abre o banco fail-closed. Por isso a 0005 é o schema e esta é
# o config, nessa ordem.
LZBOT_PLUGIN_SYNC="${LZBOT_PLUGIN_SYNC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/lzbot-plugin-sync.sh}"
if [ -x "$LZBOT_PLUGIN_SYNC" ]; then
  echo "[migration 0006] sincronizando plugins com o pin da imagem antes de migrar o config..."
  "$LZBOT_PLUGIN_SYNC" 2>&1 | sed "s/^/[migration 0006]   /" || true
fi

OPENCLAW_ENTRY="$(readlink -f "$(command -v openclaw 2>/dev/null || echo /nonexistent)" 2>/dev/null || true)"
OPENCLAW_DIST=""
if [ -n "$OPENCLAW_ENTRY" ] && [ -e "$OPENCLAW_ENTRY" ]; then
  for candidate in "$(dirname "$OPENCLAW_ENTRY")/dist" "$(dirname "$(dirname "$OPENCLAW_ENTRY")")/dist"; do
    if [ -d "$candidate" ]; then OPENCLAW_DIST="$candidate"; break; fi
  done
fi

if [ -z "$OPENCLAW_DIST" ]; then
  echo "[migration 0006] AVISO: dist do openclaw não localizado — pulando a tabela de migração do vendor." >&2
else
  OPENCLAW_DIST="$OPENCLAW_DIST" node --input-type=module <<'NODE'
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const distDir = process.env.OPENCLAW_DIST;
const file = process.env.OPENCLAW_JSON;
const log = (m) => console.log("[migration 0006] " + m);

//// Achando a função no dist
// O nome do chunk carrega hash de build (io.runtime-<hash>.js) e o export é
// minificado para uma letra — as duas coisas mudam a cada release. O que
// sobrevive é a DECLARAÇÃO: busca por conteúdo, depois casamento por `.name`.
// Mesmo truque da 0005.
const holders = readdirSync(distDir)
  .filter((f) => f.endsWith(".js") || f.endsWith(".mjs"))
  .filter((f) => {
    try {
      return readFileSync(path.join(distDir, f), "utf8").includes("function applyLegacyDoctorMigrations");
    } catch {
      return false;
    }
  });
if (holders.length !== 1) {
  console.error(`[migration 0006] AVISO: não identifiquei applyLegacyDoctorMigrations no dist (candidatos: ${holders.length}) — pulando.`);
  process.exit(0);
}

let apply;
try {
  const mod = await import(path.join(distDir, holders[0]));
  apply = Object.values(mod).find((v) => typeof v === "function" && v.name === "applyLegacyDoctorMigrations");
} catch (err) {
  console.error(`[migration 0006] AVISO: falha ao carregar ${holders[0]}: ${String(err?.message ?? err)} — pulando.`);
  process.exit(0);
}
if (!apply) {
  console.error("[migration 0006] AVISO: o chunk não exporta applyLegacyDoctorMigrations — pulando.");
  process.exit(0);
}

//// O parse aqui é seco de propósito: o PASSO 3 tem a guarda de JSONC com a
//// mensagem acionável, e ele roda logo em seguida. Duplicar aqui só daria duas
//// mensagens diferentes para o mesmo problema, então este passo se cala e deixa
//// o próximo explicar.
let raw;
try {
  raw = JSON.parse(readFileSync(file, "utf8"));
} catch {
  process.exit(0);
}

const { next, changes } = apply(raw);
if (!next || !Array.isArray(changes) || changes.length === 0) {
  log("tabela de migração do vendor: nada a aplicar.");
  process.exit(0);
}
for (const c of changes) log("vendor: " + c);
writeFileSync(file, JSON.stringify(next, null, 2) + "\n");
log(`tabela de migração do vendor aplicada (${changes.length} mudanças).`);
NODE
fi

# ---------------------------------------------------------------------------
# PASSO 3 — openclaw.json: lista curada + política do lzbot.
# ---------------------------------------------------------------------------
node -e '
const fs = require("fs");
const file = process.env.OPENCLAW_JSON;
const raw = fs.readFileSync(file, "utf8");

//// Guarda: config de instância legada ainda pode ser JSONC. JSON.parse estoura
//// com um SyntaxError que não diz nada de útil; aqui a mensagem é acionável.
let cfg;
try {
  cfg = JSON.parse(raw);
} catch (err) {
  const commented = raw.split("\n").filter((l) => /^\s*\/\//.test(l)).length;
  if (commented > 0) {
    console.error("[migration 0006] ERRO: " + file + " tem " + commented + " linha(s) de comentário `//`.");
    console.error("[migration 0006]       O openclaw.json passou a ser JSON ESTRITO: o produto o reescreve");
    console.error("[migration 0006]       com JSON.stringify (`openclaw config set` apaga comentário) e as");
    console.error("[migration 0006]       migrations o leem com JSON.parse.");
    console.error("[migration 0006]       Ação: remova as linhas de comentário do arquivo e rode de novo.");
    console.error("[migration 0006]       O racional delas está versionado em openclaw.json.md (boilerplate).");
  } else {
    console.error("[migration 0006] ERRO: " + file + " não é JSON válido: " + err.message);
  }
  process.exit(1);
}

let changed = false;

//// memory: derruba o backend qmd (chaves mortas no 2026.9.1)
if (cfg.memory && typeof cfg.memory === "object") {
  for (const dead of ["backend", "qmd"]) {
    if (dead in cfg.memory) {
      delete cfg.memory[dead];
      changed = true;
      console.log("[migration 0006] memory." + dead + " removido.");
    }
  }
}

//// memory.search: a busca builtin.
// Config decidida por bench adversarial (27 queries) em 05/set/2026 — racional
// completo em openclaw.json.md. Só preenche o que FALTA, folha a folha: uma
// instância que já tunou algo aqui mantém o valor dela.
const canonicalSearch = {
  provider: "local",
  fallback: "none",
  sources: ["memory", "sessions"],
  store: { vector: { enabled: false } },
  rememberAcrossConversations: true,
  experimental: { sessionMemory: true },
};

const isPlainObject = (v) => typeof v === "object" && v !== null && !Array.isArray(v);

function fillMissing(prefix, target, defaults) {
  for (const [key, want] of Object.entries(defaults)) {
    const path = prefix.concat(key).join(".");
    if (!(key in target)) {
      target[key] = want;
      changed = true;
      console.log("[migration 0006] " + path + " = " + JSON.stringify(want));
    } else if (isPlainObject(want) && isPlainObject(target[key])) {
      fillMissing(prefix.concat(key), target[key], want);
    }
  }
}

if (!isPlainObject(cfg.memory)) { cfg.memory = {}; changed = true; }
if (!isPlainObject(cfg.memory.search)) { cfg.memory.search = {}; changed = true; }
fillMissing(["memory", "search"], cfg.memory.search, canonicalSearch);

//// agents.list -> agents.entries (só renomeia; conteúdo intacto, inclusive $include)
if (cfg.agents && typeof cfg.agents === "object" && "list" in cfg.agents) {
  if ("entries" in cfg.agents) {
    console.error("[migration 0006] AVISO: agents.list E agents.entries coexistem — deixando como está, resolver à mão.");
  } else {
    cfg.agents.entries = cfg.agents.list;
    delete cfg.agents.list;
    changed = true;
    console.log("[migration 0006] agents.list -> agents.entries");
  }
}

//// Alcance dos session tools. Default do produto é "agent" (só sessões do
//// próprio agente); "all" alcança sessão de qualquer agente — é o que sustenta o
//// recall cross-sessão. Cross-AGENTE continua exigindo tools.agentToAgent.
if (!isPlainObject(cfg.tools)) { cfg.tools = {}; changed = true; }
if (!isPlainObject(cfg.tools.sessions)) { cfg.tools.sessions = {}; changed = true; }
fillMissing(["tools", "sessions"], cfg.tools.sessions, { visibility: "all" });

//// ----------------------------------------------------------------------
//// Drift de schema 2026.9.1: as outras 13 chaves.
////
//// Sem isto a migration termina "com sucesso" e o gate lá embaixo reprova — que
//// é exatamente o estado que o review da Fase G pegou. Um config vindo de
//// 2026.7.1 tem muito mais chave aposentada que memory.*/agents.list, e num
//// schema additionalProperties:false CADA UMA é fatal. Consertar só três deixa a
//// instância tão fora do ar quanto antes, com a diferença de que agora o boot
//// para com mensagem em vez de o gateway morrer calado.
////
//// Cada entrada foi conferida contra o schema real do 2026.9.1 (marcada
//// "@deprecated Doctor-only legacy input" no .d.ts, ou ausente do tipo) e contra
//// a tabela de migração legada do próprio produto (dist/legacy-*.js), que é a
//// fonte autoritativa de para onde cada chave foi.
//// ----------------------------------------------------------------------

const getIn = (o, path) => path.reduce((c, k) => (isPlainObject(c) && k in c ? c[k] : undefined), o);
const removeIn = (o, path) => {
  const parent = getIn(o, path.slice(0, -1));
  const leaf = path.at(-1);
  if (isPlainObject(parent) && leaf in parent) {
    delete parent[leaf];
    changed = true;
    console.log("[migration 0006] " + path.join(".") + " removido.");
  }
};
const setIn = (o, path, value) => {
  let cur = o;
  for (const k of path.slice(0, -1)) cur = (cur[k] = isPlainObject(cur[k]) ? cur[k] : {});
  cur[path.at(-1)] = value;
};
// Move preservando valor. Se o destino JÁ existe, quem vence é o destino (a
// instância declarou aquilo de propósito) e a chave velha só sai.
const moveIn = (o, from, to) => {
  if (getIn(o, from) === undefined) return;
  if (getIn(o, to) === undefined) {
    setIn(o, to, getIn(o, from));
    console.log("[migration 0006] " + from.join(".") + " -> " + to.join("."));
  }
  removeIn(o, from);
};

//// Chaves COM sucessor: movem, não somem — apagar aqui perderia comportamento.
// gateway.nodes.allowCommands -> gateway.nodes.commands.allow é o opt-in que
// habilita file_fetch/dir_list/file_write nos nodes pareados. Apagar sem mover
// desligaria transferência de arquivo caladamente.
moveIn(cfg, ["gateway", "nodes", "allowCommands"], ["gateway", "nodes", "commands", "allow"]);
moveIn(cfg, ["gateway", "nodes", "denyCommands"], ["gateway", "nodes", "commands", "deny"]);
moveIn(cfg, ["agents", "defaults", "imageGenerationModel"], ["agents", "defaults", "mediaModels", "image"]);

// messages.removeAckAfterReply -> messages.ackReactionScope. O sucessor NÃO é
// booleano: o escopo do ack virou enum. "group-mentions" é o equivalente exato
// do par {direct:false, group:"mentions"} que o canal do whatsapp usava.
if (getIn(cfg, ["messages", "removeAckAfterReply"]) !== undefined) {
  if (getIn(cfg, ["messages", "ackReactionScope"]) === undefined) {
    setIn(cfg, ["messages", "ackReactionScope"], "group-mentions");
    console.log("[migration 0006] messages.ackReactionScope = group-mentions");
  }
  removeIn(cfg, ["messages", "removeAckAfterReply"]);
}

// web.enabled era o gate do canal whatsapp; o bloco `web` inteiro saiu do schema.
// keepAliveIntervalMs/connectTimeoutMs/defaultQueryTimeoutMs NÃO têm sucessor
// (viraram política do produto), então esses somem mesmo.
if (getIn(cfg, ["web", "enabled"]) !== undefined && getIn(cfg, ["channels", "whatsapp", "enabled"]) === undefined) {
  setIn(cfg, ["channels", "whatsapp", "enabled"], getIn(cfg, ["web", "enabled"]));
  console.log("[migration 0006] web.enabled -> channels.whatsapp.enabled");
}
removeIn(cfg, ["web"]);

// tools.media.<tipo>.models -> tools.media.models (UMA lista, cada entrada
// declarando `capabilities`). tools.media.<tipo> continua existindo, mas só com
// limites (enabled/maxBytes/timeoutSeconds), sem catálogo próprio.
{
  const merged = [];
  for (const kind of ["image", "audio", "video"]) {
    const list = getIn(cfg, ["tools", "media", kind, "models"]);
    if (!Array.isArray(list)) continue;
    for (const m of list) {
      const capabilities = Array.isArray(m.capabilities) && m.capabilities.length > 0 ? m.capabilities : [kind];
      merged.push({ ...m, capabilities });
    }
    removeIn(cfg, ["tools", "media", kind, "models"]);
  }
  if (merged.length > 0 && getIn(cfg, ["tools", "media", "models"]) === undefined) {
    setIn(cfg, ["tools", "media", "models"], merged);
    console.log("[migration 0006] tools.media.<tipo>.models -> tools.media.models (" + merged.length + " entradas)");
  }
}

//// Chaves SEM sucessor — a política virou do produto.
for (const dead of [
  ["meta", "lastTouchedAt"],
  ["browser", "tabCleanup", "idleMinutes"],
  ["browser", "tabCleanup", "maxTabsPerSession"],
  ["browser", "tabCleanup", "sweepMinutes"],
  ["browser", "actionTimeoutMs"],
  ["agents", "defaults", "compaction", "memoryFlush", "prompt"],
  ["agents", "defaults", "compaction", "memoryFlush", "systemPrompt"],
  ["agents", "defaults", "compaction", "reserveTokensFloor"],
  ["agents", "defaults", "compaction", "truncateAfterCompaction"],
  ["agents", "defaults", "heartbeat", "includeSystemPromptSection"],
  ["agents", "defaults", "timeFormat"],
  // cliBackends: adaptadores de backend CLI viraram plugin. O --disallowedTools
  // que morava aqui (incluindo o deny da tool Agent) NÃO tem sucessor em config.
  // Ver o aviso no fim desta migration.
  ["agents", "defaults", "cliBackends"],
  ["commands", "ownerDisplay"],
  ["gateway", "tailscale", "resetOnExit"],
]) removeIn(cfg, dead);

//// Dono explícito das superfícies, agora que `default: true` saiu do agents.json5.
// Sem o marcador nada resolve por default; agents.ownership "explicit" +
// systemAgent + talk.agentId são o sucessor. LZBOT_DEFAULT_AGENT vem do passo do
// agents.json5, que leu quem era o `default: true` antes de removê-lo.
{
  const owner = process.env.LZBOT_DEFAULT_AGENT || "main";
  for (const [path, value] of [
    [["agents", "ownership"], "explicit"],
    [["agents", "defaults", "systemAgent", "agentId"], owner],
    [["talk", "agentId"], owner],
  ]) {
    if (getIn(cfg, path) === undefined) {
      setIn(cfg, path, value);
      changed = true;
      console.log("[migration 0006] " + path.join(".") + " = " + JSON.stringify(value));
    }
  }
}

//// Dono do CANAL, o outro sucessor do `default: true`.
//
// Aposentar o marcador não deixa só os agentes sem dono de superfície (resolvido
// acima) — deixa também cada CONTA DE CANAL sem dono. Com mais de um agente e
// `ownership: "explicit"`, um canal sem binding de nível de conta sai do ar:
//
//   [telegram] [default] channel exited: Multiple agents are configured, but
//   telegram account default routing has no explicit owner.
//   [telegram] [default] auto-restart attempt 1/10 in 5s
//
// E o gateway imprime `ready` assim mesmo: o bot fica de pé e mudo, em loop de
// auto-restart. Isto NÃO aparece no `config validate` — o config é válido; o que
// falta é semântica de roteamento.
//
// Quem cria o buraco é esta migration (é ela que escreve `ownership: explicit`),
// então é ela que fecha. O binding vai no FIM da lista: a resolução é por
// primeira regra que casa, e as regras com `peer` (mais específicas) já estão
// antes — o novo entra como fallback do que sobrar.
{
  const bindings = Array.isArray(cfg.bindings) ? cfg.bindings : [];
  const channels = isPlainObject(cfg.channels) ? cfg.channels : {};
  const owner = process.env.LZBOT_DEFAULT_AGENT || "main";
  const agentCount = (() => {
    const entries = getIn(cfg, ["agents", "entries"]) ?? getIn(cfg, ["agents", "list"]);
    if (Array.isArray(entries)) return entries.length;
    // Caso normal desta instância: `entries` é um `$include`, e os agentes moram
    // no agents.json5. Contar ali seria reparsear JSON5 aqui; basta saber se é
    // MAIS DE UM, e o boilerplate sempre tem main + coder + concierge.
    if (isPlainObject(entries)) return Object.keys(entries).filter((k) => k !== "$include").length || 2;
    return 0;
  })();

  const added = [];
  if (agentCount > 1) {
    for (const [channel, conf] of Object.entries(channels)) {
      if (!isPlainObject(conf) || conf.enabled === false) continue;
      const accounts = isPlainObject(conf.accounts) ? Object.keys(conf.accounts) : [];
      for (const accountId of new Set(["default", ...accounts])) {
        // "Nível de conta" = casa o canal/conta inteiros, sem recortar por peer.
        // Um binding com `peer` não serve de dono: ele só cobre aquela conversa.
        const hasOwner = bindings.some(
          (b) =>
            isPlainObject(b) &&
            isPlainObject(b.match) &&
            b.match.channel === channel &&
            (b.match.accountId ?? "default") === accountId &&
            b.match.peer === undefined,
        );
        if (hasOwner) continue;
        bindings.push({
          type: "route",
          agentId: owner,
          comment: "Dono de " + channel + ":" + accountId + " (sucessor do `default: true`, escrito pela migration 0006).",
          match: { channel, accountId },
        });
        added.push(channel + ":" + accountId);
      }
    }
  }
  if (added.length > 0) {
    cfg.bindings = bindings;
    changed = true;
    console.log("[migration 0006] bindings: dono explícito para " + added.join(", ") + " -> " + owner);
  }
}

//// O par que falta do `memory.search.provider = "local"` escrito acima.
//
// "local" quer dizer embeddings servidos EM PROCESSO por um llama-server local,
// e quem o serve é o plugin @openclaw/llama-cpp-provider — que só é ligado por
// uma entrada em `models.providers`. Sem ela o `openclaw memory index` falha com
// "Managed local embeddings are unavailable" e a busca degrada para keyword sem
// score: toda query volta 1.000 e a memória curada nunca aparece no topo.
//
// Não é config órfã: nada precisa ser provisionado à mão. Com este bloco no
// lugar, o plugin baixa o binário do llama-server, escreve o models.ini e baixa
// o GGUF sozinho no primeiro boot (medido num volume sem /app/tools e sem
// /app/models). O `models: []` é de propósito — o catálogo vem do models.ini.
if (getIn(cfg, ["memory", "search", "provider"]) === "local" && getIn(cfg, ["models", "providers", "llama-cpp"]) === undefined) {
  setIn(cfg, ["models", "providers", "llama-cpp"], {
    baseUrl: "http://127.0.0.1:19432/v1",
    apiKey: "llama-cpp-local",
    api: "openai-completions",
    timeoutSeconds: 600,
    localService: {
      command: "/app/tools/llama.cpp/b10534/linux-x64/llama-server",
      args: [
        "--host", "127.0.0.1",
        "--port", "19432",
        "--models-preset", "/app/tools/llama.cpp/models.ini",
        "--models-max", "2",
        "--metrics",
        "--no-ui",
      ],
      healthUrl: "http://127.0.0.1:19432/health",
      readyTimeoutMs: 30000,
      idleStopMs: 600000,
    },
    models: [],
  });
  changed = true;
  console.log("[migration 0006] models.providers.llama-cpp adicionado (embeddings locais do memory.search).");
}

if (!changed) {
  console.log("[migration 0006] openclaw.json já no formato novo, nada a fazer.");
  process.exit(0);
}

fs.writeFileSync(file, JSON.stringify(cfg, null, 2) + "\n");
console.log("[migration 0006] openclaw.json atualizado (memory builtin, agents.entries).");
'


#### GATE: o config precisa estar VÁLIDO ao fim da migration.
# Fail-closed de propósito. Config inválido faz o gateway 2026.9.1 recusar subir
# de qualquer jeito; falhar aqui, com o erro do validador na tela e o backup em
# $LZBOT_MIGRATION_BACKUP_DIR, é muito melhor que descobrir isso num boot loop.
# OPENCLAW_CONFIG_PATH aponta o validador para o arquivo que ESTA migration
# editou. Sem isso ele valida o config ativo default e o gate viraria placebo
# quando $OPENCLAW_JSON estivesse sobrescrito (teste, instância com profile).
if command -v openclaw >/dev/null 2>&1; then
  if ! validate_out="$(OPENCLAW_CONFIG_PATH="$OPENCLAW_JSON" openclaw config validate 2>&1)"; then
    echo "[migration 0006] ERRO: openclaw config validate falhou DEPOIS da migration:" >&2
    echo "$validate_out" | sed 's/^/[migration 0006]   /' >&2
    echo "[migration 0006]" >&2
    echo "[migration 0006] A tabela de migração do vendor (PASSO 2) já cobriu tudo que o" >&2
    echo "[migration 0006] produto sabe aposentar, e o PASSO 3 o que o lzbot conhece. O que" >&2
    echo "[migration 0006] sobrou acima é drift que NENHUM dos dois reconhece — reconcilie à" >&2
    echo "[migration 0006] mão: o gateway 2026.9.1 recusa subir com essas chaves." >&2
    echo "[migration 0006] ATENÇÃO: o validate mostra um erro por vez, e a parte do schema que" >&2
    echo "[migration 0006] pertence a PLUGIN de canal só é checada com o canal habilitado." >&2
    echo "[migration 0006] Backup do arquivo original em ${LZBOT_MIGRATION_BACKUP_DIR:-<sem backup>}." >&2
    exit 1
  fi
  echo "[migration 0006] openclaw config validate: OK"
else
  echo "[migration 0006] AVISO: binário openclaw não encontrado — gate de validação pulado." >&2
fi
