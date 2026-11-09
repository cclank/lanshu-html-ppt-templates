#!/usr/bin/env python3
"""Build, validate, then publish the configured gallery to Cloudflare Workers."""

import argparse
import os
import subprocess
import sys

from deck import ROOT


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    # Showcase and hover-preview media are not in git; restore them first or the deploy drops them.
    for script in ('fetch_previews.py', 'build_site.py', 'check.py'):
        subprocess.run([sys.executable, str(ROOT / 'scripts' / script)], check=True)
    env = {**os.environ, 'WRANGLER_SEND_METRICS': 'false'}
    command = ['npx', '--yes', 'wrangler@4.148.0', 'deploy', '--config', str(ROOT / 'wrangler.jsonc')]
    if args.dry_run:
        command.append('--dry-run')
    return subprocess.run(command, cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    sys.exit(main())
