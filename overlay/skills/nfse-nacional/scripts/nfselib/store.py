"""Registro local das notas emitidas e da numeração sequencial da DPS.

O número da DPS é sequencial por (emitente, ambiente, série) e não pode repetir —
por isso ele é reservado numa transação antes do envio, e a nota fica gravada com
status `pendente` até a Sefin responder. Assim uma falha de rede nunca faz o
próximo `emitir` reutilizar um número que talvez já tenha sido consumido.
"""

import json
import sqlite3
from typing import Optional

from .paths import DB_PATH, ensure_dirs

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cnpj          TEXT NOT NULL,
    ambiente      TEXT NOT NULL,
    serie         TEXT NOT NULL,
    ndps          INTEGER NOT NULL,
    id_dps        TEXT,
    chave_acesso  TEXT,
    status        TEXT NOT NULL DEFAULT 'pendente',
    dh_emissao    TEXT,
    valor         TEXT,
    tomador_doc   TEXT,
    tomador_nome  TEXT,
    descricao     TEXT,
    xml_path      TEXT,
    resposta      TEXT,
    erro          TEXT,
    UNIQUE (cnpj, ambiente, serie, ndps)
);
CREATE INDEX IF NOT EXISTS idx_notas_chave ON notas (chave_acesso);
"""


def conectar() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def reservar_ndps(conn: sqlite3.Connection, cnpj: str, ambiente: str, serie: str,
                  forcado: Optional[int] = None) -> int:
    """Reserva o próximo nDPS livre (ou valida o informado) numa transação exclusiva."""
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if forcado is not None:
            existe = conn.execute(
                'SELECT 1 FROM notas WHERE cnpj=? AND ambiente=? AND serie=? AND ndps=?',
                (cnpj, ambiente, serie, forcado),
            ).fetchone()
            if existe:
                raise ValueError(
                    f'nDPS {forcado} já usado para {cnpj} série {serie} em {ambiente}.'
                )
            return forcado
        row = conn.execute(
            'SELECT MAX(ndps) AS ultimo FROM notas WHERE cnpj=? AND ambiente=? AND serie=?',
            (cnpj, ambiente, serie),
        ).fetchone()
        return (row['ultimo'] or 0) + 1


def registrar(conn: sqlite3.Connection, **campos) -> int:
    colunas = ', '.join(campos)
    marcas = ', '.join('?' for _ in campos)
    with conn:
        cur = conn.execute(f'INSERT INTO notas ({colunas}) VALUES ({marcas})', tuple(campos.values()))
    return cur.lastrowid


def atualizar(conn: sqlite3.Connection, nota_id: int, **campos) -> None:
    if not campos:
        return
    sets = ', '.join(f'{c}=?' for c in campos)
    with conn:
        conn.execute(f'UPDATE notas SET {sets} WHERE id=?', (*campos.values(), nota_id))


def por_chave(conn: sqlite3.Connection, chave: str) -> Optional[dict]:
    row = conn.execute('SELECT * FROM notas WHERE chave_acesso=?', (chave,)).fetchone()
    return dict(row) if row else None


def listar(conn: sqlite3.Connection, cnpj: Optional[str] = None, ambiente: Optional[str] = None,
           limite: int = 50) -> list[dict]:
    sql = 'SELECT * FROM notas'
    filtros, params = [], []
    if cnpj:
        filtros.append('cnpj=?')
        params.append(cnpj)
    if ambiente:
        filtros.append('ambiente=?')
        params.append(ambiente)
    if filtros:
        sql += ' WHERE ' + ' AND '.join(filtros)
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(limite)
    return [_expor(dict(r)) for r in conn.execute(sql, params)]


def _expor(row: dict) -> dict:
    if row.get('resposta'):
        try:
            row['resposta'] = json.loads(row['resposta'])
        except json.JSONDecodeError:
            pass
    return row
