#!/usr/bin/env python3
"""
Checkout CLI — run a checkout for a given product link.

Usage:
    python checkout.py <url>
    python checkout.py --url <url> [--platform <platform>]

Platform is auto-detected from URL, or can be specified.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add skill root to path
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from data.user_data import load_user_data
from checkouts.kiwify import checkout as kiwify_checkout, format_result


PLATFORM_REGEX = {
    r"pay\.kiwify\.com\.br": "kiwify",
    r"kiwify\.com\.br": "kiwify",
    r"hotmart\.com": "hotmart",
    r"eduzz\.com": "eduzz",
    r"monetizze\.com\.br": "monetizze",
}


def detect_platform(url: str) -> str | None:
    import re
    for pattern, platform in PLATFORM_REGEX.items():
        if re.search(pattern, url):
            return platform
    return None


async def run(url: str, platform: str | None = None, product: str | None = None):
    if not platform:
        platform = detect_platform(url)
    
    if not platform:
        print(f"Não foi possível identificar a plataforma para: {url}")
        sys.exit(1)

    print(f"Plataforma detectada: {platform}")
    print("Carregando dados do usuário...")
    
    try:
        user = load_user_data()
    except FileNotFoundError:
        print("ERRO: USER.md não encontrado")
        sys.exit(1)

    print(f"Usuário: {user['fullname']} ({user['email']})")
    print("Executando checkout...\n")

    if platform == "kiwify":
        result = await kiwify_checkout(url, user)
    else:
        print(f"Plataforma '{platform}' ainda não implementada")
        sys.exit(1)

    if result.pix_qrcode:
        print(format_result(result, product or ""))
    else:
        print("ERRO: Não foi possível gerar o PIX")
        print(f"Status: {result.status}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Executa checkout em plataformas de pagamento")
    parser.add_argument("url", nargs="?", help="URL do produto")
    parser.add_argument("--url", dest="url_alt")
    parser.add_argument("--platform", "-p", help="Forçar plataforma (kiwify, hotmart, etc)")
    parser.add_argument("--product", help="Nome do produto (só cosmético, aparece no resumo)")
    args = parser.parse_args()

    url = args.url or args.url_alt
    if not url:
        parser.print_help()
        sys.exit(1)

    asyncio.run(run(url, args.platform, args.product))


if __name__ == "__main__":
    main()
