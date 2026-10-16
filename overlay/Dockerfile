## ---------------------------------------------------------------------------
## Origem do OpenClaw: `npm` (upstream publicado) ou `fork` (nosso vendor/openclaw)
## ---------------------------------------------------------------------------
## O default é `npm` de propósito: build de bot novo não depende do fork existir.
## Pra buildar com os nossos patches:
##   scripts/build-image.sh            (resolve o SHA do submodule sozinho)
##   docker build --build-arg OPENCLAW_SOURCE=fork --build-arg OPENCLAW_REF=<sha> .
## O SHA é sempre o do submodule vendor/openclaw — fonte única da verdade.
ARG OPENCLAW_SOURCE=npm
ARG NODE_VERSION=26.8.1

## Estágio fork: builda o nosso OpenClaw a partir do source e empacota o tarball.
## Clona em vez de COPY do submodule pra manter o contexto de build em ~8 MB
## (a árvore do upstream tem 894 MB + histórico; ver vendor/ no .dockerignore).
FROM node:${NODE_VERSION}-bookworm AS openclaw-fork
ARG OPENCLAW_REPO=https://github.com/luizjr8/openclaw.git
ARG OPENCLAW_REF
RUN test -n "${OPENCLAW_REF}" || { echo "OPENCLAW_REF obrigatorio com OPENCLAW_SOURCE=fork"; exit 1; }
RUN corepack enable \
    && git clone --filter=blob:none "${OPENCLAW_REPO}" /src \
    && cd /src \
    && git checkout --detach "${OPENCLAW_REF}" \
    && pnpm install --frozen-lockfile \
    && pnpm build \
    && mkdir -p /out \
    && npm pack --pack-destination /out

## Estágio npm: sem build, /out vazio — o install cai no openclaw@${OPENCLAW_VERSION}.
FROM alpine:3 AS openclaw-npm
RUN mkdir -p /out

## Seletor: resolve pra um dos dois acima conforme OPENCLAW_SOURCE.
FROM openclaw-${OPENCLAW_SOURCE} AS openclaw-pkg

## ---------------------------------------------------------------------------
## Imagem base Ubuntu 24.04 LTS (Noble) — Python 3.12: wheels aarch64 recentes p/ onnxruntime/av (evita backtracking do pip)
FROM ubuntu:noble

## Evitar prompts interativos durante instalação
ENV DEBIAN_FRONTEND=noninteractive

## Versões fixas de build
ARG NODE_VERSION=26.8.1
ARG PLAYWRIGHT_VERSION=1.60

## Variáveis de ambiente do container
# DISPLAY=:99 é o display virtual (Xvfb) que o entrypoint sobe no boot. O Chrome do
# CloakBrowser roda HEADED nesse display (browser.headless:false) e fica visível via
# x11vnc — assim dá pra ver as abas do bot por VNC sem fechar o browser.
# VNC_PASSWD é o default canônico (o container pode sobrescrever via -e VNC_PASSWD=...);
# NUNCA deixar vazio, senão o x11vnc sobe aberto. VNC_LOCALHOST=1 + VNC_PORT não-padrão
# (28311, em vez do 5900 óbvio) evitam que fingerprint checks (iphey/mixvisit) detectem
# processo VNC via port-probe; acesso externo precisa de túnel (SSH/socat) terminando
# dentro do container, já que bind fica em 127.0.0.1. Ver bloco "Virtual display + VNC" no entrypoint.
# VNC_GEOMETRY fixa a tela em 1366x768: o Xvfb não redimensiona em execução (não há xrandr
# na imagem), então quem quiser outra resolução tem que reiniciar o Xvfb — e isso derruba o
# Chrome headed que desenha nesse display. Definir aqui evita esse restart no dia a dia.
ENV NODE_VERSION=${NODE_VERSION} \
    OPENCLAW_STATE_DIR=/app \
    DISPLAY=:99 \
    VNC_PASSWD=lzbot123 \
    VNC_LOCALHOST=1 \
    VNC_PORT=28311 \
    VNC_GEOMETRY=1366x768x24 \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    TZ=America/Recife \
    CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser \
    BROWSER_EXECUTABLE_PATH=/usr/local/bin/cloakbrowser-chromium \
    NODE_COMPILE_CACHE=/tmp \
    OPENCLAW_NO_RESPAWN=1 \
    PATH="/home/lzbot/.local/bin:/home/lzbot/.nvm/versions/node/v${NODE_VERSION}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

## Instalar dependências base
RUN apt-get update && apt-get install -y --no-install-recommends \
    sudo \
    curl \
    git \
    vim \
    nano \
    build-essential \
    tini \
    python3 \
    python3-pip \
    python3-venv \
    sqlite3 \
    ca-certificates \
    tzdata \
    tesseract-ocr \
    tesseract-ocr-por \
    ffmpeg \
    openssh-client \
    socat \
    xvfb \
    x11vnc \
    fluxbox \
    x11-utils \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

## PEP 668 (noble): permitir pip install no ambiente do sistema — inclusive quando o agente instalar libs em runtime
RUN pip3 config set global.break-system-packages true

## Instalar apenas o cliente Docker (binário estático) — fala com o daemon do host via socket montado.
## Evita o pacote docker.io (traz daemon + containerd + runc, centenas de MB desnecessários).
ARG DOCKER_CLI_VERSION=27.5.1
RUN ARCH=$(dpkg --print-architecture) \
    && case "${ARCH}" in \
        amd64) DOCKER_ARCH=x86_64 ;; \
        arm64) DOCKER_ARCH=aarch64 ;; \
        *) echo "Arquitetura não suportada: ${ARCH}" && exit 1 ;; \
    esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar -xz -C /usr/local/bin --strip-components=1 docker/docker

## Tailscale — binários estáticos (tailscale + tailscaled), sem systemd.
## A integração fica INERTE por padrão (gateway.tailscale.mode=off); uma
## instância opta por tailscale setando GATEWAY_TAILSCALE_MODE=serve + authkey.
## O tarball extrai um dir tailscale_<ver>_<arch>/ contendo os dois binários.
ARG TAILSCALE_VERSION=1.98.9
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_${ARCH}.tgz" \
       | tar -xz -C /usr/local/bin --strip-components=1 \
           "tailscale_${TAILSCALE_VERSION}_${ARCH}/tailscale" \
           "tailscale_${TAILSCALE_VERSION}_${ARCH}/tailscaled"

## Criar usuário lzbot (UID 1000) e adicionar ao grupo sudo (sem senha)
# noble já traz o usuário 'ubuntu' no UID 1000; removemos para lzbot herdar 1000
# (os cache mounts do npm e o dev assumem uid/gid 1000)
RUN userdel -r ubuntu 2>/dev/null; \
    useradd -m -s /bin/bash -u 1000 lzbot \
    && usermod -aG sudo lzbot \
    && echo 'lzbot ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

## Instalar gogcli via binário pré-compilado (github.com/openclaw/gogcli)
ARG GOGCLI_VERSION=0.19.0
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/openclaw/gogcli/releases/download/v${GOGCLI_VERSION}/gogcli_${GOGCLI_VERSION}_linux_${ARCH}.tar.gz" \
       | tar -xz -C /usr/local/bin gog

## Instalar GitHub CLI (gh) via binário pré-compilado (github.com/cli/cli).
## O OpenClaw não traz gh próprio: a identidade gerenciada/nativa (tools.github) só injeta
## GH_TOKEN e usa o `gh` do PATH. Config persiste em ~/.config (symlink pra /app/.config).
ARG GH_VERSION=2.102.0
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${ARCH}.tar.gz" \
       | tar -xz -C /usr/local/bin --strip-components=2 "gh_${GH_VERSION}_linux_${ARCH}/bin/gh" \
    && gh --version

## Instalar nvm e Node.js para o usuário lzbot
RUN su - lzbot -c "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash \
    && . ~/.nvm/nvm.sh && nvm install ${NODE_VERSION} && nvm alias default ${NODE_VERSION}"

## Symlinks fixos em /usr/local/bin (resilientes a mudança de versão do Node)
RUN ln -sf /home/lzbot/.nvm/versions/node/v${NODE_VERSION}/bin/node /usr/local/bin/node \
    && ln -sf /home/lzbot/.nvm/versions/node/v${NODE_VERSION}/bin/npm /usr/local/bin/npm \
    && ln -sf /home/lzbot/.nvm/versions/node/v${NODE_VERSION}/bin/npx /usr/local/bin/npx

## Configurar NVM no .bashrc (apenas para shells interativas; não afeta RUNs do build)
RUN echo 'export NVM_DIR="$HOME/.nvm"' >> /home/lzbot/.bashrc \
    && echo '[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"' >> /home/lzbot/.bashrc

## PACOTES NODEJS GLOBAIS (Playwright incluso aqui — única instalação)
# corepack vem bundlado no Node; instalar por cima fixa a versão em vez de herdar a do runtime.
#
# `node-llama-cpp` NÃO entra aqui (removido em 30/ago/2026, ~709 MB). Era
# órfão: quem usa traz cópia própria bundlada — hoje só o plugin
# `@openclaw/llama-cpp-provider`, na árvore dele em `/app/npm/projects/`. Ele não
# resolve pro global, e nada chama o binário `node-llama-cpp`/`nlc` do PATH. Um
# `npm rm -g` em 24/ago não bastou: por estar na camada da imagem, ele ressuscitou
# no recreate do container (mesma classe do plugin do WhatsApp).
ARG COREPACK_VERSION=0.36.0
RUN --mount=type=cache,target=/home/lzbot/.npm,uid=1000,gid=1000 \
    npm install -g playwright@${PLAYWRIGHT_VERSION} clawhub corepack@${COREPACK_VERSION} \
    && rm -rf /home/lzbot/.cache

## PACOTES PYTHON PIP LEVES (wheels triviais — cacheiam separado do bloco pesado)
# --break-system-packages: noble aplica PEP 668; instala no ambiente do sistema mesmo assim
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --break-system-packages \
        requests \
        beautifulsoup4 \
        pymupdf \
        pytesseract \
        pillow \
        yt-dlp \
        pandas \
        openpyxl \
        lxml \
        cryptography \
        fast-flights \
        playwright==${PLAYWRIGHT_VERSION}.0

## WHISPER + ENGINE (bloco pesado, versões pinadas)
# --only-binary=:all: proíbe build de fonte (falha em segundos se faltar wheel, em vez de compilar)
# Versões fixas eliminam o backtracking do resolver (sem re-download de wheels binárias grandes)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --break-system-packages --only-binary=:all: \
        faster-whisper-cli==1.0.1 \
        faster-whisper==1.2.1 \
        ctranslate2==4.8.0 \
        onnxruntime==1.27.0 \
        av==18.0.0

## Instalar CloakBrowser (único Chromium da imagem)
RUN mkdir -p /opt/cloakbrowser && chown -R lzbot:lzbot /opt/cloakbrowser \
    && su - lzbot -c ". ~/.nvm/nvm.sh && npm install -g cloakbrowser && CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser cloakbrowser install" \
    # Instalar dependências de sistema do Chromium via playwright install-deps
    && npx playwright install-deps chromium \
    # Wrapper: prefere um Chromium PINADO no volume persistente (CLOAKBROWSER_PINNED_DIR, default
    # /app/browser/cloakbrowser-pinned/chromium-*), senão o mais recente em /opt/cloakbrowser.
    # Motivo: o CloakBrowser 154 pro + proxy SOCKS (tailscale exit node) leva bloqueio do AWS WAF
    # da Amazon ("Algo deu errado" a partir da 2a navegação) e o plano free só baixa o latest, então
    # o 146 não é reinstalável por versão — o binário fica copiado em /app. CLOAKBROWSER_PINNED_DIR=none desliga o pin.
    # Ativa WebGL via SwiftShader (CPU).
    && printf '#!/bin/bash\nPINNED=$(ls -1d "${CLOAKBROWSER_PINNED_DIR:-/app/browser/cloakbrowser-pinned}"/chromium-* 2>/dev/null | sort -V | tail -n 1)\nLATEST=$(ls -1d /opt/cloakbrowser/chromium-* 2>/dev/null | sort -V | tail -n 1)\nexec "${PINNED:-$LATEST}/chrome" --use-gl=angle --use-angle=swiftshader --enable-webgl --ignore-gpu-blocklist "$@"\n' > /usr/local/bin/cloakbrowser-chromium \
    && chmod +x /usr/local/bin/cloakbrowser-chromium

# CLaude Cli
RUN su - lzbot -c "curl -fsSL https://claude.ai/install.sh | bash"

## OpenClaw
## Usado só quando OPENCLAW_SOURCE=npm. Com `fork`, a versão vem do package.json
## do próprio tarball buildado — manter este ARG alinhado com a tag upstream que
## serve de base pro patch queue, senão imagem e host divergem (já aconteceu:
## ficou em 2026.9.5 enquanto o host rodava 2026.9.7).
ARG OPENCLAW_VERSION=2026.9.9
# jsonc-parser: usado SÓ pelas migrations, e só no /app/agents.json5 — ver
# scripts/lib/jsonc-edit.mjs. Não é dependência do openclaw (o produto embute uma
# cópia inline no bundle do `dist`, sem exportá-la), então instalamos explícito.
# Zero dependências transitivas, MIT, ~150 KB. O openclaw.json NÃO usa isto: lá o
# arquivo é JSON estrito de propósito (ver openclaw.json.md).
ARG JSONC_PARSER_VERSION=3.3.1
## /out vem vazio no modo npm e com o .tgz no modo fork — o install escolhe sozinho.
COPY --from=openclaw-pkg /out/ /tmp/openclaw-pkg/
RUN --mount=type=cache,target=/home/lzbot/.npm,uid=1000,gid=1000 \
    OPENCLAW_PKG="$(ls /tmp/openclaw-pkg/*.tgz 2>/dev/null | head -1)" \
    && echo "openclaw origem: ${OPENCLAW_PKG:-npm openclaw@${OPENCLAW_VERSION}}" \
    && chmod -R a+r /tmp/openclaw-pkg \
    && su - lzbot -c ". ~/.nvm/nvm.sh && npm install -g ${OPENCLAW_PKG:-openclaw@${OPENCLAW_VERSION}} jsonc-parser@${JSONC_PARSER_VERSION}" \
    && rm -rf /tmp/openclaw-pkg

## Diretório de trabalho (criado automaticamente, dono ajustado via COPY --chown)
WORKDIR /app

## Motor interno do lzbot: imutável por build, FORA do volume /app. Nunca
## sofre shadow por volume persistente — toda instância, em qualquer versão
## de imagem, sempre tem aqui o entrypoint/migrations/scripts EXATOS daquela
## build, mais as skills/extensions globais (genéricas, não dado de usuário).
## O agente RODA em /app — /lzbot só existe pra alimentar /app (via seed ou
## symlink) e pro entrypoint ler migrations. Nunca é lido em runtime pelo
## OpenClaw diretamente (OPENCLAW_STATE_DIR continua /app).
COPY --chown=lzbot:lzbot entrypoint.sh /lzbot/entrypoint.sh
COPY --chown=lzbot:lzbot VERSION /lzbot/VERSION
COPY --chown=lzbot:lzbot migrations/ /lzbot/migrations/
COPY --chown=lzbot:lzbot scripts/ /lzbot/scripts/
COPY --chown=lzbot:lzbot skills/ /lzbot/skills/
COPY --chown=lzbot:lzbot extensions/ /lzbot/extensions/
RUN chmod +x /lzbot/entrypoint.sh /lzbot/scripts/*.sh

## Seed inicial do volume /app — só tem efeito num volume novo/vazio (um
## volume já populado sombreia estes arquivos; reconciliar instâncias
## existentes é responsabilidade de /lzbot/migrations, não deste COPY).
## openclaw.json, agents.json5 e workspace/ são de fato de /app (mutam por
## instância) — não fazem sentido como symlink pro /lzbot.
COPY --chown=lzbot:lzbot openclaw.json /app/openclaw.json
COPY --chown=lzbot:lzbot agents.json5 /app/agents.json5
COPY --chown=lzbot:lzbot workspace/ /app/workspace/

## skills/ e extensions/ são genéricos (não dado de usuário) — em vez de
## copiar, /app aponta via symlink pro /lzbot: update de imagem atualiza os
## dois automaticamente, sem duplicar conteúdo. Só surte efeito em volume
## novo (mesma ressalva do bloco acima); instância existente reconcilia via
## migration.
RUN ln -sfn /lzbot/skills /app/skills \
    && ln -sfn /lzbot/extensions /app/extensions

## Plugins externos do OpenClaw (WhatsApp, Codex) — NÃO são assados aqui.
## Eles moram no state dir do VOLUME (`/app/npm/projects/...`), instalados pelo
## entrypoint a cada boot (idempotente). Motivos, todos apanhados na prática:
##
## 1. O registry gerenciado do `/app/npm` GANHA de qualquer cópia em
##    `/app/extensions`. Plugin assado na imagem vira peso morto (codex 350 MB
##    + whatsapp 90 MB) e, pior, cria drift silencioso: a versão que carrega
##    não é a que você assou.
## 2. `openclaw plugins install clawhub:<pkg>` grava em `/app/extensions`. Numa
##    instância onde esse path é symlink pra um checkout, o install suja o repo
##    e o plugin sobe UNTRUSTED (`openKeyedStore is only available for trusted
##    plugins`) — canal em loop de restart. A forma npm-spec (sem `clawhub:`)
##    instala no `/app/npm` gerenciado, que é trusted.
## 3. Plugin no volume sobrevive a recreate do container sem depender da imagem.
##
## A lista vem do catálogo oficial que o próprio OpenClaw empacota: no boot, o
## entrypoint cruza esse catálogo com os plugins/canais/providers declarados no
## openclaw.json. A versão vem do package.json do core da imagem.
##
## ⚠️ VERSÃO PINADA, e não é frescura: o plugin declara
## `compat.pluginApi >= <versão dele>`. Install sem seletor pega o `latest` do
## ClawHub (hoje 2026.8.2) e, num host pinado em 2026.7.1-2, o gateway recusa o
## plugin por incompatibilidade de pluginApi — canal do WhatsApp e harness do
## Codex somem calados. O resolver normaliza o sufixo `-N` do core, que é uma
## correção só do host e não existe nos pacotes dos plugins.
##
## ⚠️ A lista CRESCEU no 2026.9.1: deepseek, perplexity, slack e llama-cpp
## deixaram de ser stock e viraram plugin externo. `openclaw config validate`
## acusa cada um ("plugin not installed"), e o efeito de faltar é o mesmo de
## sempre — a funcionalidade some calada. O `llama-cpp` é o mais crítico: é ele
## que sustenta `memory.search.provider: "local"`; sem ele a memória não embeda.
## `codex` no PATH sem segunda cópia do binário — o wrapper resolve o shim do
## plugin instalado no state dir. Ver scripts/codex-shim.sh.
COPY scripts/codex-shim.sh /usr/local/bin/codex
RUN chmod 0755 /usr/local/bin/codex

## Definir usuário padrão
USER lzbot

## tini como PID 1 (init reaper + propagação de sinal ao process group).
## Sem isso o `exec "$@"` do entrypoint faz o node herdar o PID 1, e node só
## dá wait() nos filhos que ele mesmo spawnou: todo órfão reparentado pra 1
## (helpers do boot, crashpad do chromium) fica zumbi permanente. Tini precisa
## ser PID 1 desde o segundo zero — dentro do entrypoint chegaria tarde pros
## órfãos que nascem durante o próprio script.
## -g propaga SIGTERM/SIGINT pro process group inteiro, não só pro filho direto:
## Xvfb, fluxbox, x11vnc e tailscaled ficam órfãos pendurados no PID 1 e sem -g
## nunca recebem sinal — morrem por SIGKILL no fim do timeout do Docker.
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/lzbot/entrypoint.sh"]
CMD ["openclaw", "gateway"]
