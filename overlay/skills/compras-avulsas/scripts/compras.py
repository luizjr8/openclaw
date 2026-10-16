#!/usr/bin/env python3
"""CLI de curadoria de compras avulsas.

Opera sobre o store unificado em SQLite (`compras.db`), o mesmo do compras-monitor.
Itens desta skill vivem com `source='avulso'` e `tag='manual'`.

A busca de preço em si é feita pelo agente (web_search/web_fetch/browser) —
este script só monta o plano de busca (`check`) e grava o resultado
(`check --resultados`), mantendo o banco consistente.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date

DB_PATH = os.environ.get("COMPRAS_DB", "/app/workspace/skills/compras-monitor/compras.db")

SOURCE = "avulso"
TAG = "manual"

SITES_PADRAO = [
    "Amazon",
    "Mercado Livre",
    "Google Shopping",
    "Kabum",
    "Magalu",
    "Estante Virtual",
]


## Persistência


def conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    if not cols:
        raise ValueError(f"tabela 'items' não existe em {DB_PATH}; rode sync.py primeiro")
    if "categoria" not in cols:
        raise ValueError(f"schema de {DB_PATH} desatualizado; rode sync.py para migrar")
    return conn


def parse_price(text):
    """Parse de preço brasileiro. '.' = separador de milhar, ',' = decimal. (espelha sync.py)"""
    if not text:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    text = text.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()
    m = re.search(r"(\d[\d.]*),(\d{2})", text)
    if m:
        return float(m.group(1).replace(".", "") + "." + m.group(2))
    m = re.search(r"(\d{1,3}(?:\.\d{3})+)", text)
    if m:
        return float(m.group(1).replace(".", ""))
    m = re.search(r"(\d+)", text)
    if m:
        return float(m.group(1))
    return None


def fmt_valor(preco):
    """1234.5 -> 'R$ 1.234,50'"""
    return "R$ " + f"{preco:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def carregar_meta(row):
    try:
        return json.loads(row["meta"]) if row["meta"] else {}
    except (TypeError, ValueError):
        return {}


def gravar_meta(conn, item_id, meta):
    conn.execute("UPDATE items SET meta=? WHERE id=?",
                 (json.dumps(meta, ensure_ascii=False) if meta else None, item_id))


def achar(conn, chave):
    """Localiza item por id numérico ou por substring do título — precisa ser único."""
    if isinstance(chave, int) or (isinstance(chave, str) and str(chave).lstrip("-").isdigit()):
        row = conn.execute("SELECT * FROM items WHERE id=?", (int(chave),)).fetchone()
        if not row:
            raise KeyError(f"item {chave} não encontrado")
        return row

    alvo = f"%{chave}%"
    rows = conn.execute("SELECT * FROM items WHERE title LIKE ?", (alvo,)).fetchall()
    if not rows:
        raise KeyError(f"nenhum item casa com {chave!r}")
    if len(rows) > 1:
        raise KeyError(f"{chave!r} é ambíguo: {[r['title'] for r in rows]}")
    return rows[0]


## Helpers de leitura de item


def valor_texto(row, meta):
    """Texto livre original do preço; cai pro preço numérico quando não existe."""
    if meta.get("valor_texto"):
        return meta["valor_texto"]
    return fmt_valor(row["current_price"]) if row["current_price"] is not None else None


def preco_alvo(row, meta):
    """Preço-alvo — aceita a coluna nova e o legado (meta.preco_alvo)."""
    if row["preco_max"] is not None:
        return row["preco_max"]
    return meta.get("preco_alvo")


def faixa_preco_alvo(row):
    faixa = {}
    if row["preco_min"] is not None:
        faixa["min"] = row["preco_min"]
    if row["preco_max"] is not None:
        faixa["max"] = row["preco_max"]
    return faixa or None


def carregar_lista(valor):
    try:
        return json.loads(valor) if valor else None
    except (TypeError, ValueError):
        return None


## Comandos


def cmd_add(args):
    conn = conectar()
    if conn.execute("SELECT id FROM items WHERE title=? AND source=? AND tag=?",
                    (args.produto, SOURCE, TAG)).fetchone():
        print(json.dumps({"erro": f"já existe item com produto {args.produto!r}"}, ensure_ascii=False))
        return 1

    hoje = args.data or date.today().isoformat()
    preco = parse_price(args.valor)
    # valor é texto livre: o original sempre fica preservado no meta
    meta = {"valor_texto": args.valor} if args.valor else {}

    palavras = args.palavra_chave or ([args.produto] if args.monitorar else None)
    sites = args.site or (list(SITES_PADRAO) if args.monitorar else None)

    cur = conn.execute(
        """INSERT INTO items(title, source, tag, url, current_price, first_seen, last_seen,
               active, categoria, nota_necessidade, tipo, monitorar, palavras_chave, sites,
               preco_min, preco_max, alerta_abaixo_de, preco_baseline, analise, observacao,
               comprado, descartado, meta, data_insercao)
           VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?)""",
        (args.produto, SOURCE, TAG, args.url or None, preco, hoje, hoje,
         args.categoria, args.nota, args.tipo, 1 if args.monitorar else 0,
         json.dumps(palavras, ensure_ascii=False) if palavras else None,
         json.dumps(sites, ensure_ascii=False) if sites else None,
         args.preco_min, args.preco_max, args.alerta_abaixo_de, args.preco_baseline,
         args.analise or None, args.observacao,
         json.dumps(meta, ensure_ascii=False) if meta else None, hoje),
    )
    item_id = cur.lastrowid
    if preco is not None:
        conn.execute("INSERT INTO price_history(item_id, price, recorded_at) VALUES(?,?,?)",
                     (item_id, preco, hoje))
    conn.commit()

    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    print(json.dumps({"ok": True, "id": item_id, "item": item_resumo(row)},
                     ensure_ascii=False, indent=2))
    return 0


def plano_de_busca(conn):
    rows = conn.execute(
        """SELECT * FROM items
           WHERE monitorar=1 AND active=1 AND comprado=0 AND descartado=0
           ORDER BY id"""
    ).fetchall()
    plano = []
    for row in rows:
        meta = carregar_meta(row)
        plano.append({
            "id": row["id"],
            "produto": row["title"],
            "categoria": row["categoria"],
            "palavras_chave": carregar_lista(row["palavras_chave"]) or [row["title"]],
            "sites": carregar_lista(row["sites"]) or list(SITES_PADRAO),
            "faixa_preco_alvo": faixa_preco_alvo(row),
            "preco_alvo": preco_alvo(row, meta),
            "alerta_imediato_abaixo_de": row["alerta_abaixo_de"],
            "preco_baseline": row["preco_baseline"],
            "ultima_checagem": meta.get("ultima_checagem"),
            "url": row["url"],
        })
    return plano


def cmd_check(args):
    conn = conectar()
    if not args.resultados:
        print(json.dumps({"itens": plano_de_busca(conn)}, ensure_ascii=False, indent=2))
        return 0

    ## Gravando resultados da busca
    bruto = sys.stdin.read() if args.resultados == "-" else open(args.resultados, encoding="utf-8").read()
    resultados = json.loads(bruto)
    if isinstance(resultados, dict):
        resultados = resultados.get("resultados", [])

    hoje = date.today().isoformat()
    alertas, atualizados = [], []

    for r in resultados:
        # produto tem precedência: o id é estável, mas o agente costuma mandar o nome
        chave = r.get("produto") or r.get("id")
        if chave is None:
            print(json.dumps({"erro": "resultado sem 'id' nem 'produto'", "resultado": r}, ensure_ascii=False))
            return 1
        row = achar(conn, chave)
        meta = carregar_meta(row)

        checagem = {"data": hoje, "preco": r.get("preco"), "loja": r.get("loja"), "url": r.get("url")}
        meta["ultima_checagem"] = {k: v for k, v in checagem.items() if v is not None}

        preco = r.get("preco")
        if r.get("valor"):
            meta["valor_texto"] = r["valor"]
        elif preco is not None:
            meta["valor_texto"] = fmt_valor(preco)

        if preco is not None:
            conn.execute("UPDATE items SET current_price=?, last_seen=? WHERE id=?",
                         (preco, hoje, row["id"]))
            conn.execute("INSERT INTO price_history(item_id, price, recorded_at) VALUES(?,?,?)",
                         (row["id"], preco, hoje))
        if r.get("analise"):
            conn.execute("UPDATE items SET analise=? WHERE id=?", (r["analise"], row["id"]))
        gravar_meta(conn, row["id"], meta)

        atualizados.append({"id": row["id"], "produto": row["title"],
                            "preco": preco, "loja": r.get("loja")})

        # alerta imediato só quando o item define o gatilho e o preço fica abaixo dele
        gatilho = row["alerta_abaixo_de"]
        if gatilho is not None and preco is not None and preco < gatilho:
            alertas.append({
                "id": row["id"],
                "produto": row["title"],
                "preco": preco,
                "gatilho": gatilho,
                "loja": r.get("loja"),
                "url": r.get("url"),
            })

    conn.commit()
    print(json.dumps({"ok": True, "atualizados": atualizados, "alertas": alertas},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_comprar(args):
    conn = conectar()
    row = achar(conn, args.produto)
    meta = carregar_meta(row)

    if args.valor:
        meta["valor_texto"] = args.valor
    conn.execute(
        """UPDATE items SET comprado=1, active=0, monitorar=0, data_compra=?,
               pedido=COALESCE(?, pedido), pagamento=COALESCE(?, pagamento),
               current_price=COALESCE(?, current_price), observacao=COALESCE(?, observacao)
           WHERE id=?""",
        (args.data or date.today().isoformat(), args.pedido, args.pagamento,
         parse_price(args.valor) if args.valor else None, args.observacao, row["id"]),
    )
    gravar_meta(conn, row["id"], meta)
    conn.commit()

    row = conn.execute("SELECT * FROM items WHERE id=?", (row["id"],)).fetchone()
    print(json.dumps({"ok": True, "id": row["id"], "item": item_resumo(row)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_descartar(args):
    conn = conectar()
    row = achar(conn, args.produto)
    conn.execute(
        "UPDATE items SET descartado=1, active=0, monitorar=0, observacao=COALESCE(?, observacao) WHERE id=?",
        (args.observacao, row["id"]),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM items WHERE id=?", (row["id"],)).fetchone()
    print(json.dumps({"ok": True, "id": row["id"], "item": item_resumo(row)},
                     ensure_ascii=False, indent=2))
    return 0


def item_resumo(row):
    meta = carregar_meta(row)
    return {
        "id": row["id"],
        "produto": row["title"],
        "categoria": row["categoria"],
        "valor": valor_texto(row, meta),
        "nota_necessidade": row["nota_necessidade"],
        "tipo": row["tipo"],
        "url": row["url"],
        "analise": row["analise"],
        "avaliacao": meta.get("avaliacao"),
        "raridade": meta.get("raridade"),
        "monitorar": bool(row["monitorar"]),
        "preco_alvo": preco_alvo(row, meta),
        "preco_baseline": row["preco_baseline"],
        "ultima_checagem": meta.get("ultima_checagem"),
    }


def cmd_resumo(args):
    conn = conectar()
    query = "SELECT * FROM items WHERE active=1 AND comprado=0 AND descartado=0 AND source=?"
    params = [SOURCE]
    if args.monitorados:
        query += " AND monitorar=1"
    rows = conn.execute(query, params).fetchall()
    itens = [item_resumo(r) for r in rows]

    # nota de necessidade primeiro, depois categoria — o agente varia categoria na hora de montar o texto
    itens.sort(key=lambda x: (-(x["nota_necessidade"] or 0), x["categoria"] or ""))

    categorias = sorted({it["categoria"] for it in itens if it["categoria"]})
    comprados = conn.execute("SELECT COUNT(*) FROM items WHERE comprado=1 AND source=?", (SOURCE,)).fetchone()[0]
    print(json.dumps(
        {"total_pendentes": len(itens), "total_comprados": comprados,
         "categorias": categorias, "itens": itens},
        ensure_ascii=False, indent=2,
    ))
    return 0


def main():
    p = argparse.ArgumentParser(description="Curadoria de compras avulsas")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="adiciona item ao registro")
    a.add_argument("--produto", required=True)
    a.add_argument("--categoria", required=True)
    a.add_argument("--valor", required=True, help="preço como texto livre, ex: 'R$ 1.220,00 (tabela)'")
    a.add_argument("--tipo", default="fisico", choices=["fisico", "digital"])
    a.add_argument("--nota", type=int, required=True, choices=[1, 2, 3, 4, 5], help="nota_necessidade")
    a.add_argument("--url", default="")
    a.add_argument("--analise", default="")
    a.add_argument("--data", help="data_insercao (default: hoje)")
    a.add_argument("--monitorar", action="store_true", help="entra no check diário")
    a.add_argument("--palavra-chave", action="append", help="repetível; termos de busca livres")
    a.add_argument("--site", action="append", help=f"repetível; default: {', '.join(SITES_PADRAO)}")
    a.add_argument("--preco-min", type=float)
    a.add_argument("--preco-max", type=float)
    a.add_argument("--alerta-abaixo-de", type=float, help="preço que quebra o silêncio do check diário")
    a.add_argument("--preco-baseline", type=float)
    a.add_argument("--observacao")
    a.set_defaults(func=cmd_add)

    c = sub.add_parser("check", help="plano de busca dos itens monitorados / grava resultados")
    c.add_argument("--resultados", help="arquivo JSON com os achados, ou '-' para stdin")
    c.set_defaults(func=cmd_check)

    m = sub.add_parser("comprar", help="marca item como comprado e para o monitoramento")
    m.add_argument("--produto", required=True, help="substring do nome do produto, precisa ser único")
    m.add_argument("--data", help="data_compra (default: hoje)")
    m.add_argument("--pedido", help="número do pedido")
    m.add_argument("--pagamento", help="forma de pagamento")
    m.add_argument("--valor", help="preço final pago, se diferente do último registrado")
    m.add_argument("--observacao")
    m.set_defaults(func=cmd_comprar)

    d = sub.add_parser("descartar", help="marca item como descartado e para o monitoramento")
    d.add_argument("--produto", required=True, help="substring do nome do produto, precisa ser único")
    d.add_argument("--observacao")
    d.set_defaults(func=cmd_descartar)

    r = sub.add_parser("resumo", help="itens pendentes para a curadoria consolidada")
    r.add_argument("--monitorados", action="store_true", help="só os itens com monitorar=true")
    r.set_defaults(func=cmd_resumo)

    args = p.parse_args()
    try:
        return args.func(args)
    except (KeyError, ValueError) as e:
        # str(KeyError) devolve o argumento com repr; pegar o arg cru evita aspas duplicadas
        print(json.dumps({"erro": e.args[0] if e.args else str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
