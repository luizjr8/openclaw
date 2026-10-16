#!/bin/bash
# Migration 0008: memory_index_dreaming_and_scope
# Idempotente. Roda uma vez por instância quando ela sobe além desta versão.
# $LZBOT_MIGRATION_BACKUP_DIR está setado — faz backup de tudo que toca ANTES.
#
# Regras: só ADICIONA o que falta, ou remove algo que esta migration reconhece
# explicitamente. Nunca editar esta migration depois que ela shippar.
set -euo pipefail

echo "[migration 0008] memory_index_dreaming_and_scope"

# Fecha os dois buracos que sobraram da travessia qmd -> memória builtin (0006).
# Os dois foram consertados À MÃO na prod em 05-06/set/2026; aqui eles viram
# migração, senão instância nova continua nascendo com a memória quebrada.
#
#   PASSO 1   — o dreaming polui o índice.
#   PASSO 2.a — override de provider por agente que só existia por causa do qmd.
#   PASSO 2   — o índice fica PAUSADO em "index scope changed".
#
# A ordem importa e é acoplada: o passo 1 muda o conjunto de arquivos indexáveis,
# e é o passo 2 que faz o índice reconhecer a mudança (e podar os chunks órfãos
# do dreaming que já estavam lá). Passo 1 sem passo 2 só tira arquivo NOVO do
# índice; o lixo antigo continua ranqueando.

STATE_DIR="${OPENCLAW_STATE_DIR:-/app}"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "[migration 0008] openclaw não está no PATH — pulando." >&2
  exit 0
fi

OPENCLAW_ENTRY="$(readlink -f "$(command -v openclaw)")"
OPENCLAW_DIST="$(dirname "$OPENCLAW_ENTRY")/dist"
[ -d "$OPENCLAW_DIST" ] || OPENCLAW_DIST="$(dirname "$(dirname "$OPENCLAW_ENTRY")")/dist"
if [ ! -d "$OPENCLAW_DIST" ]; then
  echo "[migration 0008] não achei o dist do openclaw a partir de $OPENCLAW_ENTRY — pulando." >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# PASSO 1 — dreaming fora do índice, por workspace de agente.
#
#### O problema
#
# O dreaming builtin escreve os digests em
# `<workspace>/memory/dreaming/{light,rem,deep}/<dia>.md`, regenerados 3x por dia
# (cron 03:00). Isso é ARQUIVO .md DENTRO de `memory/`, que é exatamente o que o
# indexador varre — e o volume é brutal: na prod, 363 dos 447 .md do workspace do
# main eram dreaming, 81% do índice. Recall de memória curada afundado por digest
# de sonho que se reescreve todo dia.
#
#### Por que não dá pra excluir por config
#
# Não existe exclusão configurável no 2026.9.1. Lido no dist
# (`listMemoryFiles`/`collectMemoryFilesFromDir`): a varredura de `<ws>/memory` é
# incondicional e o único filtro é `shouldSkipRootMemoryAuxiliaryPath`, que
# reconhece dois caminhos fixos (`memory.md` legado e `.openclaw-repair/`). Os
# `extraPaths` com `pattern` só restringem o que ELES adicionam; não recortam a
# varredura base. E o `.dreams/` fica de fora só por acidente feliz (é `.txt`, e
# o indexador quer `.md`).
#
#### O mecanismo que sobra, e por que ele é sólido dos dois lados
#
# Mover o diretório real para `<workspace>/dreaming` e deixar um symlink
# `memory/dreaming -> ../dreaming` no lugar. Isso corta a LEITURA sem cortar a
# ESCRITA, porque os dois lados tratam symlink de forma oposta — e nos dois casos
# está no código, não na sorte:
#
#   leitura  `walkDirectory(dir, { symlinks: "skip", ... })` em
#            collectMemoryFilesFromDir. Com "skip", um dirent que é symlink é
#            descartado antes de virar entrada e antes de descer — o diretório
#            inteiro some da varredura. (Vale também para o watcher: tanto
#            `ensureWatcher` quanto `attachLinuxMemoryDirectoryTreeSubtree`
#            pulam `isSymbolicLink()`.)
#   escrita  o writer do dreaming usa `node:fs` cru — `fs.mkdir(recursive)` +
#            `replaceFileAtomic`, que resolvem o caminho ATRAVÉS do symlink e não
#            têm guarda tipo `assertNoSymlinkParents`. O digest continua sendo
#            gravado normalmente, só que fora do alcance do indexador.
#
# O `DREAMS.md` da raiz do workspace já ficava de fora (a varredura de raiz só
# pega `MEMORY.md` e `USER.md`), então ele não entra nesta conta.
#
#### Escopo: por AGENTE, e quem resolve o workspace é o produto
#
# Cada agente tem o seu workspace, e a regra de resolução não é "raiz + id":
# depende de `agents.defaults.workspace`, de `workspace` explícito na entrada, de
# `agentDir` e do modo de `agents.ownership`. Reimplementar isso aqui envelheceria
# na primeira mudança do loader — então quem responde é `resolveAgentWorkspaceDir`
# do próprio dist, igual a 0006/0007 fazem com as funções que precisam.
# Medido nesta instância: main -> /app/workspace, coder -> /app/workspace/coder,
# concierge -> /app/agents/concierge/workspace (NÃO /app/workspace/concierge).
#
#### Idempotência
#
#   já symlink            -> no-op
#   dir real              -> move para <ws>/dreaming + symlink
#   nem um nem outro      -> cria <ws>/dreaming + symlink (instância nova já
#                            nasce certa, que é o ponto desta migration)
#   ambos dir real        -> não mescla nem sobrescreve: o de dentro de memory/
#                            vai inteiro para o backup da migration e o symlink
#                            passa a apontar para o que já existia. Nada é
#                            perdido e o índice fica limpo.
# ---------------------------------------------------------------------------

# `cd` no state dir porque workspace relativo (`"workspace/"`) resolve contra o
# cwd. O entrypoint já roda com WORKDIR /app, mas depender disso seria frágil.
cd "$STATE_DIR"

OPENCLAW_DIST="$OPENCLAW_DIST" \
LZBOT_MIGRATION_BACKUP_DIR="${LZBOT_MIGRATION_BACKUP_DIR:-}" \
node --input-type=module <<'NODE'
import { readdirSync, existsSync, lstatSync, mkdirSync, renameSync, symlinkSync, readlinkSync } from "node:fs";
import path from "node:path";

const dist = process.env.OPENCLAW_DIST;
const backupDir = process.env.LZBOT_MIGRATION_BACKUP_DIR || "";
const log = (m) => console.log("[migration 0008] " + m);

//// Resolvendo o roster pelo produto
// Os chunks do dist têm hash no nome, mas os EXPORTS deste aqui não são
// minificados. Procura por padrão de nome e confirma por assinatura (o dist tem
// mais de um `agent-scope-*.js`, e só um traz o par que interessa).
let scope;
for (const file of readdirSync(dist).filter((f) => /^agent-scope.*\.m?js$/.test(f))) {
  try {
    const mod = await import(path.join(dist, file));
    if (typeof mod.listAgentIds === "function" && typeof mod.resolveAgentWorkspaceDir === "function") {
      scope = mod;
      break;
    }
  } catch {}
}
if (!scope) {
  console.error("[migration 0008] AVISO: dist não expõe listAgentIds/resolveAgentWorkspaceDir — pulando o passo do dreaming.");
  process.exit(0);
}

const { loadConfig } = await import(path.join(dist, "config", "config.js"));
const cfg = await loadConfig();

const workspaces = new Set();
for (const agentId of scope.listAgentIds(cfg)) {
  try {
    workspaces.add(scope.resolveAgentWorkspaceDir(cfg, agentId));
  } catch (err) {
    console.error(`[migration 0008] AVISO: não resolvi o workspace de "${agentId}": ${String(err?.message ?? err)}`);
  }
}

for (const workspaceDir of workspaces) {
  if (!existsSync(workspaceDir)) {
    log(`workspace ${workspaceDir} não existe — pulando.`);
    continue;
  }
  const inMemory = path.join(workspaceDir, "memory", "dreaming");
  const outside = path.join(workspaceDir, "dreaming");

  const linked = (() => {
    try { return lstatSync(inMemory).isSymbolicLink(); } catch { return false; }
  })();
  if (linked) {
    log(`${inMemory} já é symlink (-> ${readlinkSync(inMemory)}) — nada a fazer.`);
    continue;
  }

  if (existsSync(inMemory)) {
    if (existsSync(outside)) {
      // Conflito: as duas pontas existem como diretório real. Não mescla (o
      // mesmo <dia>.md pode existir dos dois lados com conteúdo diferente) e não
      // sobrescreve — arquiva o de dentro de memory/ e segue.
      if (!backupDir) {
        console.error(`[migration 0008] AVISO: ${inMemory} e ${outside} existem os dois e não há backup dir — deixando como está.`);
        continue;
      }
      const parked = path.join(backupDir, `dreaming-conflict-${path.basename(workspaceDir) || "root"}`);
      renameSync(inMemory, parked);
      log(`CONFLITO: ${outside} já existia; ${inMemory} foi para ${parked} (nada perdido, resolver à mão se precisar).`);
    } else {
      renameSync(inMemory, outside);
      log(`${inMemory} -> ${outside} (fora do alcance do indexador).`);
    }
  } else {
    mkdirSync(outside, { recursive: true });
    log(`${outside} criado (o dreaming ainda não escreveu neste workspace).`);
  }

  mkdirSync(path.dirname(inMemory), { recursive: true });
  symlinkSync(path.join("..", "dreaming"), inMemory);
  log(`${inMemory} -> ../dreaming (symlink; escrita segue, leitura do índice para).`);
}
NODE

# ---------------------------------------------------------------------------
# PASSO 2 — invalidar a identidade do índice para forçar reconciliação.
#
#### O sintoma
#
# Depois da 0006 a memória sobe indexada mas MUDA: toda `memory_search` volta
# vazia. O motivo está em `resolveMemoryIndexIdentityState` (dist do memory-core):
# o manager compara a config atual com a identidade gravada no índice
# (`memory_index_meta`, chave `memory_index_meta_v1` — sources, scopeHash, model,
# providerKey, chunking...). Se algo difere, o estado vira `mismatched`, e no
# caminho de busca:
#
#     if (repairedIndexIdentity.status !== "valid") return [];
#
# Recall morto, em silêncio, até alguém rodar rebuild à mão. É o "index scope
# changed" que pausou o vetor na prod.
#
# E o mismatch é INEVITÁVEL nesta travessia: a 0006 é justamente quem reescreve
# `memory.search.sources` e os `extraPaths` por agente (memorySearch ->
# memory.search), e o `scopeHash` é hash de extraPaths + multimodal. O PASSO 1
# acima mexe no mesmo eixo. Ou seja: quem cria o mismatch é a nossa própria
# migração, então é ela que tem que fechar.
#
#### O lever: apagar a linha, não o índice
#
# Com a linha ausente o estado vira `missing` — e `missing` é tratado de forma
# COMPLETAMENTE diferente de `mismatched`:
#
#     if (indexIdentity.status === "missing" && hasIndexedContent)
#       await this.syncAdmitted({ reason: "search", force: true }, ...)
#
# Ou seja, o produto se reconcilia sozinho — MAS só quando já existe conteúdo
# indexado. `hasIndexedContent` é a guarda dos dois ramos (o de identidade acima
# e o bootstrap logo antes dele), e numa instância que nunca indexou ela é falsa.
# Quem cobre esse caso é o PASSO 3.
#
# E é barato, o que é o ponto todo: as tabelas de conteúdo continuam onde estão
# — `memory_index_chunks*`, os `*_fts`, `memory_index_chunks_vec*` e, o que
# importa de verdade, `memory_embedding_cache`. Esse cache é endereçado por
# CONTEÚDO (chave `provider + model + provider_key + hash do chunk`), e a
# reconciliação passa por `collectCachedEmbeddings` -> `loadMemoryEmbeddingCache`
# antes de chamar o provider. Resultado: chunk que não mudou é cache hit e NÃO é
# re-embedado. Dropar o vetor (a alternativa "óbvia") faria o oposto — perderia o
# cache e forçaria re-embed de tudo.
#
# Só a chave `memory_index_meta_v1` sai. As outras linhas da tabela (ex.:
# `memory_vector_rebuild_v1`) não são identidade e ficam.
#
#### Por que NÃO rodar o rebuild INLINE aqui dentro
#
# Deliberado. Embed em CPU-only sem GPU estoura o boot: na prática deu ~7min,
# SIGKILL e contenção com o resto da instância. Este passo faz só a parte barata
# (uma linha de DELETE); quem reconstrói é o PASSO 3, fora do caminho do boot.
#
#### Idempotência
#
# Re-run com a meta ausente é no-op (não há linha). Re-run depois que o produto
# reescreveu a meta apaga de novo e dispara outra reconciliação — barata, pelo
# cache. Em nenhum caso há perda.
# ---------------------------------------------------------------------------

## PASSO 2.a — o override de provider por agente que sobrou do qmd.
#
# Antes do PASSO 2 de propósito: ele muda a IDENTIDADE do índice (o `providerKey`
# e o `model` entram na comparação), então tem que estar decidido antes de a
# identidade ser invalidada — senão a reconciliação roda com o provider errado e
# o índice nasce mismatched de novo no boot seguinte.
#
#### O que é
#
# Até a 2026.7.1 o `coder` (e qualquer agente com o mesmo padrão) tinha
# `memorySearch.provider: "openai"`. Aquilo NÃO era escolha de qualidade de
# embedding: era isolamento contra o backend qmd GLOBAL, onde operação de coleção
# de um agente corrompia o índice do outro. O qmd morreu na 0006 e cada agente já
# tem índice builtin próprio — o motivo do override foi embora com ele.
#
# A 0006 moveu `memorySearch` -> `memory.search` VERBATIM (é o que ela tinha que
# fazer: preservar valor e comentário). O efeito colateral é que o override
# sobrevive à travessia, e aí o agente fica preso a uma cota externa: sem
# `OPENAI_API_KEY` o provider degrada e o recall daquele agente morre calado,
# mesmo com o global `provider: "local"` funcionando. Medido no ensaio do frank
# (7.1 -> 9.2): o `coder` saiu da migração com "Provider: openai · Model:
# text-embedding-3-small" enquanto o `main` estava em local/embeddinggemma.
#
#### O recorte
#
# Só sai `provider: "openai"`, e só quando o global já é `"local"` — ou seja, só
# quando a instância adotou a doutrina que a 0006 escreve. Qualquer outro valor
# (ou global diferente de "local") é escolha deliberada de quem administra a
# instância e não é da conta desta migration. `extraPaths` e o resto da entrada
# não são tocados.
LZBOT_JSONC_LIB="${LZBOT_JSONC_LIB:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/lib/jsonc-edit.mjs}"
AGENTS_JSON5="${AGENTS_JSON5:-$STATE_DIR/agents.json5}"

if [ -f "$AGENTS_JSON5" ]; then
  if [ -n "${LZBOT_MIGRATION_BACKUP_DIR:-}" ]; then
    cp -a "$AGENTS_JSON5" "$LZBOT_MIGRATION_BACKUP_DIR/agents.json5.before-0008"
  fi
  LZBOT_JSONC_LIB="$LZBOT_JSONC_LIB" AGENTS_JSON5="$AGENTS_JSON5" OPENCLAW_JSON="${OPENCLAW_JSON:-$STATE_DIR/openclaw.json}" \
  node --input-type=module -e '
import fs from "node:fs";
const log = (m) => console.log("[migration 0008] " + m);
const agentsFile = process.env.AGENTS_JSON5;
const configFile = process.env.OPENCLAW_JSON;

let globalProvider;
try { globalProvider = JSON.parse(fs.readFileSync(configFile, "utf8"))?.memory?.search?.provider; } catch {}
if (globalProvider !== "local") {
  log(`memory.search.provider global é ${JSON.stringify(globalProvider)} (não "local") — override por agente fica como está.`);
  process.exit(0);
}

const { readJsonc, editJsonc } = await import(process.env.LZBOT_JSONC_LIB);
const { text, data } = readJsonc(agentsFile);
if (Array.isArray(data)) {
  console.error("[migration 0008] AVISO: agents.json5 ainda é array (a 0006 não converteu?) — pulando.");
  process.exit(0);
}

let out = text;
const dropped = [];
for (const [agentId, entry] of Object.entries(data)) {
  if (entry?.memory?.search?.provider !== "openai") continue;
  out = editJsonc(out, [{ op: "remove", path: [agentId, "memory", "search", "provider"] }]).text;
  dropped.push(agentId);
}

if (dropped.length === 0) {
  log("nenhum override `memory.search.provider: \"openai\"` por agente — nada a fazer.");
  process.exit(0);
}
fs.writeFileSync(agentsFile, out);
log(`override de provider removido de ${dropped.join(", ")} — herdam o global "local" (o isolamento existia por causa do qmd, que morreu na 0006).`);
'
  if command -v openclaw >/dev/null 2>&1; then
    if ! validate_out="$(OPENCLAW_CONFIG_PATH="${OPENCLAW_JSON:-$STATE_DIR/openclaw.json}" openclaw config validate 2>&1)"; then
      echo "[migration 0008] ERRO: openclaw config validate falhou depois de mexer no agents.json5:" >&2
      echo "$validate_out" | sed 's/^/[migration 0008]   /' >&2
      echo "[migration 0008] Backup em ${LZBOT_MIGRATION_BACKUP_DIR:-<sem backup>}." >&2
      exit 1
    fi
    echo "[migration 0008] openclaw config validate: OK"
  fi
fi

STATE_DIR="$STATE_DIR" LZBOT_MIGRATION_BACKUP_DIR="${LZBOT_MIGRATION_BACKUP_DIR:-}" node --input-type=module <<'NODE'
import { readdirSync, existsSync, copyFileSync } from "node:fs";
import path from "node:path";

const stateDir = process.env.STATE_DIR;
const backupDir = process.env.LZBOT_MIGRATION_BACKUP_DIR || "";
const log = (m) => console.log("[migration 0008] " + m);

let DatabaseSync;
try {
  ({ DatabaseSync } = await import("node:sqlite"));
} catch (err) {
  console.error(`[migration 0008] AVISO: node:sqlite indisponível (${String(err?.message ?? err)}) — pulando a invalidação do índice.`);
  process.exit(0);
}

// O banco por agente é `<state>/agents/<id>/agent/openclaw-agent.sqlite`. A
// varredura é por diretório em vez de derivar do roster de propósito: agente que
// saiu do config mas deixou banco no volume também tem que ser invalidado, senão
// ele volta com identidade velha se for reativado.
const agentsRoot = path.join(stateDir, "agents");
if (!existsSync(agentsRoot)) {
  log(`${agentsRoot} não existe — nenhum índice a invalidar.`);
  process.exit(0);
}

let invalidated = 0;
for (const entry of readdirSync(agentsRoot, { withFileTypes: true })) {
  if (!entry.isDirectory()) continue;
  const dbPath = path.join(agentsRoot, entry.name, "agent", "openclaw-agent.sqlite");
  if (!existsSync(dbPath)) continue;

  let db;
  try {
    db = new DatabaseSync(dbPath);
    // O gateway ainda não subiu neste ponto do boot, mas um CLI concorrente pode
    // estar com o banco aberto — espera em vez de morrer.
    db.exec("PRAGMA busy_timeout = 10000");
    if (db.prepare("SELECT name FROM sqlite_master WHERE name = 'memory_index_meta'").all().length === 0) {
      db.close();
      continue;
    }
    const before = db.prepare("SELECT value FROM memory_index_meta WHERE key = 'memory_index_meta_v1'").all();
    if (before.length === 0) {
      log(`${entry.name}: identidade de índice já ausente — nada a invalidar.`);
      db.close();
      continue;
    }
    db.close();
    db = undefined;

    if (backupDir) copyFileSync(dbPath, path.join(backupDir, `openclaw-agent.${entry.name}.sqlite.before-0008`));

    db = new DatabaseSync(dbPath);
    db.exec("PRAGMA busy_timeout = 10000");
    db.prepare("DELETE FROM memory_index_meta WHERE key = 'memory_index_meta_v1'").run();
    db.close();
    db = undefined;

    const meta = JSON.parse(String(before[0].value));
    log(`${entry.name}: identidade de índice invalidada (era sources=${JSON.stringify(meta.sources)} scope=${String(meta.scopeHash).slice(0, 12)}…) — o produto reconcilia no primeiro uso, reaproveitando memory_embedding_cache.`);
    invalidated += 1;
  } catch (err) {
    try { db?.close(); } catch {}
    console.error(`[migration 0008] AVISO: falha ao invalidar ${dbPath}: ${String(err?.message ?? err)}`);
  }
}

log(`invalidação de índice concluída (${invalidated} agente(s)).`);
NODE

# ---------------------------------------------------------------------------
# PASSO 3 — reconstruir o índice DEPOIS do boot, um agente por vez.
#
#### Por que o PASSO 2 sozinho não basta
#
# O PASSO 2 conta com a auto-reconciliação do produto, e ela tem uma guarda:
# `hasIndexedContent()`. Os dois ramos que reconstroem no caminho de busca —
# o bootstrap (`if (!hasIndexedContent) syncAdmitted(...)`) e o reparo de
# identidade (`identity.status === "missing" && hasIndexedContent`) — só entram
# quando o banco JÁ tem chunk ou linha de FTS. Instância que nunca indexou (ou
# que perdeu o volume) fica em `Indexed: 0/N files · 0 chunks`, `Dirty: yes`, e
# a busca devolve vazio até alguém rodar rebuild à mão.
#
# Medido no frank (7.1 -> 9.2, estado limpo): pós-migração os dois agentes
# ficaram em 0 chunks porque `/app/agents` nem existia — os bancos por agente
# nascem preguiçosos. Nada no boot os cria.
#
# E mesmo quando a auto-reconciliação ENTRA, ela entra no pior lugar possível:
# inline, dentro da primeira `memory_search` do usuário, com o build inteiro
# na frente da resposta.
#
#### O desenho: deferido e serializado, nunca inline
#
# Um runner destacado (`setsid`, `nice`) que roda DEPOIS que esta migration
# retorna, iterando os agentes um a um. O boot não espera por ele.
#
# Serializado por decisão, não por acaso: `openclaw memory index` sem `--agent`
# aceita "todos", mas aí o provider de embedding leva N agentes em paralelo em
# cima do mesmo llama-server local. Um `--agent <id>` por vez mantém a pressão
# de CPU/embedding em um agente de cada vez — que é o mesmo motivo de o rebuild
# não estar inline.
#
#### Se o runner morrer no meio
#
# Aceitável, e o produto cobre: o manager marca os arquivos pendentes como
# `dirty` e o `startAsyncSearchSync({ enabled: settings.sync.onSearch, ... })`
# reconcilia em background nas buscas seguintes. O pior caso é um índice parcial
# que se completa sozinho com o uso — não um índice morto.
#
#### Observabilidade: o marcador conta CHUNK, não exit code
#
# `openclaw memory index` sai 0 mesmo quando não indexa nada. Medido no frank:
# com o provider de embedding fora do ar (llama-server ausente) ele imprime
# "embeddings unavailable; leaving memory index dirty" / "0 files indexed" e
# retorna 0. Um marcador de rc, portanto, mente — e mentiria exatamente no caso
# que interessa detectar, que é o índice vazio.
#
# Por isso o `.done` grava `<id>=rc<rc>/chunks<n>`, com o `n` vindo de
# `openclaw memory status --agent <id> --json`. `chunks0` é o sinal de que o
# rebuild não pegou (quase sempre: provider de embedding indisponível — o engine
# builtin não indexa nem o FTS sem ele, mesmo com o vetor desligado).
#
# Os dois valores não-numéricos são diagnósticos DIFERENTES e por isso não
# compartilham símbolo: `chunksdisabled` (o agente tem
# `memory.search.enabled: false` — nada a indexar, é config e não falha; é o caso
# do `concierge` aqui) e `chunks?` (o status não foi legível — aí sim há o que
# investigar).
# ---------------------------------------------------------------------------

REINDEX_LOG="$STATE_DIR/.lzbot-0008-reindex.log"
REINDEX_DONE="$STATE_DIR/.lzbot-0008-reindex.done"
REINDEX_RUNNER="$STATE_DIR/.lzbot-0008-reindex.sh"

# Mesma resolução de roster do PASSO 1 (o produto responde, não a gente).
AGENT_IDS="$(OPENCLAW_DIST="$OPENCLAW_DIST" node --input-type=module <<'NODE' 2>/dev/null || true
import { readdirSync } from "node:fs";
import path from "node:path";
const dist = process.env.OPENCLAW_DIST;
let scope;
for (const file of readdirSync(dist).filter((f) => /^agent-scope.*\.m?js$/.test(f))) {
  try {
    const mod = await import(path.join(dist, file));
    if (typeof mod.listAgentIds === "function") { scope = mod; break; }
  } catch {}
}
if (!scope) process.exit(1);
const { loadConfig } = await import(path.join(dist, "config", "config.js"));
for (const id of scope.listAgentIds(await loadConfig())) console.log(id);
NODE
)"
# Achata para uma linha: o runner faz `for id in $LZBOT_0008_AGENTS`.
AGENT_IDS="$(echo $AGENT_IDS)"

if [ -z "$AGENT_IDS" ]; then
  echo "[migration 0008] AVISO: não consegui listar os agentes — rebuild do índice não foi agendado." >&2
else
  # Heredoc CITADO: nada é interpolado na geração. O runner recebe tudo por
  # ambiente (herdado através do setsid), o que evita uma camada de escaping
  # dentro de um script que já é gerado dentro de outro.
  cat > "$REINDEX_RUNNER" <<'RUNNER'
#!/bin/bash
# Gerado pela migration 0008. Reconstrói o índice de memória de cada agente,
# um por vez, fora do caminho do boot. Descartável: pode apagar depois do .done.
exec >>"$LZBOT_0008_LOG" 2>&1
echo "[0008-reindex] início $(date -Is)"
rm -f "$LZBOT_0008_DONE"
status=""
for id in $LZBOT_0008_AGENTS; do
  echo "[0008-reindex] --- agente $id"
  t0=$(date +%s)
  if openclaw memory index --agent "$id"; then rc=0; else rc=$?; fi
  chunks=$(openclaw memory status --agent "$id" --json 2>/dev/null | node -e '
let s="";process.stdin.on("data",(d)=>s+=d).on("end",()=>{
  try {
    const a = JSON.parse(s);
    // Lista vazia = `memory.search.enabled: false` na entrada do agente. Sai como
    // "disabled" e nao como "?": desligado de proposito e status ilegivel sao
    // diagnosticos opostos e nao podem colidir no marcador.
    console.log(Array.isArray(a) && a.length === 0 ? "disabled" : (a[0]?.status?.chunks ?? "?"));
  } catch { console.log("?") }
});')
  echo "[0008-reindex] agente $id: rc=$rc chunks=${chunks:-?} em $(( $(date +%s) - t0 ))s"
  [ "${chunks:-0}" = "0" ] && echo "[0008-reindex] AVISO: $id ficou com 0 chunks — provider de embedding indisponível?"
  status="$status$id=rc$rc/chunks${chunks:-?} "
done
echo "$status" > "$LZBOT_0008_DONE"
echo "[0008-reindex] fim $(date -Is) — $status"
RUNNER
  chmod +x "$REINDEX_RUNNER"
  rm -f "$REINDEX_DONE"
  LZBOT_0008_LOG="$REINDEX_LOG" LZBOT_0008_DONE="$REINDEX_DONE" LZBOT_0008_AGENTS="$AGENT_IDS" \
    setsid nohup nice -n 10 bash "$REINDEX_RUNNER" </dev/null >/dev/null 2>&1 &
  disown || true
  echo "[migration 0008] rebuild do índice agendado em background para: $AGENT_IDS — log em $REINDEX_LOG, marcador em $REINDEX_DONE."
fi

echo "[migration 0008] OK"
