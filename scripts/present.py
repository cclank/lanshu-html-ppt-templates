#!/usr/bin/env python3
"""Present decks on the local network and control them from a phone.

Serves a deck file or folder to this computer and the LAN, plus a phone remote
(/remote) for paging, speaker notes, jumping, blank screen, laser pointer and pen.
Open a deck, press M and scan the QR code; phone and computer must share a Wi-Fi.
Standard library only. While it runs, devices on the same network can read the
served folder, and anyone holding the 6-digit pairing code can send commands.

  python3 present.py                   serve this folder (assets/templates in the repo)
  python3 present.py deck.html         serve one deck and open it
  python3 present.py --port 9000 --no-browser
"""

import argparse
from functools import partial
import html
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import re
import socket
import threading
from urllib.parse import parse_qs, quote, urlsplit
import webbrowser

# ── QR code: byte mode, error correction level M, versions 1–10 (enough for a LAN URL) ──
QR_BLOCKS = {  # version: (ECC codewords per block, [(blocks, data codewords per block)])
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]),
    5: (24, [(2, 43)]), 6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]), 10: (26, [(4, 43), (1, 44)]),
}
QR_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
            7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
QR_MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]


def gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def rs_remainder(data, degree):
    divisor, root = [0] * (degree - 1) + [1], 1
    for _ in range(degree):
        for j in range(degree):
            divisor[j] = gf_mul(divisor[j], root)
            if j + 1 < degree:
                divisor[j] ^= divisor[j + 1]
        root = gf_mul(root, 2)
    result = [0] * degree
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= gf_mul(coef, factor)
    return result


def qr_penalty(m):
    n, score = len(m), 0
    for line in m + [list(col) for col in zip(*m)]:
        run, prev = 0, None
        for v in line + [None]:
            if v == prev:
                run += 1
                continue
            if run >= 5:
                score += run - 2
            run, prev = 1, v
        bits = ''.join('1' if v else '0' for v in line)
        score += 40 * (bits.count('10111010000') + bits.count('00001011101'))
    score += 3 * sum(m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]
                     for y in range(n - 1) for x in range(n - 1))
    dark = sum(map(sum, m))
    return score + 10 * (abs(dark * 20 - n * n * 10) // (n * n))


def qr_matrix(text):
    data = text.encode('utf-8')
    for version in range(1, 11):
        ecc, groups = QR_BLOCKS[version]
        capacity = sum(count * size for count, size in groups)
        count_bits = 8 if version < 10 else 16
        if 12 + 8 * len(data) + (count_bits - 8) <= capacity * 8:
            break
    else:
        raise ValueError('Text is too long for the QR code.')
    bits = []
    put = lambda value, n: bits.extend((value >> i) & 1 for i in range(n - 1, -1, -1))
    put(0b0100, 4)
    put(len(data), count_bits)
    for byte in data:
        put(byte, 8)
    put(0, min(4, capacity * 8 - len(bits)))
    put(0, -len(bits) % 8)
    words = [int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    filler = 0xEC
    while len(words) < capacity:
        words.append(filler)
        filler ^= 0xEC ^ 0x11
    blocks, i = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(words[i:i + size])
            i += size
    eccs = [rs_remainder(block, ecc) for block in blocks]
    final = [block[j] for j in range(max(map(len, blocks))) for block in blocks if j < len(block)]
    final += [e[j] for j in range(ecc) for e in eccs]

    n = version * 4 + 17
    m = [[False] * n for _ in range(n)]
    fixed = [[False] * n for _ in range(n)]

    def fix(x, y, dark):
        m[y][x], fixed[y][x] = dark, True

    for i in range(n):
        fix(6, i, i % 2 == 0)
        fix(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if 0 <= cx + dx < n and 0 <= cy + dy < n:
                    fix(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    align = QR_ALIGN[version]
    for ax in align:
        for ay in align:
            if (ax, ay) in ((6, 6), (6, align[-1]), (align[-1], 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    fix(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask):
        value = mask  # level M contributes 00 to the format bits
        rem = value
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        word = (value << 10 | rem) ^ 0x5412
        bit = lambda i: (word >> i) & 1 == 1
        for i in range(6):
            fix(8, i, bit(i))
        fix(8, 7, bit(6))
        fix(8, 8, bit(7))
        fix(7, 8, bit(8))
        for i in range(9, 15):
            fix(14 - i, 8, bit(i))
        for i in range(8):
            fix(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            fix(8, n - 15 + i, bit(i))
        fix(8, n - 8, True)

    draw_format(0)
    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        word = version << 12 | rem
        for i in range(18):
            dark = (word >> i) & 1 == 1
            fix(n - 11 + i % 3, i // 3, dark)
            fix(i // 3, n - 11 + i % 3, dark)

    i, right = 0, n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(n):
            for j in range(2):
                x = right - j
                y = n - 1 - vert if (right + 1) & 2 == 0 else vert
                if not fixed[y][x] and i < len(final) * 8:
                    m[y][x] = (final[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2

    def apply(mask):
        f = QR_MASKS[mask]
        for y in range(n):
            for x in range(n):
                if not fixed[y][x] and f(x, y):
                    m[y][x] = not m[y][x]

    scores = []
    for mask in range(8):
        apply(mask)
        draw_format(mask)
        scores.append(qr_penalty(m))
        apply(mask)
    best = scores.index(min(scores))
    apply(best)
    draw_format(best)
    return m


def qr_svg(text, border=4):
    m = qr_matrix(text)
    size = len(m) + 2 * border
    path = ''.join(f'M{x + border},{y + border}h1v1h-1z'
                   for y, row in enumerate(m) for x, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
            f'<rect width="100%" height="100%" fill="#fff"/><path d="{path}" fill="#000"/></svg>')


# ── Pairing hub: decks and phones meet on a 6-digit code; messages flow over Server-Sent Events ──
class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}

    def session(self, code):
        with self.lock:
            return self.sessions.setdefault(code, {'state': {}, 'deck': set(), 'remote': set()})

    def send(self, code, role, message):
        s = self.session(code)
        with self.lock:
            targets = list(s[role])
        for q in targets:
            q.put(message)
        return len(targets)

    def announce(self, code):
        s = self.session(code)
        with self.lock:
            decks, remotes = len(s['deck']), len(s['remote'])
        self.send(code, 'deck', {'t': 'remote', 'count': remotes})
        self.send(code, 'remote', {'t': 'deck', 'online': decks > 0})


HUB = Hub()


def lan_ips():
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('10.255.255.255', 1))  # no packet is sent; this only picks the outgoing interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    ips = []
    for ip in found:
        if ip not in ips and not ip.startswith(('127.', '169.254.')):
            ips.append(ip)
    private = re.compile(r'(192\.168|10|172\.(1[6-9]|2\d|3[01]))\.')
    return sorted(ips, key=lambda ip: not private.match(ip))


def index_page(directory):
    decks = ''.join(f'<li><a href="{quote(p.name)}">{html.escape(p.stem)}</a></li>'
                    for p in sorted(Path(directory).glob('*.html')))
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地放映</title><style>body{{max-width:720px;margin:48px auto;padding:0 24px;font:16px/1.7 system-ui,"PingFang SC","Microsoft YaHei",sans-serif;color:#1b1b1f}}
a{{color:#0a5bd3}}li{{margin:6px 0}}</style><h1>本地放映</h1><p>打开任意一份演示，按 M 用手机扫码遥控；按 ? 查看全部快捷键。</p><ul>{decks}</ul></html>'''


class Handler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def reply(self, body, ctype, status=200):
        data = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path == '/api/info':
            return self.reply(json.dumps({'ips': lan_ips(), 'port': self.server.server_port}), 'application/json')
        if url.path == '/api/qr.svg':
            try:
                return self.reply(qr_svg(query.get('text', [''])[0][:200]), 'image/svg+xml')
            except ValueError:
                return self.send_error(400)
        if url.path in ('/remote', '/remote.html'):
            return self.reply(REMOTE_HTML, 'text/html; charset=utf-8')
        events = re.fullmatch(r'/api/s/(\d{6})/events', url.path)
        if events:
            return self.events(events[1], query.get('role', ['remote'])[0])
        if url.path == '/' and not (Path(self.directory) / 'index.html').is_file():
            return self.reply(index_page(self.directory), 'text/html; charset=utf-8')
        return super().do_GET()

    def do_POST(self):
        match = re.fullmatch(r'/api/s/(\d{6})/(state|cmd)', urlsplit(self.path).path)
        length = int(self.headers.get('Content-Length') or 0)
        if not match or length > 512 * 1024:
            return self.send_error(404 if not match else 413)
        try:
            data = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            return self.send_error(400)
        code, kind = match[1], match[2]
        if kind == 'state':
            s = HUB.session(code)
            with HUB.lock:
                s['state'].update(data)
                state = dict(s['state'])
            HUB.send(code, 'remote', {**state, 't': 'state'})
            return self.reply('{"ok":true}', 'application/json')
        decks = HUB.send(code, 'deck', data)
        return self.reply(json.dumps({'ok': True, 'decks': decks}), 'application/json')

    def events(self, code, role):
        if role not in ('deck', 'remote'):
            return self.send_error(400)
        s, q = HUB.session(code), queue.Queue()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.end_headers()
        self.close_connection = True
        with HUB.lock:
            s[role].add(q)
            state = dict(s['state'])
        HUB.announce(code)
        if role == 'remote' and state:
            q.put({**state, 't': 'state'})
        try:
            self.wfile.write(b'retry: 1500\n\n')
            self.wfile.flush()
            while True:
                try:
                    chunk = 'data: ' + json.dumps(q.get(timeout=5), ensure_ascii=False) + '\n\n'
                except queue.Empty:
                    chunk = ': ping\n\n'  # also how a closed tab is noticed
                self.wfile.write(chunk.encode('utf-8'))
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with HUB.lock:
                s[role].discard(q)
            HUB.announce(code)


REMOTE_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="theme-color" content="#111214"><title>演示遥控器</title>
<style>
:root{--bg:#111214;--panel:#1C1D21;--line:#2C2D33;--fg:#F2F2F4;--mute:#9C9DA6;--acc:#7DD3FC}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;overscroll-behavior:none}
body{display:flex;flex-direction:column;height:100dvh;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);-webkit-user-select:none;user-select:none}
button{font:inherit;color:inherit;cursor:pointer}
header{display:flex;align-items:center;gap:10px;padding:12px 16px}
.dot{flex:none;width:10px;height:10px;border-radius:50%;background:#FF453A}
.dot.wait{background:#FFD60A}.dot.on{background:#30D158}
.ttl{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:15px;color:var(--mute)}
.pg{font:600 20px/1 ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}
.tm{padding:7px 9px;border:0;border-radius:8px;background:var(--panel);font:500 15px/1 ui-monospace,Menlo,monospace;color:var(--mute)}
.wrap{flex:1;min-height:0;display:flex;flex-direction:column}
.stage{position:relative;flex:none;margin:0 16px;aspect-ratio:16/9;border-radius:12px;overflow:hidden;background:#000;box-shadow:0 0 0 1px var(--line);touch-action:none}
.stage iframe{position:absolute;inset:0;width:100%;height:100%;border:0;pointer-events:none}
.stage canvas{position:absolute;inset:0;width:100%;height:100%}
.stage .tip{position:absolute;inset:0;display:grid;place-items:center;padding:12px;color:var(--mute);font-size:15px;text-align:center;pointer-events:none}
.side{flex:1;min-height:0;display:flex;flex-direction:column}
.modes{display:flex;gap:4px;margin:12px 16px 0;padding:4px;border-radius:12px;background:var(--panel)}
.modes button{flex:1;height:40px;border:0;border-radius:9px;background:none;color:var(--mute);font-size:15px;font-weight:500}
.modes button.on{background:#34353C;color:var(--fg)}
main{flex:1;min-height:0;display:flex;flex-direction:column;gap:12px;padding:12px 16px 16px}
section{flex:1;min-height:0;display:none;flex-direction:column;gap:12px}
section.on{display:flex}
.notes{flex:1;min-height:80px;overflow:auto;padding:14px 16px;border-radius:12px;background:var(--panel);font-size:var(--nfs,18px);line-height:1.7;white-space:pre-wrap;-webkit-user-select:text;user-select:text}
.row{display:flex;gap:10px}
.row button{flex:1;height:48px;border:0;border-radius:12px;background:var(--panel);font-size:15px;font-weight:500}
.row button.on{background:var(--fg);color:var(--bg)}
.ctl{display:grid;grid-template-columns:1fr 2fr;gap:10px}
.ctl button{height:64px;border:0;border-radius:14px;background:var(--panel);font-size:18px;font-weight:600}
.ctl .nx{background:#E8F7FF;color:#0B1D2A}
.ctl button:active,.row button:active{transform:scale(.97)}
.hint{color:var(--mute);font-size:15px;text-align:center}
.sw{display:flex;justify-content:center;gap:14px}
.sw button{width:38px;height:38px;border-radius:50%;border:2px solid rgba(255,255,255,.25)}
.sw button.sel{box-shadow:0 0 0 2px var(--bg),0 0 0 4px var(--fg)}
.grid{flex:1;min-height:0;overflow:auto;display:grid;grid-template-columns:1fr 1fr;gap:8px;align-content:start}
.grid button{display:flex;gap:8px;align-items:flex-start;min-height:64px;padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--panel);font-size:15px;line-height:1.4;text-align:left}
.grid button b{flex:none;font:600 15px/1.4 ui-monospace,Menlo,monospace;color:var(--mute)}
.grid button.cur{border-color:var(--acc)}.grid button.cur b{color:var(--acc)}
#pair{flex:1;display:none;flex-direction:column;justify-content:center;gap:16px;padding:24px}
#pair h1{font-size:24px}#pair p{color:var(--mute)}
#pair input{height:56px;padding:0 16px;border:1px solid var(--line);border-radius:12px;background:var(--panel);color:var(--fg);font:600 24px/1 ui-monospace,Menlo,monospace;letter-spacing:.3em;text-align:center}
#pair button{height:52px;border:0;border-radius:12px;background:#E8F7FF;color:#0B1D2A;font-size:17px;font-weight:600}
body.unpaired #pair{display:flex}body.unpaired .wrap,body.unpaired header{display:none}
@media (orientation:landscape) and (min-width:560px){
  .wrap{flex-direction:row}.stage{flex:1.25;align-self:flex-start;margin:0 0 16px 16px}.side{flex:1}.modes{margin-top:0}}
</style></head>
<body>
<header><span class="dot" id="dot"></span><span class="ttl" id="ttl">正在连接…</span><span class="pg" id="pg">-- / --</span><button class="tm" id="tm" title="点按归零">00:00</button></header>
<div class="wrap">
  <div class="stage" id="stage"><div id="pv"></div><canvas id="cv"></canvas><div class="tip" id="tip"></div></div>
  <div class="side">
    <nav class="modes" id="modes"><button data-m="page" class="on">翻页</button><button data-m="laser">激光笔</button><button data-m="pen">画笔</button><button data-m="list">总览</button></nav>
    <main>
      <section data-m="page" class="on"><div class="notes" id="notes"></div>
        <div class="row"><button id="blank">黑屏</button><button id="fm" aria-label="缩小讲稿字号">A−</button><button id="fp" aria-label="放大讲稿字号">A+</button><button id="pvb" class="on">预览</button></div></section>
      <section data-m="laser"><p class="hint">在上方画面里按住并移动手指，大屏幕上会出现红点，松手后消失。</p></section>
      <section data-m="pen"><div class="sw" id="sw"></div><div class="row"><button id="undo">撤销</button><button id="clear">清空本页</button></div>
        <p class="hint">在上方画面里书写，大屏幕同步显示。</p></section>
      <section data-m="list"><div class="grid" id="grid"></div></section>
      <div class="ctl"><button id="prev">‹ 上一页</button><button class="nx" id="next">下一页 ›</button></div>
    </main>
  </div>
</div>
<div id="pair"><h1>演示遥控器</h1><p>在电脑上的演示里按 M，用手机相机扫描二维码即可连接；也可以输入电脑上显示的 6 位配对码。</p>
  <input id="code" inputmode="numeric" maxlength="6" autocomplete="off" placeholder="000000"><button id="go">连接</button></div>
<script>
const $=s=>document.querySelector(s);
const store=(k,v)=>{try{if(v===undefined)return localStorage.getItem('remote-'+k);localStorage.setItem('remote-'+k,v)}catch(e){}};
let code=(location.hash.match(/\d{6}/)||[])[0],st={},mode='page',deckOn=false,es=null,pvOn=store('pv')!=='off',color='#FF3B30',t0=Date.now();
const COLORS=['#FF3B30','#FFCC00','#0A84FF','#34C759','#FFFFFF','#111111'];
const pad=n=>String(n).padStart(2,'0'),buzz=()=>navigator.vibrate&&navigator.vibrate(8);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* 指令按顺序逐条发送；连续的激光位置只发最新的，连续的笔迹合并成一条 */
const Q=[];let busy=false;
function send(m){const l=Q[Q.length-1];
  if(l&&m.t==='laser'&&!m.off&&l.t==='laser'&&!l.off)Q[Q.length-1]=m;
  else if(l&&m.t==='pen'&&m.a==='move'&&l.t==='pen'&&l.a==='move')l.pts.push(...m.pts);
  else Q.push(m);pump()}
function pump(){if(busy||!Q.length)return;busy=true;const m=Q.shift();
  fetch(`/api/s/${code}/cmd`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(m)}).catch(()=>{}).finally(()=>{busy=false;pump()})}
const key=k=>{buzz();send({t:'key',k})};

function connect(){document.body.classList.remove('unpaired');history.replaceState(null,'','#'+code);
  es=new EventSource(`/api/s/${code}/events?role=remote`);
  es.onmessage=e=>{const m=JSON.parse(e.data);if(m.t==='state'){const deckChanged=m.deck!==st.deck;st=m;render(deckChanged)}else if(m.t==='deck'){deckOn=m.online;status()}};
  es.onerror=status;es.onopen=status}
function status(){const ok=es&&es.readyState===1;$('#dot').className='dot'+(ok&&deckOn?' on':ok?' wait':'');
  if(!ok)$('#ttl').textContent='连接中断，正在重连…';else if(!deckOn)$('#ttl').textContent='等待电脑上的演示…';else $('#ttl').textContent=st.title||'演示'}

/* ── 画面预览：同一份演示的独立实例，只换页码 ── */
function preview(reload){const box=$('#pv');
  if(!pvOn||!st.deck){box.innerHTML='';$('#tip').textContent=pvOn?'':'预览已关闭';return}
  $('#tip').textContent='';let f=box.querySelector('iframe');const src=`${st.deck}?presenter-preview#${st.page}`;
  if(!f||reload){box.innerHTML='';f=document.createElement('iframe');f.tabIndex=-1;box.appendChild(f)}
  if(f.dataset.src!==src){f.src=src;f.dataset.src=src}}

/* ── 本机笔迹：只用来看自己写了什么，大屏幕上的批注由演示负责 ── */
const cv=$('#cv'),cx=cv.getContext('2d'),ink={};let curStroke=null,dot=null,raf=0;
const mine=()=>ink[st.page]||(ink[st.page]=[]);
function fit(){const r=cv.getBoundingClientRect(),k=Math.min(2,devicePixelRatio||1);cv.width=r.width*k;cv.height=r.height*k;draw()}
function draw(){raf=0;const W=cv.width,H=cv.height;cx.clearRect(0,0,W,H);cx.lineCap=cx.lineJoin='round';
  for(const s of ink[st.page]||[]){cx.strokeStyle=s.c;cx.lineWidth=Math.max(2,W/1920*6);cx.beginPath();s.pts.forEach(([x,y],i)=>i?cx.lineTo(x*W,y*H):cx.moveTo(x*W,y*H));if(s.pts.length===1)cx.lineTo(s.pts[0][0]*W+.5,s.pts[0][1]*H);cx.stroke()}
  if(dot){const g=cx.createRadialGradient(dot[0]*W,dot[1]*H,0,dot[0]*W,dot[1]*H,18);g.addColorStop(0,'rgba(255,60,60,.95)');g.addColorStop(1,'rgba(255,0,0,0)');cx.fillStyle=g;cx.beginPath();cx.arc(dot[0]*W,dot[1]*H,18,0,7);cx.fill()}}
const later=()=>{if(!raf)raf=requestAnimationFrame(draw)};

function render(deckChanged){status();const n=st.n||0;$('#pg').textContent=pad(st.page||0)+' / '+pad(n);
  $('#notes').textContent=(st.notes||[])[st.page-1]||'（本页暂无备注）';
  $('#blank').classList.toggle('on',!!st.blank);$('#blank').textContent=st.blank?'恢复画面':'黑屏';
  const T=st.titles||[];$('#grid').innerHTML=Array.from({length:n},(_,i)=>`<button data-n="${i+1}" class="${i+1===st.page?'cur':''}"><b>${pad(i+1)}</b><span>${esc(T[i]||'')}</span></button>`).join('');
  preview(deckChanged);later()}

/* ── 触控：翻页模式左右滑动翻页，激光笔和画笔模式在预览画面上操作 ── */
const stage=$('#stage');let sx=0,sy=0;
const norm=e=>{const r=stage.getBoundingClientRect();return[Math.min(1,Math.max(0,(e.clientX-r.left)/r.width)),Math.min(1,Math.max(0,(e.clientY-r.top)/r.height))]};
stage.addEventListener('pointerdown',e=>{stage.setPointerCapture(e.pointerId);const p=norm(e);sx=e.clientX;sy=e.clientY;
  if(mode==='laser'){dot=p;send({t:'laser',x:p[0],y:p[1]});later()}
  else if(mode==='pen'){curStroke={c:color,pts:[p]};mine().push(curStroke);send({t:'pen',a:'down',c:color,pts:[p]});later()}});
stage.addEventListener('pointermove',e=>{if(!stage.hasPointerCapture(e.pointerId))return;const p=norm(e);
  if(mode==='laser'){dot=p;send({t:'laser',x:p[0],y:p[1]});later()}
  else if(mode==='pen'&&curStroke){curStroke.pts.push(p);send({t:'pen',a:'move',pts:[p]});later()}});
const up=e=>{if(mode==='laser'){dot=null;send({t:'laser',off:1});later()}
  else if(mode==='pen'&&curStroke){curStroke=null;send({t:'pen',a:'up',pts:[]})}
  else if(mode==='page'){const dx=e.clientX-sx,dy=e.clientY-sy;if(Math.abs(dx)>40&&Math.abs(dx)>Math.abs(dy))key(dx<0?'ArrowRight':'ArrowLeft')}};
stage.addEventListener('pointerup',up);stage.addEventListener('pointercancel',up);

/* ── 按钮 ── */
$('#modes').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;mode=b.dataset.m;
  document.querySelectorAll('#modes button').forEach(x=>x.classList.toggle('on',x===b));
  document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.dataset.m===mode));
  $('#tip').textContent=mode==='laser'?'在这里按住移动':mode==='pen'?'在这里书写':pvOn?'':'预览已关闭';
  if(mode==='list'){const c=$('#grid .cur');if(c)c.scrollIntoView({block:'nearest'})}});
$('#next').onclick=()=>key('ArrowRight');$('#prev').onclick=()=>key('ArrowLeft');
$('#blank').onclick=()=>{buzz();send({t:'blank',m:st.blank?'':'b'})};
$('#grid').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;buzz();send({t:'go',n:+b.dataset.n});$('#modes button[data-m=page]').click()});
$('#sw').innerHTML=COLORS.map(c=>`<button data-c="${c}" style="background:${c}" aria-label="笔迹颜色"></button>`).join('');
const pick=c=>{color=c;document.querySelectorAll('#sw button').forEach(b=>b.classList.toggle('sel',b.dataset.c===c))};pick(color);
$('#sw').addEventListener('click',e=>{const b=e.target.closest('button');if(b)pick(b.dataset.c)});
$('#undo').onclick=()=>{buzz();mine().pop();later();send({t:'undo'})};
$('#clear').onclick=()=>{buzz();ink[st.page]=[];later();send({t:'clear'})};
let nfs=+(store('nfs')||18);const setFs=()=>document.documentElement.style.setProperty('--nfs',nfs+'px');setFs();
$('#fp').onclick=()=>{nfs=Math.min(34,nfs+2);store('nfs',nfs);setFs()};$('#fm').onclick=()=>{nfs=Math.max(14,nfs-2);store('nfs',nfs);setFs()};
$('#pvb').classList.toggle('on',pvOn);$('#pvb').onclick=()=>{pvOn=!pvOn;store('pv',pvOn?'on':'off');$('#pvb').classList.toggle('on',pvOn);preview(true)};
$('#tm').onclick=()=>{t0=Date.now()};
setInterval(()=>{const s=Math.floor((Date.now()-t0)/1000);$('#tm').textContent=pad(Math.floor(s/60))+':'+pad(s%60)},500);
addEventListener('resize',fit);fit();
$('#go').onclick=()=>{const v=$('#code').value.replace(/\D/g,'');if(v.length===6){code=v;connect()}};
if(code)connect();else document.body.classList.add('unpaired');
</script></body></html>'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('path', nargs='?', type=Path, help='a deck HTML file or a folder of decks')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()
    here = Path(__file__).resolve().parent
    bundled = here.parent / 'assets' / 'templates'
    target = (args.path or (bundled if here.name == 'scripts' and bundled.is_dir() else here)).resolve()
    if target.is_file():
        directory, page = target.parent, quote(target.name)
    elif target.is_dir():
        directory, page = target, ''
    else:
        ap.error(f'Not found: {target}')
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(('0.0.0.0', port), partial(Handler, directory=str(directory)))
            break
        except OSError:
            continue
    else:
        ap.error('No free port found.')
    local = f'http://localhost:{port}/{page}'
    print(f'Presenting {directory}')
    print(f'  This computer: {local}')
    for ip in lan_ips():
        print(f'  Same Wi-Fi:    http://{ip}:{port}/{page}')
    print('  Phone remote:  open a deck, press M and scan the QR code. Ctrl+C stops the server.', flush=True)
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, [local]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
