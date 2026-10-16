# MEMÓRIA DE LONGO PRAZO

_Arquivo de memória persistente do agente. Preenchido automaticamente pelo sistema de memória e por interações._

## Agentes

_(preencher com agentes customizados e suas configurações)_

## Configurações do Gateway

### Subagents

- `agents.defaults.subagents.allowAgents: ["main", "coder"]`
- `tools.sessions.visibility: "all"` — visibilidade entre agentes
- `tools.agentToAgent.enabled: true` — comunicação cross-agent

### Hot Reload

- Gateway configurado com `gateway.reload.mode: "hybrid"` — tenta hot reload, cai pra restart quando necessário
- Alterações no `openclaw.json` são aplicadas sem restart; **código de extensão TS não** — exige restart real do processo

## Skills

_(documentar skills instaladas, comandos e formatos de saída)_

## Usuário

- Nome: _(preencher)_
- Canal principal: _(preencher)_
- Timezone: America/Recife
- Preferências: _(preencher)_

## Atualizações Pendentes

_(registrar atualizações e tarefas pendentes)_
