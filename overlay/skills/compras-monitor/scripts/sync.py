#!/usr/bin/env python3
"""compras-monitor: sync e monitoramento de preços ML + Amazon BR."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

from playwright.sync_api import sync_playwright

DB_PATH = "/app/workspace/skills/compras-monitor/compras.db"
BROWSER_DATA = "/app/browser/openclaw/user-data/"
SCREENSHOTS_DIR = "/app/workspace/skills/compras-monitor/data/screenshots"
TZ_BR = timezone(timedelta(hours=-3))

SOURCES = {
    "ml_carrinho": {
        "url": "https://www.mercadolivre.com.br/gz/cart/v2",
        "source": "ml",
        "tag": "carrinho",
    },
    "ml_favoritos": {
        "url": "https://myaccount.mercadolivre.com.br/bookmarks/list",
        "source": "ml",
        "tag": "favoritos",
    },
    "amazon_carrinho": {
        "url": "https://www.amazon.com.br/gp/cart/view.html",
        "source": "amazon",
        "tag": "carrinho",
    },
    "amazon_wishlist": {
        "url": "https://www.amazon.com.br/hz/wishlist/ls/default",
        "source": "amazon",
        "tag": "wishlist",
    },
}


## Store unificado: colunas vindas da antiga skill compras-avulsas (compras.json)
EXTRA_COLUMNS = [
    ("categoria", "TEXT"),
    ("nota_necessidade", "INTEGER"),
    ("tipo", "TEXT"),
    ("monitorar", "INTEGER DEFAULT 0"),
    ("palavras_chave", "TEXT"),   # JSON array
    ("sites", "TEXT"),            # JSON array
    ("preco_min", "REAL"),
    ("preco_max", "REAL"),
    ("alerta_abaixo_de", "REAL"),
    ("preco_baseline", "REAL"),
    ("analise", "TEXT"),
    ("observacao", "TEXT"),
    ("comprado", "INTEGER DEFAULT 0"),
    ("descartado", "INTEGER DEFAULT 0"),
    ("data_compra", "TEXT"),
    ("pedido", "TEXT"),
    ("pagamento", "TEXT"),
    ("meta", "TEXT"),             # JSON blob livre
    ("data_insercao", "TEXT"),
]


def migrate_schema(conn):
    """SQLite não tem ADD COLUMN IF NOT EXISTS — tenta uma a uma e ignora as já existentes."""
    for name, decl in EXTRA_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {decl}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # A migration is never destructive: snapshot the original DB once before the
    # normalized schema/triggers are created.
    needs_normalization = os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            source TEXT NOT NULL,       -- ml | amazon
            tag TEXT NOT NULL,          -- carrinho | favoritos | wishlist | salvo
            url TEXT,
            current_price REAL,
            original_price REAL,
            promo_tag TEXT,             -- OFF, promo badge, etc
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            UNIQUE(title, source, tag)
        );
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(id),
            price REAL NOT NULL,
            original_price REAL,
            promo_tag TEXT,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_items_source_tag ON items(source, tag);
        CREATE INDEX IF NOT EXISTS idx_ph_item ON price_history(item_id);
    """)
    conn.commit()
    migrate_schema(conn)
    normalize_schema(conn, needs_normalization)
    deactivate_legacy_ml_phantoms(conn)
    return conn


def normalize_schema(conn, needs_backup):
    """Add the 1:N store and mirror legacy writers while clients are upgraded.

    `items` remains readable/writable for CLI compatibility; triggers keep it as a
    compatibility projection. The authoritative price/list data is in
    `observacoes_loja`.
    """
    has_products = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='produtos'").fetchone()
    if not has_products and needs_backup:
        # SQLite backup captures WAL state too; copy2(db) alone can miss live rows.
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        dst = sqlite3.connect(f'{DB_PATH}.pre-normalizacao-{stamp}.bak')
        conn.backup(dst)
        dst.close()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS produtos (
      id INTEGER PRIMARY KEY, nome TEXT NOT NULL, nome_chave TEXT NOT NULL UNIQUE,
      marca TEXT, modelo TEXT, categoria TEXT, descricao TEXT, estado TEXT,
      meta TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS observacoes_loja (
      id INTEGER PRIMARY KEY, produto_id INTEGER NOT NULL REFERENCES produtos(id),
      loja TEXT NOT NULL, source TEXT NOT NULL, lista TEXT NOT NULL, tag TEXT,
      preco_atual REAL, preco_anterior REAL, preco_original REAL, percentual_desconto REAL,
      url TEXT, screenshot_path TEXT, promocao TEXT, descricao TEXT, metadados TEXT,
      first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, active INTEGER DEFAULT 1,
      UNIQUE(produto_id, source, lista)
    );
    CREATE TABLE IF NOT EXISTS historico_observacoes (
      id INTEGER PRIMARY KEY, observacao_id INTEGER NOT NULL REFERENCES observacoes_loja(id),
      preco REAL NOT NULL, preco_original REAL, promocao TEXT, registrado_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_observacoes_lista ON observacoes_loja(source, lista, active);
    CREATE INDEX IF NOT EXISTS idx_historico_observacao ON historico_observacoes(observacao_id, registrado_at);
    """)
    # Initial copy is idempotent; title key collapses the same product saved in
    # several lists while retaining one observation per source/list.
    rows = conn.execute("SELECT * FROM items").fetchall()
    for row in rows:
        d = dict(row); title = clean_title(d['title']); k = re.sub(r'[^a-z0-9]+', '', title.lower())
        if not title: continue
        # Procurar por `nome` antes da chave, igual ao COALESCE dos gatilhos. As
        # duas chaves divergem em título com acento ou pontuação (o gatilho só
        # tira espaço e hífen), e buscar só por chave duplicava o produto.
        prod = (conn.execute("SELECT id FROM produtos WHERE nome=?", (title,)).fetchone()
                or conn.execute("SELECT id FROM produtos WHERE nome_chave=?", (k,)).fetchone())
        if not prod:
            cur = conn.execute("INSERT INTO produtos(nome,nome_chave,categoria,meta,created_at,updated_at) VALUES(?,?,?,?,?,?)", (title,k,d.get('categoria'),d.get('meta'),d.get('first_seen') or now_br(),d.get('last_seen') or now_br())); pid=cur.lastrowid
        else: pid=prod[0]
        conn.execute("""INSERT OR IGNORE INTO observacoes_loja(produto_id,loja,source,lista,tag,preco_atual,preco_original,url,promocao,first_seen,last_seen,active)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (pid,d['source'],d['source'],d['tag'],d['tag'],d['current_price'],d['original_price'],d['url'],d['promo_tag'],d['first_seen'],d['last_seen'],d['active']))
    # Existing callers still write items. These triggers make compatibility safe
    # until every consumer has moved, without losing any observation.
    # Recreate these triggers so installations normalized by earlier versions get
    # the same identity rule. Existing product names win over the legacy key:
    # historical keys removed more punctuation than the old trigger did.
    conn.executescript("""
    DROP TRIGGER IF EXISTS items_ai_normalize;
    DROP TRIGGER IF EXISTS items_au_normalize;
    CREATE TRIGGER items_ai_normalize AFTER INSERT ON items BEGIN
      INSERT OR IGNORE INTO produtos(nome,nome_chave,categoria,meta,created_at,updated_at)
      SELECT NEW.title,lower(replace(replace(NEW.title,' ',''),'-','')),NEW.categoria,NEW.meta,NEW.first_seen,NEW.last_seen
       WHERE NOT EXISTS (SELECT 1 FROM produtos WHERE nome=NEW.title);
      INSERT OR REPLACE INTO observacoes_loja(produto_id,loja,source,lista,tag,preco_atual,preco_original,url,promocao,first_seen,last_seen,active)
      VALUES(COALESCE((SELECT id FROM produtos WHERE nome=NEW.title LIMIT 1),
                      (SELECT id FROM produtos WHERE nome_chave=lower(replace(replace(NEW.title,' ',''),'-','')) LIMIT 1)),
             NEW.source,NEW.source,NEW.tag,NEW.tag,NEW.current_price,NEW.original_price,NEW.url,NEW.promo_tag,NEW.first_seen,NEW.last_seen,NEW.active);
    END;
    CREATE TRIGGER items_au_normalize AFTER UPDATE OF current_price,original_price,promo_tag,url,last_seen,active ON items BEGIN
      UPDATE observacoes_loja SET preco_anterior=preco_atual,preco_atual=NEW.current_price,preco_original=NEW.original_price,url=NEW.url,promocao=NEW.promo_tag,last_seen=NEW.last_seen,active=NEW.active
       WHERE produto_id=COALESCE((SELECT id FROM produtos WHERE nome=NEW.title LIMIT 1),
                                 (SELECT id FROM produtos WHERE nome_chave=lower(replace(replace(NEW.title,' ',''),'-','')) LIMIT 1))
         AND source=NEW.source AND lista=NEW.tag;
    END;
    """)
    conn.commit()


def now_br():
    return datetime.now(TZ_BR).isoformat(timespec="seconds")


def clean_title(t):
    if not t:
        return t
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*Abre em uma nova aba\s*", "", t)
    t = re.sub(r"^(.{15,}?)\1+", r"\1", t)  # dedupe repeated title
    return t[:200].strip()


# Navigation, category and seller-group labels emitted inside ML cart cards are
# never products, even when their URL contains an item_id query parameter.
ML_UI_TITLE_RE = re.compile(
    r"^(?:resumo da compra|celulares e telefones|produtos\s*de|produtos\s+full)\b",
    re.IGNORECASE,
)


def is_ml_product_url(url):
    """Accept only ML item/product paths, never category/seller/cart URLs."""
    if not isinstance(url, str):
        return False
    match = re.match(r"https?://(?:www\.)?mercadolivre\.com\.br(?P<path>/[^?#]*)", url, re.I)
    if not match:
        return False
    return bool(re.search(r"/p/MLB\d+(?:/|$)|/MLB[A-Z]?-?\d{6,}(?:[-/]|$)", match.group("path"), re.I))


def is_valid_ml_item(item):
    """Validate extraction before it can create or reactivate an observation."""
    title = clean_title(item.get("title"))
    if not title or ML_UI_TITLE_RE.match(title) or not is_ml_product_url(item.get("url")):
        return False
    price = item.get("price")
    if isinstance(price, (int, float)) and price > 0:
        return True
    # A real cart row can defer price rendering. Its concrete MLB card identity,
    # combined with a product URL, is sufficient to retain it as "sem preço".
    return bool(re.fullmatch(r"MLB-?\d+", str(item.get("card_item_id") or ""), re.I))


def deactivate_legacy_ml_phantoms(conn):
    """Non-destructively deactivate UI rows written by the old broad extractor."""
    where = """source='ml' AND active=1 AND
        (title IN ('Resumo da compra', 'Celulares e Telefones')
         OR lower(title) GLOB 'produtos de*')"""
    rows = conn.execute(f"SELECT id FROM items WHERE {where}").fetchall()
    if not rows:
        return 0
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = sqlite3.connect(f'{DB_PATH}.pre-limpeza-phantoms-{stamp}.bak')
    conn.backup(backup)
    backup.close()
    # Keeps legacy history/audit data. Trigger mirrors compatible rows.
    conn.execute(f"UPDATE items SET active=0 WHERE {where}")
    # Cover canonical observations that predate triggers or had divergent identity.
    conn.execute("""
        UPDATE observacoes_loja SET active=0
         WHERE source='ml' AND active=1 AND produto_id IN (
           SELECT id FROM produtos
            WHERE nome IN ('Resumo da compra', 'Celulares e Telefones')
               OR lower(nome) GLOB 'produtos de*'
         )
    """)
    conn.commit()
    return len(rows)


def upsert_item(conn, title, source, tag, url, price, original_price=None, promo_tag=None, *, commit=True):
    title = clean_title(title)
    ts = now_br()
    cur = conn.execute(
        "SELECT id, current_price FROM items WHERE title=? AND source=? AND tag=?",
        (title, source, tag),
    )
    row = cur.fetchone()
    if row:
        item_id, old_price = row
        conn.execute(
            """UPDATE items SET current_price=?, original_price=?, promo_tag=?,
               last_seen=?, url=COALESCE(?, url), active=1 WHERE id=?""",
            (price, original_price, promo_tag, ts, url, item_id),
        )
        if price and (old_price is None or abs(price - old_price) > 0.01):
            conn.execute(
                "INSERT INTO price_history(item_id, price, original_price, promo_tag, recorded_at) VALUES(?,?,?,?,?)",
                (item_id, price, original_price, promo_tag, ts),
            )
    else:
        cur2 = conn.execute(
            """INSERT INTO items(title, source, tag, url, current_price, original_price,
               promo_tag, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?)""",
            (title, source, tag, url, price, original_price, promo_tag, ts, ts),
        )
        item_id = cur2.lastrowid
        if price:
            conn.execute(
                "INSERT INTO price_history(item_id, price, original_price, promo_tag, recorded_at) VALUES(?,?,?,?,?)",
                (item_id, price, original_price, promo_tag, ts),
            )
    if commit:
        conn.commit()
    return item_id


def screenshot_filename(source, tag, title, url=None):
    """Stable, filesystem-safe filename for one store observation."""
    slug = re.sub(r"[^a-z0-9]+", "-", clean_title(title).lower()).strip("-")[:80] or "produto"
    identity = "\0".join((source, tag, clean_title(title), url or ""))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{source}--{tag}--{slug}--{digest}.png"


def persist_observation_screenshot(conn, item_id, screenshot_path, *, commit=True):
    """Write the card screenshot directly to the normalized observation."""
    row = conn.execute("SELECT title, source, tag FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return False
    title, source, tag = row
    cur = conn.execute("""
        UPDATE observacoes_loja
           SET screenshot_path=?
         WHERE source=? AND lista=?
           AND produto_id=(SELECT id FROM produtos WHERE nome=? LIMIT 1)
    """, (screenshot_path, source, tag, title))
    if commit:
        conn.commit()
    return cur.rowcount == 1


def capture_item_card(page, source, tag, item):
    """Capture only the extracted product card/row in the current CDP page."""
    title = clean_title(item.get("title"))
    if not title:
        return None
    token = hashlib.sha256((source + "\0" + tag + "\0" + title + "\0" + (item.get("url") or "")).encode()).hexdigest()[:20]
    marked = page.evaluate(r"""({source, title, url, token}) => {
      const selectors = source === 'ml'
        ? ['[data-item-id]', '[data-testid*=item]', 'li[class*=item]', 'article', '[class*=card]']
        : ['[data-asin]', 'li[data-itemid]', '[id^=itemMain_]', '[id^=item_]'];
      const wantedTitle = title.replace(/\s+/g, ' ').trim().toLowerCase();
      const wantedUrl = (url || '').split('?')[0];
      for (const el of document.querySelectorAll(selectors.join(','))) {
        const links = [...el.querySelectorAll('a[href]')];
        const hasUrl = wantedUrl && links.some(a => a.href.split('?')[0] === wantedUrl);
        const text = (el.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase();
        if (hasUrl || text.includes(wantedTitle)) {
          el.setAttribute('data-compras-screenshot-id', token);
          return true;
        }
      }
      return false;
    }""", {"source": source, "title": title, "url": item.get("url") or "", "token": token})
    if not marked:
        return None
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOTS_DIR, screenshot_filename(source, tag, title, item.get("url")))
    try:
        page.locator(f'[data-compras-screenshot-id="{token}"]').first.screenshot(path=path)
        return path
    finally:
        page.evaluate("token => document.querySelector(`[data-compras-screenshot-id=\"${token}\"]`)?.removeAttribute('data-compras-screenshot-id')", token)


def mark_inactive(conn, source, tag, active_ids, *, commit=True):
    if not active_ids:
        return
    placeholders = ",".join("?" * len(active_ids))
    conn.execute(
        f"UPDATE items SET active=0 WHERE source=? AND tag=? AND id NOT IN ({placeholders})",
        [source, tag] + active_ids,
    )
    if commit:
        conn.commit()


def parse_price(text):
    """Parse Brazilian price string. '.' = thousands separator, ',' = decimal."""
    if not text:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    # Mercado Livre exposes the reliable accessible value as e.g.
    # "348 reais com 29 centavos"; its rendered spans may concatenate price
    # and discount text, so parse this representation before visual text.
    accessible = re.search(r"(\d[\d.]*)\s+reais?(?:\s+com\s+(\d{1,2})\s+centavos?)?", text, re.I)
    if accessible:
        reais = accessible.group(1).replace(".", "")
        cents = accessible.group(2) or "0"
        return float(f"{reais}.{int(cents):02d}")
    text = text.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()
    # "1.080,00" or "1.080" or "109,80"
    m = re.search(r"(\d[\d.]*),(\d{2})", text)
    if m:
        return float(m.group(1).replace(".", "") + "." + m.group(2))
    # "1.080" without decimal — dots are thousands separators
    m = re.search(r"(\d{1,3}(?:\.\d{3})+)", text)
    if m:
        return float(m.group(1).replace(".", ""))
    # Plain integer "153" or "43"
    m = re.search(r"(\d+)", text)
    if m:
        return float(m.group(1))
    return None


## Importação do registro legado (compras.json)

COMPRAS_JSON = "/app/workspace/memory/compras.json"

# faixa ("R$60-90"), estimativa ("~R$350") ou moeda estrangeira não viram preço numérico
VALOR_AMBIGUO_RE = re.compile(r"[-–—~]|US\$|U\$|£|€|/vol|a partir|estimativa", re.IGNORECASE)


def parse_valor_livre(texto):
    """`valor` do compras.json é texto livre; só vira preço quando é inequívoco.

    Devolve (preco, preco_original). Texto ambíguo devolve (None, ...) — o original
    fica preservado em meta.valor_texto, sem inventar número.
    """
    if not texto:
        return None, None
    original = None
    m = re.search(r"de\s*R\$\s*([\d.,]+)", texto, re.IGNORECASE)
    if m:
        original = parse_price(m.group(1))
    ocorrencias = re.findall(r"R\$\s*[\d.,]+", texto)
    if len(ocorrencias) == 1 and not VALOR_AMBIGUO_RE.search(texto):
        return parse_price(ocorrencias[0]), original
    # "R$63,99 (promoção, de R$210,00)": 2 valores, mas o segundo é o preço riscado
    if original is not None and len(ocorrencias) == 2:
        return parse_price(ocorrencias[0]), original
    return None, original


def do_migrate_json(conn, path=COMPRAS_JSON):
    with open(path, encoding="utf-8") as f:
        dados = json.load(f)

    inseridos, pulados = 0, 0
    for it in dados.get("itens", []):
        title = re.sub(r"\s+", " ", it.get("produto") or "").strip()
        if not title:
            continue
        if conn.execute(
            "SELECT id FROM items WHERE title=? AND source='avulso' AND tag='manual'", (title,)
        ).fetchone():
            pulados += 1
            continue

        preco, original = parse_valor_livre(it.get("valor"))
        faixa = it.get("faixa_preco_alvo") or {}
        comprado = 1 if it.get("comprado") else 0
        descartado = 1 if it.get("descartado") else 0

        # meta absorve tudo que não tem coluna própria — nada do JSON original se perde
        meta = dict(it.get("meta") or {})
        for k in ("avaliacao", "raridade", "valor_pago", "loja_monitorada",
                  "analise_frete", "ultima_checagem"):
            if it.get(k) is not None:
                meta[k] = it[k]
        if it.get("valor"):
            meta["valor_texto"] = it["valor"]

        data_insercao = it.get("data_insercao") or now_br()
        cur = conn.execute(
            """INSERT INTO items(title, source, tag, url, current_price, original_price,
                   first_seen, last_seen, active, categoria, nota_necessidade, tipo, monitorar,
                   palavras_chave, sites, preco_min, preco_max, alerta_abaixo_de, preco_baseline,
                   analise, observacao, comprado, descartado, data_compra, pedido, pagamento,
                   meta, data_insercao)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (title, "avulso", "manual", it.get("url") or None, preco, original,
             data_insercao, now_br(), 0 if (comprado or descartado) else 1,
             it.get("categoria"), it.get("nota_necessidade"), it.get("tipo"),
             1 if it.get("monitorar") else 0,
             json.dumps(it["palavras_chave"], ensure_ascii=False) if it.get("palavras_chave") else None,
             json.dumps(it["sites"], ensure_ascii=False) if it.get("sites") else None,
             faixa.get("min"), faixa.get("max"),
             it.get("alerta_imediato_abaixo_de"), it.get("preco_baseline"),
             it.get("analise"), it.get("observacao"), comprado, descartado,
             it.get("data_compra"), it.get("pedido"), it.get("pagamento"),
             json.dumps(meta, ensure_ascii=False) if meta else None, data_insercao),
        )
        item_id = cur.lastrowid

        checagem = it.get("ultima_checagem") or {}
        if checagem.get("preco") is not None:
            conn.execute(
                "INSERT INTO price_history(item_id, price, recorded_at) VALUES(?,?,?)",
                (item_id, float(checagem["preco"]), checagem.get("data") or data_insercao),
            )
        inseridos += 1

    conn.commit()
    print(json.dumps({"ok": True, "inseridos": inseridos, "ja_existentes": pulados},
                     ensure_ascii=False, indent=2))


CDP_URL = "http://127.0.0.1:16899"


def launch_browser():
    """Attach only to the managed browser; never launch or own a browser process."""
    pw = sync_playwright().start()
    try:
        return pw, pw.chromium.connect_over_cdp(CDP_URL), "cdp"
    except Exception:
        pw.stop()
        raise RuntimeError("CDP do browser OpenClaw indisponível; sync não será executado")


GOTO_TIMEOUT = 45000
NAV_PAUSE = 3
SCROLL_PAUSE = 2
BETWEEN_SOURCES = 5
WISHLIST_MAX_SCROLLS = 40


def get_page_text(page, url, scroll=False):
    page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT)
    time.sleep(NAV_PAUSE)
    if scroll:
        for _ in range(4):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(SCROLL_PAUSE)
    return page.evaluate("() => document.body.innerText")


def scrape_ml_js(page, url):
    """Extract ML items: titles+links via JS, prices from innerText blocks."""
    page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT)
    time.sleep(NAV_PAUSE)
    # Each result is extracted from its own card. Do not parse document.body.innerText:
    # navigation/UI labels otherwise become phantom products and prices get crossed.
    cards = page.evaluate(r'''() => {
      const out=[], seen=new Set();
      // This is JavaScript embedded in a Python raw string: regex slashes must
      // remain literal, not Python-escaped ("\\/" terminates the JS regex).
      const uiTitle=/^(resumo da compra|celulares e telefones|produtos\s*de|produtos\s+full)\b/i;
      const isProductUrl = href => {
        try {
          const u=new URL(href);
          return /(^|\.)mercadolivre\.com\.br$/i.test(u.hostname)
            && (/\/p\/MLB\d+(?:\/|$)/i.test(u.pathname) || /\/MLB[A-Z]?-?\d{6,}(?:[-\/]|$)/i.test(u.pathname));
        } catch (_) { return false; }
      };
      for (const card of document.querySelectorAll('[data-item-id], [data-testid*=item], li[class*=item], article, [class*=card]')) {
        const link=[...card.querySelectorAll('a[href]')].find(a => isProductUrl(a.href));
        const titleEl=card.querySelector('h2,h3,[class*=title],[class*=Title],a[title]');
        const title=(titleEl?.getAttribute('title')||titleEl?.textContent||'').replace(/\s+/g,' ').trim();
        if (!link || title.length < 5 || uiTitle.test(title) || seen.has(title)) continue;
        // Mercado Livre nests current and previous money amounts in a common
        // .cl-price wrapper. Reading that wrapper concatenates `999` + `39%`
        // into `99939`; select the semantic money elements instead.
        const currentEl = card.querySelector('.cl-price__current [data-andes-money-amount], .cl-price__current .andes-money-amount, [data-testid="price-part"] [data-andes-money-amount], [data-andes-money-amount][aria-label^="Agora:"]')
          || [...card.querySelectorAll('[data-andes-money-amount]')].find(el => !el.classList.contains('andes-money-amount--previous') && !el.classList.contains('polylabel-price'));
        const oldEl = card.querySelector('.cl-price__previous-wrapper [data-andes-money-amount], [data-andes-money-amount].andes-money-amount--previous, s[data-andes-money-amount], del[data-andes-money-amount]');
        const current = currentEl?.getAttribute('aria-label') || currentEl?.textContent.trim() || null;
        const old = oldEl?.getAttribute('aria-label') || oldEl?.textContent.trim() || null;
        const cardItemId=card.getAttribute('data-item-id')||'';
        // Product URL plus either a numeric price or a real MLB card identifier.
        if (!current && !/^MLB-?\d+$/i.test(cardItemId)) continue;
        const text=card.innerText||'';
        seen.add(title); out.push({title,url:link.href,price:current,origPrice:old,card_item_id:cardItemId,
          promo:(text.match(/\b\d+%\s*OFF\b/i)||text.match(/Oferta|Promoção|Cupom/i)||[])[0]||null,
          description:text.slice(0,1000)});
      } return out;
    }''')
    items = [{"title": x["title"], "url": x["url"], "price": parse_price(x["price"]),
              "original_price": parse_price(x["origPrice"]), "promo": x["promo"],
              "description": x["description"], "card_item_id": x["card_item_id"]} for x in cards]
    return {"fallback": False, "items": [item for item in items if is_valid_ml_item(item)]}
    js_data = page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        const allLinks = document.querySelectorAll('a[href]');
        allLinks.forEach(a => {
            const href = a.href || '';
            if (!href.match(/MLB-?\\d{6,}/i) && !href.match(/\\/p\\/MLB\\d+/i)) return;
            if (href.includes('lista.mercadolivre') || href.includes('_Container_')) return;
            let title = a.getAttribute('title') || a.getAttribute('aria-label') || '';
            if (!title || title.length < 5) {
                const container = a.closest('[class*="item"]') || a.closest('[class*="card"]')
                    || a.closest('[class*="bookmark"]') || a.closest('[class*="product"]')
                    || a.parentElement?.parentElement?.parentElement;
                if (container) {
                    const h = container.querySelector('h2, h3, [class*="title"]');
                    title = h ? h.textContent.trim() : a.textContent.trim();
                }
            }
            if (!title || title.length < 5 || seen.has(title)) return;
            if (title.startsWith('Produtos de ') || title.startsWith('Produtos FULL')) return;
            seen.add(title);
            results.push({title, url: href});
        });
        return {links: results, text: document.body.innerText};
    }""")
    text = js_data.get("text", "")
    links = js_data.get("links", [])
    if not links:
        return {"fallback": True, "text": text}
    link_map = {it["title"]: it["url"] for it in links}
    text_items = parse_ml_text_fallback(text)
    # Merge: text items get URLs from link_map; link-only items added without price
    merged = {}
    for it in text_items:
        title = it["title"]
        merged[title] = it
        for link_title, link_url in link_map.items():
            if link_title in title or title in link_title:
                merged[title]["url"] = link_url
                break
    for link_title, link_url in link_map.items():
        found = False
        for existing_title in merged:
            if link_title in existing_title or existing_title in link_title:
                found = True
                break
        if not found:
            merged[link_title] = {"title": link_title, "url": link_url, "price": None,
                                  "original_price": None, "promo": None}
    return {"fallback": False, "items": list(merged.values()), "premerged": True}


def scrape_amazon_cart_js(page):
    """Extract cart and saved rows from product links, not incidental data-asin nodes."""
    page.goto("https://www.amazon.com.br/gp/cart/view.html", wait_until="domcontentloaded", timeout=GOTO_TIMEOUT)
    time.sleep(NAV_PAUSE)
    return page.evaluate("""() => {
        const result = {active: [], saved: [], fallback: false};
        const asinFrom = href => {
            try {
                const u = new URL(href);
                if (!/(^|\\.)amazon\\.com\\.br$/i.test(u.hostname)) return null;
                return u.pathname.match(/\\/(?:dp|gp\\/product)\\/([A-Z0-9]{10})(?:[/?]|$)/i)?.[1]?.toUpperCase() || null;
            } catch (_) { return null; }
        };
        const activeSection = document.querySelector('#sc-active-cart, [data-name="Active Items"]')
            || document.querySelector('.sc-list-body');
        const savedSection = document.querySelector('#sc-saved-cart, [data-name="Saved Items"]');

        function extractItems(section, requirePrice) {
            if (!section) return [];
            const items = [], seen = new Set();
            // Contract verified 2026-09-13 against the logged-in Amazon cart:
            // each real line is .sc-list-item[data-asin] and uses /gp/product/
            // links. Iterate the row, not arbitrary product links, so nested
            // delete/image/title anchors cannot select a partial wrapper.
            for (const row of section.querySelectorAll('.sc-list-item[data-asin]')) {
                const asin = (row.dataset.asin || '').toUpperCase();
                const linkEl = [...row.querySelectorAll('a[href*="/dp/"], a[href*="/gp/product/"]')]
                    .find(link => asinFrom(link.href) === asin);
                if (!asin || !linkEl || !section.contains(row) || seen.has(asin)) continue;
                const titleEl = row.querySelector('.a-truncate-full.a-offscreen, .sc-product-title, .a-truncate-cut, [class*="sc-product-title"]') || linkEl;
                const title = (titleEl.getAttribute('title') || titleEl.textContent || '').replace(/\\s+/g, ' ').trim();
                if (title.length < 3 || /^(remover|salvar para mais tarde|mover para o carrinho)$/i.test(title)) continue;
                // `.sc-price` cobre a linha sem buy box própria, que troca o preço
                // por "Ver outras ofertas novas a partir de R$ X" — verificado em
                // 2026-09-13 no item salvo B0FMV5MRVJ, que ficava sem preço.
                const priceEl = row.querySelector('.sc-product-price .a-offscreen, .sc-product-price, .sc-badge-price-to-pay .a-offscreen, .a-price .a-offscreen, .sc-price');
                const priceText = priceEl ? priceEl.textContent.trim() : '';
                if (requirePrice && !/R\\$\\s*[\\d.]+,[\\d]{2}/.test(priceText)) continue;
                const text = row.innerText || '';
                let promo = row.querySelector('[class*="badge"], [class*="coupon"], [class*="deal"]')?.textContent?.trim() || null;
                const offMatch = text.match(/(\\d+)%\\s*(?:OFF|off|desconto)/);
                if (offMatch) promo = offMatch[0];
                const origText = row.querySelector('.a-text-strike, [class*="was-price"]')?.textContent?.trim() || null;
                seen.add(asin);
                items.push({title, price: priceText, url: linkEl.href.split('?')[0], promo, origPrice: origText, asin});
            }
            return items;
        }

        result.active = extractItems(activeSection, true);
        result.saved = extractItems(savedSection, false);
        if (result.active.length === 0 && result.saved.length === 0) {
            result.fallback = true;
            result.text = document.body.innerText;
        }
        return result;
    }""")


def scrape_amazon_wishlist_js(page):
    """Use JS to extract Amazon wishlist items via DOM."""
    page.goto("https://www.amazon.com.br/hz/wishlist/ls/default", wait_until="domcontentloaded", timeout=GOTO_TIMEOUT)
    time.sleep(NAV_PAUSE)
    # A wishlist carrega por scroll infinito. Um número fixo de rolagens corta a
    # lista no meio (60 de 95 em 2026-09-13) e desativa em massa o que ficou de
    # fora; rolar até `#endOfListMarker` ou até a contagem estabilizar.
    stable = 0
    previous = -1
    for _ in range(WISHLIST_MAX_SCROLLS):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(SCROLL_PAUSE)
        state = page.evaluate(
            "() => ({rows: document.querySelectorAll('li[data-itemid]').length,"
            " end: !!document.querySelector('#endOfListMarker')})"
        )
        if state["end"] and state["rows"] == previous:
            break
        stable = stable + 1 if state["rows"] == previous else 0
        if stable >= 3:
            break
        previous = state["rows"]
    return page.evaluate("""() => {
        const items = [];
        const seenAsins = new Set();
        const asinFrom = href => {
            try {
                const u = new URL(href);
                if (!/(^|\\.)amazon\\.com\\.br$/i.test(u.hostname)) return null;
                return u.pathname.match(/\\/(?:dp|gp\\/product)\\/([A-Z0-9]{10})(?:[/?]|$)/i)?.[1]?.toUpperCase() || null;
            } catch (_) { return null; }
        };
        // Start from an actual Amazon product link, then require its closest
        // wishlist row. Broad id-prefix selectors also match nested layout,
        // recommendation and control nodes and previously yielded 180 rows.
        for (const linkEl of document.querySelectorAll('a[href*="/dp/"], a[href*="/gp/product/"]')) {
            const asin = asinFrom(linkEl.href);
            const el = linkEl.closest('li[data-itemid], [id^="itemMain_"]');
            if (!asin || !el || seenAsins.has(asin)) continue;
            const titleEl = el.querySelector('[id^="itemName_"] a[href], a[id^="itemName_"][href]') || linkEl;
            const title = (titleEl.getAttribute('title') || titleEl.textContent || '').replace(/\\s+/g, ' ').trim();
            if (title.length < 3 || /^(adicionar|remover|mover|compartilhar|ver detalhes)$/i.test(title)) continue;
            const text = el.innerText || '';
            const priceEl = el.querySelector('[id^="itemPrice_"] .a-offscreen, [id^="itemPrice_"] .a-price .a-offscreen, .a-price .a-offscreen');
            const priceText = priceEl ? priceEl.textContent.trim() : '';
            // Valid product identity is mandatory; a price is optional because
            // unavailable Amazon wishlist products are still legitimate entries.
            seenAsins.add(asin);
            let promo = null;
            let origPrice = null;
            const caiu = text.match(/Preço caiu (\\d+)%/);
            if (caiu) {
                promo = 'Preço caiu ' + caiu[1] + '%';
                const custava = text.match(/custava R\\$\\s*([\\d.,]+)/);
                if (custava) origPrice = 'R$ ' + custava[1];
            }
            const offMatch = text.match(/(\\d+)%\\s*off\\s*Oferta/i);
            if (offMatch) {
                promo = offMatch[0];
                const sug = text.match(/sugerido:\\s*R\\$\\s*([\\d.,]+)/);
                if (sug) origPrice = 'R$ ' + sug[1];
            }
            if (!promo) {
                for (const kw of ['Oferta', 'Deal', 'Promoção', 'Mais vendido']) {
                    if (text.includes(kw)) { promo = kw; break; }
                }
            }
            items.push({title, price: priceText, url: linkEl.href.split('?')[0], promo, origPrice, asin});
        }
        return items.length ? {fallback: false, items} : {fallback: true, text: document.body.innerText};
    }""")


def parse_ml_text_fallback(text):
    """Fallback: parse ML innerText by splitting on 'Excluir' blocks."""
    blocks = re.split(r"\nExcluir\n|\nExcluir$", text)
    items = []
    skip_set = {"Adicionar à lista", "Frete grátis", "Último disponível", "Meus favoritos",
                "Compras", "Vendas", "Faturamento", "Meu perfil", "Configurações", "Favoritos",
                "Criar lista", "Listas", "Pular para o conteúdo", "Comentar sobre acessibilidade",
                "Mais informações", "Trabalhe conosco", "Atalhos do teclado", "Menu de atalhos",
                "Minha conta"}
    skip_contains = ["Mercado Li", "Mercado Livre", "produtos em seu carrinho", "Onde comprar",
                     "Pesquisar", "atalhos do teclado", "Menu de atalho"]
    for block in blocks:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        title = None
        for l in lines:
            if l in skip_set:
                continue
            if any(s in l for s in skip_contains):
                continue
            if len(l) > 15 \
               and not l.startswith("R$") and not re.match(r"^[\d.,]+$", l) \
               and "OFF" not in l and "Pix" not in l and "juros" not in l \
               and not l.startswith("Por ") and not re.match(r"^\d+x\s", l) \
               and not l.startswith("ou "):
                title = l
                break
        if not title:
            continue
        full = " ".join(lines)
        # Extract all R$ amounts
        all_prices = re.findall(r"R\$\s*[\d.,]+", full)
        parsed = [parse_price(p) for p in all_prices]
        parsed = [p for p in parsed if p and p > 5]
        # The first large price is typically the original, second is the discounted
        # But we want the main (discounted) price — take the second if OFF is present
        price = None
        original = None
        promo = None
        m = re.search(r"(\d+)%\s*OFF", full, re.IGNORECASE)
        if m:
            promo = f"{m.group(1)}% OFF"
        if len(parsed) >= 2 and promo:
            # First price = original, second = discounted
            original = parsed[0]
            price = parsed[1]
            if price > original:
                price, original = original, price
        elif parsed:
            price = parsed[0]
        items.append({"title": title, "price": price, "original_price": original, "promo": promo, "url": None})
    return items


def parse_amazon_text_fallback(text):
    """Fallback: parse Amazon wishlist/cart text by 'Mover' blocks."""
    blocks = re.split(r"\nMover\n|\nMover$", text)
    items = []
    skip_words = {"Mostrar:", "Classificar por:", "Lista de desejos", "Particular",
                  "Conheça-nos", "Ganhe dinheiro conosco", "Deixe-nos ajudar você"}
    for block in blocks:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if len(lines) < 3:
            continue
        title = None
        for l in lines:
            if len(l) > 10 and l not in skip_words \
               and not l.startswith("R$") and not l.startswith("de ") \
               and not re.match(r"^[\d.,]+", l) and "estrelas" not in l \
               and "Item adicionado" not in l and "Adicionar" not in l \
               and "Comprar" not in l and "No carrinho" not in l \
               and "Amazon" not in l and "Enviar para" not in l:
                title = l
                break
        if not title:
            continue
        full = " ".join(lines)
        all_prices = re.findall(r"R\$\s*[\d.,]+", full)
        parsed = [parse_price(p) for p in all_prices]
        parsed = [p for p in parsed if p and p > 1]
        price = parsed[0] if parsed else None
        original = None
        promo = None
        m = re.search(r"Preço caiu (\d+)%", full)
        if m:
            promo = f"Preço caiu {m.group(1)}%"
            m2 = re.search(r"custava R\$\s*([\d.,]+)", full)
            if m2:
                original = parse_price(f"R$ {m2.group(1)}")
        m = re.search(r"(\d+)%\s*off\s*Oferta", full, re.IGNORECASE)
        if m:
            promo = m.group(0)
            m2 = re.search(r"sugerido:\s*R\$\s*([\d.,]+)", full)
            if m2:
                original = parse_price(f"R$ {m2.group(1)}")
        items.append({"title": title, "price": price, "original_price": original, "promo": promo, "url": None})
    return items


def process_ml_data(data):
    """Process ML scraper result into safe item dicts.

    A second pure-Python guard protects persistence if a future DOM change bypasses
    the browser-side card filter. Text-only fallback has no product URL and is
    intentionally ignored rather than manufacturing observations from UI text.
    """
    if data.get("fallback"):
        return []
    return [item for item in data.get("items", []) if is_valid_ml_item(item)]


def process_amazon_items(raw_items):
    """Process Amazon JS scraper items into standard dicts."""
    items = []
    for it in raw_items:
        items.append({
            "title": it["title"], "url": it.get("url"),
            "price": parse_price(it.get("price")),
            "original_price": parse_price(it.get("origPrice")),
            "promo": it.get("promo"),
        })
    return items


def upsert_scraped_item(conn, page, source, tag, item, *, commit=True):
    """Persist one scrape result and its card-only screenshot when available."""
    screenshot_path = capture_item_card(page, source, tag, item)
    item_id = upsert_item(conn, item["title"], source, tag, item.get("url"),
                          item.get("price"), item.get("original_price"), item.get("promo"),
                          commit=commit)
    if screenshot_path:
        persist_observation_screenshot(conn, item_id, screenshot_path, commit=commit)
    return item_id


def validate_source_snapshot(conn, source, tag, items):
    """Reject degraded extracts before they can modify one source/list snapshot."""
    unique = {}
    for item in items:
        title = clean_title(item.get("title"))
        url = (item.get("url") or "").split("?", 1)[0]
        if not title or not url:
            continue
        if source == "amazon" and not is_amazon_product_url(url):
            continue
        # A cart line has a payable price. Saved-for-later and wishlist entries
        # can legitimately be unavailable, so they intentionally remain price-optional.
        if source == "amazon" and tag == "carrinho":
            price = item.get("price")
            if not isinstance(price, (int, float)) or price <= 0:
                continue
        # URL/ASIN is the stable identity; duplicate DOM wrappers must not count.
        unique[url] = {**item, "title": title, "url": url}
    clean = list(unique.values())
    if not clean:
        raise ValueError(f"extração vazia/inválida para {source}/{tag}; estado anterior preservado")
    previous = conn.execute(
        "SELECT COUNT(*) FROM observacoes_loja WHERE source=? AND lista=? AND active=1",
        (source, tag),
    ).fetchone()[0]
    if previous >= 5:
        minimum = max(1, (previous + 1) // 2)
        maximum = max(previous + 15, (previous * 3 + 1) // 2)
        if not minimum <= len(clean) <= maximum:
            raise ValueError(
                f"cardinalidade suspeita para {source}/{tag}: {len(clean)} (anterior {previous}, aceitável {minimum}..{maximum}); estado anterior preservado"
            )
    return clean


def apply_source_snapshot(conn, page, source, tag, items):
    """Atomically apply one validated source/list snapshot; never partial-churn it."""
    items = validate_source_snapshot(conn, source, tag, items)
    savepoint = f"sync_{source}_{tag}".replace("-", "_")
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        ids = [upsert_scraped_item(conn, page, source, tag, item, commit=False) for item in items]
        mark_inactive(conn, source, tag, ids, commit=False)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        conn.commit()
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        conn.rollback()
        raise
    return len(items)


def is_amazon_product_url(url):
    """Accept a BR Amazon product URL, including localized slug prefixes before /dp."""
    if not isinstance(url, str) or not re.match(
        r"^https?://(?:www\.)?amazon\.com\.br(?:/|$)", url, re.I,
    ):
        return False
    path = url.split("?", 1)[0].split("#", 1)[0]
    return bool(re.search(r"/(?:dp|gp/product)/[A-Z0-9]{10}(?:/|$)", path, re.I))


## Estado do produto: catálogo vivo vs. saído de linha
ESTADO_ATIVO = "ativo"
ESTADO_DESCONTINUADO = "descontinuado"
## `estado` nasceu NULL no schema; NULL vale como 'ativo' em toda leitura.
ESTADO_VIVO_SQL = f"COALESCE(p.estado, '{ESTADO_ATIVO}') <> '{ESTADO_DESCONTINUADO}'"
ESTADO_MORTO_SQL = f"COALESCE(p.estado, '{ESTADO_ATIVO}') = '{ESTADO_DESCONTINUADO}'"

# Frases que a loja usa quando o item saiu do catálogo ou está indisponível sem
# previsão. Deliberadamente estreitas: "não está disponível" solto casa com o
# seletor de edição da Kindle Store em produto vivo.
INDISPONIVEL_RE = re.compile(
    r"não conseguimos encontrar esta página"
    r"|atualmente indisponível"
    r"|no momento,? este item não está disponível"
    r"|produto (?:temporariamente )?indisponível"
    r"|temporariamente fora de estoque",
    re.IGNORECASE,
)


## Preço na página do produto: escopos verificados ao vivo em 2026-09-13
## Uma varredura livre por `.a-price` casa com carrossel de recomendação e
## "compre junto", devolvendo preço de OUTRO produto. Por isso o preço só é
## aceito dentro de um escopo que pertence à oferta rastreada.
PRODUCT_PRICE_JS = r"""() => {
    const RE = /R\$\s*([\d.]+,\d{2})/;
    const visible = el => !!(el && el.offsetParent !== null);
    const outOfStock = !!document.querySelector('#outOfStockBuyBox_feature_div');
    const thirdPartyOnly = !!document.querySelector('#unqualifiedBuyBox_feature_div');
    const state = {
        title: document.title || '',
        text: (document.body?.innerText || '').slice(0, 20000),
        price: null, origPrice: null, priceScope: null,
        outOfStock, thirdPartyOnly,
    };
    // Oferta rastreada sem estoque: o twister ao lado ainda anuncia preço de
    // outra variação. Devolver null aqui é o resultado correto.
    if (outOfStock) { state.priceScope = 'outOfStock'; return state; }
    const scopes = ['#corePriceDisplay_desktop_feature_div', '#corePrice_feature_div',
                    '#apex_offerDisplay_desktop_single_offer', '#desktop_buybox', '#buybox'];
    // Produto vendido só por terceiros não tem buy box; o preço da variação
    // selecionada fica no twister.
    if (thirdPartyOnly) scopes.push('#apex_price.apex_on_twister_price', '[id^="inline-twister-row-"]');
    const grab = (box, sel) => {
        for (const el of box.querySelectorAll(sel)) {
            const m = (el.textContent || '').match(RE);
            if (m && m[1] !== '0,00') return m[1];
        }
        return null;
    };
    for (const scope of scopes) {
        for (const box of document.querySelectorAll(scope)) {
            if (!visible(box)) continue;
            // `.a-text-price` é o "de/por" riscado, nunca o preço corrente.
            let price = grab(box, '.a-price:not(.a-text-price) .a-offscreen');
            if (!price) {
                const m = (box.innerText || '').match(RE);
                if (m && m[1] !== '0,00') price = m[1];
            }
            if (!price) continue;
            const orig = grab(box, '.a-price.a-text-price .a-offscreen, .a-text-strike');
            state.price = price;
            state.origPrice = orig && orig !== price ? orig : null;
            state.priceScope = scope;
            return state;
        }
    }
    return state;
}"""


def classify_product_page(page, url):
    """Inspeciona a página de um produto: estado + preço da oferta rastreada.

    Devolve `{estado, price, original_price}`. `estado` só é `descontinuado` com
    evidência forte: 404 da loja, ou frase de indisponibilidade **sem nenhum
    preço** na página. Falha de rede, captcha ou página que renderizou preço caem
    em ativo/None — nunca em descontinuado, para que um bug de extração não
    apague o item das listas.
    """
    blank = {"estado": None, "price": None, "original_price": None}
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT)
    except Exception:
        return blank
    time.sleep(NAV_PAUSE)
    status = response.status if response is not None else None
    if status == 404 or (status is not None and 400 <= status < 500):
        return {**blank, "estado": ESTADO_DESCONTINUADO}
    try:
        state = page.evaluate(PRODUCT_PRICE_JS)
    except Exception:
        return blank
    price = parse_price(state.get("price"))
    result = {"estado": None, "price": price,
              "original_price": parse_price(state.get("origPrice"))}
    if price:
        result["estado"] = ESTADO_ATIVO
    elif INDISPONIVEL_RE.search(state.get("title", "") + "\n" + state.get("text", "")):
        result["estado"] = ESTADO_DESCONTINUADO
    return result


def set_estado(conn, produto_id, estado, *, commit=True):
    cur = conn.execute(
        "UPDATE produtos SET estado=?, updated_at=? WHERE id=? AND COALESCE(estado,?)<>?",
        (estado, now_br(), produto_id, ESTADO_ATIVO, estado),
    )
    if commit:
        conn.commit()
    return cur.rowcount


def record_observation_price(conn, observacao_id, price, original_price, *, commit=True):
    """Grava na observação o preço recuperado na página do produto.

    Escreve direto em `observacoes_loja`/`historico_observacoes` porque a lista
    não trouxe preço nenhum: não há linha nova em `items` para o gatilho de
    compatibilidade espelhar. `items` fica alinhado em seguida, sem disparar o
    gatilho de UPDATE, que sobrescreveria a observação com o NULL da lista.
    """
    row = conn.execute(
        "SELECT preco_atual, produto_id, source, lista FROM observacoes_loja WHERE id=?",
        (observacao_id,),
    ).fetchone()
    if row is None:
        return False
    old_price, produto_id, source, lista = row
    if old_price is None:
        # A lista não traz preço para este item, então o snapshot de cada sync
        # zera `preco_atual` pelo gatilho de compatibilidade. O último preço
        # conhecido está no histórico; sem ele, toda execução gravaria de novo o
        # mesmo valor e inventaria uma variação.
        last = conn.execute(
            "SELECT preco FROM historico_observacoes WHERE observacao_id=? ORDER BY id DESC LIMIT 1",
            (observacao_id,),
        ).fetchone()
        old_price = last[0] if last else None
    ts = now_br()
    changed = old_price is None or abs(old_price - price) > 0.01
    if changed:
        conn.execute(
            """UPDATE observacoes_loja SET preco_anterior=?, preco_atual=?,
               preco_original=COALESCE(?, preco_original), last_seen=? WHERE id=?""",
            (old_price, price, original_price, ts, observacao_id),
        )
        conn.execute(
            "INSERT INTO historico_observacoes(observacao_id, preco, preco_original, registrado_at) VALUES(?,?,?,?)",
            (observacao_id, price, original_price, ts),
        )
    else:
        # Preço igual ao já conhecido: só repor o valor que o snapshot zerou.
        conn.execute(
            """UPDATE observacoes_loja SET preco_atual=?,
               preco_original=COALESCE(?, preco_original), last_seen=? WHERE id=?""",
            (price, original_price, ts, observacao_id),
        )
    conn.execute(
        """UPDATE items SET current_price=?
            WHERE title=(SELECT nome FROM produtos WHERE id=?) AND source=? AND tag=?""",
        (price, produto_id, source, lista),
    )
    if commit:
        conn.commit()
    return changed


def check_discontinued(conn, page, verbose=True):
    """Revisita produtos suspeitos, recupera o preço e (re)classifica `estado`.

    Candidatos: observação ativa e scrapeada (fora `avulso`) sem preço, mais os já
    marcados como descontinuados — assim a marcação é reversível quando o item
    volta ao catálogo. Listas como wishlist e salvos escondem o preço de itens
    sem buy box própria, então a página do produto é a única fonte para eles.
    Nada é apagado: só `estado` e o preço da observação mudam.
    """
    rows = conn.execute(f"""
        SELECT p.id, p.nome, o.url, MIN(o.id)
          FROM observacoes_loja o JOIN produtos p ON p.id=o.produto_id
         WHERE o.active=1 AND o.source<>'avulso' AND o.url IS NOT NULL
           AND (o.preco_atual IS NULL OR {ESTADO_MORTO_SQL})
         GROUP BY p.id
    """).fetchall()
    result = {"verificados": len(rows), "descontinuados": [], "reativados": [],
              "inconclusivos": [], "precos_recuperados": []}
    for produto_id, nome, url, observacao_id in rows:
        info = classify_product_page(page, url)
        estado, price = info["estado"], info["price"]
        if estado == ESTADO_DESCONTINUADO:
            set_estado(conn, produto_id, ESTADO_DESCONTINUADO)
            result["descontinuados"].append({"id": produto_id, "nome": nome, "url": url})
        elif estado == ESTADO_ATIVO:
            if set_estado(conn, produto_id, ESTADO_ATIVO):
                result["reativados"].append({"id": produto_id, "nome": nome, "url": url})
        else:
            result["inconclusivos"].append({"id": produto_id, "nome": nome, "url": url})
        if price and record_observation_price(conn, observacao_id, price, info["original_price"]):
            result["precos_recuperados"].append({"id": produto_id, "nome": nome, "preco": price})
        if verbose:
            extra = f" | preço R$ {price:.2f}" if price else ""
            print(f"  [{produto_id}] {estado or 'inconclusivo'}{extra}: {nome[:55]}")
    return result


def dedupe_produtos(conn, apply=False):
    """Funde produtos com o mesmo `nome` criados por chaves divergentes.

    O gatilho de `items` normaliza o título só tirando espaço e hífen; a cópia em
    `normalize_schema` tirava todo caractere fora de [a-z0-9]. Em título com
    acento ou pontuação as duas chaves discordavam e o mesmo produto era inserido
    duas vezes. Mantém o produto mais antigo, reaponta as observações e leva o
    histórico junto; nenhuma linha de histórico é descartada.
    """
    nomes = [r[0] for r in conn.execute(
        "SELECT nome FROM produtos GROUP BY nome HAVING COUNT(*)>1").fetchall()]
    result = {"grupos": len(nomes), "produtos_removidos": [], "observacoes_fundidas": [],
              "historico_movido": 0, "aplicado": apply}
    if not apply:
        result["produtos_duplicados"] = nomes
        return result
    conn.execute("SAVEPOINT dedupe_produtos")
    try:
        for nome in nomes:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM produtos WHERE nome=? ORDER BY id", (nome,)).fetchall()]
            keeper, dups = ids[0], ids[1:]
            for dup in dups:
                for obs_id, source, lista in conn.execute(
                        "SELECT id, source, lista FROM observacoes_loja WHERE produto_id=?",
                        (dup,)).fetchall():
                    alvo = conn.execute(
                        "SELECT id FROM observacoes_loja WHERE produto_id=? AND source=? AND lista=?",
                        (keeper, source, lista)).fetchone()
                    if alvo is None:
                        conn.execute("UPDATE observacoes_loja SET produto_id=? WHERE id=?",
                                     (keeper, obs_id))
                        continue
                    # UNIQUE(produto_id, source, lista) impede reapontar: o
                    # histórico migra para a observação que fica e a duplicata sai.
                    conn.execute(
                        """UPDATE observacoes_loja SET screenshot_path=COALESCE(
                               screenshot_path, (SELECT screenshot_path FROM observacoes_loja WHERE id=?))
                            WHERE id=?""", (obs_id, alvo[0]))
                    moved = conn.execute(
                        "UPDATE historico_observacoes SET observacao_id=? WHERE observacao_id=?",
                        (alvo[0], obs_id)).rowcount
                    result["historico_movido"] += moved
                    conn.execute("DELETE FROM observacoes_loja WHERE id=?", (obs_id,))
                    result["observacoes_fundidas"].append({"removida": obs_id, "mantida": alvo[0]})
                conn.execute("DELETE FROM produtos WHERE id=?", (dup,))
                result["produtos_removidos"].append({"id": dup, "nome": nome, "mantido": keeper})
        conn.execute("RELEASE SAVEPOINT dedupe_produtos")
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT dedupe_produtos")
        conn.execute("RELEASE SAVEPOINT dedupe_produtos")
        conn.rollback()
        raise
    return result


def repair_bad_wishlist_run(conn, backup_path, bad_since="2026-09-13T11:50:00-03:00"):
    """Restore only verified wishlist rows from a pre-run backup.

    The repair never copies arbitrary old rows back: every restored observation
    must have a real Amazon product URL. Rows created by the bad run are merely
    deactivated, and price history plus current screenshots are retained.
    """
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(backup_path)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    safety_path = f"{DB_PATH}.pre-reparo-wishlist-{stamp}.bak"
    safety = sqlite3.connect(safety_path)
    conn.backup(safety)
    safety.close()
    safe = sqlite3.connect(backup_path)
    safe.row_factory = sqlite3.Row
    rows = safe.execute("""
        SELECT p.nome, o.preco_atual, o.preco_anterior, o.preco_original,
               o.percentual_desconto, o.url, o.promocao, o.descricao,
               o.metadados, o.first_seen, o.last_seen, o.active
          FROM observacoes_loja o JOIN produtos p ON p.id=o.produto_id
         WHERE o.source='amazon' AND o.lista='wishlist'
    """).fetchall()
    verified = {r['nome']: r for r in rows if is_amazon_product_url(r['url'])}
    if not verified:
        raise ValueError("backup não contém wishlist Amazon verificável")
    conn.execute("SAVEPOINT repair_bad_wishlist")
    restored = deactivated = removed_history = 0
    try:
        current = conn.execute("""
          SELECT o.id, p.nome FROM observacoes_loja o JOIN produtos p ON p.id=o.produto_id
           WHERE o.source='amazon' AND o.lista='wishlist' AND o.last_seen>=?
        """, (bad_since,)).fetchall()
        for obs_id, title in current:
            row = verified.get(title)
            if row is None:
                conn.execute("UPDATE observacoes_loja SET active=0 WHERE id=?", (obs_id,))
                conn.execute("UPDATE items SET active=0 WHERE title=? AND source='amazon' AND tag='wishlist'", (title,))
                deactivated += 1
                continue
            conn.execute("""
              UPDATE observacoes_loja
                 SET preco_atual=?, preco_anterior=?, preco_original=?, percentual_desconto=?,
                     url=?, promocao=?, descricao=?, metadados=?, first_seen=?, last_seen=?, active=?
               WHERE id=?
            """, (*[row[k] for k in ('preco_atual','preco_anterior','preco_original','percentual_desconto','url','promocao','descricao','metadados','first_seen','last_seen','active')], obs_id))
            conn.execute("""
              UPDATE items SET current_price=?, original_price=?, promo_tag=?, url=?,
                  first_seen=?, last_seen=?, active=?
               WHERE title=? AND source='amazon' AND tag='wishlist'
            """, (row['preco_atual'], row['preco_original'], row['promocao'], row['url'], row['first_seen'], row['last_seen'], row['active'], title))
            restored += 1
        # The backup predates the defective run. Delete only a window event for
        # an item whose current state has been restored exactly to that backup;
        # this removes duplicate-DOM noise without touching real transitions.
        for title, row in verified.items():
            item = conn.execute("""
              SELECT current_price, original_price, promo_tag, url, first_seen, last_seen, active
                FROM items WHERE title=? AND source='amazon' AND tag='wishlist'
            """, (title,)).fetchone()
            expected = (row['preco_atual'], row['preco_original'], row['promocao'], row['url'],
                        row['first_seen'], row['last_seen'], row['active'])
            if item is not None and tuple(item) == expected:
                hist = conn.execute("""
                  DELETE FROM price_history
                   WHERE item_id IN (SELECT id FROM items WHERE title=? AND source='amazon' AND tag='wishlist')
                     AND recorded_at>=?
                """, (title, bad_since))
                removed_history += hist.rowcount
        conn.execute("RELEASE SAVEPOINT repair_bad_wishlist")
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT repair_bad_wishlist")
        conn.execute("RELEASE SAVEPOINT repair_bad_wishlist")
        conn.rollback()
        raise
    finally:
        safe.close()
    return {"restored": restored, "deactivated": deactivated, "removed_history": removed_history, "backup": safety_path}


def do_sync(conn, verbose=True):
    errors = []
    total = 0
    pw, browser, mode = launch_browser()

    try:
        if mode == "cdp":
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        else:
            ctx = browser
        page = ctx.new_page()

        # --- ML Carrinho ---
        if verbose:
            print("[sync] ML Carrinho...")
        try:
            data = scrape_ml_js(page, "https://www.mercadolivre.com.br/gz/cart/v2")
            items = process_ml_data(data)
            count = apply_source_snapshot(conn, page, "ml", "carrinho", items)
            total += count
            if verbose:
                print(f"  -> {count} itens")
        except Exception as e:
            errors.append(f"ML Carrinho: {e}")
            if verbose:
                print(f"  ERRO: {e}")

        time.sleep(BETWEEN_SOURCES)

        # --- ML Favoritos ---
        if verbose:
            print("[sync] ML Favoritos...")
        try:
            data = scrape_ml_js(page, "https://myaccount.mercadolivre.com.br/bookmarks/list")
            items = process_ml_data(data)
            count = apply_source_snapshot(conn, page, "ml", "favoritos", items)
            total += count
            if verbose:
                print(f"  -> {count} itens")
        except Exception as e:
            errors.append(f"ML Favoritos: {e}")
            if verbose:
                print(f"  ERRO: {e}")

        time.sleep(BETWEEN_SOURCES)

        # --- Amazon Carrinho + Salvos ---
        if verbose:
            print("[sync] Amazon Carrinho + Salvos...")
        try:
            data = scrape_amazon_cart_js(page)
            if data.get("fallback"):
                raise ValueError("cart Amazon sem linhas verificáveis; estado anterior preservado")
            active_items = process_amazon_items(data.get("active", []))
            saved_items = process_amazon_items(data.get("saved", []))
            # Each list has an independent snapshot/transaction: a selector
            # regression in one must not block or churn the other.
            for tag, items, label in (("carrinho", active_items, "no carrinho"),
                                      ("salvo", saved_items, "salvos")):
                try:
                    count = apply_source_snapshot(conn, page, "amazon", tag, items)
                    total += count
                    if verbose:
                        print(f"  -> {count} {label}")
                except Exception as e:
                    errors.append(f"Amazon {tag}: {e}")
                    if verbose:
                        print(f"  ERRO {tag}: {e}")
        except Exception as e:
            errors.append(f"Amazon Carrinho: {e}")
            if verbose:
                print(f"  ERRO: {e}")

        time.sleep(BETWEEN_SOURCES)

        # --- Amazon Wishlist ---
        if verbose:
            print("[sync] Amazon Wishlist...")
        try:
            data = scrape_amazon_wishlist_js(page)
            if data.get("fallback"):
                # Text fallback cannot prove an Amazon product URL/ASIN. It must
                # fail closed rather than mass-deactivating a real wishlist.
                raise ValueError("wishlist sem cards Amazon verificáveis; estado anterior preservado")
            items = process_amazon_items(data.get("items", []))
            count = apply_source_snapshot(conn, page, "amazon", "wishlist", items)
            total += count
            if verbose:
                print(f"  -> {count} itens")
        except Exception as e:
            errors.append(f"Amazon Wishlist: {e}")
            if verbose:
                print(f"  ERRO: {e}")

        # --- Produtos sem preço: confirmar na página do produto ---
        if verbose:
            print("[sync] Revisando produtos sem preço...")
        try:
            estados = check_discontinued(conn, page, verbose=verbose)
            if verbose:
                print(f"  -> {len(estados['descontinuados'])} descontinuados, "
                      f"{len(estados['reativados'])} reativados, "
                      f"{len(estados['inconclusivos'])} inconclusivos")
        except Exception as e:
            errors.append(f"Estado dos produtos: {e}")
            if verbose:
                print(f"  ERRO: {e}")

        page.close()
    finally:
        # page é a única tab criada por este fluxo. Não fechar browser/contexto CDP.
        pw.stop()

    result = {"total": total, "errors": errors, "timestamp": now_br()}
    if verbose:
        print(f"\nSync completo: {total} itens, {len(errors)} erros")
        if errors:
            print("Erros:")
            for e in errors:
                print(f"  - {e}")
    return result


def do_list(conn, source=None, tag=None, discontinued=False):
    estado_sql = ESTADO_MORTO_SQL if discontinued else ESTADO_VIVO_SQL
    query = f"""SELECT p.id, p.nome, o.source, o.lista, o.preco_atual, o.preco_original,
               o.promocao, o.url FROM observacoes_loja o JOIN produtos p ON p.id=o.produto_id
               WHERE o.active=1 AND {estado_sql}"""
    params = []
    if source:
        query += " AND source=?"
        params.append(source)
    if tag:
        query += " AND tag=?"
        params.append(tag)
    query += " ORDER BY source, tag, nome"
    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("Nenhum item descontinuado." if discontinued else "Nenhum item encontrado.")
        return
    if discontinued:
        print("=== ITENS DESCONTINUADOS ===")
    current_source = None
    current_tag = None
    for row in rows:
        iid, title, src, tg, price, orig, promo, url = row
        if is_ui_title(title):
            continue
        if src != current_source or tg != current_tag:
            current_source, current_tag = src, tg
            print(f"\n=== {src.upper()} / {tg} ===")
        price_str = f"R$ {price:.2f}" if price else "sem preço"
        if orig and price and orig > price:
            price_str += f" (de R$ {orig:.2f})"
        promo_str = f" [{promo}]" if promo else ""
        print(f"  [{iid}] {title} — {price_str}{promo_str}")
        if url:
            print(f"       {url}")


## Seções do relatório de ofertas, na ordem de exibição
DEAL_SECTIONS = [
    ("ml", "carrinho", "ML / Carrinho"),
    ("ml", "favoritos", "ML / Favoritos"),
    ("amazon", "carrinho", "Amazon / Carrinho"),
    ("amazon", "salvo", "Amazon / Salvos"),
    ("amazon", "wishlist", "Amazon / Wishlist"),
]


def is_ui_title(title):
    return (not title or len(title) < 5 or not re.search(r"[a-zà-ÿ]", title, re.I)
            or re.search(r"^(cupom disponível|produtos de |abrir/fechar|menu de|pular para|aproveite o frete|continuar \()", title, re.I))


MIN_DROP_PCT = 3.0  # quedas menores que isso não entram no relatório


def ensure_deal_columns(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(observacoes_loja)")]
    if "ultimo_preco_reportado" not in cols:
        conn.execute("ALTER TABLE observacoes_loja ADD COLUMN ultimo_preco_reportado REAL")
        conn.commit()


def build_deals(conn):
    """Quedas reais: preço atual vs último preço anterior diferente (preco_anterior),
    queda >= MIN_DROP_PCT e ainda não reportada naquele preço. `menor_preco` = menor
    preço do histórico; `novo_minimo` = atual <= menor já visto."""
    ensure_deal_columns(conn)
    sections = []
    for source, tag, label in DEAL_SECTIONS:
        rows = conn.execute(f"""
          SELECT p.nome, o.preco_atual, o.preco_anterior, o.promocao, o.url,
                 o.id, o.ultimo_preco_reportado,
                 (SELECT MIN(preco) FROM historico_observacoes h WHERE h.observacao_id=o.id)
          FROM observacoes_loja o JOIN produtos p ON p.id=o.produto_id
          WHERE o.active=1 AND o.source=? AND o.lista=? AND {ESTADO_VIVO_SQL}
          ORDER BY p.nome
        """, (source, tag)).fetchall()
        items=[]
        for title, current, previous, promo, url, oid, reportado, menor in rows:
            if is_ui_title(title) or not url or current is None:
                continue
            if previous is None or previous <= current:
                continue
            if reportado is not None and abs(reportado - current) <= 0.01:
                continue  # já reportado a este preço
            pct = (previous-current)*100/previous
            if pct < MIN_DROP_PCT:
                continue
            menor = min(menor, current) if menor is not None else current
            items.append({"produto": title, "preco_anterior": round(previous,2),
                          "preco_atual": round(current,2),
                          "percentual_reducao": round(pct,2),
                          "menor_preco": round(menor,2),
                          "novo_minimo": current <= menor + 0.01,
                          "url":url, "promocao":promo, "_obs_id": oid})
        if items: sections.append({"lista":label, "items":items})
    return {"sections": sections, "has_changes": bool(sections)}


def mark_deals_reported(conn, result):
    for sec in result["sections"]:
        for it in sec["items"]:
            conn.execute("UPDATE observacoes_loja SET ultimo_preco_reportado=? WHERE id=?",
                         (it["preco_atual"], it["_obs_id"]))
    conn.commit()


def format_price_br(value):
    txt = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    # Drop a trailing ",00" — mirrors how the report has always shown whole reais.
    return txt[:-3] if txt.endswith(",00") else txt


def format_deals_text(result):
    """Render the exact chat-ready format mandated by SKILL.md: one bullet per
    item, price arrow and the item URL always on the same line. This is the
    single source of truth for the report text — never hand-format from the
    JSON or from memory/recall, precisely because that produced link-less
    reports in the past (2026-09-28)."""
    if not result["sections"]:
        return "Nenhuma oferta nova."
    lines = []
    for section in result["sections"]:
        lines.append(f"**{section['lista']}**")
        for item in section["items"]:
            preco_ant = format_price_br(item["preco_anterior"])
            preco_atu = format_price_br(item["preco_atual"])
            pct = round(item["percentual_reducao"])
            lines.append(
                f"- {item['produto']}: R$ {preco_ant} → R$ {preco_atu} "
                f"(−{pct}%){' · menor preço' if item.get('novo_minimo') else ''} — {item['url']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def do_deals(conn, fmt="json", mark=False):
    result = build_deals(conn)
    if mark:
        mark_deals_reported(conn, result)
    for sec in result["sections"]:
        for it in sec["items"]:
            it.pop("_obs_id", None)
    if fmt == "text":
        print(format_deals_text(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def do_history(conn, item_id):
    item = conn.execute("SELECT title, source, tag FROM items WHERE id=?", (item_id,)).fetchone()
    if not item:
        print(f"Item {item_id} não encontrado.")
        return
    title, src, tag = item
    print(f"{title} ({src}/{tag})\n")
    rows = conn.execute(
        "SELECT price, original_price, promo_tag, recorded_at FROM price_history WHERE item_id=? ORDER BY recorded_at",
        (item_id,),
    ).fetchall()
    if not rows:
        print("Sem histórico de preços.")
        return
    for price, orig, promo, ts in rows:
        orig_str = f" (de R$ {orig:.2f})" if orig else ""
        promo_str = f" [{promo}]" if promo else ""
        print(f"  {ts}: R$ {price:.2f}{orig_str}{promo_str}")


def do_summary(conn):
    # A product can have observations in multiple source/list pairs. The
    # monitor's active inventory is the canonical observations, not the
    # distinct product catalog or the legacy `items` compatibility table.
    # Produtos descontinuados saem de toda contagem principal; ficam só na
    # linha própria e em `list --discontinued`.
    vivos = f"observacoes_loja o JOIN produtos p ON p.id=o.produto_id WHERE o.active=1 AND {ESTADO_VIVO_SQL}"
    total = conn.execute(f"SELECT COUNT(*) FROM {vivos}").fetchone()[0]
    by_source = conn.execute(
        f"SELECT o.source, o.lista, COUNT(*) FROM {vivos} GROUP BY o.source, o.lista ORDER BY o.source, o.lista"
    ).fetchall()
    deals = conn.execute(
        f"SELECT COUNT(*) FROM {vivos} AND (o.promocao IS NOT NULL OR o.preco_original IS NOT NULL)"
    ).fetchone()[0]
    discontinued = conn.execute(f"""
        SELECT COUNT(*) FROM observacoes_loja o JOIN produtos p ON p.id=o.produto_id
         WHERE o.active=1 AND {ESTADO_MORTO_SQL}
    """).fetchone()[0]
    last_sync = conn.execute(
        "SELECT MAX(last_seen) FROM observacoes_loja"
    ).fetchone()[0]

    print(f"Total de itens ativos: {total}")
    print(f"Último sync: {last_sync or 'nunca'}")
    print(f"Itens com oferta/promo: {deals}")
    print(f"Itens descontinuados: {discontinued}\n")
    for src, tag, count in by_source:
        print(f"  {src}/{tag}: {count}")


def main():
    parser = argparse.ArgumentParser(description="compras-monitor")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("sync", help="Sincronizar itens de ML e Amazon")

    p_list = sub.add_parser("list", help="Listar itens")
    p_list.add_argument("--source", choices=["ml", "amazon"])
    p_list.add_argument("--tag", choices=["carrinho", "favoritos", "wishlist", "salvo"])
    p_list.add_argument("--discontinued", action="store_true",
                        help="Listar apenas os itens descontinuados")

    p_deals = sub.add_parser("deals", help="Ver ofertas e promos")
    p_deals.add_argument("--mark", action="store_true",
                         help="grava os preços reportados (não repete na próxima execução); usar só no cron")
    p_deals.add_argument("--format", choices=["json", "text"], default="json",
                         help="text = bloco já pronto pra chat, com link por item (ver format_deals_text)")

    p_hist = sub.add_parser("history", help="Histórico de preço")
    p_hist.add_argument("item_id", type=int)

    sub.add_parser("summary", help="Resumo geral")

    p_repair = sub.add_parser("repair-bad-wishlist", help="Reparar execução degradada da wishlist a partir de backup")
    p_repair.add_argument("--backup", required=True, help="SQLite anterior à execução ruim")
    p_repair.add_argument("--bad-since", default="2026-09-13T11:50:00-03:00")

    p_dedupe = sub.add_parser("dedupe-produtos", help="Fundir produtos duplicados pelo mesmo nome")
    p_dedupe.add_argument("--apply", action="store_true", help="Aplicar (sem isso, só lista)")

    p_mig = sub.add_parser("migrate-json", help="Importar o registro legado compras.json")
    p_mig.add_argument("--path", default=COMPRAS_JSON)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    conn = init_db()

    if args.cmd == "sync":
        result = do_sync(conn)
        # Output JSON for automation parsing
        print(f"\n__RESULT__:{json.dumps(result)}")
        if result["errors"]:
            sys.exit(1)
    elif args.cmd == "list":
        do_list(conn, args.source, args.tag, args.discontinued)
    elif args.cmd == "deals":
        do_deals(conn, args.format, args.mark)
    elif args.cmd == "history":
        do_history(conn, args.item_id)
    elif args.cmd == "summary":
        do_summary(conn)
    elif args.cmd == "repair-bad-wishlist":
        print(json.dumps(repair_bad_wishlist_run(conn, args.backup, args.bad_since), ensure_ascii=False))
    elif args.cmd == "dedupe-produtos":
        print(json.dumps(dedupe_produtos(conn, args.apply), ensure_ascii=False))
    elif args.cmd == "migrate-json":
        do_migrate_json(conn, args.path)

    conn.close()


if __name__ == "__main__":
    main()
