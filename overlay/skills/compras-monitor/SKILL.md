---
name: compras-monitor
description: Monitora preços e itens salvos (carrinho/favoritos/wishlist) no Mercado Livre e Amazon BR, com sync via browser, histórico de preço e detecção de ofertas. Use para "sincronizar compras", "ver ofertas salvas" ou o resumo periódico de itens monitorados.
metadata:
  openclaw:
    emoji: "🛒"
    requires:
      bins: ["python3"]
---

# compras-monitor

Skill de monitoramento de preços e itens salvos no Mercado Livre e Amazon BR.

## Estrutura

- `scripts/sync.py` — CLI principal (sync, list, deals, history, summary, migrate-json)
- Dados do usuário em `/app/workspace/skills/compras-monitor/compras.db` (SQLite).
  O schema canônico é `produtos` (nome, marca/modelo, categoria, descrição, estado) 1:N
  `observacoes_loja` (loja/source/lista, preços, URL, promoção, screenshot/metadados e timestamps),
  com `historico_observacoes` por observação. A tabela antiga `items` é mantida apenas como
  compatibilidade e é espelhada durante a transição.
- A primeira abertura migra com backup SQLite `compras.db.pre-normalizacao-*.bak`.
- Sync usa **somente CDP** do browser OpenClaw; cria e fecha apenas a sua própria aba, sem encerrar
  tabs/contextos do usuário.

## Uso

```bash
# Sincronizar todos os itens (ML + Amazon)
python3 /app/skills/compras-monitor/scripts/sync.py sync

# Listar itens
python3 /app/skills/compras-monitor/scripts/sync.py list [--source ml|amazon] [--tag carrinho|favoritos|wishlist|salvo]

# Listar só os itens descontinuados (saem de list, deals, summary e do relatório das 08h)
python3 /app/skills/compras-monitor/scripts/sync.py list --discontinued

# Ver ofertas / itens com desconto
python3 /app/skills/compras-monitor/scripts/sync.py deals

# Histórico de preço de um item
python3 /app/skills/compras-monitor/scripts/sync.py history <item_id>

# Resumo geral
python3 /app/skills/compras-monitor/scripts/sync.py summary

# Importar o registro JSON legado da compras-avulsas (idempotente, pula o que já existe)
python3 /app/skills/compras-monitor/scripts/sync.py migrate-json [--path ...]
```

## Automação e recuperação de seletor

Roda diariamente via automation do OpenClaw. O `sync.py` é sempre determinístico e usa apenas CDP.
Quando uma fonte falhar por seletor, extração vazia ou cardinalidade anômala, ele falha fechada e preserva o último snapshot válido. O agente então usa o browser OpenClaw para inspecionar a página logada, remapear a linha/card real, corrigir o gabarito do extrator e validar uma rodada CDP completa antes de aceitar dados novos. Não usar LLM para navegar em toda execução normal.

Regra padrão: qualquer erro/lacuna de scrape que aparecer (falha dura, campo faltando tipo preço null em item que existe, seletor quebrado, duplicata) já dispara o Remax (`sessions_spawn agentId=coder`, visible) pra investigar com browser real + LLM e corrigir o script/gabarito de forma determinística — não corrigir "no achismo" nem deixar acumular. Sempre reportar causa raiz por categoria e o que ficou como exceção real (não é bug).

## Entrega no tópico Compras

Ao listar ou reportar itens, separar por `Fonte / Lista` (por exemplo, `Amazon / Carrinho`). Em
cada seção, usar **lista de bullets** (não tabela markdown — o link do Telegram não renderiza
clicável dentro de célula de tabela), um item por linha: `Produto — preço anterior → preço atual
(−N%) — URL`. Sem referência de preço anterior válida, mostrar `—` no lugar da redução.

**REGRA DURA, sem exceção: todo item reportado leva o link (`url`) na mesma linha.** `deals` sempre traz `url` por item — é campo obrigatório do JSON, não opcional de formatação. Reportar sem link é bug de entrega, não estilo; se o output rascunhado não tiver `url` em cada bullet, refazer antes de enviar. Motivo de recorrência conhecido: montar a mensagem de memória/resumo em vez de a partir do JSON de `deals` — sempre formatar direto do JSON (campo `url`), nunca de recall.

Quando uma queda for drástica, publicar também um card do produto com o screenshot persistido da observação, descrição curta, `era → agora`, percentual de redução e link. Considerar drástica uma queda real de pelo menos 20% ou R$ 200; não disparar card por badge promocional sem comparação de preço.

## Fontes

| Fonte           | URL                                                               | Tag       |
| --------------- | ----------------------------------------------------------------- | --------- |
| ML Carrinho     | `mercadolivre.com.br/gz/cart/v2`                                  | carrinho  |
| ML Favoritos    | `myaccount.mercadolivre.com.br/bookmarks/list`                    | favoritos |
| Amazon Carrinho | `amazon.com.br/gp/cart/view.html`                                 | carrinho  |
| Amazon Salvos   | `amazon.com.br/gp/cart/view.html` (seção "Salvo para mais tarde") | salvo     |
| Amazon Wishlist | `amazon.com.br/hz/wishlist/ls/default`                            | wishlist  |

## Detecção de ofertas (`deals`)

Imprime JSON no stdout: `{"sections": [{"lista", "items": [...]}], "has_changes": bool}`.

Por item: `produto`, `preco_anterior`, `preco_atual`, `percentual_reducao` (positivo = queda),
`menor_preco` (mínimo do histórico), `novo_minimo` (atual <= mínimo já visto), `promocao`, `url`.

- `preco_anterior` = último preço diferente observado (não mais o preço riscado/original).
- Só entra queda **>= 3%** (`MIN_DROP_PCT` em `sync.py`) e que ainda não foi reportada àquele preço
  (`observacoes_loja.ultimo_preco_reportado`).
- `deals --mark` grava o preço reportado (usar só no cron; sem `--mark` é consulta idempotente).
- Itens de UI/sem URL são descartados.
- Seções na ordem: ML / Carrinho, ML / Favoritos, Amazon / Carrinho, Amazon / Salvos,
  Amazon / Wishlist. Seção vazia não aparece. Itens `avulso/manual` ficam de fora — são da
  `compras-avulsas`.
