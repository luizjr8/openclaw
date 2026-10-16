# USER.md - Seu Humano

- **Nome:** _(preencher)_
- **Chamar de:** _(preencher)_
- **Tratamento:** "você" (não usar "tu")
- **Timezone:** _(preencher)_
- **Perfil:** Desenvolvedor sênior. Comunique em nível de engenharia — sem explicar o básico.
- **Canal principal:** Telegram.

## Contexto Técnico

- Preferências de código e estilo: ver `AGENTS.md` → Disciplina de Código + Convenções.
- Ação destrutiva/externa: confirmar antes. `trash` > `rm`.
- Bases onde você atua: `<aqui os projetos>` — repos, servidores, infra que o humano mantém.

## Memória Compartilhada

Você indexa a memória do main como referência **read-only** (`/app/workspace/MEMORY.md` + `/app/workspace/memory/`, via `agents.json5 → coder.memorySearch.extraPaths`). Dados completos do humano vivem lá (`USER.md` do main). Puxe de lá em vez de duplicar; escreva só na sua própria memória.
