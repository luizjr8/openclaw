---
name: habitica
description: Habitica gamified habit tracker integration. Use for listing/creating/completing habits, dailies, todos, and rewards. Trigger on "habitica", "习惯", "待办", "日常任务", or requests to check off tasks.
---

# Habitica Skill

Full-featured CLI for Habitica's gamified task manager.

## Setup

Credentials in `~/.habitica`:

```bash
HABITICA_USER_ID="your-user-id"
HABITICA_API_TOKEN="your-api-token"
```

Get from: Habitica → Settings → Site Data → Show API Token

## Commands

### Tasks

```bash
./scripts/habitica.sh list [habits|dailys|todos|rewards|all]
./scripts/habitica.sh create <type> "text" ["notes"]
./scripts/habitica.sh score <task-id> [up|down] ["task text"]

**Score output format:**
```

⏫ Tarefa ✅

⭐ XP: antes → depois (+ganho)
💰 Gold: antes → depois (+ganho)
❤️ HP: antes → depois (+/-ganho)
📍 Lv N | MP: valor

```
The script fetches stats before and after score to show exact gains.
./scripts/habitica.sh update <task-id> --text "new" --notes "new"
./scripts/habitica.sh delete <task-id>
```

### User & Stats

```bash
./scripts/habitica.sh user          # Basic stats
./scripts/habitica.sh stats         # Full stats (STR/INT/CON/PER)
```

### Collections

```bash
./scripts/habitica.sh pets          # Your pets
./scripts/habitica.sh mounts        # Your mounts
./scripts/habitica.sh achievements  # Achievement list
./scripts/habitica.sh inventory     # Eggs, potions, food, quest scrolls
```

### Party & Social

```bash
./scripts/habitica.sh party         # Party info + chat
./scripts/habitica.sh party-chat 10 # Last N messages
./scripts/habitica.sh party-send "message"
./scripts/habitica.sh guilds        # Guild list
```

### Skills (Class Abilities)

```bash
./scripts/habitica.sh skills        # List available skills
./scripts/habitica.sh cast <skill> [taskId]
```

**Rogue:** pickPocket, backStab, toolsOfTrade, stealth
**Warrior:** smash, defensiveStance, valorousPresence, intimidate
**Mage:** fireball, mpheal, earth, frost
**Healer:** heal, healAll, protectAura, brightness

### Quest

```bash
./scripts/habitica.sh quest         # Current quest status
./scripts/habitica.sh quest-accept  # Check and accept pending quest invitations
```

### Other

```bash
./scripts/habitica.sh history [exp|todos]
./scripts/habitica.sh cron          # Force new day
```

## Checklist (subtarefas de um todo)

```bash
./scripts/habitica.sh checklist-add <task-id> "texto do item"
./scripts/habitica.sh checklist-set <task-id> <item-id> "novo texto"   # edita in-place
./scripts/habitica.sh checklist-del <task-id> <item-id>
```

Para popular vários itens, **edite in-place quando possível** (`checklist-set`) em vez de deletar+recriar: menos chamadas e não perde os checks. IDs dos itens saem do `GET /tasks/<id>` (campo `checklist[].id`).

## Rate limit (IMPORTANTE — corrigido)

A API do Habitica permite **30 requisições por minuto por usuário** (não "30s entre chamadas" — isso estava errado). Cada resposta traz headers:

- `X-RateLimit-Limit: 30`
- `X-RateLimit-Remaining` — quantas restam na janela
- `X-RateLimit-Reset` — quando a janela zera
- `Retry-After` — segundos a esperar (aparece só no `429 Too Many Requests`)

Regras:

1. Batch de escritas (ex: N itens de checklist) → throttle de ~0.8–1s entre chamadas. `api_request` já dá retry automático no 429 respeitando `Retry-After`.
2. Nunca faça retry imediato após 429.
3. Operações compostas (criar todo + popular checklist): se o batch falhar no meio, o estado fica **parcial** — cheque com `GET` antes de recriar pra não duplicar (foi o que gerou uma to-do vazia duplicada em 14/06).

## Notes

- Dailies use `dailys` (Habitica's spelling)
- Task IDs are UUIDs from `list` output

## Background Execution (Sub-agents)

For batch operations (e.g., scoring multiple tasks) or slow operations, spawn a sub-agent to keep the main chat responsive.

**Prompt Pattern:**

```text
Task: Habitica Batch Operation
- Score task 123 (up)
- Score task 456 (up)
- Create todo "New Task"
Report back briefly when done.
```

**When to use:**

- User asks to complete >1 task at once
- User asks for a summary/analysis that requires multiple API calls (e.g., "check all my tasks and tell me what to do")
- Network latency is high
