#!/bin/bash

## Script de dev: limpa estado, prepara workspace e sobe o container

KEEP_WORKSPACE=false
if [ "$1" = "--keep" ]; then
    KEEP_WORKSPACE=true
fi

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
    cp agents-extra.json5 app/agents-extra.json5
    cp -r assets app/assets
    # Entrypoint precisa existir no ./app montado como volume (senão o container não sobe)
    cp entrypoint.sh app/entrypoint.sh
    chmod +x app/entrypoint.sh
fi

# Garantir que não há container anterior com o arquivo aberto
docker compose down

## Subir container
docker compose up --build
