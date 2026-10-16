#!/bin/bash
# Migration 0007: migrate_legacy_state_sources
# Idempotente. Roda uma vez por instância quando ela sobe além desta versão.
# $LZBOT_MIGRATION_BACKUP_DIR está setado — faz backup de tudo que toca ANTES.
#
# Regras: só ADICIONA o que falta, ou remove algo que esta migration reconhece
# explicitamente. Nunca editar esta migration depois que ela shippar.
set -euo pipefail

echo "[migration 0007] migrate_legacy_state_sources"

# Importa as FONTES DE ESTADO LEGADO que a 2026.9.x recolheu para o SQLite
# compartilhado, mas que o boot do gateway NÃO importa sozinho.
#
#### O sintoma, e por que 0005 não basta
#
# Com o schema do state DB já reconciliado (0005), o gateway ainda morre em:
#
#   Gateway failed to start: Legacy workspace setup state requires migration
#   for /app/workspace/concierge; run openclaw doctor --fix.
#
# É outro eixo do mesmo upgrade: além do SCHEMA, a 9.x mudou ONDE mora o estado.
# Coisas que eram arquivo solto no volume viraram linha no SQLite:
#
#   <workspace>/openclaw-workspace-state.json      (bootstrapSeededAt/setupCompletedAt)
#   <workspace>/.openclaw/workspace-state.json
#   <state>/workspace-attestations/<hash>.attested (atestado de confiança do workspace)
#   memory/.dreams/events.jsonl                    (eventos do Memory Core)
#   linhas de auth do agente main no store antigo
#
# O gateway roda `autoMigrateLegacyState` no boot, mas essas fontes estão
# marcadas como `doctorOnlyStateMigrations` — a importação delas é do doctor. Aí
# o boot só CHECA (`assertNoUnmigratedWorkspaceState`) e falha fail-closed
# mandando rodar `doctor --fix`.
#
#### Por que não `openclaw doctor --fix`
#
# Porque o `--fix` é dois trabalhos num comando só: migrar ESTADO e reescrever
# CONFIG. O segundo é justamente o que não pode acontecer aqui — ele normaliza o
# agents.json5 e mata os comentários que são a documentação viva daquele arquivo.
#
# Esta migration chama só a metade de estado, pela API do próprio produto:
#
#   detectLegacyStateMigrations({ cfg, doctorOnlyStateMigrations: true, ... })
#   runLegacyStateMigrations({ detected, config: cfg, ... })
#
# São exports com nome estável (não minificados) do chunk
# `state-migrations.doctor-<hash>.js`; o hash muda a cada build, então a
# resolução é por PADRÃO de nome, e o `loadConfig` vem do entrypoint público
# `dist/config/config.js`. Nada de config é escrito por este caminho — provado no
# lab comparando md5 de openclaw.json e agents.json5 antes/depois.
#
# Idempotente por construção: o detector é quem decide o que ainda falta. Numa
# instância já migrada `preview` vem vazio e a migration não chama o runner.
#
# Fail-closed, mas no alvo certo: o runner devolve os avisos da DETECÇÃO
# misturados com os da própria migração, e os de detecção são condição
# pré-existente do ambiente, não falha da importação. Medido: o plugin do codex
# avisa que o doctor-contract dele não resolve o pacote `openclaw` (peer link que
# o gateway religa no start) — abortar o boot por causa disso seria fail-closed
# no alvo errado. Então só aviso NOVO, nascido do runner, derruba a migration.

STATE_DIR="${OPENCLAW_STATE_DIR:-/app}"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "  openclaw não está no PATH — pulando"
  exit 0
fi

OPENCLAW_ENTRY="$(readlink -f "$(command -v openclaw)")"
OPENCLAW_DIST="$(dirname "$OPENCLAW_ENTRY")/dist"
[ -d "$OPENCLAW_DIST" ] || OPENCLAW_DIST="$(dirname "$(dirname "$OPENCLAW_ENTRY")")/dist"
if [ ! -d "$OPENCLAW_DIST" ]; then
  echo "  não achei o dist do openclaw a partir de $OPENCLAW_ENTRY — pulando" >&2
  exit 0
fi

# O importador escreve no state DB; backup dele antes, como a 0005 faz.
if [ -n "${LZBOT_MIGRATION_BACKUP_DIR:-}" ] && [ -f "$STATE_DIR/state/openclaw.sqlite" ]; then
  mkdir -p "$LZBOT_MIGRATION_BACKUP_DIR"
  for _suffix in "" "-wal" "-shm"; do
    _src="$STATE_DIR/state/openclaw.sqlite$_suffix"
    [ -f "$_src" ] && cp -a "$_src" "$LZBOT_MIGRATION_BACKUP_DIR/"
  done
  echo "  backup do state DB em $LZBOT_MIGRATION_BACKUP_DIR"
fi

OPENCLAW_DIST="$OPENCLAW_DIST" node --input-type=module <<'NODE'
import { readdirSync } from "node:fs";
import path from "node:path";

const dist = process.env.OPENCLAW_DIST;
const entries = readdirSync(dist);
// Resolve por EXPORT, não pelo primeiro nome que casa: desde a 2026.9.3 o dist
// publica DOIS chunks `state-migrations.doctor-*` — um minificado (exports `a`,
// `i`, `n`, `r`, `t`) e o real. O `find` pegava o minificado e o import quebrava
// com "detectLegacyStateMigrations is not a function".
const importByExports = async (re, names) => {
  for (const file of entries.filter((f) => re.test(f))) {
    try {
      const mod = await import(path.join(dist, file));
      if (names.every((n) => typeof mod[n] === "function")) return mod;
    } catch {
      // chunk que não carrega isolado não nos interessa
    }
  }
  return undefined;
};

const stateMigrationsMod = await importByExports(/^state-migrations\.doctor-.*\.m?js$/, [
  "detectLegacyStateMigrations",
  "runLegacyStateMigrations",
]);
const sessionSurfacesMod = await importByExports(/^legacy-session-surfaces-.*\.m?js$/, [
  "prepareLegacySessionSurfaces",
]);
if (!stateMigrationsMod || !sessionSurfacesMod) {
  console.log("  dist não expõe o migrador de estado legado — pulando");
  process.exit(0);
}

const { loadConfig } = await import(path.join(dist, "config", "config.js"));
const { detectLegacyStateMigrations, runLegacyStateMigrations } = stateMigrationsMod;
const { prepareLegacySessionSurfaces } = sessionSurfacesMod;

const cfg = await loadConfig();
const legacySessionSurfaces = prepareLegacySessionSurfaces({ config: cfg });
const detected = await detectLegacyStateMigrations({
  cfg,
  doctorOnlyStateMigrations: true,
  legacySessionSurfaces,
});

// `runLegacyStateMigrations` devolve os avisos da DETECÇÃO misturados com os da
// própria migração. Guardamos os de detecção para descontar depois: eles são
// condição PRÉ-EXISTENTE do ambiente (ex.: um plugin cujo doctor-contract não
// resolve o pacote `openclaw` até o gateway religar os peer links), não falha da
// importação — abortar o boot por causa deles seria fail-closed no alvo errado.
const preexisting = new Set(detected.warnings ?? []);
for (const w of preexisting) console.error(`  aviso pré-existente na detecção: ${w}`);
if (!detected.preview?.length) {
  console.log("  nenhuma fonte de estado legado pendente — no-op");
  process.exit(0);
}
for (const line of detected.preview) console.log(`  pendente: ${line.replace(/^- /, "")}`);

const result = await runLegacyStateMigrations({
  detected,
  config: cfg,
  legacySessionSurfaces,
  doctorOnlyStateMigrations: true,
});
for (const change of result.changes ?? []) console.log(`  ${change}`);
const fresh = (result.warnings ?? []).filter((w) => !preexisting.has(w));
if (fresh.length > 0) {
  for (const w of fresh) console.error(`  FALHA: ${w}`);
  process.exit(1);
}
NODE

# ---------------------------------------------------------------------------
# PASSO 2 — o session store legado (`<agentDir>/sessions/sessions.json`).
#
# Este NÃO passa pelo `runLegacyStateMigrations` do passo anterior: é um submodo
# próprio do doctor (`--session-sqlite`), com detector próprio e manifesto de
# execução próprio. Por isso o passo 1 diz "nenhuma fonte pendente" e o gateway
# morre logo em seguida:
#
#   Gateway failed to start: Legacy session store requires migration:
#   /app/agents/main/sessions/sessions.json. Run "openclaw doctor --fix" ...
#
# `assertSessionStoreMigrationComplete` roda no start e recusa QUALQUER store
# configurado que não termine em `.sqlite`. Numa instância com três agentes são
# três stores, um por agentDir — e nenhum deles aparece no ensaio de quem clonou
# só o /app sem os agentDir, que foi como este blocker passou batido na Fase I.
#
# `--session-sqlite import` é um submodo TARGETED: o doctor entra nele e sai
# (`exitCliAfterOutput`) sem chegar no reescritor de config. Medido no lab: md5
# de openclaw.json e agents.json5 idêntico antes/depois. É por isso que dá para
# usar o binário aqui sem violar a regra de nunca rodar `doctor --fix`.
#
# Ele faz mais que importar: arquiva os .jsonl órfãos e compacta o SQLite de
# sessão (no clone da prod: 1569 transcripts arquivados, ~190 MB recuperados).
# Nada é apagado — vai para o diretório de arquivo, com manifesto restaurável em
# `<state>/session-sqlite-migration-runs/`.
#
# Guarda de entrada e gate de saída são a MESMA função do produto: só chamamos o
# doctor se o assert do start reprovar agora, e no fim ele tem de aprovar. Exit 0
# do CLI provaria só que ele não quebrou.
# ---------------------------------------------------------------------------
OPENCLAW_DIST="$OPENCLAW_DIST" node --input-type=module <<'NODE'
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const dist = process.env.OPENCLAW_DIST;
//// Mesma resolução por CONTEÚDO da 0005: o hash do chunk muda a cada build e o
//// export é uma letra minificada, mas o `.name` da função sobrevive.
const holders = readdirSync(dist)
  .filter((f) => f.endsWith(".js") || f.endsWith(".mjs"))
  .filter((f) => {
    try {
      return readFileSync(path.join(dist, f), "utf8").includes("function assertSessionStoreMigrationComplete");
    } catch {
      return false;
    }
  });
if (holders.length !== 1) {
  console.log(`  dist não expõe o assert de session store (candidatos: ${holders.length}) — pulando`);
  process.exit(0);
}
const mod = await import(path.join(dist, holders[0]));
const assertComplete = Object.values(mod).find(
  (v) => typeof v === "function" && v.name === "assertSessionStoreMigrationComplete",
);
if (!assertComplete) {
  console.log("  chunk não exporta assertSessionStoreMigrationComplete — pulando");
  process.exit(0);
}

const { loadConfig } = await import(path.join(dist, "config", "config.js"));
const cfg = await loadConfig();
const pending = () => {
  try {
    assertComplete({ cfg });
    return undefined;
  } catch (err) {
    return String(err?.message ?? err);
  }
};

const before = pending();
if (!before) {
  console.log("  nenhum session store legado — no-op");
  process.exit(0);
}
console.log(`  pendente: ${before.split(". Run ")[0]}`);

//// Até 3 tentativas — o import é parcial sob perda de lease, não é all-or-nothing
// O doctor compacta o SQLite de cada agente segurando um lease de manutenção com
// heartbeat. Num agentDir grande (o `coder` desta instância: 336 MB) a compactação
// bloqueia o event loop tempo bastante para o heartbeat atrasar, o lease cair e o
// alvo abortar no meio:
//
//   [sqlite_compact_failed] ... maintenance lease core:agent-database-maintenance/
//   global was lost | state lease heartbeat is not responsive
//
// O alvo que perdeu o lease não arquiva o `sessions.json` dele, e só ELE fica
// pendente — os outros ficam prontos. É por isso que repetir resolve: a segunda
// passada tem quase nada para fazer (medido: 3 alvos/50 issues na 1ª, 1 alvo/0
// issue na 2ª) e o import é idempotente por construção (manifesto + reconcile).
//
// Falha depois de 3 é falha de verdade e derruba o boot: o gateway 2026.9.1 não
// sobe com session store legado, então deixar passar só adiaria o erro.
for (let attempt = 1; attempt <= 3; attempt += 1) {
  const run = spawnSync("openclaw", ["doctor", "--session-sqlite", "import", "--session-sqlite-all-agents"], {
    stdio: ["ignore", "inherit", "inherit"],
  });
  if (run.error) {
    console.error(`  FALHA ao chamar o doctor: ${String(run.error.message)}`);
    process.exit(1);
  }
  const after = pending();
  if (!after) {
    console.log(`  session stores importados para SQLite (assert do start aprovou, tentativa ${attempt})`);
    process.exit(0);
  }
  if (attempt === 3) {
    console.error(`  FALHA: o session store legado continua pendente após 3 tentativas: ${after}`);
    process.exit(1);
  }
  console.log(`  ainda pendente após a tentativa ${attempt}, repetindo: ${after.split(". Run ")[0]}`);
}
NODE
