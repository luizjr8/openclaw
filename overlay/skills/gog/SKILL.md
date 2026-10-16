---
name: gog
description: Google Workspace CLI para Gmail, Calendar, Drive, Docs, Sheets, Slides e Analytics.
homepage: https://gogcli.sh
metadata:
  {
    "openclaw":
      {
        "emoji": "🎮",
        "requires": { "bins": ["gog"] },
        "install":
          [
            {
              "id": "brew",
              "kind": "brew",
              "formula": "gogcli",
              "bins": ["gog"],
              "label": "Install gog (brew)",
            },
          ],
      },
  }
---

# gog - Google Workspace CLI

CLI unificada para automação de Google Workspace (Gmail, Calendar, Drive, Docs, Sheets, Slides, Analytics, Search Console).

## 🔑 Credential lookup

Sempre buscar o perfil em `auth-profiles.json` pelo `user_id` antes de qualquer invocação:

```python
import json, tempfile, os

profiles = json.load(open("/app/auth-profiles.json"))
user_id = "<user_id>"  # slug/id do cliente (ex: "acme", "luiz")

profile = profiles.get("google", {}).get(user_id)
# profile: {"email": "...", "keyring_password": "...", "credentials": {...}}

if profile:
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump(profile["credentials"], tmp); tmp.close()
    # registrar credenciais se ainda não autenticado:
    # gog auth credentials <tmp.name>
else:
    import secrets
    keyring_password = secrets.token_urlsafe(32)
    profiles.setdefault("google", {})[user_id] = {
        "email": "client@gmail.com",
        "credentials": client_secret_content,
    }
    with open("/app/auth-profiles.json", "w") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)

    # salvar keyring password no .env do workspace do cliente
    env_path = f"/app/workspace/clients/{user_id}/.env"
    os.makedirs(os.path.dirname(env_path), exist_ok=True)
    with open(env_path, "a") as f:
        f.write(f"GOG_KEYRING_PASSWORD={keyring_password}\n")
```

### Invocar gog com perfil

```bash
# Carregar keyring password do .env do cliente
source /app/workspace/clients/<user_id>/.env

GOG_ACCOUNT="<profile.email>" \
  gog gmail search 'newer_than:7d' --max 10 --no-input
```

Shape esperado em `auth-profiles.json`:

```json
{
  "google": {
    "<user_id>": {
      "email": "client@gmail.com",
      "credentials": {
        "installed": {
          "client_id": "...",
          "client_secret": "...",
          "project_id": "...",
          "auth_uri": "https://accounts.google.com/o/oauth2/auth",
          "token_uri": "https://oauth2.googleapis.com/token",
          "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
        }
      }
    }
  }
}
```

---

## ⚙️ Setup (uma vez)

### IMPORTANTE: Webhooks OAuth e Chave do Cofre

- O `redirect_uri` para OAuth é sempre estático no Google Console: `https://<public_url>/in/gog-oauth-callback`
- `GOG_KEYRING_PASSWORD` **deve** estar configurado permanentemente no ambiente (ex: `gateway.env.GOG_KEYRING_PASSWORD` no `openclaw.json`) para estar disponível em boot/background jobs

### Ambiente local

```bash
gog auth credentials /path/to/client_secret.json
gog auth add seu-email@gmail.com --services gmail,calendar,drive,contacts,sheets,docs,analytics,searchconsole,slides
gog auth list
```

### Variáveis de ambiente (shells não-interativos)

```bash
export GOG_KEYRING_PASSWORD="<password>"
export GOG_ACCOUNT="you@gmail.com"
```

### Auth remota via webhook-inbound (instância sem browser local)

> ⚠️ **Isso exige um client OAuth tipo "Aplicativo da Web" (`web`) no Google Console —
> nunca o "Aplicativo de Desktop" (`installed`).** Desktop não tem campo de "Authorized
> redirect URIs" no Console (é restrição do Google, não config nossa); ele só aceita
> `http://localhost`/`urn:ietf:wg:oauth:2.0:oob`, e qualquer `--redirect-uri` custom cai
> de volta em `localhost` na hora do consentimento — foi exatamente esse o bug
> diagnosticado ao vivo em 14/09/2026, achado só depois de comparar o `client_id` real
> da URL gerada com o que a gente achava que tava usando. Cheque o campo raiz do JSON:
> `"web": {...}` é o tipo certo, `"installed": {...}` não serve pra esse fluxo.
>
> Se o `client_secret.json` já registrado como client `default` for tipo `installed`
> (comum — é o que o `gog auth credentials` sem `--client` grava), registre o client Web
> **num bucket separado**, sem sobrescrever o `default`: `gog auth credentials
/path/to/web-client-secret.json --client web`. Isso evita quebrar qualquer conta já
> autenticada no `default` — refresh token é amarrado ao client que o emitiu, trocar o
> client por baixo invalida silenciosamente contas já conectadas. Use `--client web` em
> todos os comandos do fluxo remoto abaixo.

Quando a instância não tem browser à mão pra completar o OAuth (servidor headless), o
fluxo é `--remote` em dois passos, com o passo 2 disparado por uma rota `webhook-inbound`
(tool `webhook_manage`) que recebe o callback do Google e roda o comando de troca de
código:

```bash
# Passo 1: imprime a URL de autorização (mandar o usuário abrir e logar)
gog auth add <email> --remote --step 1 --client web \
  --redirect-uri "https://<public_url>/in/gog-oauth-callback" \
  --services gmail,calendar,drive,contacts,docs,sheets
```

> ⚠️ **`--redirect-uri` é obrigatório no passo 1, nunca omita.** Sem ele, o `gog` cai no
> default do `client_secret.json` (`http://localhost/`, com barra) e a URL de
> autorização nunca bate no webhook — o usuário cai numa página "não é possível acessar
> este site" com o `code` preso na barra de endereço, e o passo 2 exige colar essa URL
> manualmente (mismatch confirmado ao vivo em 14/09/2026). O `--redirect-uri` do passo 2
> tem que ser **byte-a-byte igual** ao usado no passo 1 (inclusive barra final), senão dá
> `state mismatch`.

Rota (crie com `webhook_manage action=add`, id `gog-oauth-callback`):

```json
{
  "sessionKey": "<sessão/canal pra onde reportar o callback>",
  "prompt": "Recebi um callback OAuth do Google para autenticar <email> no gog. A URL recebida acima começa com /in/gog-oauth-callback?... — concatene o domínio: https://<public_url>{URL_PATH_COM_QUERY}. Execute: GOG_KEYRING_PASSWORD=<senha> gog auth add <email> --remote --step 2 --client web --auth-url \"<URL_COMPLETA>\" --redirect-uri \"https://<public_url>/in/gog-oauth-callback\" --services <mesmos_do_passo_1> && GOG_KEYRING_PASSWORD=<senha> gog auth list --client web",
  "secret": "",
  "deliver": true,
  "runOnce": true,
  "messageTarget": "<canal>",
  "responseHtml": "<html><body><h2>OK</h2><p>Callback recebido, processando autenticação...</p></body></html>"
}
```

- **Antes de criar a rota, liste o que já existe** (`webhook_manage action=list`, e
  confira `/app/workspace/webhook-routes.json`). O `redirect_uri` é estático no Google
  Console, então o id `gog-oauth-callback` é **global e único**: se sobrou uma rota de um
  fluxo antigo (outra conta, outra sessão), ela vence a sua e o callback do usuário é
  entregue na **sessão errada**, que roda o passo 2 com os parâmetros dela. Se houver
  colisão: faça backup do arquivo (`cp webhook-routes.json webhook-routes.json.bak-$(date +%s)`),
  reescreva a entrada apontando pra sua sessão/conta, e no fim restaure o backup ou remova
  a rota — combine com o usuário qual dos dois.
- `runOnce: true` é o padrão certo pra autenticação pontual — a rota se remove sozinha
  após o primeiro hit, então não fica exposta depois de usada e não sobra pra colidir com
  o próximo fluxo semanas depois (foi exatamente o que aconteceu em 14/09/2026: rota de
  `luiz@lzjr.com.br` deixada com `runOnce: false` sequestrou o callback de outra conta).
  Só deixe `false` se a instância reautentica com frequência nesse mesmo domínio.
- **Nunca coloque `GOG_KEYRING_PASSWORD` em texto puro no `prompt` da rota.** A variável já
  está no env do gateway (`gateway.env`), o comando herda sozinho — em JSON no workspace
  ela só vira credencial vazada.
- **Ponha uma guarda no `prompt` contra request sem `code=`:** _"Se a query NÃO tiver o
  parâmetro `code=`, não execute nada e não remova a rota."_ Prescan do provedor, retry ou
  um probe seu batem na URL sem `code` e, com `runOnce: true`, queimam a rota antes do
  login real. Com a guarda, dá pra testar a rota com `curl -s
"https://<public_url>/in/gog-oauth-callback?probe=1"` e confirmar em qual sessão ela cai
  antes de mandar a URL pro usuário.
- **`"secret": ""` é obrigatório nessa rota, nunca omita.** Se `webhook-inbound.config.secret`
  (ou `WEBHOOK_SECRET`) estiver setado globalmente, toda rota exige esse secret por padrão
  — mas o redirect do Google não manda nenhum header/token nosso, só o `state` OAuth
  próprio. Sem o override, a rota devolve **HTTP 418** pro navegador do usuário no meio
  do fluxo de login (confirmado ao vivo em 14/09/2026). O `state` já cumpre o papel de
  anti-CSRF aqui, então abrir mão do secret nessa rota específica é seguro.
- O `redirect_uri` (`https://<public_url>/in/gog-oauth-callback`) precisa estar
  registrado nas "Authorized redirect URIs" do client OAuth no Google Console **antes**
  do passo 1 — isso é feito manualmente no Console, o CLI não registra sozinho.
- Múltiplas instâncias/domínios podem compartilhar o **mesmo** client_id/secret Web
  (`client_secret.json` tipo `web`), desde que cada domínio usado tenha seu próprio
  redirect_uri registrado no mesmo client OAuth no Console.
- Avise o usuário pra **conferir a conta na tela de consentimento**. Quem tem várias contas
  logadas no browser aprova com a errada sem perceber; o callback vem com `authuser=N`
  (N > 0 = não é a primeira conta) e o token grava pra conta errada.

#### Diagnóstico: `manual auth state mismatch`

Esse erro quase nunca é link expirado. O `state` do passo 1 fica pendente no bucket do
client, e o passo 2 procura no bucket que **você** passar. Na ordem:

1. **Faltou `--client web` no passo 2** → procura no bucket `default`, não acha o state.
2. **A rota errada atendeu o callback** (ver colisão de id acima) — o comando executado foi
   o da outra sessão, com outra conta e outro `--client`.
3. `--redirect-uri` diferente do passo 1 (inclusive barra final).

Antes de gerar URL nova, rode `gog auth add ... --remote --step 1 ...` de novo: se a saída
vier com `state_reused: true`, **o state antigo continua vivo e o link original ainda
vale** — o problema era bucket/rota, não expiração. Regerar por reflexo só esconde a causa.

### Gatilho: usuário pede pra conectar a conta Google

Frases como "quero conectar meu gmail/google", "autenticar minha conta", "linkar meu
Google" disparam o fluxo acima **self-service**, sem precisar do operador:

1. Pergunte (ou já saiba) qual email e quais serviços autorizar; rode o passo 1
   (`gog auth add <email> --remote --step 1 ...`) e mande a URL de autorização pro
   próprio usuário abrir e logar.
2. Antes de mandar a URL, liste as rotas existentes e resolva colisão de id, então crie a
   rota `gog-oauth-callback` via `webhook_manage` (`action=add`, ver JSON acima) com
   `sessionKey`/`messageTarget` apontando pra esta mesma conversa e `runOnce: true` — assim
   o retorno chega automaticamente aqui quando o usuário concluir o login, sem o usuário
   precisar colar nada manualmente. Um probe sem `code=` confirma que caiu na sua sessão.
3. `redirect_uri` já registrado no client OAuth (Google Console) e consent screen
   público são pré-requisitos de infra, verificados uma vez pelo operador — não
   precisam ser checados a cada pedido do usuário.

---

## 📧 Gmail

### Enviar Email

> **`--signature` só existe no `send`**, não no `drafts create`. Se o usuário quiser revisar antes de enviar, salvar como draft sem assinatura é o comportamento correto — a assinatura é aplicada apenas no envio final.

```bash
# Básico (sempre com assinatura)
gog gmail send --to recipient@example.com --subject "Assunto" --body "Conteúdo" --signature

# Com assinatura de outro endereço send-as
gog gmail send --to recipient@example.com --subject "Assunto" --body-html "<p>HTML</p>" --signature-from outro@email.com

# Com CC/BCC
gog gmail send --to a@b.com --cc c@d.com --bcc e@f.com --subject "Assunto" --body "Texto" --signature

# Com anexos
gog gmail send --to a@b.com --subject "Report" --body "Ver anexo" --attach report.pdf --attach data.csv --signature

# Reply
gog gmail send --to a@b.com --subject "Re: Assunto" --body "Resposta" --reply-to-message-id <messageId> --signature
```

### Buscar Emails

```bash
# Últimos 7 dias
gog gmail search 'newer_than:7d' --max 10

# Com detalhes do corpo
gog gmail search 'subject:importante' --include-body --max 5

# JSON output
gog gmail search 'from:client@example.com' --max 20 --json
```

### Gerenciar Drafts

> Draft salva o conteúdo puro, **sem assinatura**. A assinatura é aplicada pelo `send` na hora do envio.
> Use este fluxo quando o usuário quiser revisar antes de enviar.

```bash
# Criar rascunho (sem assinatura — comportamento correto)
gog gmail drafts create --to a@b.com --subject "Assunto" --body "Conteúdo"

# Enviar rascunho (aplica assinatura automaticamente via Gmail)
gog gmail drafts send <draftId>

# Deletar rascunho
gog gmail drafts delete <draftId>
```

### Outras Operações

```bash
gog gmail archive <messageId> # Arquivar email
gog gmail trash <messageId> # Mover para lixo
gog gmail labels list # Listar labels
```

---

## 📅 Calendar

### Listar Eventos

```bash
# Hoje
gog calendar events primary --from 2026-05-27T00:00:00Z --to 2026-05-27T23:59:59Z

# Intervalo de datas
gog calendar events primary --from 2026-05-01 --to 2026-05-31 --json

# Múltiplos calendários
gog calendar events primary --calendars work,personal --max 50

# Com query
gog calendar events primary --query "meeting" --max 20
```

### Criar Evento

```bash
# Básico
gog calendar create primary --summary "Reunião" --from "2026-05-30T10:00:00" --to "2026-05-30T11:00:00"

# Dia inteiro
gog calendar create primary --summary "Feriado" --from "2026-06-01" --to "2026-06-02" --all-day

# Com detalhes
gog calendar create primary \
 --summary "Apresentação" \
 --from "2026-05-30T14:00:00" \
 --to "2026-05-30T15:00:00" \
 --description "Reunião importante" \
 --location "Sala 101" \
 --attendees "cliente@example.com,colega@company.com"

# Com timezone
gog calendar create primary \
 --summary "Call" \
 --from "2026-05-30T10:00:00" \
 --to "2026-05-30T11:00:00" \
 --start-timezone "America/Sao_Paulo" \
 --end-timezone "America/Sao_Paulo"
```

### Deletar Evento

```bash
gog calendar delete primary <eventId>
```

---

## 📊 Analytics (GA4)

### Listar Contas

```bash
gog analytics accounts --all --json
```

### Rodar Relatório

```bash
# Últimos 30 dias
gog analytics report 528125334 \
 --from 30daysAgo --to today \
 --dimensions date,country \
 --metrics activeUsers,sessions \
 --json

# Com filtros avançados
gog analytics report 528125334 \
 --from 2026-05-01 --to 2026-05-27 \
 --dimensions date,deviceCategory,country \
 --metrics activeUsers,sessions,averageSessionDuration,bounceRate \
 --max 10000 --json

# Por página
gog analytics report 528125334 \
 --from 7daysAgo --to today \
 --dimensions pagePath \
 --metrics users,sessions,bounceRate \
 --json
```

---

## 🔍 Search Console

### Listar Sites

```bash
gog searchconsole sites --json
```

### Rodar Query

```bash
gog searchconsole query sc-domain:example.com \
 --from 2026-05-01 --to 2026-05-27 \
 --dimensions query,page \
 --json

# Com filtro
gog searchconsole query sc-domain:example.com \
 --from 2026-05-01 --to 2026-05-27 \
 --dimensions query \
 --filter "query:contains:produto" \
 --json
```

### Gerenciar Sitemaps

```bash
gog searchconsole sitemaps list sc-domain:example.com --json
gog searchconsole sitemaps submit sc-domain:example.com https://example.com/sitemap.xml --force
gog searchconsole sitemaps delete sc-domain:example.com https://example.com/sitemap.xml --force
```

---

## 💾 Drive

### Listar Arquivos

```bash
gog drive ls # Root
gog drive ls --all --max 50 --json # Todos os arquivos
gog drive ls --parent <folderId> # Dentro de pasta
```

### Buscar Arquivos

```bash
gog drive search "relatorio" --max 10
gog drive search "is:folder" --max 20
gog drive search "mimeType='application/pdf'" --raw-query
```

### Criar/Copiar/Deletar

```bash
gog drive mkdir "Nova Pasta" --parent <parentId>
gog drive copy <fileId> "Cópia do Arquivo"
gog drive delete <fileId> # Lixo
gog drive delete <fileId> --permanent # Deletar permanentemente
```

---

## 📄 Docs

### Ler Conteúdo

```bash
gog docs cat <docId> # Ler no terminal
gog docs cat <docId> --tab "Notas" # Aba específica
```

### Exportar

```bash
gog docs export <docId> --format txt --out /tmp/doc.txt
gog docs export <docId> --format pdf --out /tmp/doc.pdf
gog docs export <docId> --format html --out /tmp/doc.html
```

### Criar/Copiar

```bash
gog docs create "Novo Documento"
gog docs copy <docId> "Cópia do Documento"
```

### Formatar (Pesquisa + Estilo)

```bash
gog docs format <docId> --match "Status" --bold --font-size 18
```

---

## 📊 Sheets

### Ler Dados

```bash
gog sheets get <sheetId> "Sheet1!A1:D10" --json
gog sheets metadata <sheetId> --json # Info da planilha
```

### Atualizar/Adicionar

```bash
# Atualizar valores
gog sheets update <sheetId> "Sheet1!A1:B2" --values-json '[["A","B"],["1","2"]]' --input USER_ENTERED

# Adicionar linhas
gog sheets append <sheetId> "Sheet1!A:C" --values-json '[["x","y","z"]]' --insert INSERT_ROWS

# Limpar intervalo
gog sheets clear <sheetId> "Sheet1!A2:Z"
```

### Tabelas (Formato)

```bash
gog sheets table append <sheetId> Tasks 'Tarefa 1|done'
```

---

## 🎨 Slides

### Criar Apresentação

```bash
gog slides create "Nova Apresentação"
```

### Criar a partir de Markdown

```bash
gog slides create-from-markdown "Apresentação Semanal" --content-file slides.md
```

---

## 💡 Tips & Truques

### Variáveis de Ambiente

```bash
export GOG_ACCOUNT=seu-email@gmail.com # Evitar repetir --account
export GOG_CALENDAR_WEEKDAY=1 # Filtro automático
```

### Scripting

```bash
# Sempre use JSON + --no-input para automation
gog analytics report 528125334 --from 7daysAgo --to today --dimensions date --metrics users --json --no-input | jq .

# Piping com jq
gog gmail search 'is:unread' --max 50 --json | jq '.[] | {from: .headers.from, subject: .headers.subject}'
```

### Confirm Prompts

```bash
# Desativar confirmações para CI/CD
gog gmail send --to a@b.com --subject "Auto" --body "Msg" --no-input
gog calendar create primary --summary "Event" --from "2026-05-30T10:00:00" --to "2026-05-30T11:00:00" --no-input
```

---

## 💬 Reply (manter thread)

Sempre usar flags de thread para manter contexto, e **sempre incluir a assinatura**:

```bash
gog gmail send \
  --reply-to-message-id <message-id> \
  --quote \
  --reply-all \
  --signature \
  --body "Resposta aqui" \
  --no-input --force
```

- `--reply-to-message-id`: vincula ao thread original (In-Reply-To + References)
- `--quote`: inclui o corpo da mensagem anterior como citação
- `--reply-all`: popula automaticamente todos os destinatários (To + Cc)
- `--signature`: adiciona a assinatura Gmail configurada na conta (usar **sempre**)
- `--signature-from=EMAIL`: usa assinatura de outro endereço send-as configurado
- Subject é herdado com prefixo "Re:" automaticamente

---

## 📝 Notas Importantes

- Gmail signatures: `--signature` (padrão) ou `--signature-file` (customizada)
- Dates: RFC3339 ou 7daysAgo, 30daysAgo, today
- JSON output: Sempre use `--json` para parseability
- Docs edits: Apenas cat/export/copy (edição in-place requer Docs API)
- Confirmações: Use `--no-input` para automation/CI
- Rate limits: Considere delays entre chamadas em bulk operations
