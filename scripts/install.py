#!/usr/bin/env python3
"""Install the self-contained skill; refuse to overwrite an existing skill."""

import argparse
import os
from pathlib import Path
import shutil
import sys

from deck import ROOT


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    default = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'skills'
    ap.add_argument('--skills-dir', type=Path, default=default)
    args = ap.parse_args()
    target = args.skills_dir.expanduser() / 'lanshu-html-ppt-templates'
    if target.exists() or target.is_symlink():
        print(f'Refusing to overwrite existing skill: {target}', file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + '.installing')
    if staging.exists() or staging.is_symlink():
        print(f'Installation staging directory already exists: {staging}', file=sys.stderr)
        return 1
    staging.mkdir()
    try:
        for name in ['SKILL.md', 'catalog.json', 'NOTICE.md', 'LICENSE']:
            shutil.copy2(ROOT / name, staging / name)
        for name in ['assets', 'references', 'agents']:
            shutil.copytree(ROOT / name, staging / name)
        (staging / 'scripts').mkdir()
        for name in ['deck.py', 'present.py', 'embed_fonts.py']:
            shutil.copy2(ROOT / 'scripts' / name, staging / 'scripts' / name)
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging)
        raise
    print(f'Installed skill: {target}')
    print('Open a new chat to refresh skill discovery, then use $lanshu-html-ppt-templates.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
