#!/usr/bin/env python3
"""
Script de benchmark para agentes OpenClaw.
Itera modelos configurados em config.yml, executa tarefas agênticas e salva resultados.

Uso:
  python benchmark.py                  # sobe gateway por modelo (modo padrão)
  python benchmark.py --external-gw    # usa gateway já em execução externamente
  python benchmark.py --local          # modo embedded sem gateway (sem browser)
  python benchmark.py --model <id>     # testa apenas um modelo
  python benchmark.py --task <id>      # testa apenas uma tarefa
"""

import json
import time
import socket
import signal
import logging
import argparse
import subprocess
import shutil
import os
import sys
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import yaml
from dotenv import dotenv_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BENCH_DIR = Path(__file__).parent
PROJECT_ROOT = BENCH_DIR.parent
APP_DIR = PROJECT_ROOT / "app"
RESULTS_DIR = BENCH_DIR / "results"
BENCHMARK_FILE = RESULTS_DIR / "benchmark.json"
CONFIG_FILE = BENCH_DIR / "config.yml"
ENV_FILE = PROJECT_ROOT / ".env"
# Pasta com subpastas por task contendo arquivos de contexto (design.md, datasets, etc.)
TASKS_DIR = BENCH_DIR / "tasks"


def short_id(timestamp: str) -> str:
    return hashlib.md5(timestamp.encode()).hexdigest()[:4].upper()


## Carregamento de configuração e ambiente

def load_config() -> Dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_workspace(external_gw, model: Optional[str] = None, config: Optional[Dict] = None) -> None:
    """
    Recria app/ a partir dos templates — mesma lógica do run_local.sh.
    Garante estado limpo (sem memória nem sessões anteriores) para cada modelo.
    Se model for fornecido, hardcoda o modelo em agents.defaults.model para que
    sessions_spawn (subagentes) usem o mesmo modelo testado (não ${PRIMARY_AGENT_MODEL}).
    """
    logger.info("Recriando workspace limpo em app/...")

    if not external_gw:
      if APP_DIR.exists():
          # rm -rf é mais robusto que shutil.rmtree no macOS (evita ENOTEMPTY com arquivos locked)
          subprocess.run(["rm", "-rf", str(APP_DIR)], check=True)
      APP_DIR.mkdir()

      # Copia todos os artefatos que o openclaw precisa
      shutil.copytree(PROJECT_ROOT / "workspace",   APP_DIR / "workspace")
      shutil.copytree(PROJECT_ROOT / "extensions",  APP_DIR / "extensions")
      shutil.copytree(PROJECT_ROOT / "skills",      APP_DIR / "skills")
      shutil.copytree(PROJECT_ROOT / "assets",      APP_DIR / "assets")
      shutil.copy2(PROJECT_ROOT / "agents.json5",   APP_DIR / "agents.json5")
      shutil.copy2(PROJECT_ROOT / "agents-extra.json5", APP_DIR / "agents-extra.json5")

    # Copia openclaw.json, sobrescreve porta e desativa delegação para sub-agentes no benchmark
    oc_src = PROJECT_ROOT / "openclaw.json"
    oc_dst = APP_DIR / "openclaw.json"
    with open(oc_src, "r", encoding="utf-8") as f:
        oc_config = json.load(f)
    gateway_port = _bench_cfg(config or {}).get("gateway_port", GATEWAY_PORT_DEFAULT)
    oc_config.setdefault("gateway", {})["port"] = gateway_port
    # Minimiza delegação para sub-agentes no benchmark
    subagents = oc_config.get("agents", {}).get("defaults", {}).get("subagents", {})
    subagents["delegationMode"] = "suggest"   # minimiza delegação no benchmark
    subagents["maxSpawnDepth"] = 1            # schema exige mínimo 1; limita a 1 nível de sub-agente
    # Hardcoda o modelo testado para sessions_spawn não usar ${PRIMARY_AGENT_MODEL} do ambiente
    if model:
        model_cfg = oc_config.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})
        model_cfg["primary"] = model
        model_cfg["fallbacks"] = [model]
    with open(oc_dst, "w", encoding="utf-8") as f:
        json.dump(oc_config, f, indent=2, ensure_ascii=False)

    # Aguarda o hot-reload do gateway aplicar o novo openclaw.json
    if external_gw and model:
        time.sleep(2)

    # Remove BOOTSTRAP.md para não interceptar os prompts do benchmark
    bootstrap = APP_DIR / "workspace" / "BOOTSTRAP.md"
    if bootstrap.exists():
        bootstrap.unlink()

    # Renomeia *.example.md → *.md para o agente ter o contexto completo do workspace
    for f in (APP_DIR / "workspace").glob("*.example.md"):
        f.rename(f.with_name(f.name.replace(".example.md", ".md")))


def build_env(model: Optional[str] = None) -> Dict:
    """
    Mescla env do processo com .env do projeto e define os caminhos corretos,
    espelhando o que run_local.sh exporta antes de rodar o openclaw.
    Se model for fornecido, sobrescreve FALLBACK_AGENT_MODEL com o mesmo modelo,
    evitando que uma falha silenciosa use outro modelo e contamine os resultados.
    """
    env = dict(os.environ)
    if ENV_FILE.exists():
        env.update(dotenv_values(ENV_FILE))

    # Mesmas vars que run_local.sh exporta
    env["OPENCLAW_WORKSPACE_DIR"] = str(APP_DIR / "workspace")
    env["OPENCLAW_STATE_DIR"] = str(APP_DIR)

    # Remove a env TELEGRAM
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TELEGRAM_USER_ID", None)

    # Garante Node.js >= v22.19 no PATH (openclaw exige)
    node_bin = find_node_bin_dir()
    if node_bin:
        env["PATH"] = node_bin + os.pathsep + env.get("PATH", "")

    # Garante que o fallback seja o próprio modelo testado — falha visível em vez de silenciosa
    if model:
        env["FALLBACK_AGENT_MODEL"] = model

    return env


def find_node_bin_dir() -> Optional[str]:
    """Retorna o diretório do Node.js nvm mais recente >= v22.19, ou None."""
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if not nvm_dir.exists():
        return None
    # Ordena por versão semver decrescente e pega o primeiro que satisfaz o req mínimo
    candidates = []
    for p in nvm_dir.iterdir():
        ver_str = p.name.lstrip("v")
        parts = ver_str.split(".")
        try:
            ver = tuple(int(x) for x in parts)
        except ValueError:
            continue
        if ver >= (22, 19, 0):
            candidates.append((ver, p / "bin"))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return str(candidates[0][1])


def find_openclaw_bin() -> str:
    """Retorna o caminho para o binário openclaw (local ou global)."""
    local_bin = PROJECT_ROOT / "node_modules" / ".bin" / "openclaw"
    if local_bin.exists():
        return str(local_bin)
    return "openclaw"


## Gerenciamento do gateway

def _bench_cfg(config: Dict) -> Dict:
    """Retorna a seção benchmark: do config.yml com fallbacks."""
    return config.get("benchmark", {})

# Fallbacks — valores reais devem estar em config.yml > benchmark:
GATEWAY_PORT_DEFAULT    = 16888
DOCKER_CONTAINER_DEFAULT = "lzbot"
MAX_TASK_RETRIES_DEFAULT = 3
RETRY_SLEEP_DEFAULT      = 60
YIELD_RETRY_DELAY_DEFAULT = 30


def _is_gateway_alive(port: int) -> bool:
    """Verifica se há algo escutando na porta do gateway."""
    try:
        with socket.create_connection(("localhost", port), timeout=3):
            return True
    except OSError:
        return False

def stop_gateway(proc: subprocess.Popen) -> None:
    """Encerra o gateway e todo o grupo de processos filho."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
    logger.info("Gateway encerrado")


## Coleta de tokens e artefatos

# Mensagem de fallback que o openclaw emite quando não consegue entregar a resposta
OPENCLAW_FALLBACK_MSG = "⚠️ Agent couldn't generate a response"
# Respostas que indicam falha do LLM — devem ser tratadas como erro, não como sucesso
OPENCLAW_LLM_ERROR_PREFIXES = (
    "LLM request timed out.",
    "LLM request failed.",
)


def extract_response_from_session(session_key: str) -> str:
    """
    Extrai a última mensagem do assistant do session.jsonl quando o CLI
    não retornou texto (non_deliverable_terminal_turn ou resposta vazia).
    """
    sessions_json = APP_DIR / "agents" / "main" / "sessions" / "sessions.json"
    if not sessions_json.exists():
        return ""
    try:
        with open(sessions_json, "r", encoding="utf-8") as f:
            sessions = json.load(f)
        full_key = f"agent:main:{session_key}".lower()
        entry = sessions.get(full_key) or {}
        session_id = entry.get("sessionId")
        if not session_id:
            return ""
        jsonl = APP_DIR / "agents" / "main" / "sessions" / f"{session_id}.jsonl"
        if not jsonl.exists():
            return ""
        texts = []
        with open(jsonl, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message", {})
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").strip():
                            texts.append(c["text"])
                elif isinstance(content, str) and content.strip():
                    texts.append(content)
        return texts[-1] if texts else ""
    except Exception as e:
        logger.debug("Fallback session read falhou: %s", e)
        return ""


def extract_tokens_from_json(data: Dict) -> Dict:
    """Tenta extrair contagem de tokens do JSON retornado pelo openclaw."""
    # Campos possíveis dependendo da versão e do provider
    usage = (
        data.get("usage")
        or data.get("result", {}).get("usage")
        or data.get("meta", {}).get("usage")
        or {}
    )
    inp = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    out = usage.get("output_tokens", usage.get("completion_tokens", 0))
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}


def extract_tokens_from_sessions() -> Dict:
    """
    Fallback: varre arquivos JSONL de sessão gerados pelo openclaw em app/agents/
    e soma o uso de tokens de todas as chamadas ao LLM.
    """
    agents_dir = APP_DIR / "agents"
    if not agents_dir.exists():
        return {}

    inp = out = 0
    for jsonl_file in agents_dir.rglob("*.jsonl"):
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        usage = (
                            entry.get("usage")
                            or entry.get("result", {}).get("usage")
                            or entry.get("response", {}).get("usage")
                            or {}
                        )
                        inp += usage.get("input_tokens", usage.get("prompt_tokens", 0))
                        out += usage.get("output_tokens", usage.get("completion_tokens", 0))
                    except (json.JSONDecodeError, AttributeError):
                        continue
        except Exception:
            continue

    if inp + out == 0:
        return {}
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}


def collect_artifacts(task: Dict, model_key: str, results_dir: Path, run_sid: str = "") -> List[Dict]:
    """
    Scanneia o output_dir da tarefa, copia os arquivos gerados para
    results/artifacts/<run_sid>/<model_key>/<task_id>/ e retorna metadados de cada arquivo.
    """
    output_dir = task.get("output_dir")
    if not output_dir:
        return []

    # Caminhos relativos são resolvidos a partir de APP_DIR (workspace do agente)
    src = Path(output_dir) if Path(output_dir).is_absolute() else APP_DIR / output_dir
    if not src.exists():
        return []

    dest = results_dir / "artifacts" / run_sid / model_key / task["id"]
    dest.mkdir(parents=True, exist_ok=True)

    artifacts = []
    for f in src.iterdir():
        if not f.is_file():
            continue
        try:
            shutil.copy2(f, dest / f.name)
            suffix = f.suffix.lower().lstrip(".")
            artifacts.append({
                "nome": f.name,
                "tipo": suffix or "bin",
                "tamanho_bytes": f.stat().st_size,
                "caminho_relativo": str(dest.relative_to(results_dir) / f.name),
            })
        except Exception as e:
            logger.warning(f"Não foi possível copiar artefato {f}: {e}")

    return artifacts


def collect_session_files(session_key: str, task_id: str, model_key: str, results_dir: Path, run_sid: str = "") -> List[Dict]:
    """
    Localiza os arquivos de sessão gerados pelo openclaw (JSONL de conversa e trajectory),
    copia para results/artifacts/<run_sid>/<model_key>/<task_id>/session/ e retorna lista de artefatos.
    """
    sessions_json = APP_DIR / "agents" / "main" / "sessions" / "sessions.json"
    if not sessions_json.exists():
        return []

    try:
        with open(sessions_json, "r", encoding="utf-8") as f:
            sessions = json.load(f)
    except Exception as e:
        logger.warning("Não foi possível ler sessions.json: %s", e)
        return []

    # O openclaw armazena com prefixo "agent:main:" e lowercaseia a chave
    full_key = f"agent:main:{session_key}".lower()
    entry = sessions.get(full_key)
    if not entry:
        logger.debug("Sessão não encontrada em sessions.json: %s", full_key)
        return []

    session_id = entry.get("sessionId")
    if not session_id:
        return []

    sessions_dir = APP_DIR / "agents" / "main" / "sessions"
    dest = results_dir / "artifacts" / run_sid / model_key / task_id / "session"
    dest.mkdir(parents=True, exist_ok=True)

    # Copia JSONL de conversa e trajectory; coleta metadados para artefatos
    files_to_copy = {
        f"{session_id}.jsonl": "session.jsonl",
        f"{session_id}.trajectory.jsonl": "trajectory.jsonl",
        f"{session_id}.trajectory-path.json": "trajectory-path.json",
    }
    artifacts = []
    for src_name, dst_name in files_to_copy.items():
        src = sessions_dir / src_name
        if src.exists():
            try:
                shutil.copy2(src, dest / dst_name)
                suffix = Path(dst_name).suffix.lower().lstrip(".")
                artifacts.append({
                    "nome": dst_name,
                    "tipo": suffix or "bin",
                    "tamanho_bytes": src.stat().st_size,
                    "caminho_relativo": str((dest / dst_name).relative_to(results_dir)),
                })
            except Exception as e:
                logger.warning("Não foi possível copiar %s: %s", src_name, e)

    logger.debug("Sessão copiada para %s (%d arquivo(s))", dest, len(artifacts))
    return artifacts


def collect_works_artifacts(session_key: str, task_id: str, model_key: str, results_dir: Path, run_sid: str = "") -> List[Dict]:
    """
    Coleta arquivos gerados pelo agente em workspace/works/<session_key>/ (diretório
    padronizado para runs com --external-gw).  Copia para results/artifacts/<run_sid>/<model>/<task>/works/.
    """
    works_dir = APP_DIR / "workspace" / "works" / session_key
    if not works_dir.exists():
        return []

    dest = results_dir / "artifacts" / run_sid / model_key / task_id / "works"
    dest.mkdir(parents=True, exist_ok=True)

    artifacts = []
    for f in works_dir.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(works_dir)
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(f, dst)
            suffix = f.suffix.lower().lstrip(".")
            artifacts.append({
                "nome": str(rel),
                "tipo": suffix or "bin",
                "tamanho_bytes": f.stat().st_size,
                "caminho_relativo": str(dst.relative_to(results_dir)),
            })
        except Exception as e:
            logger.warning("Não foi possível copiar works artifact %s: %s", f, e)

    if artifacts:
        logger.debug("Works artifacts copiados de %s: %d arquivo(s)", works_dir, len(artifacts))
    return artifacts


## Injeção de arquivos de contexto por task

def run_pre_commands(task: Dict) -> None:
    """Executa comandos de preparação definidos em pre_commands na task config."""
    cmds = task.get("pre_commands", [])
    for cmd in cmds:
        logger.info("pre_command: %s", cmd)
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning("pre_command falhou (rc=%d): %s", result.returncode, result.stderr[:200])
        else:
            logger.debug("pre_command ok: %s", result.stdout.strip()[:100])


def inject_task_files(task: Dict) -> List[Path]:
    """
    Copia os arquivos declarados em context_files da task para app/workspace/.
    Os arquivos devem estar em bench/tasks/<task_id>/<nome>.
    Retorna lista de caminhos copiados para limpeza posterior.
    """
    context_files = task.get("context_files", [])
    if not context_files:
        return []

    src_dir = TASKS_DIR / task["id"]
    workspace = APP_DIR / "workspace"
    copied = []
    for fname in context_files:
        src = src_dir / fname
        if not src.is_file():
            logger.warning("context_file não encontrado: %s", src)
            continue
        dst = workspace / fname
        shutil.copy2(src, dst)
        copied.append(dst)
        logger.debug("Injetado: %s → workspace/%s", fname, fname)

    if copied:
        logger.info("Arquivos da task injetados no workspace: %s", [p.name for p in copied])
    return copied


def cleanup_task_files(injected: List[Path]) -> None:
    """Remove arquivos injetados do workspace após a task terminar."""
    for p in injected:
        try:
            p.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Não foi possível remover %s: %s", p, e)


## Execução de tarefas

def _await_yield_result(
    openclaw: str,
    agent_id: str,
    model: str,
    session_key: str,
    thinking: str,
    timeout: int,
    start_time: float,
    max_retries: int = 8,
    retry_delay: int = YIELD_RETRY_DELAY_DEFAULT,
    external_gw: bool = False,
    docker_container: str = DOCKER_CONTAINER_DEFAULT,
) -> Tuple[str, Dict, int]:
    """
    Quando o agente faz sessions_yield (delegou para sub-agente), re-invoca a sessão
    até receber a resposta final ou esgotar as tentativas.
    Usa o timeout da tarefa como teto total (não ultrapassa timeout * 2).
    Retorna (resposta_texto, tokens_extra, duration_ms_total).
    """
    extra_tokens: Dict = {}
    deadline = start_time + timeout * 2  # teto: no máximo o dobro do timeout da tarefa

    for attempt in range(1, max_retries + 1):
        elapsed = time.time() - start_time
        remaining = deadline - time.time()
        if remaining <= 0:
            logger.warning("  Deadline de yield atingido após %.0fs — abortando", elapsed)
            break

        wait = min(retry_delay, max(5, remaining - timeout))
        logger.info(
            "  sessions_yield detectado — aguardando sub-agente (%.0fs, tentativa %d/%d)…",
            wait, attempt, max_retries,
        )
        time.sleep(wait)

        try:
            agent_args = [
                "openclaw" if external_gw else openclaw, "agent",
                "--agent", agent_id, "--model", model,
                "--session-key", session_key,
                "--message", "pode continuar",
                "--thinking", thinking, "--timeout", str(timeout), "--json",
            ]
            if external_gw:
                yield_cmd = ["docker", "exec", "-e", f"FALLBACK_AGENT_MODEL={model}",
                             "-w", "/app", docker_container] + agent_args
                run_kw: Dict = {}
            else:
                yield_cmd = agent_args
                run_kw = {"cwd": str(APP_DIR), "env": build_env(model=model)}
            r = subprocess.run(
                yield_cmd, capture_output=True, text=True,
                timeout=timeout + 120, **run_kw,
            )
        except Exception as e:
            logger.warning("  Re-invocação falhou: %s", e)
            continue

        if r.returncode != 0 or not r.stdout.strip():
            continue

        try:
            data = json.loads(r.stdout.strip())
            res = data.get("result", data)
            payloads = res.get("payloads", [])
            text = payloads[0].get("text", "").strip() if payloads else ""
            if text:
                # Agrega tokens desta chamada adicional
                usage = res.get("meta", {}).get("agentMeta", {}).get("usage", {})
                if usage:
                    inp = usage.get("input", 0)
                    out = usage.get("output", 0)
                    extra_tokens = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
                duration_ms = int((time.time() - start_time) * 1000)
                logger.info("  Sub-agente retornou após %d tentativa(s)", attempt)
                return text, extra_tokens, duration_ms
        except Exception:
            pass

    logger.warning("  Sub-agente não retornou após %d tentativas (%ds)", max_retries, max_retries * retry_delay)
    return "", {}, int((time.time() - start_time) * 1000)


def run_task(
    task: Dict,
    model: str,
    session_key: str,
    config: Dict,
    use_local: bool = False,
    verbose: bool = False,
    external_gw: bool = False,
    docker_container: str = DOCKER_CONTAINER_DEFAULT,
    yield_retry_delay: int = YIELD_RETRY_DELAY_DEFAULT,
) -> Tuple[Dict, str]:
    """
    Executa uma tarefa no agente openclaw via subprocess e retorna (métricas, resposta).
    Com use_local=True roda embedded sem gateway (sem browser).
    Com verbose=True o stderr do agente é exibido em tempo real no terminal.
    Com external_gw=True executa via `docker exec` dentro do container para evitar
    problemas de auth de scope com gateways externos.
    """
    agent_id = config.get("agent", {}).get("id", "main")
    thinking = config.get("agent", {}).get("thinking", "low")
    # Timeout por tarefa tem prioridade sobre o global
    timeout = int(task.get("timeout", config.get("agent", {}).get("timeout", 600)))

    # Substitui {works_dir} pelo caminho real da sessão antes de enviar ao agente
    works_dir = f"workspace/works/{session_key}"
    prompt = task["prompt"].replace("{works_dir}", works_dir)

    agent_cmd = [
        "openclaw", "agent",
        "--agent", agent_id,
        "--channel", "telegram",
        "--to", "0",
        "--model", model,
        "--session-key", session_key,
        "--message", prompt,
        "--thinking", thinking,
        "--timeout", str(timeout),
        "--json",
    ]

    if use_local:
        agent_cmd.append("--local")

    if verbose:
        agent_cmd.extend(["--verbose", "on"])

    # Com gateway externo (Docker), executa dentro do container via docker exec para
    # evitar problemas de scope de autenticação ao conectar de fora
    if external_gw:
        cmd = [
            "docker", "exec",
            "-e", f"FALLBACK_AGENT_MODEL={model}",
            "-w", "/app",
            docker_container,
        ] + agent_cmd
    else:
        openclaw = find_openclaw_bin()
        agent_cmd[0] = openclaw
        cmd = agent_cmd

    start_time = time.time()

    try:
        if verbose:
            # stderr flui direto para o terminal; stdout (JSON) é capturado
            proc_kwargs: Dict = {"stdout": subprocess.PIPE, "stderr": None, "text": True}
            if not external_gw:
                proc_kwargs["cwd"] = str(APP_DIR)
                proc_kwargs["env"] = build_env(model=model)
            proc = subprocess.Popen(cmd, **proc_kwargs)
            try:
                stdout_data, _ = proc.communicate(timeout=timeout + 120)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return {"erro": f"Timeout após {timeout + 120}s"}, ""
            returncode = proc.returncode
            stderr_data = ""
        else:
            run_kwargs: Dict = {"capture_output": True, "text": True, "timeout": timeout + 120}
            if not external_gw:
                run_kwargs["cwd"] = str(APP_DIR)
                run_kwargs["env"] = build_env(model=model)
            result = subprocess.run(cmd, **run_kwargs)
            stdout_data = result.stdout
            stderr_data = result.stderr
            returncode = result.returncode

        wall_time = time.time() - start_time

        if returncode != 0:
            err_snippet = stderr_data[-500:] if stderr_data else stdout_data[-500:]
            return {"erro": f"Exit {returncode}: {err_snippet}"}, ""

        stdout = stdout_data.strip()
        if not stdout:
            err_snippet = stderr_data[-300:] if stderr_data else ""
            return {"erro": f"Resposta vazia. stderr: {err_snippet}"}, ""

        data = json.loads(stdout)

        # Estrutura do gateway: {result: {payloads, meta: {durationMs, agentMeta: {usage}}}}
        result = data.get("result", data)

        payloads = result.get("payloads", [])
        response_text = payloads[0].get("text", "") if payloads else ""

        meta = result.get("meta", {})
        # durationMs vem do gateway; fallback para tempo medido localmente
        duration_ms = meta.get("durationMs", int(wall_time * 1000))

        # Detecta sessions_yield: resposta vazia com exit 0 = agente delegou para sub-agente
        if not response_text:
            openclaw_bin = find_openclaw_bin()
            agent_id_cfg = config.get("agent", {}).get("id", "main")
            thinking_cfg = config.get("agent", {}).get("thinking", "low")
            timeout_cfg = int(task.get("timeout", config.get("agent", {}).get("timeout", 600)))
            # Retries proporcionais ao timeout: no mínimo 2, máximo 8 (30s cada)
            yield_retries = max(2, min(8, timeout_cfg // 60))
            yield_text, extra_tok, yield_dur = _await_yield_result(
                openclaw_bin, agent_id_cfg, model, session_key,
                thinking_cfg, timeout_cfg, start_time,
                max_retries=yield_retries,
                retry_delay=yield_retry_delay,
                external_gw=external_gw,
                docker_container=docker_container,
            )
            if yield_text:
                response_text = yield_text
                duration_ms = yield_dur
                if extra_tok:
                    # Será mesclado abaixo quando tokens for construído
                    meta["_extra_tokens"] = extra_tok
            # Recalcula wall_time incluindo o tempo de espera pelo sub-agente
            wall_time = time.time() - start_time

        # Tokens em result.meta.agentMeta.usage com chaves input/output/total
        agent_meta = meta.get("agentMeta", {})
        usage = agent_meta.get("usage", {})
        if usage:
            inp = usage.get("input", usage.get("input_tokens", usage.get("prompt_tokens", 0)))
            out = usage.get("output", usage.get("output_tokens", usage.get("completion_tokens", 0)))
            tokens = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
        else:
            tokens = extract_tokens_from_sessions()
        # Soma tokens de re-invocações por yield
        extra = meta.get("_extra_tokens", {})
        if extra:
            tokens["input_tokens"] += extra.get("input_tokens", 0)
            tokens["output_tokens"] += extra.get("output_tokens", 0)
            tokens["total_tokens"] += extra.get("total_tokens", 0)

        metrics = {
            "duration_ms": duration_ms,
            "wall_time_s": round(wall_time, 2),
            "transport": data.get("meta", {}).get("transport", "local"),
            "tokens": tokens,
        }

        # LLM error: timeout, provider failed, etc. — não deve ser tratado como sucesso
        if any(response_text.strip().startswith(p) for p in OPENCLAW_LLM_ERROR_PREFIXES):
            logger.warning("  Erro LLM detectado (%s) — marcando como erro", response_text.strip()[:60])
            metrics["erro"] = response_text.strip()
            return metrics, ""

        # Fallback: resposta vazia ou mensagem de erro do openclaw → tenta session JSONL
        if not response_text or response_text.startswith(OPENCLAW_FALLBACK_MSG):
            fallback = extract_response_from_session(session_key)
            if fallback:
                logger.info("  Resposta recuperada do session.jsonl")
                response_text = fallback

        return metrics, response_text

    except json.JSONDecodeError as e:
        snippet = stdout_data[:400] if stdout_data else ""
        return {"erro": f"JSON inválido: {e} | stdout: {snippet}"}, ""
    except Exception as e:
        return {"erro": str(e)}, ""


## Persistência de resultados

def _build_meta(config: Dict, tasks: List[Dict]) -> Dict:
    """
    Monta o bloco meta embutido no benchmark.json para o viewer se auto-montar.
    Cada tarefa declara seu grupo via campo 'group'; aqui agrupamos e populamos taskNames.
    """
    # Agrupa tarefas por group key preservando a ordem de aparição no config.yml
    tasks_by_group: Dict[str, List[Dict]] = {}
    for t in tasks:
        gk = t.get("group", "")
        tasks_by_group.setdefault(gk, []).append(t)

    groups_meta = []
    for g in config.get("groups", []):
        group_tasks = tasks_by_group.get(g["key"], [])
        groups_meta.append({
            "key":       g["key"],
            "label":     g["label"],
            "icon":      g.get("icon", ""),
            "color":     g.get("color", "#888888"),
            "diag":      bool(g.get("diag", False)),
            "tasks":     [t["id"] for t in group_tasks],
            "taskNames": {t["id"]: t["name"] for t in group_tasks},
        })

    tasks_meta = {t["id"]: {"name": t["name"], "category": t.get("category", "")} for t in tasks}
    return {"groups": groups_meta, "tasks": tasks_meta}


def initialize_run(
    models: List[str],
    tasks: List[Dict],
    run_timestamp: str,
    run_sid: str,
    config: Optional[Dict] = None,
) -> None:
    """
    Pré-popula benchmark.json com entradas vazias para todos os modelos × tarefas
    antes de qualquer execução, permitindo ao viewer exibir o progresso em tempo real.
    Entradas existentes (de runs anteriores) não são alteradas.
    Embute meta.groups e meta.tasks para o viewer derivar grupos dinamicamente.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if BENCHMARK_FILE.exists():
        with open(BENCHMARK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"modelos": {}}

    # Atualiza meta com a configuração atual (sempre sobrescreve — reflete a config vigente)
    if config is not None:
        data["meta"] = _build_meta(config, tasks)

    for model in models:
        model_key = model.replace("/", "_").replace(":", "_")
        if model_key not in data["modelos"]:
            data["modelos"][model_key] = {"model_id": model, "runs": []}

        # Evita criar run duplicada (idempotente)
        run = next(
            (r for r in data["modelos"][model_key]["runs"] if r["timestamp"] == run_timestamp),
            None,
        )
        if run is None:
            run = {"timestamp": run_timestamp, "sid": run_sid, "respostas": {}}
            data["modelos"][model_key]["runs"].append(run)

        # Cria placeholder para cada tarefa — sobrescrito por save_result() quando concluir
        for task in tasks:
            if task["id"] not in run["respostas"]:
                run["respostas"][task["id"]] = {
                    "nome": task["name"],
                    "categoria": task.get("category", ""),
                    "prompt": task.get("prompt", "").strip(),
                    "prompt_analise": task.get("evaluation", "").strip(),
                    "resposta": None,
                    "metricas": {"pendente": True},
                    "artefatos": [],
                }

    with open(BENCHMARK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("benchmark.json inicializado: %d modelos × %d tarefas [%s]", len(models), len(tasks), run_sid)


def save_result(
    task: Dict,
    model: str,
    run_timestamp: str,
    metrics: Dict,
    response: str,
    artifacts: List[Dict],
) -> None:
    """Salva resultado de uma tarefa em results/benchmark.json."""
    model_key = model.replace("/", "_").replace(":", "_")

    if BENCHMARK_FILE.exists():
        with open(BENCHMARK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"modelos": {}}

    if model_key not in data["modelos"]:
        data["modelos"][model_key] = {"model_id": model, "runs": []}

    current_run = next(
        (r for r in data["modelos"][model_key]["runs"] if r["timestamp"] == run_timestamp),
        None,
    )
    if current_run is None:
        current_run = {"timestamp": run_timestamp, "sid": short_id(run_timestamp), "respostas": {}}
        data["modelos"][model_key]["runs"].append(current_run)

    current_run["respostas"][task["id"]] = {
        "nome": task["name"],
        "categoria": task.get("category", ""),
        "prompt": task.get("prompt", "").strip(),
        "prompt_analise": task.get("evaluation", "").strip(),
        "resposta": response,
        "metricas": metrics,
        "artefatos": artifacts,
    }

    with open(BENCHMARK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


## Avaliação LLM dos resultados

def _call_analysis_model(model_id: str, prompt: str, api_key: str) -> str:
    """Chama a API do Gemini diretamente e retorna o texto da resposta."""
    import urllib.request, urllib.error

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{model_id}:generateContent?key={api_key}"
    )
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0},
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    return result["candidates"][0]["content"]["parts"][0]["text"]


def _extract_json(text: str) -> Dict:
    """Extrai o primeiro bloco JSON válido do texto."""
    # Tenta parse direto
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # Tenta extrair de bloco ```json ... ```
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Tenta achar qualquer {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Nenhum JSON válido encontrado: {text[:200]}")


def extract_session_summary(model_key: str, task_id: str, run_sid: str = "", max_chars: int = 6000) -> str:
    """
    Lê o session.jsonl salvo em artifacts e retorna um resumo das tool calls/results
    para enriquecer o contexto do avaliador LLM.
    """
    session_jsonl = RESULTS_DIR / "artifacts" / run_sid / model_key / task_id / "session" / "session.jsonl"
    if not session_jsonl.exists():
        return ""

    entries = []
    try:
        with open(session_jsonl, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return ""

    lines = []
    for entry in entries:
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")

            if btype == "tool_use" and role == "assistant":
                name = block.get("name", "?")
                inp = block.get("input", {})
                # Resume o input: omite valores muito longos
                inp_summary = json.dumps(
                    {k: (v[:120] + "…" if isinstance(v, str) and len(v) > 120 else v)
                     for k, v in (inp.items() if isinstance(inp, dict) else {})},
                    ensure_ascii=False,
                )
                lines.append(f"[tool_use] {name} {inp_summary}")

            elif btype == "tool_result" and role == "user":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    # Pega só blocos de texto
                    texts = [c.get("text", "") for c in result_content if isinstance(c, dict) and c.get("type") == "text"]
                    result_content = " ".join(texts)
                snippet = str(result_content)[:300]
                if len(str(block.get("content", ""))) > 300:
                    snippet += "…"
                lines.append(f"[tool_result] {snippet}")

    summary = "\n".join(lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "\n…[truncado]"
    return summary


def evaluate_results(run_id: str | None = None, model_filter: str | None = None, force: bool = False) -> None:
    """
    Roda avaliação LLM para todas as tarefas não-diagnóstico sem avaliação ainda.
    run_id: limita a uma run pelo short_id.
    model_filter: limita a modelos cujo id contenha essa substring.
    force: reavalia mesmo tarefas já avaliadas.
    """
    if not BENCHMARK_FILE.exists():
        return

    config = load_config()
    task_map = {t["id"]: t for t in config.get("tasks", [])}
    analysis_model = config.get("analysis", {}).get("model", "gemini-3.1-pro-preview")

    env = {**os.environ}
    if ENV_FILE.exists():
        from dotenv import dotenv_values
        env.update(dotenv_values(ENV_FILE))
    api_key = env.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("GEMINI_API_KEY não encontrada — avaliação ignorada")
        return

    with open(BENCHMARK_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Resolve o timestamp alvo quando um short_id for especificado
    target_ts: str | None = None
    if run_id:
        target_sid = run_id.upper()
        all_ts = {
            run.get("timestamp", "")
            for mv in data["modelos"].values()
            for run in mv["runs"]
        }
        target_ts = next((ts for ts in all_ts if short_id(ts) == target_sid), None)
        if not target_ts:
            logger.error(f"Run com ID '{target_sid}' não encontrada. Use 'python manage_benchmark.py list' para ver os IDs.")
            return
        logger.info(f"Filtrando avaliação para run [{target_sid}] {target_ts[:19]}")

    def _in_scope(model_id: str, run: dict) -> bool:
        if target_ts and run.get("timestamp") != target_ts:
            return False
        if model_filter and model_filter not in model_id:
            return False
        return True

    # Remove avaliações existentes antes de reavaliar
    if force:
        cleared = 0
        for mv in data["modelos"].values():
            for run in mv["runs"]:
                if not _in_scope(mv["model_id"], run):
                    continue
                for tv in run["respostas"].values():
                    if "avaliacao" in tv:
                        del tv["avaliacao"]
                        cleared += 1
        if cleared:
            logger.info(f"{cleared} avaliação(ões) removida(s) antes de reavaliar.")
            with open(BENCHMARK_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def _should_eval(model_id: str, run: dict, tid: str, tv: dict) -> bool:
        if not _in_scope(model_id, run):
            return False
        return (
            "avaliacao" not in tv
            and tv.get("categoria", "") != "Diagnóstico"
            and "erro" not in tv.get("metricas", {})
            and task_map.get(tid, {}).get("evaluation")
        )

    total_pending = sum(
        1
        for mv in data["modelos"].values()
        for run in mv["runs"]
        for tid, tv in run["respostas"].items()
        if _should_eval(mv["model_id"], run, tid, tv)
    )

    if total_pending == 0:
        logger.info("Nenhuma tarefa pendente de avaliação.")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"Avaliação LLM — {total_pending} tarefa(s) pendentes")
    logger.info(f"Modelo: {analysis_model}")
    logger.info(f"{'='*60}")

    done = 0
    for model_key, mv in data["modelos"].items():
        for run in mv["runs"]:
            for tid, tv in run["respostas"].items():
                if not _should_eval(mv["model_id"], run, tid, tv):
                    continue
                task_cfg = task_map.get(tid)

                done += 1
                resposta = tv.get("resposta", "")
                artifacts = tv.get("artefatos", [])
                run_sid = short_id(run.get("timestamp", ""))
                session_summary = extract_session_summary(model_key, tid, run_sid=run_sid)

                # Artefatos gerados — evidência objetiva de criação de arquivos
                artifacts_section = ""
                if artifacts:
                    artifact_lines = "\n".join(
                        f"- {a['nome']} ({a['tipo']}, {a['tamanho_bytes']} bytes)"
                        for a in artifacts
                    )
                    artifacts_section = f"\n\n## Artefatos Gerados pelo Agente\n{artifact_lines}"

                session_section = (
                    f"\n\n## Execução da Sessão (tool calls e resultados)\n{session_summary}"
                    if session_summary else ""
                )
                eval_prompt = (
                    f"Avalie a seguinte resposta de um agente de IA.\n\n"
                    f"IMPORTANTE: O agente opera em modo ferramenta — ele salva arquivos em disco "
                    f"e reporta os caminhos. A lista de Artefatos Gerados abaixo é a evidência "
                    f"objetiva de criação de arquivos; considere-a como prova de entrega mesmo "
                    f"quando o texto da resposta apenas menciona o caminho do arquivo.\n\n"
                    f"## Resposta do Agente\n{resposta}"
                    f"{artifacts_section}"
                    f"{session_section}\n\n"
                    f"## Critério de Avaliação\n{task_cfg['evaluation']}\n\n"
                    f"## Formato de Saída\n"
                    f"Retorne APENAS um objeto JSON válido, sem texto adicional, no seguinte formato:\n"
                    f'{{"nota_geral": <inteiro de 0 a 10>, '
                    f'"criterios_atendidos": ["descrição do critério atendido", ...], '
                    f'"criterios_faltando": ["descrição do critério não atendido", ...], '
                    f'"justificativa": "<resumo objetivo da avaliação>"}}'
                )

                try:
                    logger.info(f"  [{done}/{total_pending}] {mv['model_id']} / {tid}")
                    raw = _call_analysis_model(analysis_model, eval_prompt, api_key)
                    tv["avaliacao"] = _extract_json(raw)
                    nota = tv["avaliacao"].get("nota_geral", "?")
                    logger.info(f"    nota: {nota}")
                except Exception as e:
                    logger.warning(f"    falha na avaliação ({tid}): {e}")

                # Salva após cada avaliação para não perder em caso de erro
                with open(BENCHMARK_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Avaliação concluída.")


## Ponto de entrada

def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark de agentes OpenClaw")
    parser.add_argument(
        "--local", action="store_true",
        help="Modo embedded sem gateway — sem browser, sem recursos do gateway"
    )
    parser.add_argument(
        "--external-gw", action="store_true",
        help="Usa gateway já em execução externamente (não sobe nem derruba)"
    )
    parser.add_argument("--model", type=str, help="Testa apenas este modelo (substring do id); também filtra avaliação com --eval-only")
    parser.add_argument("--task", type=str, help="Testa apenas estas tarefas (id exato; vírgula para múltiplas, na ordem informada)")
    parser.add_argument("--eval-only", nargs="?", const=True, metavar="ID", help="Só roda avaliação LLM no benchmark.json existente; passa ID para limitar a uma run específica")
    parser.add_argument("--force", action="store_true", help="Com --eval-only, força reavaliação mesmo de tarefas já avaliadas")
    parser.add_argument("-v", "--verbose", action="store_true", help="Exibe o output do agente em tempo real")
    args = parser.parse_args()

    if args.eval_only is not None:
        run_id = args.eval_only if isinstance(args.eval_only, str) else None
        evaluate_results(run_id=run_id, model_filter=args.model, force=args.force)
        return

    config = load_config()
    bench = _bench_cfg(config)
    gateway_port     = bench.get("gateway_port",      GATEWAY_PORT_DEFAULT)
    docker_container = bench.get("docker_container",  DOCKER_CONTAINER_DEFAULT)
    max_task_retries = bench.get("max_task_retries",  MAX_TASK_RETRIES_DEFAULT)
    retry_sleep      = bench.get("retry_sleep_s",     RETRY_SLEEP_DEFAULT)
    yield_retry_delay = bench.get("yield_retry_delay_s", YIELD_RETRY_DELAY_DEFAULT)

    if args.external_gw:
        if not _is_gateway_alive(gateway_port):
            logger.error(
                "Gateway externo não encontrado na porta %d — verifique se o Docker está rodando",
                gateway_port,
            )
            sys.exit(1)
        logger.info("Gateway externo detectado em localhost:%d", gateway_port)

    models: List[str] = config.get("models", [])
    if args.model:
        models = [m for m in models if args.model in m] or [args.model]

    tasks: List[Dict] = config.get("tasks", [])
    if args.task:
        # Preserva a ordem exata informada pelo usuário
        task_ids = [tid.strip() for tid in args.task.split(",") if tid.strip()]
        task_map_cfg = {t["id"]: t for t in tasks}
        tasks = [task_map_cfg[tid] for tid in task_ids if tid in task_map_cfg]

    if not models:
        logger.error("Nenhum modelo encontrado no config.yml")
        sys.exit(1)
    if not tasks:
        logger.error("Nenhuma tarefa encontrada no config.yml")
        sys.exit(1)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    use_local = args.local

    if use_local:
        mode_label = "embedded (--local, sem gateway)"
    elif args.external_gw:
        mode_label = "gateway externo"
    else:
        mode_label = "gateway gerenciado"

    run_timestamp = datetime.now().isoformat()
    run_sid = short_id(run_timestamp)

    logger.info(f"Run: {run_timestamp} [{run_sid}]")
    logger.info(f"Modelos: {len(models)} | Tarefas: {len(tasks)} | Modo: {mode_label}")

    # Pré-popula benchmark.json para o viewer mostrar o progresso desde o início
    initialize_run(models, tasks, run_timestamp, run_sid, config=config)

    total = len(models) * len(tasks)
    done = 0

    for model in models:
        model_key = model.replace("/", "_").replace(":", "_")
        model_slug = model_key[:24]
        logger.info(f"\n{'='*60}\nModelo: {model}\n{'='*60}")

        # Reseta app/ para garantir estado limpo por modelo; hardcoda modelo para subagentes
        setup_workspace(args.external_gw, model=model, config=config)

        for task in tasks:
            done += 1
            ts_safe = run_timestamp[:19].replace(":", "-")
            session_key = f"bench-{ts_safe}-{model_slug}-{task['id']}"

            # Limpa output_dir da tarefa anterior (evita contaminação entre tasks)
            output_dir = task.get("output_dir")
            if output_dir:
                p = Path(output_dir)
                if p.exists():
                    shutil.rmtree(p)

            # Limpa workspace/works/ para evitar resquícios de tasks anteriores
            works_dir = APP_DIR / "workspace" / "works"
            if works_dir.exists():
                shutil.rmtree(works_dir)
            works_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"[{done}/{total}] {task['name']} ({task.get('category', '')})")

            # Roda comandos de preparação da task (ex: remove fli, limpa estado)
            run_pre_commands(task)

            # Injeta arquivos de contexto específicos desta task no workspace
            injected = inject_task_files(task)

            metrics: Dict = {}
            response: str = ""
            artifacts: List[Dict] = []

            for attempt in range(1, max_task_retries + 1):
                if attempt > 1:
                    logger.info("  Retentativa %d/%d após erro…", attempt, max_task_retries)
                    time.sleep(retry_sleep)

                try:
                    metrics, response = run_task(
                        task, model, session_key, config,
                        use_local=use_local, verbose=args.verbose,
                        external_gw=args.external_gw,
                        docker_container=docker_container,
                        yield_retry_delay=yield_retry_delay,
                    )
                finally:
                    cleanup_task_files(injected)

                # Limpa artefatos de tentativas anteriores para não contaminar a coleta atual
                artifact_base = RESULTS_DIR / "artifacts" / run_sid / model_key / task["id"]
                if artifact_base.exists():
                    shutil.rmtree(artifact_base)

                # Coleta artefatos desta tentativa
                artifacts = collect_artifacts(task, model_key, RESULTS_DIR, run_sid=run_sid)
                session_arts = collect_session_files(session_key, task["id"], model_key, RESULTS_DIR, run_sid=run_sid)
                artifacts.extend(session_arts)
                if args.external_gw:
                    works_arts = collect_works_artifacts(session_key, task["id"], model_key, RESULTS_DIR, run_sid=run_sid)
                    artifacts.extend(works_arts)

                if "erro" not in metrics:
                    break  # sucesso — sai do loop de retry

                logger.warning("  Tentativa %d falhou: %s", attempt, metrics["erro"][:120])
                if attempt < max_task_retries:
                    # Re-injeta arquivos de contexto para a próxima tentativa
                    injected = inject_task_files(task)

            if "erro" in metrics:
                logger.error(f"  ✗ {metrics['erro'][:120]}")
            else:
                wall = metrics.get("wall_time_s", 0)
                llm  = metrics.get("duration_ms", 0) / 1000
                tok  = metrics.get("tokens", {}).get("total_tokens", "?")
                art  = f" | {len(artifacts)} artefato(s)" if artifacts else ""
                retry_label = f" [tentativa {attempt}]" if attempt > 1 else ""
                # Alerta quando wall time >> LLM duration (indica sessions_yield ativo)
                if wall > llm * 3 and wall > 30:
                    logger.warning(f"  ✓ {wall:.1f}s parede | {llm:.1f}s LLM | tokens: {tok}{art}{retry_label} [yield detectado]")
                else:
                    logger.info(f"  ✓ {wall:.1f}s | tokens: {tok}{art}{retry_label}")

            save_result(task, model, run_timestamp, metrics, response, artifacts)

        # Pausa entre modelos para evitar rate limit
        time.sleep(3)

    evaluate_results()

    logger.info(f"\n{'='*60}")
    logger.info("Benchmark concluído!")
    logger.info(f"Resultados em: {RESULTS_DIR}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
