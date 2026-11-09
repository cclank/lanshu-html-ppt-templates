#!/usr/bin/env python3
"""Re-subset the embedded Google Fonts of a deck so every character on its pages has a glyph.

Templates embed fonts subset to their sample text, so new text may fall back to system fonts.
Run this (online) after editing a deck: it collects the characters the file uses, asks Google
Fonts for exactly those glyphs (text=, in chunks because Google ignores text= past ~800
characters) and rewrites <style id="embedded-fonts"> with Base64 WOFF2 data.

The font request comes from the file's Google Fonts <link>, else from the "css2:" note left in
the embedded-fonts comment, else it is rebuilt from the embedded @font-face rules.

  python3 embed_fonts.py deck.html                 update in place
  python3 embed_fonts.py source.html -o out.html   write elsewhere (also turns <link> fonts into embedded ones)
"""

import argparse
import base64
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
LINK = re.compile(r'<link[^>]+href="(https://fonts\.googleapis\.com/css2\?[^"]+)"[^>]*>\s*', re.I)
PRECONNECT = re.compile(r'<link[^>]+rel="preconnect"[^>]+fonts\.(googleapis|gstatic)\.com[^>]*>\s*', re.I)
EMBEDDED = re.compile(r'<style id="embedded-fonts">(.*?)</style>\s*', re.S)
CHUNK = 700
# Parts that never render in the deck's fonts: toolkit and presenter UI (system fonts),
# speaker notes, and stylesheets apart from their content: strings.
NOT_SHOWN = [re.compile(p, re.S) for p in (
    r'<!-- ═+ TOOLKIT v\d.*?END TOOLKIT ═+ -->', r'<!-- ═+ PRESENTER v1.*?END PRESENTER ═+ -->',
    r'<script type="application/json" id="deck-notes">.*?</script>', r'<style\b[^>]*>.*?</style>')]


def shown_text(html):
    contents = ''.join(m.group(2) for style in re.findall(r'<style\b[^>]*>(.*?)</style>', html, re.S)
                       for m in re.finditer(r'content:\s*([\'"])(.*?)\1', style))
    for pattern in NOT_SHOWN:
        html = pattern.sub('', html)
    return html + contents


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=60) as r:
        return r.read()


def query_from_faces(css):
    """Rebuild a css2 query (family, italic, weight) from embedded @font-face rules."""
    families = {}
    for block in re.findall(r'@font-face\s*{([^}]*)}', css):
        family = re.search(r'font-family:\s*[\'"]?([^;\'"]+)', block).group(1).strip()
        italic = 1 if re.search(r'font-style:\s*italic', block) else 0
        weight = int(re.search(r'font-weight:\s*(\d+)', block).group(1))
        families.setdefault(family, set()).add((italic, weight))
    parts = []
    for family, axes in families.items():
        name = family.replace(' ', '+')
        if any(i for i, _ in axes):
            parts.append(f'family={name}:ital,wght@' + ';'.join(f'{i},{w}' for i, w in sorted(axes)))
        else:
            parts.append(f'family={name}:wght@' + ';'.join(str(w) for _, w in sorted(axes)))
    return '&'.join(parts) + '&display=swap'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('file')
    ap.add_argument('-o', '--output', help='write here instead of updating the file in place')
    args = ap.parse_args()
    html = open(args.file, encoding='utf-8').read()
    links = [u.replace('&amp;', '&') for u in LINK.findall(html)]
    embedded = EMBEDDED.search(html)
    if links:
        queries = [re.sub(r'&text=[^&]*', '', u).split('?', 1)[1] for u in links]
    elif embedded:
        note = re.search(r'css2: ([^ *]+)', embedded.group(1))
        queries = [note.group(1) if note else query_from_faces(embedded.group(1))]
    else:
        sys.exit('No Google Fonts <link> and no embedded fonts found.')
    body = EMBEDDED.sub('', html)
    chars = sorted({c for c in shown_text(body) if ord(c) >= 0x20 and c not in '  '} | {chr(i) for i in range(0x20, 0x7f)})
    text, css = ''.join(chars), []
    for query in queries:
        for i in range(0, len(text), CHUNK):
            url = 'https://fonts.googleapis.com/css2?' + query + '&text=' + urllib.parse.quote(text[i:i + CHUNK], safe='')
            sheet = get(url).decode('utf-8')
            if sheet.count('@font-face') > 40:
                sys.exit('Google Fonts returned unsubsetted fonts; lower CHUNK.')
            for block in re.findall(r'@font-face\s*{[^}]*}', sheet):
                font = re.search(r'url\((https://fonts\.gstatic\.com/[^)]+)\)', block).group(1)
                try:
                    data = base64.b64encode(get(font)).decode('ascii')
                except urllib.error.HTTPError:
                    continue  # none of this chunk's characters exist in that font
                css.append(block.replace(f'url({font})', f'url(data:font/woff2;base64,{data})'))
    style = (f'<style id="embedded-fonts">/* 内嵌字体：按本页用到的 {len(chars)} 个字符裁剪自 Google Fonts（SIL OFL），离线可用'
             f' · css2: {queries[0] if len(queries) == 1 else "&".join(queries)} */\n' + '\n'.join(css) + '\n</style>\n')
    if embedded and not links:
        out = html[:embedded.start()] + style + html[embedded.end():]
    else:
        out = PRECONNECT.sub('', LINK.sub('', body))
        out = re.sub(r'\s*<!--[^>]*在线字体[^>]*-->', '', out, count=1)
        out = out.replace('</title>\n', '</title>\n' + style, 1) if '</title>\n' in out else out.replace('</head>', style + '</head>', 1)
    target = args.output or args.file
    with open(target, 'w', encoding='utf-8') as f:
        f.write(out)
    print(f'{target}: {len(chars)} characters, {len(css)} font files, {len(style.encode()) / 1024:,.0f} KB embedded')


if __name__ == '__main__':
    main()
