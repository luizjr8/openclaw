#!/bin/bash
# lzbot-plugin-sync.sh — põe os plugins declarados no config na versão do core.
#
# Idempotente e NÃO-FATAL: ClawHub/npm fora do ar não pode derrubar o bot.
#
#### Dois chamadores, de propósito
#
# 1. entrypoint, todo boot — é o que faz o bump de versão da imagem chegar no
#    volume persistente.
# 2. migration de config (0006), uma vez, ANTES de reconciliar o openclaw.json —
#    porque parte do schema de config pertence ao PLUGIN, não ao core, e a tabela
#    de migração legada do produto consulta o plugin INSTALADO para saber o que
#    aposentou. Com plugin velho a migration termina "válida" e o gateway morre
#    depois, quando o plugin novo entra:
#
#      channels.whatsapp: invalid config for plugin whatsapp:
#        must not have additional properties: "blockStreaming", "ackReaction"
#
# A segunda chamada (entrypoint) vira no-op: o probe de versão abaixo enxerga a
# generation nova e imprime "ok".
#
#### Ordem: isto NÃO pode rodar antes da migration de schema do state DB
#
# `plugins install` sobe o CLI inteiro, e o CLI abre o state DB. Com o volume
# vindo de 2026.7.1 o schema ainda é o antigo e o open é fail-closed:
#
#   OpenClaw startup migrations did not complete cleanly ...
#   column definitions differ for current_conversation_bindings; ... subagent_runs
#
# Daí a ordem das migrations em 2026.9.2: 0005 (schema do state DB) -> plugins ->
# 0006 (config). Foi o que custou duas rodadas de ensaio para descobrir.
set -uo pipefail

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Plugin do OpenClaw mora no state dir do VOLUME (/app/npm/projects/...), NUNCA
# assado na imagem. Racional completo no Dockerfile; o resumo é: o registry
# gerenciado do /app/npm ganha de qualquer cópia em /app/extensions (plugin
# assado vira peso morto + drift silencioso), e `plugins install clawhub:<pkg>`
# grava em /app/extensions — que numa instância com checkout é symlink pro repo,
# e de lá o plugin sobe UNTRUSTED (`openKeyedStore is only available for trusted
# plugins`). A forma npm-spec (SEM o prefixo `clawhub:`) instala no /app/npm.
#
# A lista vem dos catálogos oficiais externos que o core empacota. O script
# cruza plugin IDs, canais e model providers declarados no openclaw.json com
# esses catálogos e instala apenas os npmSpecs oficiais correspondentes. A
# versão é a do package.json do core, normalizada para remover o sufixo de
# correção do host (por exemplo, `2026.9.2-1` -> `2026.9.2`). Assim não há uma
# segunda lista de plugins nem uma segunda variável de versão para manter.
# Não-fatal de propósito: ClawHub/npm fora do ar não pode derrubar o bot — o
# gateway sobe sem o plugin e loga. Idempotente: só age quando a versão diverge.
#
#### Por que ANTES das migrations (e não depois, como era até a 2026.9.2)
#
# Porque parte do schema de config é de PROPRIEDADE DO PLUGIN, não do core: as
# chaves de `channels.whatsapp` são validadas pelo schema que o plugin whatsapp
# publica. E a tabela de migração legada que a 0005 usa também consulta o plugin
# instalado para saber o que aposentou.
#
# Com plugin velho + config velho, a 0005 termina "válida" e o gateway morre
# depois, quando o plugin novo entra:
#
#   channels.whatsapp: invalid config for plugin whatsapp:
#     must not have additional properties: "blockStreaming", "ackReaction"
#
# Não dá para inverter só no papel: `plugins install` carrega o config no start,
# então instalar o plugin NOVO com o config VELHO falha pelo mesmo erro — e falha
# inteiro, sem deixar nada instalado (medido: a generation nova é descartada e o
# volume continua na versão antiga). Deadlock nos dois sentidos.
#
# A saída é instalar apontando `OPENCLAW_CONFIG_PATH` para um config VAZIO (`{}`),
# num tmpdir: o install não precisa de nada do config da instância, e sem canal
# declarado nenhum schema de plugin de canal se aplica. O config real fica
# intocado. Logo em seguida a 0005 roda já com o plugin novo no lugar, e a tabela
# do vendor move as chaves para o shape que aquele plugin aceita.
_plugins_state_dir="${OPENCLAW_STATE_DIR:-/app}"
_plugins_config_path="${OPENCLAW_CONFIG_PATH:-/app/openclaw.json}"

# Devolve a MAIOR versão instalada, não a primeira encontrada.
# `plugins install --force` não substitui o diretório antigo: escreve um
# `<projeto>__openclaw-generation__<hash>` ao lado e passa o registry para ele. O
# probe antigo varria em ordem de glob, achava o diretório VELHO primeiro e
# reportava a versão velha para sempre — resultado: o pin nunca batia, o
# "plugin: X ok" nunca acontecia e todo boot reinstalava tudo do zero.
_plugin_installed_version() {
  local pkg="$1" f best=""
  for f in "$_plugins_state_dir"/npm/projects/*/node_modules/"$pkg"/package.json; do
    [ -f "$f" ] || continue
    local v
    v="$(node -p "require('$f').version" 2>/dev/null)" || continue
    [ -n "$v" ] || continue
    if [ -z "$best" ] || [ "$(printf '%s\n%s\n' "$best" "$v" | sort -V | tail -1)" = "$v" ]; then
      best="$v"
    fi
  done
  [ -n "$best" ] && printf '%s\n' "$best"
  return 0
}

# Resolve os plugins oficiais exigidos pela configuração a partir da fonte que
# o OpenClaw usa para descobrir plugins externos. Não chama a CLI: precisa
# funcionar antes de instalar os schemas que tornarão o config antigo válido.
_required_plugin_specs() {
  local cli package_root
  cli="$(command -v openclaw 2>/dev/null)" || {
    echo "[entrypoint] AVISO: openclaw não encontrado; não foi possível resolver plugins" >&2
    return 1
  }
  package_root="$(dirname "$(readlink -f "$cli")")"
  node - "$_plugins_config_path" "$package_root" <<'NODE'
const fs = require("fs");
const path = require("path");
const [configPath, packageRoot] = process.argv.slice(2);
const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
const core = JSON.parse(fs.readFileSync(path.join(packageRoot, "package.json"), "utf8"));
const version = core.version.replace(/-\d+$/, "");
const catalogFiles = [
  "official-external-plugin-catalog.json",
  "official-external-channel-catalog.json",
  "official-external-provider-catalog.json",
];
// `enabled: false` é desativação explícita: a chave existe só pra documentar a
// escolha. Instalar assim mesmo baixa e ocupa disco com plugin que o gateway
// nunca carrega (ex.: cua-computer, ~centenas de MB). Ausência de `enabled`
// continua valendo como ligado (default do core).
const enabledKeys = (record) =>
  Object.entries(record ?? {})
    .filter(([, value]) => !(value && typeof value === "object" && value.enabled === false))
    .map(([key]) => key);
const wanted = new Set([
  ...enabledKeys(config.plugins?.entries),
  ...enabledKeys(config.channels),
  ...enabledKeys(config.models?.providers),
]);
const specs = new Set();
for (const file of catalogFiles) {
  const catalog = JSON.parse(fs.readFileSync(path.join(packageRoot, "scripts", "lib", file), "utf8"));
  for (const entry of catalog.entries ?? []) {
    if (entry.source !== "official") continue;
    const openclaw = entry.openclaw ?? {};
    const ids = [
      openclaw.plugin?.id,
      openclaw.channel?.id,
      ...(openclaw.providers ?? []).map((provider) => provider.id),
    ].filter(Boolean);
    const npmSpec = openclaw.install?.npmSpec;
    if (!ids.some((id) => wanted.has(id)) || !/^@openclaw\/[^@]+$/.test(npmSpec ?? "")) continue;
    specs.add(`${npmSpec}@${version}`);
  }
}
if (wanted.size && specs.size === 0) {
  throw new Error("nenhum plugin oficial configurado foi encontrado nos catálogos do core");
}
process.stdout.write([...specs].sort().join("\n") + (specs.size ? "\n" : ""));
NODE
}

# O installer materializa TODOS os `requiredPlatformPackages` do plugin — no
# codex são as 6 plataformas do @openai/codex, 2.0 GB no volume, num container
# que roda uma só. Só o par desta máquina é usado; o resto é lastro (350 MB
# depois da poda). Reaplicado a cada install porque `plugins install/update`
# repõe tudo.
_prune_codex_platform_packages() {
  local keep d
  case "$(uname -m)" in
    x86_64|amd64) keep="codex-linux-x64" ;;
    aarch64|arm64) keep="codex-linux-arm64" ;;
    *) return 0 ;;
  esac
  for d in "$_plugins_state_dir"/npm/projects/*/node_modules/@openclaw/codex/node_modules/@openai/codex-*; do
    [ -d "$d" ] || continue
    [ "$(basename "$d")" = "$keep" ] && continue
    rm -rf "$d"
  done
}

# Config VAZIO só para o install. Não é "config sem channels": neste ponto o
# config da instância ainda é o antigo INTEIRO — a 0005 só roda depois — e o CLI
# recusa carregar qualquer config inválido, não só o pedaço dos canais. `{}` é
# válido, e o install não precisa de nada do config: o que ele usa (state dir,
# registry do /app/npm) vem de env.
_plugins_safe_dir="$(mktemp -d)"
printf '{}\n' > "$_plugins_safe_dir/openclaw.json"
_plugins_safe_config="$_plugins_safe_dir/openclaw.json"

_plugins_specs_file="$_plugins_safe_dir/plugins.txt"
if _required_plugin_specs >"$_plugins_specs_file"; then
  while IFS= read -r _spec || [ -n "$_spec" ]; do
    _spec="${_spec%%#*}"
    _spec="$(echo "$_spec" | tr -d '[:space:]')"
    [ -n "$_spec" ] || continue
    _pkg="${_spec%@*}"
    _ver="${_spec##*@}"
    [ -n "$_pkg" ] && [ -n "$_ver" ] && [ "$_pkg" != "$_ver" ] || {
      echo "[entrypoint] AVISO: spec inválido resolvido do catálogo: '$_spec'" >&2
      continue
    }
    _have="$(_plugin_installed_version "$_pkg")"
    if [ "$_have" = "$_ver" ]; then
      echo "  plugin: $_pkg@$_ver ok"
      continue
    fi
    echo "[entrypoint] plugin $_pkg: ${_have:-ausente} -> $_ver (instalando em $_plugins_state_dir/npm)"
    # --force é obrigatório no upgrade: `plugins install` sem ele é NO-OP SILENCIOSO
    # quando já existe qualquer versão do pacote (sai 0 e não troca nada), então o
    # bump de versão da imagem nunca chegava no volume. `plugins update` não serve:
    # recebe id de plugin, não npm spec, vai pro latest e fura o pin.
    if timeout 900 env OPENCLAW_CONFIG_PATH="$_plugins_safe_config" \
         openclaw plugins install "$_pkg@$_ver" --force >"$_plugins_safe_dir/install.log" 2>&1; then
      echo "  plugin: $_pkg@$_ver instalado"
    else
      # O motivo importa: install que falha costuma ser config recusado pelo
      # schema do plugin, e sem o log a mensagem "falhou" manda investigar às
      # cegas. Poucas linhas, só na falha.
      echo "[entrypoint] AVISO: falhou instalar $_pkg@$_ver — gateway sobe SEM esse plugin" >&2
      tail -n 5 "$_plugins_safe_dir/install.log" 2>/dev/null | sed 's/^/[entrypoint]   /' >&2
    fi
  done < "$_plugins_specs_file"
  _prune_codex_platform_packages
else
  echo "[entrypoint] AVISO: não foi possível resolver plugins a partir do catálogo oficial; mantendo instalações atuais" >&2
fi
rm -rf "$_plugins_safe_dir"
