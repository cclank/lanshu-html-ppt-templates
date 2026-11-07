#!/usr/bin/env python3
"""Install assets/toolkit.html (presentation toolkit) into every bundled template; safe to rerun.

The toolkit adds a laser pointer, pen annotations, black/white screen, page jump, an overview
for every template, the phone remote (with scripts/present.py) and a shortcut panel.
It is placed before the presenter block so its keys are handled first, replaces any older
toolkit block, and teaches the presenter window to forward B/W (blank screen).
Template checksums change; review the result and update catalog.json deliberately.
"""

import argparse
from pathlib import Path
import re

from deck import ROOT

OLD = re.compile(r'<!-- ═+ TOOLKIT v\d.*?<!-- ═+ END TOOLKIT ═+ -->\n?', re.S)
PRESENTER = '<!-- ═══════════════ PRESENTER v1'
# Presenter window patches: (original, replacement); skipped when already applied.
PATCHES = [
    ("Home:'Home',End:'End'};",
     "Home:'Home',End:'End',b:'b',B:'b','.':'b',w:'w',W:'w',',':'w'};"),
    ('<button class="nx" id="bn">下一页 ›</button></div>',
     '<button class="nx" id="bn">下一页 ›</button><button id="bb">黑屏 B</button></div>'),
    ("$('bn').onclick=()=>send('ArrowRight');",
     "$('bn').onclick=()=>send('ArrowRight');$('bb').onclick=()=>send('b');"),
    ('主屏按 F 全屏</div></footer>',
     '主屏按 F 全屏 · B 黑屏</div></footer>'),
]
# 17-terminal writes its hint at runtime, so the toolkit cannot append to it.
HINTS = {
    '17-terminal.html': ("'→ 执行下一条命令 · 动画中再按 → 立即完成 · F 全屏'",
                         "'→ 执行下一条命令 · 动画中再按 → 立即完成 · F 全屏 · ? 快捷键'"),
}


def inject(path, block):
    src = path.read_text(encoding='utf-8')
    html = OLD.sub('', src)
    if html.count(PRESENTER) != 1:
        raise ValueError(f'{path.name}: expected exactly one presenter block.')
    html = html.replace(PRESENTER, block + PRESENTER)
    for old, new in PATCHES:
        if new in html:
            continue
        if html.count(old) != 1:
            raise ValueError(f'{path.name}: presenter patch target is not unique: {old}')
        html = html.replace(old, new)
    hint = HINTS.get(path.name)
    if hint and hint[1] not in html:
        if html.count(hint[0]) != 1:
            raise ValueError(f'{path.name}: hint text is not unique.')
        html = html.replace(hint[0], hint[1])
    if html != src:
        path.write_text(html, encoding='utf-8')
    return html != src


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='*', type=Path, help='templates to update (default: assets/templates/*.html)')
    args = ap.parse_args()
    block = (ROOT / 'assets/toolkit.html').read_text(encoding='utf-8').strip() + '\n'
    for path in args.files or sorted((ROOT / 'assets/templates').glob('[0-9][0-9]-*.html')):
        print(f'{path.name:22} {"updated" if inject(path, block) else "unchanged"}')


if __name__ == '__main__':
    main()
