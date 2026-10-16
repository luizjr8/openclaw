# Inbound Webhook Plugin

Este plugin sobe um servidor HTTP interno durante o ciclo de vida do OpenClaw para receber webhooks crus de serviços externos (como Facebook, Stripe, etc).

## Configuração Global (openclaw.json)

As configurações de porta, segurança padrão e estado de ativação são lidas automaticamente do `openclaw.json` (geridas nativamente pelo Gateway):

```json
"plugins": {
  "entries": {
    "inbound-webhook-plugin": {
      "enabled": true,
      "config": {
        "port": 16889,
        "prefix": "/in/",
        "secret": "TOKEN_GLOBAL_OPCIONAL"
      }
    }
  }
}
```

## Como funciona

1. O serviço externo envia um POST para `http://<seu-ip>:<PORTA><PREFIXO><ID-DINAMICO>` (Ex: `16889/in/xyz`).
2. O payload é validado e salvo em `/app/workspace/memory/webhook-<ID-DINAMICO>.jsonl`.
3. O plugin lê o arquivo `/app/workspace/webhook-routes.json` para saber o que fazer com aquele ID.
4. Um sub-agente é invocado em background rodando o prompt configurado e injetando o payload isolado.

## Configuração de Rotas Dinâmicas (webhook-routes.json)

Enquanto a porta e a infra sobem via `openclaw.json`, você pode adicionar rotas "a quente" criando e editando o arquivo `/app/workspace/webhook-routes.json`. Ele é lido em tempo real sem precisar reiniciar o plugin.

Exemplo:

```json
{
  "facebook-meta-ser-webhook": {
    "secret": "meu-token-especifico-da-rota",
    "agent": "main",
    "prompt": "Seu prompt customizado de como processar o payload..."
  }
}
```

> **Nota de Segurança:** O `secret` na rota sobrepõe o `secret` configurado no `openclaw.json`. Se ambos existirem, o do `webhook-routes.json` tem prioridade.
