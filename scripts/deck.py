#!/usr/bin/env python3
"""Select, copy, inspect and statically validate a bundled HTML presentation."""

import argparse
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def catalog():
    return json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))


def select_template(value):
    value = value.removesuffix('.html')
    matches = [t for t in catalog()['templates'] if value in
               (t['id'], str(t['number']), f"{t['number']:02}", t['id'].split('-', 1)[1])]
    if len(matches) != 1:
        raise ValueError(f'Unknown or ambiguous template: {value}. Use the list command.')
    return matches[0]


class DeckParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.current_script = None
        self.title_parts = []
        self.in_head = False
        self.in_title = False
        self.slide_count = 0
        self.ids = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'head':
            self.in_head = True
        if tag == 'title' and self.in_head:
            self.in_title = True
        if tag == 'script':
            self.current_script = {'attrs': attrs, 'source': ''}
        if 'slide' in attrs.get('class', '').split():
            self.slide_count += 1
        if attrs.get('id'):
            self.ids.append(attrs['id'])
        for key in ('href', 'src', 'poster', 'data-src'):
            if attrs.get(key):
                self.links.append(attrs[key])

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False
        if tag == 'title':
            self.in_title = False
        if tag == 'script' and self.current_script is not None:
            self.scripts.append(self.current_script)
            self.current_script = None

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.current_script is not None:
            self.current_script['source'] += data


def inspect_file(path):
    source = Path(path).read_text(encoding='utf-8')
    parser = DeckParser()
    parser.feed(source)
    notes_scripts = [s for s in parser.scripts if s['attrs'].get('id') == 'deck-notes']
    if len(notes_scripts) != 1:
        raise ValueError('Expected one script#deck-notes JSON array.')
    notes = json.loads(notes_scripts[0]['source'])
    if not isinstance(notes, list) or not notes or not all(isinstance(n, str) and n.strip() for n in notes):
        raise ValueError('Speaker notes must be a non-empty JSON array of non-empty strings.')
    return parser, {
        'file': str(Path(path).resolve()),
        'title': ''.join(parser.title_parts),
        'pages_from_notes': len(notes),
        'slide_elements': parser.slide_count,
        'notes': len(notes),
        'inline_javascript': sum(not s['attrs'].get('src') and s['attrs'].get('type') != 'application/json'
                                 for s in parser.scripts),
        'embedded_fonts': len(re.findall(r'data:font/', source)),
    }


def validate_file(path):
    parser, report = inspect_file(path)
    failures = []
    if not report['title'].strip():
        failures.append('Missing document title.')
    if report['slide_elements'] and report['slide_elements'] != report['notes']:
        failures.append('The .slide count differs from the speaker-note count.')
    node = shutil.which('node')
    if not node:
        failures.append('Node.js is required for JavaScript syntax validation; it was not found.')
    checked = 0
    for i, script in enumerate(parser.scripts, 1):
        if script['attrs'].get('type') == 'application/json':
            try:
                json.loads(script['source'])
            except json.JSONDecodeError as error:
                failures.append(f'JSON script {i}: {error}')
            continue
        if script['attrs'].get('src') or not node:
            continue
        result = subprocess.run([node, '--check'], input=script['source'], text=True,
                                capture_output=True, timeout=30)
        checked += 1
        if result.returncode:
            failures.append(f'JavaScript script {i}: {result.stderr.strip()[:1400]}')
    report.update(javascript_checked=checked, errors=failures,
                  validation='static only; browser rendering was not executed')
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='Show the bundled templates.')
    listing.add_argument('--json', action='store_true')
    listing.add_argument('--pdf', action='store_true', help='Only templates with multi-page print CSS.')
    new = commands.add_parser('new', help='Copy a complete template; never overwrite a file.')
    new.add_argument('template')
    new.add_argument('--output', required=True, type=Path)
    new.add_argument('--title', help='Replace the browser title only, not the visible cover.')
    source = commands.add_parser('source', help='Read source with font Base64 hidden; not a valid output file.')
    source.add_argument('template')
    for command in ('inspect', 'validate'):
        p = commands.add_parser(command)
        p.add_argument('file', type=Path)
    args = ap.parse_args()
    try:
        if args.command == 'list':
            templates = [t for t in catalog()['templates'] if not args.pdf or t['multi_page_print']]
            if args.json:
                print(json.dumps(templates, ensure_ascii=False, indent=2))
            else:
                for t in templates:
                    pdf = 'print CSS' if t['multi_page_print'] else 'browser playback'
                    print(f"{t['id']:21} {t['pages']:2} pages  {pdf:17} {t['name']} · {t['description']}")
        elif args.command == 'new':
            t = select_template(args.template)
            if args.output.suffix.lower() != '.html':
                raise ValueError('--output must end with .html')
            data = (ROOT / 'assets/templates' / t['file']).read_text(encoding='utf-8')
            if args.title:
                data = re.sub(r'(<title>).*?(</title>)',
                              lambda m: m[1] + html.escape(args.title) + m[2], data,
                              count=1, flags=re.S)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8') as f:
                f.write(data)
            print(f'Created: {args.output.resolve()} ({t["pages"]} pages)')
            print('Edit the visible content and deck-notes next; --title only changes the browser title.')
        elif args.command == 'source':
            t = select_template(args.template)
            data = (ROOT / 'assets/templates' / t['file']).read_text(encoding='utf-8')
            for i, line in enumerate(data.splitlines(), 1):
                line = re.sub(r'data:font/[^;,]+;base64,[A-Za-z0-9+/=]+',
                              '[FONT BASE64 HIDDEN FOR READING]', line)
                if len(line) > 1600:
                    line = line[:1600] + ' … [long line truncated for reading]'
                print(f'{i:4}: {line}')
        else:
            report = validate_file(args.file) if args.command == 'validate' else inspect_file(args.file)[1]
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report.get('errors') else 0
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
