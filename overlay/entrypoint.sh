#!/bin/bash
set -e

# Resolve this script's own directory — works whether it ships baked at
# /lzbot (production images) or lives in a git checkout like
# /app/dev/lzbot (this creator instance).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# - Guarda de PID 1 -----------------------
# A imagem sobe `/usr/bin/tini -g` como PID 1 (Dockerfile, 2026-08-20) porque o
# gateway é node: node só faz wait() nos filhos que ele mesmo spawnou, então
# órfão reparentado pro 1 fica zumbi PARA SEMPRE (7 acumulados aqui antes do
# tini). Se um rebuild/override de ENTRYPOINT tirar o init, a regressão é
# silenciosa: só aparece semanas depois como zumbi acumulando e RAM subindo.
# Não é fatal de propósito — o container tem que subir mesmo assim.
_pid1=$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')
case "$_pid1" in
  tini|init|systemd|dumb-init|s6-svscan|docker-init) : ;;
  *) echo "[entrypoint] AVISO: PID 1 é '$_pid1', não um init — zumbis de subagente/browser não serão colhidos (esperado: tini -g via ENTRYPOINT)" >&2 ;;
esac

# - State ---------------------------------
# /app é bind mount do host e pode ter milhões de arquivos (browser profile,
# node_modules, modelos, índice de memória). chown -R nele é caro em qualquer
# boot e FICA MUITO caro com cache de disco frio (ex: logo após reboot do
# host) — chegou a travar o entrypoint por minutos antes do gateway subir,
# atrasando o polling do Telegram e afins (sentinel, 2026-09-19).
# O dono já bate na maioria dos boots (bind mount persiste o chown anterior no
# host), então primeiro tenta um chown NÃO-recursivo no topo (barato) e só
# dispara o -R completo em BACKGROUND — sem bloquear o resto do boot — quando
# o topo ainda não bate. `/app` já fica 770 (grupo), então lzbot consegue ler/
# escrever ali mesmo com arquivos ainda root-owned enquanto o -R roda atrás.
if [ "$(stat -c %U /app 2>/dev/null)" != "lzbot" ]; then
  echo "[entrypoint] dono de /app não é lzbot — chown -R rodando em background (não bloqueia o boot)"
  ( sudo chown -R lzbot:lzbot /app >/dev/null 2>&1 || true ) &
  disown 2>/dev/null || true
else
  sudo chown lzbot:lzbot /app 2>/dev/null || true
fi

# - SSH -----------------------------------
# ~/.ssh é SYMLINK para /app/.ssh, não cópia.
#
# Antes isto copiava config/known_hosts de /app/.ssh para ~/.ssh a cada boot, e
# o resultado era um par de estados divergentes: /home/lzbot está no overlay
# (morre ao recriar o container) e /app é bind mount (persiste). Quem editava
# ~/.ssh perdia a mudança no restart; quem editava /app/.ssh não via efeito
# nenhum até reiniciar — e o único sintoma era "o ssh ignora minha config".
# Com o symlink existe um estado só, e ele é o persistente.
#
# Mesma forma do bloco do Claude Code abaixo: migra o que houver, depois liga.
if [ -e /home/lzbot/.ssh ] && [ ! -L /home/lzbot/.ssh ]; then
  mkdir -p /app/.ssh
  # known_hosts precisa de MERGE, não cópia: o do overlay acumula hosts
  # aceitos em runtime que o do /app não tem, e um cp -n os perderia calado.
  if [ -f /home/lzbot/.ssh/known_hosts ]; then
    cat /home/lzbot/.ssh/known_hosts /app/.ssh/known_hosts 2>/dev/null \
      | sort -u > /tmp/known_hosts.merged
    mv /tmp/known_hosts.merged /app/.ssh/known_hosts
  fi
  cp -rn /home/lzbot/.ssh/. /app/.ssh/ 2>/dev/null || true
  rm -rf /home/lzbot/.ssh
fi
ln -sfn /app/.ssh /home/lzbot/.ssh

# O ssh RECUSA config com permissão de grupo ("Bad owner or permissions"), e o
# /app fica 770 por causa do chown/ACL acima — sem este chmod o symlink acima
# faria o ssh ignorar a config inteira.
[ -f /app/.ssh/config ] && chmod 600 /app/.ssh/config
[ -f /app/.ssh/known_hosts ] && chmod 600 /app/.ssh/known_hosts
for _key in /app/.ssh/id_*; do
  case "$_key" in *.pub) continue ;; esac
  [ -f "$_key" ] && chmod 600 "$_key"
done

# - Docker socket -------------------------
# Quando o compose monta /var/run/docker.sock, o socket chega com o GID do
# grupo `docker` DO HOST (aqui: 991) — um número que não existe como grupo
# dentro do container. Resultado: `docker ps` responde "permission denied" e
# o agente conclui que não tem acesso ao Docker, quando na verdade só falta
# o usuário estar no grupo certo (10/10/2026).
#
# O GID é derivado do próprio socket, nunca hard-coded: cada host tem o seu,
# e fixar 991 quebraria calado em qualquer outra máquina.
#
# Tem que ser AQUI, no entrypoint, antes do gateway subir: mudança de grupo
# só vale para processos criados DEPOIS dela. Com o gateway já rodando, o
# usermod não tem efeito no PID 1 nem nos exec que descendem dele — só um
# restart de verdade do container resolve (`sg docker -c ...` contorna no
# shell avulso, mas não conserta o gateway).
if [ -S /var/run/docker.sock ]; then
  _dsock_gid=$(stat -c %g /var/run/docker.sock 2>/dev/null)
  if [ -n "$_dsock_gid" ] && [ "$_dsock_gid" != "0" ]; then
    _dsock_grp=$(getent group "$_dsock_gid" | cut -d: -f1)
    if [ -z "$_dsock_grp" ]; then
      # Nome `docker` pode já existir com outro GID (imagem base): remapeia.
      if getent group docker >/dev/null 2>&1; then
        sudo groupmod -g "$_dsock_gid" docker >/dev/null 2>&1 || true
      else
        sudo groupadd -g "$_dsock_gid" docker >/dev/null 2>&1 || true
      fi
      _dsock_grp=$(getent group "$_dsock_gid" | cut -d: -f1)
    fi
    if [ -n "$_dsock_grp" ] && ! id -nG lzbot 2>/dev/null | tr ' ' '\n' | grep -qx "$_dsock_grp"; then
      sudo usermod -aG "$_dsock_grp" lzbot >/dev/null 2>&1 \
        && echo "[entrypoint] lzbot adicionado ao grupo '$_dsock_grp' (gid $_dsock_gid) do docker.sock"
    fi
  fi
fi

# - Git ------------------------------------
if [ -d /app/.git ]; then
  git -C /app config core.sshCommand "ssh -i /app/.ssh/id_ed25519"
fi

# - Claude Code --------------------------------
if [ -e /home/lzbot/.claude ] && [ ! -L /home/lzbot/.claude ]; then
  mkdir -p /app/.claude
  cp -rn /home/lzbot/.claude/. /app/.claude/ 2>/dev/null || true
  rm -rf /home/lzbot/.claude
fi
ln -sfn /app/.claude /home/lzbot/.claude

if [ -e /home/lzbot/.claude.json ] && [ ! -L /home/lzbot/.claude.json ]; then
  cp -n /home/lzbot/.claude.json /app/.claude.json 2>/dev/null || true
  rm -f /home/lzbot/.claude.json
fi
if [ -f /app/.claude.json ]; then
  ln -sf /app/.claude.json /home/lzbot/.claude.json
fi

# Nega as tools de spawn do Claude Code no settings.json do usuário.
#
# Por que aqui e não no openclaw.json: até a 2026.7.1 isso era
# `agents.defaults.cliBackends.<id>.args` com `--disallowedTools ...,Monitor,Agent`.
# Essa chave saiu do schema na 2026.9.1 e NADA a substituiu — conferido no dist.
# Existe SIM um injetor de `--disallowedTools` em runtime
# (`mergeClaudeDisallowedTools`), mas ele não serve: quem o alimenta é
# `injectClaudeMcpConfigArgs`, e a lista que ele monta só pode conter nomes
# `mcp__<server>__<tool>` derivados de `mcpToolsDeny`, mais o literal "WebSearch"
# quando a busca está desligada. Nome de tool NATIVA (Agent, Workflow) não tem
# como entrar ali. Ou seja: `tools.deny` do OpenClaw governa o registry de tools
# do PRÓPRIO OpenClaw e as tools MCP, não o toolkit interno do harness claude-cli.
#
# O que sobra é o settings.json do usuário, que o backend lê porque passa
# `settingSources: ["user"]` (hard-coded no dist) — e `~/.claude` é symlink para
# /app/.claude, feito logo acima.
#
# Medido neste boilerplate (claude 2.1.261, --permission-mode bypassPermissions):
# com `permissions.deny`, a tool não é sequer exposta ao modelo ("I don't see a
# Bash tool in my available tools") — deny ganha de bypassPermissions, não é
# prompt em runtime. Sem deny, `Agent` aparece; com deny, some.
#
# `Workflow` entra na lista por ser vetor de spawn tão bom quanto `Agent` (ele
# orquestra dezenas de subagentes) e não existia na denylist de 2026.7.1.
# `Monitor` mantém paridade com a lista antiga. `Task` é seguro-morto: hoje o
# binário não expõe tool com esse nome exato, mas já expôs, e um deny de nome
# inexistente não custa nada — é a garantia contra a tool voltar a se chamar
# assim num upgrade de claude.
#
# Medido (claude 2.1.261) com a lista completa aplicada: `Agent` e `Workflow`
# somem do toolset. Fica de fora, de propósito, a família `TaskCreate/TaskStop/
# TaskOutput/...` — são gerência de task detachada, não spawn de subagente, e
# negá-las quebraria uso legítimo. Se um dia a política mudar, é aqui.
#
# MERGE, nunca sobrescrita: o arquivo é estado da instância e o usuário edita
# (theme, hooks, etc.). Só acrescenta o que falta em permissions.deny.
LZBOT_CLAUDE_DENY_TOOLS="${LZBOT_CLAUDE_DENY_TOOLS:-Agent,Workflow,Monitor,Task}"
if [ -n "$LZBOT_CLAUDE_DENY_TOOLS" ]; then
  CLAUDE_SETTINGS=/app/.claude/settings.json LZBOT_CLAUDE_DENY_TOOLS="$LZBOT_CLAUDE_DENY_TOOLS" node -e '
const fs = require("fs");
const file = process.env.CLAUDE_SETTINGS;
const want = process.env.LZBOT_CLAUDE_DENY_TOOLS.split(",").map((s) => s.trim()).filter(Boolean);

let cfg = {};
if (fs.existsSync(file)) {
  try {
    cfg = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    // Não é nosso lugar de consertar: sobrescrever aqui apagaria config do usuário.
    console.error("  claude: " + file + " não é JSON válido (" + err.message + ") — denylist NÃO aplicada");
    process.exit(0);
  }
}
if (typeof cfg !== "object" || cfg === null || Array.isArray(cfg)) {
  console.error("  claude: " + file + " não é um objeto — denylist NÃO aplicada");
  process.exit(0);
}

cfg.permissions = (typeof cfg.permissions === "object" && cfg.permissions !== null && !Array.isArray(cfg.permissions)) ? cfg.permissions : {};
const deny = Array.isArray(cfg.permissions.deny) ? cfg.permissions.deny : [];
const missing = want.filter((t) => !deny.includes(t));
if (missing.length === 0) process.exit(0);

cfg.permissions.deny = deny.concat(missing);
fs.mkdirSync(require("path").dirname(file), { recursive: true });
fs.writeFileSync(file, JSON.stringify(cfg, null, 2) + "\n");
console.log("  claude: permissions.deny += " + missing.join(", "));
'
fi

# - Image skills/extensions symlinks -----------
# /app/{skills,extensions} são STATELESS à imagem: sempre um PONTEIRO pro baked
# /lzbot, nunca cópia real no volume persistente. Assim uma imagem nova traz
# skills/extensions atualizadas de graça, sem depender de migration e sem drift
# entre o que o gateway carrega (load: ${OPENCLAW_STATE_DIR}/extensions/) e o que
# a imagem embarca.
#
# FORÇA o symlink todo boot (idempotente). Supersede o migration 0001, que por
# design conservador não troca um dir real preexistente — aqui a gente troca,
# preservando o dir real antigo como backup .pre-symlink em vez de clobberar.
#
# Referência fixa /lzbot (baked), NÃO $SCRIPT_DIR nem /app/dev/lzbot: este é o
# entrypoint da imagem; o checkout de dev é concern da instância criadora e não
# entra aqui (ela re-aponta no próprio hook /app/entrypoint.sh, que roda depois).
force_image_symlink() {
  local target="$1" link="$2"
  [ -e "$target" ] || { echo "[entrypoint] baked ausente ($target) — não linkando $link" >&2; return 0; }
  # já é o symlink certo? idempotente, nada a fazer.
  [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ] && return 0
  # caminho real preexistente (dir/arquivo): preserva como backup, não clobbera calado.
  if [ -e "$link" ] && [ ! -L "$link" ]; then
    local bak="$link.pre-symlink.$(date -u +%Y%m%dT%H%M%SZ)"
    echo "[entrypoint] $link é caminho real — movendo p/ $bak antes de linkar" >&2
    mv "$link" "$bak"
  fi
  ln -sfn "$target" "$link"
  echo "[entrypoint] symlink de imagem: $link -> $target"
}

force_image_symlink /lzbot/skills /app/skills
force_image_symlink /lzbot/extensions /app/extensions

# - Cache (~/.cache) ---------------------------
# ~/.cache vive no overlay (morre ao recriar o container). Ali ficam downloads
# caros de reconstruir — hoje sobretudo os Chromium do Playwright/CloakBrowser
# (bloco abaixo). Sem persistir, todo recreate repaga o download no boot.
# Mesmo padrão do .ssh/.claude: migra o que houver pro volume, symlinka.
if [ -e /home/lzbot/.cache ] && [ ! -L /home/lzbot/.cache ]; then
  mkdir -p /app/.cache
  cp -rn /home/lzbot/.cache/. /app/.cache/ 2>/dev/null || true
  rm -rf /home/lzbot/.cache
fi
ln -sfn /app/.cache /home/lzbot/.cache

# - Config XDG (~/.config) ---------------------
# O gateway injeta XDG_CONFIG_HOME=/app/.config nos processos que ele spawna, então
# o gog funciona pelo fluxo do agente. Mas ~/.config nunca existiu (nada na imagem
# cria) e não era symlinkado: qualquer CLI chamado à mão via Bash cai no default
# ~/.config e não acha credencial nenhuma. Sintoma: `gog auth list` devolve
# "No tokens stored" enquanto o token está vivo em /app/.config/gogcli.
# Symlinkar resolve pros dois caminhos e persiste config de CLI no volume.
# Mesmo padrão do .ssh/.claude/.cache: migra o que houver pro volume, symlinka.
if [ -e /home/lzbot/.config ] && [ ! -L /home/lzbot/.config ]; then
  mkdir -p /app/.config
  cp -rn /home/lzbot/.config/. /app/.config/ 2>/dev/null || true
  rm -rf /home/lzbot/.config
fi
mkdir -p /app/.config
ln -sfn /app/.config /home/lzbot/.config

# - Browsers: Playwright Chromium + CloakBrowser (idempotente, todo boot) -----
# O build NÃO baka o Chromium do Playwright — só roda `install-deps` (libs de
# sistema). CloakBrowser é o Chromium "oficial" da imagem (BROWSER_EXECUTABLE_PATH),
# mas skills que usam Playwright puro (chromium.launch() sem executablePath) exigem
# o binário próprio do Playwright, que vive em ~/.cache/ms-playwright — agora já
# symlinkado pro volume /app (bloco acima), então o download PERSISTE entre boots.
# Aqui garantimos presença a cada boot: o próprio playwright/cloakbrowser PULA se já
# tem a versão certa (idempotente), e o bump de versão (revision novo) é absorvido
# sem intervenção. NÃO-fatal: download que falha loga e segue, não derruba o boot
# (set -e ativo) — por isso cada passo é guardado com `|| echo ... >&2`.
ensure_browsers() {
  if command -v playwright >/dev/null 2>&1; then
    playwright install chromium >/tmp/pw-install.log 2>&1 \
      && echo "[entrypoint] playwright chromium (node): ok" \
      || echo "[entrypoint] playwright chromium (node): FALHOU (ver /tmp/pw-install.log) — seguindo" >&2
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -c "import playwright" >/dev/null 2>&1; then
    python3 -m playwright install chromium >/tmp/pw-install-py.log 2>&1 \
      && echo "[entrypoint] playwright chromium (py): ok" \
      || echo "[entrypoint] playwright chromium (py): FALHOU (ver /tmp/pw-install-py.log) — seguindo" >&2
  fi
  if command -v cloakbrowser >/dev/null 2>&1; then
    CLOAKBROWSER_CACHE_DIR="${CLOAKBROWSER_CACHE_DIR:-/opt/cloakbrowser}" cloakbrowser install >/tmp/cloak-install.log 2>&1 \
      && echo "[entrypoint] cloakbrowser: ok" \
      || echo "[entrypoint] cloakbrowser: FALHOU (ver /tmp/cloak-install.log) — seguindo" >&2
  fi
}
ensure_browsers

# - Locks órfãos do Chromium (perfil do browser do OpenClaw) --------------------
# O SingletonLock/Cookie/Socket guarda o hostname do container que abriu o perfil.
# Container recriado = hostname novo, e o OpenClaw recusa subir o browser
# ("Chromium profile lock names another hostname"). No boot não há Chromium nosso
# rodando, então os Singleton* do perfil são sempre lixo — limpa. Guardado por
# pgrep pra nunca mexer em perfil com processo vivo. NÃO-fatal.
clean_chromium_locks() {
  local d="${OPENCLAW_BROWSER_USER_DATA:-/app/browser/openclaw/user-data}"
  [ -d "$d" ] || return 0
  if pgrep -f -- "--user-data-dir=$d" >/dev/null 2>&1; then
    echo "[entrypoint] chromium lock: perfil em uso, mantendo Singleton*" >&2
    return 0
  fi
  rm -f "$d"/SingletonLock "$d"/SingletonCookie "$d"/SingletonSocket 2>/dev/null \
    && echo "[entrypoint] chromium lock: Singleton* órfãos limpos ($d)" \
    || echo "[entrypoint] chromium lock: falha ao limpar — seguindo" >&2
}
clean_chromium_locks

# - Modelos do embedding local (memory.search.provider: "local") --------------
# A memória do 2026.9.1 é builtin e in-process: sem backend externo, sem daemon,
# sem coleção curada, sem XDG por agente. O único componente externo é o
# llama-server, gerenciado pelo plugin @openclaw/llama-cpp-provider —
# `models.providers["llama-cpp"]` com `baseUrl` + `localService` é a config
# inteira (ver openclaw.json).
#
# NADA disso é assado na imagem, e não é omissão: o plugin baixa o binário do
# llama-server e o GGUF QAT do embeddinggemma-300m para DENTRO do state dir
# (/app/models/llama.cpp), que em produção é bind mount — conteúdo de imagem
# nesse caminho seria sombreado pelo volume. Baixa uma vez (~328 MB) e persiste.
#
# A única coisa a garantir aqui é o diretório existir com o dono certo, senão o
# primeiro download falha por permissão num volume criado por outro uid.
mkdir -p /app/models/llama.cpp


# GATEWAY_TAILSCALE_MODE precisa estar exportado ANTES das migrations, não só
# antes do gateway: a 0005 termina com `openclaw config validate`, que é um
# config load como qualquer outro e rejeita o ${...} não resolvido do
# gateway.tailscale.mode (campo enum). Sem isto, o gate fail-closed derruba o
# boot de toda instância que não define a variável. O uso de fato é no bloco
# Tailscale, mais abaixo.
export GATEWAY_TAILSCALE_MODE="${GATEWAY_TAILSCALE_MODE:-off}"

# - Migrations -------------------------------
# Reconcile a persisted /app volume from an older lzbot image version.
# See migrations/README.md for how/when to add a migration. Fail-closed:
# if a migration fails, do not proceed to Start below.
if [ -x "$SCRIPT_DIR/scripts/lzbot-migrate.sh" ]; then
  "$SCRIPT_DIR/scripts/lzbot-migrate.sh"
fi

# - Plugins externos do OpenClaw -------------
# Põe os plugins declarados na configuração na versão do core da imagem.
# Implementação (e o racional de ordem) em scripts/lzbot-plugin-sync.sh — é compartilhada com a
# migration 0006, que precisa do plugin novo no lugar ANTES de reconciliar o
# config. Aqui roda todo boot: é assim que o bump de versão da imagem chega no
# volume persistente. Não-fatal.
if [ -x "$SCRIPT_DIR/scripts/lzbot-plugin-sync.sh" ]; then
  SCRIPT_DIR="$SCRIPT_DIR" "$SCRIPT_DIR/scripts/lzbot-plugin-sync.sh" || true
fi

# - Tailscale (opt-in) -----------------------
# Canonical images ship the tailscale/tailscaled binaries but keep the
# integration INERT by default. An instance opts in by setting
# GATEWAY_TAILSCALE_MODE=serve (tailnet-only) or =funnel (public) and
# providing TAILSCALE_AUTHKEY (first boot only; state persists on /app).
# GATEWAY_TAILSCALE_MODE is ALWAYS exported so the ${...} ref in
# openclaw.json never throws at config load (OpenClaw errors on missing/
# empty env refs — there is no ${VAR:-default} syntax). O export em si subiu
# para antes das migrations — ver o bloco Migrations acima.

if [ "$GATEWAY_TAILSCALE_MODE" != "off" ]; then
  if ! command -v tailscaled >/dev/null 2>&1; then
    echo "  tailscale: mode=$GATEWAY_TAILSCALE_MODE but tailscaled not installed — skipping"
  elif [ -z "${TAILSCALE_AUTHKEY:-}" ] && [ ! -f /app/.tailscale/tailscaled.state ]; then
    echo "  tailscale: mode=$GATEWAY_TAILSCALE_MODE but no TAILSCALE_AUTHKEY and no saved state — skipping bring-up (gateway serve will fail until the tailnet is up)"
  else
    # Rootless/userspace bring-up: no TUN device or NET_ADMIN needed. The
    # socket lives at the CLI's DEFAULT path so `tailscale serve` (invoked by
    # the gateway with no flags) finds it; state persists on the /app volume.
    sudo mkdir -p /var/run/tailscale && sudo chown lzbot:lzbot /var/run/tailscale || \
      echo "  tailscale: warn — could not prepare /var/run/tailscale"
    mkdir -p /app/.tailscale
    # SOCKS5/HTTP-proxy opt-in: TAILSCALE_SOCKS5_PORT expõe um proxy local no
    # daemon (ex.: browser saindo pela tailnet via --proxy-server=socks5://...).
    # Vazio = sem proxy (default). Genérico; valor/uso é decisão de instância.
    TS_PROXY_ARGS=""
    [ -n "${TAILSCALE_SOCKS5_PORT:-}" ] && \
      TS_PROXY_ARGS="--socks5-server=localhost:${TAILSCALE_SOCKS5_PORT} --outbound-http-proxy-listen=localhost:${TAILSCALE_SOCKS5_PORT}"
    if [ ! -S /var/run/tailscale/tailscaled.sock ]; then
      tailscaled \
        --tun=userspace-networking \
        --socket=/var/run/tailscale/tailscaled.sock \
        --statedir=/app/.tailscale \
        ${TS_PROXY_ARGS} \
        >/app/.tailscale/tailscaled.log 2>&1 &
      for _ in $(seq 1 30); do
        [ -S /var/run/tailscale/tailscaled.sock ] && break
        sleep 0.2
      done
    fi
    if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
      tailscale up --authkey="$TAILSCALE_AUTHKEY" \
        --hostname="${TAILSCALE_HOSTNAME:-lzbot}" ${TAILSCALE_UP_ARGS:-} || \
        echo "  tailscale: warn — 'up' failed (check authkey/tailnet/HTTPS)"
    else
      tailscale up --hostname="${TAILSCALE_HOSTNAME:-lzbot}" ${TAILSCALE_UP_ARGS:-} || true
    fi
    echo "  tailscale: mode=$GATEWAY_TAILSCALE_MODE, daemon up (userspace), state in /app/.tailscale"
  fi
fi

# - Virtual display + VNC (headed Chrome, visible over VNC) ------------------
# Brings up Xvfb :99 + x11vnc:5900 and exports DISPLAY for the gateway, so the
# CloakBrowser Chromium (a child of the gateway) draws on the virtual display
# instead of running headless. That lets a human watch the bot's tabs over VNC
# without having to close the browser. Requires browser.headless:false in
# openclaw.json. Idempotent (won't double-spawn) and non-blocking (a failure
# here won't take down the gateway, despite set -e).
#   VNC_DISPLAY   (default :99)          which X display to use
#   VNC_GEOMETRY  (default 1920x1080x24) Xvfb screen geometry; the image pins 1366x768x24
#                                        via ENV (Xvfb cannot resize at runtime: changing
#                                        it means restarting Xvfb, which kills the headed
#                                        Chrome drawing on that display)
#   VNC_PORT      (default 5900)         x11vnc RFB port
#   VNC_LOCALHOST (unset)                set to bind x11vnc to localhost only
#   VNC_PASSWD    (default lzbot123)     VNC password; NEVER left open by default
# SECURITY: x11vnc always runs password-protected (default lzbot123). Override
# VNC_PASSWD per instance, and/or set VNC_LOCALHOST to require an SSH tunnel.
export DISPLAY="${VNC_DISPLAY:-:99}"
VNC_PASSWD="${VNC_PASSWD:-lzbot123}"
if command -v Xvfb >/dev/null 2>&1; then
  if ! pgrep -f "Xvfb ${DISPLAY} " >/dev/null 2>&1; then
    nohup Xvfb "${DISPLAY}" -screen 0 "${VNC_GEOMETRY:-1920x1080x24}" >/tmp/xvfb.log 2>&1 &
    for _ in $(seq 1 40); do DISPLAY="${DISPLAY}" xdpyinfo >/dev/null 2>&1 && break; sleep 0.2; done
  fi
  # Window manager: without one, Chrome's windows have no title bar and can't be moved,
  # resized or maximized over VNC. The check is the EWMH property on the root window, not
  # `pgrep -f fluxbox`: if Xvfb is ever restarted (a geometry change, say) fluxbox dies with
  # it and, since PID 1 doesn't reap, lingers as <defunct>. A zombie has an empty cmdline, so
  # `pgrep -f` falls back to the process name and matches the corpse — the WM would then
  # never come back. The if/fi form is also what keeps `set -e` out of the way: a false
  # condition yields 0, and the backgrounded fluxbox can't fail the boot either.
  if command -v fluxbox >/dev/null 2>&1 && \
     ! xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q '0x'; then
    nohup fluxbox >/tmp/fluxbox.log 2>&1 &
  fi
  if command -v x11vnc >/dev/null 2>&1 && ! pgrep -f "x11vnc .*-rfbport ${VNC_PORT:-5900}" >/dev/null 2>&1; then
    # -noprimary: don't ship the PRIMARY selection (mouse-drag highlight) to the viewer.
    # Without it, merely selecting text inside the container overwrites whatever the human
    # had copied on their own machine — the single biggest source of "VNC clipboard is
    # unreliable". Ctrl+C/Ctrl+V (CLIPBOARD) still sync both ways, and -setprimary stays on
    # by default, so pasting from the host still fills PRIMARY for middle-click paste.
    nohup x11vnc -display "${DISPLAY}" -forever -shared -noprimary -rfbport "${VNC_PORT:-5900}" \
      ${VNC_LOCALHOST:+-localhost} ${VNC_PASSWD:+-passwd "$VNC_PASSWD"} \
      -o /tmp/x11vnc.log >/dev/null 2>&1 &
  fi
  echo "  display: Xvfb ${DISPLAY} + x11vnc:${VNC_PORT:-5900} (chrome headed)"
fi

# - Patches locais no dist do OpenClaw ------
# O dist vive em ~/.nvm (overlay) e volta pristino a cada recreate do container,
# então patch de bug do openclaw é reaplicado a cada boot — não é "instalação".
#
# Cada script é idempotente e all-or-nothing: valida TODAS as âncoras antes de
# escrever, e se alguma não bater (upgrade do openclaw mexeu no trecho) falha
# alto e deixa o dist INTACTO. Por isso o `||` aqui só avisa: patch que não cola
# não pode impedir o boot.
#
# TEM que rodar antes do `exec` E antes da delegação: o node importa os módulos
# no start, patch depois disso não vale nada, e rodar aqui (não no hook) faz
# valer pra instância com e sem hook.
if [ -d "$SCRIPT_DIR/scripts/patches" ]; then
  for _p in "$SCRIPT_DIR"/scripts/patches/*.sh; do
    [ -x "$_p" ] || continue
    "$_p" || echo "[entrypoint] AVISO: $(basename "$_p") NÃO aplicado (dist intacto) — reescrever âncoras" >&2
  done
fi

# - Start -----------------------------------
# Hook de instância: /app/entrypoint.sh.
#
# Este arquivo (na imagem) é EFÊMERO — um rebuild devolve ele ao original. O
# /app é bind mount, então é lá que cada instância guarda o que é só dela.
# Genérico vem para cá; específico da instância vai para lá.
#
# O nome é /app/entrypoint.sh de propósito, e não um entrypoint.instance.sh:
# toda instância anterior a esta imagem usava /app/entrypoint.sh como ENTRYPOINT
# de verdade, e manter o nome faz esses volumes continuarem valendo sem precisar
# editar cada um. O custo é que um /app/entrypoint.sh legado é uma CÓPIA COMPLETA
# deste script, então o trecho genérico roda duas vezes. Tudo aqui é idempotente
# (guardas de pgrep, ln -sfn, chmod, chown), então isso desperdiça tempo de boot
# e nada mais.
#
# Contrato do hook: recebe o CMD em "$@" e é responsável por dar `exec "$@"` no
# final. Um hook que esqueça isso sobe o container sem o gateway — sintoma
# silencioso, primeiro lugar a olhar.
#
# LZBOT_ENTRYPOINT_HOOK trava recursão, e ela não é hipotética: como o hook legado
# é uma cópia DESTE script, ele carrega este mesmo bloco — sem a trava, chamaria a
# si mesmo em loop infinito. Cobre também o caso de alguém apontar um para o outro
# por symlink "pra unificar".
#
# COMO DECIDIMOS SE /app/entrypoint.sh É HOOK OU CÓPIA LEGADA (mudou 2026-08-20):
#
#  1. Declaração explícita ganha de tudo: a linha `# lzbot-entrypoint: hook` em
#     qualquer lugar do arquivo diz "sou hook, delega". É o caminho recomendado —
#     comentário declarativo não é código, ninguém escreve por acidente.
#  2. Sem declaração, cai na heurística de cópia legada, que exige DUAS âncoras
#     do bootstrap antigo juntas. Antes era UMA (`cp` de known_hosts), e isso é
#     linha de código plausível: qualquer hook que quisesse mexer em known_hosts
#     — ou só COMENTAR sobre o marcador — era classificado como legado e ignorado
#     em silêncio. Foi exatamente o que aconteceu numa instância: o hook era código
#     morto, o tini nunca virou PID 1 e os patches do dist sumiam a cada boot.
#  3. Nem declaração nem âncoras: delega. O default seguro é delegar, porque
#     falso-"legado" mata o setup específico da instância (Tailscale, patches),
#     enquanto falso-"hook" só roda o bloco genérico duas vezes — e tudo aqui é
#     idempotente.
#
# Quando a heurística acusa legado, GRITA com motivo: o arquivo não vai rodar, e
# se alguém tinha coisa específica lá dentro ela está sendo descartada agora.
if [ -z "${LZBOT_ENTRYPOINT_HOOK:-}" ] && [ -f /app/entrypoint.sh ]; then
  _hook_declared=0
  _legacy=0
  grep -qE '^[[:space:]]*#[[:space:]]*lzbot-entrypoint:[[:space:]]*hook' /app/entrypoint.sh 2>/dev/null && _hook_declared=1
  if [ "$_hook_declared" = "0" ] \
     && grep -q 'cp /app/\.ssh/known_hosts /home/lzbot/\.ssh/known_hosts' /app/entrypoint.sh 2>/dev/null \
     && grep -q 'sudo chown -R lzbot:lzbot /app' /app/entrypoint.sh 2>/dev/null; then
    _legacy=1
  fi

  if [ "$_legacy" = "1" ]; then
    echo "[entrypoint] AVISO: /app/entrypoint.sh parece CÓPIA COMPLETA do bootstrap antigo (achei 'cp known_hosts' + 'sudo chown -R lzbot:lzbot /app'), então NÃO vou delegar — ele não vai rodar." >&2
    echo "[entrypoint] AVISO: se for hook de verdade, qualquer coisa específica desta instância está sendo IGNORADA. Conserte com uma das duas: (a) enxugue o arquivo deixando só o específico, ou (b) declare-o com a linha '# lzbot-entrypoint: hook'." >&2
  else
    export LZBOT_ENTRYPOINT_HOOK=1
    if [ "$_hook_declared" = "1" ]; then
      echo "  entrypoint: instância /app/entrypoint.sh assumindo (hook declarado)"
    else
      echo "  entrypoint: instância /app/entrypoint.sh assumindo"
    fi
    if [ -x /app/entrypoint.sh ]; then
      exec /app/entrypoint.sh "$@"
    fi
    # Volume legado pode ter perdido o bit de execução no bind mount
    exec bash /app/entrypoint.sh "$@"
  fi
fi

exec "$@"
