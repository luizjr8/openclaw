---
name: financas
description: Gerencia finanças pessoais — receitas e despesas avulsas ou recorrentes, planejadas e executadas. Use para registrar transações, marcar pagamentos, gerar planejamento mensal a partir de recorrências, e obter resumo de planejado vs executado com saldo. Banco de dados SQLite local.
metadata:
  openclaw:
    emoji: "💰"
    requires:
      bins: ["python3"]
---

# Finanças Pessoais

Gestão de receitas e despesas com suporte a recorrências e controle de planejado vs executado.

## Caminhos

- Script: `scripts/financas.py`
- Banco: `financas.db`
- Categorias: `financas-categorias.md`
- Skill: `/app/workspace/skills/financas/`

## Uso

```bash
python3 scripts/financas.py <comando> [opções]
```

> ⚠️ Todos os comandos retornam JSON puro (não use `--json`, não existe mais). O LLM formata a saída amigável para o usuário.

## Comandos — Transações Avulsas

### add

Registrar uma transação. Por padrão é registrada como EXECUTADA (já aconteceu). Use `--planejada` para pendente.

```bash
python3 scripts/financas.py add <tipo> --desc <texto> --valor <num> --categoria <id> [opcoes]
```

**Argumentos obrigatórios:**

- `tipo`: `receita` | `despesa`
- `--desc`: descrição da transação
- `--valor`: valor (ex: 350.00)
- `--categoria`: ID da categoria (use `categorias` para ver a lista)

**Opções:**

- `--data YYYY-MM-DD`: data da transação (padrão: hoje)
- `--conta CONTA`: conta/banco (ex: HYPER, FRMNT, LZJR)
- `--fornecedor NOME`: fornecedor/contraparte
- `--atraso-maximo DIAS`: prazo máximo em dias para atraso
- `--planejada`: registrar como pendente (não executada)
- `--obs TEXTO`: observação livre
- `--dados-pagamento TEXTO`: "pix:CODIGO" ou "boleto:CODIGO_BARRAS" — armazena código para pagamento futuro
- `--calcular-imposto`: calcular e gerar lançamentos de PIS, COFINS, ISS, IRPJ, CSLL (Lucro Presumido)
- `--aliquota-iss PCT`: alíquota ISS em % (padrão: 5)

**Exemplos:**

```bash
python3 scripts/financas.py add despesa --desc "Mercado" --valor 350 --categoria alimentacao
python3 scripts/financas.py add despesa --desc "Servidor" --valor 850 --categoria servidores --conta HYPER --fornecedor "eConsulters"
python3 scripts/financas.py add receita --desc "Freelance" --valor 2000 --categoria freelance --data 2026-05-20 --planejada
python3 scripts/financas.py add receita --desc "SER Cloud" --valor 12000 --categoria servicos-cloud --conta FRMNT --planejada --calcular-imposto
python3 scripts/financas.py add despesa --desc "Financiamento" --valor 5600 --categoria casa --conta LZJR --planejada --atraso-maximo 60
python3 scripts/financas.py add despesa --desc "Fornecedor" --valor 350 --categoria servidores --planejada --dados-pagamento "pix:abcdef123456"
```

### executar

Confirmar transação planejada como executada.

```bash
python3 scripts/financas.py executar <id> [--valor VALOR] [--data YYYY-MM-DD] [--obs TEXTO]
```

- `--valor`: valor real executado (se diferente do previsto)
- `--data`: data de execução (padrão: hoje)

### cancelar

Cancelar transação.

```bash
python3 scripts/financas.py cancelar <id>
```

### atualizar

Atualizar campos específicos de uma transação existente.

```bash
python3 scripts/financas.py atualizar <id> [opcoes]
```

**Opções:**

- `--data YYYY-MM-DD`: nova data prevista
- `--dados-pagamento TEXTO`: "pix:CODIGO" ou "boleto:CODIGO_BARRAS"
- `--obs TEXTO`: nova observação
- `--desc TEXTO`: nova descrição
- `--valor NUM`: novo valor previsto
- `--categoria CAT`: nova categoria
- `--conta CONTA`: nova conta/banco
- `--fornecedor NOME`: novo fornecedor

**Exemplos:**

```bash
python3 scripts/financas.py atualizar 210 --dados-pagamento "boleto:34191790010104351004791020150008901234567000"
python3 scripts/financas.py atualizar 210 --obs "Vencimento alterado para dia 15" --data 2026-05-15
python3 scripts/financas.py atualizar 42 --valor 99.90 --categoria alimentacao
```

## Comandos — Recorrências

### recorrencia add

Cadastrar receita ou despesa recorrente.

```bash
python3 scripts/financas.py recorrencia add <tipo> --desc <texto> --valor <num> --categoria <id> [opcoes]
```

**Argumentos obrigatórios:** `tipo`, `--desc`, `--valor`, `--categoria`

**Opções:**

- `--frequencia`: `diaria` | `semanal` | `mensal` (padrão) | `anual`
- `--dia N`: dia de referência no mês (ex: 5 → todo dia 5)
- `--conta CONTA`: conta/banco
- `--fornecedor NOME`: fornecedor/contraparte
- `--atraso-maximo DIAS`: prazo máximo em dias para atraso

**Exemplos:**

```bash
python3 scripts/financas.py recorrencia add receita --desc "Salário" --valor 5000 --categoria salario --dia 5
python3 scripts/financas.py recorrencia add despesa --desc "Aluguel" --valor 2500 --categoria moradia --dia 10 --conta HYPER
python3 scripts/financas.py recorrencia add despesa --desc "Servidor Trant" --valor 850 --categoria servidores --conta HYPER --fornecedor "eConsulters" --dia 6
python3 scripts/financas.py recorrencia add despesa --desc "Financiamento" --valor 5600 --categoria casa --conta LZJR --dia 15 --atraso-maximo 60
```

### recorrencia listar

Listar todas as recorrências cadastradas.

```bash
python3 scripts/financas.py recorrencia listar
```

### recorrencia gerar

Gerar transações planejadas do mês a partir das recorrências ativas. Idempotente — não duplica.

```bash
python3 scripts/financas.py recorrencia gerar [--mes YYYY-MM]
```

- Se a recorrência for `lucro-presumido`, gera automaticamente os 5 lançamentos de imposto (PIS, COFINS, ISS, IRPJ, CSLL)
- Verifica duplicatas via `id_referencia`

### recorrencia desativar

Desativar recorrência (não exclui).

```bash
python3 scripts/financas.py recorrencia desativar <id>
```

### recorrencia atualizar

Atualizar recorrência e propagar mudanças para transações futuras (status `planejada`, meses seguintes ao atual).

```bash
python3 scripts/financas.py recorrencia atualizar <id> [--desc TEXTO] [--valor NUM] [--dia N] [--categoria CAT] [--conta CONTA] [--fornecedor NOME] [--atraso-maximo DIAS]
```

**Exemplos:**

```bash
python3 scripts/financas.py recorrencia atualizar 8 --valor 5500 --desc "Alisson Dividendos (atualizado)"
python3 scripts/financas.py recorrencia atualizar 8 --categoria dividendos --conta FRMNT
python3 scripts/financas.py recorrencia atualizar 17 --atraso-maximo 60
```

## Comandos — Consultas

### listar

Listar transações do mês.

```bash
python3 scripts/financas.py listar [--mes YYYY-MM] [--tipo receita|despesa] [--status planejada|executada|cancelada] [--conta CONTA]
```

### resumo

Resumo mensal: planejado vs executado.

```bash
python3 scripts/financas.py resumo [--mes YYYY-MM] [--conta CONTA]
```

### saldo

Saldo do mês (receitas - despesas executadas).

```bash
python3 scripts/financas.py saldo [--mes YYYY-MM]
```

### dre

DRE detalhado por categoria com lançamentos individuais. Gera automaticamente as recorrências do mês antes de calcular.

```bash
python3 scripts/financas.py dre [--mes YYYY-MM] [--conta CONTA] [--report]
```

- `--report`: saída formatada pronta para exibição (com alinhamento TW=30, descrição 17 chars, valor 12 chars)
- Sem `--report`: retorna JSON puro com categorias, lançamentos, totais

### categorias

Listar categorias disponíveis e caminho do arquivo.

```bash
python3 scripts/financas.py categorias
```

### atrasados

Listar transações vencidas com prazo máximo de pagamento.

```bash
python3 scripts/financas.py atrasados [--report]
```

- `--report`: saída formatada pronta para exibição (mesmo layout TW=30)
- Sem `--report`: JSON puro com dados de vencimento, dias atrasados, dias restantes

## Campos da Transação (JSON)

| Campo               | Tipo           | Descrição                                              |
| ------------------- | -------------- | ------------------------------------------------------ |
| `id`                | int            | ID único                                               |
| `tipo`              | string         | `receita` ou `despesa`                                 |
| `descricao`         | string         | Descrição                                              |
| `valor_previsto`    | float          | Valor planejado                                        |
| `valor_executado`   | float ou null  | Valor real executado                                   |
| `categoria`         | string         | ID da categoria                                        |
| `conta`             | string ou null | Conta/banco                                            |
| `fornecedor`        | string ou null | Fornecedor                                             |
| `data_prevista`     | string         | Data prevista (YYYY-MM-DD)                             |
| `data_executada`    | string ou null | Data executada                                         |
| `status`            | string         | `planejada`, `executada`, `cancelada`                  |
| `recorrencia_id`    | int ou null    | ID da recorrência que gerou                            |
| `atraso_maximo`     | int ou null    | Prazo máximo de atraso em dias                         |
| `id_referencia`     | int ou null    | ID da transação que gerou esta (ex: imposto → receita) |
| `regime_tributario` | string ou null | `lucro-presumido` ou null                              |
| `observacao`        | string ou null | Observação livre                                       |
| `dados_pagamento`   | string ou null | Código PIX (`pix:...`) ou boleto (`boleto:...`)        |

## Cálculo Automático de Impostos (Lucro Presumido)

Quando uma receita é marcada com `--calcular-imposto` (ou recorrência com `regime_tributario='lucro-presumido'`), o script gera automaticamente 5 despesas de imposto:

| Tributo | Alíquota            | Base de Cálculo                 |
| ------- | ------------------- | ------------------------------- |
| PIS     | 0,65%               | Receita bruta                   |
| COFINS  | 3,0%                | Receita bruta                   |
| ISS     | 5,0% (configurável) | Receita bruta                   |
| IRPJ    | 15%                 | 32% da receita (base presumida) |
| CSLL    | 9%                  | 32% da receita (base presumida) |

Os lançamentos de imposto são vinculados à receita via `id_referencia`. A geração é idempotente — verifica duplicatas antes de criar.

## Categorias

Arquivo: `financas-categorias.md` (configurável via `FINANCAS_CATEGORIAS`)

### Formato

```
- id: Nome Legível
```

Exemplo:

```
- impostos: Impostos e Taxas
- dividendos: Dividendos Pagos
```

O **id** (antes dos `:`) é usado nos comandos (`--categoria impostos`). O **nome legível** é exibido pelo LLM.

### Regras ao cadastrar

1. Antes de adicionar transações ou recorrências com `--categoria`, SEMPRE leia o arquivo de categorias.
2. Analise semanticamente se já existe um ID similar — mesmo que o usuário tenha falado diferente:
   - "imposto" ≈ `impostos`
   - "assinatura" ≈ `assinaturas`
   - "supermercado" ≈ `alimentacao`
   - "contador" ≈ `servicos-contabeis`
3. Se encontrar similar, sugira o ID correto ao usuário, não crie automaticamente.
4. Se não existir e não houver similar: pergunte ao usuário se deseja criar. Se sim, adicione no arquivo de categorias.

## Formato Padrão de Relatórios (`--report`)

Todos os comandos com `--report` usam o mesmo formato compartilhado:

| Parâmetro     | Valor                                                                |
| ------------- | -------------------------------------------------------------------- |
| **TW**        | 30 (largura total)                                                   |
| **DW**        | 17 caracteres para descrição (truncado se maior, padding à esquerda) |
| **VW**        | 12 caracteres para valor (alinhado à direita, formato `R$ X.XXX,XX`) |
| **Separador** | `━` × 30                                                             |

Funções compartilhadas no código:

- `report_line(descricao, valor)` — formata uma linha
- `report_separator()` — `━` × 30

### Comandos que suportam `--report`

- `dre` — DRE detalhado com hierarquia de categorias
- `atrasados` — transações vencidas com prazo máximo
- `resumo` — planejado vs executado
- `saldo` — saldo do mês
- `listar` — listagem de transações
- `recorrencia listar` — listagem de recorrências

### Exemplo DRE

```
DRE --- 2026-05
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RECEITAS          R$ 37.200,00

 Licença HyperPlay
 └ SER HyperPlay  R$ 24.000,00

 Serviços Cloud   R$ 13.200,00
 ├ BUni Cloud      R$ 1.200,00
 └ SER Cloud      R$ 12.000,00

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DESPESAS          R$ 29.832,89

 Casa              R$ 7.063,42
 ├ Condominio Tro    R$ 950,00
 ├ Energisa Energ    R$ 350,00
 ├ Financiamento   R$ 5.600,00
 └ Vivo Fibra        R$ 163,42

 Dividendos Pagos
 └ Alisson Divide  R$ 5.000,00

 ...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Total Receitas    R$ 37.200,00
Total Despesas    R$ 29.832,89
Resultado do Mes   R$ 7.367,11
Saldo Acumulado   R$ -12.113,78
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Exemplo Atrasados

```
Atrasados
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Despesa pendente   R$ 1.500,00
     Venceu 01/05 (5d) Pagar ate 31/05 (25d)
Despesa pendente   R$ 900,00
     Venceu 03/05 (3d) Pagar ate 02/06 (27d)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total atrasado     R$ 2.400,00
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Exemplo Resumo

```
Resumo - 2026-05
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Receitas planejad R$ 10.000,00
Receitas executad R$ 10.000,00

Despesas planejad  R$ 6.500,00
Despesas executad  R$ 6.200,00

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Saldo previsto     R$ 3.500,00
Saldo real         R$ 3.800,00
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Regras de formatação (LLM)

1. **Nomes legíveis**: SEMPRE usar o nome legível da categoria (campo `nome` do JSON, ou consultar `categorias`)
2. **Hierarquia (DRE)**: `├` para itens do meio, `└` para o último
3. **Subtotal**: exibir ao lado do nome da categoria, sempre que houver 2+ lançamentos, OU quando for categoria de agrupamento
4. **Conta**: quando uma categoria tiver lançamentos em contas diferentes, mostrar a conta ao lado
5. **Fornecedor**: exibir entre parênteses, sem ícone
6. **Sempre usar `--report`**: ao responder no Telegram, SEMPRE passar `--report` nos comandos que o suportam (atrasados, listar, resumo, saldo, dre, recorrencia listar). A saída `--report` já vem formatada no padrão TW=30.
7. **Envio no Telegram**: cada linha do `--report` embrulhada com crase simples (\`) para renderização monoespaçada inline — NUNCA usar bloco de código (\`\`\`)
8. **Separador**: `━` × 30

## Fluxo Típico

1. **Configurar recorrências** (`recorrencia add`) — salário, aluguel, assinaturas, etc.
2. **Consultar DRE** (`dre --report`) — já gera recorrências automaticamente
3. **Ao longo do mês** — `add` para transações avulsas; `executar <id>` ao confirmar pagamentos
4. **Consultar atrasados** (`atrasados --report`) — ver contas vencidas com prazo máximo
5. **Consultar situação** — `resumo` para visão geral; `listar --status planejada` para ver o que falta

## Saldo acumulado vs mensal

- "Saldo **em** junho" / "saldo **em** agosto" → **acumulado** desde o início do ano até o mês pedido.
- "Saldo **de** junho" / "saldo **só de** junho" → **apenas aquele mês**, sem considerar meses anteriores.

Para calcular o acumulado, some os saldos de todos os meses do ano até o mês pedido.

## Banco de Dados

SQLite local em `financas.db`.

### Tabelas

| Tabela         | Descrição                                               |
| -------------- | ------------------------------------------------------- |
| `transacoes`   | Todas as transações (avulsas + geradas por recorrência) |
| `recorrencias` | Templates de receitas/despesas recorrentes              |

### Status

| Status      | Significado                       |
| ----------- | --------------------------------- |
| `planejada` | Prevista, ainda não ocorreu       |
| `executada` | Confirmada, com data e valor real |
| `cancelada` | Descartada                        |

## Contas Disponíveis

- `HYPER` — Empresarial HyperPlay
- `FRMNT` — Empresarial (SER Cloud)
- `LZJR` — Pessoal (Luiz Junior)
