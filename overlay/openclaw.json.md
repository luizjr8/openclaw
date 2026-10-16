# `openclaw.json` — racional das escolhas

Este arquivo é a documentação do `openclaw.json` canônico do boilerplate.

## Por que o racional mora aqui e não dentro do JSON

Até a `2026.9.1` o `openclaw.json` era mantido como JSONC: 83 linhas de comentário
`//` explicando cada escolha não-óbvia. Não dá mais para sustentar isso:

- **O produto trata `openclaw.json` como JSON puro.** `openclaw config set` reescreve
  o arquivo via `JSON.parse`/`JSON.stringify` e apaga **todos** os comentários (medido
  no binário real: 83 → 0). Qualquer uso do comando derrubava a documentação inteira,
  em silêncio.
- **As migrations do boilerplate também usam `JSON.parse`.** As `0002`, `0003` e `0005`
  leem o arquivo direto; com comentário, elas não degradam — elas **crasham**. E a
  `0002` roda antes de todas, então uma instância nova morria no primeiro boot.
- **Não havia rota barata de edição preservando comentário.** `jsonc-parser` não é
  dependência exportada do `openclaw@2026.9.1` (existe só inlined no bundle do `dist`),
  então usá-la significaria assar uma dep nova na imagem para manter algo que o próprio
  produto apaga na primeira oportunidade.

Então o JSON virou estrito e o racional veio para cá. **Ao mexer no `openclaw.json`,
atualize a seção correspondente deste arquivo.**

`agents.json5` **continua sendo JSON5 com comentário** — aquele arquivo não é reescrito
por nenhuma migration nem pelo `openclaw config set`, e o racional por agente segue lá.

---

## `gateway.auth.token`

SecretRef (`{source:"env", provider:"default", id:"OPENCLAW_GATEWAY_TOKEN"}`) em vez de
`"${OPENCLAW_GATEWAY_TOKEN}"`: a interpolação de string resolve o valor no load e ele
passa a circular em dump de config/diagnóstico. A ref é resolvida só na hora do uso e o
valor nunca é serializado de volta.

**Não "sincronize" isto de volta para a forma string.** A `2026.9.2` mantém a SecretRef
como cidadã de primeira classe neste campo — `configuration-reference.md:852` fala de
`gateway.auth.token` "including SecretRefs" e a `:913` descreve o fail-closed quando a ref
não resolve. Instâncias que aparecem com `"token": "${OPENCLAW_GATEWAY_TOKEN}"` foram
reescritas pelo `configure`/`doctor`, que normaliza para string; é regressão de writer, não
mudança de schema. A forma objeto continua validando (`openclaw config validate` → `Config
valid` na 2026.9.2).

## `agents.defaults.systemAgent`

A `2026.9.1` aposentou o marcador `default: true` por agente: o loader o remove e
"materializa" o dono de cada superfície sozinho, com warning. Melhor declarar —
`agents.ownership: "explicit"` + `systemAgent` + `talk`.

## `agents.defaults.compaction`

`memoryFlush.prompt` / `memoryFlush.systemPrompt` saíram do schema na `2026.9.1` — o
flush pré-compactação agora usa o prompt do próprio produto. Quem quiser texto próprio
precisa de um hook, não de config.

## `agents.entries`

`agents.list` (array) virou `agents.entries` (objeto chaveado pelo id) na `2026.9.1`.
O `$include` continua valendo — ver `agents.json5`.

## `plugins.entries.codex`

Harness Codex (app-server da OpenAI). Assado na imagem em `/lzbot/extensions/codex`.
Inerte enquanto nenhum agente usar modelo `openai/*` com runtime codex — só registra
provider + harness.

## `plugins.entries.llama-cpp`

Provider de embedding local (llama-server). É o que sustenta
`memory.search.provider: "local"` — ver a seção `memory` abaixo.

## `tools.media`

`2026.9.1`: os modelos de mídia saíram de `tools.media.<tipo>.models` e viraram UMA
lista em `tools.media.models`, cada entrada declarando `capabilities`.
`tools.media.<tipo>` continua existindo, mas só com limites (`enabled` / `maxBytes` /
`timeoutSeconds`), sem catálogo próprio.

`faster-whisper` roda com `--device cpu --compute_type float32` explícitos. A imagem
instala `faster-whisper==1.2.1` **sem** CUDA/torch-GPU (`Dockerfile:158-159`), então o
`device="auto"` do ctranslate2 não tem GPU para achar e o `compute_type` default cai em
int8 conforme a CPU. Declarar os dois remove a autodetecção do caminho de áudio — a
imagem é CPU-only por construção, não há cenário em que "auto" acerte melhor.

## `tools.byProvider.telegram.deny`

`secrets` fica negada apenas para Telegram. Desde a `2026.9.3`, essa tool abre uma
entrada mascarada vinculada ao Control UI ou app nativo; o próprio Telegram não aceita o
valor. O boilerplate é Telegram-first e não pressupõe Control UI público, portanto expor
a tool que sempre falhará só faz o agente parar num fluxo sem saída.

Isso não libera credenciais no chat. Use uma superfície segura fora do transcript (por
exemplo, terminal controlado pelo operador) quando a tarefa exigir uma senha ou token.

Pelo mesmo motivo, Telegram também nega `terminal`, `dashboard`, `portal`, `canvas` e
`mobile_ui`: todas dependem de Control UI, app nativo ou dispositivo pareado. Não negar
`ask_user`; a pergunta interativa usa botões e resposta textual nativos do Telegram.

## `gateway.controlUi.enabled`

`false` por padrão no boilerplate. `enabled` é `true` por default do schema (Control UI
liga sozinha), mas o boilerplate é Telegram-first e não pressupõe operador acessando
Control UI — mesma lógica do deny acima. Instância que precisa de Control UI habilita
explicitamente.

## `browser.tabCleanup`

`2026.9.1`: virou só on/off. `idleMinutes` / `maxTabsPerSession` / `sweepMinutes` saíram
do schema; a política agora é do produto.

## Runtime Anthropic por agente

O provider e o runtime são conceitos separados. As refs usam o provider canônico
`anthropic/*`; somente o `coder` fixa `agentRuntime.id: "claude-cli"`, por wildcard em
`agents.entries.coder.models["anthropic/*"]`. O `main` não tem runtime explícito e cai no
loop nativo do OpenClaw. Não há `models.providers.anthropic.agentRuntime` nem provider
legado `claude-cli` no canônico.

## `agents.defaults.models` / `modelPolicy.allow`

**Deliberadamente ausentes do canônico.** São o catálogo por modelo (aliases/parâmetros) e
a allowlist explícita de override. `modelPolicy.allow` omitido ou `[]` significa
_allow-any_ (`concepts/models.md:60`), que é o default certo para um boilerplate: quem
clona escolhe os modelos pelo `.env`, não por uma lista assada aqui.

Instância viva que aparecer com os dois preenchidos e um
`meta.migrations.modelPolicyAllowlist: true` não ganhou shape novo — é o `doctor --fix`
copiando o _legacy model map_ daquela instância para dentro do `modelPolicy.allow`
(`concepts/models.md:69`). O conteúdo é a escolha de modelos daquele host; não sobe para o
canônico.

## `memory` — busca builtin in-process

A busca é builtin e in-process desde a `2026.9.1`: sem backend externo, sem daemon, sem
coleção curada.

Config decidida por bench adversarial (27 queries: 9 exatas, 9 paráfrases, 9 conceituais),
05/set/2026:

| chave                                | valor                   | racional                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------ | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `search.provider`                    | `"local"`               | embedding via llama-server residente (embeddinggemma 300m QAT). Vetorizado de verdade: paráfrase top-5 **6/9** contra **2/9** do keyword-only (`provider:"none"`). Nas exatas os dois empatam — o vetor só soma.                                                                                                                                            |
| `search.store.vector.enabled`        | `false`                 | **não** desliga a busca semântica. O status do produto rotula "fts-only", e o rótulo mente: `false` só troca o `sqlite-vec`, que roda em SUBPROCESSO por query, por um scan vetorial in-process. Resultado idêntico path-a-path nas 27 queries, a **296 ms** em vez de **2902 ms**. Revisar se o corpus passar de ~500 chunks (hoje ~3,5k chunks, folgado). |
| `search.sources`                     | `["memory","sessions"]` | além da memória curada, indexa transcript de sessão — é o que dá recall de "ontem". Ver a seção abaixo.                                                                                                                                                                                                                                                     |
| `search.rememberAcrossConversations` | `true`                  | recall protegido das outras conversas do agente.                                                                                                                                                                                                                                                                                                            |
| `search.fallback`                    | `"none"`                | sem degradar para outro provider em silêncio: se o embedding local não estiver de pé, é para aparecer.                                                                                                                                                                                                                                                      |

<!-- Não adianta comentar o `vector.enabled` dentro do próprio openclaw.json.
     Testado em 06/set/2026: o loader ACEITA comentário JSONC (`config validate`
     passa, `config get` devolve o valor), mas o watcher do gateway regrava o
     arquivo em segundos e apaga o comentário. Nenhum backup histórico do
     openclaw.json tem uma linha de comentário — é o writer, não a sintaxe.
     Por isso o racional mora aqui e no migrations/0006. -->

<Note>
`store.vector.enabled: false` é a maior pegadinha desta seção: o `openclaw memory status`
imprime `Vector store: disabled` / `Semantic vectors: disabled` e parece defeito. Não é —
a busca semântica continua de pé (query sem sobreposição lexical acerta o alvo). Antes de
"consertar" isso, leia a linha da tabela acima: ligar **piora** a latência ~10x.
</Note>

### `sources: ["sessions"]` + `experimental.sessionMemory` — recall de "ontem"

Paridade deliberada com o sentinel (lzbot `2026.7.1` em sing1), que tem recall bom de
conversas recentes. O equivalente builtin na `2026.9.1` (doc `memory-search.md:159`) é opt-in em três
lugares, e **os três são necessários**:

1. `memory.search.sources` inclui `"sessions"`;
2. `memory.search.experimental.sessionMemory: true`;
3. `tools.sessions.visibility: "all"`.

Os três foram validados contra o schema real da `2026.9.1` (`openclaw config
validate` → `Config valid`). Detalhe que engana: a doc de `memory-search` só cita
`"agent"` / `"tree"` / `"self"` para `visibility` e **omite `"all"`** — mas o enum
do schema aceita os quatro. `"all"` é o mais amplo (qualquer sessão, de qualquer
usuário do mesmo agente); cross-agent ainda exige `tools.agentToAgent`.

**Atualização `2026.9.2`:** dois dos três opt-ins deixaram de ser opt-in.
`tools.sessions.visibility` passou a ter default `"all"` e o `tools.agentToAgent` virou
default-on (CHANGELOG 2026.9.2, "Cross-agent session access", #136755;
`configuration-reference.md:1421` chama a policy de "default-on"). Os dois seguem
escritos no canônico **de propósito**: valem como declaração explícita (o boilerplate não
deve depender de default que o produto pode reverter) e o `visibility` é justamente a
chave onde a doc de `memory-search` erra o enum. Quem quiser fechar o escopo agora precisa
de ação afirmativa — `visibility: "agent"` ou `"self"`, e `agentToAgent.enabled: false`.

Só o item 2 (`memory.search.experimental.sessionMemory: true`) continua sendo opt-in de
verdade.

O custo é O(n) sobre os transcripts, aceito de propósito — recall de ontem vale mais que
a latência de busca neste workload.

Nota de migração: a `0005` preenche `memory.search` só onde falta. Uma instância antiga,
sem `memory.search` nenhum, recebe o bloco inteiro, com `sessions`. Uma instância que já tenha `sources` definido mantém o valor dela — se quiser
sessions lá, é edição manual.

### `dreaming/` fora do índice

Não há exclusão por config: o walker varre `memory/**` sempre. O único mecanismo é o
walker pular symlink — ele chama `walkDirectory(dir, { symlinks: "skip" })`, hard-coded.
Então `memory/dreaming` precisa ser um **symlink** para um diretório fora de `memory/`.

## `messages`

O ack do WhatsApp era `channels.whatsapp.ackReaction {emoji, direct, group}`; consolidou
em `messages.ackReaction` + `messages.ackReactionScope`, agora globais.
`whatsapp.debounceMs` virou `messages.inbound.byChannel.whatsapp`.

## `channels.whatsapp`

- **`enabled`** aqui era o antigo top-level `web.enabled` (o bloco `web` inteiro saiu do
  schema; `keepAliveIntervalMs` / `connectTimeoutMs` / `defaultQueryTimeoutMs` não têm
  sucessor — são do produto agora).
- **`dmPolicy: "pairing"`** é o default recomendado pela doc; `"open"` faz o bot responder
  qualquer número que mandar mensagem.
- **`accounts.default.name`** fica a cargo da instância (nome do bot no WhatsApp) — por
  isso o objeto vazio no canônico.

## `bindings`

Traz **um** binding: dono de `telegram:default` -> `main`. Não é opcional, e o motivo é a
combinação de duas mudanças da 2026.9.1: `agents.ownership: "explicit"` + a aposentadoria
do `default: true` no `agents.json5`. Com as duas juntas, **nenhum** agente é dono de um
canal por herança, e o telegram sai do ar no boot com

    channel exited: Multiple agents are configured, but telegram account default
    routing has no explicit owner. Add a channel-wide binding for telegram:default
    or configure a sole agent.

Não conflita com o roteamento por tópico (`channels.telegram.groups[...].topics`): route de
grupo/tópico tem precedência sobre o binding channel-wide, que é só o fallback.

Não há binding de WhatsApp por default — quando o canal for ligado, o dono precisa de um
binding equivalente pelo mesmo motivo.

Para o padrão hub-de-fornecedores (todo número desconhecido atendido pelo concierge), use
o catch-all abaixo — e note que o route do próprio número **precisa vir antes**, senão o
dono também cai no concierge:

```json
"bindings": [
  {
    "type": "route",
    "agentId": "main",
    "comment": "Dono -> agente principal (precede o catch-all).",
    "match": {
      "channel": "whatsapp",
      "accountId": "default",
      "peer": { "kind": "direct", "id": "${WHATSAPP_OWNER_NUMBER}" }
    }
  },
  {
    "type": "route",
    "agentId": "concierge",
    "comment": "Catch-all whatsapp: qualquer outro numero -> concierge. Zero config por contato.",
    "match": { "channel": "whatsapp", "accountId": "default" }
  }
]
```

## `env.vars`

Env exportada pelo gateway para o ambiente de execução dos agentes.

`LZBOT_WORKSPACE_ROOT: "/app/workspace"` é o mesmo valor que a
`migrations/0003_ensure_lzbot_workspace_root_env.sh` injeta (o default
`WORKSPACE_ROOT_VALUE` dela). Declarar no canônico torna a 0003 um no-op em instância
nova — ela só adiciona a chave quando falta, então não há conflito, e a instância passa a
nascer já com a env em vez de depender da migration ter rodado.

Serve às skills genéricas (`financas`, `deepseek-saldo`, ...) que resolvem dados em
`<LZBOT_WORKSPACE_ROOT>/skills/<nome>/...` com fallback pro cwd do processo. Sem a env o
fallback funciona por coincidência (o cwd normalmente já é o workspace do agente); com ela,
a dependência deixa de ser implícita.

**Só valor genérico aqui.** `env.vars` é texto puro no config — nada de senha, keyring ou
token. Credencial de instância entra por `.env`/SecretRef, nunca por este bloco.

## O que NÃO sobe de uma instância viva para cá

Diff contra `/app/openclaw.json` de uma instância em produção devolve muita coisa que
parece shape novo e não é. Categorias que ficam de fora por definição:

- **Estado escrito pelo produto:** `wizard.*` (`lastRunAt` / `lastRunVersion` / ...) e
  `meta.*` (`lastTouchedVersion`, `migrations.*`). São marcadores de execução daquele host.
- **Endereço e topologia de deploy:** `gateway.bind`, `gateway.trustedProxies`,
  `gateway.controlUi.allowedOrigins`, `gateway.nodes.commands.allow`,
  `plugins.entries.device-pair.config.publicUrl`, `browser.extraArgs` de proxy.
- **Identidade e roteamento por peer:** telefone, id de Telegram, `bindings` com
  `peer.id`, `accounts.default.name`, `dmPolicy`/`allowFrom` afrouxados, `groups.*`
  específicos.
- **Tuning de conversa:** `messages.queue.mode`, `messages.inbound.byChannel.*`,
  `session.resetByType` / `resetByChannel`, `channels.*.reactionLevel`,
  `channels.*.streaming.block`, `thinkingDefault`, `skills.workshop.approvalPolicy`,
  `tools.message.crossContext`. Schema válido, mas é gosto de operação — o canônico fica no
  default documentado.
- **`memory.search.extraPaths` duplicando o default:** entrada tipo
  `[{path:"memory",pattern:"*.md"}, "MEMORY.md"]` é resíduo do `doctor --fix` em instância
  migrada, que preserva os paths antigos como `extraPaths`. O builtin já indexa `MEMORY.md`,
  `USER.md` e `memory/*.md` sem configuração (`concepts/memory-builtin.md:170`).
  `extraPaths` só vale para diretório **fora** do workspace (ex.: memória compartilhada
  entre agentes).
- **Secret literal:** chave de API escrita à mão onde o canônico usa `${VAR}`. É vazamento
  a corrigir na instância, não valor a copiar.

## `talk.agentId`

Dono das sessões de Talk criadas sem agente explícito no session key. Faz par com
`agents.defaults.systemAgent`: com `ownership: "explicit"` nada resolve por default, tem
que estar escrito.

## `models.providers["llama-cpp"]`

O backend de embedding da memória. Sem ele, `memory.search.provider: "local"` fica sem
motor: `openclaw memory index` falha com _"Managed local embeddings are unavailable"_ e a
busca degrada para keyword sem score sobre os transcripts já indexados — a memória CURADA
(`MEMORY.md`, `memory/**`) simplesmente não aparece. Medido no ensaio da Fase I: sem o
bloco, toda query volta 1.000 e só cita `sessions/`; com ele, 3553 chunks (86 arquivos de
memória + 228 de sessão) e a paráfrase acerta `MEMORY.md` e `memory/feedback-*.md`.

Nada aqui precisa ser provisionado à mão. O plugin `@openclaw/llama-cpp-provider` baixa o
binário do `llama-server` (build pinada no `command`), escreve o `models.ini` e baixa o
GGUF QAT do embeddinggemma-300m para dentro do state dir — verificado num volume sem
`/app/tools` nem `/app/models`, que é o estado da produção hoje.

`localService.idleStopMs` mantém o serviço quente por 10 min depois do último uso; é ele
que evita o spawn por query. `store.vector.enabled: false` no `memory.search` NÃO desliga
o vetor — desliga o sqlite-vec em subprocesso; o scan continua in-process e vetorizado
(ver Fase E).
