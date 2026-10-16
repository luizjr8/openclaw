#!/usr/bin/env bash
## Builda a imagem do lzbot.
##
## Origem do OpenClaw:
##   ./scripts/build-image.sh            -> upstream publicado (npm), default
##   ./scripts/build-image.sh --fork     -> nosso fork, no SHA pinado em vendor/openclaw
##
## O SHA NUNCA é digitado à mão: vem do submodule vendor/openclaw, que é a fonte
## única da verdade do que a imagem contém. Isso é o que mantém "imagem buildada"
## e "source auditável" amarrados — sem isso o fork vira caixa-preta.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SOURCE=npm
IMAGE_TAG=""   # resolvido depois, quando a origem ja e conhecida
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fork) SOURCE=fork; shift ;;
    --npm) SOURCE=npm; shift ;;
    --tag) IMAGE_TAG="$2"; shift 2 ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

BUILD_ARGS=(--build-arg "OPENCLAW_SOURCE=${SOURCE}")

## A tag nomeia a VERSAO DO OPENCLAW que a imagem contem — e so isso identifica
## uma imagem no host. NAO usar o arquivo VERSION: ele e o contador de migration
## do lzbot (hoje "9"), e geraria `lzbot:9`, colidindo a cada release do openclaw.
if [[ -z "$IMAGE_TAG" ]]; then
  if [[ "$SOURCE" == "fork" ]]; then
    ## No fork a versao vem do package.json do submodule, que e o que o tarball
    ## buildado vai carregar. Sufixo com o SHA curto: fork e upstream da mesma
    ## versao base nao podem compartilhar tag.
    _ocver="$(node -p "require('$REPO_ROOT/vendor/openclaw/package.json').version" 2>/dev/null || true)"
    _ocsha="$(git -C vendor/openclaw rev-parse --short=12 HEAD 2>/dev/null || true)"
    [[ -n "$_ocver" && -n "$_ocsha" ]] || {
      echo "erro: nao consegui resolver versao/SHA do vendor/openclaw para a tag." >&2
      echo "      passe --tag lzbot:<versao> explicitamente." >&2
      exit 1
    }
    IMAGE_TAG="lzbot:${_ocver}-fork.${_ocsha}"
  else
    ## No modo npm a fonte da verdade e o ARG do Dockerfile — o mesmo valor que o
    ## `npm install -g openclaw@...` vai usar. Ler daqui impede imagem e tag de
    ## divergirem quando alguem bumpa so um dos dois.
    _ocver="$(sed -n 's/^ARG OPENCLAW_VERSION=\([^ ]*\).*/\1/p' Dockerfile | tail -1)"
    [[ -n "$_ocver" ]] || {
      echo "erro: ARG OPENCLAW_VERSION nao encontrado no Dockerfile." >&2
      exit 1
    }
    IMAGE_TAG="lzbot:${_ocver}"
  fi
fi

if [[ "$SOURCE" == "fork" ]]; then
  if [[ ! -e vendor/openclaw/.git ]]; then
    echo "erro: submodule vendor/openclaw ausente. Rode: git submodule update --init vendor/openclaw" >&2
    exit 1
  fi

  OPENCLAW_REF="$(git -C vendor/openclaw rev-parse HEAD)"

  # Divergência entre o submodule gravado no commit e o que está no disco gera
  # imagem que não corresponde a nenhum commit do lzbot. Barrar é mais barato
  # que descobrir depois por que a imagem não bate com o source.
  if ! git diff --quiet -- vendor/openclaw; then
    echo "erro: vendor/openclaw sujo ou fora do SHA registrado no lzbot." >&2
    echo "      commit o bump do submodule antes de buildar." >&2
    exit 1
  fi

  # A URL de push do fork é SSH; o builder do Docker não tem as nossas chaves,
  # então converte pra HTTPS (o fork é público).
  OPENCLAW_REPO="$(git -C vendor/openclaw remote get-url origin \
    | sed -e 's|^git@github.com:|https://github.com/|' -e 's|\.git$||').git"

  BUILD_ARGS+=(--build-arg "OPENCLAW_REF=${OPENCLAW_REF}")
  BUILD_ARGS+=(--build-arg "OPENCLAW_REPO=${OPENCLAW_REPO}")

  echo "openclaw: fork ${OPENCLAW_REPO} @ ${OPENCLAW_REF:0:12}"
  echo "patches no topo da base upstream:"
  git -C vendor/openclaw log --oneline "$(git -C vendor/openclaw describe --tags --abbrev=0)..HEAD" 2>/dev/null \
    | sed 's/^/  /' || echo "  (nenhuma tag base encontrada)"
else
  echo "openclaw: upstream npm (OPENCLAW_VERSION do Dockerfile)"
fi

echo "imagem: ${IMAGE_TAG}"

set -x
exec docker build "${BUILD_ARGS[@]}" -t "$IMAGE_TAG" "${EXTRA_ARGS[@]}" .
