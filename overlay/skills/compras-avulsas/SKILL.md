---
name: compras-avulsas
description: Curadoria e monitoramento de preço de produtos avulsos (gadgets, acessórios, HQs, eletrônicos). Registra itens, monitora preço por palavra-chave em Amazon/Mercado Livre/Google Shopping e demais lojas, e compila resumo consolidado das oportunidades pendentes. Use para "monitorar preço de X", "adicionar X na lista de compras" ou o resumo periódico de curadoria.
metadata:
  openclaw:
    emoji: "🛒"
    requires:
      bins: ["python3"]
---

# Compras Avulsas

Monitor genérico de preço para **qualquer produto avulso**. Sem scraper dedicado por site: a busca é
feita por **palavra-chave** com as tools do agente (`web_search` / `web_fetch` / `browser`), o que
permite monitorar um relógio, um gadget ou uma HQ sem escrever código novo pra cada loja.

**Fora do escopo:** CDs/discos de segunda mão (skill `olx-monitor`) e jogos (`nintendo-wishlist-monitor`,
`nintendo-newreleases-economy-monitor`). Esses têm scraper/fluxo dedicado e continuam separados.

## Dados de instância vivem no workspace, NÃO nesta skill

O registro fica no SQLite unificado `/app/workspace/skills/compras-monitor/compras.db`
(sobrescrevível via env `COMPRAS_DB`), compartilhado com a skill `compras-monitor`. Itens desta
skill vivem com `source='avulso'` e `tag='manual'`; o crawler de ML/Amazon usa outros `source`/`tag`
no mesmo banco. Nada específico da instância — item, preço-alvo, chat de destino — entra nesta pasta.

O registro JSON antigo (`/app/workspace/memory/compras.json`) foi migrado e virou `.bak`.

## Uso

```bash
python3 scripts/compras.py <add|check|comprar|descartar|resumo> [opções]
```

Todos os comandos imprimem **JSON puro** no stdout. O agente formata a saída pro usuário.

### add

Registra um item novo.

```bash
python3 scripts/compras.py add --produto "..." --categoria "..." --valor "..." --nota 4 [opções]
```

| Opção                                          | Descrição                                                                                       |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `--produto`                                    | nome/descrição (obrigatório, precisa ser único no registro)                                     |
| `--categoria`                                  | ex: `Acessórios / Óculos de Sol` (obrigatório)                                                  |
| `--valor`                                      | preço como texto livre, ex: `"R$ 1.220,00 (tabela)"` (obrigatório)                              |
| `--nota`                                       | `nota_necessidade` 1 (supérfluo) a 5 (necessário) (obrigatório)                                 |
| `--tipo`                                       | `fisico` (default) ou `digital`                                                                 |
| `--url`, `--analise`, `--observacao`, `--data` | complementares                                                                                  |
| `--monitorar`                                  | entra no `check` diário; sem isso é registro pontual                                            |
| `--palavra-chave`                              | **repetível** — termos de busca livres                                                          |
| `--site`                                       | **repetível** — default: Amazon, Mercado Livre, Google Shopping, Kabum, Magalu, Estante Virtual |
| `--preco-min` / `--preco-max`                  | faixa de preço alvo                                                                             |
| `--alerta-abaixo-de`                           | preço que **quebra o silêncio** do check diário                                                 |
| `--preco-baseline`                             | preço de referência para comparar quedas                                                        |

```bash
python3 scripts/compras.py add \
  --produto "Ray-Ban RB4428 Havana Polarizado 894/57 56-21" \
  --categoria "Acessórios / Óculos de Sol" --valor "R$ 1.220,00" --nota 4 \
  --monitorar --palavra-chave "Ray-Ban RB4428 polarizado havana" \
  --preco-max 950 --alerta-abaixo-de 800 --preco-baseline 1220
```

### check

Roda em duas etapas, porque quem busca é o agente, não o script.

**1. Pegar o plano de busca** — lista os itens com `monitorar=true` que ainda estão pendentes:

```bash
python3 scripts/compras.py check
```

Retorna, por item: `id`, `palavras_chave`, `sites`, `preco_alvo`, `alerta_imediato_abaixo_de`,
`preco_baseline` e a `ultima_checagem`.

**2. Buscar e gravar** — o agente pesquisa cada item nas lojas indicadas, monta o JSON de achados e
grava:

```bash
echo '[{"produto": "RB4428 Havana", "preco": 780.0, "loja": "Ótica Carol", "url": "https://...",
        "analise": "Menor preço desde jul/2026..."}]' \
  | python3 scripts/compras.py check --resultados -
```

Campos aceitos por resultado: `produto` **ou** `id`, `preco` (float), `loja`, `url`, `analise`,
`valor` (texto livre; se ausente é derivado de `preco`).

⚠️ `produto` é substring do título e precisa casar com **exatamente um** item; `id` é a chave
estável da tabela `items`.

⚠️ Ao buscar: só considerar **loja/vendedor com reputação** — nada de réplica, vendedor sem avaliação
ou anúncio com preço fora da curva sem explicação. Preço absurdamente baixo quase sempre é réplica,
item danificado ou golpe.

A saída traz `atualizados` e `alertas`. **`alertas` só vem preenchido quando o preço achado ficou abaixo
do `alerta_imediato_abaixo_de` do item** — é isso, e só isso, que autoriza mandar mensagem na hora.
Lista vazia = silêncio; o achado entra no resumo consolidado normal.

### comprar / descartar

Marcam o item como resolvido e param o monitoramento (`monitorar=false`).

```bash
python3 scripts/compras.py comprar --produto "..." [--data AAAA-MM-DD] [--pedido "..."] [--pagamento "..."] [--valor "..."] [--observacao "..."]
python3 scripts/compras.py descartar --produto "..." [--observacao "..."]
```

`--produto` é substring, igual ao `check --resultados` — precisa casar com exatamente um item.
`comprar` seta `comprado=true`, `data_compra` (default hoje), e opcionalmente `pedido`/`pagamento`/
`valor` final pago. `descartar` seta `descartado=true`. Os dois somem do `resumo` e do plano do
`check` a partir daí (ver `pendente()`).

### resumo

Compila os itens pendentes (`comprado != true` e `descartado != true`) para a curadoria consolidada.

```bash
python3 scripts/compras.py resumo [--monitorados]
```

Ordena por `nota_necessidade` desc, depois categoria. Ao montar o texto, **variar categoria** — não
empilhar só HQ ou só eletrônico — e priorizar raridade/oportunidade (ver `memory/compras-topic.md`).

## Schema do item (tabela `items`)

Colunas próprias: `title` (= `produto`), `categoria`, `current_price`, `data_insercao`, `tipo`,
`nota_necessidade`, `url`, `analise`, `observacao`, `preco_baseline`, `comprado`, `descartado`,
`data_compra`, `pedido`, `pagamento`, `active`.

Colunas de monitoramento (todas opcionais):

| Coluna                    | Tipo       | Descrição                                               |
| ------------------------- | ---------- | ------------------------------------------------------- |
| `monitorar`               | 0/1        | entra no `check` diário                                 |
| `palavras_chave`          | JSON array | termos de busca livres — não precisa marca/modelo exato |
| `sites`                   | JSON array | lojas a consultar                                       |
| `preco_min` / `preco_max` | REAL       | faixa desejada (`preco_max` é o preço-alvo)             |
| `alerta_abaixo_de`        | REAL       | gatilho de alerta na hora                               |

A coluna `meta` (JSON livre) guarda o que não tem coluna própria: `valor_texto` (o `--valor` como
texto original), `ultima_checagem` (`{data, preco, loja, url}`), `avaliacao`, `raridade` e os
metadados de categoria. Compatibilidade: `meta.preco_alvo` (formato antigo) continua valendo como
preço-alvo quando `preco_max` é nulo.

O histórico de preço vai pra tabela `price_history` a cada `check --resultados`.

## Cron

- **`compras-monitor-diario`** — roda `check` todo dia. **Silencioso por padrão**: só manda mensagem
  se `alertas` voltar não-vazio.
- **`compras-curadoria-1525`** — roda `resumo` nos dias 15 e 25 e entrega a curadoria consolidada.

Monitor novo de categoria segue o mesmo padrão: alimenta o `compras.json` em silêncio, entrega só
no cron de curadoria.
