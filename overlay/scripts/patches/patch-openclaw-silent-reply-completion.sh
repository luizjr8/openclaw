#!/usr/bin/env bash
#
# patch-openclaw-silent-reply-completion
#
# Bug (openclaw 2026.7.1 → ainda vivo em 2026.9.7): em grupo o agente recebe as
# linhas imperativas mandando responder exatamente "NO_REPLY", mas o
# silentReplyPromptMode é forçado a "none" sempre que existe contexto de
# conversa (direct OU grupo). "none" liga suppressSilentTokenGuidance, que:
#   - troca "...or default to NO_REPLY." por "...or default to a silent
#     placeholder." (eufemismo: o token deixa de ser nomeado), e
#   - suprime a seção "## Silent Replies", que carrega a regra anti-append
#     ("never include NO_REPLY in real replies").
# Resultado: completion event de subagente cai em NO_REPLY e o resultado some —
# o evento não é persistido no transcript. Medido 2026-08-20: 4 engolidas.
#
# Fix: em grupo o modo volta a ser "generic" (token nomeado + seção presente).
# Direct segue "none" de propósito: lá a policy é hardcoded disallow, o token
# não existe e nomeá-lo seria errado.
#
# 2026-10-01 — reescrito para o dist 2026.9.7. O que mudou lá:
#   antes: directChatContext || groupChatContext || sourceReplyDeliveryMode...
#   agora: sessionStableConversationContext || sourceReplyDeliveryMode...
# Os dois flags foram fundidos num só (derivado de sourceConversationContextByMode,
# que já discrimina isDirectChat/isGroupChat internamente). Por isso NÃO dá pra
# simplesmente apagar o termo: isso mudaria o direct junto. A troca correta é
# pelo `isDirectChat`, que está declarado no mesmo escopo da expressão.
#
# Idempotente e all-or-nothing: se a âncora não bater (upgrade do openclaw), sai
# != 0 e deixa o dist intacto. TEM que rodar antes do exec do gateway — o node
# importa os módulos no start e patch depois disso não vale nada.
# Âncora por CONTEÚDO, nunca por filename: os nomes do dist são content-hash.
set -euo pipefail

MARK="__openclaw_group_silent_token_guidance_patch__"
OLD='const silentReplyPromptMode = sessionStableConversationContext || sourceReplyDeliveryMode === "message_tool_only" ? "none" : "generic";'
NEW='const silentReplyPromptMode = isDirectChat || sourceReplyDeliveryMode === "message_tool_only" ? "none" : "generic";'

# stderr de propósito: resolve_dist roda dentro de $( ), então tudo que fosse
# pra stdout viraria parte do valor da variável em vez de aparecer no boot log.
log() { echo "[patch-silent-reply] $*" >&2; }
fail() { log "ERRO: $*"; exit 1; }

# Carimbo de aplicação. O mtime do dist não prova ordem: update/extract também
# escrevem lá durante o boot. Este stamp é escrito só por este script, então dá
# pra comparar com o lstart do `node ... openclaw gateway` e saber se o patch
# entrou ANTES do import. /tmp é efêmero de propósito: só vale pro boot corrente.
STAMP="/tmp/openclaw/patch-silent-reply.stamp"
stamp() {
  mkdir -p /tmp/openclaw 2>/dev/null || true
  printf 'epoch=%s\ntime=%s\nstatus=%s\nfiles=%s\n' \
    "$(date +%s)" "$(date -Is)" "$1" "$2" >"$STAMP" 2>/dev/null || true
}

# NÃO depender do PATH: no entrypoint o PATH é o mínimo do sistema (sem nvm),
# então `command -v openclaw` falha no boot. Também não hardcodar versão do node
# — era o bug da versão anterior (v24.15.0 fixo, imagem já em v26.8.1: o script
# saía 0 dizendo "nada a fazer", falha silenciosa). Ordem: override -> glob do
# nvm -> PATH -> npm root -g. Só aceita candidato que tenha a âncora, e
# diferencia "não achei dir" de "achei sem âncora" (a versão antiga misturava os
# dois e mandava investigar o path errado).
resolve_dist() {
  local c found_dir=0
  for c in \
    ${OPENCLAW_DIST:-} \
    /home/lzbot/.nvm/versions/node/*/lib/node_modules/openclaw/dist \
    "$(dirname "$(readlink -f "$(command -v openclaw 2>/dev/null)" 2>/dev/null)" 2>/dev/null)/dist" \
    "$(npm root -g 2>/dev/null)/openclaw/dist"
  do
    [ -n "$c" ] && [ -d "$c" ] || continue
    found_dir=1
    # --include ANTES do padrão/path: depois do `--` o grep trata a flag como
    # operando e sai "No such file or directory" varrendo o dist inteiro.
    if grep -rlF --include='*.mjs' -e "$OLD" "$c" >/dev/null 2>&1 \
       || grep -rlF --include='*.mjs' -e "$MARK" "$c" >/dev/null 2>&1; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  [ "$found_dir" = 1 ] && fail "dist encontrado, mas sem a âncora silentReplyPromptMode — openclaw mudou, reescrever o patch"
  fail "dist do openclaw não encontrado (tentei OPENCLAW_DIST, glob do nvm, PATH, npm root -g)"
}

DIST="$(resolve_dist)"

# A expressão aparece em MAIS DE UM bundle do dist (o get-reply e o bundle de
# recovery de update). Patchar só o primeiro deixava o outro intacto e a 2a
# execução "aplicava" de novo noutro arquivo — parecia não-idempotente. Então:
# pega TODOS os arquivos com a âncora, de uma vez.
mapfile -t TARGETS < <(grep -rlF --include='*.mjs' -e "$OLD" "$DIST" 2>/dev/null || true)

if [ "${#TARGETS[@]}" -eq 0 ]; then
  # Âncora de DESCOBERTA tem que sobreviver ao próprio patch, senão a 2a
  # execução não acha nada e o script falha achando que o dist mudou.
  APPLIED="$(grep -rlF --include='*.mjs' -e "$MARK" "$DIST" 2>/dev/null | xargs -r -n1 basename | tr '\n' ',' || true)"
  [ -n "$APPLIED" ] || fail "não achei a expressão de silentReplyPromptMode em $DIST"
  log "já aplicado ($APPLIED) — skip"
  stamp skip "$APPLIED"
  exit 0
fi

for TARGET in "${TARGETS[@]}"; do
[ -f "$TARGET.orig" ] || cp -p "$TARGET" "$TARGET.orig"

python3 - "$TARGET" "$OLD" "$NEW" "$MARK" <<'PY'
import sys

target, old, new, mark = sys.argv[1:5]
s = open(target, encoding="utf-8").read()

n = s.count(old)
assert n == 1, f"âncora não bateu ({n} ocorrências)"

# isDirectChat precisa estar no MESMO escopo da expressão, senão o patch gera um
# ReferenceError só em runtime (node --check não pega). Checagem barata: existe
# a declaração antes do alvo e o balanço de chaves entre os dois é zero.
decl = s.find("const isDirectChat =")
pos = s.find(old)
assert 0 <= decl < pos, "isDirectChat não declarado antes da expressão"
seg = s[decl:pos]
assert seg.count("{") == seg.count("}"), "isDirectChat fora do escopo da expressão"

open(target, "w", encoding="utf-8").write(s.replace(old, f"{new} /* {mark} */"))
PY

node --check "$TARGET" 2>/dev/null || node --input-type=module --check <"$TARGET"
log "aplicado: $(basename "$TARGET")"
done

stamp applied "$(printf '%s,' "${TARGETS[@]##*/}")"
