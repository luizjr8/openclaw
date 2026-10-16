"""
Kiwify checkout automation.
Generates PIX payment code for a Kiwify product link.
"""

import asyncio
import json
import re
from dataclasses import dataclass, asdict
from playwright.async_api import async_playwright


@dataclass
class CheckoutResult:
    platform: str = "kiwify"
    status: str = ""
    amount: int = 0
    order: str = ""
    pix_qrcode: str = ""
    approved_url: str = ""


def _extract_qrcode(responses: dict) -> str | None:
    """Extract pix_qrcode from the charge API response."""
    for url, data in responses.items():
        if "v2/charge" in url and isinstance(data, dict):
            qr = data.get("pix_qrcode")
            if qr:
                return qr
    return None


def _extract_order(responses: dict) -> str | None:
    for url, data in responses.items():
        if "v2/charge" in url and isinstance(data, dict):
            return data.get("order")
    return None


def _extract_amount(responses: dict) -> int | None:
    for url, data in responses.items():
        if "v2/charge" in url and isinstance(data, dict):
            return data.get("amount")
    return None


async def checkout(link_url: str, user: dict) -> CheckoutResult:
    """
    Execute Kiwify checkout for the given product link.
    
    Args:
        link_url: Full Kiwify checkout URL (e.g., https://pay.kiwify.com.br/XXXXXX)
        user: Dict with 'fullname', 'email', 'cpf' keys
    
    Returns:
        CheckoutResult with PIX QR code and order info
    """
    result = CheckoutResult()
    api_responses = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="pt-BR",
            timezone_id="America/Fortaleza",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        async def capture_response(response):
            url = response.url
            if "checkout-api.kiwify" in url:
                try:
                    body = await response.json()
                    api_responses[url] = body
                except:
                    pass

        page.on("response", capture_response)

        await page.goto(link_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        # Fill form
        await page.fill('input[name="fullname"]', user["fullname"])
        for e in await page.query_selector_all('input[name="email"]'):
            await e.fill(user["email"])
        await page.fill('input[name="document"]', re.sub(r"\D", "", user["cpf"]))
        await page.wait_for_timeout(1000)

        # Select PIX
        pix_link = await page.query_selector('a:has-text("Pix")')
        if pix_link:
            await pix_link.click()
            await page.wait_for_timeout(500)

        # Submit
        pay_btn = await page.query_selector('a[kiwi-data="pay_button"]')
        if pay_btn:
            await pay_btn.click()
            await page.wait_for_timeout(8000)

        # Extract results from API responses
        result.pix_qrcode = _extract_qrcode(api_responses) or ""
        result.order = _extract_order(api_responses) or ""
        result.amount = _extract_amount(api_responses) or 0
        result.status = "waiting_payment"

        await browser.close()

    return result


def format_result(res: CheckoutResult, product_name: str = "") -> str:
    """Format CheckoutResult as a user-friendly message."""
    amount_real = res.amount / 100 if res.amount else 0
    lines = [
        f"**Pedido gerado!** PIX R${amount_real:.2f}",
        f"Produto: {product_name or '—'}",
        f"Pedido: {res.order}",
        f"Status: {res.status}",
        "",
        "**Código PIX (copia e cola):**",
        f"`{res.pix_qrcode}`",
        "",
        "Basta copiar, abrir o app do banco e pagar via PIX.",
    ]
    return "\n".join(lines)
