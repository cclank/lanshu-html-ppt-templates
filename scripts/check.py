#!/usr/bin/env python3
"""Check bundle integrity and syntax without executing presentation code."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from deck import ROOT, DeckParser, catalog, validate_file


def main():
    data = catalog()
    errors = []
    checked = pages = 0
    templates = data['templates']
    toolkit = (ROOT / 'assets/toolkit.html').read_text(encoding='utf-8').strip()
    if len(templates) != 25 or len({t['id'] for t in templates}) != 25:
        errors.append('Expected 25 uniquely named templates.')
    for t in templates:
        file = ROOT / 'assets/templates' / t['file']
        try:
            report = validate_file(file)
            errors.extend(f'{t["id"]}: {e}' for e in report['errors'])
            checked += report['javascript_checked']
            pages += report['pages_from_notes']
            if toolkit not in file.read_text(encoding='utf-8'):
                errors.append(f'{t["id"]}: presentation toolkit is missing or outdated; run scripts/inject_toolkit.py.')
            if report['pages_from_notes'] != t['pages']:
                errors.append(f'{t["id"]}: catalog page count does not match speaker notes.')
            if hashlib.sha256(file.read_bytes()).hexdigest() != t['sha256']:
                errors.append(f'{t["id"]}: source checksum changed; inspect and deliberately update the catalog.')
            thumb = ROOT / 'assets/thumbs' / (t['id'] + '.jpg')
            if not thumb.is_file():
                errors.append(f'{t["id"]}: missing thumbnail.')
            elif hashlib.sha256(thumb.read_bytes()).hexdigest() != t['thumbnail_sha256']:
                errors.append(f'{t["id"]}: thumbnail checksum changed.')
        except (OSError, ValueError) as error:
            errors.append(f'{t["id"]}: {error}')
    if pages != 282:
        errors.append(f'Expected 282 total pages, got {pages}.')
    for file in (ROOT / 'scripts').glob('*.py'):
        try:
            compile(file.read_text(encoding='utf-8'), str(file), 'exec')
        except SyntaxError as error:
            errors.append(str(error))
    site = ROOT / 'site'
    for file in site.glob('*.html'):
        p = DeckParser()
        p.feed(file.read_text(encoding='utf-8'))
        for s in p.scripts:
            if s['attrs'].get('src') or s['attrs'].get('type') == 'application/ld+json':
                continue
            result = subprocess.run(['node', '--check'], input=s['source'], text=True, capture_output=True)
            if result.returncode:
                errors.append(f'{file.name}: {result.stderr[:1000]}')
        checked += len([s for s in p.scripts if not s['attrs'].get('src')
                        and s['attrs'].get('type') != 'application/ld+json'])
    for name in ['SKILL.md', 'README.md', 'NOTICE.md', 'LICENSE', 'agents/openai.yaml', 'assets/toolkit.html',
                 'references/template-catalog.md', 'references/authoring.md', 'wrangler.jsonc']:
        if not (ROOT / name).is_file():
            errors.append('Missing ' + name)
    # Build links point only at public allowlisted files; never publish the source tree wholesale.
    if (ROOT / 'dist/index.html').is_file():
        for file in (ROOT / 'dist').glob('*.html'):
            p = DeckParser()
            p.feed(file.read_text(encoding='utf-8'))
            for link in p.links:
                if link.startswith(('#', 'data:', 'https:', 'http:', 'mailto:')):
                    continue
                relative = link.split('#', 1)[0].split('?', 1)[0]
                target = ROOT / 'dist' / relative.lstrip('/') if relative.startswith('/') else file.parent / relative
                if relative and not target.exists():
                    errors.append(f'{file.name}: missing local link {link}')
    print(json.dumps({'templates': len(templates), 'pages': pages, 'javascript_syntax_checks': checked,
                      'original_asset_integrity': not any('checksum' in e for e in errors),
                      'errors': errors, 'scope': 'static integrity and syntax; no browser execution'},
                     ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
