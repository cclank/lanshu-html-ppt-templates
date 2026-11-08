#!/usr/bin/env python3
"""Build the full source/skill ZIP and publish it as a GitHub release asset.

The site links to releases/latest/download/, so the newest release must carry the ZIP and
SHA256SUMS.txt. Creates tag v<catalog version> on the pushed default branch, or replaces the
assets when that release already exists. Requires the GitHub CLI (gh) to be logged in.
"""

import argparse
import json
import subprocess
import sys

from deck import ROOT, catalog


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--notes', help='release notes (default: a one-line summary)')
    ap.add_argument('--dry-run', action='store_true', help='build and print the gh command without publishing')
    args = ap.parse_args()
    for script in ('build_site.py', 'check.py'):
        subprocess.run([sys.executable, str(ROOT / 'scripts' / script)], check=True)
    data = catalog()
    build = json.loads((ROOT / 'dist/build.json').read_text(encoding='utf-8'))
    tag = 'v' + data['version']
    files = [str(ROOT / 'release' / (data['name'] + '.zip')), str(ROOT / 'release/SHA256SUMS.txt')]
    notes = args.notes or (f"{build['templates']} 套模板 · {build['pages']} 页示例 · "
                           f"SHA256 {build['bundle_sha256']}")
    exists = subprocess.run(['gh', 'release', 'view', tag], cwd=ROOT, capture_output=True).returncode == 0
    command = (['gh', 'release', 'upload', tag, *files, '--clobber'] if exists else
               ['gh', 'release', 'create', tag, *files, '--title', tag, '--notes', notes, '--latest'])
    if args.dry_run:
        print(' '.join(command))
        return 0
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == '__main__':
    sys.exit(main())
