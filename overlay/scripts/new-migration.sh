#!/bin/bash
# new-migration.sh — scaffolds a new numbered migration and bumps VERSION.
#
# Usage: scripts/new-migration.sh "short_description_snake_case"
#
# The version IS the highest migration number — there is no separate manual
# version bump. See migrations/README.md for when a migration is warranted.
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 \"short_description\"" >&2
  exit 1
fi

cd "$(dirname "$0")/.."

last=0
for f in migrations/*.sh; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  n=$((10#${base%%_*}))
  if [ "$n" -gt "$last" ]; then
    last=$n
  fi
done

next=$((last + 1))
next_padded=$(printf "%04d" "$next")
slug=$(echo "$1" | tr '[:upper:] ' '[:lower:]_' | tr -cd 'a-z0-9_')
file="migrations/${next_padded}_${slug}.sh"

if [ -e "$file" ]; then
  echo "Refusing to overwrite existing $file" >&2
  exit 1
fi

cat > "$file" <<EOF
#!/bin/bash
# Migration ${next_padded}: $1
# Idempotent. Runs once per instance when it upgrades past this version.
# \$LZBOT_MIGRATION_BACKUP_DIR is set — back up any file you modify there
# BEFORE touching it, e.g.:
#   cp -a /app/openclaw.json "\$LZBOT_MIGRATION_BACKUP_DIR/openclaw.json"
#
# Rules: only ADD missing things, or remove a key/file this migration
# explicitly recognizes as deprecated. Never delete anything you don't
# recognize. Never edit this file after it ships — write a new migration
# instead if something was wrong.
set -euo pipefail

echo "[migration ${next_padded}] $1"

# TODO: implement.
EOF
chmod +x "$file"

echo "$next" > VERSION

echo "Created $file"
echo "VERSION bumped to $next"
