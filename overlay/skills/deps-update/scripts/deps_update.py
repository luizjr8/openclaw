#!/usr/bin/env python3
"""deps-update — detecta drift de dependências declaradas em um manifesto.

Detecção é automática; aplicação é sempre sob aprovação humana. O comando
`apply` exige versão explícita e só roda depois de um backup bem-sucedido do
estado mutável que o pacote é dono (índices, bancos, config).
"""

import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from html import unescape
from pathlib import Path

try:
    import yaml
except ImportError:
    print("erro: pyyaml não instalado (pip3 install --break-system-packages pyyaml)", file=sys.stderr)
    sys.exit(2)


## Caminhos

# Estado da instância vive fora do repo da skill: manifesto, histórico e backups.
WORKSPACE_ROOT = Path(os.environ.get('LZBOT_WORKSPACE_ROOT', str(Path.cwd())))
DATA_DIR = Path(os.environ.get('DEPS_UPDATE_DATA_DIR',
                               str(WORKSPACE_ROOT / 'skills' / 'deps-update')))
MANIFEST_PATH = DATA_DIR / 'manifest.yml'
STATE_PATH = DATA_DIR / 'state.json'
BACKUP_DIR = DATA_DIR / 'backups'

KINDS = ('npm-global', 'pip', 'apt', 'binary')
# status declarado à mão no manifesto (o status calculado pelo check é outro campo)
DECLARED_STATUS = (None, 'orphan-candidate')

# Feed de releases do GitHub. Deliberadamente não é a api.github.com: ela
# respondeu 504 na primeira varredura real, o atom respondeu normal.
ATOM_TIMEOUT = 20


## Infra de processo

def run(cmd, check=False, env=None):
    """Executa comando e devolve (rc, stdout, stderr). Nunca levanta por rc."""
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise RuntimeError(f"comando falhou ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc.returncode, proc.stdout, proc.stderr


def die(msg, code=2):
    print(f"erro: {msg}", file=sys.stderr)
    sys.exit(code)


## Manifesto

def load_manifest():
    if not MANIFEST_PATH.exists():
        die(f"manifesto não encontrado em {MANIFEST_PATH} "
            f"(copie o manifest.example.yml da skill e edite)")
    with open(MANIFEST_PATH) as fh:
        data = yaml.safe_load(fh) or []
    if not isinstance(data, list):
        die("manifesto deve ser uma lista de dependências")

    entries = []
    seen = set()
    for i, raw in enumerate(data):
        if not isinstance(raw, dict) or 'name' not in raw:
            die(f"entrada {i} do manifesto sem 'name'")
        name = raw['name']
        if name in seen:
            die(f"nome duplicado no manifesto: {name}")
        seen.add(name)
        kind = raw.get('kind')
        if kind not in KINDS:
            die(f"{name}: kind '{kind}' inválido (use um de {', '.join(KINDS)})")
        declared = raw.get('status')
        if declared not in DECLARED_STATUS:
            die(f"{name}: status '{declared}' inválido "
                f"(só existe 'orphan-candidate' — omita para o caso normal)")
        entries.append({
            'name': name,
            'kind': kind,
            'package': raw.get('package', name),
            'state': raw.get('state') or [],
            'changelog': _changelog_sources(raw.get('changelog'), name),
            'verify': raw.get('verify'),
            'critical': bool(raw.get('critical')),
            'declared_status': declared,
            'notes': raw.get('notes'),
        })
    return entries


def _changelog_sources(raw, name):
    """`changelog:` aceita string (atalho para path) ou mapa {path, atom}.

    Duas formas porque nem todo pacote shipa CHANGELOG: o playwright não tem
    nenhum, só release notes no GitHub.
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {'path': raw}
    if not isinstance(raw, dict):
        die(f"{name}: changelog deve ser string ou mapa com 'path'/'atom'")
    unknown = sorted(set(raw) - {'path', 'atom'})
    if unknown:
        die(f"{name}: changelog só aceita 'path' e 'atom' (achei: {', '.join(unknown)})")
    return {k: v for k, v in raw.items() if v}


def find_entry(entries, name):
    for e in entries:
        if e['name'] == name or e['package'] == name:
            return e
    die(f"'{name}' não está no manifesto ({MANIFEST_PATH})")


## Estado (histórico local)

def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH) as fh:
            return json.load(fh)
    return {'last_check': None, 'packages': {}}


def save_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, 'w') as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write('\n')


def record_event(name, event):
    state = load_state()
    pkg = state['packages'].setdefault(name, {'history': []})
    pkg['history'].append(event)
    pkg['history'] = pkg['history'][-20:]  # histórico curto, só o suficiente pra auditar
    save_state(state)


## Versões: parse e classificação do salto

def _parse(version):
    """Devolve (nums, sufixo). '2026.7.1-2' -> ([2026,7,1], '-2')."""
    m = re.match(r'^v?(\d+(?:\.\d+)*)(.*)$', str(version).strip())
    if not m:
        return None, str(version)
    nums = [int(x) for x in m.group(1).split('.')]
    while len(nums) < 3:
        nums.append(0)
    return nums, m.group(2)


def jump_type(current, available):
    """patch/minor/major/build — heurística semver tolerante a calver."""
    if current == available:
        return '—'
    cur, cur_sfx = _parse(current)
    new, new_sfx = _parse(available)
    if cur is None or new is None:
        return '?'
    for idx, label in enumerate(('major', 'minor', 'patch')):
        if new[idx] != cur[idx]:
            return label
    if new[3:] != cur[3:] or new_sfx != cur_sfx:
        return 'build'
    return '—'


def is_newer(a, b):
    """True se a > b."""
    na, sa = _parse(a)
    nb, sb = _parse(b)
    if na is None or nb is None:
        return a != b
    if na != nb:
        return na > nb
    # sufixo vazio é release; release < release-2 (build metadata do npm)
    return sa > sb


## Adaptadores por kind

def npm_root():
    rc, out, _ = run(['npm', 'root', '-g'])
    return Path(out.strip()) if rc == 0 and out.strip() else None


def npm_snapshot():
    """{pacote: {'current': v, 'available': v}} para os globais do npm."""
    snap = {}
    rc, out, _ = run(['npm', 'ls', '-g', '--depth=0', '--json'])
    if out.strip():
        deps = (json.loads(out).get('dependencies') or {})
        for pkg, info in deps.items():
            v = info.get('version')
            snap[pkg] = {'current': v, 'available': v}
    # `npm outdated` sai com rc=1 quando há pendências — não é erro.
    rc, out, _ = run(['npm', 'outdated', '-g', '--json'])
    if out.strip():
        for pkg, info in json.loads(out).items():
            latest = info.get('latest') or info.get('wanted')
            snap.setdefault(pkg, {'current': info.get('current')})
            snap[pkg]['current'] = info.get('current') or snap[pkg].get('current')
            snap[pkg]['available'] = latest
    return snap


def pip_snapshot():
    snap = {}
    rc, out, _ = run(['pip3', 'list', '--format=json'])
    if out.strip():
        for item in json.loads(out):
            key = _pip_key(item['name'])
            snap[key] = {'current': item['version'], 'available': item['version'],
                         'display': item['name']}
    rc, out, _ = run(['pip3', 'list', '--outdated', '--format=json'])
    if out.strip():
        for item in json.loads(out):
            key = _pip_key(item['name'])
            snap.setdefault(key, {'current': item['version'], 'display': item['name']})
            snap[key]['available'] = item['latest_version']
    return snap


def _pip_key(name):
    """Normalização PEP 503: pymupdf == PyMuPDF == py_mupdf."""
    return re.sub(r'[-_.]+', '-', name).lower()


def apt_probe(package):
    rc, out, _ = run(['dpkg-query', '-W', "-f=${Version}", package])
    current = out.strip() if rc == 0 and out.strip() else None
    available = None
    rc, out, _ = run(['apt-cache', 'policy', package])
    m = re.search(r'Candidate:\s*(\S+)', out or '')
    if m and m.group(1) != '(none)':
        available = m.group(1)
    return current, available


def binary_probe(entry):
    """Sem registry pra consultar: versão vem do próprio verify, disponível é manual."""
    if not entry.get('verify'):
        return None, None
    rc, out, err = run(['sh', '-c', entry['verify']])
    if rc != 0:
        return None, None
    m = re.search(r'\d+(?:\.\d+)+', (out or '') + (err or ''))
    return (m.group(0) if m else (out or '').strip().splitlines()[0] if out else None), None


def probe(entries):
    """Resolve current/available de todas as entradas, consultando cada gerenciador 1x."""
    kinds = {e['kind'] for e in entries}
    npm = npm_snapshot() if 'npm-global' in kinds else {}
    pip = pip_snapshot() if 'pip' in kinds else {}

    rows = []
    for e in entries:
        current = available = None
        if e['kind'] == 'npm-global':
            info = npm.get(e['package'], {})
            current, available = info.get('current'), info.get('available')
        elif e['kind'] == 'pip':
            info = pip.get(_pip_key(e['package']), {})
            current, available = info.get('current'), info.get('available')
        elif e['kind'] == 'apt':
            current, available = apt_probe(e['package'])
        elif e['kind'] == 'binary':
            current, available = binary_probe(e)

        # Entrada marcada à mão como órfã não é update pendente: o veredito é
        # remover. Sem isso ela volta em toda varredura como falso pendente.
        if current is not None and e.get('declared_status') == 'orphan-candidate':
            status = 'orphan'
        elif current is None:
            status = 'ausente'
        elif available is None:
            status = 'manual'
        elif is_newer(available, current):
            status = 'update'
        else:
            status = 'ok'

        rows.append({**e, 'current': current, 'available': available,
                     'status': status, 'jump': jump_type(current, available) if current and available else '?',
                     'state_paths': len(expand_state(e))})
    return rows, npm, pip


## Estado mutável: globs, backup, restore

def expand_state(entry):
    """Expande os globs de state/ para os caminhos que existem agora.

    Diretório também conta: cache de browser é estado que vaza (revisão nova
    baixa, velha fica), e precisa aparecer no check mesmo sem ser copiável.
    """
    paths = []
    for pattern in entry['state']:
        for hit in sorted(glob.glob(pattern, recursive=True)):
            p = Path(hit)
            if p.is_file() or p.is_dir():
                paths.append(p)
    return paths


def _copy_one(src, dst):
    """sqlite ao vivo pode ser copiado torto — usa a API de backup quando é db."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix in ('.sqlite', '.db', '.sqlite3'):
        try:
            with sqlite3.connect(f'file:{src}?mode=ro', uri=True) as source, \
                 sqlite3.connect(str(dst)) as target:
                source.backup(target)
            return 'sqlite-backup'
        except sqlite3.Error:
            pass  # não era sqlite de verdade (ou está corrompido): cai pra cópia crua
    shutil.copy2(src, dst)
    return 'copy'


def do_backup(entry, version_before=None):
    backup_id = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = BACKUP_DIR / entry['name'] / backup_id
    files = expand_state(entry)
    saved = []
    for src in files:
        # Diretório de cache (revisões do chromium, p.ex.) pode ter GB e se
        # "restaura" por re-download — registra o caminho, não copia.
        if src.is_dir():
            saved.append({'source': str(src), 'stored': None, 'method': 'noted',
                          'bytes': None, 'entries': len(os.listdir(src))})
            continue
        # caminho absoluto vira relativo dentro do backup pra permitir restore exato
        rel = Path(str(src).lstrip('/'))
        method = _copy_one(src, dest / rel)
        saved.append({'source': str(src), 'stored': str(rel), 'method': method,
                      'bytes': src.stat().st_size})
    meta = {'backup_id': backup_id, 'name': entry['name'], 'kind': entry['kind'],
            'package': entry['package'], 'version_before': version_before,
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'files': saved}
    dest.mkdir(parents=True, exist_ok=True)
    with open(dest / 'backup.json', 'w') as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    return meta


def list_backups(name):
    base = BACKUP_DIR / name
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / 'backup.json').exists())


def load_backup(name, backup_id):
    with open(BACKUP_DIR / name / backup_id / 'backup.json') as fh:
        return json.load(fh)


## Install / verify

def install(entry, version):
    spec_pkg = entry['package']
    env_path = f"PATH={os.environ.get('PATH', '')}"
    if entry['kind'] == 'npm-global':
        # node_modules global é do root; lzbot tem sudo sem senha
        cmd = ['sudo', '-n', 'env', env_path, 'npm', 'install', '-g', f'{spec_pkg}@{version}']
    elif entry['kind'] == 'pip':
        # PEP 668 (Ubuntu Noble) exige --break-system-packages. E sudo: sem ele o pip
        # cai em "user installation" (~/.local), que vive no overlay e evapora quando
        # o container é recriado — o upgrade sumiria calado no próximo boot.
        cmd = ['sudo', '-n', 'env', env_path, 'pip3', 'install',
               '--break-system-packages', f'{spec_pkg}=={version}']
    elif entry['kind'] == 'apt':
        cmd = ['sudo', '-n', 'apt-get', 'install', '-y', f'{spec_pkg}={version}']
    else:
        die(f"{entry['name']}: kind '{entry['kind']}' não tem install automático — aplique à mão")
    print(f"  $ {' '.join(cmd)}")
    rc, out, err = run(cmd)
    if out.strip():
        print(indent(out.strip()))
    if rc != 0:
        print(indent(err.strip()), file=sys.stderr)
    return rc


def do_verify(entry):
    if not entry.get('verify'):
        return None, "sem comando de verify no manifesto"
    rc, out, err = run(['sh', '-c', entry['verify']])
    return rc, (out or err).strip()


def indent(text, prefix='    '):
    return '\n'.join(prefix + line for line in str(text).splitlines())


## Changelog

# Fallback por palavra-chave, usado só quando o subtítulo não classifica o bloco.
# Deliberadamente estreito: "schema"/"removed" soltos em prosa de docs geram
# falso positivo em massa e treinam a gente a ignorar os destaques.
CL_MARKERS = (
    ('SEGURANÇA', re.compile(r'\b(security|vulnerabilit\w*|CVE-|RCE|exploit|sandbox escape|'
                             r'path traversal|injection|segurança|vulnerab\w*)', re.I)),
    ('BREAKING', re.compile(r'\b(breaking change|breaking|incompatib\w*|no longer supported)', re.I)),
    ('MIGRAÇÃO', re.compile(r'\b(migrat\w*|re-?index\w*|re-?embed\w*|upgrade path|'
                            r'auto-migrate|índice|migração)', re.I)),
)
HEADING_RE = re.compile(r'^\s{0,3}(#{1,4})\s*\[?v?(\d+(?:\.\d+)+[^\s\]]*)\]?', re.M)


def resolve_changelog(entry, version=None):
    """Devolve (texto, origem, tentativas).

    Ordem: tarball da versão-alvo (npm) → CHANGELOG local → feed de releases.
    O atom entra como fallback automático porque pacote sem CHANGELOG é comum;
    o tarball vem primeiro porque o arquivo instalado só conhece o passado.
    """
    sources = entry.get('changelog') or {}
    rel = sources.get('path')
    tried = []

    if version and entry['kind'] == 'npm-global':
        tried.append(f"tarball {entry['package']}@{version}")
        text = _changelog_from_npm_tarball(entry, version, rel)
        if text:
            return text, tried[-1], tried

    if rel:
        candidates = [Path(rel)] if os.path.isabs(rel) else []
        if not candidates:
            bases = []
            if entry['kind'] == 'npm-global':
                root = npm_root()
                if root:
                    bases += [root.parent, root.parent.parent]  # <prefix>/lib e <prefix>
            bases += [WORKSPACE_ROOT, Path.cwd()]
            candidates = [b / rel for b in bases]
        tried.append(f"path {rel}")
        for cand in candidates:
            if cand.is_file():
                return cand.read_text(errors='replace'), str(cand), tried

    url = sources.get('atom') or _atom_from_registry(entry)
    if url:
        tried.append(f"atom {url}")
        text = _changelog_from_atom(url)
        if text:
            return text, url, tried
    return None, None, tried


def _changelog_from_npm_tarball(entry, version, rel):
    """Baixa o tarball da versão e extrai o CHANGELOG de dentro dele."""
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, err = run(['npm', 'pack', f"{entry['package']}@{version}",
                            '--pack-destination', tmp, '--silent'])
        tarballs = list(Path(tmp).glob('*.tgz'))
        if rc != 0 or not tarballs:
            return None
        wanted = os.path.basename(rel) if rel else 'CHANGELOG.md'
        with tarfile.open(tarballs[0]) as tf:
            for member in tf.getmembers():
                if member.isfile() and os.path.basename(member.name).lower() == wanted.lower():
                    fh = tf.extractfile(member)
                    return fh.read().decode('utf-8', errors='replace') if fh else None
    return None


ATOM_NS = {'a': 'http://www.w3.org/2005/Atom'}
GH_REPO_RE = re.compile(r'github\.com[:/]+([^/]+)/([^/#?]+?)(?:\.git)?/?$')


def _atom_from_registry(entry):
    """Deriva o feed de releases do campo repository do registry.

    Evita ter que escrever a URL à mão em toda entrada npm — e mantém o
    manifesto certo quando o pacote troca de org.
    """
    if entry['kind'] != 'npm-global':
        return None
    rc, out, _ = run(['npm', 'view', entry['package'], 'repository.url'])
    if rc != 0 or not out.strip():
        return None
    m = GH_REPO_RE.search(out.strip().splitlines()[0].strip())
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/{m.group(2)}/releases.atom"


def _changelog_from_atom(url):
    """Converte o feed de releases em markdown com uma seção por versão.

    A saída imita um CHANGELOG.md justamente para reaproveitar o slice e o
    highlight — quem chama não precisa saber de onde veio.
    """
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'deps-update'})
        with urllib.request.urlopen(req, timeout=ATOM_TIMEOUT) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
    except (urllib.error.URLError, ET.ParseError, OSError, ValueError):
        return None

    sections = []
    for item in root.findall('a:entry', ATOM_NS):
        title = (item.findtext('a:title', '', ATOM_NS) or '').strip()
        tag = (item.findtext('a:id', '', ATOM_NS) or '').rsplit('/', 1)[-1]
        m = re.search(r'\d+(?:\.\d+)+[^\s,;)]*', f"{title} {tag}")
        if not m:
            continue  # release sem versão no título nem na tag: não dá pra ordenar
        body = _html_to_text(item.findtext('a:content', '', ATOM_NS) or '')
        sections.append(f"## {m.group(0)}\n\n{body}".rstrip())
    return '\n\n'.join(sections) if sections else None


def _html_to_text(html):
    """HTML do release note → texto com headings e bullets preservados.

    Heading importa: é o `### Security` que classifica o bloco no highlight.
    """
    text = re.sub(r'(?is)<h[1-6][^>]*>(.*?)</h[1-6]>', r'\n### \1\n', html)
    text = re.sub(r'(?is)<li[^>]*>', '\n- ', text)
    text = re.sub(r'(?is)<br\s*/?>|</p>|</li>|</div>|</tr>', '\n', text)
    text = re.sub(r'(?s)<[^>]+>', '', text)
    text = unescape(text)
    text = '\n'.join(line.rstrip() for line in text.splitlines())
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def slice_changelog(text, v_from=None, v_to=None):
    """Corta o changelog no range (from exclusivo, to inclusivo)."""
    marks = [(m.start(), m.group(2)) for m in HEADING_RE.finditer(text)]
    if not marks:
        return text, []
    sections = []
    for i, (pos, ver) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        sections.append({'version': ver, 'body': text[pos:end].rstrip()})

    picked = []
    for sec in sections:
        if v_to and is_newer(sec['version'], v_to):
            continue
        if v_from and not is_newer(sec['version'], v_from):
            continue
        picked.append(sec)
    if not picked:
        picked = sections[:1]
    return '\n\n'.join(s['body'] for s in picked), picked


SUBHEAD_LABEL = ((re.compile(r'security|segurança', re.I), 'SEGURANÇA'),
                 (re.compile(r'breaking|removed', re.I), 'BREAKING'))
BULLET_RE = re.compile(r'^\s*[-*+]\s+')


def _blocks(body):
    """Quebra a seção em (subtítulo, bloco). Bullet quebrado em várias linhas é
    um bloco só — casar linha a linha produz fragmentos sem sentido."""
    out = []
    subhead = ''
    buf = []
    for line in body.splitlines():
        if line.lstrip().startswith('#'):
            if buf:
                out.append((subhead, ' '.join(buf)))
                buf = []
            subhead = line.strip('# ').strip()
            continue
        if BULLET_RE.match(line) or (line.strip() and not buf):
            if buf:
                out.append((subhead, ' '.join(buf)))
            buf = [BULLET_RE.sub('', line).strip()]
        elif line.strip():
            buf.append(line.strip())
        else:
            if buf:
                out.append((subhead, ' '.join(buf)))
                buf = []
    if buf:
        out.append((subhead, ' '.join(buf)))
    return out


def highlight(sections, limit=200):
    """Blocos do range que são segurança/breaking/migração. O subtítulo
    (### Security) manda; palavra-chave no texto é o fallback."""
    hits = []
    for sec in sections:
        for subhead, text in _blocks(sec['body']):
            label = None
            for pattern, name in SUBHEAD_LABEL:
                if pattern.search(subhead):
                    label = name
                    break
            if not label:
                for name, pattern in CL_MARKERS:
                    if pattern.search(text):
                        label = name
                        break
            if not label:
                continue
            snippet = text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0] + '…'
            hits.append((label, sec['version'], snippet))
    return hits


## Comando: check

def cmd_check(args):
    entries = load_manifest()
    rows, npm, pip = probe(entries)

    managed_npm = {e['package'] for e in entries if e['kind'] == 'npm-global'}
    managed_pip = {_pip_key(e['package']) for e in entries if e['kind'] == 'pip'}
    unmanaged = []
    for pkg, info in sorted(npm.items()):
        if pkg not in managed_npm and info.get('available') and is_newer(info['available'], info.get('current') or '0'):
            unmanaged.append({'kind': 'npm-global', 'package': pkg,
                              'current': info['current'], 'available': info['available']})
    for key, info in sorted(pip.items()):
        if key not in managed_pip and info.get('available') and is_newer(info['available'], info.get('current') or '0'):
            unmanaged.append({'kind': 'pip', 'package': info.get('display', key),
                              'current': info['current'], 'available': info['available']})

    state = load_state()
    state['last_check'] = datetime.now().isoformat(timespec='seconds')
    save_state(state)

    if args.json:
        print(json.dumps({'checked_at': state['last_check'], 'managed': rows,
                          'unmanaged': unmanaged}, indent=2, ensure_ascii=False))
        return 0

    print(render_check(rows, unmanaged, state['last_check']))
    return 0


def render_check(rows, unmanaged, checked_at):
    updates = [r for r in rows if r['status'] == 'update']
    critical = [r for r in updates if r['critical']]
    normal = [r for r in updates if not r['critical']]
    ok = [r for r in rows if r['status'] == 'ok']
    missing = [r for r in rows if r['status'] == 'ausente']
    manual = [r for r in rows if r['status'] == 'manual']
    orphans = [r for r in rows if r['status'] == 'orphan']
    with_state = [r for r in updates if r['state_paths']]

    width = max([len(r['name']) for r in rows] or [8])
    out = [f"deps-update · {checked_at.replace('T', ' ')}",
           f"{len(updates)} update(s) no manifesto · {len(critical)} crítico(s) · "
           f"{len(orphans)} órfão(s) · {len(unmanaged)} fora do manifesto"]

    def line(r):
        tag = ' · mexe em estado' if r['state_paths'] else ''
        return (f"  {r['name']:<{width}}  {r['current']} → {r['available']}"
                f"  [{r['jump']}]{tag}")

    if critical:
        out.append("\n⚠ CRÍTICO (janela de segurança fora de cadência)")
        out += [line(r) for r in critical]
    if normal:
        out.append("\nAtualizações")
        out += [line(r) for r in normal]
    if orphans:
        out.append("\nÓrfãos (remover, não atualizar)")
        out += [f"  {r['name']:<{width}}  {r['current']}  → remover" for r in orphans]
    if manual:
        out.append("\nSem registry (checar à mão)")
        out += [f"  {r['name']:<{width}}  {r['current'] or '?'}" for r in manual]
    if missing:
        out.append("\nAusentes (declarado no manifesto, não instalado)")
        out += [f"  {r['name']:<{width}}  {r['kind']}" for r in missing]
    if ok:
        out.append("\nEm dia: " + ', '.join(f"{r['name']} {r['current']}" for r in ok))

    if unmanaged:
        by_kind = {}
        for u in unmanaged:
            by_kind.setdefault(u['kind'], []).append(f"{u['package']} {u['current']}→{u['available']}")
        out.append("")
        for kind, items in by_kind.items():
            out.append(f"fora do manifesto ({len(items)} {kind}): " + ', '.join(items))

    if with_state:
        out.append(f"\n{len(with_state)} update(s) mexem em estado mutável — "
                   f"backup obrigatório antes do apply: "
                   + ', '.join(r['name'] for r in with_state))
    for r in updates + orphans:
        if r['notes']:
            out.append(f"nota · {r['name']}: {r['notes']}")
    return '\n'.join(out)


## Comando: changelog

def cmd_changelog(args):
    entries = load_manifest()
    entry = find_entry(entries, args.name)
    rows, _, _ = probe([entry])
    row = rows[0]
    v_from = args.from_version or row['current']
    v_to = args.to_version or row['available'] or row['current']

    text, origin, tried = resolve_changelog(entry, v_to)
    if not text:
        die(f"{entry['name']}: changelog não encontrado. Tentado: "
            + (' → '.join(tried) if tried else 'nada (entrada sem campo changelog)'))

    body, sections = slice_changelog(text, v_from, v_to)
    hits = highlight(sections)

    print(f"# {entry['name']} · {v_from} → {v_to}")
    print(f"# fonte: {origin}")
    if hits:
        print(f"\n## Destaques ({len(hits)})")
        for label, ver, line in hits:
            print(f"  [{label}] {ver}: {line}")
    else:
        print("\n## Destaques: nenhum bloco de segurança/breaking/migração detectado")
    if entry['notes']:
        print(f"\n## Nota do manifesto: {entry['notes']}")
    print(f"\n## Range ({len(sections)} versões)\n")
    print(body)
    return 0


## Comando: backup / rollback / verify / apply

def cmd_backup(args):
    entries = load_manifest()
    entry = find_entry(entries, args.name)
    rows, _, _ = probe([entry])
    meta = do_backup(entry, version_before=rows[0]['current'])
    record_event(entry['name'], {'action': 'backup', 'backup_id': meta['backup_id'],
                                 'at': meta['created_at'], 'files': len(meta['files'])})
    print(f"backup {meta['backup_id']} · {len(meta['files'])} arquivo(s) · "
          f"{BACKUP_DIR / entry['name'] / meta['backup_id']}")
    for f in meta['files']:
        if f['method'] == 'noted':
            print(f"  {f['source']}  (diretório, {f['entries']} entrada(s) — registrado, não copiado)")
        else:
            print(f"  {f['source']}  ({f['bytes']} B, {f['method']})")
    if not meta['files']:
        print("  (sem globs de state/ no manifesto — nada de estado mutável a salvar)")
    return 0


def cmd_verify(args):
    entries = load_manifest()
    entry = find_entry(entries, args.name)
    rc, output = do_verify(entry)
    if rc is None:
        die(output)
    print(output)
    print(f"verify rc={rc}")
    return rc


def cmd_rollback(args):
    entries = load_manifest()
    entry = find_entry(entries, args.name)
    available = list_backups(entry['name'])
    if not available:
        die(f"{entry['name']}: nenhum backup em {BACKUP_DIR / entry['name']}")
    backup_id = args.backup or available[-1]
    if backup_id not in available:
        die(f"backup '{backup_id}' não existe (disponíveis: {', '.join(available)})")
    meta = load_backup(entry['name'], backup_id)
    base = BACKUP_DIR / entry['name'] / backup_id

    # rollback = voltar versão E estado; a versão vem gravada no backup
    if meta.get('version_before'):
        print(f"reinstalando {entry['package']} {meta['version_before']}")
        if install(entry, meta['version_before']) != 0:
            print("aviso: reinstalação da versão anterior falhou — restaurando estado mesmo assim",
                  file=sys.stderr)
    for f in meta['files']:
        if f['method'] == 'noted':
            print(f"  ignorado {f['source']} (diretório só registrado, não copiado)")
            continue
        src = base / f['stored']
        dst = Path(f['source'])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  restaurado {dst}")
    record_event(entry['name'], {'action': 'rollback', 'backup_id': backup_id,
                                 'at': datetime.now().isoformat(timespec='seconds'),
                                 'version_restored': meta.get('version_before')})
    rc, output = do_verify(entry)
    if rc is not None:
        print(f"verify rc={rc}: {output}")
    return 0


def cmd_apply(args):
    entries = load_manifest()
    entry = find_entry(entries, args.name)
    rows, _, _ = probe([entry])
    row = rows[0]
    target = args.to

    if entry.get('declared_status') == 'orphan-candidate':
        die(f"{entry['name']} está marcado como orphan-candidate no manifesto "
            f"(veredito é remover, não atualizar). Se mudou de ideia, tire o "
            f"campo 'status' da entrada antes de aplicar.")

    if row['current'] == target:
        print(f"{entry['name']} já está em {target} — nada a fazer")
        return 0

    print(f"apply {entry['name']}: {row['current']} → {target} "
          f"[{jump_type(row['current'], target)}]")
    if entry['notes']:
        print(f"nota do manifesto: {entry['notes']}")

    ## Backup obrigatório — sem ele o apply não roda
    try:
        meta = do_backup(entry, version_before=row['current'])
    except Exception as exc:
        die(f"backup falhou, apply abortado: {exc}")
    print(f"backup {meta['backup_id']} · {len(meta['files'])} arquivo(s) de estado")

    ## Install
    if install(entry, target) != 0:
        print("install falhou — estado não foi tocado, nada a reverter", file=sys.stderr)
        record_event(entry['name'], {'action': 'apply', 'result': 'install-failed',
                                     'to': target, 'backup_id': meta['backup_id'],
                                     'at': datetime.now().isoformat(timespec='seconds')})
        return 1

    ## Verify — falha aqui dispara rollback automático
    rc, output = do_verify(entry)
    if rc is None:
        print(f"aviso: {output}")
    elif rc != 0:
        print(f"verify falhou (rc={rc}): {output}", file=sys.stderr)
        print("revertendo...", file=sys.stderr)
        record_event(entry['name'], {'action': 'apply', 'result': 'verify-failed',
                                     'to': target, 'backup_id': meta['backup_id'],
                                     'at': datetime.now().isoformat(timespec='seconds')})
        args_rb = argparse.Namespace(name=entry['name'], backup=meta['backup_id'])
        cmd_rollback(args_rb)
        return 1
    else:
        print(f"verify ok: {output}")

    record_event(entry['name'], {'action': 'apply', 'result': 'ok', 'from': row['current'],
                                 'to': target, 'backup_id': meta['backup_id'],
                                 'at': datetime.now().isoformat(timespec='seconds')})
    print(f"aplicado: {entry['name']} {row['current']} → {target} "
          f"(rollback: deps_update.py rollback {entry['name']} --backup {meta['backup_id']})")
    return 0


## CLI

def main():
    parser = argparse.ArgumentParser(prog='deps-update',
                                     description='Detecta drift de dependências declaradas em manifesto.')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('check', help='compara instalado vs disponível (único comando de cron)')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_check)

    p = sub.add_parser('changelog', help='extrai o range de changelog e destaca segurança/breaking')
    p.add_argument('name')
    p.add_argument('--from', dest='from_version')
    p.add_argument('--to', dest='to_version')
    p.set_defaults(func=cmd_changelog)

    p = sub.add_parser('backup', help='snapshot dos globs de state/')
    p.add_argument('name')
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser('apply', help='backup -> install -> verify (rollback se verify falhar)')
    p.add_argument('name')
    p.add_argument('--to', required=True, help='versão exata (não existe "latest" implícito)')
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser('rollback', help='restaura versão e estado de um backup')
    p.add_argument('name')
    p.add_argument('--backup', help='id do backup (padrão: o mais recente)')
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser('verify', help='só o comando de verify do manifesto')
    p.add_argument('name')
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == '__main__':
    main()
