#!/usr/bin/env python3
"""
CLI para gerenciar runs do benchmark.json.

Uso:
  python manage_benchmark.py list
  python manage_benchmark.py remove
  python manage_benchmark.py rename
"""

import json
import sys
import hashlib
import shutil
from pathlib import Path

BENCHMARK_FILE = Path(__file__).parent / "results" / "benchmark.json"
ARTIFACTS_DIR = Path(__file__).parent / "results" / "artifacts"


def short_id(timestamp: str) -> str:
    return hashlib.md5(timestamp.encode()).hexdigest()[:4].upper()


def load() -> dict:
    if not BENCHMARK_FILE.exists():
        print(f"Erro: {BENCHMARK_FILE} não encontrado. Rode benchmark.py primeiro.")
        sys.exit(1)
    with open(BENCHMARK_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save(data: dict) -> None:
    with open(BENCHMARK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Salvo em {BENCHMARK_FILE}")


## Listar runs

def list_runs(data: dict) -> dict:
    """Exibe todas as runs e retorna mapa {short_id: timestamp}."""
    # Coleta todos os timestamps únicos
    all_ts: set = set()
    for model_data in data.get("modelos", {}).values():
        for run in model_data.get("runs", []):
            all_ts.add(run.get("timestamp", "unknown"))

    ts_to_id = {ts: short_id(ts) for ts in sorted(all_ts)}

    print("\n=== RUNS DISPONÍVEIS ===\n")
    for model_key, model_data in data.get("modelos", {}).items():
        runs = model_data.get("runs", [])
        if not runs:
            continue
        print(f"Modelo: {model_key}")
        for run in runs:
            ts = run.get("timestamp", "unknown")
            sid = ts_to_id.get(ts, "????")
            nome = run.get("nome", ts[:19])
            analyzed = "✓ analisado" if run.get("analyzed") else "○ pendente"
            n_respostas = len(run.get("respostas", {}))
            print(f"  [{sid}] {nome}  ({ts[:19]})  —  {n_respostas} tarefas  {analyzed}")
        print()

    return {v: k for k, v in ts_to_id.items()}  # {short_id: timestamp}


## Remover run

def remove_run(data: dict, target_sid: str | None = None, all_models: bool = False) -> None:
    id_to_ts = list_runs(data)

    print("=== REMOVER RUN ===\n")

    if target_sid:
        sid = target_sid.upper()
    else:
        sid = input("ID da run para remover (ou 'c' para cancelar): ").strip().upper()
        if sid == "C":
            print("Cancelado.")
            return

    ts = id_to_ts.get(sid)
    if not ts:
        print(f"ID '{sid}' não encontrado.")
        return

    print(f"\nRun selecionada: [{sid}] {ts[:19]}")

    if all_models:
        target_models = None  # todos, sem perguntar
    else:
        print("1. Remover de todos os modelos")
        print("2. Remover de modelos específicos")
        choice = input("Opção (1 ou 2): ").strip()

        if choice == "2":
            models_available = list(data.get("modelos", {}).keys())
            for i, m in enumerate(models_available):
                print(f"  [{i}] {m}")
            raw = input("Índices dos modelos (vírgula): ").strip()
            try:
                indices = [int(x) for x in raw.split(",")]
                target_models = {models_available[i] for i in indices if 0 <= i < len(models_available)}
            except ValueError:
                print("Índices inválidos.")
                return
        else:
            target_models = None

        confirm = input(f"\nRemover [{sid}] de {'todos' if not target_models else str(target_models)}? (s/n): ").strip().lower()
        if confirm != "s":
            print("Cancelado.")
            return

    removed = 0
    for model_key, model_data in data.get("modelos", {}).items():
        if target_models and model_key not in target_models:
            continue
        before = len(model_data.get("runs", []))
        model_data["runs"] = [r for r in model_data.get("runs", []) if r.get("timestamp") != ts]
        removed += before - len(model_data["runs"])

    print(f"{removed} run(s) removida(s).")
    save(data)

    # Remove artefatos da run (só quando removida de todos os modelos)
    if not target_models:
        artifact_run_dir = ARTIFACTS_DIR / sid
        if artifact_run_dir.exists():
            shutil.rmtree(artifact_run_dir)
            print(f"Artefatos de [{sid}] removidos: {artifact_run_dir}")


## Renomear run

def rename_run(data: dict) -> None:
    id_to_ts = list_runs(data)

    print("=== RENOMEAR RUN ===\n")
    sid = input("ID da run para renomear (ou 'c' para cancelar): ").strip().upper()
    if sid == "C":
        print("Cancelado.")
        return

    ts = id_to_ts.get(sid)
    if not ts:
        print(f"ID '{sid}' não encontrado.")
        return

    novo_nome = input("Novo nome: ").strip()
    if not novo_nome:
        print("Nome inválido.")
        return

    updated = 0
    for model_data in data.get("modelos", {}).values():
        for run in model_data.get("runs", []):
            if run.get("timestamp") == ts:
                run["nome"] = novo_nome
                updated += 1

    print(f"{updated} run(s) renomeada(s) para '{novo_nome}'.")
    save(data)


## Remover avaliação

def uneval_run(data: dict, target_sid: str | None) -> None:
    # Coleta todos os timestamps e monta mapa {short_id: timestamp}
    all_ts: set = set()
    for model_data in data.get("modelos", {}).values():
        for run in model_data.get("runs", []):
            all_ts.add(run.get("timestamp", ""))

    ts_to_id = {ts: short_id(ts) for ts in sorted(all_ts) if ts}
    id_to_ts = {v: k for k, v in ts_to_id.items()}

    if target_sid:
        ts = id_to_ts.get(target_sid.upper())
        if not ts:
            print(f"ID '{target_sid}' não encontrado. Use 'list' para ver os IDs disponíveis.")
            sys.exit(1)
    else:
        # Último run = maior timestamp
        if not all_ts:
            print("Nenhuma run encontrada.")
            sys.exit(1)
        ts = max(all_ts)
        sid = ts_to_id[ts]
        print(f"Usando último run: [{sid}] {ts[:19]}")

    removed = 0
    for model_data in data.get("modelos", {}).values():
        for run in model_data.get("runs", []):
            if run.get("timestamp") != ts:
                continue
            for task in run.get("respostas", {}).values():
                if "avaliacao" in task:
                    del task["avaliacao"]
                    removed += 1

    if removed == 0:
        print("Nenhuma avaliação encontrada nessa run.")
        return

    print(f"{removed} avaliação(ões) removida(s).")
    save(data)


## Main

def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python manage_benchmark.py <comando>")
        print("\nComandos:")
        print("  list               — lista todas as runs")
        print("  remove [--all] [ID]  — remove uma run (--all pula confirmação e remove de todos os modelos)")
        print("  rename             — renomeia uma run")
        print("  uneval [ID]        — remove avaliações do último run (ou do run especificado)")
        sys.exit(1)

    data = load()
    cmd = sys.argv[1].lower()
    extra_args = sys.argv[2:]

    if cmd == "list":
        list_runs(data)
    elif cmd == "remove":
        # Suporte a: remove --all ID  ou  remove ID --all  ou  remove (interativo)
        all_flag = "--all" in extra_args
        ids = [a for a in extra_args if not a.startswith("-")]
        target_sid = ids[0] if ids else None
        remove_run(data, target_sid=target_sid, all_models=all_flag)
    elif cmd == "rename":
        rename_run(data)
    elif cmd == "uneval":
        target = extra_args[0] if extra_args else None
        uneval_run(data, target)
    else:
        print(f"Comando desconhecido: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
