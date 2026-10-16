---
name: housekeeping
description: Limpeza periódica de lixo acumulado (vetores órfãos de índice, caches de browser/npm/pip, scratch de /tmp, backups vencidos) restrita a alvos declarados num manifesto por instância. Use para "quanto disco dá pra recuperar?", "limpar cache", ou o ciclo periódico de faxina. Dry-run é o default; remover exige --apply.
metadata:
  openclaw:
    emoji: "🧹"
    requires:
      bins: ["python3"]
      python: ["pyyaml"]
---

# housekeeping

`du -sh` sabe o que é grande. Não sabe que `~/.cache/pip/http-v2` é tarball
re-baixável e `~/.cache/pip/wheels` ao lado é build compilado localmente —
mesmo diretório-pai, um é lixo e o outro custa hora de CPU. Quem sabe a
diferença é o manifesto.

> ⚠️ **`--dry-run` é o default.** `scan`, e `clean` sem `--apply`, só relatam.
> Nada é removido sem `--apply` explícito. Limpeza errada aqui custa horas de
> re-embed em CPU-only.

Irmã da `deps-update`: mesmo formato de manifesto por instância, mesma saída
legível, mesma regra de "detectar é automático, mexer é humano".

## Caminhos

- Script: `scripts/housekeeping.py`
- Schema do manifesto: `manifest.example.yml`
- **Dados da instância** (fora do repo): `$LZBOT_WORKSPACE_ROOT/skills/housekeeping/`
  - `manifest.yml` — os alvos que esta instância de fato tem
  - `state.json` — último scan/apply + histórico curto do que foi liberado

Sobrescreva o diretório de dados com `HOUSEKEEPING_DATA_DIR`.

## Uso

```bash
python3 scripts/housekeeping.py <comando> [opções]
```

| Comando                      | O que faz                                                      |
| ---------------------------- | -------------------------------------------------------------- |
| `scan [--target X] [--json]` | dry-run: o que seria removido, por alvo, com total recuperável |
| `clean [--target X] --apply` | executa; sem `--apply` é idêntico ao `scan`                    |

`scan` é o único comando que o cron chama. `clean --apply` é humano no gatilho.

## Allowlist, não denylist

A skill **não varre disco** procurando o que parece lixo. Alvo não declarado no
manifesto não é candidato. Isso é o que impede que um diretório novo — que
alguém acabou de criar com dado importante dentro — apareça como "50 MB de
provável lixo" na semana seguinte.

## Guarda dura de caminhos protegidos

`PROTECTED`, no topo do script, não é documentação: é execução. Candidato que
resolva **para dentro** ou **para cima** de um caminho protegido é reportado
como `BLOQ` e não é removido — declarar no manifesto não fura a guarda.

| Protegido                                              | Por quê                                                                  |
| ------------------------------------------------------ | ------------------------------------------------------------------------ |
| `/app/models`                                          | GGUF do embedding local; apagar derruba a memória e o re-download é caro |
| `/app/workspace/{memory,secrets,skills}`, `MEMORY.md`  | dado de usuário                                                          |
| `/app/dev`                                             | repositórios git                                                         |
| `/app/.ssh`, `/app/agents.json5`, `/app/openclaw.json` | credencial e config                                                      |
| `/`, `/app`, `/tmp`, `/home/lzbot`, `/app/workspace`   | raiz nunca é candidato                                                   |

Três detalhes do desenho que não são óbvios:

- **"Para cima" também bloqueia.** Declarar `/app/workspace` levaria `memory/`
  junto, então é recusado. Só a folha certa passa.
- **Symlink é removido como link, nunca como alvo.** A guarda resolve o
  diretório-pai e mantém o último componente literal; resolver o fim faria a
  guarda avaliar o destino e a remoção apagar outra coisa.
- **Uma única exceção, e estreita:** `/app/workspace/skills/*/backups/*/*` —
  o snapshot individual, para o alvo de retenção funcionar. Nem o `backups/`,
  nem o `backups/<alvo>/` inteiro. Glob mal escrito bate na guarda.

Candidato de outro dono (uid diferente) sai como `skip`, não como falha: `rm`
ali falharia de qualquer forma e não é problema desta skill resolver.

## Kinds de alvo

| kind        | semântica                                                                 |
| ----------- | ------------------------------------------------------------------------- |
| `paths`     | todo match do glob é lixo. Use quando o caminho já é o veredito           |
| `age`       | match mais velho que `older_than_days` (default 7)                        |
| `retention` | agrupa por diretório-pai e mantém os `keep` mais recentes (default 3)     |
| `command`   | o dono da limpeza é comando externo (`apply`), tamanho vem de `size_path` |

Armadilha que os kinds resolvem:

- **`age` usa o mtime mais recente da árvore**, não o do diretório. mtime de
  diretório só muda quando entra/sai entrada — um dir com data de duas semanas
  atrás pode ter arquivo escrito hoje dentro. Usar o mais recente erra para o
  lado de manter.

## Manifesto

```yaml
- name: tmp-stale
  kind: age
  older_than_days: 7
  paths: ["/tmp/*"]
  exclude: ["/tmp/openclaw*", "/tmp/claude-*", "/tmp/.X*"]

- name: backups-retention
  kind: retention
  keep: 3
  paths: ["/app/backups/*", "/app/workspace/skills/*/backups/*/*"]
```

`enabled: false` desliga um alvo sem apagar a entrada — útil para alvo que só
faz sentido em outro host. Schema completo e comentado: `manifest.example.yml`.

## Limites conhecidos

- **Não roda `docker prune` neste container.** O binário `docker` existe mas o
  socket não está montado; a entrada fica comentada no `manifest.example.yml`
  como referência para bot que rode no host. Em host com Swarm, `system prune`
  também mexe em imagem de serviço.
- `retention` ordena por mtime, não pelo timestamp do nome. Os dois concordam
  nos backups de hoje; `touch` num snapshot antigo o promoveria a recente.
- `command` mede o liberado por diferença de tamanho antes/depois. Se outro
  processo escrever no cache no meio, o número sai errado — o `apply` do
  comando, não.
- `scan` **não é atômico** com o `clean`: a guarda roda de novo item por item no
  apply, mas um caminho que apareceu entre os dois momentos não entra.
- Não toca em `~/.cache/{huggingface,torch,Cypress}` (~2,8 GB aqui). São modelos
  e binários de teste em uso; a decisão é caso a caso, não cron.
