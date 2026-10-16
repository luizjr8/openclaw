# Regras de Envio de Email

> Template canônico do boilerplate. Troque `<seu-email>` pelo endereço da conta
> ativa (ver `USER.md` / `secrets/auth-profiles.yml`). Não hardcodar email pessoal aqui.

## Antes de Enviar

1. **Sempre mostrar o texto final do email antes de enviar**
   - Só enviar quando o usuário disser "aprovado, envie" ou equivalente
2. **Respostas:** incluir corpo das mensagens anteriores inline (histórico completo citado no corpo)
   - Não confiar só na thread do Gmail para contexto
   - Usar `--quote` + `--reply-all` com `--reply-to-message-id`

## Subject

3. **Acentos e caracteres especiais no Subject:**
   - O `gog` NÃO codifica UTF-8 no Subject corretamente
   - Usar `email.header.Header("Assunto com çáéí", "utf-8")` do Python
   - Body aceita UTF-8 normal (sem necessidade de codificação especial)

## Assinatura

4. **Sempre incluir a assinatura do Gmail** configurada na conta ativa (`<seu-email>`)
   - Obter via API: `GET /gmail/v1/users/me/settings/sendAs/<seu-email>`
   - A assinatura costuma ser HTML (avatar, nome, email, links)
   - Emails com assinatura HTML devem usar `multipart/alternative` com parte `text/plain` + `text/html`

## Threading

5. **Respostas devem manter o thread:**
   - `In-Reply-To` = Message-ID da mensagem original
   - `References` = Message-ID da mensagem original
   - `threadId` = Gmail thread ID da conversa

## API vs gog

6. Para criar rascunhos ou operações mais complexas, usar **API REST do Gmail** diretamente (não via gog)
   - gog não suporta criação de drafts nativamente
   - Fluxo: refresh token → access token → chamada REST

## Tons

7. **Email em pt-BR:** usar "Atenciosamente," no fechamento
8. **Email em inglês:** usar "Best," ou "Best regards," no fechamento
9. **Sempre usar "você"** (nunca "tu") em português

## Template de código (draft via API)

```python
import json, base64, httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formatdate

# Tokens
with open("/tmp/gog_tokens.json") as f:
    tokens = json.load(f)
with open("/app/.config/gogcli/credentials.json") as f:
    creds = json.load(f)

# Access token
r = httpx.post("https://oauth2.googleapis.com/token", data={
    "client_id": creds["client_id"],
    "client_secret": creds["client_secret"],
    "refresh_token": tokens["refresh_token"],
    "grant_type": "refresh_token",
})
access_token = r.json()["access_token"]
headers_api = {"Authorization": f"Bearer {access_token}"}

# Build message
msg = MIMEMultipart("alternative")
msg["From"] = "Seu Nome <<seu-email>>"
msg["To"] = "..."
msg["Subject"] = Header("...", "utf-8")
msg["In-Reply-To"] = "<original-message-id>"
msg["References"] = "<original-message-id>"
msg["Date"] = formatdate(localtime=True)

msg.attach(MIMEText(plain_body, "plain", "utf-8"))
msg.attach(MIMEText(html_body, "html", "utf-8"))

raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

# Create draft
httpx.post("https://gmail.googleapis.com/gmail/v1/users/me/drafts",
    headers=headers_api,
    json={"message": {"raw": raw, "threadId": "..."}})
```
