"""
Prospector — Servidor de desenvolvimento local
Serve o frontend estático na porta 8088 e faz proxy de /api/ para o backend Flask na porta 5000.
"""
import http.server
import urllib.request
import urllib.error
import os
import sys

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
BACKEND_URL = "http://127.0.0.1:5000"
PORT = 8088


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            # SPA fallback: se o arquivo não existir, serve index.html
            file_path = os.path.join(FRONTEND_DIR, self.path.lstrip("/"))
            if not os.path.exists(file_path) or os.path.isdir(file_path):
                self.path = "/index.html"
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_response(204)
            self.end_headers()

    def _proxy(self):
        target = BACKEND_URL + self.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # Monta headers para repassar ao backend
        headers = {}
        for key in ("Content-Type", "Authorization", "X-API-Key"):
            val = self.headers.get(key)
            if val:
                headers[key] = val

        try:
            req = urllib.request.Request(target, data=body, headers=headers, method=self.command)
            with urllib.request.urlopen(req, timeout=300) as resp:
                self.send_response(resp.status)
                for key, val in resp.headers.items():
                    if key.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(key, val)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            msg = f'{{"success": false, "error": {{"code": "PROXY_ERROR", "message": "{str(e)}"}}}}'
            self.wfile.write(msg.encode())

    def log_message(self, fmt, *args):
        # Silencia logs de arquivos estáticos, mostra só /api/
        if "/api/" in (args[0] if args else ""):
            print(f"[proxy] {fmt % args}")


if __name__ == "__main__":
    os.chdir(FRONTEND_DIR)
    print(f"Frontend: http://localhost:{PORT}")
    print(f"Backend proxy: /api/ → {BACKEND_URL}")
    print("Pressione Ctrl+C para parar.\n")
    with http.server.ThreadingHTTPServer(("", PORT), ProxyHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor encerrado.")
