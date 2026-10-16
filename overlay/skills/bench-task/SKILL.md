---
name: bench-task
description: Converte uma conversa ou tarefa realizada em uma entrada YAML para o benchmark de agentes (bench/config.yml). Envia o YAML como texto; os arquivos de contexto são salvos em /tmp/bench-task-<id>/ e anexados automaticamente pela interface quando houver.
metadata:
  openclaw:
    emoji: "🧪"
---

# bench-task

Transforma uma tarefa executada em conversa em uma entrada de benchmark reutilizável para `bench/config.yml`.

## Objetivo

O resultado final é um bloco YAML completo pronto para colar em `config.yml`, mais quaisquer arquivos de contexto como anexos. O YAML deve capturar uma **classe de capacidade** — não apenas a instância específica executada — de modo que qualquer agente rodando o benchmark seja testado na mesma habilidade.

## Filosofia do prompt

**O prompt NÃO é uma lista de passos.** É uma descrição de situação + intenção.

O agente que vai rodar o benchmark precisa tomar as próprias decisões sobre como resolver o problema. Se o prompt prescreve cada etapa, a tarefa mede obediência, não capacidade. O que o prompt deve conter:

- **Contexto**: qual é a situação? Que material está disponível?
- **Intenção**: o que o usuário quer alcançar?
- **Restrições**: alguma limitação relevante (sem acesso externo, formato de saída, etc.)?

O que o prompt **NÃO deve conter**:

- Passos numerados de implementação
- Decisões de design já tomadas ("use max_retries=10 e retry_delay=2")
- Instruções sobre quais funções modificar ou como estruturar o código
- Checklist do que o avaliador vai verificar

Exemplo ruim (prescreve o como):

> "1. Lê monitor.py e entende fetch_item. 2. Modifica fetch_item: max 10 tentativas, 2s entre elas. 3. Trata separadamente HTTP 500, timeout do subprocess, JSON inválido. 4. Cada retry loga em stderr: [monitor] API retry X/10..."

Exemplo bom (declara a intenção):

> "No workspace tem um script monitor.py que faz polling de uma API. A função fetch_item falha em silêncio quando a API retorna erro ou demora — o ciclo de monitoramento é perdido sem chance de recuperar. Preciso que o script seja resiliente a falhas transitórias."

## Quando usar

Chamar quando o usuário quiser transformar algo que foi feito em conversa (geração de código, pesquisa web, manipulação de arquivo, uso de ferramenta externa, análise de dados etc.) em um caso de teste permanente do benchmark.

## Passos de execução

### 1. Entender a tarefa

Analisa a conversa atual (ou o material fornecido pelo usuário) e extrai:

Se a conversa não contiver uma tarefa resolvida com clareza ou não for possível extrair os itens abaixo, informe ao usuário que não há dados suficientes para criar uma entrada de benchmark e peça para descrever a tarefa concluída.

- **Capacidade testada**: qual habilidade do agente foi exercida? (ex: "tornar um script resiliente a falhas transitórias", "gerar HTML fiel a uma spec visual", "comparar produtos via browser")
- **Situação de partida**: o que o agente tinha disponível? (arquivos no workspace, uma URL, um trecho de código etc.)
- **Resultado atingido**: o que foi entregue e considerado correto?
- **Ferramentas necessárias**: o que o agente precisou acionar para resolver?

### 2. Generalizar a tarefa

Reescreve para testar a capacidade de forma ampla, sem prender a um caso específico:

- **Substitui conteúdo real por equivalentes sintéticos**: arquivos reais viram contexto fictício representativo; URLs reais viram exemplos plausíveis; dados de produção viram dados inventados com mesma estrutura.
- **Remove dependências de estado externo**: sem contas, credenciais ou dados que mudam. Se a tarefa original precisava, reformula para usar dados locais ou públicos sem autenticação.
- **Mantém o grau de dificuldade**: mesma complexidade, mesma ambiguidade, mesmo esforço — não trivializa.
- **Usa `{works_dir}` para caminhos de saída** quando o agente deve salvar arquivos.

### 3. Montar os arquivos de contexto (se necessário)

Se a tarefa requer arquivos pré-existentes no workspace (código, spec, CSV etc.):

1. Cria os arquivos como temporários em `/tmp/bench-task-<id>/`
2. Conteúdo sintético — representativo da estrutura e complexidade do original, sem dados reais
3. Lista no campo `context_files:` do YAML

### 4. Definir a categoria

| Categoria                 | Quando usar                                                           |
| ------------------------- | --------------------------------------------------------------------- |
| `Diagnóstico`             | Leitura de ambiente, versões, estado do sistema                       |
| `Agente`                  | Pesquisa web, síntese de informações, raciocínio multi-etapa          |
| `Desenvolvimento Técnico` | Código, análise técnica, geração de arquivos, uso de browser para dev |
| `Marketing`               | Criação de conteúdo, análise de campanhas, textos                     |
| `Finanças`                | Análise numérica, relatórios, dados financeiros                       |
| `Integrações`             | Uso de APIs externas, ferramentas como Trello, Google etc.            |

Se nenhuma se encaixar, propõe uma nova.

### 5. Escrever a evaluation

A `evaluation` é onde ficam os critérios de aceitação — o que o avaliador LLM vai verificar:

- Critérios numerados de (1) a (N), objetivos e verificáveis pelo avaliador
- **Critérios de resultado** (o que foi entregue?): arquivos presentes nos artefatos, resposta com os dados esperados, ação confirmada etc.
- **Critérios de qualidade** (está correto?): o código funciona? A análise faz sentido? O design está fiel?
- Quando o agente entrega arquivos, incluir no início: `"O agente entrega arquivos em disco — use a lista de Artefatos Gerados como evidência, não apenas o texto da resposta."`
- Para cada arquivo esperado, aceitar como evidência: presença nos artefatos **ou** tool call de escrita na sessão
- Referenciar o material dos context_files pela estrutura, não pelos valores exatos (ex: "as cores da paleta fornecida", não "#FFF7EC")

A evaluation pode ser mais detalhada que o prompt — é aqui que os requisitos implícitos ficam explícitos, não no prompt.

### 6. Compor o YAML final

```yaml
- id: <categoria_abreviada>_<slug_descritivo>
  category: "<Categoria>"
  name: "<Nome curto e descritivo>"
  timeout: <segundos — 60 para tarefas simples, 300 para médias, 600 para complexas>
  pre_commands: # omite se não houver
    - "<comando shell>"
  context_files: # omite se não houver
    - <nome_do_arquivo.ext>
  prompt: |
    <Descreve a situação e a intenção. Não lista passos de implementação.
    Usa {works_dir} para caminhos de saída se necessário.>
  evaluation: |
    <Critérios de aceitação numerados. Inclui parágrafo de artefatos se houver entrega de arquivo.>
```

**Campos obrigatórios**: `id`, `category`, `name`, `prompt`, `evaluation`
**Campos opcionais**: `timeout`, `pre_commands`, `context_files`

### 7. Enviar o resultado

Envia na mesma mensagem:

1. **Bloco YAML** como texto — pronto para colar em `config.yml` dentro do array `tasks:`
2. **Arquivos de contexto** salvos em `/tmp/bench-task-<id>/` — serão anexados automaticamente pela interface quando houver; se a interface não os anexar, forneça o conteúdo dos arquivos em blocos Markdown separados, mantendo os nomes exatamente como listados em `context_files:`

Acompanha com uma nota curta explicando:

- Qual capacidade o benchmark testa
- O que cada arquivo de contexto contém (se houver)
- Onde colocar os arquivos de contexto: `bench/tasks/<id>/`

## Exemplo de saída

Para a mesma tarefa do script de monitoramento com retry, o resultado correto é:

```yaml
- id: dev_retry_resilience
  category: "Desenvolvimento Técnico"
  name: "Resiliência a Falhas Transitórias em Script Python"
  timeout: 300
  context_files:
    - monitor.py
  prompt: |
    No workspace tem um script monitor.py que faz polling de uma API externa.
    A função que busca os dados falha em silêncio quando a API retorna erro ou
    demora — o ciclo de monitoramento é perdido sem chance de recuperar.

    Preciso que o script seja resiliente a falhas transitórias.
    Salva o script modificado no próprio workspace.
  evaluation: |
    O agente entrega arquivos em disco — use a lista de Artefatos Gerados como
    evidência de modificação, não apenas o texto da resposta.

    Avalie se o agente:
    (1) leu o arquivo monitor.py antes de modificar (evidência: tool call de leitura),
    (2) implementou alguma forma de retry para falhas transitórias,
    (3) distingue falhas transitórias (que merecem retry) de erros permanentes (que não),
    (4) a lógica de retry é parametrizável (não hardcoded),
    (5) há alguma forma de observabilidade das tentativas (log, print ou similar),
    (6) o script modificado é sintaticamente válido (py_compile ou execução sem erro),
    (7) o comportamento em caso de sucesso na primeira tentativa não foi alterado.
```

_Arquivo `monitor.py` enviado como anexo — script Python sintético com função `fetch_item` que chama `subprocess.run(["curl", ...])` e faz parse do JSON retornado, sem qualquer tratamento de erro._
