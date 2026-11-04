#!/usr/bin/env python3
"""Restore optional hosted preview media and verify its manifest checksums."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / 'preview-media.json').read_text(encoding='utf-8'))
    directory = ROOT / 'site/media'
    directory.mkdir(parents=True, exist_ok=True)
    for entry in manifest['files']:
        name = entry['file']
        if Path(name).name != name:
            raise ValueError('Invalid media filename.')
        target = directory / name
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == entry['sha256']:
            print('Already verified:', name)
            continue
        url = 'https://ppt.lanshuagent.com/media/' + name
        request = urllib.request.Request(url, headers={'User-Agent': 'Lanshu-Preview-Downloader/1.0'})
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read(25 * 1024 * 1024 + 1)
        if len(data) > 25 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Media checksum failed: ' + name)
        temporary = target.with_suffix(target.suffix + '.partial')
        temporary.write_bytes(data)
        temporary.replace(target)
        print('Restored and verified:', name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
