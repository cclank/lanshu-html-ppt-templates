#!/usr/bin/env python3
"""Build and serve the static gallery on loopback for local development."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys

from deck import ROOT


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', type=int, default=4173)
    args = ap.parse_args()
    subprocess.run([sys.executable, str(ROOT / 'scripts/build_site.py')], check=True)
    server = ThreadingHTTPServer(('127.0.0.1', args.port),
                                 partial(SimpleHTTPRequestHandler, directory=str(ROOT / 'dist')))
    print(f'Gallery: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
