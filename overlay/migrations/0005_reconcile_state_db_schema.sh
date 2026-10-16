#!/bin/bash
# Migration 0005: reconcile_state_db_schema
# Idempotente. Roda uma vez por instância quando ela sobe além desta versão.
# $LZBOT_MIGRATION_BACKUP_DIR está setado — faz backup de tudo que toca ANTES.
#
# Regras: só ADICIONA o que falta, ou remove algo que esta migration reconhece
# explicitamente. Nunca editar esta migration depois que ela shippar — se estiver
# errada, escreva uma nova.
set -euo pipefail

echo "[migration 0005] reconcile_state_db_schema"

# Reconcilia o SQLite de estado (`<state>/state/openclaw.sqlite`) com o schema
# canônico do dist do OpenClaw INSTALADO, antes do gateway abrir o banco.
#
#### O bug de plataforma que motiva isto
#
# O OpenClaw tem reconciliador próprio de schema (`ensureColumn`: guarda de
# `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`), chamado no boot por
# `ensureSchema`. Ele é INCOMPLETO e ASSIMÉTRICO:
#
#  1. Nem toda coluna do `CREATE TABLE` canônico tem `ensureColumn` correspondente.
#     Como o `CREATE TABLE` é `IF NOT EXISTS`, tabela que já existe NUNCA ganha a
#     coluna nova -> drift silencioso, e o INSERT quebra em runtime.
#  2. A partir do user_version 15, a reconciliação de tabela existente é SÓ o
#     `ensureAdditiveStateColumns`; não há caminho que remova coluna.
#  3. Pior: o validador de boot (`assertCurrentStateRuntimeSchema`) recusa os DOIS
#     sentidos — tem código `missing-column` E `unexpected-column`. Coluna a MAIS
#     é tão fatal quanto coluna a MENOS, e a mensagem ("column definitions differ
#     for <tabela>") não diz qual coluna.
#
# Medido nesta instância (05/set/2026), subindo 2026.7.1-2 -> 2026.9.x:
# a 2026.9.x ESTREITOU duas tabelas — o dado agora mora num blob JSON, mesmo
# movimento que o `cron_jobs`/`job_json` já tinha feito:
#
#   subagent_runs                  45 col (7.1)  ->  6 col (9.x, `payload_json`)
#   current_conversation_bindings  17 col (7.1)  -> 15 col (9.x, `record_json`)
#
# Um volume vindo da 7.1 chega com as colunas velhas e o boot da 9.x morre em
# `SQLite schema is incomplete or noncanonical`. É fail-closed: gateway não sobe.
#
#### Desenho: três camadas, e o canônico vem do VENDOR, não de regex
#
# (0) ESCADA DE SCHEMA DO VENDOR (`repairOpenClawStateDatabaseSchemaIfNeeded`).
#     Roda primeiro e é quem faz o trabalho pesado num volume 7.1: reconstrói as
#     tabelas que a 9.x estreitou, dobrando o dado velho nos blobs JSON novos e
#     recriando os índices. As camadas abaixo viram rede de segurança para o
#     drift aditivo que o ladder do vendor não cobre.
#
# (a) REPLAY dos `ensureColumn` declarados pelo dist instalado. É o SQL e o
#     DEFAULT exatos do vendor, aplicados sob guarda — pega o que o version-gate
#     pulou sem inventar coluna nenhuma.
#
# (b) RECONCILIAÇÃO BIDIRECIONAL (ADD faltante + DROP inesperada) de uma
#     ALLOWLIST curada de tabelas. Só as duas acima. Tabela fora da lista não é
#     tocada, nem para adicionar nem para remover.
#
# A fonte da verdade do "canônico" NÃO é regex no dist minificado — isso dá
# falso-positivo (DDL fantasma de outra versão dentro do mesmo bundle, tabela
# compacta por design que parece "faltando coluna"). Em vez disso a migration
# manda o PRÓPRIO VENDOR construir um banco de referência VAZIO num tmpdir e lê
# `PRAGMA table_info` dele. O que o produto cria do zero É, por definição, o que
# o produto aceita — imune a mudança de hash de arquivo, de minificação e de
# versão. A camada (a) também é filtrada por essa referência: `ensureColumn` cuja
# coluna não existe no canônico é IGNORADO.
#
# Gate final, mesma doutrina do `config validate` no README: no fim a migration
# reabre o banco com o `openOpenClawStateDatabase` do vendor. Se o produto não
# aceitar, a migration FALHA (exit != 0) e o boot para, com o backup no lugar.
# Exit 0 do script provaria só que ele não quebrou; quem sabe se o schema está
# bom é o validador do produto.

STATE_DIR="${OPENCLAW_STATE_DIR:-/app}"

if ! command -v openclaw >/dev/null 2>&1; then
  echo "  openclaw não está no PATH — nada a reconciliar, pulando"
  exit 0
fi

# dist do openclaw instalado: .../node_modules/openclaw/dist. Resolvido a partir
# do binário real (readlink -f), não de caminho fixo de nvm/versão de node.
OPENCLAW_ENTRY="$(readlink -f "$(command -v openclaw)")"
OPENCLAW_DIST="$(dirname "$OPENCLAW_ENTRY")/dist"
if [ ! -d "$OPENCLAW_DIST" ]; then
  # layout alternativo: bin/ ao lado de dist/
  OPENCLAW_DIST="$(dirname "$(dirname "$OPENCLAW_ENTRY")")/dist"
fi
if [ ! -d "$OPENCLAW_DIST" ]; then
  echo "  não achei o dist do openclaw a partir de $OPENCLAW_ENTRY — pulando" >&2
  exit 0
fi

OPENCLAW_DIST="$OPENCLAW_DIST" \
OPENCLAW_STATE_DIR="$STATE_DIR" \
LZBOT_MIGRATION_BACKUP_DIR="${LZBOT_MIGRATION_BACKUP_DIR:-}" \
node --input-type=module <<'NODE'
import { readdirSync, readFileSync, existsSync, copyFileSync, mkdtempSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { DatabaseSync } from "node:sqlite";

const distDir = process.env.OPENCLAW_DIST;
const backupDir = process.env.LZBOT_MIGRATION_BACKUP_DIR || "";

//// Carregando o módulo de state DB do dist instalado
// O nome do arquivo carrega hash de build (openclaw-state-db-<hash>.js) e muda a
// cada release — por isso a busca é por CONTEÚDO (a declaração da função), não
// por nome. Idem para pegar as funções: os exports do bundle são letras
// minificadas ("i", "l", ...) e a letra troca entre versões; o `.name` da função
// sobrevive.
const files = readdirSync(distDir).filter((f) => /^openclaw-state-db.*\.m?js$/.test(f));
const holders = files.filter((f) =>
  readFileSync(path.join(distDir, f), "utf8").includes("function openOpenClawStateDatabase"),
);
if (holders.length !== 1) {
  console.log(`  não consegui identificar o módulo de state DB (candidatos: ${holders.length}) — pulando`);
  process.exit(0);
}
// As duas funções que precisamos não vivem no mesmo chunk (o resolvedor de path
// está num módulo `.paths-<hash>.js` à parte), então a varredura é por arquivo.
const byName = (mods, name) => {
  for (const m of mods) {
    const fn = Object.values(m).find((v) => typeof v === "function" && v.name === name);
    if (fn) return fn;
  }
  return undefined;
};
const mods = [];
for (const f of files) {
  try {
    mods.push(await import(path.join(distDir, f)));
  } catch {
    // chunk que não carrega isolado não nos interessa
  }
}
const openStateDb = byName(mods, "openOpenClawStateDatabase");
const resolveStatePath = byName(mods, "resolveOpenClawStateSqlitePath");
if (!openStateDb || !resolveStatePath) {
  console.log("  dist não expõe open/resolve do state DB — pulando");
  process.exit(0);
}

const target = resolveStatePath(process.env);
if (!existsSync(target)) {
  console.log(`  ${target} não existe (instância nova) — o vendor cria canônico, nada a fazer`);
  process.exit(0);
}

//// Backup antes de qualquer escrita
// Copia o -wal/-shm junto: um .sqlite sem o seu WAL é um backup que perde as
// últimas transações, calado.
if (backupDir) {
  mkdirSync(backupDir, { recursive: true });
  for (const suffix of ["", "-wal", "-shm"]) {
    const src = `${target}${suffix}`;
    if (existsSync(src)) copyFileSync(src, path.join(backupDir, path.basename(src)));
  }
  console.log(`  backup do state DB em ${backupDir}`);
}

//// Camada (0): a escada de migração de schema do PRÓPRIO VENDOR
// Tem que vir ANTES das nossas camadas, e é ela que faz o trabalho de verdade
// num volume vindo da 7.1: `repairOpenClawStateDatabaseSchemaIfNeeded` roda o
// ladder versionado (v10..v16 + STRICT typing) que RECONSTRÓI a tabela — dobra o
// dado das colunas velhas nos blobs JSON novos (`payload_json`, `record_json`) e
// recria os índices. Sobe `user_version` 1 -> 16.
//
// Por que isto e não `openclaw doctor --fix` no entrypoint: o doctor faz a mesma
// migração de state, mas de carona com instalação de plugin, reparo de registry
// e reescrita de config — e rodaria ANTES da 0004, que ainda não reconciliou o
// config. Esta função é o subconjunto exato de que a 0005 depende, sem efeito
// colateral e sem mudar a ordem de boot.
//
// Sem ela as nossas camadas encalham: `subagent_runs.ended_at` (7.1) não existe
// no canônico da 9.3 e está no índice `idx_subagent_runs_ended_cleanup`, então a
// camada (b) bate no guard de PK/índice e a migration falha fail-closed —
// corretamente, porque DROP COLUMN ali jogaria fora dado que o vendor dobra.
const repairSchema = byName(mods, "repairOpenClawStateDatabaseSchemaIfNeeded");
if (!repairSchema) {
  // Não é fatal: as camadas abaixo seguem valendo como rede de segurança para o
  // drift aditivo que o vendor não cobre.
  console.log("  dist não expõe repairOpenClawStateDatabaseSchemaIfNeeded — seguindo só com as camadas (a)/(b)");
} else {
  try {
    const res = await repairSchema();
    const applied = Array.isArray(res?.changes) ? res.changes : [];
    if (applied.length === 0) {
      console.log("  (0) escada de schema do vendor: nada a aplicar");
    } else {
      for (const c of applied) console.log(`  (0) vendor: ${c}`);
    }
    for (const w of Array.isArray(res?.warnings) ? res.warnings : []) {
      console.log(`  (0) vendor AVISO: ${w}`);
    }
  } catch (err) {
    console.error(`  FALHA na escada de schema do vendor: ${String(err?.message ?? err)}`);
    process.exit(1);
  }
}

//// Schema canônico: construído pelo próprio vendor num banco vazio
const refDir = mkdtempSync(path.join(os.tmpdir(), "lzbot-schema-ref-"));
const refPath = path.join(refDir, "openclaw.sqlite");
let canonical;
try {
  const ref = openStateDb({ path: refPath });
  canonical = new Map();
  const tables = ref.db
    .prepare("SELECT name FROM sqlite_master WHERE type = 'table'")
    .all()
    .map((r) => r.name);
  for (const t of tables) {
    canonical.set(t, new Map(ref.db.prepare(`PRAGMA table_info("${t}")`).all().map((c) => [c.name, c])));
  }
} catch (err) {
  console.error(`  FALHA construindo o schema de referência: ${String(err?.message ?? err)}`);
  process.exit(1);
}

const db = new DatabaseSync(target);
db.exec("PRAGMA busy_timeout = 30000;");
const tableExists = (t) =>
  db.prepare("SELECT 1 AS ok FROM sqlite_master WHERE type = 'table' AND name = ?").get(t)?.ok === 1;
const columnsOf = (t) => new Map(db.prepare(`PRAGMA table_info("${t}")`).all().map((c) => [c.name, c]));
const changes = [];

//// Camada (a): replay dos ensureColumn do dist instalado
// Só aplica quando: a tabela existe aqui, a coluna falta aqui, E a coluna existe
// no canônico do vendor. O último filtro é o que mata falso-positivo — bundle
// tem declaração de tabela que a versão instalada não usa mais.
const decls = new Map();
for (const f of files) {
  const src = readFileSync(path.join(distDir, f), "utf8");
  for (const m of src.matchAll(/ensureColumn\(\s*\w+\s*,\s*"([a-zA-Z0-9_]+)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)/g)) {
    const table = m[1];
    const columnSql = m[2].replace(/\\(.)/g, "$1");
    const column = columnSql.trim().split(/\s+/, 1)[0];
    if (column) decls.set(`${table}.${column}`, { table, column, columnSql });
  }
}
for (const { table, column, columnSql } of decls.values()) {
  if (!tableExists(table)) continue;
  if (columnsOf(table).has(column)) continue;
  if (!canonical.get(table)?.has(column)) continue;
  db.exec(`ALTER TABLE "${table}" ADD COLUMN ${columnSql};`);
  changes.push(`(a) ${table}: + ${column}`);
}

//// Camada (b): reconciliação bidirecional, allowlist curada
// Só estas duas. São as que a 2026.9.x estreitou e as únicas com drift provado
// em runtime nesta instância. Reconciliar tabela fora da lista às cegas é como
// se quebra um banco: tabela compacta por design (cron_jobs guarda o job inteiro
// em job_json) parece "faltando 30 colunas" para qualquer comparação mecânica.
const ALLOWLIST = ["subagent_runs", "current_conversation_bindings"];

const indexedColumns = (t, d = db) => {
  const cols = new Set();
  for (const idx of d.prepare(`PRAGMA index_list("${t}")`).all()) {
    for (const c of d.prepare(`PRAGMA index_info("${idx.name}")`).all()) {
      if (c.name) cols.add(c.name);
    }
  }
  return cols;
};
const safeDefault = (col) => {
  if (col.dflt_value !== null && col.dflt_value !== undefined) return String(col.dflt_value);
  return /INT|REAL|NUM/i.test(String(col.type || "")) ? "0" : "''";
};

// Captura os CREATE TABLE e CREATE INDEX canônicos do banco de referência pra
// poder reconstruir tabelas quando colunas sobrando estão em índice.
const refDb = new DatabaseSync(refPath, { open: true, readOnly: true });
const canonicalDDL = new Map();
for (const table of ALLOWLIST) {
  if (!canonical.has(table)) continue;
  const createSql = refDb.prepare(
    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?"
  ).get(table)?.sql;
  const indexes = refDb.prepare(
    "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL"
  ).all(table);
  canonicalDDL.set(table, { createSql, indexes });
}
refDb.close();

// Rebuild de tabela: cria nova com schema canônico, copia só colunas canônicas,
// substitui a original, recria índices. Usado quando coluna sobrando está em
// índice (DROP COLUMN não funciona nesse caso no SQLite).
const rebuildTable = (table, want, have) => {
  const ddl = canonicalDDL.get(table);
  if (!ddl?.createSql) {
    console.error(`  FALHA: sem DDL canônico pra rebuild de ${table}`);
    process.exit(1);
  }
  const tmpName = `_lzbot_rebuild_${table}`;
  const wantCols = [...want.keys()];
  const selectCols = wantCols.filter((c) => have.has(c)).map((c) => `"${c}"`);
  const insertCols = wantCols.filter((c) => have.has(c)).map((c) => `"${c}"`);

  db.exec(`DROP TABLE IF EXISTS "${tmpName}";`);
  db.exec(ddl.createSql.replace(
    new RegExp(`^CREATE TABLE\\s+"?${table}"?`, "i"),
    `CREATE TABLE "${tmpName}"`
  ));
  if (insertCols.length > 0) {
    db.exec(`INSERT INTO "${tmpName}" (${insertCols.join(", ")}) SELECT ${selectCols.join(", ")} FROM "${table}";`);
  }
  db.exec(`DROP TABLE "${table}";`);
  db.exec(`ALTER TABLE "${tmpName}" RENAME TO "${table}";`);
  for (const idx of ddl.indexes) {
    db.exec(idx.sql + ";");
  }
};

for (const table of ALLOWLIST) {
  const want = canonical.get(table);
  if (!want || !tableExists(table)) continue;
  const have = columnsOf(table);

  for (const [name, col] of want) {
    if (have.has(name)) continue;
    if (Number(col.pk ?? 0) > 0) {
      console.error(`  FALHA: ${table}.${name} falta e é chave primária — não dá para ALTER ADD`);
      process.exit(1);
    }
    const notNull = Number(col.notnull ?? 0) === 1;
    const sql = `${name} ${col.type}${notNull ? ` NOT NULL DEFAULT ${safeDefault(col)}` : ""}`;
    db.exec(`ALTER TABLE "${table}" ADD COLUMN ${sql};`);
    changes.push(`(b) ${table}: + ${name}`);
  }

  const indexed = indexedColumns(table);
  let needsRebuild = false;
  const extraCols = [];
  for (const [name, col] of have) {
    if (want.has(name)) continue;
    if (Number(col.pk ?? 0) > 0) {
      console.error(`  FALHA: ${table}.${name} sobra e é PK — rebuild não resolve sem perda de identidade`);
      process.exit(1);
    }
    if (indexed.has(name)) {
      needsRebuild = true;
    }
    extraCols.push(name);
  }

  if (extraCols.length === 0) continue;

  if (needsRebuild) {
    rebuildTable(table, want, have);
    for (const name of extraCols) {
      changes.push(`(b) ${table}: - ${name} (rebuild — coluna em índice, não existe no schema canônico)`);
    }
  } else {
    for (const name of extraCols) {
      db.exec(`ALTER TABLE "${table}" DROP COLUMN "${name}";`);
      changes.push(`(b) ${table}: - ${name} (não existe no schema canônico desta versão)`);
    }
  }
}

db.close();

if (changes.length === 0) {
  console.log("  state DB já bate com o schema canônico — no-op");
} else {
  for (const c of changes) console.log(`  ${c}`);
}

//// Gate: quem diz se o schema está bom é o produto
try {
  openStateDb({ path: target });
} catch (err) {
  console.error(`  FALHA: o OpenClaw ainda recusa o state DB após reconciliar: ${String(err?.message ?? err)}`);
  process.exit(1);
}
console.log("  openOpenClawStateDatabase aceitou o state DB");
rmSync(refDir, { recursive: true, force: true });
NODE
