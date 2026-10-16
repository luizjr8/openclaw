#!/bin/bash
# Migration 0002: ensure_qmd_memory_config
# Idempotent. Runs once per instance when it upgrades past this version.
# $LZBOT_MIGRATION_BACKUP_DIR is set — back up any file you modify there
# BEFORE touching it, e.g.:
#   cp -a /app/openclaw.json "$LZBOT_MIGRATION_BACKUP_DIR/openclaw.json"
#
# Rules: only ADD missing things, or remove a key/file this migration
# explicitly recognizes as deprecated. Never delete anything you don't
# recognize. Never edit this file after it ships — write a new migration
# instead if something was wrong.
set -euo pipefail

echo "[migration 0002] ensure_qmd_memory_config"

# Garante o bloco global memory.* (qmd + embed gemma via env do Dockerfile,
# searchMode query, etc.) em /app/openclaw.json — SÓ preenche chaves
# AUSENTES, nunca sobrescreve valor já existente (uma instância pode ter
# customizado rerank, timeouts, etc. de propósito).
#
# O que NÃO entra aqui, de propósito (é julgamento por instância, não
# mecânico): o padrão "coder com query via builtin" (memorySearch.extraPaths
# em vez do daemon completo) depende de quais agentes existem na instância e
# quais arquivos de memória fazem sentido compartilhar — ver
# migrations/README.md, seção "Casos que exigem julgamento (LLM), não script".

export OPENCLAW_JSON="${OPENCLAW_JSON:-/app/openclaw.json}"

if [ ! -f "$OPENCLAW_JSON" ]; then
  echo "[migration 0002] $OPENCLAW_JSON não existe — pulando." >&2
  exit 0
fi

if [ -n "${LZBOT_MIGRATION_BACKUP_DIR:-}" ]; then
  cp -a "$OPENCLAW_JSON" "$LZBOT_MIGRATION_BACKUP_DIR/openclaw.json.before"
fi

node -e '
const fs = require("fs");
const path = process.env.OPENCLAW_JSON;
const cfg = JSON.parse(fs.readFileSync(path, "utf8"));

const canonicalMemory = {
  backend: "qmd",
  qmd: {
    searchMode: "query",
    rerank: false,
    limits: { timeoutMs: 45000 },
    update: { embedTimeoutMs: 600000 },
    includeDefaultMemory: false,
    paths: [
      { name: "memory", path: "memory", pattern: "*.md" },
      { name: "memory-root", path: "MEMORY.md" },
    ],
    sessions: { enabled: true },
    scope: { default: "allow" },
    mcporter: { enabled: true, serverName: "qmd", startDaemon: true },
  },
};

// Deep-merge: valor já existente SEMPRE vence; só preenche o que falta.
function fillMissing(target, defaults) {
  let changed = false;
  for (const key of Object.keys(defaults)) {
    if (!(key in target)) {
      target[key] = defaults[key];
      changed = true;
    } else if (
      typeof defaults[key] === "object" &&
      defaults[key] !== null &&
      !Array.isArray(defaults[key]) &&
      typeof target[key] === "object" &&
      target[key] !== null &&
      !Array.isArray(target[key])
    ) {
      if (fillMissing(target[key], defaults[key])) changed = true;
    }
  }
  return changed;
}

cfg.memory = cfg.memory || {};
const changed = fillMissing(cfg.memory, canonicalMemory);

if (changed) {
  fs.writeFileSync(path, JSON.stringify(cfg, null, 2) + "\n");
  console.log("[migration 0002] memory.* atualizado (chaves ausentes preenchidas).");
} else {
  console.log("[migration 0002] memory.* já completo, nada a fazer.");
}
'
