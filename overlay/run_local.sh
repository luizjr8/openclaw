#!/bin/bash

## Script de dev: limpa estado, prepara workspace e sobe o container
##
## Flags do script (o resto dos args vai para o openclaw):
##   --keep              mantém app/ e o que já está instalado
##   --version <spec>    força a versão do openclaw (senão usa OPENCLAW_VERSION do .env)
##   --clean-cache       apaga o cache local do npm inteiro (.npm-local)

KEEP_WORKSPACE=false
CLEAN_CACHE=false
FORCE_VERSION=""
OPENCLAW_COMMAND="gateway"
OPENCLAW_SUBCOMMAND="run"
OPENCLAW_ARGS=()
COMMAND_SET=false

while [ $# -gt 0 ]; do
    ARG="$1"

    case "$ARG" in
        --keep)
            KEEP_WORKSPACE=true
            ;;
        --clean-cache)
            CLEAN_CACHE=true
            ;;
        --version=*)
            FORCE_VERSION="${ARG#--version=}"
            ;;
        --version)
            if [ -z "$2" ]; then
                echo "Uso: --version <versao> (ex: --version 2026.9.1)" >&2
                exit 1
            fi

            FORCE_VERSION="$2"
            shift
            ;;
        *)
            if [ "$COMMAND_SET" = false ]; then
                OPENCLAW_COMMAND="$ARG"
                COMMAND_SET=true

                if [ "$ARG" != "gateway" ]; then
                    OPENCLAW_SUBCOMMAND=""
                fi
            else
                OPENCLAW_ARGS+=("$ARG")
            fi
            ;;
    esac

    shift
done

## Resetar workspace a partir do template (ou manter com --keep)
if [ "$KEEP_WORKSPACE" = true ]; then
    echo "Mantendo workspace existente..."
else
    echo "Recriando workspace a partir do template..."
    rm -rf app
    mkdir app
    cp -r workspace app/workspace
    cp -r extensions app/extensions
    cp -r skills app/skills
    cp openclaw.json app/openclaw.json
    cp agents.json5 app/agents.json5
    cp -r assets app/assets

    ## node_modules do repo não é usado na execução (o npx instala no cache
    ## local); remover evita ficar com uma árvore de deps de outra versão
    rm -rf node_modules
fi

## Ler o .env para a sessão
set -a
source .env
set +a

## Ajustar workspace local após carregar o .env
export OPENCLAW_WORKSPACE_DIR="$(pwd)/app/workspace"
export OPENCLAW_STATE_DIR="$(pwd)/app/"

## Cache do npm/npx isolado no projeto (não usa o ~/.npm da máquina).
## Evita que troca de versão do bin do openclaw reaproveite libs de outra versão.
export NPM_CONFIG_CACHE="$(pwd)/.npm-local"
export npm_config_cache="$NPM_CONFIG_CACHE"

## Raiz de projeto vazia para o npx: sem ela o npx resolveria o
## ./node_modules/openclaw do repo (com as deps hoisted, fonte da inconsistência)
NPX_PREFIX="$NPM_CONFIG_CACHE/_root"

if [ "$CLEAN_CACHE" = true ]; then
    echo "Limpando cache local do npm ($NPM_CONFIG_CACHE)..."
    rm -rf "$NPM_CONFIG_CACHE"
elif [ "$KEEP_WORKSPACE" = false ]; then
    ## Sem --keep: descarta as árvores já instaladas para o npx baixar de novo.
    ## O _cacache fica (é indexado por integridade, então não mistura versão)
    echo "Descartando instalações do npx (mantendo os tarballs baixados)..."
    rm -rf "$NPM_CONFIG_CACHE/_npx" "$NPX_PREFIX"
fi

mkdir -p "$NPX_PREFIX"

## Versão: --version manda, depois OPENCLAW_VERSION do .env, e por último
## o ARG do Dockerfile (mesma versão que roda no container)
if [ -n "$FORCE_VERSION" ]; then
    OPENCLAW_VERSION="$FORCE_VERSION"
elif [ -z "$OPENCLAW_VERSION" ]; then
    OPENCLAW_VERSION="$(grep -m1 '^ARG OPENCLAW_VERSION=' Dockerfile | cut -d= -f2)"
fi

if [ -n "$OPENCLAW_VERSION" ]; then
    OPENCLAW_SPEC="openclaw@$OPENCLAW_VERSION"
else
    OPENCLAW_SPEC="openclaw"
fi

## Regerar o schema do openclaw.json para a versão que vai rodar, com os $ref
## dos plugins consertados (ver scripts/gen-schema.mjs). Falha aqui não impede
## de subir: só deixa o editor sem schema atualizado.
if ! npm_config_loglevel=warn npx --yes --prefix "$NPX_PREFIX" "$OPENCLAW_SPEC" config schema --json \
    | node scripts/gen-schema.mjs doc/schema.json; then
    echo "Aviso: não foi possível gerar doc/schema.json" >&2
fi

echo "Executando $OPENCLAW_SPEC (cache local: $NPM_CONFIG_CACHE)"

cd app

if [ "$OPENCLAW_SUBCOMMAND" != "" ]; then
    npx --yes --prefix "$NPX_PREFIX" "$OPENCLAW_SPEC" "$OPENCLAW_COMMAND" "$OPENCLAW_SUBCOMMAND" "${OPENCLAW_ARGS[@]}"
else
    npx --yes --prefix "$NPX_PREFIX" "$OPENCLAW_SPEC" "$OPENCLAW_COMMAND" "${OPENCLAW_ARGS[@]}"
fi
