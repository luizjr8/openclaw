"""Resolução de caminhos: código da skill (repo) vs dados do usuário (workspace).

Regra do repo: o código é genérico e versionado; certificados, config de emitentes
e o banco de notas emitidas ficam FORA do repo, no workspace da instância.
"""

import os
from pathlib import Path

## Código da skill (versionado no repo)

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
SCHEMAS_DIR = SKILL_DIR / 'schemas'

## Dados do usuário (nunca no repo)

# NFSE_DATA_DIR tem prioridade; senão cai no padrão das demais skills
# (LZBOT_WORKSPACE_ROOT/skills/<nome-da-skill>).
_WORKSPACE_ROOT = Path(os.environ.get('LZBOT_WORKSPACE_ROOT', str(Path.cwd())))
DATA_DIR = Path(os.environ.get('NFSE_DATA_DIR', str(_WORKSPACE_ROOT / 'skills' / SKILL_DIR.name)))

EMITENTES_DIR = DATA_DIR / 'emitentes'
CERTS_DIR = DATA_DIR / 'certs'
XML_DIR = DATA_DIR / 'xml'
DB_PATH = DATA_DIR / 'nfse.db'


def ensure_dirs() -> None:
    """Cria a árvore de dados na primeira execução. certs/ nasce com 0700."""
    for d in (DATA_DIR, EMITENTES_DIR, XML_DIR):
        d.mkdir(parents=True, exist_ok=True)
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CERTS_DIR, 0o700)
