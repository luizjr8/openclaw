#!/usr/bin/env python3
"""
Busca preços dos modelos no OpenRouter e salva em results/model_prices.json
Uso: python bench/fetch_prices.py
"""

import json
import urllib.request
from pathlib import Path

API_URL = "https://openrouter.ai/api/v1/models"
OUTPUT  = Path(__file__).parent / "results" / "model_prices.json"


def fetch() -> None:
    print(f"Buscando preços em {API_URL}…")
    req = urllib.request.Request(API_URL, headers={"User-Agent": "lzbot-bench/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())

    prices: dict[str, dict] = {}
    for model in data.get("data", []):
        mid = model.get("id", "")
        p   = model.get("pricing", {})
        if not p:
            continue
        # Preços por token em USD (strings no JSON do OpenRouter)
        prices[mid] = {
            "prompt":      float(p.get("prompt",           0) or 0),
            "completion":  float(p.get("completion",       0) or 0),
            "cache_read":  float(p.get("input_cache_read", 0) or 0),
        }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(prices, f, indent=2, ensure_ascii=False)

    print(f"✓ {len(prices)} modelos salvos em {OUTPUT}")


if __name__ == "__main__":
    fetch()
