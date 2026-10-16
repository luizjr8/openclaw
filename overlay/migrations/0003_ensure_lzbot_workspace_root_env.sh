#!/bin/bash
# Migration 0003: ensure_lzbot_workspace_root_env
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

echo "[migration 0003] ensure_lzbot_workspace_root_env"

# Skills genéricas (financas, deepseek-saldo, ...) resolvem seus dados em
# <LZBOT_WORKSPACE_ROOT>/skills/<nome>/... com fallback pro cwd do processo
# se a env não existir (cwd normalmente já é o workspace do agente). Setar
# a env explicitamente remove essa dependência implícita — barato e seguro,
# só adiciona a chave se ainda não existir.
OPENCLAW_JSON="${OPENCLAW_JSON:-/app/openclaw.json}"
WORKSPACE_ROOT_VALUE="${LZBOT_WORKSPACE_ROOT_VALUE:-/app/workspace}"

if [ ! -f "$OPENCLAW_JSON" ]; then
  echo "[migration 0003] $OPENCLAW_JSON não existe — pulando." >&2
  exit 0
fi

if [ -n "${LZBOT_MIGRATION_BACKUP_DIR:-}" ]; then
  cp -a "$OPENCLAW_JSON" "$LZBOT_MIGRATION_BACKUP_DIR/openclaw.json.before-0003"
fi

OPENCLAW_JSON="$OPENCLAW_JSON" WORKSPACE_ROOT_VALUE="$WORKSPACE_ROOT_VALUE" node -e '
const fs = require("fs");
const path = process.env.OPENCLAW_JSON;
const value = process.env.WORKSPACE_ROOT_VALUE;
const cfg = JSON.parse(fs.readFileSync(path, "utf8"));

cfg.env = cfg.env || {};
cfg.env.vars = cfg.env.vars || {};

if ("LZBOT_WORKSPACE_ROOT" in cfg.env.vars) {
  console.log("[migration 0003] LZBOT_WORKSPACE_ROOT já existe (" + cfg.env.vars.LZBOT_WORKSPACE_ROOT + "), nada a fazer.");
} else {
  cfg.env.vars.LZBOT_WORKSPACE_ROOT = value;
  fs.writeFileSync(path, JSON.stringify(cfg, null, 2) + "\n");
  console.log("[migration 0003] LZBOT_WORKSPACE_ROOT adicionado: " + value);
}
'
