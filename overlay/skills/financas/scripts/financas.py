#!/usr/bin/env python3
"""
Gestão financeira pessoal: receitas e despesas (planejadas e executadas), com recorrências.
Armazena histórico em SQLite local. Categorias definidas em arquivo Markdown editável.

Uso rápido:
  financas.py add despesa --desc "Mercado" --valor 350 --categoria alimentacao
  financas.py add receita --desc "Freelance" --valor 2000 --planejada --data 2026-05-20
  financas.py executar 3 --valor 1950
  financas.py recorrencia add despesa --desc "Aluguel" --valor 2500 --dia 10
  financas.py recorrencia gerar --mes 2026-05
  financas.py resumo --mes 2026-05
  financas.py listar --mes 2026-05 --status planejada
  financas.py categorias
"""

import sqlite3
import argparse
import json
import os
import sys
import calendar
from datetime import date
from pathlib import Path
from typing import Optional

SKILL_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_WORKSPACE = Path('/app/workspace')
WORKSPACE_ROOT = Path(os.environ.get('LZBOT_WORKSPACE_ROOT', str(_DEFAULT_WORKSPACE)))
_DATA_DIR = WORKSPACE_ROOT / 'skills' / SKILL_DIR.name
DB_PATH = _DATA_DIR / 'financas.db'
CATEGORIAS_PATH = _DATA_DIR / 'financas-categorias.md'

_CATEGORIAS_DEFAULT = """\
# Categorias Financeiras
> Edite este arquivo para personalizar suas categorias.
> Use listas com `-` sob os títulos `## Despesas` e `## Receitas`.
> Arquivo em uso: {path}

## Despesas
- moradia: Moradia
- alimentacao: Alimentação
- transporte: Transporte
- saude: Saúde
- lazer: Lazer
- assinaturas: Assinaturas
- educacao: Educação
- vestuario: Vestuário
- impostos: Impostos e Taxas
- dividendos: Dividendos Pagos
- servicos-contabeis: Serviços Contábeis
- outros: Outros

## Receitas
- salario: Salário
- freelance: Freelance
- investimentos: Investimentos
- bonus: Bônus
- aluguel-recebido: Aluguel Recebido
- outros: Outros
"""


## Categorias

def load_categorias() -> dict:
    """Carrega categorias do .md do usuário; cria o arquivo padrão na primeira execução.
    Formato: `- id: Nome Legível`
    Retorna dict com listas de IDs."""
    if not CATEGORIAS_PATH.exists():
        CATEGORIAS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CATEGORIAS_PATH.write_text(_CATEGORIAS_DEFAULT.format(path=CATEGORIAS_PATH), encoding='utf-8')

    cats = {'despesa': [], 'receita': []}
    section = None
    for line in CATEGORIAS_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.lower().startswith('## despesas'):
            section = 'despesa'
        elif line.lower().startswith('## receitas'):
            section = 'receita'
        elif line.startswith('- ') and section:
            # Formato: "- id: Nome" ou "- id" (compatibilidade retroativa)
            entry = line[2:].strip()
            if not entry:
                continue
            cat_id = entry.split(':')[0].strip()
            if cat_id:
                cats[section].append(cat_id)
    return cats


def listar_categorias_com_nome() -> dict:
    """Retorna dict com ID -> Nome legível para exibição."""
    result = {}
    if not CATEGORIAS_PATH.exists():
        return result
    section = None
    for line in CATEGORIAS_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.lower().startswith('## despesas') or line.lower().startswith('## receitas'):
            continue
        if line.startswith('- ') and ':' in line:
            parts = line[2:].split(':', 1)
            cat_id = parts[0].strip()
            cat_name = parts[1].strip()
            if cat_id and cat_name:
                result[cat_id] = cat_name
    return result


def warn_categoria(categoria: str, tipo: str, cats: dict):
    """Avisa se a categoria não está no arquivo, mas não bloqueia.
    A análise de similaridade semântica é feita pelo LLM (SKILL.md),
    que deve ler as categorias antes de chamar o script."""
    if categoria and categoria not in cats.get(tipo, []):
        print(f"⚠️  Categoria '{categoria}' não está em {CATEGORIAS_PATH.name} "
              f"(use `categorias` para ver a lista)", file=sys.stderr)


## Banco de Dados

def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection):
    """Cria as tabelas se não existirem."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recorrencias (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo        TEXT    NOT NULL CHECK(tipo IN ('receita','despesa')),
            descricao   TEXT    NOT NULL,
            valor       REAL    NOT NULL,
            categoria   TEXT,
            conta       TEXT,
            frequencia  TEXT    NOT NULL DEFAULT 'mensal'
                            CHECK(frequencia IN ('diaria','semanal','mensal','anual')),
            dia_ref     INTEGER,
            ativo       INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS transacoes (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo             TEXT    NOT NULL CHECK(tipo IN ('receita','despesa')),
            descricao        TEXT    NOT NULL,
            valor_previsto   REAL    NOT NULL,
            valor_executado  REAL,
            categoria        TEXT,
            conta            TEXT,
            fornecedor       TEXT,
            data_prevista    TEXT    NOT NULL,
            data_executada   TEXT,
            status           TEXT    NOT NULL DEFAULT 'planejada'
                                 CHECK(status IN ('planejada','executada','cancelada')),
            recorrencia_id   INTEGER REFERENCES recorrencias(id),
            observacao       TEXT,
            created_at       TEXT    DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def migrate_db(conn: sqlite3.Connection):
    """Adiciona colunas novas em bancos criados antes desta versão."""
    for table, col in [('transacoes', 'conta'), ('recorrencias', 'conta'),
                       ('transacoes', 'fornecedor'), ('recorrencias', 'fornecedor'),
                       ('transacoes', 'dados_pagamento')]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # coluna já existe


## Transações

def add_transacao(conn, tipo, descricao, valor, data_prevista, categoria=None,
                  conta=None, fornecedor=None, status='planejada',
                  valor_executado=None, data_executada=None,
                  recorrencia_id=None, observacao=None,
                  regime_tributario=None, atraso_maximo=None,
                  id_referencia=None, dados_pagamento=None) -> int:
    """Insere uma nova transação."""
    cur = conn.execute("""
        INSERT INTO transacoes
            (tipo, descricao, valor_previsto, valor_executado, categoria,
             conta, fornecedor, data_prevista, data_executada, status,
             recorrencia_id, observacao, regime_tributario, atraso_maximo, id_referencia,
             dados_pagamento)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (tipo, descricao, valor, valor_executado, categoria,
          conta, fornecedor, data_prevista, data_executada, status,
          recorrencia_id, observacao, regime_tributario, atraso_maximo,
          id_referencia, dados_pagamento))
    conn.commit()
    return cur.lastrowid


def executar_transacao(conn, id_: int, valor: Optional[float] = None,
                       data: Optional[str] = None, observacao: Optional[str] = None):
    """Marca uma transação planejada como executada."""
    row = conn.execute("SELECT * FROM transacoes WHERE id=?", (id_,)).fetchone()
    if not row:
        raise ValueError(f"Transação #{id_} não encontrada")
    if row['status'] == 'executada':
        raise ValueError(f"Transação #{id_} já foi executada")
    if row['status'] == 'cancelada':
        raise ValueError(f"Transação #{id_} está cancelada")

    conn.execute("""
        UPDATE transacoes
        SET status='executada',
            valor_executado=COALESCE(?,valor_previsto),
            data_executada=COALESCE(?,date('now')),
            observacao=COALESCE(?,observacao)
        WHERE id=?
    """, (valor, data, observacao, id_))
    conn.commit()


def cancelar_transacao(conn, id_: int):
    """Cancela uma transação."""
    if not conn.execute("SELECT id FROM transacoes WHERE id=?", (id_,)).fetchone():
        raise ValueError(f"Transação #{id_} não encontrada")
    conn.execute("UPDATE transacoes SET status='cancelada' WHERE id=?", (id_,))
    conn.commit()


def listar_transacoes(conn, mes=None, tipo=None, status=None, conta=None):
    """Lista transações com filtros opcionais."""
    conds, params = [], []
    if mes:
        conds.append("strftime('%Y-%m', data_prevista) = ?")
        params.append(mes)
    if tipo:
        conds.append("tipo = ?")
        params.append(tipo)
    if status:
        conds.append("status = ?")
        params.append(status)
    if conta:
        conds.append("conta = ?")
        params.append(conta)

    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    return conn.execute(
        f"SELECT * FROM transacoes {where} ORDER BY data_prevista, tipo", params
    ).fetchall()


## Recorrências

def add_recorrencia(conn, tipo, descricao, valor, frequencia='mensal',
                    dia_ref=None, categoria=None, conta=None,
                    fornecedor=None, regime_tributario=None,
                    atraso_maximo=None) -> int:
    """Cadastra uma recorrência."""
    cur = conn.execute("""
        INSERT INTO recorrencias (tipo, descricao, valor, categoria, conta, fornecedor, frequencia, dia_ref, regime_tributario, atraso_maximo)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (tipo, descricao, valor, categoria, conta, fornecedor, frequencia, dia_ref, regime_tributario, atraso_maximo))
    conn.commit()
    return cur.lastrowid


def gerar_recorrencias(conn, mes: str) -> list:
    """Gera transações planejadas do mês a partir das recorrências ativas."""
    ano, m = map(int, mes.split('-'))
    geradas = []

    for rec in conn.execute("SELECT * FROM recorrencias WHERE ativo=1").fetchall():
        # Usar dia de referência respeitando o último dia do mês
        dia = min(rec['dia_ref'] or 1, calendar.monthrange(ano, m)[1])
        data_prevista = f"{ano:04d}-{m:02d}-{dia:02d}"

        # Pular se já existe transação gerada por essa recorrência neste mês
        existe = conn.execute("""
            SELECT id FROM transacoes
            WHERE recorrencia_id=? AND strftime('%Y-%m', data_prevista)=?
        """, (rec['id'], mes)).fetchone()

        if not existe:
            # Pega fornecedor com fallback para None (coluna pode não existir em bancos antigos)
            try:
                fornecedor = rec['fornecedor']
            except (IndexError, KeyError):
                fornecedor = None
            atr = rec['atraso_maximo'] if 'atraso_maximo' in rec else None
            id_ = add_transacao(conn, rec['tipo'], rec['descricao'], rec['valor'],
                                data_prevista, rec['categoria'], conta=rec['conta'],
                                fornecedor=fornecedor, atraso_maximo=atr,
                                recorrencia_id=rec['id'])
            geradas.append({'id': id_, 'descricao': rec['descricao'], 'data': data_prevista})

    return geradas


def listar_recorrencias(conn):
    return conn.execute(
        "SELECT * FROM recorrencias ORDER BY ativo DESC, tipo, descricao"
    ).fetchall()


def desativar_recorrencia(conn, id_: int):
    if not conn.execute("SELECT id FROM recorrencias WHERE id=?", (id_,)).fetchone():
        raise ValueError(f"Recorrência #{id_} não encontrada")
    conn.execute("UPDATE recorrencias SET ativo=0 WHERE id=?", (id_,))
    conn.commit()


## Resumo / Saldo

def resumo_mes(conn, mes: str, conta: Optional[str] = None) -> dict:
    """Calcula resumo do mês: planejado vs executado por tipo."""
    r = {
        'mes': mes,
        'receita': {'planejado': 0.0, 'executado': 0.0},
        'despesa': {'planejado': 0.0, 'executado': 0.0},
    }
    for t in listar_transacoes(conn, mes=mes, conta=conta):
        if t['status'] == 'cancelada':
            continue
        r[t['tipo']]['planejado'] += t['valor_previsto']
        if t['status'] == 'executada':
            r[t['tipo']]['executado'] += t['valor_executado'] or t['valor_previsto']

    r['saldo_previsto'] = r['receita']['planejado'] - r['despesa']['planejado']
    r['saldo_real'] = r['receita']['executado'] - r['despesa']['executado']
    return r


## Formatação

def fmt_valor(v: float) -> str:
    """Formata valor como moeda brasileira."""
    return f"R$ {v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


# ── Formatação compartilhada de relatórios (TW=30, DW=17, VW=12) ──

TW = 30
DW = 17
VW = 12
SEP = '━' * TW


def _fmt(v: float) -> str:
    """Formata float para moeda BR (R$ X.XXX,XX)."""
    return '{:,.2f}'.format(v).replace(',', 'X').replace('.', ',').replace('X', '.')


def report_line(descricao: str, valor: float) -> str:
    """Linha padrão de relatório: descrição truncada à esquerda, valor alinhado à direita."""
    # Formato brasileiro: R$ X.XXX,XX — ponto para milhar, vírgula para decimal
    vstr = 'R$ ' + _fmt(valor)
    # Se valor excede VW, estoura alinhamento mas mantém legibilidade (separador de milhar preservado)
    return descricao[:DW].ljust(DW) + ' ' + vstr.rjust(VW)


def report_header(titulo: str) -> str:
    """Título centralizado grosseiramente no espaço de descrição, sem valor."""
    return titulo.ljust(DW) + ' ' + ' ' * VW


def report_separator() -> str:
    return SEP


def fmt_transacao(t) -> str:
    icon = '💰' if t['tipo'] == 'receita' else '💸'
    status_icon = {'planejada': '⏳', 'executada': '✅', 'cancelada': '❌'}[t['status']]
    valor = fmt_valor(t['valor_executado'] or t['valor_previsto'])
    cat = f" [{t['categoria']}]" if t['categoria'] else ''
    rec = ' 🔁' if t['recorrencia_id'] else ''
    # Exibe conta (banco/instituição) quando presente
    conta = f" 🏦 {t['conta']}" if t['conta'] else ''
    # Exibe fornecedor quando presente
    try:
        forn_val = t['fornecedor']
    except (IndexError, KeyError):
        forn_val = None
    forn = f" 📦{forn_val}" if forn_val else ''
    # Exibe indicador de dados de pagamento (PIX/boleto)
    try:
        dp = t['dados_pagamento']
    except (IndexError, KeyError):
        dp = None
    pag = ' 💳' if dp else ''
    return f"{status_icon} #{t['id']} {icon} {t['data_prevista']} — {t['descricao']}{cat}{conta}{forn}{rec}{pag}  {valor}"


def fmt_recorrencia(r) -> str:
    icon = '💰' if r['tipo'] == 'receita' else '💸'
    ativo = '✅' if r['ativo'] else '❌'
    dia = f" (dia {r['dia_ref']})" if r['dia_ref'] else ''
    cat = f" [{r['categoria']}]" if r['categoria'] else ''
    # Exibe conta (banco/instituição) quando presente
    conta = f" 🏦 {r['conta']}" if r['conta'] else ''
    # Exibe fornecedor quando presente
    try:
        forn_val = r['fornecedor']
    except (IndexError, KeyError):
        forn_val = None
    forn = f" 📦{forn_val}" if forn_val else ''
    return f"{ativo} #{r['id']} {icon} {r['descricao']}{cat}{conta}{forn} — {fmt_valor(r['valor'])} / {r['frequencia']}{dia}"


## Comandos


# Calcular impostos - Lucro Presumido

def calcular_imposto_lucro_presumido(valor_receita: float, aliquota_iss: float = 5.0) -> dict:
    """
    Calcula os tributos mensais para uma receita de servico no Lucro Presumido.
    Retorna dict com os valores calculados de PIS, COFINS, ISS, IRPJ, CSLL e total.
    Aliquota ISS padrao: 5%.
    Presuncao para servicos: 32%.
    """
    pis = round(valor_receita * 0.0065, 2)
    cofins = round(valor_receita * 0.03, 2)
    iss = round(valor_receita * (aliquota_iss / 100), 2)
    base_presumida = round(valor_receita * 0.32, 2)
    irpj = round(base_presumida * 0.15, 2)
    csll = round(base_presumida * 0.09, 2)
    total = round(pis + cofins + iss + irpj + csll, 2)
    return {
        'pis': pis, 'cofins': cofins, 'iss': iss,
        'irpj': irpj, 'csll': csll, 'total': total,
        'aliquota_iss': aliquota_iss,
        'base_presumida': base_presumida
    }


def gerar_lancamentos_imposto(conn, receita_id: int, valor_receita: float,
                               data_base: str, conta: str = None,
                               aliquota_iss: float = 5.0) -> list:
    """
    Gera transacoes de imposto para cada tributo calculado a partir de uma receita.
    Retorna lista de ids das transacoes criadas.
    """
    tributos = calcular_imposto_lucro_presumido(valor_receita, aliquota_iss)
    ids = []
    trib_map = [
        ('PIS sobre Receita', tributos['pis']),
        ('COFINS sobre Receita', tributos['cofins']),
        ('ISS sobre Receita', tributos['iss']),
        ('IRPJ (Lucro Presumido)', tributos['irpj']),
        ('CSLL (Lucro Presumido)', tributos['csll']),
    ]
    for desc, val in trib_map:
        tid = add_transacao(
            conn, 'despesa', desc, val,
            data_prevista=data_base, categoria='impostos',
            conta=conta, fornecedor=None,
            status='planejada', id_referencia=receita_id
        )
        ids.append(tid)
    return ids

def cmd_add(conn, args):
    """
    Adiciona uma transação avulsa.
    Por padrão é registrada como EXECUTADA (já aconteceu).
    Use --planejada para registrar como pendente (ex: conta a pagar, receita futura).
    Use --calcular-imposto para gerar automaticamente os lancamentos de PIS, COFINS, ISS, IRPJ, CSLL.
    """
    cats = load_categorias()
    if args.categoria:
        warn_categoria(args.categoria, args.tipo, cats)

    status = 'planejada' if args.planejada else 'executada'
    data = args.data or date.today().isoformat()
    val_exec = None if args.planejada else args.valor
    data_exec = None if args.planejada else data

    id_ = add_transacao(
        conn, args.tipo, args.desc, args.valor,
        data_prevista=data, categoria=args.categoria,
        conta=args.conta, fornecedor=args.fornecedor,
        status=status, valor_executado=val_exec,
        data_executada=data_exec, observacao=args.obs,
        regime_tributario='lucro-presumido' if args.calcular_imposto else None,
        atraso_maximo=args.atraso_maximo,
        dados_pagamento=args.dados_pagamento
    )
    out = {'id': id_, 'tipo': args.tipo, 'descricao': args.desc,
           'valor': args.valor, 'status': status, 'data': data}
    if args.dados_pagamento:
        out['dados_pagamento'] = args.dados_pagamento

    if args.calcular_imposto and args.tipo == 'receita':
        tributos = calcular_imposto_lucro_presumido(args.valor, args.aliquota_iss)
        ids_imp = gerar_lancamentos_imposto(
            conn, id_, args.valor, data,
            conta=args.conta, aliquota_iss=args.aliquota_iss
        )
        out['tributos_calculados'] = tributos
        out['lancamentos_imposto_ids'] = ids_imp

    print(json.dumps(out, ensure_ascii=False))


def cmd_executar(conn, args):
    """
    Marca uma transacao planejada como executada.
    Use --valor se o valor real for diferente do previsto.
    """
    executar_transacao(conn, args.id, args.valor, args.data, args.obs)
    out = {'id': args.id, 'status': 'executada'}
    if args.valor is not None:
        out['valor_executado'] = args.valor
    print(json.dumps(out, ensure_ascii=False))


def cmd_cancelar(conn, args):
    """Cancela uma transação planejada (ex: despesa que não vai ocorrer mais)."""
    cancelar_transacao(conn, args.id)
    print(json.dumps({"id": args.id, "status": "cancelada"}, ensure_ascii=False))


def cmd_atualizar(conn, args):
    """
    Atualiza campos específicos de uma transação existente.
    Aceita: --data, --dados-pagamento, --obs, --desc, --valor, --categoria,
            --conta, --fornecedor.
    """
    row = conn.execute("SELECT * FROM transacoes WHERE id=?", (args.id,)).fetchone()
    if not row:
        raise ValueError(f"Transação #{args.id} não encontrada")

    # Coleta apenas os campos informados
    updates = {}
    if args.data is not None:
        updates['data_prevista'] = args.data
    if args.dados_pagamento is not None:
        updates['dados_pagamento'] = args.dados_pagamento
    if args.obs is not None:
        updates['observacao'] = args.obs
    if args.desc is not None:
        updates['descricao'] = args.desc
    if args.valor is not None:
        updates['valor_previsto'] = args.valor
    if args.categoria is not None:
        updates['categoria'] = args.categoria
    if args.conta is not None:
        updates['conta'] = args.conta
    if args.fornecedor is not None:
        updates['fornecedor'] = args.fornecedor

    if not updates:
        print("ℹ️  Nenhum campo para atualizar. Use --data, --dados-pagamento, --obs, --desc, --valor, --categoria, --conta ou --fornecedor")
        return

    set_clause = ', '.join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [args.id]
    conn.execute(f"UPDATE transacoes SET {set_clause} WHERE id=?", values)
    conn.commit()

    out = {'id': args.id, 'status': 'atualizada', 'campos': list(updates.keys())}
    print(json.dumps(out, ensure_ascii=False))


def cmd_listar(conn, args):
    """Lista transacoes do mes com filtros opcionais."""
    mes = args.mes or date.today().strftime('%Y-%m')
    rows = listar_transacoes(conn, mes=mes, tipo=args.tipo, status=args.status, conta=args.conta)
    data = []
    for r in rows:
        data.append({
            'id': r['id'], 'tipo': r['tipo'], 'descricao': r['descricao'],
            'valor_previsto': r['valor_previsto'],
            'valor_executado': r['valor_executado'],
            'categoria': r['categoria'], 'conta': r['conta'],
            'fornecedor': r['fornecedor'] if 'fornecedor' in r.keys() else None,
            'data_prevista': r['data_prevista'],
            'data_executada': r['data_executada'],
            'status': r['status'], 'recorrencia_id': r['recorrencia_id'],
            'observacao': r['observacao'],
            'atraso_maximo': r['atraso_maximo'],
            'dados_pagamento': r['dados_pagamento'] if 'dados_pagamento' in r.keys() else None
        })
    if args.report:
        L = []
        L.append('Transacoes - ' + mes)
        L.append(report_separator())
        for r in data:
            icon = {'planejada': '⏳', 'executada': '✅', 'cancelada': '❌'}.get(r['status'], '?')
            vals = r['valor_executado'] or r['valor_previsto']
            # Indicador de dados de pagamento (PIX ou boleto) — antes do ID para não ser truncado
            pag = '💳' if r['dados_pagamento'] else ''
            L.append(report_line(icon + pag + ' #' + str(r['id']) + ' ' + r['descricao'], vals))
        L.append(report_separator())
        print('\n'.join(L))
        return
    print(json.dumps({'mes': mes, 'transacoes': data}, ensure_ascii=False))



def cmd_resumo(conn, args):
    """
    Resumo mensal com planejado vs executado.
    PLANEJADO = soma de todas as transações não canceladas (incluindo pendentes).
    EXECUTADO = soma apenas das transações já confirmadas.
    """
    mes = args.mes or date.today().strftime('%Y-%m')
    r = resumo_mes(conn, mes, conta=args.conta)

    if args.report:
        L = []
        L.append('Resumo - ' + mes)
        L.append(report_separator())
        L.append(report_line('Receitas planejadas', r['receita']['planejado']))
        L.append(report_line('Receitas executadas', r['receita']['executado']))
        L.append('')
        L.append(report_line('Despesas planejadas', r['despesa']['planejado']))
        L.append(report_line('Despesas executadas', r['despesa']['executado']))
        L.append('')
        L.append(report_separator())
        L.append(report_line('Saldo previsto', r['saldo_previsto']))
        L.append(report_line('Saldo real', r['saldo_real']))
        L.append(report_separator())
        print('\n'.join(L))
        return

    print(json.dumps({'mes': mes, **r}, ensure_ascii=False))


def cmd_saldo(conn, args):
    """Saldo real do mes: receitas executadas menos despesas executadas."""
    mes = args.mes or date.today().strftime('%Y-%m')
    r = resumo_mes(conn, mes)
    if args.report:
        L = []
        L.append('Saldo - ' + mes)
        L.append(report_separator())
        L.append(report_line('Receitas', r['receita']['executado']))
        L.append(report_line('Despesas', r['despesa']['executado']))
        L.append(report_separator())
        L.append(report_line('Saldo', r['saldo_real']))
        L.append(report_separator())
        print('\n'.join(L))
        return
    print(json.dumps({'mes': mes, 'saldo': r['saldo_real'],
                      'receitas': r['receita']['executado'],
                      'despesas': r['despesa']['executado']},
                     ensure_ascii=False))



def format_dre_report(mes, categorias, lancamentos, total_receitas, total_despesas, saldo_mes, saldo_acum, conta=None, nomes_cat=None):
    """Gera o DRE formatado pronto para exibicao, com valores alinhados a direita."""
    if nomes_cat is None:
        nomes_cat = {}
    
    L = []
    L.append('DRE --- ' + mes)
    L.append(report_separator())
    L.append('')
    L.append(report_line('RECEITAS', total_receitas))
    L.append('')

    rec_cats = [c for c in categorias if c['tipo'] == 'receita']
    desp_cats = [c for c in categorias if c['tipo'] == 'despesa']
    rec_lancs = [l for l in lancamentos if l['tipo'] == 'receita']
    desp_lancs = [l for l in lancamentos if l['tipo'] == 'despesa']

    for cat in rec_cats:
        cn = nomes_cat.get(cat['categoria'], cat['categoria'])
        cl = [l for l in rec_lancs if l['categoria'] == (cat['categoria'] or 'sem-categoria')]
        if len(cl) > 1:
            L.append(report_line(' ' + cn, cat['total']))
            for i, l in enumerate(cl):
                p = '└' if i == len(cl) - 1 else '├'
                L.append(report_line(' ' + p + ' ' + l['descricao'], l['valor']))
        else:
            L.append(' ' + cn)
            L.append(report_line(' └ ' + cl[0]['descricao'], cl[0]['valor']))
        L.append('')

    L.append(report_separator())
    L.append('')
    L.append(report_line('DESPESAS', total_despesas))
    L.append('')

    for cat in desp_cats:
        cn = nomes_cat.get(cat['categoria'], cat['categoria'])
        cl = [l for l in desp_lancs if l['categoria'] == (cat['categoria'] or 'sem-categoria')]
        if len(cl) > 1:
            L.append(report_line(' ' + cn, cat['total']))
            for i, l in enumerate(cl):
                p = '└' if i == len(cl) - 1 else '├'
                txt = l['descricao']
                if txt == 'DAS - Simples Nacional':
                    txt = 'DAS Simples Nac.'
                elif txt == 'DARF - INSS Sócios':
                    txt = 'DARF INSS Sócios'
                elif txt == 'IRPJ (Lucro Presumido)':
                    txt = 'IRPJ L. Presumido'
                elif txt == 'CSLL (Lucro Presumido)':
                    txt = 'CSLL L. Presumido'
                elif 'PIS sobre' in txt:
                    txt = 'PIS'
                elif 'COFINS sobre' in txt:
                    txt = 'COFINS'
                elif 'ISS sobre' in txt:
                    txt = 'ISS'
                elif txt == 'Alisson Dividendos':
                    txt = 'Alisson'
                elif txt == 'Contabilizei':
                    txt = 'Contabilizei'
                L.append(report_line(' ' + p + ' ' + txt, l['valor']))
        else:
            L.append(' ' + cn)
            L.append(report_line(' └ ' + cl[0]['descricao'], cl[0]['valor']))
        L.append('')

    L.append(report_separator())
    L.append('')
    L.append(report_line('Total Receitas', total_receitas))
    L.append(report_line('Total Despesas', total_despesas))
    L.append(report_line('Resultado do Mes', saldo_mes))
    L.append(report_line('Saldo Acumulado', saldo_acum))
    L.append('')
    L.append(report_separator())
    return '\n'.join(L)

def cmd_dre(conn, args):
    """
    DRE detalhado: receitas e despesas agrupadas por categoria,
    com lancamentos individuais. Aceita --conta como filtro.
    Gera automaticamente as recorrências do mês antes de calcular.
    """
    mes = args.mes or date.today().strftime('%Y-%m')
    # Garantir que recorrências do mês estão geradas
    gerar_recorrencias(conn, mes)
    ano = mes.split('-')[0]
    filtro_conta = args.conta

    wheres = ["strftime('%Y-%m', data_prevista)=?", "status != 'cancelada'"]
    params = [mes]
    if filtro_conta:
        wheres.append("conta=?")
        params.append(filtro_conta)
    where = ' AND '.join(wheres)

    cats = conn.execute(
        "SELECT tipo, categoria, SUM(valor_previsto) as total "
        "FROM transacoes WHERE " + where + " "
        "GROUP BY tipo, categoria ORDER BY tipo, categoria", params).fetchall()

    lancs = conn.execute(
        "SELECT id, tipo, descricao, categoria, conta, valor_previsto, "
        "valor_executado, status, recorrencia_id, fornecedor, dados_pagamento "
        "FROM transacoes WHERE " + where + " "
        "ORDER BY tipo, data_prevista", params).fetchall()

    acum_params = [ano]
    if filtro_conta:
        acum_wheres = ["strftime('%Y', data_prevista)=?", "status != 'cancelada'",
                       "conta=?", "strftime('%Y-%m', data_prevista) <= ?"]
        acum_params += [filtro_conta, mes]
    else:
        acum_wheres = ["strftime('%Y', data_prevista)=?", "status != 'cancelada'",
                       "strftime('%Y-%m', data_prevista) <= ?"]
        acum_params.append(mes)

    acum = conn.execute(
        "SELECT SUM(CASE WHEN tipo='receita' THEN valor_previsto ELSE 0 END) as rec, "
        "SUM(CASE WHEN tipo='despesa' THEN valor_previsto ELSE 0 END) as desp "
        "FROM transacoes WHERE " + ' AND '.join(acum_wheres), acum_params).fetchone()
    saldo_acum = (acum['rec'] or 0) - (acum['desp'] or 0)

    saida = {'mes': mes, 'conta': filtro_conta, 'categorias': [], 'lancamentos': [],
             'total_receitas': 0, 'total_despesas': 0, 'saldo_mes': 0,
             'saldo_acumulado': saldo_acum}

    for r in cats:
        saida['categorias'].append({
            'tipo': r['tipo'], 'categoria': r['categoria'] or 'sem-categoria',
            'total': r['total']
        })
        if r['tipo'] == 'receita':
            saida['total_receitas'] += r['total']
        else:
            saida['total_despesas'] += r['total']

    for l in lancs:
        try:
            forn_val = l['fornecedor']
        except (IndexError, KeyError):
            forn_val = None
        try:
            dp_val = l['dados_pagamento']
        except (IndexError, KeyError):
            dp_val = None
        saida['lancamentos'].append({
            'id': l['id'], 'tipo': l['tipo'], 'descricao': l['descricao'],
            'categoria': l['categoria'] or 'sem-categoria',
            'conta': l['conta'], 'fornecedor': forn_val,
            'valor': l['valor_executado'] or l['valor_previsto'],
            'status': l['status'],
            'dados_pagamento': dp_val
        })

    saida['saldo_mes'] = saida['total_receitas'] - saida['total_despesas']

    if args.report:
        nomes = listar_categorias_com_nome()
        texto = format_dre_report(
            mes, saida['categorias'], saida['lancamentos'],
            saida['total_receitas'], saida['total_despesas'],
            saida['saldo_mes'], saida['saldo_acumulado'],
            conta=filtro_conta, nomes_cat=nomes
        )
        print(texto)
        return

    print(json.dumps(saida, ensure_ascii=False))




def cmd_atrasados(conn, args):
    """
    Lista transacoes planejadas com data vencida, exibindo a data maxima de pagamento
    baseada no campo atraso_maximo (se definido).
    """
    from datetime import date, datetime, timedelta
    hoje = date.today()
    cur = conn.execute("""
        SELECT id, tipo, descricao, valor_previsto, data_prevista, 
               conta, categoria, atraso_maximo, recorrencia_id, dados_pagamento
        FROM transacoes 
        WHERE status = 'planejada' 
          AND data_prevista < ?
        ORDER BY data_prevista
    """, (hoje.isoformat(),))
    
    rows = cur.fetchall()
    saida = []
    for r in rows:
        prev = datetime.strptime(r['data_prevista'], '%Y-%m-%d').date()
        atr = r['atraso_maximo']
        data_max = (prev + timedelta(days=atr)).isoformat() if atr else None
        dias_vencido = (hoje - prev).days
        dias_restantes = atr - dias_vencido if atr else None
        
        item = {
            'id': r['id'],
            'tipo': r['tipo'],
            'descricao': r['descricao'],
            'valor': r['valor_previsto'],
            'data_vencimento': r['data_prevista'],
            'data_max_pagamento': data_max,
            'dias_vencido': dias_vencido,
            'dias_restantes': dias_restantes,
            'conta': r['conta'],
            'categoria': r['categoria'],
            'dados_pagamento': r['dados_pagamento'] if 'dados_pagamento' in r.keys() else None,
        }
        saida.append(item)
    
    if args.report:
        def dd(s):
            return datetime.strptime(s, '%Y-%m-%d').strftime('%d/%m')
        L = []
        L.append('Atrasados')
        L.append(report_separator())
        total = 0
        for r in rows:
            prev = datetime.strptime(r['data_prevista'], '%Y-%m-%d').date()
            atr = r['atraso_maximo']
            data_max = (prev + timedelta(days=atr)) if atr else None
            dias_vencido = (hoje - prev).days
            dias_restantes = atr - dias_vencido if atr else None
            L.append(report_line(r['descricao'], r['valor_previsto']))
            info = '     Venceu ' + dd(r['data_prevista']) + ' (' + str(dias_vencido) + 'd)'
            if data_max:
                info += ' Pagar ate ' + data_max.strftime('%d/%m') + ' (' + str(dias_restantes) + 'd)'
            L.append(info)
            total += r['valor_previsto']
        L.append(report_separator())
        L.append(report_line('Total atrasado', total))
        L.append(report_separator())
        print('\n'.join(L))
    else:
        print(json.dumps(saida, ensure_ascii=False))

def cmd_categorias(conn, args):
    """Exibe as categorias disponiveis."""
    cats = load_categorias()
    nomes = listar_categorias_com_nome()
    data = {"path": str(CATEGORIAS_PATH), "despesas": [], "receitas": []}
    for c in cats["despesa"]:
        data["despesas"].append({"id": c, "nome": nomes.get(c, c)})
    for c in cats["receita"]:
        data["receitas"].append({"id": c, "nome": nomes.get(c, c)})
    print(json.dumps(data, ensure_ascii=False))


def cmd_rec_add(conn, args):
    """
    Cadastra uma receita ou despesa recorrente.
    Use `recorrencia gerar --mes YYYY-MM` para criar as transações planejadas do mês.
    """
    cats = load_categorias()
    if args.categoria:
        warn_categoria(args.categoria, args.tipo, cats)

    id_ = add_recorrencia(conn, args.tipo, args.desc, args.valor,
                          args.frequencia, args.dia, args.categoria,
                          args.conta, args.fornecedor, atraso_maximo=args.atraso_maximo)
    out = {'id': id_, 'tipo': args.tipo, 'descricao': args.desc,
           'valor': args.valor, 'frequencia': args.frequencia}
    print(json.dumps(out, ensure_ascii=False))


def cmd_rec_listar(conn, args):
    rows = listar_recorrencias(conn)
    data = []
    for r in rows:
        data.append({
            "id": r["id"], "tipo": r["tipo"], "descricao": r["descricao"],
            "valor": r["valor"], "categoria": r["categoria"],
            "conta": r["conta"], "fornecedor": r["fornecedor"] if "fornecedor" in r.keys() else None,
            "frequencia": r["frequencia"], "dia_ref": r["dia_ref"],
            "ativo": bool(r["ativo"]),
            "atraso_maximo": r["atraso_maximo"]
        })
    if args.report:
        L = []
        L.append('Recorrencias')
        L.append(report_separator())
        for r in data:
            icon = '💰' if r['tipo'] == 'receita' else '💸'
            ativo = '✅' if r['ativo'] else '❌'
            L.append(report_line(ativo + ' #' + str(r['id']) + ' ' + icon + ' ' + r['descricao'], r['valor']))
        L.append(report_separator())
        print('\n'.join(L))
        return
    print(json.dumps(data, ensure_ascii=False))


def cmd_rec_gerar(conn, args):
    """
    Gera transações planejadas para o mês a partir das recorrências ativas.
    Idempotente: não duplica se chamado mais de uma vez no mesmo mês.
    Se a recorrência for lucro-presumido, gera automaticamente os lancamentos de imposto.
    """
    mes = args.mes or date.today().strftime('%Y-%m')
    geradas = gerar_recorrencias(conn, mes)

    # Gerar lancamentos de imposto para recorrências lucro-presumido
    impostos_gerados = []
    for rec in conn.execute("SELECT * FROM recorrencias WHERE ativo=1 AND regime_tributario='lucro-presumido'").fetchall():
        # Find the transaction just created for this recurrency
        ano, m = map(int, mes.split('-'))
        dia = min(rec['dia_ref'] or 1, calendar.monthrange(ano, m)[1])
        data_str = f'{ano}-{m:02d}-{dia:02d}'
        tx = conn.execute(
            "SELECT id FROM transacoes WHERE recorrencia_id=? AND data_prevista=? AND status='planejada'",
            (rec['id'], data_str)
        ).fetchone()
        if tx:
            # Verificar se ja existem lancamentos de imposto vinculados a esta receita
            imp_existente = conn.execute(
                "SELECT COUNT(*) FROM transacoes WHERE id_referencia=? AND status='planejada'",
                (tx['id'],)
            ).fetchone()[0]
            if imp_existente > 0:
                continue
            imp_ids = gerar_lancamentos_imposto(
                conn, tx['id'], rec['valor'], data_str,
                conta=rec['conta']
            )
            impostos_gerados.append({
                'rec_id': rec['id'],
                'receita_id': tx['id'],
                'lancamentos_ids': imp_ids
            })

    out = {'mes': mes, 'geradas': geradas}
    if impostos_gerados:
        out['impostos_gerados'] = impostos_gerados
    print(json.dumps(out, ensure_ascii=False))


def cmd_rec_desativar(conn, args):
    desativar_recorrencia(conn, args.id)
    print(json.dumps({'id': args.id, 'status': 'desativada'}, ensure_ascii=False))


def cmd_rec_atualizar(conn, args):
    """
    Atualiza uma recorrência e propaga as mudanças
    para todas as transações futuras (status planejada)
    vinculadas a ela.
    """
    rec = conn.execute("SELECT * FROM recorrencias WHERE id=?", (args.id,)).fetchone()
    if not rec:
        print(f"❌ Recorrência #{args.id} não encontrada")
        return

    # Coleta campos alterados
    updates = {}
    campos_texto = {'descricao': args.desc, 'categoria': args.categoria,
                    'conta': args.conta, 'fornecedor': args.fornecedor}
    for campo, valor in campos_texto.items():
        if valor is not None:
            updates[campo] = valor
    if args.valor is not None:
        updates['valor'] = args.valor
    if args.dia is not None:
        updates['dia_ref'] = args.dia
    if args.atraso_maximo is not None:
        updates['atraso_maximo'] = args.atraso_maximo

    if not updates:
        print("ℹ️  Nenhum campo para atualizar. Use --desc, --valor, --dia, --categoria, --conta, --fornecedor ou --atraso-maximo")
        return

    # Atualiza a recorrência
    set_clause = ', '.join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [args.id]
    conn.execute(f"UPDATE recorrencias SET {set_clause} WHERE id=?", values)
    conn.commit()

    # Propaga para transacoes futuras (status planejada, mes > atual)
    hoje = date.today()
    # Primeiro dia do mes seguinte
    if hoje.month == 12:
        prox_mes = f"{hoje.year + 1:04d}-01"
    else:
        prox_mes = f"{hoje.year:04d}-{hoje.month + 1:02d}"
    prop_updates = {}
    for campo, novo_valor in updates.items():
        if campo == 'descricao':
            prop_updates['descricao'] = novo_valor
        elif campo == 'valor':
            prop_updates['valor_previsto'] = novo_valor
        elif campo == 'categoria':
            prop_updates['categoria'] = novo_valor
        elif campo == 'conta':
            prop_updates['conta'] = novo_valor
        elif campo == 'fornecedor':
            prop_updates['fornecedor'] = novo_valor

    if prop_updates:
        prop_set = ', '.join(f"{k}=?" for k in prop_updates)
        prop_vals = list(prop_updates.values())
        conn.execute(f"""
            UPDATE transacoes
            SET {prop_set}
            WHERE recorrencia_id=?
              AND status='planejada'
              AND strftime('%Y-%m', data_prevista) >= ?
        """, prop_vals + [args.id, prox_mes])
        conn.commit()

    out = {'id': args.id, 'status': 'atualizada', 'campos': list(updates.keys())}
    print(json.dumps(out, ensure_ascii=False))


## CLI

def main():
    parser = argparse.ArgumentParser(
        description='Gestão financeira pessoal — receitas, despesas, recorrências.',
        epilog=(
            'Exemplos:\n'
            '  %(prog)s add despesa --desc "Mercado" --valor 350 --categoria alimentacao\n'
            '  %(prog)s add receita --desc "Freelance" --valor 2000 --planejada --data 2026-05-20\n'
            '  %(prog)s executar 3 --valor 1950\n'
            '  %(prog)s recorrencia add despesa --desc "Aluguel" --valor 2500 --dia 10\n'
            '  %(prog)s recorrencia gerar --mes 2026-05\n'
            '  %(prog)s resumo --mes 2026-05\n'
            '  %(prog)s listar --mes 2026-05 --status planejada\n'
            '  %(prog)s categorias'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    # add
    p_add = sub.add_parser('add', help='Adicionar transação avulsa',
                           description=cmd_add.__doc__,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
    p_add.add_argument('tipo', choices=['receita', 'despesa'])
    p_add.add_argument('--desc', required=True, metavar='TEXTO', help='Descrição da transação')
    p_add.add_argument('--valor', type=float, required=True, metavar='VALOR', help='Valor (ex: 350.00)')
    p_add.add_argument('--data', metavar='YYYY-MM-DD', help='Data da transação (padrão: hoje)')
    p_add.add_argument('--categoria', required=True, metavar='CAT', help='Categoria (use `categorias` para ver a lista)')
    p_add.add_argument('--conta', metavar='CONTA', help='Conta/banco/instituição (ex: Nubank, Itaú, C6)')
    p_add.add_argument('--fornecedor', metavar='NOME', help='Fornecedor/contraparte da transação')
    p_add.add_argument('--atraso-maximo', type=int, metavar='DIAS', help='Prazo maximo em dias para atraso')
    p_add.add_argument('--planejada', action='store_true', help='Registrar como planejada/pendente')
    p_add.add_argument('--obs', metavar='TEXTO', help='Observação livre')
    p_add.add_argument('--dados-pagamento', metavar='TEXTO', dest='dados_pagamento', help='Dados de pagamento. Use "pix:CODIGO" ou "boleto:CODIGO_BARRAS"')
    p_add.add_argument('--calcular-imposto', action='store_true', help='Calcular e gerar lancamentos de imposto (Lucro Presumido)')
    p_add.add_argument('--aliquota-iss', type=float, default=5.0, help='Aliquota ISS em %% (padrao: 5)')

    # executar
    p_exec = sub.add_parser('executar', help='Confirmar transação planejada como executada',
                            description=cmd_executar.__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    p_exec.add_argument('id', type=int, metavar='ID', help='ID da transação planejada')
    p_exec.add_argument('--valor', type=float, metavar='VALOR', help='Valor real executado (se diferente do previsto)')
    p_exec.add_argument('--data', metavar='YYYY-MM-DD', help='Data de execução (padrão: hoje)')
    p_exec.add_argument('--obs', metavar='TEXTO', help='Observação')

    # cancelar
    p_cancel = sub.add_parser('cancelar', help='Cancelar transação',
                              description=cmd_cancelar.__doc__,
                              formatter_class=argparse.RawDescriptionHelpFormatter)
    p_cancel.add_argument('id', type=int, metavar='ID')

    # atualizar
    p_atualizar = sub.add_parser('atualizar', help='Atualizar campos de uma transação existente',
                                 description=cmd_atualizar.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p_atualizar.add_argument('id', type=int, metavar='ID', help='ID da transação a atualizar')
    p_atualizar.add_argument('--data', metavar='YYYY-MM-DD', help='Nova data prevista')
    p_atualizar.add_argument('--dados-pagamento', metavar='TEXTO', dest='dados_pagamento', help='Dados de pagamento. Use "pix:CODIGO" ou "boleto:CODIGO_BARRAS"')
    p_atualizar.add_argument('--obs', metavar='TEXTO', help='Nova observação')
    p_atualizar.add_argument('--desc', metavar='TEXTO', help='Nova descrição')
    p_atualizar.add_argument('--valor', type=float, metavar='VALOR', help='Novo valor previsto')
    p_atualizar.add_argument('--categoria', metavar='CAT', help='Nova categoria')
    p_atualizar.add_argument('--conta', metavar='CONTA', help='Nova conta/banco')
    p_atualizar.add_argument('--fornecedor', metavar='NOME', help='Novo fornecedor')

    # listar
    p_list = sub.add_parser('listar', help='Listar transações do mês',
                            description=cmd_listar.__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    p_list.add_argument('--mes', metavar='YYYY-MM', help='Mês a consultar (padrão: mês atual)')
    p_list.add_argument('--tipo', choices=['receita', 'despesa'], help='Filtrar por tipo')
    p_list.add_argument('--status', choices=['planejada', 'executada', 'cancelada'], help='Filtrar por status')
    p_list.add_argument('--conta', metavar='CONTA', help='Filtrar por conta/banco')
    p_list.add_argument('--report', action='store_true', help='Saida formatada pronta para exibicao')

    # resumo
    p_atr = sub.add_parser('atrasados', help='Listar transacoes vencidas com prazo maximo de pagamento')
    p_atr.add_argument('--report', action='store_true', help='Saida formatada pronta para exibicao')
    p_res = sub.add_parser('resumo', help='Resumo mensal: planejado vs executado',
                           description=cmd_resumo.__doc__,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
    p_res.add_argument('--mes', metavar='YYYY-MM', help='Mês a consultar (padrão: mês atual)')
    p_res.add_argument('--conta', metavar='CONTA', help='Filtrar por conta/banco')
    p_res.add_argument('--report', action='store_true', help='Saida formatada pronta para exibicao')

    # saldo
    p_saldo = sub.add_parser('saldo', help='Saldo do mês (executado)',
                             description=cmd_saldo.__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
    p_saldo.add_argument('--mes', metavar='YYYY-MM', help='Mês a consultar (padrão: mês atual)')
    p_saldo.add_argument('--report', action='store_true', help='Saida formatada pronta para exibicao')

    # categorias
    # dre detalhado
    p_dre = sub.add_parser('dre', help='DRE detalhado por categoria e conta',
                           description=cmd_dre.__doc__,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
    p_dre.add_argument('--mes', metavar='YYYY-MM', help='Mês a consultar (padrão: mês atual)')
    p_dre.add_argument('--conta', metavar='CONTA', help='Filtrar por conta/banco')
    p_dre.add_argument('--report', action='store_true', help='Saida formatada pronta para exibicao')

    sub.add_parser('categorias', help='Listar categorias disponíveis e caminho do arquivo',
                   description=cmd_categorias.__doc__,
                   formatter_class=argparse.RawDescriptionHelpFormatter)

    # recorrencia
    p_rec = sub.add_parser('recorrencia', help='Gerenciar recorrências (salário, aluguel, assinaturas...)')
    rec_sub = p_rec.add_subparsers(dest='rec_cmd', required=True)

    p_rec_add = rec_sub.add_parser('add', help='Cadastrar nova recorrência',
                                   description=cmd_rec_add.__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    p_rec_add.add_argument('tipo', choices=['receita', 'despesa'])
    p_rec_add.add_argument('--desc', required=True, metavar='TEXTO', help='Descrição')
    p_rec_add.add_argument('--valor', type=float, required=True, metavar='VALOR')
    p_rec_add.add_argument('--frequencia', default='mensal', metavar='FREQ',
                           choices=['diaria', 'semanal', 'mensal', 'anual'],
                           help='Frequência (padrão: mensal)')
    p_rec_add.add_argument('--dia', type=int, metavar='N',
                           help='Dia de referência no mês (ex: 5 → todo dia 5)')
    p_rec_add.add_argument('--categoria', required=True, metavar='CAT', help='Categoria (use `categorias` para ver a lista)')
    p_rec_add.add_argument('--conta', metavar='CONTA', help='Conta/banco/instituição (ex: Nubank, Itaú, C6)')
    p_rec_add.add_argument('--fornecedor', metavar='NOME', help='Fornecedor/contraparte da recorrência')
    p_rec_add.add_argument('--atraso-maximo', type=int, metavar='DIAS', help='Prazo maximo em dias para atraso')

    p_rec_list = rec_sub.add_parser('listar', help='Listar recorrências cadastradas')
    p_rec_list.add_argument('--report', action='store_true', help='Saida formatada pronta para exibicao')

    p_rec_ger = rec_sub.add_parser('gerar', help='Gerar transações planejadas do mês',
                                   description=cmd_rec_gerar.__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    p_rec_ger.add_argument('--mes', metavar='YYYY-MM', help='Mês alvo (padrão: mês atual)')

    p_rec_des = rec_sub.add_parser('desativar', help='Desativar recorrência')
    p_rec_des.add_argument('id', type=int, metavar='ID')

    p_rec_atualizar = rec_sub.add_parser('atualizar', help='Atualizar recorrência e propagar para transações futuras',
                                          description=cmd_rec_atualizar.__doc__,
                                          formatter_class=argparse.RawDescriptionHelpFormatter)
    p_rec_atualizar.add_argument('id', type=int, metavar='ID')
    p_rec_atualizar.add_argument('--desc', metavar='TEXTO', help='Nova descrição')
    p_rec_atualizar.add_argument('--valor', type=float, metavar='VALOR', help='Novo valor')
    p_rec_atualizar.add_argument('--dia', type=int, metavar='N', help='Novo dia de referência')
    p_rec_atualizar.add_argument('--categoria', metavar='CAT', help='Nova categoria')
    p_rec_atualizar.add_argument('--conta', metavar='CONTA', help='Nova conta/banco')
    p_rec_atualizar.add_argument('--fornecedor', metavar='NOME', help='Novo fornecedor')
    p_rec_atualizar.add_argument('--atraso-maximo', type=int, metavar='DIAS', help='Novo prazo maximo em dias para atraso')

    args = parser.parse_args()
    conn = get_conn()
    init_db(conn)
    migrate_db(conn)

    try:
        dispatch = {
            'add': cmd_add,
            'executar': cmd_executar,
            'cancelar': cmd_cancelar,
            'atualizar': cmd_atualizar,
            'listar': cmd_listar,
            'resumo': cmd_resumo,
            'saldo': cmd_saldo,
            'atrasados': cmd_atrasados,
            'dre': cmd_dre,
            'categorias': cmd_categorias,
        }
        if args.cmd in dispatch:
            dispatch[args.cmd](conn, args)
        elif args.cmd == 'recorrencia':
            rec_dispatch = {
                'add': cmd_rec_add,
                'listar': cmd_rec_listar,
                'gerar': cmd_rec_gerar,
                'desativar': cmd_rec_desativar,
                'atualizar': cmd_rec_atualizar,
            }
            rec_dispatch[args.rec_cmd](conn, args)
    except ValueError as e:
        print(f"❌ Erro: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
