"""Optional local Ollama bridge for the hosted PyChat Web application.

Binds to loopback only. Set PYCHAT_WEB_ORIGINS to the exact hosted website
origin(s), for example https://pychat-web.example.com.
"""
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = int(os.environ.get("PYCHAT_LOCAL_PORT", "8768"))
ALLOWED_ORIGINS = {x.strip().rstrip("/") for x in os.environ.get("PYCHAT_WEB_ORIGINS", "").split(",") if x.strip()}
MAX_BODY = 10 * 1024 * 1024


def validate_local_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only local HTTP(S) endpoints are allowed by the PyChat local connector.")
    host = parsed.hostname.lower().rstrip(".")
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        return value
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve the local endpoint.") from exc
    if not addresses:
        raise ValueError("Could not resolve the local endpoint.")
    for item in addresses:
        ip = ipaddress.ip_address(item[4][0].split("%", 1)[0])
        if not (ip.is_private or ip.is_loopback or ip.is_link_local):
            raise ValueError("The local connector only accepts private/LAN or loopback endpoints.")
    return value


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def cors(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        if not origin or origin not in ALLOWED_ORIGINS:
            return False
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        return True

    def do_OPTIONS(self):
        self.send_response(204)
        if not self.cors():
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length < 0 or length > MAX_BODY:
            raise ValueError("Request body is too large.")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    def send_json(self, status, value):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        allowed = self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        origin = self.headers.get("Origin", "").rstrip("/")
        if not origin or origin not in ALLOWED_ORIGINS:
            self.send_error(403, "This website origin is not allowed. Configure PYCHAT_WEB_ORIGINS.")
            return
        try:
            payload = self.read_json()
            base = validate_local_url(str(payload.get("url", "")))
            if self.path == "/api/models":
                target = base.rstrip("/") + "/api/tags"
                request = urllib.request.Request(target, headers={"Accept": "application/json"})
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=12) as response:
                    data = json.loads(response.read().decode("utf-8"))
                self.send_json(200, {"models": [m["name"] for m in data.get("models", []) if isinstance(m, dict) and "name" in m], "used": base})
                return
            if self.path != "/api/proxy":
                self.send_json(404, {"error": "Not found"})
                return
            method = str(payload.get("method", "POST")).upper()
            if method not in ("GET", "POST", "PUT", "DELETE"):
                self.send_json(405, {"error": "Unsupported method"})
                return
            headers = {str(k): str(v) for k, v in (payload.get("headers") or {}).items() if str(k).lower() not in ("host", "connection", "content-length", "transfer-encoding")}
            body = payload.get("body")
            raw = None if body is None else json.dumps(body).encode("utf-8")
            if raw and len(raw) > MAX_BODY:
                self.send_json(413, {"error": "Request body is too large"})
                return
            target = urllib.parse.urljoin(base.rstrip("/") + "/", str(payload.get("path", ""))) if payload.get("path") else str(payload.get("url", ""))
            validate_local_url(target)
            request = urllib.request.Request(target, data=raw, headers=headers, method=method)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                response = opener.open(request, timeout=900)
            except urllib.error.HTTPError as error:
                response = error
            self.send_response(getattr(response, "status", response.code))
            self.cors()
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            finally:
                response.close()
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            try:
                self.send_json(502, {"error": f"Local AI connection failed: {str(exc)[:240]}"})
            except (BrokenPipeError, ConnectionResetError):
                pass


if __name__ == "__main__":
    if not ALLOWED_ORIGINS:
        raise SystemExit("Set PYCHAT_WEB_ORIGINS to your exact hosted PyChat website origin first.")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"PyChat local connector listening on http://{HOST}:{PORT}")
    print("Allowed website origins:", ", ".join(sorted(ALLOWED_ORIGINS)))
    print("Keep this window open while using local Ollama from PyChat Web. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
