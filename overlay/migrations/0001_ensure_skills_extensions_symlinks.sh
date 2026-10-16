#!/bin/bash
# Migration 0001: ensure_skills_extensions_symlinks
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

echo "[migration 0001] ensure_skills_extensions_symlinks"

# Instâncias criadas antes desta convenção (ex.: o sentinel, 2026.6.8) têm
# skills direto em /app/workspace/skills/, sem /app/skills, e /app/extensions
# como diretório real (não symlink). Este migration só cria o symlink pro
# /lzbot quando o caminho em /app está AUSENTE — nunca troca um diretório
# real já existente por symlink (poderia perder dado/customização). Um
# diretório real é só reportado e snapshotado no backup, pra revisão manual.
ensure_symlink() {
  local target="$1" # ex: /lzbot/skills
  local link="$2"   # ex: /app/skills

  if [ -L "$link" ]; then
    echo "[migration 0001] $link já é symlink -> $(readlink "$link"); mantendo como está."
    return 0
  fi

  if [ -e "$link" ]; then
    echo "[migration 0001] $link existe como caminho real (não symlink) — NÃO tocado; precisa revisão manual." >&2
    if [ -n "${LZBOT_MIGRATION_BACKUP_DIR:-}" ]; then
      cp -a "$link" "$LZBOT_MIGRATION_BACKUP_DIR/$(basename "$link").snapshot"
      echo "[migration 0001] snapshot salvo em $LZBOT_MIGRATION_BACKUP_DIR/$(basename "$link").snapshot"
    fi
    return 0
  fi

  if [ ! -e "$target" ]; then
    echo "[migration 0001] $target não existe — nada a linkar, pulando." >&2
    return 0
  fi

  ln -s "$target" "$link"
  echo "[migration 0001] criado $link -> $target"
}

ensure_symlink /lzbot/skills /app/skills
ensure_symlink /lzbot/extensions /app/extensions
