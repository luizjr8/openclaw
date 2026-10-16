---
name: pdf-ocr
description: Extração de texto de PDFs usando pipeline local (PyMuPDF para PDFs com texto nativo + Tesseract OCR para PDFs escaneados/imagem). Use quando o usuário pedir para extrair texto de PDF, OCR de documento escaneado, ou ler conteúdo de PDF localmente.
---

# PDF OCR

Extrai texto de PDFs localmente com fallback automático para OCR.

## Script

`scripts/pdf-ocr.py` — CLI que aceita path do PDF e opções.

```bash
# Extração automática (tenta PyMuPDF, fallback pra OCR se for escaneado)
pdf-ocr /caminho/documento.pdf

# Páginas específicas
pdf-ocr relatorio.pdf --pages 3-5

# Forçar OCR (ignorar texto nativo)
pdf-ocr scan.pdf --force-ocr

# Salvar em arquivo
pdf-ocr documento.pdf --output /tmp/texto.txt

# Idioma diferente (padrão: por)
pdf-ocr documento.pdf --lang eng
```

## Comportamento

1. Tenta extrair texto nativo via PyMuPDF
2. Se média < 20 chars/página, assume PDF escaneado e roda Tesseract OCR
3. OCR usa 300 DPI, idioma `por` (português) por padrão
4. `--force-ocr` ignora a heurística e vai direto pro OCR

## Instalação

Já instalado: PyMuPDF, pytesseract, pillow, tesseract-ocr + pacote `por`.
