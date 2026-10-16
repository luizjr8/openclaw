# TOOLS.md

Dados técnicos do ambiente local. Skills definem _como_ as ferramentas funcionam; aqui ficam os detalhes específicos desta instância.

## Ambiente

- **Host:** **identificar**
- **Container:** Ubuntu 24.04 (Noble) em Docker, usuário `lzbot` com sudo sem senha
- **CPU:** **identificar**
- **RAM:** **identificar**
- **Disk:** **identificar**
- **Node.js:** via nvm (`~/.nvm`), versão fixada no build
- **Python:** System Python 3.12 (via apt). PEP 668 ativo (Noble) — instalar pacotes com `pip install --break-system-packages` (sem venv por skill; o container já é o isolamento). `faster-whisper`, `playwright` etc. instalados assim.
- **Config:** `/app/openclaw.json`
- **Bootstrap:** `/app/entrypoint.sh` — roda automaticamente no boot do container; symlinka `~/.ssh`, `~/.cache`, `~/.claude` pro volume `/app` (persistência) e monta as coleções de memória. Não existe mais `.bootstrap.sh` manual.
- **Reload:** `gateway.reload.mode: "hybrid"` — tenta hot reload e cai pra restart quando necessário. Config hot-reloada; **código de extensão TS não** (ver Webhooks Inbound).
- **Workspace:** `/app/workspace/`

## Agentes

Os agentes não fixam modelo próprio: herdam o default do gateway (definido em `/app/.env` / `openclaw.json`). Não hardcode nome de modelo aqui — ele muda com frequência; consulte `session_status` ou o `.env` pra saber o modelo em uso.

- **main** — agente padrão de uso diário.
- **coder** — agente de trabalho técnico (revisão, debugging, implementação).
  - ID: `coder`
  - Spawnável via `sessions_spawn(agentId: "coder")`
  - Comunicável via `sessions_send()`

## Comunicação entre Agentes

- `tools.sessions.visibility: all` — sessões visíveis entre agentes
- `tools.agentToAgent.enabled: true` — envio de mensagens cross-agent

## Áudio / Transcrição

- Configurado em `openclaw.json → tools.media.audio`
- Whisper via `faster-whisper` CLI, modelo `base` (idioma não fixado nos args — autodetect)
- Limite: 20 MB (`maxBytes: 20971520`), timeout: 120s (`timeoutSeconds`)

**REGRA:** USAR APENAS o faster-whisper local. NUNCA usar API externa de transcrição (OpenAI Whisper API, etc) — consome tokens desnecessariamente. A transcrição local já gera arquivos `.srt` automaticamente no inbound. É só lê-los.

## Skills Custom

Duas origens:

- **`/app/skills`** — skills genéricas do boilerplate, **stateless** (vêm na versão/imagem). É symlink pro `dev/lzbot/skills`, então editar uma dessas é diff direto no repo canônico. Dados de usuário dessas skills (DBs, arquivos customizados) ficam em `/app/workspace/skills/<nome>/`, nunca dentro de `/app/skills`.
- **`/app/workspace/skills`** — skills e dados específicos **da instância**, não versionados no boilerplate.

Skills em `/app/workspace/skills/` disponíveis para uso:

| Skill            | Descrição                                 | Comando                                                            |
| ---------------- | ----------------------------------------- | ------------------------------------------------------------------ |
| busca-voos       | Busca voos domésticos via Google Flights  | `fli flights <ORIGEM> <DESTINO> <DATA> [flags]`                    |
| financas         | Gerencia finanças pessoais                | `python skills/financas/scripts/financas.py <comando>`             |
| gog              | Google Workspace (Gmail, Calendar, Drive) | Via `~/.openclaw/scripts/gog`                                      |
| gog-auth-init    | Setup inicial OAuth do Google             | Seguir SKILL.md                                                    |
| instagram-reader | Extrai posts do Instagram sem login       | `python skills/instagram-reader/scripts/instagram_reader.py <url>` |
| liturgia-diaria  | Leituras da missa e evangelho do dia      | `python skills/liturgia-diaria/scripts/main.py`                    |
| deepseek-saldo   | Saldo e uso da API DeepSeek               | `python skills/deepseek-saldo/scripts/consulta.py`                 |

## Regras de workflow

- Skills Python: estruturar como pacote instalável (`setup.py` ou `pyproject.toml`), usar via CLI após `pip install`.
- Scripts temporários ou de missão única: criar em `/tmp`, não na pasta da skill.
- Workspace da skill: manter apenas arquivos essenciais (código, SKILL.md, `_meta.json`). Outputs e artefatos de sessão ficam em `/tmp` ou são deletados.

## Webhooks Inbound

- **Gestão de rotas:** usar SEMPRE a tool `webhook_manage` (ações: `list`, `add`, `remove`). Não ler nem editar arquivos de rotas na mão — use a action `list` pra consultas.
- A extensão (`extensions/inbound-webhooks`, skill homônima) escuta na porta **16889** por default — override via `pluginConfig.port` ou `WEBHOOK_PORT`. Logs de requisições em `/app/workspace/memory/webhooks/`.
- **Gotcha — `add` precisa de `sessionKey` explícito** no objeto `config`: o contrato de execução da tool não expõe a sessão ativa, então sem isso a rota nasce sem destino.
- **Gotcha — reload de código de plugin TS exige restart REAL do processo** (`kill -TERM 1` / `docker restart`). `gateway restart` (SIGUSR1) só faz reload soft de config/sessões; os módulos TS ficam cacheados no registry ESM e não re-importam.

## SSH / Hosts locais

- **Chave:** `/app/.ssh/id_ed25519`
- **`~/.ssh` é symlink pra `/app/.ssh`** (feito pelo `entrypoint.sh`) — chaves, `config` e `known_hosts` persistem sozinhos; nada a copiar depois do restart.
- **Git config:** `core.sshCommand: ssh -i /app/.ssh/id_ed25519` — o `entrypoint.sh` só aplica isso se `/app/.git` existir; quando `/app` não é repo, setar por repo (`git -C <repo> config core.sshCommand ...`).
