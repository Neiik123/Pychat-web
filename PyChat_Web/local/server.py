#!/usr/bin/env python3
"""PyChat server - stdlib only. Run:  python server.py   (then it opens your browser)
Serves the UI, stores data in data.json, and proxies requests to Ollama / any API
(so there are no CORS problems and API keys never touch a third-party page)."""
import json, os, re, threading, webbrowser, urllib.request, urllib.parse, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

D = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(D, 'data.json')
PORT = int(os.environ.get('PORT', 8777))
VERSION = 6
if os.name == 'nt':
    ThreadingHTTPServer.allow_reuse_address = False   # Windows: never share a port with an old copy
lock = threading.Lock()


def OP(url):
    """Local/LAN addresses must never go through a system proxy (breaks Ollama on some Windows setups)."""
    h = urllib.parse.urlparse(url).hostname or ''
    local = h in ('localhost', '127.0.0.1', '::1') or h.startswith(('192.168.', '10.', '172.'))
    return urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _r(self, code, body, ct='application/json'):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code); self.send_header('Content-Type', ct)
        self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def _body(self):
        return self.rfile.read(int(self.headers.get('Content-Length', 0) or 0))

    def do_GET(self):
        if self.path.startswith('/api/info'):
            self._r(200, json.dumps({'v': VERSION, 'folder': D, 'pid': os.getpid()}))
        elif self.path.startswith('/api/db'):
            with lock:
                self._r(200, open(DB, 'rb').read() if os.path.exists(DB) else '{}')
        else:
            self._r(200, open(os.path.join(D, 'index.html'), 'rb').read(), 'text/html; charset=utf-8')

    def do_PUT(self):
        data = self._body()
        with lock:
            tmp = DB + '.tmp'
            open(tmp, 'wb').write(data); os.replace(tmp, DB)
        self._r(200, '{}')

    def do_POST(self):
        j = json.loads(self._body() or b'{}')
        if self.path == '/api/search':
            return self.search(j.get('q', ''))
        if self.path == '/api/models':
            return self.models(j.get('url', ''))
        if self.path == '/api/proxy':
            return self.proxy(j)
        self._r(404, '{}')

    def models(self, url):
        base = url.rstrip('/'); alts = [base]
        if 'localhost' in base: alts.append(base.replace('localhost', '127.0.0.1'))
        elif '127.0.0.1' in base: alts.append(base.replace('127.0.0.1', 'localhost'))
        err = ''
        for u in alts:
            try:
                d = json.loads(OP(u).open(u + '/api/tags', timeout=8).read())
                return self._r(200, json.dumps({'models': [m['name'] for m in d.get('models', [])], 'used': u}))
            except Exception as e:
                err = f'{u}: {e}'
        self._r(200, json.dumps({'error': err + ' - is Ollama running? Open the Ollama app, or run: ollama serve'}))

    def search(self, q):
        try:
            rq = urllib.request.Request('https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(q),
                                        headers={'User-Agent': 'Mozilla/5.0'})
            t = urllib.request.urlopen(rq, timeout=15).read().decode('utf8', 'ignore')
            clean = lambda s: re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s)).strip()
            tt = [clean(x) for x in re.findall(r'class="result__a"[^>]*>(.*?)</a>', t, re.S)]
            sn = [clean(x) for x in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', t, re.S)]
            out = '\n'.join(f'- {a}: {b}' for a, b in zip(tt, sn))[:3000]
            self._r(200, json.dumps({'text': out or 'No results.'}))
        except Exception as e:
            self._r(200, json.dumps({'text': f'Search failed: {e}'}))

    def proxy(self, j):
        body = j.get('body')
        rq = urllib.request.Request(j['url'], data=None if body is None else json.dumps(body).encode(),
                                    headers=j.get('headers', {}), method=j.get('method', 'POST'))
        import time
        for attempt in range(3):
            try:
                r = OP(j['url']).open(rq, timeout=900); code = r.status
                ct = r.headers.get('Content-Type', 'application/octet-stream'); break
            except urllib.error.HTTPError as e:
                r, code, ct = e, e.code, 'text/plain'; break
            except Exception as e:
                if attempt < 2 and 'closed' in str(e).lower():
                    time.sleep(3); continue   # Ollama often drops the first request while a model loads
                return self._r(502, f'Cannot reach {j["url"]} ({e}). Ollama is up but dropped the request - the model probably crashed (out of memory?). Check the Ollama window/logs, try a smaller model or lower the Context size in Model settings.', 'text/plain')
        self.send_response(code); self.send_header('Content-Type', ct)
        self.send_header('Connection', 'close'); self.end_headers()
        rd = getattr(r, 'read1', r.read)
        try:
            while True:
                c = rd(8192)
                if not c: break
                self.wfile.write(c); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == '__main__':
    os.chdir(D)  # Same as: cd /d "%~dp0" in run.bat
    try:
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
        except OSError:
            raise SystemExit(f'Port {PORT} is already in use - an OLD PyChat is probably still running. Close its window (or end python.exe in Task Manager) and start again, or run with PORT=8766.')
        url = f'http://127.0.0.1:{PORT}'
        import time
        print(f'PyChat running at {url}  (Ctrl+C to stop)')
        print('Serving folder:', D)
        print('index.html last saved:', time.ctime(os.path.getmtime(os.path.join(D, 'index.html'))))
        threading.Timer(.8, lambda: webbrowser.open(url)).start()
        try: srv.serve_forever()
        except KeyboardInterrupt: pass
    finally:
        if os.name == 'nt':
            os.system('pause')  # Same as the final "pause" in run.bat
