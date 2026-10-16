---
name: liturgia-diaria
description: Acessa a liturgia diária católica. Use quando o usuário pedir liturgia do dia, evangelho do dia, salmo responsorial, leituras da missa, palavra do dia ou comentário do papa. Fontes: Acervo Católico (leituras completas: 1ª Leitura, Salmo, 2ª Leitura, Evangelho) + Vatican News (comentário/palavras dos papas).
---

# Liturgia Diária

Acessa a liturgia católica diária combinando duas fontes:

- **Acervo Católico** → Leituras completas
- **Vatican News** → "Palavras dos Papas" (comentário)

## Script

O script `workspace-dev/skills/liturgia-diaria/scripts/liturgia.py` encapsula todo o acesso às duas fontes:

- **Acervo Católico** → Leituras completas
- **Vatican News** → "Palavras dos Papas"

Aceita data opcional no formato `YYYY-MM-DD` como argumento posicional. Sem argumento, usa a data atual.

## Procedimento

1. Se o usuário não especificar data, usar a data atual (horário UTC)
2. Executar o script Python via bash:
   ```bash
   python3 workspace-dev/skills/liturgia-diaria/scripts/liturgia.py [YYYY-MM-DD]
   ```
   - Omitir a data para usar o dia atual
   - O script retorna duas partes separadas por `---SEPARADOR_MENSAGEM---`
3. Dividir a saída no separador: a primeira parte são as leituras, a segunda são as palavras dos papas
4. Enviar cada parte como mensagem separada (ver seção Saída Esperada)

## Saída Esperada

**Mensagem 1 — Leituras:**

```
📖 LITURGIA DIÁRIA — DD/MM/AAAA
[DIA LITÚRGICO]

PRIMEIRA LEITURA — [referência bíblica]
[Texto completo da leitura]

SALMO RESPONSORIAL — [referência]
[Texto do salmo completo]

SEGUNDA LEITURA — [referência bíblica]
[Texto completo completo]

EVANGELHO — [referência bíblica]
[Texto completo completo]
```

**Mensagem 2 — Palavras dos Papas (separada):**

```
🗣️ PALAVRAS DOS PAPAS
[Comentário papal — Vatican News]
```

> ⚠️ **IMPORTANTE:** As palavras dos Papas DEVEM ser enviadas em uma **segunda mensagem separada**, NUNCA junto com as leituras. Use o recurso de mensagens do canal (ex: uma resposta ao usuário, ou duas chamadas de envio distintas).

> ⚠️ **Não incluir** rodapé de fonte nas mensagens.

## Formatação

- **Títulos das seções** (PRIMEIRA LEITURA, SALMO, etc.) em **negrito** com **bold markdown**
- **Referências bíblicas** incluídas junto ao título da seção
- **Frases importantes** no texto (versículos-chave, mensagens centrais, ensinamentos de Jesus) em **negrito** para destacar — usar bom senso, não exagerar
- Respeitar a estrutura original dos textos
- Salmo: o refrão (R.) pode ficar em itálico
- Palavras dos Papas: o autor e contexto em **negrito** no início

> ⚠️ **IMPORTANTE:** As palavras dos Papas DEVEM ser enviadas em uma **segunda mensagem separada**, NUNCA junto com as leituras. Use o recurso de mensagens do canal (ex: uma resposta ao usuário, ou duas chamadas de envio distintas).

## Notas

- Enviar **sempre o texto completo** de cada leitura — nunca resumir, truncar ou omitir versículos
- O script já filtra conteúdo irrelevante (propagandas, rodapés); não é necessário pós-processamento adicional
- Se o script retornar erro, informar o usuário com a mensagem retornada
