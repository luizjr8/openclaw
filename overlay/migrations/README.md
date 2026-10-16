# Migrations do lzbot

## Onde isso vive: `/lzbot` (imutável, fora do volume)

A imagem copia o **motor interno** do lzbot pra `/lzbot` (`Dockerfile`),
**fora** do volume persistente `/app`: `entrypoint.sh`, `VERSION`,
`migrations/`, `scripts/`, e as skills/extensions **genéricas** (não dado de
usuário). Isso garante que toda instância — nova ou rodando há meses —
sempre tem essas peças **exatas** da versão de imagem rodando, sem depender
de checkout git nem sofrer shadow de volume.

`/lzbot` nunca é lido em runtime pelo OpenClaw diretamente — o agente sempre
roda com `OPENCLAW_STATE_DIR=/app`. `openclaw.json`, `agents.json5` e
`workspace/` são semeados **direto em `/app`** (mutam por instância, não faz
sentido como symlink pro `/lzbot`). Já `skills/` e `extensions/` — genéricos,
sem dado de usuário — entram em `/app` como **symlink** pro `/lzbot`
(`ln -sfn /lzbot/skills /app/skills`), não cópia: um update de imagem
atualiza os dois de graça, sem duplicar conteúdo.

- `ENTRYPOINT` do Dockerfile aponta pra `/lzbot/entrypoint.sh` (não mais
  `/app/entrypoint.sh`) — isso corrige, de raiz, o bug que motivou toda essa
  investigação: antes, `/app/entrypoint.sh` era copiado só na criação do
  volume e nunca mais atualizado por rebuilds de imagem seguintes.
- `scripts/lzbot-migrate.sh` resolve o próprio `LZBOT_ROOT` a partir do
  caminho de onde foi chamado (`$(dirname "$0")/..`) — funciona igual rodando
  de `/lzbot` (produção) ou de `/app/dev/lzbot` (esta instância criadora, que
  mantém o checkout git pra edição/commit).
- Customização por instância entra em `/app/entrypoint.instance.sh`
  (persistente, opcional) — o `/lzbot/entrypoint.sh` executa isso por último,
  se existir, senão cai no `exec "$@"` padrão.
- `/app/openclaw.json`, `agents.json5`, `workspace/`, `extensions/` continuam
  sendo semeados em `/app` pelo `Dockerfile` — mas isso só tem efeito real num
  volume **novo/vazio**; num volume já populado, o mount sombreia esses
  arquivos (por isso reconciliar instâncias existentes é trabalho das
  migrations, não desse `COPY`).

Este diretório contém as migrations versionadas que reconciliam instâncias
**já rodando** (containers antigos, com volume `/app` persistente) quando a
imagem do lzbot é atualizada. Ver contexto completo na conversa do tópico
Hackermode (MaruZone, 24/jul/2026) — resumo:

- A imagem carrega `dev/lzbot/VERSION` (inteiro, incrementa 1 por migration).
- O volume persistente guarda `/app/.lzbot-version` (última migration aplicada).
- No boot, `scripts/lzbot-migrate.sh` roda toda migration com número entre
  `.lzbot-version` (exclusive) e `VERSION` (inclusive), em ordem, atualizando
  o state a cada uma. Se uma falhar, o boot **não continua** (fail-closed).

> **Migrations já shipadas são histórico, não documentação do estado atual.** Elas
> continuam no disco porque a cadeia precisa ser replicável do 0 numa instância
> adotada — por isso o header de cada uma diz "nunca editar depois que shippar".
> Exemplo: a `0002` escreve um bloco de memória que a `0006` depois remove, e a
> `0008` limpa o resto. Ler uma delas isolada dá uma foto de uma versão antiga;
> o estado canônico vive em `openclaw.json` / `agents.json5`.

## Quando escrever uma migration (a resposta pro "commit vs versão")

**Não é por commit.** A maioria dos commits no `dev/lzbot` (docs, skill nova,
ajuste de prompt, refactor) não muda a _forma_ do estado persistido de uma
instância já rodando — não precisa de migration.

Escreva uma migration **só** quando a mudança exige reconciliar algo que já
existe no volume `/app` de instâncias antigas:

- chave nova obrigatória (ou com default diferente) no `openclaw.json`
- chave depreciada/removida que precisa sair
- env var nova que algum script/skill passou a exigir
- convenção de layout de arquivo/diretório mudou (ex: split `/app/skills`)
- rotina de boot nova que só existiria via `entrypoint.sh` velho (embora o
  padrão de entrypoint canônico + `entrypoint.instance.sh` já elimine boa
  parte desse caso — ver `scripts/lzbot-migrate.sh` e o entrypoint)

**Fluxo prático:**

1. No mesmo commit/PR que introduz a mudança, rode:
   ```bash
   scripts/new-migration.sh "descricao_curta"
   ```
   Isso cria `migrations/NNNN_descricao_curta.sh` já numerado e **bumpa o
   `VERSION` automaticamente** para `NNNN`. É assim que o versionamento avança
   — não existe bump manual de versão "por lançamento"; a versão _é_ o número
   da última migration.
2. Implemente a migration: **idempotente**, só **adiciona** o que falta ou
   remove uma chave que a migration reconhece explicitamente (nunca "limpa o
   que não reconheço"). Faça backup de qualquer arquivo tocado em
   `$LZBOT_MIGRATION_BACKUP_DIR` antes de escrever.
3. Nunca edite uma migration já mergeada/já em produção em alguma instância.
   Errou? Escreva uma **migration nova** que corrige a anterior.
4. Teste localmente: `scripts/lzbot-migrate.sh` com um `/app/.lzbot-version`
   fake apontando pra uma versão anterior, confirme que aplica certo e que
   rodar de novo é no-op (idempotência).

## ⛔ Critério de aceite obrigatório: `openclaw config validate`

**Toda migration que escreve em `openclaw.json` TEM que terminar rodando
`openclaw config validate` sobre o arquivo que ela acabou de editar, e falhar
(exit != 0) se o resultado não for `Config valid`.**

```bash
if command -v openclaw >/dev/null 2>&1; then
  if ! out="$(OPENCLAW_CONFIG_PATH="$OPENCLAW_JSON" openclaw config validate 2>&1)"; then
    echo "$out" >&2
    exit 1
  fi
fi
```

`OPENCLAW_CONFIG_PATH` não é opcional: sem ele o validador roda contra o config
ativo default e o gate vira placebo justamente nos testes, onde `$OPENCLAW_JSON`
aponta pra outro lugar.

**Por que isso é regra e não sugestão.** A `0005` foi escrita, revisada e passou
em teste de idempotência consertando três chaves aposentadas do bloco de memória
e `agents.list`. Ela rodava limpa, saía 0, e mesmo assim deixava o gateway sem
subir: um config vindo da 2026.7.1 tinha **outras 13** chaves aposentadas, e num
schema `additionalProperties:false` cada uma é fatal. Nenhuma leitura de código
achou isso — **um único `config validate` listou as 13 de uma vez**.

A lição generaliza: o exit 0 de uma migration só prova que o script não quebrou,
não que o produto aceita o resultado. Quem sabe se o config é válido é o
validador do produto. Chame ele.

Corolário, aprendido no mesmo episódio: **cuidado com "conserto" que só migra
parte de um par acoplado.** A `0005` renomeava `agents.list` -> `agents.entries`
sem converter o `agents.json5` de array para objeto. Só que o loader _tolera_ o
array enquanto a chave é `list` e passa a _exigir_ record quando ela vira
`entries` — o rename isolado transformava um problema tolerado num gateway
morto. Se A e B só são válidos juntos, migre A e B na mesma migration, ou não
migre nenhum.

## Casos que exigem julgamento (LLM), não script

Nem tudo que parece "migration" deve virar script mecânico. Regra prática:
se a decisão depende de **quais agentes existem** naquela instância, **quais
arquivos de memória fazem sentido compartilhar**, ou qualquer coisa que
varie com o roster/propósito da instância — isso é julgamento por instância,
não mecânico. Deixe pro agente residente decidir (ver design original:
"usar a própria LLM pra mediar a migração").

**Exemplo real — padrão "coder com query via builtin"**: neste container,
o agente `coder` enxerga a memória do agente `default` por `extraPaths`, em
`agents.json5`:

```json5
{
  id: "coder",
  memorySearch: {
    extraPaths: ["/app/workspace/MEMORY.md", "/app/workspace/memory/auth-profiles.md"],
  },
}
```

Ou seja: indexação `builtin` (via `extraPaths`, não daemon) de arquivos
pontuais do agente default (infra/credenciais gerais), sem puxar a pasta
`memory/` inteira (evita trazer `dreaming/` junto). Isso **não virou
migration mecânica** de propósito — uma instância como o sentinel tem um
roster de agentes completamente diferente (`Sentinela`, `SubNeo`,
`mkt-*`...), então "qual agente deveria enxergar qual arquivo do quê" é
decisão caso a caso. Migration 0002 só garante o bloco `memory.*` **global**;
o padrão `extraPaths` por agente fica documentado
aqui como referência pro agente que for aplicar a migração naquela instância
específica, com contexto — não como script cego.

> **Atualização (0008):** o parêntese acima — "evita trazer `dreaming/` junto" —
> descrevia um contorno por `extraPaths` que só protegia quem apontasse arquivo a
> arquivo. Quem indexa a própria `memory/` (todo agente, por padrão) continuava
> engolindo o dreaming: 363 dos 447 `.md` do workspace do main, 81% do índice.
> Isso **virou** migration mecânica na `0008`, e pôde virar porque o mecanismo não
> depende do roster: mover `memory/dreaming` para `<ws>/dreaming` e deixar um
> symlink no lugar vale igual em qualquer instância, e o workspace de cada agente
> é resolvido pelo próprio produto (`resolveAgentWorkspaceDir`), não por convenção
> nossa. O que continua sendo julgamento por instância é só a parte de cima: qual
> agente enxerga o arquivo de qual outro.

## Caso de adoção (instância mais antiga que o próprio sistema de migrations)

Se `/app/.lzbot-version` não existir, a instância é mais velha que este
mecanismo (ex.: o sentinel, criado antes de existir esse versionamento) — ou
é simplesmente nova. Nesse caso o `lzbot-migrate.sh` **semeia a versão em 0 e
replaya todas as migrations idempotentes** até a versão da imagem, logando
`replayedFromZero: true` no `.lzbot-migrations-applied.json`.

Isso é seguro **porque toda migration é idempotente por contrato** (só ADICIONA
o que falta, nunca sobrescreve valor existente — ver seção acima). Uma instância
pré-existente só tem as lacunas preenchidas; nada do estado atual é destruído.

> ⚠️ **Histórico:** até 2026-07-26, o adoption path **stampava** a versão da
> imagem e saía **sem replay** ("deixar pro humano revisar"). Isso deixou o
> sentinel com `adopted_at_version: 3, applied: []` e, portanto, **sem** o
> bloco de memória que a 0002 configura — `content_vectors: 0`. A correção (replay
> idempotente a partir do 0) elimina essa classe de bug. Se algum dia uma
> migration **não** puder ser idempotente, ela não deve depender deste path:
> trate o não-idempotente explicitamente dentro da própria migration.
