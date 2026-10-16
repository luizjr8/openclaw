#!/usr/bin/env python3
"""PDF OCR — extrai texto de PDFs via PyMuPDF + fallback Tesseract.

Usage:
  pdf-ocr <pdf_path> [--pages N[-M]] [--lang LANG] [--force-ocr] [--output FILE]

Examples:
  pdf-ocr documento.pdf
  pdf-ocr documento.pdf --pages 3-5 --lang por
  pdf-ocr scan.pdf --force-ocr
  pdf-ocr scan.pdf --output /tmp/texto.txt
"""

import argparse
import sys
import tempfile
from pathlib import Path

# ── Dependencies ──────────────────────────────────────────────────────────────

# O módulo canônico é `pymupdf`; `fitz` é o nome legado e passou a emitir
# warning ao ser importado a partir da 1.28.2. Mantido como fallback para
# instâncias ainda em versões antigas, onde só `fitz` existe.
try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
    Image = None


# ── Core ──────────────────────────────────────────────────────────────────────

def extract_pymupdf(pdf_path: str, page_spec: str | None = None) -> str:
    """Extract text via PyMuPDF (native text layer)."""
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) not installed — run: pip install PyMuPDF")

    pages = _parse_pages(pdf_path, page_spec)
    parts: list[str] = []

    with fitz.open(pdf_path) as doc:
        for i in pages:
            text = doc[i].get_text().strip()
            if text:
                parts.append(f"─── Página {i + 1} ───\n{text}")

    return "\n\n".join(parts)


def extract_ocr(pdf_path: str, page_spec: str | None = None, lang: str = "por") -> str:
    """Extract text via Tesseract OCR (image-based / scanned PDFs)."""
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract not installed — run: pip install pytesseract pillow"
        )
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) not installed — run: pip install PyMuPDF")

    pages = _parse_pages(pdf_path, page_spec)
    parts: list[str] = []

    with fitz.open(pdf_path) as doc:
        for i in pages:
            page = doc[i]
            # Render page as image (300 DPI for good OCR)
            mat = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            text = pytesseract.image_to_string(img, lang=lang).strip()
            if text:
                parts.append(f"─── Página {i + 1} ───\n{text}")

    return "\n\n".join(parts)


def extract_auto(pdf_path: str, page_spec: str | None = None, lang: str = "por") -> str:
    """Try native text first; fall back to OCR if text is too sparse."""
    text = extract_pymupdf(pdf_path, page_spec)

    # Heuristic: if avg chars per page < 50, it's probably scanned
    pages = _parse_pages(pdf_path, page_spec)
    avg_chars = len(text.strip()) / max(len(pages), 1)

    if avg_chars < 20:
        sys.stderr.write(
            f"[pdf-ocr] Native text sparse ({avg_chars:.0f} chars/page), "
            f"falling back to OCR...\n"
        )
        text = extract_ocr(pdf_path, page_spec, lang)

    return text


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_pages(pdf_path: str, spec: str | None) -> list[int]:
    """Return 0-indexed page numbers from a spec like '3-5' or '1'."""
    if fitz is None:
        raise RuntimeError("PyMuPDF required for page parsing")

    with fitz.open(pdf_path) as doc:
        total = len(doc)

    if spec is None:
        return list(range(total))

    parts = spec.split("-")
    try:
        if len(parts) == 1:
            start = end = int(parts[0])
        elif len(parts) == 2:
            start = int(parts[0]) if parts[0] else 1
            end = int(parts[1]) if parts[1] else total
        else:
            raise ValueError
    except ValueError:
        raise ValueError(
            f"Formato inválido: '{spec}'. Use N ou N-M (1-indexed)"
        )

    if start < 1 or end > total or start > end:
        raise ValueError(
            f"Páginas {start}-{end} inválidas (PDF tem {total} páginas)"
        )

    return list(range(start - 1, end))


def _parse_pages_from_args(args) -> list[int]:
    return _parse_pages(args.pdf, args.pages)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extrai texto de PDFs (nativo + OCR fallback)"
    )
    parser.add_argument("pdf", help="Caminho do arquivo PDF")
    parser.add_argument(
        "--pages", "-p",
        help="Páginas: N ou N-M (1-indexed, ex: 3-5). Padrão: todas"
    )
    parser.add_argument(
        "--lang", "-l",
        default="por",
        help="Idioma Tesseract (ex: por, eng). Padrão: por"
    )
    parser.add_argument(
        "--force-ocr", "-f",
        action="store_true",
        help="Forçar OCR mesmo que PDF tenha texto nativo"
    )
    parser.add_argument(
        "--output", "-o",
        help="Salvar em arquivo em vez de stdout"
    )

    args = parser.parse_args()
    pdf_path = args.pdf

    if not Path(pdf_path).exists():
        sys.stderr.write(f"Erro: arquivo não encontrado: {pdf_path}\n")
        sys.exit(1)

    try:
        if args.force_ocr:
            text = extract_ocr(pdf_path, args.pages, args.lang)
        else:
            text = extract_auto(pdf_path, args.pages, args.lang)
    except Exception as e:
        sys.stderr.write(f"Erro: {e}\n")
        sys.exit(1)

    if not text.strip():
        sys.stderr.write("[pdf-ocr] Nenhum texto extraído.\n")
        sys.exit(0)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        sys.stderr.write(f"[pdf-ocr] Salvo em: {args.output}\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
