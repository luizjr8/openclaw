#!/usr/bin/env python3
"""
Instagram Reader - Extrai conteúdo de posts do Instagram (legendas, comentários, imagens, transcrição de vídeos)
Usa Playwright para renderizar a página e extrair dados estruturados.
Para Reels: baixa o vídeo com yt-dlp, extrai áudio com ffmpeg e transcreve com faster-whisper.
"""

import asyncio
import json
import sys
import os
import re
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

import requests
import pytesseract
from PIL import Image
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


INSTAGRAM_URL_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv)/([a-zA-Z0-9_-]+)'
)

## Extração e filtragem de links

URL_PATTERN = re.compile(r'https?://[^\s\)\]\>\"\']+')

# Domínios/padrões que indicam link de ad ou irrelevante
_AD_SKIP = re.compile(
    r'instagram\.com|facebook\.com|whatsapp\.com|mailto:|tel:'
    r'|/shop/|/store/|/buy/|/produto/|/checkout/|/cart/'
    r'|utm_medium=paid|fbclid=',
    re.IGNORECASE
)


def is_ad_link(url: str) -> bool:
    """Verifica se a URL parece ser link de ad/produto."""
    return bool(_AD_SKIP.search(url))


def fetch_link_content(url: str) -> Optional[Dict[str, Any]]:
    """Busca e extrai título + texto principal de uma URL de conteúdo."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        resp.raise_for_status()

        if 'text/html' not in resp.headers.get('content-type', ''):
            return None

        # Checar URL final (após redirects) também
        if is_ad_link(resp.url):
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Extrair título
        og_title = soup.find('meta', property='og:title')
        title = og_title['content'] if og_title and og_title.get('content') else ''
        if not title:
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else ''

        # Extrair conteúdo do article/main, senão og:description
        content = ''
        container = soup.find('article') or soup.find('main')
        if container:
            paragraphs = [p.get_text(strip=True) for p in container.find_all('p') if len(p.get_text(strip=True)) > 30]
            content = '\n'.join(paragraphs[:12])

        if not content:
            og_desc = soup.find('meta', property='og:description')
            content = og_desc['content'] if og_desc and og_desc.get('content') else ''

        if not title and not content:
            return None

        return {'url': resp.url, 'title': title[:200], 'content': content[:1500]}

    except Exception as e:
        return {'url': url, 'error': str(e)}


def extract_post_id(url_or_id: str) -> str:
    """Extrai o ID do post de uma URL ou retorna o próprio ID."""
    match = INSTAGRAM_URL_PATTERN.search(url_or_id)
    if match:
        return match.group(1)
    return url_or_id


def make_url(post_id: str) -> str:
    return f"https://www.instagram.com/p/{post_id}/"


def is_reel_url(url_or_id: str) -> bool:
    """Verifica se a URL é de um Reel."""
    return '/reel/' in url_or_id or url_or_id.startswith('reel')


def transcribe_video(post_id: str, output_dir: str) -> Dict[str, Any]:
    """
    Baixa o vídeo do Instagram Reel e transcreve o áudio.
    Retorna dict com caminhos e transcrição.
    """
    result = {
        "video_path": None,
        "audio_path": None,
        "transcript_srt": None,
        "transcript_text": None,
        "duration_secs": None,
        "error": None
    }

    tmpdir = os.path.join(output_dir, post_id)
    os.makedirs(tmpdir, exist_ok=True)

    reel_url = f"https://www.instagram.com/reel/{post_id}/"
    video_out = os.path.join(tmpdir, f"{post_id}.mp4")
    audio_out = os.path.join(tmpdir, f"{post_id}_audio.wav")
    srt_out = os.path.join(tmpdir, f"{post_id}.srt")
    txt_out = os.path.join(tmpdir, f"{post_id}_transcript.txt")

    try:
        # 1. Baixar vídeo com yt-dlp
        subprocess.run([
            "yt-dlp",
            "--no-embed-metadata",
            "--no-write-info-json",
            "-o", video_out,
            reel_url
        ], check=True, capture_output=True, text=True, timeout=120)

        if not os.path.exists(video_out):
            # yt-dlp pode adicionar extensão diferente
            for f in os.listdir(tmpdir):
                if f.endswith('.mp4') and post_id in f:
                    video_out = os.path.join(tmpdir, f)
                    break

        result["video_path"] = video_out

        # 2. Extrair áudio com ffmpeg
        subprocess.run([
            "ffmpeg", "-i", video_out,
            "-vn", "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le",
            "-y", audio_out
        ], check=True, capture_output=True, text=True, timeout=60)

        result["audio_path"] = audio_out

        # 3. Transcrever com faster-whisper
        whisper_result = subprocess.run([
            "faster-whisper", audio_out,
            "--model", "base",
            "--language", "pt"
        ], capture_output=True, text=True, timeout=300)

        # faster-whisper CLI salva SRT ao lado do áudio
        expected_srt = audio_out.replace('.wav', '.srt')
        if os.path.exists(expected_srt):
            os.rename(expected_srt, srt_out)
            with open(srt_out, 'r') as f:
                srt_content = f.read()
            result["transcript_srt"] = srt_out

            # Converter SRT para texto limpo
            text_lines = []
            for line in srt_content.split('\n'):
                line = line.strip()
                if not line or re.match(r'^\d+$', line) or '-->' in line:
                    continue
                text_lines.append(line)
            clean_text = ' '.join(text_lines)
            result["transcript_text"] = clean_text

            with open(txt_out, 'w') as f:
                f.write(clean_text)

        # Estimar duração do SRT (último timestamp)
        if result["transcript_srt"]:
            import re
            timestamps = re.findall(r'(\d+\.\d+) -->', srt_content)
            if timestamps:
                result["duration_secs"] = float(timestamps[-1])

    except subprocess.CalledProcessError as e:
        result["error"] = f"Erro: {e.stderr[:500] if e.stderr else str(e)}"
    except Exception as e:
        result["error"] = str(e)

    return result


async def scrape_post(url: str, output_dir: str = "/tmp/ig_reader",
                      download_images: bool = True,
                      image_limit: int = 5) -> Dict[str, Any]:
    """
    Scrape um post do Instagram e retorna dados estruturados.

    Args:
        url: URL completa do post
        output_dir: Diretório para salvar screenshots/imagens
        download_images: Se deve baixar as imagens do post
        image_limit: Número máximo de imagens do post para baixar

    Returns:
        Dicionário com dados do post
    """
    os.makedirs(output_dir, exist_ok=True)

    result: Dict[str, Any] = {
        "url": url,
        "post_id": extract_post_id(url),
        "author": None,
        "likes": None,
        "comments_count": None,
        "caption": None,
        "date": None,
        "top_comments": [],
        "images": [],
        "screenshots": [],
        "links": [],
        "error": None
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        ctx = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/125.0.0.0 Safari/537.36'
            )
        )
        page = await ctx.new_page()

        try:
            await page.goto(url, timeout=30000, wait_until='networkidle')
            await page.wait_for_timeout(3000)

            # Screenshot da página
            ss_path = os.path.join(output_dir, f"{result['post_id']}_screenshot.png")
            await page.screenshot(path=ss_path, full_page=False)
            result['screenshots'].append(ss_path)

            # Extrair meta tags
            meta = {}
            for m in await page.query_selector_all('meta[property], meta[name]'):
                prop = await m.get_attribute('property') or await m.get_attribute('name') or ''
                content = await m.get_attribute('content') or ''
                if prop:
                    meta[prop] = content

            result['meta'] = meta

            # Extrair dados do body text
            body = await page.query_selector('body')
            if not body:
                raise Exception("Could not find body element")

            body_text = (await body.inner_text())

            # Parse do body text para extrair informações estruturadas
            lines = [l.strip() for l in body_text.split('\n') if l.strip()]

            # O Instagram renderiza o post no body mesmo sem login
            # O formato típico é:
            # [username]\n•\nFollow\n[caption]\n[likes]\n[comments]

            # Encontrar o nome de usuário (primeiro nome antes de • Follow)
            author_idx = None
            for i, line in enumerate(lines):
                if line == '•' and i > 0:
                    author = lines[i - 1]
                    # Pular "Never miss a post from..." que aparece no topo
                    if author and author not in ['Log In', 'Sign Up', 'Follow']:
                        result['author'] = author
                        author_idx = i
                        break

            # Extrair likes e comentários da meta description (fonte mais confiável)
            for desc_key in ['description', 'og:description', 'twitter:description']:
                val = meta.get(desc_key, '')
                if not isinstance(val, str) or not val:
                    continue
                # "602 likes"
                like_m = re.search(r'([\d,.KMB]+)\s+like', val, re.IGNORECASE)
                if like_m:
                    result['likes'] = like_m.group(1).replace(',', '')
                cmt_m = re.search(r'([\d,]+)\s+comment', val, re.IGNORECASE)
                if cmt_m and not result.get('comments_count'):
                    result['comments_count'] = cmt_m.group(1) + ' comments'
                if result.get('likes'):
                    break

            # Extrair caption - texto principal do post
            # No Instagram logado, o formato é:
            # ... [username] [relative_date] [caption_text] ...

            # Usar as meta tags OG como fallback principal (mais confiável)
            og_desc = meta.get('og:description', '')
            if og_desc:
                # Extrair apenas o texto da legenda (remover "username on Instagram: \"...\"")
                desc_clean = re.sub(r'^[^:]+:\s*"(.*)"\s*$', r'\1', og_desc)
                desc_clean = re.sub(r'^[^:]+:\s*', '', desc_clean)
                desc_clean = desc_clean.strip('" \n')
                if len(desc_clean) > 10:
                    result['caption'] = desc_clean

            # Fallback: extrair do body text
            if not result.get('caption'):
                # Procurar entre "Follow" e "Log in to like or comment"
                for i, line in enumerate(lines):
                    if line == 'Follow' and i + 3 < len(lines):
                        # Pular "Follow" e pegar linhas seguintes que não são username/data
                        caption_parts = []
                        for j in range(i + 1, min(i + 30, len(lines))):
                            if 'log in to like' in lines[j].lower() or \
                               lines[j] == 'Follow' or \
                               re.match(r'^\d+[wd]$', lines[j]) or \
                               lines[j].startswith('More posts'):
                                break
                            caption_parts.append(lines[j])
                        if caption_parts:
                            result['caption'] = '\n'.join(caption_parts)
                            break

            ## Extração de links do post

            # Buscar URLs na legenda que não sejam ads, acessar para enriquecer resumo
            caption_text = result.get('caption') or ''
            raw_links = [lnk.rstrip('.,;:!?)') for lnk in URL_PATTERN.findall(caption_text)]
            content_links = list(dict.fromkeys(lnk for lnk in raw_links if not is_ad_link(lnk)))

            links_data = []
            for lnk in content_links[:3]:
                link_content = await asyncio.to_thread(fetch_link_content, lnk)
                if link_content:
                    links_data.append(link_content)
            result['links'] = links_data

            # Extrair comentários do body text
            comment_blocks = []

            # Encontrar seção de comentários - procurar o footer "Log in to like or comment"
            comment_end_marker = None
            for i, line in enumerate(lines):
                if 'log in to like' in line.lower():
                    comment_end_marker = i
                    break

            if comment_end_marker:
                comment_section = lines[max(0, comment_end_marker - 50):comment_end_marker]

                # Pular linhas de metadados no final
                skip_count = 0
                for line in reversed(comment_section):
                    if re.match(r'^[\d,.]+$', line):
                        skip_count += 1
                    elif re.match(r'^\d+\s+comment', line, re.IGNORECASE):
                        skip_count += 1
                    elif re.match(r'^(January|February|March|April|May|June|July|August|September|October|November|December)', line):
                        skip_count += 1
                    else:
                        break
                if skip_count > 0:
                    comment_section = comment_section[:-skip_count]

                raw_blocks = '\n'.join(comment_section).split('\nReply')

                for block in raw_blocks:
                    block = block.strip()
                    if not block:
                        continue
                    block_lines = block.split('\n')
                    username = block_lines[0].strip()
                    if not username or len(username) > 40 or re.match(r'^[\d\s]+$', username):
                        continue
                    if username in ['Original audio', 'Log in', 'Sign up', 'Follow', 'Verified', 'See more posts', 'Blog', 'Jobs', 'Help', 'API', 'Privacy', 'Terms', 'Locations', 'Instagram Lite', 'Meta', 'Threads']:
                        continue

                    text_lines = []
                    likes_count = 0
                    for bl in block_lines[1:]:
                        bl = bl.strip()
                        if not bl:
                            continue
                        if re.match(r'^\d+[wd]$', bl):
                            continue
                        like_m = re.match(r'^(\d+)?\s*like', bl, re.IGNORECASE)
                        if like_m:
                            likes_count = int(like_m.group(1)) if like_m.group(1) else 1
                            continue
                        if bl in ['Original audio', 'Verified', '•']:
                            continue
                        text_lines.append(bl)

                    if text_lines:
                        comment = {'username': username, 'text': ' '.join(text_lines).strip()}
                        if likes_count:
                            comment['likes'] = likes_count
                        comment_blocks.append(comment)

                result['top_comments'] = comment_blocks

            # Extrair data do post do body text
            for line in lines:
                # Padrões de data em inglês: "1w", "2d", "April 11", "7 days ago"
                if re.match(r'^\d+[wd]$', line):
                    result['date'] = line
                    break
                elif 'ago' in line.lower() and re.search(r'\d+', line):
                    result['date'] = line
                    break
                elif re.match(r'^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+', line):
                    result['date'] = line
                    break

            # Detectar se é vídeo (Reel)
            is_video = '/reel/' in url

            # Se for Reel, transcrever
            if is_video:
                transcript_data = await asyncio.to_thread(transcribe_video, result['post_id'], output_dir)
                result['transcript'] = transcript_data

            ## Extração de imagens do post (percorrendo carrossel se houver múltiplos slides)

            # O Instagram só renderiza no DOM o slide atual + o(s) slide(s) vizinho(s) do
            # carrossel; slides seguintes só aparecem após clicar na seta "Next". Por isso
            # escopamos a busca ao container do carrossel (achado a partir da imagem que
            # corresponde ao og:image) e vamos clicando em "Next" até esgotar os slides,
            # em vez de pegar `img` da página inteira (que também pega thumbnails de
            # "mais posts" do mesmo perfil, sem relação com este post).
            carousel_js = """
            async (limit) => {
                function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

                const ogMeta = document.querySelector('meta[property="og:image"]');
                const ogUrl = ogMeta ? ogMeta.content : null;

                let container = null;
                if (ogUrl) {
                    const idPart = ogUrl.split('/').pop().split('_')[0];
                    const imgs = Array.from(document.querySelectorAll('img'));
                    const match = imgs.find(img => img.src.includes(idPart));
                    if (match) {
                        let el = match;
                        for (let i = 0; i < 12 && el.parentElement; i++) {
                            el = el.parentElement;
                            container = el;
                            if (el.querySelector('button[aria-label="Next"]')) break;
                        }
                    }
                }
                if (!container) container = document.body;

                const seen = new Map();
                function harvest() {
                    for (const img of container.querySelectorAll('img')) {
                        const src = img.src;
                        if (!src) continue;
                        if (!src.includes('cdninstagram.com') && !src.includes('fna.fbcdn.net')) continue;
                        if (src.includes('s150x150') || src.includes('s320x320')) continue;
                        if (src.includes('/logos/')) continue;
                        const key = src.split('?')[0];
                        if (!seen.has(key)) seen.set(key, { src, alt: img.alt || '' });
                    }
                }

                harvest();
                const MAX_CLICKS = 30;
                for (let i = 0; i < MAX_CLICKS && seen.size < limit; i++) {
                    const btn = container.querySelector('button[aria-label="Next"]');
                    if (!btn) break;
                    btn.click();
                    await sleep(900);
                    harvest();
                }

                return Array.from(seen.values());
            }
            """

            carousel_imgs = await page.evaluate(carousel_js, image_limit)
            post_images = []
            for img_data in carousel_imgs:
                post_images.append({
                    "src": img_data['src'],
                    "alt": img_data.get('alt') or "",
                    "index": len(post_images) + 1,
                })

            # Baixar imagens
            if download_images:
                import urllib.request
                downloaded = []
                for idx, img_info in enumerate(post_images[:image_limit]):
                    try:
                        ext = '.jpg'
                        img_path = os.path.join(output_dir, f"{result['post_id']}_img{idx+1}{ext}")
                        urllib.request.urlretrieve(img_info['src'], img_path)
                        img_info['local_path'] = img_path

                        # OCR na imagem usando Tesseract
                        try:
                            ocr_text = pytesseract.image_to_string(Image.open(img_path), lang='por+eng')
                            if ocr_text.strip():
                                img_info['ocr_text'] = ocr_text.strip()
                        except Exception as ocr_err:
                            img_info['ocr_error'] = str(ocr_err)

                        downloaded.append(img_info)
                    except Exception as e:
                        img_info['error'] = str(e)
                        downloaded.append(img_info)
                result['images'] = downloaded
            else:
                result['images'] = post_images[:image_limit]

        except Exception as e:
            result['error'] = str(e)
        finally:
            await browser.close()

    return result


def format_output(data: Dict[str, Any]) -> str:
    """Formata os dados do post para exibição amigável."""
    lines = []

    if data.get('error') and not data.get('caption'):
        lines.append(f"⚠️ Erro ao ler post: {data['error']}")
        return '\n'.join(lines)

    # Cabeçalho
    author = data.get('author', 'desconhecido')
    is_video = '/reel/' in data.get('url', '')
    prefix = '🎬' if is_video else '📸'
    lines.append(f"{prefix} **@{author}**")

    # Stats em uma linha
    stats_parts = []
    if data.get('likes'):
        stats_parts.append(f"❤️ **{data['likes']}** likes")
    if data.get('comments_count'):
        stats_parts.append(f"💬 {data['comments_count']}")
    if data.get('date'):
        stats_parts.append(f"📅 {data['date']}")
    if stats_parts:
        lines.append(' | '.join(stats_parts))

    lines.append('')

    # Legenda
    if data.get('caption'):
        caption = data['caption']
        # Remover aspas extras do final
        caption = caption.rstrip('" \n')
        lines.append("📝 **Legenda:**")
        lines.append(caption)
        lines.append('')

    # Transcrição de vídeo
    transcript = data.get('transcript', {})
    if transcript and transcript.get('transcript_text'):
        dur = transcript.get('duration_secs')
        dur_str = f" ({dur:.0f}s)" if dur else ''
        lines.append(f"🎙️ **Transcrição**{dur_str}:")
        lines.append(transcript['transcript_text'])
        lines.append('')
        if transcript.get('error'):
            lines.append(f"⚠️ Erro na transcrição: {transcript['error']}")
            lines.append('')

    # Imagens com ALT/OCR
    images = data.get('images', [])
    if images:
        lines.append(f"🖼️ **{len(images)} imagem(ns)**")
        for idx, img in enumerate(images, 1):
            local_path = img.get('local_path', '')
            alt = img.get('alt', '').strip()
            ocr_text = img.get('ocr_text', '').strip()
            if local_path:
                lines.append(f"  ⬇️ Imagem {idx}: {local_path}")
            if ocr_text:
                desc = ocr_text[:300]
            elif alt:
                desc = alt[:300]
            else:
                desc = None
            if desc:
                lines.append(f"  📝 desc: {desc}")
        lines.append('')

    # Links de conteúdo extraídos do post
    links = data.get('links', [])
    if links:
        lines.append(f"🔗 **Links do post ({len(links)})**")
        for lnk in links:
            if lnk.get('error'):
                continue
            lines.append(f"  **{lnk.get('title', lnk['url'])}**")
            lines.append(f"  {lnk['url']}")
            if lnk.get('content'):
                lines.append(f"  {lnk['content']}")
            lines.append('')

    # Comentários principais
    comments = data.get('top_comments', [])
    if comments:
        lines.append(f"💬 **Comentários** ({len(comments)}):")
        for i, c in enumerate(comments[:10], 1):
            username = c.get('username', '')
            text = c.get('text', '').strip()[:200]
            likes_str = ''
            if c.get('likes'):
                likes_str = f' 👍{c["likes"]}'
            if text:
                lines.append(f"  {i}. **{username}**: \"{text[:150]}{'...' if len(text) > 150 else ''}\"{likes_str}")
        lines.append('')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Instagram Reader - Extrai conteúdo de posts do Instagram'
    )
    parser.add_argument('post', help='URL do Instagram ou ID do post')
    parser.add_argument('--output', '-o', default='/tmp/ig_reader',
                        help='Diretório de saída para imagens/screenshots')
    parser.add_argument('--images', '-i', type=int, default=5,
                        help='Número máximo de imagens para baixar')
    parser.add_argument('--no-images', action='store_true',
                        help='Não baixar imagens')
    parser.add_argument('--json', action='store_true',
                        help='Saída em JSON')
    parser.add_argument('--screenshot', action='store_true',
                        help='Tirar screenshot da página')

    args = parser.parse_args()

    # Extrair URL
    post_id = extract_post_id(args.post)
    url = make_url(post_id)

    # Scrape
    data = asyncio.run(scrape_post(
        url=url,
        output_dir=os.path.join(args.output, post_id),
        download_images=not args.no_images,
        image_limit=args.images
    ))

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_output(data))

    # Retornar caminhos das imagens/vídeo para fácil acesso
    images = data.get('images', [])
    if images:
        local_paths = [img.get('local_path') for img in images if img.get('local_path')]
        if local_paths:
            print(f"\n📂 Imagens salvas em: {os.path.join(args.output, post_id)}/")
            for p in local_paths:
                print(f"   {p}")

    transcript = data.get('transcript', {})
    if transcript and transcript.get('video_path'):
        print(f"\n🎬 Vídeo: {transcript.get('video_path')}")
        print(f"📄 Transcrição: {os.path.join(args.output, post_id, post_id + '_transcript.txt')}")


if __name__ == '__main__':
    main()
