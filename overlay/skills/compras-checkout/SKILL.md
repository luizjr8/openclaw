---
name: compras-checkout
description: Automação de checkout em plataformas de pagamento (Kiwify, Hotmart, Eduzz, etc). Preenche formulários, gera código PIX e retorna para o usuário pagar.
---

# Skill: Compras Checkout

## Descrição

Automação de checkout em plataformas de pagamento (Kiwify, Hotmart, Eduzz, etc). Preenche formulários, gera código PIX e retorna para o usuário pagar.

## Como usar

O usuário envia um link de produto para comprar. A skill identifica a plataforma de checkout e executa o fluxo correspondente.

```
python3 scripts/checkout.py <url> [--platform kiwify] [--product "Nome do produto"]
```

`--product` é só cosmético (aparece no resumo). O workspace é resolvido por
`LZBOT_WORKSPACE_ROOT` (fallback: cwd) — nada de path hardcoded.

### Plataformas suportadas

| Plataforma | Handler               | Status       |
| ---------- | --------------------- | ------------ |
| Kiwify     | `checkouts/kiwify.py` | Implementado |

## Fluxo geral

1. Identificar plataforma pelo URL
2. Carregar dados do usuário (nome, email, CPF)
3. Navegar até o checkout via Playwright
4. Preencher formulário com dados do usuário
5. Selecionar forma de pagamento (PIX)
6. Submeter e capturar código PIX
7. Retornar código + resumo pro usuário
8. Salvar no `memory/compras.json`

## Dados do usuário

Carregar de `USER.md` (campos: Nome completo, Email, CPF)

## Registro de compras

Manter atualizado em `memory/compras.json`:

- produto, categoria, valor, data_insercao, tipo, nota_necessidade, url, analise
- comprado, data_compra, pedido, pagamento

## Adicionar nova plataforma

Criar arquivo em `checkouts/<plataforma>.py` seguindo o padrão:

- Função principal que recebe URL e dados do usuário
- Retorna dict com: pix_qrcode, order_id, amount, status
