---
name: instagram-reader
description: Extrai conteúdo de posts do Instagram (legendas, comentários, imagens e links) via Playwright, sem login. Aceita URL ou shortcode. Usa o alt text das imagens e acessa links de conteúdo do post para enriquecer o resumo.
homepage: https://playwright.dev
metadata:
  openclaw:
    emoji: 📷
    requires:
      bins: [python3, playwright]
---

# instagram-reader

Extrai legenda, comentários, imagens e transcrição de áudio de posts públicos do Instagram via Playwright (headless).
Para Reels, baixa o vídeo com yt-dlp, extrai áudio com ffmpeg e transcreve com faster-whisper.

## Uso

Não há CLI instalada — o script roda direto com `python3` do sistema (as dependências ficam instaladas globalmente via `pip install --break-system-packages`, sem venv).

```bash
python3 /app/skills/instagram-reader/scripts/instagram_reader.py "<url-ou-shortcode>"
python3 /app/skills/instagram-reader/scripts/instagram_reader.py ABC123 --images 10
python3 /app/skills/instagram-reader/scripts/instagram_reader.py ABC123 --no-images
python3 /app/skills/instagram-reader/scripts/instagram_reader.py ABC123 --json
```

Padrão: baixa até 5 imagens. Para Reels, transcrição automática com faster-whisper.

## Saída

- Autor, likes, total de comentários
- Legenda completa
- Imagens baixadas com **alt text** do HTML (descrição gerada pelo Instagram)
- Links de conteúdo do post acessados (título + texto principal, até 3)
- Primeiros comentários visíveis (até 10)
- **Transcrição de vídeo** (Reels): transcrição do áudio via faster-whisper + duração

Com `--json`: objeto completo com todos os campos, incluindo `links[]`, `alt` por imagem, e `transcript`.

## Dependências para transcrição de vídeo

- `yt-dlp` — download do vídeo
- `ffmpeg` — extração de áudio
- `faster-whisper` — transcrição local (modelo `base`, idioma `pt`)

Instalar:

```bash
pip install yt-dlp
sudo apt install ffmpeg
pip install faster-whisper
```

## Fluxo ao receber um post (para o operador do bot)

Ao processar um post do Instagram, o bot deve:

1. Executar o script e obter os dados (incluindo paths das imagens baixadas, alt e ocr)
2. **Enviar cada imagem baixada como mídia no chat**, usando o alt text ou OCR como legenda/caption da imagem (até 1024 chars)
3. Se o alt estiver vazio e não houver OCR, enviar a imagem sem legenda
4. Após enviar todas as imagens, enviar o resumo textual completo (legenda + comentários + análise)

## Filtragem de links

Links de ads/produtos são ignorados automaticamente. São considerados links de conteúdo tudo que **não** contenha:

- Domínios circulares: `instagram.com`, `facebook.com`, `whatsapp.com`
- Padrões de loja: `/shop/`, `/store/`, `/buy/`, `/produto/`, `/checkout/`, `/cart/`
- Parâmetros de rastreamento: `utm_medium=paid`, `fbclid`

## Limitações

- Apenas posts públicos
- Somente comentários visíveis no load inicial
- Sujeito a rate limiting do Instagram
- OCR pode ser lento na primeira execução (download de modelos)
- Transcrição de Reels requer yt-dlp, ffmpeg e faster-whisper instalados
- Downloads em grupo podem ser restritos pelo Instagram

## Script

[scripts/](scripts/)
