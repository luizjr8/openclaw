# AGENTS.md — Notas para agentes de codificação

Esse aqui é um projeto de agente (openclaw) generalista que vamos usar como imagem para nossos bots me containers.
A ideia é deixá-lo SOTA em configuração de agilidade, rapidez.

## Filosofia: boilerplate/template de agente

Este projeto é o **boilerplate/template** a partir do qual cada agente/bot é derivado. Ao configurá-lo, mire:

- **Completo e out-of-box**: deve funcionar bem sem ajustes obrigatórios — dependências, modelos, memória e integrações já vêm prontos para uso imediato.
- **Genérico**: nada específico de um bot concreto. Configuração deve servir de base para qualquer agente; a especialização acontece depois, por agente.
- **SOTA nos defaults**: escolha os melhores defaults sensatos (ex.: modelos, `searchMode`, embeddings multilíngues), mas sempre sobrescrevíveis por agente.

Regra prática ao decidir: se algo vale para _todo_ agente, entra aqui (template); se é de um bot específico, fica na especialização daquele agente.

## Direção: distro própria do OpenClaw, não customização dele

**O alvo não é "OpenClaw com os nossos dados" — é uma distribuição nossa do OpenClaw.**
Nossas skills, extensões e prompts shippam **nativos**, não enxertados por symlink ou
config de runtime. Quem builda a imagem recebe o produto já na nossa forma.

Do upstream queremos sobretudo as **libs**: os providers (Telegram, OpenAI, Claude), o
runtime do gateway, o protocolo. É por isso que continuamos atrelados a eles e que a
divergência tem que ser disciplinada — a última coisa que queremos é reimplementar
integração de canal.

O que sai, aos poucos: o excesso de superfície de controle e compatibilidade voltado pro
usuário simples. O OpenClaw é generalista por obrigação; nós temos um caso de uso
específico e conhecido. Material genérico demais, script frouxo, opção que existe só pra
cobrir cenário que não é o nosso — tudo isso é candidato a corte, em favor de algo mais
profissional e robusto.

### Consequência técnica: integração é por MERGE, não por rebase

Distro deleta arquivo e muda estrutura — e deleção sob `rebase` é o pior caso que existe:
cada release que encostar num arquivo removido reabre o conflito, porque o rebase
reescreve os commits e não guarda memória da remoção.

- Integrar release novo: `git merge v<versão>` na nossa branch. A deleção fica registrada
  **uma vez** no histórico de merge e o git não volta a perguntar.
- `git config rerere.enabled true` — memoriza resolução de conflito recorrente.
- `rebase --onto` só serve pro modelo de patch queue (poucos commits, nada deletado).
  **Não é o nosso modelo.**

### Triagem antes de divergir: config > patch > deleção

A maior parte do que queremos cortar não exige tocar no fonte. Nessa ordem, sempre:

1. **Config.** Skill indesejada, plugin exagerado, canal que não usamos ⇒ desabilitar em
   `openclaw.json`. Custo de divergência: **zero**.
2. **Patch.** Comportamento que a config não alcança — prompt, default hard-coded, bug ⇒
   commit no fork, com motivo e condição de aposentadoria registrados na mensagem.
3. **Deleção.** Só quando carregar o arquivo custar mais que resolver merge nele a cada
   release. **É a opção mais cara, não a mais limpa.**

Cada patch de fonte carrega trailers na mensagem de commit — `Patch-Reason:` e
`Drop-When:` — pra que a retirada seja uma decisão verificável, e não arqueologia.

## Uma sessão por vez: o working tree deste repo é serializado

**O boilerplate é editado por UMA sessão de cada vez, serializada pelo main.** Não
existe edição paralela do working tree — nem duas sessões `claude -p`, nem
subagente escrevendo enquanto o pai escreve, nem "só um arquivinho enquanto o
outro roda".

Se precisar de fan-out de verdade, cada agente ganha seu **`git worktree`** e o
merge é **explícito**, revisado por quem coordena.

Racional (04–05/set/2026): duas sessões `claude -p` rodaram em paralelo neste
checkout e a segunda **sobrescreveu a migration `0005`** que a primeira tinha
acabado de escrever. Não houve conflito de merge para avisar — é o mesmo working
tree, o último `write` simplesmente ganha, e o prejuízo só aparece quando alguém
relê o arquivo. Migration perdida é especialmente cara aqui: ela é contratualmente
imutável depois de shippar, então "reescrever" não é o mesmo arquivo de volta.

## Estrutura do projeto

- `openclaw.json` — configuração **fonte de verdade**. Edite sempre aqui.
- `app/` — diretório **efêmero** montado como volume Docker (`./app:/app`). O conteúdo é gerado/sincronizado a partir da raiz; **nunca edite arquivos dentro de `app/` diretamente**, pois as alterações podem ser sobrescritas.
- `bench/` — sistema de benchmark (ver `bench/benchmark.py` e `bench/config.yml`).
- `skills/` — skills do agente openclaw.
- Dockerfile montamos o ambiente ideal para nosso bot já nascer com as dependências todas.

## Dev vs Produção

- `run_local.sh`, `run_dev.sh`, `.env` e `docker-compose.yml` na raiz servem **apenas para o ambiente de desenvolvimento** (mount de volume, envs locais, credenciais de teste).
- Em **produção vale exclusivamente a imagem gerada pelo Dockerfile**. Qualquer coisa que precise valer em prd (deps, ENV padrão, modelos, config) tem que estar no `Dockerfile` — não confie em `.env`/compose/scripts de dev.
- Ex.: o que `run_local.sh`/`docker-compose.yml` exportam (paths de modelo, envs de teste) só afeta o dev; o que precisa valer em produção é `ENV`/`ARG` no Dockerfile.

## Volume `./app:/app` e o entrypoint

- O mount `./app:/app` (dev) **sombreia tudo que a imagem tem em `/app/*`**. Então o `COPY` do Dockerfile para dentro de `/app` funciona só como **seed**; em dev vale o que os scripts (`run_dev.sh`/`run_local.sh`) copiam para `./app`. Ao adicionar arquivo baked em `/app`, lembre de replicá-lo nesses scripts.
- O `entrypoint.sh` mora em `/app` **de propósito**: é estado **persistente e editável pelo agente** — cada agente acrescenta os próprios `ln -s`/init em runtime e isso sobrevive a reboots. Não mova para `/usr/local/bin` (viraria imutável e reverteria a cada recriação). Como o volume sombreia `/app`, o `run_dev.sh` copia o `entrypoint.sh` para `app/` (o `run_local.sh` não usa Docker/entrypoint).

## Plugins de canal: instale no Dockerfile, nunca em runtime

- `/lzbot/skills` e `/lzbot/extensions` são **assados na imagem**, e `/app/skills` e `/app/extensions` são só symlinks pra lá — que o `entrypoint.sh` **reforça em todo boot** (`force_image_symlink`), por design: skills/extensions são stateless à imagem. Consequência: `openclaw plugins install` com o container de pé grava na camada gravável do container e **some na próxima recriação**.
- Quando é um plugin de **canal**, a perda é silenciosa: o gateway sobe normal, só sem o plugin, e loga `channels.X is configured but no channel plugin is installed or loadable (no-channel-owner)`. Ninguém é avisado — o bot só fica mudo naquele canal.
- Copiar o plugin pro volume e apontar em `plugins.load.paths` **não resolve**: fora do diretório oficial ele é tratado como _untrusted_ e o canal entra em loop de restart com `openKeyedStore is only available for trusted plugins`.
- Portanto: **todo plugin necessário vira `RUN` no Dockerfile** (ver o bloco do `@openclaw/whatsapp`, que usa `OPENCLAW_STATE_DIR=/lzbot` para instalar direto no baked). Verificado em 27/ago/2026, depois de um lzbot perder o canal WhatsApp num restart.

## Dependências Python das skills

- **Nada de venv por skill.** Base é Ubuntu 24.04 (Noble), que aplica PEP 668 (`externally-managed-environment`) — o Dockerfile já roda `pip3 config set global.break-system-packages true`, então `pip3 install --break-system-packages <lib>` no Python global do sistema já é o padrão. Toda skill deve assumir que roda com `python3` puro do PATH.
- Editar o `Dockerfile` é a forma correta de fixar uma dependência — não instalar ad-hoc em runtime e não criar um venv isolado só pra contornar. Ver blocos "PACOTES PYTHON PIP LEVES" (wheels triviais) e "WHISPER + ENGINE" (libs pesadas, versões pinadas) no Dockerfile.
- Um `.venv` isolado (ex: `/app/workspace/.venv`, específico de uma instância) já quebrou skills duas vezes: qualquer sessão/agente que chamasse `python3` genérico (sem saber do path do venv) caía em `ModuleNotFoundError`. Preferir sempre lib instalada globalmente na imagem.
- Único caso legítimo pra isolar em venv: conflito real de versão entre duas skills (ex: uma precisa de `numpy` 1.x e outra de 2.x) — aí sim, venv _por skill_, nunca um único venv compartilhado.

## Memória (builtin, embedding local)

- **A busca é builtin e in-process** desde o OpenClaw 2026.9.1: sem backend externo, sem
  daemon, com índice por agente dentro do state dir.
- Config canônica vive em `memory.search` do `openclaw.json` (comentada lá em detalhe):

  | chave                         | valor        | por quê                                                                |
  | ----------------------------- | ------------ | ---------------------------------------------------------------------- |
  | `provider`                    | `"local"`    | embedding no llama-server residente (embeddinggemma-300m QAT)          |
  | `store.vector.enabled`        | `false`      | scan in-process (296 ms) em vez de sqlite-vec em subprocesso (2902 ms) |
  | `sources`                     | `["memory"]` | busca default só na memória curada                                     |
  | `rememberAcrossConversations` | `true`       | recall protegido das outras conversas do agente                        |

- ⚠️ **`store.vector.enabled: false` NÃO é keyword-only.** Ele só troca o _mecanismo_ do
  vetor; o caminho semântico continua ativo. O produto ajuda a confundir: o status rotula
  esse estado como `"fts-only"`, e o rótulo mente. Keyword-only de verdade é
  `provider: "none"` — e ele custa **-67% de recall em paráfrase** (top-5 2/9 contra 6/9),
  medido em bench adversarial de 27 queries (05/set/2026).
- **Nenhum GGUF é assado na imagem.** O plugin `@openclaw/llama-cpp-provider` baixa o
  binário do llama-server e o GGUF QAT (~328 MB) para `/app/models/llama.cpp`, dentro do
  state dir. Em produção esse caminho é bind mount: conteúdo de imagem ali seria sombreado
  pelo volume, então assar não funcionaria. O `entrypoint.sh` só garante o diretório.
- ⚠️ **Usar um GGUF próprio quebra chunk grande.** O provider decide o preset por
  _comparação literal de string_ com o nome do modelo QAT default. GGUF próprio →
  `isDefault:false` → o preset sai sem `ubatch-size = 2048` → llama-server fica no default
  512 e todo chunk >512 tokens devolve `HTTP 500 "input is too large to process"`. Pior,
  `memory status --deep` continua dizendo `Embeddings: ready` (ele embeda só uma query
  curta). Fique no QAT default; se precisar de outro modelo, passe
  `--ubatch-size 2048 --batch-size 2048` nos args do `localService`.
- Trocar de modelo de embedding **invalida o índice** (pesos diferentes, mesmo com as
  mesmas 768 dims): exige `openclaw memory index --force`.
- **`dreaming/` fica fora do índice por symlink.** Não há exclusão por config — o walker
  varre `memory/**` sempre e a única alavanca é `walkDirectory(dir, { symlinks: "skip" })`,
  hard-coded no produto. `memory/dreaming` tem que ser symlink pra fora de `memory/`.

## Regras

- Alterações de configuração vão em `openclaw.json` (raiz), não em `app/openclaw.json`.
- O container Docker precisa ser reiniciado (`docker restart lzbot`) para aplicar mudanças em `openclaw.json`.

### Higiene de browser (toda skill que usa a tool `browser`)

Regra da casa, canônica — vale pra qualquer skill que dirige o browser (compras, watchlist, olx/cds, nintendo, spotify, instagram, etc.). Toda instância deve carregar isto no `AGENTS.md` de runtime (seção Skills) e/ou nas próprias skills:

- Antes de navegar, `action=tabs` e anote as abas já existentes (do usuário) — **nunca feche essas**.
- **Uma única aba reutilizável**: troque a URL na mesma aba, não abra aba nova por página.
- Cleanup em `finally` (**inclusive em erro**): feche só o delta de abas que este fluxo abriu.
- Verificação final via `action=tabs`; se sobrou aba sua, feche.
- **Nunca** encerre o browser/perfil persistente (`action=stop`/fechar perfil `openclaw`) — derruba a sessão logada e força relogin.
