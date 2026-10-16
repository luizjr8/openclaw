---
name: inbound-webhooks
description: Criar, configurar ou gerenciar rotas de webhook inbound (receber leads, eventos de pagamento, callbacks OAuth) via a tool nativa webhook_manage.
---

# Inbound Webhooks Setup

Use esta skill quando o usuário pedir para criar, configurar ou gerenciar uma nova rota de webhook (ex: receber leads, eventos de pagamentos, callbacks OAuth, etc).

## A Ferramenta Nativa `webhook_manage`

A extensão `webhook-inbound` expõe uma **ferramenta nativa (tool)** chamada `webhook_manage` diretamente na engine do OpenClaw. **Sempre utilize essa tool para gerenciar rotas**, não use scripts avulsos.

### Como usar a tool `webhook_manage`:

A tool aceita os seguintes parâmetros:

- `action`: "add", "list" ou "remove"
- `id`: O nome/sufixo dinâmico da rota (ex: `gog-oauth-callback`, `lead-zap`)
- `config`: Objeto com a configuração da rota (apenas para `add`):
  - `sessionKey`: Identificador de onde a notificação/retorno vai chegar (ex: `agent:main:telegram:default:direct:1811751`). Pode pegar via `session_status` ou `sessions_list`.
  - `prompt`: Instruções de como o sub-agente deve tratar o `<payload>`.
  - `deliveryMode`: Modo de entrega ("sync", "channel", "silent"). Default é "sync".
  - `runOnce`: Booleano (opcional). Se `true`, a engine injetará uma instrução automática no seu prompt exigindo que **você exclua a rota** usando `webhook_manage` (ação `remove`) somente após finalizar o processamento com sucesso.
  - `secret`: String (opcional). Token de segurança para exigir validação na URL.
  - `responseText`: Texto simples (opcional) a ser retornado imediatamente na resposta HTTP (ex: "OK").
  - `responseHtml`: HTML (opcional) a ser retornado imediatamente na resposta HTTP (ex: uma página de sucesso para o navegador).

#### Modos de Entrega (`deliveryMode`)

- `"sync"`: (Padrão). O sub-agente processa e o texto da resposta dele é entregue de volta na thread automaticamente. O gateway se encarrega do envio.
- `"channel"`: Exige `channelTarget` (e opcionalmente `channelThreadId`). Força o sub-agente a responder em um canal específico usando a tool `message`.
- `"silent"`: O sub-agente executa o script (ex: escreve num arquivo ou banco de dados) e morre sem enviar nenhuma mensagem ao usuário.

### Exemplo de uso para criar uma rota:

\`\`\`json
{
"action": "add",
"id": "minha-nova-rota",
"config": {
"sessionKey": "agent:main:telegram:default:direct:1811751",
"deliveryMode": "sync",
"prompt": "Analise o payload recebido e faça um resumo.",
"responseHtml": "<h1>Tudo certo!</h1>"
}
}
\`\`\`

A ferramenta retornará uma URL pública formatada (ex: `https://<public_url>/in/minha-nova-rota`).

Se retornar que a `WEBHOOK_PUBLIC_URL` não está configurada, avise o usuário que as integrações externas (como Google OAuth) não vão funcionar e oriente-o a configurar a env!

## Gotchas operacionais

- **`sessionKey` é obrigatório no `add`.** A tool não expõe a sessão ativa pro contrato de execução — se você omitir, a rota nasce sem destino de entrega. Pegue o valor via `session_status` ou `sessions_list` e passe explícito no `config`.
- **Alterar o código TS da extensão exige restart REAL do processo** (`kill -TERM 1` no container, ou `docker restart`). O `gateway restart` (SIGUSR1) só faz reload soft de config/sessões: módulos TS já importados ficam cacheados no registry ESM e não são re-importados. Config hot-reloada; código de extensão não.
