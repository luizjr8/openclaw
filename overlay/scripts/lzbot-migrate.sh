#!/bin/bash
# lzbot-migrate.sh — applies pending versioned migrations for this lzbot
# instance. Canonical, ships inside the image (dev/lzbot/scripts). Called by
# entrypoint.sh before handing off to the instance-specific entrypoint (or
# exec "$@").
#
# See migrations/README.md for the authoring workflow.
set -euo pipefail

# Resolve relative to this script's own location by default, so it works
# unmodified whether it ships baked at /lzbot (production images) or lives
# in a git checkout like /app/dev/lzbot (this creator instance).
LZBOT_ROOT="${LZBOT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LZBOT_IMAGE_VERSION_FILE="${LZBOT_IMAGE_VERSION_FILE:-$LZBOT_ROOT/VERSION}"
LZBOT_STATE_VERSION_FILE="${LZBOT_STATE_VERSION_FILE:-/app/.lzbot-version}"
LZBOT_MIGRATIONS_DIR="${LZBOT_MIGRATIONS_DIR:-$LZBOT_ROOT/migrations}"
LZBOT_MIGRATIONS_LOG="${LZBOT_MIGRATIONS_LOG:-/app/.lzbot-migrations-applied.json}"
LZBOT_BACKUP_ROOT="${LZBOT_BACKUP_ROOT:-/app/.migrations-backup}"

image_version=$(cat "$LZBOT_IMAGE_VERSION_FILE" 2>/dev/null || echo 0)

if [ ! -f "$LZBOT_STATE_VERSION_FILE" ]; then
  # No state file: either a brand-new instance OR a pre-existing one adopting
  # the versioning system for the first time. Every migration is contractually
  # idempotent — it only ADDS what is missing and never overwrites existing
  # values (see migrations/README.md) — so replaying them all from 0 is SAFE
  # against unknown historical state and self-heals adopted instances instead
  # of silently skipping config they still need. (Previously we stamped
  # image_version and exited WITHOUT replay, which left adopted instances
  # missing everything earlier migrations set up — e.g. the memory config.)
  echo "[lzbot-migrate] No $LZBOT_STATE_VERSION_FILE found — seeding version 0 and replaying idempotent migrations up to $image_version." >&2
  echo "0" > "$LZBOT_STATE_VERSION_FILE"
  if [ ! -f "$LZBOT_MIGRATIONS_LOG" ]; then
    printf '{"adopted_at_version": %s, "replayedFromZero": true, "applied": []}\n' "$image_version" > "$LZBOT_MIGRATIONS_LOG"
  fi
fi

state_version=$(cat "$LZBOT_STATE_VERSION_FILE")

if [ "$state_version" -ge "$image_version" ]; then
  echo "[lzbot-migrate] Already at version $state_version (image: $image_version). Nothing to do."
  exit 0
fi

echo "[lzbot-migrate] Migrating from version $state_version to $image_version..."

shopt -s nullglob
for migration in $(printf '%s\n' "$LZBOT_MIGRATIONS_DIR"/*.sh | sort); do
  base=$(basename "$migration")
  num_raw="${base%%_*}"
  num=$((10#$num_raw))

  if [ "$num" -le "$state_version" ]; then
    continue
  fi
  if [ "$num" -gt "$image_version" ]; then
    break
  fi

  echo "[lzbot-migrate] Applying $base..."
  backup_dir="$LZBOT_BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-$base"
  mkdir -p "$backup_dir"

  if LZBOT_MIGRATION_BACKUP_DIR="$backup_dir" bash "$migration"; then
    echo "$num" > "$LZBOT_STATE_VERSION_FILE"
    tmp_log=$(mktemp)
    node -e "
      const fs = require('fs');
      const path = '$LZBOT_MIGRATIONS_LOG';
      let log = { applied: [] };
      try { log = JSON.parse(fs.readFileSync(path, 'utf8')); } catch {}
      log.applied = log.applied || [];
      log.applied.push({ num: $num, name: '$base', appliedAt: new Date().toISOString(), backupDir: '$backup_dir' });
      fs.writeFileSync(path, JSON.stringify(log, null, 2));
    " 2>/dev/null || true
    echo "[lzbot-migrate] $base applied OK, state now at $num."
  else
    echo "[lzbot-migrate] FAILED applying $base. Backup at $backup_dir. Refusing to continue boot." >&2
    exit 1
  fi
done

echo "[lzbot-migrate] Migration complete, now at version $(cat "$LZBOT_STATE_VERSION_FILE")."
