#!/usr/bin/env python3
"""
Script para acessar a liturgia diária católica.
Fontes: Acervo Católico (leituras) + Vatican News (palavras dos papas).
"""

import requests
from bs4 import BeautifulSoup
import datetime
import json
import sys


def fetch_acervo_catolico(date_str):
    """
    Busca leituras do Acervo Católico.

    Args:
        date_str: Data no formato YYYY-MM-DD

    Returns:
        Dicionário com as leituras
    """
    url = f"https://acervocatolico.com/liturgia/{date_str}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        sections = {}

        # Mapear IDs das seções no HTML
        section_ids = {
            "primeira_leitura": "lit-primeira",
            "salmo": "lit-salmo",
            "segunda_leitura": "lit-segunda",
            "evangelho": "lit-evangelho"
        }

        for key, section_id in section_ids.items():
            section = soup.find('section', id=section_id)
            if not section:
                continue

            # Pegar referência (título h3 ou similar)
            ref = section.find(['h3', 'h4'])
            ref_text = ref.get_text(strip=True) if ref else ""

            # Pegar conteúdo em .e-content
            econtent = section.find('div', class_='e-content')
            if econtent:
                paragraphs = []
                # Pegar todos os elementos de texto relevantes
                for elem in econtent.find_all(['p', 'blockquote', 'div', 'span'], recursive=True):
                    text = elem.get_text(strip=True)
                    # Ignorar textos muito curtos que provavelmente são só espaçadores
                    if text and len(text) > 3:
                        paragraphs.append(text)
                # Remover duplicatas mantendo ordem
                seen = set()
                unique_paragraphs = []
                for p in paragraphs:
                    if p not in seen:
                        seen.add(p)
                        unique_paragraphs.append(p)
                content = '\n\n'.join(unique_paragraphs)
            else:
                content = section.get_text(separator='\n', strip=True)

            sections[key] = {
                "referencia": ref_text,
                "texto": content
            }

        # Pegar título/dia litúrgico
        titulo = soup.find('h1')
        dia_liturgico = titulo.get_text(strip=True) if titulo else ""

        return {"dia_liturgico": dia_liturgico, "leituras": sections}

    except Exception as e:
        return {"error": f"Erro ao acessar Acervo Católico: {str(e)}"}


def fetch_vatican_news(date_obj):
    """
    Busca palavras dos papas do Vatican News.

    Args:
        date_obj: datetime object

    Returns:
        String com o conteúdo ou None
    """
    # Formato AAAA/MM/DD
    url_date = date_obj.strftime("%Y/%m/%d")
    url = f"https://www.vaticannews.va/pt/palavra-do-dia/{url_date}.html"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Buscar section "As palavras dos Papas"
        palavras_section = None
        for sec in soup.find_all('section', class_='section--isStatic'):
            h2 = sec.find('h2')
            if h2 and 'papas' in h2.get_text(strip=True).lower():
                palavras_section = sec
                break

        if not palavras_section:
            return None

        content_div = palavras_section.find('div', class_='section__content')
        if not content_div:
            return None

        # Extrair parágrafos com conteúdo relevante
        paragraphs = []
        for p in content_div.find_all('p'):
            text = p.get_text(strip=True)
            if text and len(text) > 10:
                paragraphs.append(text)

        return '\n\n'.join(paragraphs) if paragraphs else None

    except Exception:
        return None


def format_leituras(data_str, dia_liturgico, leituras):
    """
    Formata as leituras com bold markdown.
    """
    data_br = datetime.datetime.strptime(data_str, "%Y-%m-%d").strftime("%d/%m/%Y")

    output = f"📖 LITURGIA DIÁRIA — {data_br}\n"
    if dia_liturgico:
        output += f"{dia_liturgico}\n"
    output += "\n"

    # Primeira Leitura
    if "primeira_leitura" in leituras:
        pl = leituras["primeira_leitura"]
        ref = pl.get("referencia", "")
        texto = pl.get("texto", "")
        output += f"**PRIMEIRA LEITURA** — {ref}\n"
        output += f"{texto}\n\n"

    # Salmo
    if "salmo" in leituras:
        sl = leituras["salmo"]
        ref = sl.get("referencia", "")
        texto = sl.get("texto", "")
        output += f"**SALMO RESPONSORIAL** — {ref}\n"
        output += f"{texto}\n\n"

    # Segunda Leitura
    if "segunda_leitura" in leituras:
        sl = leituras["segunda_leitura"]
        ref = sl.get("referencia", "")
        texto = sl.get("texto", "")
        output += f"**SEGUNDA LEITURA** — {ref}\n"
        output += f"{texto}\n\n"

    # Evangelho
    if "evangelho" in leituras:
        ev = leituras["evangelho"]
        ref = ev.get("referencia", "")
        texto = ev.get("texto", "")
        output += f"**EVANGELHO** — {ref}\n"
        output += f"{texto}\n"

    return output


def format_palavras_papas(conteudo):
    """
    Formata palavras dos papas.
    """
    if not conteudo:
        return None

    return f"🗣️ **PALAVRAS DOS PAPAS**\n\n{conteudo}"


def fetch_liturgia(date_str=None):
    """
    Busca liturgia completa (leituras + palavras dos papas).

    Args:
        date_str: Data no formato YYYY-MM-DD (opcional, usa hoje)

    Returns:
        Dicionário com leituras e palavras dos papas
    """
    if date_str:
        try:
            date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return {"error": f"Data inválida: {date_str}. Use formato YYYY-MM-DD"}
    else:
        date_obj = datetime.datetime.now()

    date_str = date_obj.strftime("%Y-%m-%d")

    # Buscar leituras do Acervo Católico
    acervo_data = fetch_acervo_catolico(date_str)

    if "error" in acervo_data:
        return acervo_data

    # Buscar palavras dos papas do Vatican News
    palavras_papas = fetch_vatican_news(date_obj)

    return {
        "data": date_str,
        "leituras": acervo_data,
        "palavras_papas": palavras_papas
    }


def main():
    """Função principal do script."""
    import argparse

    parser = argparse.ArgumentParser(description="Acessa a liturgia diária católica")
    parser.add_argument("data", nargs="?", help="Data no formato YYYY-MM-DD (padrão: hoje)")
    parser.add_argument("--json", action="store_true", help="Saída em formato JSON")

    args = parser.parse_args()

    data = fetch_liturgia(args.data)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if "error" in data:
        print(f"❌ {data['error']}")
        sys.exit(1)

    # Formatar leituras
    leituras_output = format_leituras(
        data["data"],
        data["leituras"]["dia_liturgico"],
        data["leituras"]["leituras"]
    )

    # Formatar palavras dos papas
    palavras_output = None
    if data["palavras_papas"]:
        palavras_output = format_palavras_papas(data["palavras_papas"])

    # Imprimir leituras
    print(leituras_output)

    # Imprimir separador para indicar segunda mensagem
    print("\n---SEPARADOR_MENSAGEM---\n")

    # Imprimir palavras dos papas se existirem
    if palavras_output:
        print(palavras_output)


if __name__ == "__main__":
    main()
