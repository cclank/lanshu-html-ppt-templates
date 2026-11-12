#!/usr/bin/env python3
"""Build an allowlisted static site and a self-contained source/skill ZIP."""

import hashlib
import html
import json
from pathlib import Path
import shutil
import sys
import zipfile

from deck import ROOT, catalog

RELEASE_URL = 'https://github.com/cclank/lanshu-html-ppt-templates/releases/latest/download/'
EXCLUDED = {'dist', 'release', '.git', '.wrangler', 'node_modules', '__pycache__', '.DS_Store'}
SOURCE_FILES = {'.gitignore', 'LICENSE', 'NOTICE.md', 'README.md', 'SKILL.md', 'catalog.json',
                'package.json', 'preview-media.json', 'wrangler.jsonc'}
SOURCE_DIRS = {'.github', 'agents', 'assets', 'docs', 'references', 'scripts', 'site'}


def source_files():
    candidates = [ROOT / name for name in SOURCE_FILES]
    for name in sorted(SOURCE_DIRS):
        directory = ROOT / name
        if directory.is_dir():
            candidates.extend(directory.rglob('*'))
    for p in sorted(candidates):
        parts = p.relative_to(ROOT).parts
        if not p.is_file() or any(x in EXCLUDED for x in parts):
            continue
        if parts[:2] == ('site', 'media'):
            continue
        if any(x.startswith('.env') for x in parts) or p.suffix in ('.pyc', '.log', '.zip'):
            continue
        yield p


def card(t):
    esc = html.escape
    pdf = '多页打印样式' if t['multi_page_print'] else '浏览器放映'
    cat = '经典' if t['category'] == 'classic' else '实验'
    new = t['number'] >= 21
    badge = '<span class="new-badge">新增</span>' if new else ''
    media = json.loads((ROOT / 'preview-media.json').read_text(encoding='utf-8'))
    preview = media['previews']['pattern'].format(id=t['id'])
    search = esc(' '.join([t['id'], t['name'], t['english'], t['description'], *t['use_cases']]), quote=True)
    return f'''<article class="card" data-category="{t['category']}" data-new="{str(new).lower()}" data-print="{str(t['multi_page_print']).lower()}" data-search="{search}">
      <a class="preview" href="/{t['file']}" aria-label="预览{esc(t['name'])}"><img src="/thumbs/{t['id']}.jpg" alt="{esc(t['name'])}模板封面" width="960" height="540" loading="lazy" decoding="async"><video class="hover" muted loop playsinline preload="none" data-src="__PREVIEW_BASE__/media/{preview}" aria-hidden="true"></video><span class="play">打开演示 ↗</span></a>
      <div class="card-body"><div class="card-top"><span class="number">{t['number']:02} {badge}</span><span class="category">{cat} / {t['pages']} 页</span></div>
      <h3><a href="/{t['file']}">{esc(t['name'])}</a><span>{esc(t['english'])}</span></h3>
      <p>{esc(t['description'])}</p><div class="card-foot"><span class="cap {'print' if t['multi_page_print'] else ''}">{pdf}</span><a class="download" href="/{t['file']}" download="lanshu-{t['file']}" aria-label="下载{esc(t['name'])} HTML">下载 HTML ↓</a></div></div></article>'''


def main():
    data = catalog()
    dist = ROOT / 'dist'
    # Only this generated directory is replaced; original templates and user outputs are separate.
    if dist.is_symlink():
        raise ValueError('Refusing to replace a symlinked dist directory.')
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir()
    for file in (ROOT / 'site').iterdir():
        if file.is_file():
            shutil.copy2(file, dist / file.name)
        elif file.name == 'media' and file.is_dir():
            shutil.copytree(file, dist / 'media')
    page = (dist / 'index.html').read_text(encoding='utf-8')
    preview_base = '' if (dist / 'media').is_dir() else data['homepage'].rstrip('/')
    page = page.replace('<!-- TEMPLATE_CARDS -->', '\n'.join(card(t) for t in data['templates']))
    page = page.replace('__PREVIEW_BASE__', preview_base)
    (dist / 'index.html').write_text(page, encoding='utf-8')
    for t in data['templates']:
        shutil.copy2(ROOT / 'assets/templates' / t['file'], dist / t['file'])
    shutil.copytree(ROOT / 'assets/thumbs', dist / 'thumbs')
    shutil.copy2(ROOT / 'assets/lanshu-mark.svg', dist / 'favicon.svg')
    for name in ('README.md', 'SKILL.md', 'NOTICE.md', 'LICENSE', 'catalog.json'):
        shutil.copy2(ROOT / name, dist / name)
    if (ROOT / 'docs/media').is_dir():
        shutil.copytree(ROOT / 'docs/media', dist / 'docs/media')
    if (ROOT / 'preview-media.json').is_file():
        shutil.copy2(ROOT / 'preview-media.json', dist / 'preview-media.json')
    # The full ZIP outgrew the 25 MiB per-file cap of Workers static assets, so it is built into
    # release/ and published as a GitHub release asset (scripts/release.py); /downloads/ redirects there.
    release = ROOT / 'release'
    if release.is_symlink():
        raise ValueError('Refusing to write into a symlinked release directory.')
    release.mkdir(exist_ok=True)
    bundle = release / (data['name'] + '.zip')
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p in source_files():
            info = zipfile.ZipInfo(data['name'] + '/' + p.relative_to(ROOT).as_posix(), (2026, 11, 4, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, p.read_bytes())
    checksum = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (release / 'SHA256SUMS.txt').write_text(f'{checksum}  {bundle.name}\n', encoding='utf-8')
    (dist / 'build.json').write_text(json.dumps({
        'name': data['name'], 'version': data['version'], 'templates': len(data['templates']),
        'pages': sum(t['pages'] for t in data['templates']),
        'bundle_sha256': checksum, 'bundle_bytes': bundle.stat().st_size,
        'bundle_url': RELEASE_URL + bundle.name,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    base = data['homepage'].rstrip('/')
    urls = [base + '/', base + '/guide.html'] + [base + '/' + t['file'] for t in data['templates']]
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += '\n'.join('<url><loc>' + html.escape(u) + '</loc></url>' for u in urls) + '\n</urlset>\n'
    (dist / 'sitemap.xml').write_text(sitemap, encoding='utf-8')
    (dist / 'robots.txt').write_text(f'User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n', encoding='utf-8')
    print(json.dumps({'site': str(dist), 'bundle': str(bundle), 'templates': len(data['templates']),
                      'pages': sum(t['pages'] for t in data['templates']),
                      'bundle_mib': round(bundle.stat().st_size / 1024 ** 2, 2), 'sha256': checksum}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
