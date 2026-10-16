#!/usr/bin/env bash
# Writer-claim fix (upstream openclaw#154572): sessions_spawn para filho com runtime
# claude-cli falha sempre com SessionTranscriptWriterClaimReboundError (~350ms).
# Afeta qualquer agente com models["anthropic/*"].agentRuntime.id = "claude-cli".
#
# Guard antes da leitura de historico do CLI (prepare.runtime) + trace no construtor
# do erro (transcript-write-context). Idempotente, all-or-nothing, node --check,
# restaura .orig em falha. Localiza os bundles por CONTEUDO: o hash no nome muda
# a cada build do openclaw.
#
# ESTADO EM 2026-10-08 (verificado contra o dist do 2026.9.9):
#   - O defeito segue VIVO e cresceu: throw sites do erro foram de 118 (9.7) para
#     120 (9.9). As duas ancoras batem 1x no 9.9.
#   - O guard nao registra skip desde 2026-10-03 15:31, mas o trace registrou 18
#     disparos DEPOIS disso (15 em 04/10, 3 em 07/10). Ou seja: o erro que ainda
#     acontece nao passa pelo site que o guard cobre, esta em outro dos 120.
#   - Por isso o trace foi RESTAURADO aqui. O commit 6844330 removeu-o dizendo
#     "diagnostico encerrado", o que era falso: a cacada do site real esta aberta.
#
# CONDICAO DE RETIRADA (cada um sai sozinho, nao junto):
#   - wc-trace-patch.mjs: sai quando o site real do erro estiver identificado e
#     corrigido, OU quando wc-stack.log ficar vazio por um ciclo inteiro de
#     release. E instrumentacao, nao fix — nao pode virar permanente.
#   - wc-cli-guard.mjs: sai quando upstream#154572 fechar. Ausencia de skip NAO e
#     evidencia de conserto: pode ser caminho nao exercido.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="${OPENCLAW_DIST:-$(npm root -g 2>/dev/null)/openclaw/dist}"
[ -d "$DIST" ] || { echo "  writer-claim: AVISO dist nao encontrado ($DIST)" >&2; exit 0; }
node "$DIR/wc-cli-guard.mjs"   "$DIST" || true
node "$DIR/wc-trace-patch.mjs" "$DIST" || true
