---
name: deps-update
description: Detecta e aplica atualizações de dependências (npm global, pip, apt, binários) declaradas em um manifesto por instância. Use para "tem update pendente?", "o que mudou entre as versões X e Y", "atualizar o pacote X", ou o ciclo periódico de checagem. Detecção é automática; aplicação exige versão explícita e backup do estado mutável.
metadata:
  openclaw:
    emoji: "📦"
    requires:
      bins: ["python3", "npm", "pip3"]
      python: ["pyyaml"]
---

# deps-update

`npm outdated` diz "2.5.3 → 2.8.3". Não diz se a versão nova fecha um CVE, se ela
migra estado no disco sem perguntar, nem quanto custa reconstruir esse estado se
der errado. É exatamente essa parte que decide se o update espera a cadência ou
entra hoje — e é o manifesto quem sabe dela.

> ⚠️ **Nunca aplica update sozinha.** Detecção é automática, aplicação é sob
> aprovação humana. `apply` exige `--to` explícito (não existe "latest"
> implícito) e recusa rodar sem backup bem-sucedido do estado mutável.

## Caminhos

- Script: `scripts/deps_update.py`
- Schema do manifesto: `manifest.example.yml`
- **Dados da instância** (fora do repo): `$LZBOT_WORKSPACE_ROOT/skills/deps-update/`
  - `manifest.yml` — o que a instância de fato depende
  - `state.json` — último check + histórico curto de backup/apply/rollback
  - `backups/<name>/<YYYYmmdd-HHMMSS>/` — snapshot do estado + `backup.json`

Sobrescreva o diretório de dados com `DEPS_UPDATE_DATA_DIR`.

## Uso

```bash
python3 scripts/deps_update.py <comando> [opções]
```

| Comando                                | O que faz                                                      |
| -------------------------------------- | -------------------------------------------------------------- |
| `check [--json]`                       | instalado vs disponível; separa manifesto de fora-do-manifesto |
| `changelog <name> [--from V] [--to V]` | extrai o range e destaca Segurança / Breaking / Migração       |
| `backup <name>`                        | snapshot dos globs de `state:` com timestamp                   |
| `apply <name> --to <versão>`           | backup → install → verify; rollback se o verify falhar         |
| `rollback <name> [--backup <id>]`      | volta versão **e** estado (padrão: backup mais recente)        |
| `verify <name>`                        | só o comando de `verify` do manifesto                          |

`check` é o único comando que o cron chama. Todo o resto é humano no gatilho.

## Manifesto

Uma entrada por dependência. Só `name` e `kind` são obrigatórios.

```yaml
- name: playwright
  kind: npm-global # npm-global | pip | apt | binary
  package: playwright # nome real no gerenciador (default: name)
  state: # globs de estado mutável, salvos antes do apply
    - "~/.cache/ms-playwright"
  changelog: # `path:` quando o pacote shipa CHANGELOG; senão, atom
    atom: "https://github.com/microsoft/playwright/releases.atom"
  verify: "playwright --version"
  critical: true # entra na janela de segurança fora de cadência
  notes: "subir a lib sem baixar o browser da versão nova deixa o Chromium sem par"
```

- **`state:`** é o campo que justifica a skill. É o que transforma "update de
  patch" em "operação que mexe em dado irreversível". Sem `state:`, o `backup`
  é um no-op válido e o `apply` segue.
  Arquivo é copiado; **diretório é só registrado** no `backup.json`. Cache de
  browser tem GB e se "restaura" por re-download — mas precisa aparecer no
  `check` para o apply não passar como se não mexesse em nada.
- **`changelog:`** aceita string (atalho para `path`) ou mapa com duas fontes:
  - `path` — para `npm-global` é relativo ao prefix global do npm (aceita
    absoluto). O comando **baixa o tarball da versão-alvo** para ler as entradas
    novas: o arquivo instalado só conhece o passado, que é justamente o que não
    interessa.
  - `atom` — feed `https://github.com/<org>/<repo>/releases.atom`, para pacote
    que não shipa CHANGELOG (o `playwright` não tem nenhum). É **fallback
    automático**: sem `path`, ou com `path` que não existe, o atom é tentado. Se
    `atom` não estiver escrito, a URL é derivada do `repository` do registry.
  - **Nunca `api.github.com`** — devolveu 504 na primeira varredura real; o atom
    respondeu normal. O feed também não pede token.
- **`status: orphan-candidate`** — o veredito é _remover_, não atualizar: alguém
  bundla a própria cópia e a global não serve ninguém. Sai num bloco separado do
  `check` em vez de voltar toda semana como falso pendente, e o `apply` recusa
  rodar até o campo sair da entrada. Omita no caso normal.
- **`verify:`** roda em `sh -c`. `rc != 0` depois de um `apply` dispara rollback
  automático.
- **`notes:`** aparece no rodapé do `check` para todo item com update pendente ou
  marcado como órfão.

## Como cada kind é resolvido

| kind         | instalado                            | disponível                     | install                                                              |
| ------------ | ------------------------------------ | ------------------------------ | -------------------------------------------------------------------- |
| `npm-global` | `npm ls -g --depth=0`                | `npm outdated -g`              | `sudo -n env PATH=$PATH npm i -g pkg@v`                              |
| `pip`        | `pip3 list`                          | `pip3 list --outdated`         | `sudo -n env PATH=$PATH pip3 install --break-system-packages pkg==v` |
| `apt`        | `dpkg-query`                         | `apt-cache policy` (Candidate) | `sudo -n apt-get install -y pkg=v`                                   |
| `binary`     | regex de versão na saída do `verify` | — (status `manual`)            | não tem; aplique à mão                                               |

Dois detalhes de ambiente que não são óbvios:

- O `node_modules` global é do **root**; `npm i -g` sem `sudo` falha. O usuário
  `lzbot` tem sudo sem senha.
- `pip3 install` **sem sudo** cai em "user installation" (`~/.local`), que mora
  no overlay do container e **evapora quando o container é recriado** — o
  upgrade sumiria calado no próximo boot. Por isso pip também vai por sudo.

## Status no `check`

| status    | significado                                                        |
| --------- | ------------------------------------------------------------------ |
| `update`  | tem versão maior disponível                                        |
| `ok`      | em dia                                                             |
| `ausente` | declarado no manifesto e **não instalado** (drift real, não ruído) |
| `manual`  | sem registry para consultar (`binary`)                             |
| `orphan`  | `status: orphan-candidate` no manifesto — remover, não atualizar   |

Itens instalados que **não** estão no manifesto vão num bloco separado e
resumido — hoje são ~16 transitivos de pip, que não devem poluir o quadro.

## Fluxo recomendado para um update

```bash
python3 scripts/deps_update.py check                       # 1. o que mudou
python3 scripts/deps_update.py changelog playwright --to 1.61.0   # 2. o que a versão faz
# 3. decisão humana aqui — inclusive sobre o custo da migração de estado
python3 scripts/deps_update.py apply playwright --to 1.61.0       # 4. backup+install+verify
python3 scripts/deps_update.py rollback playwright                # 5. se algo cheirar mal
```

O `rollback` reinstala a versão anterior (gravada no `backup.json`) **e**
restaura os arquivos de estado. Bancos `.sqlite`/`.db` são copiados pela API de
backup do sqlite, não por `cp` — cópia crua de banco em uso sai torta.

## Limites conhecidos

- `changelog` só busca o tarball e só deriva a URL do atom para `npm-global`.
  Para `pip`/`apt` lê o `path` local, ou o `atom` se estiver escrito à mão.
- O atom traz **release notes**, que não são changelog: pode faltar o `###
Security` que o highlight usa para classificar. Ausência de destaque num item
  vindo de atom é "não sabemos", não "não tem".
- Diretório em `state:` não é copiado no backup, só registrado. Rollback de item
  assim não devolve o diretório — o `apply` fica com a limpeza pendente.
- Migração de estado é **irreversível pelo pacote**: o `rollback` devolve o
  arquivo salvo, não desfaz a migração in-place. É por isso que o backup é
  obrigatório e não opcional.
- `apply` não reinicia serviço nenhum. Restart de gateway/container é decisão e
  ação separada.
