---
name: trello-helper
description: Auxilia operações no Trello via REST (boards, listas, cards, comentários, anexos) usando API Key + Token. Uso agnóstico: pedir board ID no primeiro uso e tratar Trello como fonte de verdade de tarefas.
metadata:
  openclaw:
    emoji: "🗂"
    requires:
      bins: [python3]
      env: [TRELLO_API_KEY, TRELLO_TOKEN]
---

# Trello Helper

Client REST para operar boards/listas/cards do Trello.

## Setup

- Credenciais: `TRELLO_API_KEY` e `TRELLO_TOKEN` no `.env` (configure via skill `mkt-setup`). Sem essas vars o script aborta.
- Primeiro uso: pedir link ou ID do board ao usuário.

## Comandos

```bash
# Boards
python3 scripts/trello_helper.py boards

# Listas de um board
python3 scripts/trello_helper.py lists <board_id>

# Cards de uma lista
python3 scripts/trello_helper.py cards <list_id>

# Detalhe de card
python3 scripts/trello_helper.py card <card_id>

# Comentários / anexos
python3 scripts/trello_helper.py comments <card_id>
python3 scripts/trello_helper.py attachments <card_id>

# Mover ou arquivar
python3 scripts/trello_helper.py move <card_id> <list_id>
python3 scripts/trello_helper.py archive <card_id>

# Adicionar comentário
python3 scripts/trello_helper.py add_comment <card_id> "texto"

# Buscar
python3 scripts/trello_helper.py search "query" [board_id]
```

## Notas

- Tratar Trello como fonte principal de tarefas: textos finais e status ficam no card.
- Imagens: subir no Drive e anexar o link ao card (não armazenar local).
- Não salvar credenciais ou board IDs em memória de sessão; persistir em config apropriada se for recorrente.
