#!/usr/bin/env python3
"""housekeeping — limpeza de lixo acumulado, restrita a alvos declarados.

Duas regras estruturais, nesta ordem:

1. `--dry-run` é o default. `scan` e `clean` sem `--apply` só relatam. Apagar
   exige `--apply` explícito, porque limpeza errada aqui custa horas de
   re-embed em CPU-only.
2. Allowlist, não denylist. A skill não varre disco procurando "o que parece
   lixo": só o que está declarado no manifesto é candidato. Em cima disso há
   uma guarda dura de caminhos protegidos que recusa e aborta o alvo inteiro.
"""

import argparse
import fnmatch
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("erro: pyyaml não instalado (pip3 install --break-system-packages pyyaml)", file=sys.stderr)
    sys.exit(2)


## Caminhos

# Manifesto e histórico da instância vivem fora do repo da skill.
WORKSPACE_ROOT = Path(os.environ.get('LZBOT_WORKSPACE_ROOT', str(Path.cwd())))
DATA_DIR = Path(os.environ.get('HOUSEKEEPING_DATA_DIR',
                               str(WORKSPACE_ROOT / 'skills' / 'housekeeping')))
MANIFEST_PATH = DATA_DIR / 'manifest.yml'
STATE_PATH = DATA_DIR / 'state.json'

KINDS = ('paths', 'age', 'retention', 'command')


## Guarda dura: o que nunca pode ser removido

# Não é documentação, é execução: um alvo que resolva para dentro (ou para cima)
# de qualquer um destes aborta antes de tocar em nada.
PROTECTED = (
    '/app/models',                                     # GGUF do embedding; re-download é caro
    '/app/workspace/memory',
    '/app/workspace/MEMORY.md',
    '/app/workspace/secrets',
    '/app/workspace/skills',                           # DBs e manifestos de instância
    '/app/dev',                                        # repositórios git
    '/app/.ssh',
    '/app/agents.json5',
    '/app/openclaw.json',
)

# Única brecha na guarda, e deliberada: o alvo de retenção precisa entrar em
# /app/workspace/skills/<skill>/backups/<alvo>/<snapshot>. Vale SÓ para o
# snapshot individual — nem o `backups/`, nem o `backups/<alvo>/` inteiro, nem
# qualquer outro caminho sob `skills/`. Glob mal escrito no manifesto para
# `backups/*` bate na guarda, não nos backups.
PROTECTED_EXCEPTIONS = (
    '/app/workspace/skills/*/backups/*/*',
)


def _norm(path):
    """Resolve o caminho sem seguir o último componente.

    Symlink candidato deve ser removido como link, não como alvo — resolver o
    fim faria a guarda avaliar o destino e a remoção apagar outra coisa.
    """
    p = Path(path)
    try:
        parent = p.parent.resolve()
    except OSError:
        parent = p.parent
    return str(parent / p.name)


def _is_inside(child, parent):
    return child == parent or child.startswith(parent.rstrip('/') + '/')


def guard(path):
    """Devolve o padrão protegido violado, ou None se o caminho é liberado."""
    p = _norm(path)

    if p in ('/', '/app', '/home', '/home/lzbot', '/tmp', '/app/workspace'):
        return f'raiz protegida ({p})'

    excepted = any(fnmatch.fnmatch(p, pat) for pat in PROTECTED_EXCEPTIONS)

    for pattern in PROTECTED:
        # Match direto contra o padrão declarado: pega também caminho protegido
        # que ainda não existe (index.sqlite* de um agente sem índice).
        if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(p, pattern.rstrip('/') + '/*'):
            if excepted:
                continue
            return pattern
        for real in glob.glob(pattern):
            real = _norm(real)
            # Candidato dentro de protegido: a exceção pode liberar.
            if _is_inside(p, real):
                if excepted:
                    continue
                return pattern
            # Candidato é ancestral de protegido: nunca liberado. Remover
            # /app/workspace levaria memory/ junto.
            if _is_inside(real, p):
                return pattern
    return None


## Infra

def run(cmd, env=None, shell=False):
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, shell=shell)
    return proc.returncode, proc.stdout, proc.stderr


def die(msg, code=2):
    print(f"erro: {msg}", file=sys.stderr)
    sys.exit(code)


def human(n):
    for unit in ('B', 'K', 'M', 'G', 'T'):
        if abs(n) < 1024 or unit == 'T':
            return f"{n:.0f}{unit}" if unit == 'B' else f"{n:.1f}{unit}"
        n /= 1024.0


def tree_size(path):
    """Bytes ocupados. Não segue symlink e não conta o mesmo inode duas vezes."""
    p = Path(path)
    if p.is_symlink():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total, seen = 0, set()
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in files:
            fp = os.path.join(root, name)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if st.st_ino in seen:
                continue
            seen.add(st.st_ino)
            total += st.st_size
    return total


def tree_newest_mtime(path):
    """mtime mais recente da árvore.

    mtime de diretório só muda quando entra/sai entrada — um dir "velho" pode
    ter arquivo escrito hoje dentro. Usar o mais recente é o lado conservador:
    mantém por mais tempo, nunca apaga por menos.
    """
    p = Path(path)
    try:
        newest = p.lstat().st_mtime
    except OSError:
        return 0
    if p.is_file() or p.is_symlink():
        return newest
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            try:
                newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
            except OSError:
                continue
    return newest


def expanduser_glob(pattern):
    return sorted(glob.glob(os.path.expanduser(pattern)))


## Manifesto

def load_manifest():
    if not MANIFEST_PATH.exists():
        die(f"manifesto não encontrado: {MANIFEST_PATH}\n"
            f"copie o manifest.example.yml da skill e edite")
    raw = yaml.safe_load(MANIFEST_PATH.read_text()) or []
    if not isinstance(raw, list):
        die("manifesto deve ser uma lista de alvos")

    targets = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or 'name' not in item:
            die(f"alvo #{i + 1} sem `name`")
        kind = item.get('kind')
        if kind not in KINDS:
            die(f"alvo {item['name']}: kind inválido {kind!r} (use {', '.join(KINDS)})")
        item.setdefault('paths', [])
        item.setdefault('exclude', [])
        item.setdefault('notes', '')
        item.setdefault('enabled', True)
        targets.append(item)
    return targets


def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + '\n')


## Coleta de candidatos por kind

def collect_paths(target):
    """Globs cujo match inteiro é lixo."""
    items = []
    for pattern in target['paths']:
        for match in expanduser_glob(pattern):
            items.append({'path': match, 'size': tree_size(match), 'why': 'declarado'})
    return items


def collect_age(target):
    """Globs filtrados por idade. `older_than_days` default 7."""
    days = int(target.get('older_than_days', 7))
    cutoff = time.time() - days * 86400
    excludes = [os.path.expanduser(e) for e in target['exclude']]
    items = []
    for pattern in target['paths']:
        for match in expanduser_glob(pattern):
            if any(fnmatch.fnmatch(match, e) for e in excludes):
                continue
            mtime = tree_newest_mtime(match)
            if mtime >= cutoff:
                continue
            age = int((time.time() - mtime) / 86400)
            items.append({'path': match, 'size': tree_size(match),
                          'why': f'{age}d sem escrita'})
    return items


def collect_retention(target):
    """Mantém os `keep` mais recentes por diretório-pai; o resto é candidato."""
    keep = int(target.get('keep', 3))
    excludes = [os.path.expanduser(e) for e in target['exclude']]
    groups = {}
    for pattern in target['paths']:
        for match in expanduser_glob(pattern):
            if any(fnmatch.fnmatch(match, e) for e in excludes):
                continue
            groups.setdefault(os.path.dirname(match), []).append(match)

    items = []
    for parent, matches in sorted(groups.items()):
        # Ordena por mtime da própria entrada: nome com timestamp e mtime
        # concordam aqui, e mtime não depende do formato do nome.
        ranked = sorted(matches, key=lambda m: tree_newest_mtime(m), reverse=True)
        for stale in ranked[keep:]:
            items.append({'path': stale, 'size': tree_size(stale),
                          'why': f'excede keep={keep} em {parent}'})
    return items


def collect_command(target):
    """Alvo cujo dono é um comando (npm/pip). Tamanho vem de `size_path`."""
    size = 0
    for pattern in target.get('size_path', []) or []:
        for match in expanduser_glob(pattern):
            size += tree_size(match)
    label = (target.get('size_path') or ['(comando)'])[0]
    return [{'path': os.path.expanduser(label), 'size': size,
             'why': target.get('apply', '(sem comando de apply)')}]


COLLECTORS = {'paths': collect_paths, 'age': collect_age, 'retention': collect_retention,
              'command': collect_command}


def scan_target(target):
    """Coleta candidatos e passa cada um pela guarda. Violação aborta o alvo."""
    result = {'name': target['name'], 'kind': target['kind'], 'notes': target['notes'],
              'items': [], 'blocked': [], 'skipped': [], 'size': 0, 'error': None}
    if not target['enabled']:
        result['error'] = 'desabilitado no manifesto'
        return result
    try:
        items = COLLECTORS[target['kind']](target)
    except Exception as exc:                       # alvo quebrado não derruba o scan
        result['error'] = f'{type(exc).__name__}: {exc}'
        return result

    for item in items:
        if item.get('error'):
            result['skipped'].append({**item, 'why': item['error']})
            continue
        # Alvo de comando não remove caminho direto: nada a guardar.
        if target['kind'] == 'command':
            result['items'].append(item)
            result['size'] += item['size']
            continue
        violated = guard(item['path'])
        if violated:
            result['blocked'].append({**item, 'why': f'protegido por {violated}'})
            continue
        try:
            st = os.lstat(item['path'])
        except OSError as exc:
            result['skipped'].append({**item, 'why': str(exc)})
            continue
        if st.st_uid != os.getuid():
            result['skipped'].append({**item, 'why': f'outro dono (uid {st.st_uid})'})
            continue
        result['items'].append(item)
        result['size'] += item['size']
    return result


## Remoção (só com --apply)

def remove_item(path):
    p = Path(path)
    if p.is_symlink() or p.is_file():
        p.unlink()
    else:
        shutil.rmtree(path)


def apply_target(target, result):
    """Executa o alvo. Blocked nunca chega aqui — scan_target já filtrou."""
    freed, failed = 0, []

    if target['kind'] == 'command':
        cmd = target.get('apply')
        if not cmd:
            return 0, [(target['name'], 'sem comando de apply no manifesto')]
        before = result['size']
        rc, out, err = run(cmd, shell=True)
        if rc != 0:
            return 0, [(target['name'], (err or out).strip()[:200])]
        after = 0
        for pattern in target.get('size_path', []) or []:
            for match in expanduser_glob(pattern):
                after += tree_size(match)
        return max(0, before - after), failed

    for item in result['items']:
        # Segunda passada pela guarda: o scan e o apply são momentos distintos.
        violated = guard(item['path'])
        if violated:
            failed.append((item['path'], f'protegido por {violated}'))
            continue
        try:
            remove_item(item['path'])
            freed += item['size']
        except OSError as exc:
            failed.append((item['path'], str(exc)))
    return freed, failed


## Saída

def render(results, apply_mode, applied=None):
    total = sum(r['size'] for r in results)
    n_items = sum(len(r['items']) for r in results)
    n_blocked = sum(len(r['blocked']) for r in results)

    head = 'housekeeping · ' + datetime.now().strftime('%Y-%m-%d %H:%M')
    mode = 'APPLY' if apply_mode else 'dry-run (nada foi removido)'
    out = [head, f"{mode} · {n_items} candidato(s) em {len(results)} alvo(s)"
                 + (f" · {n_blocked} bloqueado(s) pela guarda" if n_blocked else '')]

    for r in results:
        if r['error']:
            out.append(f"\n{r['name']}  —  {r['error']}")
            continue
        if not (r['items'] or r['blocked'] or r['skipped']):
            out.append(f"\n{r['name']}  —  nada a fazer")
            continue
        out.append(f"\n{r['name']}  ({human(r['size'])})")
        for item in r['items'][:12]:
            out.append(f"  {human(item['size']):>7}  {item['path']}"
                       + (f"  · {item['why']}" if item.get('why') else ''))
        if len(r['items']) > 12:
            rest = r['items'][12:]
            out.append(f"  {human(sum(i['size'] for i in rest)):>7}  "
                       f"… +{len(rest)} item(ns)")
        for item in r['blocked']:
            out.append(f"  {'BLOQ':>7}  {item['path']}  · {item['why']}")
        for item in r['skipped']:
            out.append(f"  {'skip':>7}  {item['path']}  · {item['why']}")
        if applied and r['name'] in applied:
            freed, failed = applied[r['name']]
            out.append(f"  → liberado {human(freed)}"
                       + (f", {len(failed)} falha(s)" if failed else ''))
            for path, why in failed:
                out.append(f"     falha: {path} · {why}")

    verb = 'liberado' if apply_mode else 'recuperável'
    out.append(f"\nTotal {verb}: {human(total)}")
    if not apply_mode and n_items:
        out.append("Para executar: housekeeping clean --apply  (ou --target <alvo>)")
    for r in results:
        if r['notes'] and (r['items'] or r['blocked']):
            out.append(f"nota · {r['name']}: {r['notes']}")
    return '\n'.join(out)


## Comandos

def select(targets, name):
    if not name:
        return targets
    hit = [t for t in targets if t['name'] == name]
    if not hit:
        die(f"alvo desconhecido: {name} (declarados: "
            f"{', '.join(t['name'] for t in targets)})")
    return hit


def cmd_scan(args):
    targets = select(load_manifest(), args.target)
    results = [scan_target(t) for t in targets]

    state = load_state()
    state['last_scan'] = datetime.now().isoformat(timespec='seconds')
    state['last_scan_bytes'] = sum(r['size'] for r in results)
    save_state(state)

    if args.json:
        print(json.dumps({'scanned_at': state['last_scan'], 'targets': results},
                         indent=2, ensure_ascii=False))
        return 0
    print(render(results, apply_mode=False))
    return 0


def cmd_clean(args):
    targets = select(load_manifest(), args.target)
    results = [scan_target(t) for t in targets]

    if not args.apply:
        print(render(results, apply_mode=False))
        print("\n(--dry-run é o default; nada foi removido)")
        return 0

    applied, total_freed = {}, 0
    for target, result in zip(targets, results):
        if not result['items'] or result['error']:
            continue
        freed, failed = apply_target(target, result)
        applied[target['name']] = (freed, failed)
        total_freed += freed

    state = load_state()
    state['last_apply'] = datetime.now().isoformat(timespec='seconds')
    state['last_apply_bytes'] = total_freed
    history = state.setdefault('history', [])
    history.append({'at': state['last_apply'], 'freed': total_freed,
                    'targets': {k: v[0] for k, v in applied.items()}})
    state['history'] = history[-20:]
    save_state(state)

    print(render(results, apply_mode=True, applied=applied))
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog='housekeeping',
        description='limpeza de lixo declarado no manifesto; dry-run é o default')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('scan', help='dry-run: o que seria removido e quanto libera')
    p.add_argument('--target')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser('clean', help='remove de fato — exige --apply')
    p.add_argument('--target')
    p.add_argument('--apply', action='store_true',
                   help='sem esta flag o comando é idêntico ao scan')
    p.add_argument('--dry-run', action='store_true', help='explícito; já é o default')
    p.set_defaults(func=cmd_clean)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == '__main__':
    main()
