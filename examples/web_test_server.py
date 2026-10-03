from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.split('?', 1)[0] == '/api/message':
            body = json.dumps({
                'engine': 'WebCat',
                'message': 'Hello from a real HTTP server.',
                'path': self.path,
            }).encode()
            self._send(200, body, 'application/json; charset=utf-8')
            return
        clean = self.path.split('?', 1)[0]
        files = {
            '/': ('network-demo.html', 'text/html; charset=utf-8'),
            '/network-demo.html': ('network-demo.html', 'text/html; charset=utf-8'),
            '/network-demo.css': ('network-demo.css', 'text/css; charset=utf-8'),
            '/network-demo.js': ('network-demo.js', 'text/javascript; charset=utf-8'),
        }
        if clean in files:
            filename, content_type = files[clean]
            self._send(200, (ROOT / filename).read_bytes(), content_type)
            return
        self._send(404, b'Not Found', 'text/plain; charset=utf-8')

    def log_message(self, fmt: str, *args) -> None:
        print('[web-test-server]', fmt % args)

if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
    print('WebCat test server: http://127.0.0.1:8765/network-demo.html')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
