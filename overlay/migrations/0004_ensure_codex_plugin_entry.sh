#!/bin/bash
# Migration 0004: ensure_codex_plugin_entry
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

echo "[migration 0004] ensure_codex_plugin_entry"

# O plugin `codex` (harness OpenAI app-server) passou a ser assado na imagem
# em /lzbot/extensions/codex, do mesmo jeito que o do WhatsApp. O plugin é
# carregado por path (plugins.load.paths = ${OPENCLAW_STATE_DIR}/extensions/),
# mas o openclaw.json de uma instância já existente é sombreado pelo volume e
# nunca recebe o `plugins.entries.codex` do seed — sem essa chave o plugin
# fica dependendo de default implícito, que é exatamente o tipo de coisa que
# some calada num upgrade. Só adiciona se ainda não existir; nunca sobrescreve
# escolha de quem já configurou (inclusive quem deixou enabled:false de
# propósito).
OPENCLAW_JSON="${OPENCLAW_JSON:-/app/openclaw.json}"

if [ ! -f "$OPENCLAW_JSON" ]; then
  echo "[migration 0004] $OPENCLAW_JSON não existe — pulando." >&2
  exit 0
fi

if [ -n "${LZBOT_MIGRATION_BACKUP_DIR:-}" ]; then
  cp -a "$OPENCLAW_JSON" "$LZBOT_MIGRATION_BACKUP_DIR/openclaw.json.before-0004"
fi

# Config com comentários (o seed do boilerplate tem) não é JSON estrito e
# quebra o JSON.parse. Nesse caso o arquivo é o seed recém-copiado da imagem —
# que já traz plugins.entries.codex — então a migration não tem nada a fazer e
# NÃO pode derrubar o boot por isso. Só avisa se o texto realmente não citar a
# chave.
OPENCLAW_JSON="$OPENCLAW_JSON" node -e '
const fs = require("fs");
const path = process.env.OPENCLAW_JSON;
const raw = fs.readFileSync(path, "utf8");
let cfg;
try {
  cfg = JSON.parse(raw);
} catch (err) {
  const hasEntry = /"codex"\s*:\s*\{/.test(raw);
  console.log("[migration 0004] " + path + " não é JSON estrito (" + err.message + ") — pulando." +
    (hasEntry ? " A chave codex já aparece no arquivo." : " ⚠️  A chave codex NÃO aparece: adicione plugins.entries.codex na mão."));
  process.exit(0);
}

cfg.plugins = cfg.plugins || {};
cfg.plugins.entries = cfg.plugins.entries || {};

if ("codex" in cfg.plugins.entries) {
  console.log("[migration 0004] plugins.entries.codex já existe, nada a fazer.");
} else {
  cfg.plugins.entries.codex = { enabled: true };
  fs.writeFileSync(path, JSON.stringify(cfg, null, 2) + "\n");
  console.log("[migration 0004] plugins.entries.codex adicionado (enabled: true).");
}
'
