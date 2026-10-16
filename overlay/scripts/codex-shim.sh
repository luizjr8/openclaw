#!/bin/bash
# `codex` no PATH = shim do plugin @openclaw/codex instalado no state dir.
#
# O CLI NÃO é instalado à parte de propósito: o plugin já traz a própria cópia
# (`@openai/codex-linux-<arch>`, 336 MB) e é ELA que ele roda como app-server —
# a doc é explícita que a versão fica presa ao plugin, "instead of whichever
# separate Codex CLI happens to be installed locally". Um `npm install -g
# @openai/codex` duplicaria 336 MB pra nada.
#
# Resolve por glob no state dir (o dir do projeto npm tem hash no nome), e
# aponta pro shim `.bin/codex` — assim não precisa saber arquitetura nem versão.
for shim in "${OPENCLAW_STATE_DIR:-/app}"/npm/projects/*/node_modules/@openclaw/codex/node_modules/.bin/codex; do
  [ -x "$shim" ] && exec "$shim" "$@"
done
echo "codex: plugin @openclaw/codex não instalado em ${OPENCLAW_STATE_DIR:-/app}/npm" >&2
echo "instale com: openclaw plugins install @openclaw/codex@<versao>" >&2
exit 127
