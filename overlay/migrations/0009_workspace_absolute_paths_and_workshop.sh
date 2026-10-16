#!/bin/bash
# Migration 0009: workspace_absolute_paths_and_workshop
# Idempotent. Runs once per instance when it upgrades past this version.
# $LZBOT_MIGRATION_BACKUP_DIR is set — back up any file you modify there
# BEFORE touching it.
#
# Two fixes:
#
# (a) agents.json5: workspace paths from relative ("workspace/") to absolute
#     ("${LZBOT_WORKSPACE_ROOT}"). Relative paths resolve against the agent
#     session cwd, not the workspace root — for the coder agent this doubled
#     the path (/app/workspace/coder/workspace/coder) and broke memory
#     indexing (extraPaths resolved to stubs, 432/347 orphans measured live).
#     The main agent got "workspace/" from 0006's fill, which worked only
#     because the session cwd happened to be /app — fragile and not
#     guaranteed. Using ${LZBOT_WORKSPACE_ROOT} (set by 0003) is correct.
#
# (b) openclaw.json: canonize skills.workshop.approvalPolicy = "auto".
#     Skills auto-apply without manual review — deliberate posture for the
#     lzbot boilerplate.
#
# Rules: only ADD missing things, or fix a known-wrong value this migration
# explicitly recognizes. Never delete anything you don't recognize. Never
# edit this file after it ships — write a new migration instead.
set -euo pipefail

echo "[migration 0009] workspace_absolute_paths_and_workshop"

LZBOT_JSONC_LIB="${LZBOT_JSONC_LIB:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/lib/jsonc-edit.mjs}"
AGENTS_JSON5="${AGENTS_JSON5:-/app/agents.json5}"
export LZBOT_JSONC_LIB AGENTS_JSON5

# ---------------------------------------------------------------------------
# STEP 1 — agents.json5: workspace relative → absolute via env var.
# ---------------------------------------------------------------------------

if [ -f "$AGENTS_JSON5" ]; then
  cp -a "$AGENTS_JSON5" "$LZBOT_MIGRATION_BACKUP_DIR/agents.json5.before-0009"

  node --input-type=module -e '
import fs from "node:fs";
const { readJsonc, editJsonc, hasIn } = await import(process.env.LZBOT_JSONC_LIB);
const file = process.env.AGENTS_JSON5;
const log = (m) => console.log("[migration 0009] " + m);

let { text, data } = readJsonc(file);
let out = text;

// Fix main workspace: "workspace/" → "${LZBOT_WORKSPACE_ROOT}"
if (data.main && data.main.workspace === "workspace/") {
  out = editJsonc(out, [{ op: "set", path: ["main", "workspace"], value: "${LZBOT_WORKSPACE_ROOT}" }]).text;
  log("main.workspace: \"workspace/\" → \"${LZBOT_WORKSPACE_ROOT}\"");
} else if (data.main && data.main.workspace) {
  log("main.workspace already set to \"" + data.main.workspace + "\", skipping.");
} else {
  // No workspace at all — fill it
  out = editJsonc(out, [{ op: "fill", path: ["main", "workspace"], value: "${LZBOT_WORKSPACE_ROOT}" }]).text;
  log("main.workspace: filled with \"${LZBOT_WORKSPACE_ROOT}\"");
}

// Fix coder workspace: fill if absent, set if "workspace/" (wrong relative)
if (data.coder) {
  if (!data.coder.workspace) {
    out = editJsonc(out, [{ op: "fill", path: ["coder", "workspace"], value: "${LZBOT_WORKSPACE_ROOT}/coder" }]).text;
    log("coder.workspace: filled with \"${LZBOT_WORKSPACE_ROOT}/coder\"");
  } else if (data.coder.workspace === "workspace/" || data.coder.workspace === "workspace/coder") {
    out = editJsonc(out, [{ op: "set", path: ["coder", "workspace"], value: "${LZBOT_WORKSPACE_ROOT}/coder" }]).text;
    log("coder.workspace: \"" + data.coder.workspace + "\" → \"${LZBOT_WORKSPACE_ROOT}/coder\"");
  } else {
    log("coder.workspace already set to \"" + data.coder.workspace + "\", skipping.");
  }

  // Also fix extraPaths if they use hardcoded /app/workspace
  const search = data.coder?.memory?.search;
  if (search?.extraPaths) {
    const fixed = search.extraPaths.map(p =>
      p.replace(/^\/app\/workspace\//, "${LZBOT_WORKSPACE_ROOT}/")
       .replace(/^\/app\/workspace$/, "${LZBOT_WORKSPACE_ROOT}")
    );
    const changed = fixed.some((f, i) => f !== search.extraPaths[i]);
    if (changed) {
      out = editJsonc(out, [{ op: "set", path: ["coder", "memory", "search", "extraPaths"], value: fixed }]).text;
      log("coder.memory.search.extraPaths: hardcoded paths → env var");
    }
  }
}

if (out !== text) {
  fs.writeFileSync(file, out);
  log("agents.json5 updated.");
} else {
  log("agents.json5 already correct, nothing to do.");
}
'
else
  echo "[migration 0009] agents.json5 not found at $AGENTS_JSON5, skipping step 1."
fi

# ---------------------------------------------------------------------------
# STEP 2 — openclaw.json: skills.workshop.approvalPolicy = "auto".
#
# openclaw.json is strict JSON — use JSON.parse/stringify, not editJsonc
# (see jsonc-edit.mjs header comment).
# ---------------------------------------------------------------------------

OPENCLAW_JSON="${OPENCLAW_JSON:-/app/openclaw.json}"
if [ -f "$OPENCLAW_JSON" ]; then
  cp -a "$OPENCLAW_JSON" "$LZBOT_MIGRATION_BACKUP_DIR/openclaw.json.before-0009"

  node -e '
const fs = require("fs");
const file = process.env.OPENCLAW_JSON || "/app/openclaw.json";
const log = (m) => console.log("[migration 0009] " + m);

const cfg = JSON.parse(fs.readFileSync(file, "utf8"));

if (!cfg.skills) cfg.skills = {};
if (!cfg.skills.workshop) cfg.skills.workshop = {};

if (cfg.skills.workshop.approvalPolicy === "auto") {
  log("skills.workshop.approvalPolicy already \"auto\", nothing to do.");
} else {
  const old = cfg.skills.workshop.approvalPolicy;
  cfg.skills.workshop.approvalPolicy = "auto";
  fs.writeFileSync(file, JSON.stringify(cfg, null, 2) + "\n");
  log("skills.workshop.approvalPolicy: " + (old ? "\"" + old + "\"" : "(absent)") + " → \"auto\"");
}
'
else
  echo "[migration 0009] openclaw.json not found at $OPENCLAW_JSON, skipping step 2."
fi

echo "[migration 0009] done."
