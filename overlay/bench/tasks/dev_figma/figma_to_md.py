#!/usr/bin/env python3
"""
Extrai conteúdo de um frame do Figma via API e gera um design.md
com tokens de design (cores, tipografia, textos, layout) prontos
para uso em prompts de benchmark.

Uso:
  python figma_to_md.py --file FILE_ID --node NODE_ID --out design.md
  python figma_to_md.py  # usa defaults do config.yml (figma[0])
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests
import yaml
from dotenv import dotenv_values

BENCH_DIR = Path(__file__).parent.parent
ENV_FILE = BENCH_DIR.parent / ".env"
CONFIG_FILE = BENCH_DIR / "config.yml"


## Helpers de cor e dimensão

def rgba_to_hex(color: dict, opacity: float = 1.0) -> str:
    r = round(color.get("r", 0) * 255)
    g = round(color.get("g", 0) * 255)
    b = round(color.get("b", 0) * 255)
    a = color.get("a", 1.0) * opacity
    if a < 1.0:
        return f"rgba({r},{g},{b},{a:.2f})"
    return f"#{r:02X}{g:02X}{b:02X}"


def node_size(node: dict) -> str:
    bb = node.get("absoluteBoundingBox") or node.get("size") or {}
    w = round(bb.get("width", 0))
    h = round(bb.get("height", 0))
    return f"{w}×{h}px"


def font_style(style: dict) -> str:
    parts = []
    if style.get("fontFamily"):
        parts.append(style["fontFamily"])
    if style.get("fontWeight"):
        parts.append(f"weight {style['fontWeight']}")
    if style.get("fontSize"):
        parts.append(f"{style['fontSize']}px")
    if style.get("lineHeightPx"):
        lh = round(style["lineHeightPx"], 1)
        parts.append(f"line-height {lh}px")
    if style.get("letterSpacing") and style["letterSpacing"] != 0:
        parts.append(f"tracking {style['letterSpacing']}px")
    return ", ".join(parts)


## Extração recursiva de nós

def collect_fills(node: dict, fills: list[str]):
    """Coleta cores de fill únicas."""
    for fill in node.get("fills", []):
        if fill.get("visible") is False:
            continue
        color = fill.get("color")
        if color:
            hex_color = rgba_to_hex(color, fill.get("opacity", 1.0))
            if hex_color not in fills:
                fills.append(hex_color)


def collect_strokes(node: dict, strokes: list[str]):
    for stroke in node.get("strokes", []):
        if stroke.get("visible") is False:
            continue
        color = stroke.get("color")
        if color:
            hex_color = rgba_to_hex(color)
            if hex_color not in strokes:
                strokes.append(hex_color)


def collect_text_nodes(node: dict, texts: list[dict]):
    if node.get("type") == "TEXT":
        style = node.get("style", {})
        texts.append({
            "content": node.get("characters", "").strip(),
            "font": font_style(style),
            "fills": [rgba_to_hex(f["color"]) for f in node.get("fills", []) if f.get("color")],
        })
    for child in node.get("children", []):
        collect_text_nodes(child, texts)


def collect_images(node: dict, images: list[str]):
    for fill in node.get("fills", []):
        if fill.get("type") == "IMAGE" and fill.get("imageRef"):
            ref = fill["imageRef"]
            if ref not in images:
                images.append(ref)
    for child in node.get("children", []):
        collect_images(child, images)


def describe_layout(node: dict, depth: int = 0) -> list[str]:
    """Gera descrição hierárquica de layout."""
    lines = []
    indent = "  " * depth
    name = node.get("name", "")
    ntype = node.get("type", "")
    size = node_size(node)

    # Pula nós invisíveis
    if node.get("visible") is False:
        return lines

    # Descrição do nó atual
    fills_raw = [f for f in node.get("fills", []) if f.get("color") and f.get("visible") is not False]
    fill_str = ""
    if fills_raw:
        fill_str = f" | fill: {rgba_to_hex(fills_raw[0]['color'])}"

    layout_mode = node.get("layoutMode", "")
    layout_str = f" | layout: {layout_mode}" if layout_mode else ""

    if ntype == "TEXT":
        chars = node.get("characters", "").strip()[:60]
        style = node.get("style", {})
        lines.append(f'{indent}[TEXT] "{chars}" — {font_style(style)}{fill_str}')
    elif ntype in ("FRAME", "COMPONENT", "INSTANCE", "GROUP", "SECTION"):
        lines.append(f"{indent}[{ntype}] {name} — {size}{fill_str}{layout_str}")
        for child in node.get("children", []):
            lines.extend(describe_layout(child, depth + 1))
    elif ntype == "RECTANGLE":
        corner = node.get("cornerRadius", 0)
        corner_str = f" | radius: {corner}px" if corner else ""
        lines.append(f"{indent}[RECT] {name} — {size}{fill_str}{corner_str}")
    elif ntype in ("VECTOR", "ELLIPSE", "POLYGON", "STAR"):
        lines.append(f"{indent}[{ntype}] {name} — {size}{fill_str}")

    return lines


## Fetch via Figma API

def fetch_node(file_id: str, node_id: str, token: str) -> dict:
    node_id_enc = node_id.replace("-", ":")
    url = f"https://api.figma.com/v1/files/{file_id}/nodes?ids={node_id_enc}&depth=10"
    resp = requests.get(url, headers={"X-Figma-Token": token}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    nodes = data.get("nodes", {})
    # A API retorna a chave com ":" mesmo se enviamos "-"
    key = node_id_enc if node_id_enc in nodes else list(nodes.keys())[0]
    return nodes[key]["document"]


## Geração do markdown

def generate_md(node: dict, figma_url: str) -> str:
    fills: list[str] = []
    strokes: list[str] = []
    texts: list[dict] = []
    images: list[str] = []

    # Coleta recursiva de fills no nó raiz e filhos diretos (paleta global)
    def walk_fills(n: dict):
        collect_fills(n, fills)
        collect_strokes(n, strokes)
        for child in n.get("children", []):
            walk_fills(child)

    walk_fills(node)
    collect_text_nodes(node, texts)
    collect_images(node, images)
    layout_lines = describe_layout(node)

    # Dedup textos longos para evitar repetição
    seen_texts: set[str] = set()
    unique_texts = []
    for t in texts:
        key = t["content"][:80]
        if key and key not in seen_texts:
            seen_texts.add(key)
            unique_texts.append(t)

    frame_size = node_size(node)
    frame_name = node.get("name", "Frame")

    lines = [
        f"# Design Spec — {frame_name}",
        f"\nFigma: {figma_url}",
        f"Dimensões do frame: {frame_size}",
        "",
        "## Paleta de Cores",
    ]

    if fills:
        for c in fills[:20]:
            lines.append(f"- `{c}`")
    else:
        lines.append("- (sem fills detectados)")

    if strokes:
        lines.append("\n### Strokes / Bordas")
        for c in strokes[:10]:
            lines.append(f"- `{c}`")

    lines.append("\n## Textos e Tipografia")
    if unique_texts:
        for t in unique_texts[:40]:
            content = t["content"].replace("\n", " ")
            lines.append(f'\n**"{content}"**')
            if t["font"]:
                lines.append(f'  - Fonte: {t["font"]}')
            if t["fills"]:
                lines.append(f'  - Cor: `{t["fills"][0]}`')
    else:
        lines.append("(sem textos detectados)")

    lines.append("\n## Hierarquia de Layout")
    if layout_lines:
        lines.extend(layout_lines[:120])
    else:
        lines.append("(sem layout detectado)")

    if images:
        lines.append(f"\n## Imagens")
        lines.append(f"O frame contém {len(images)} imagem(ns) embutida(s) (background/photos).")
        lines.append("Use placeholders ou gradientes similares para representá-las.")

    lines.append("\n## Instruções para Reprodução")
    lines.append(
        "Reproduza esta página como HTML standalone (CSS inline em `<style>`), "
        "sendo o mais fiel possível às cores, tipografia, espaçamentos e layout descritos acima. "
        "Não é necessário acessar o Figma — use apenas as informações desta spec."
    )

    return "\n".join(lines)


## Main

def main():
    parser = argparse.ArgumentParser(description="Gera design.md a partir de um frame do Figma")
    parser.add_argument("--file", help="Figma file ID")
    parser.add_argument("--node", help="Node ID (ex: 237-4930)")
    parser.add_argument("--out", help="Arquivo de saída (default: bench/tasks/design_<node>.md)")
    args = parser.parse_args()

    env = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    token = env.get("FIGMA_ACCESS_TOKEN") or os.environ.get("FIGMA_ACCESS_TOKEN")
    if not token:
        print("ERRO: FIGMA_ACCESS_TOKEN não encontrado em .env", file=sys.stderr)
        sys.exit(1)

    # Defaults do config.yml
    file_id = args.file
    node_id = args.node
    figma_url = ""

    if not file_id or not node_id:
        with open(CONFIG_FILE) as f:
            cfg = yaml.safe_load(f)
        figma_cfg = cfg.get("figma", [{}])[0]
        figma_url = figma_cfg.get("url", "")
        if not file_id:
            # Extrai file ID da URL
            m = re.search(r"/design/([^/?]+)", figma_url)
            file_id = m.group(1) if m else figma_cfg.get("file_id", "")
        if not node_id:
            # Extrai node-id da URL
            m = re.search(r"node-id=([^&]+)", figma_url)
            node_id = m.group(1).replace("%3A", "-").replace(":", "-") if m else ""

    if not file_id or not node_id:
        print("ERRO: --file e --node são obrigatórios (ou configure figma no config.yml)", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out) if args.out else BENCH_DIR / "tasks" / f"design_{node_id.replace(':', '-')}.md"

    print(f"Buscando frame {node_id} do arquivo {file_id}...")
    node = fetch_node(file_id, node_id, token)
    print(f"Frame '{node.get('name')}' ({node_size(node)}) obtido.")

    md = generate_md(node, figma_url or f"https://www.figma.com/design/{file_id}")
    out_path.write_text(md, encoding="utf-8")
    print(f"Salvo em: {out_path}")
    print(f"  {len(md.splitlines())} linhas | {len(md):,} chars")


if __name__ == "__main__":
    main()
