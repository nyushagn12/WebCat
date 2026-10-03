from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import gzip

from safehtmltk.network import NetworkError, fetch, origin_of, resolve_url


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/hello':
            body = b'{"hello":"world"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/hello')
            self.end_headers()
        elif self.path == '/gzip':
            body = gzip.compress(b'compressed-ok')
            self.send_response(200)
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/cors':
            body = b'cors-ok'
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', 'http://origin.example')
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        if self.path == '/cors-preflight':
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', 'http://origin.example')
            self.send_header('Access-Control-Allow-Methods', 'POST')
            self.send_header('Access-Control-Allow-Headers', 'x-webcat-test')
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/cors-preflight':
            body = b'post-ok'
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', 'http://origin.example')
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def test_url_helpers():
    assert resolve_url('https://example.com/a/b', '../c') == 'https://example.com/c'
    assert origin_of('https://example.com/a') == 'https://example.com'


def test_http_fetch_and_redirect():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        url = f'http://127.0.0.1:{port}/hello'
        response = fetch(url, allow_private=True)
        assert response.status == 200
        assert response.text() == '{"hello":"world"}'
        redirected = fetch(f'http://127.0.0.1:{port}/redirect', allow_private=True)
        assert redirected.status == 200
        assert redirected.url.endswith('/hello')
        gzip_resp = fetch(f'http://127.0.0.1:{port}/gzip', allow_private=True)
        assert gzip_resp.text() == 'compressed-ok'
        cors_post = fetch(
            f'http://127.0.0.1:{port}/cors-preflight',
            origin='http://origin.example', method='POST',
            headers={'X-WebCat-Test': '1'}, body=b'hello',
            allow_private=True, require_cors=True, cors_mode='cors',
        )
        assert cors_post.text() == 'post-ok'
        try:
            fetch(
                f'http://127.0.0.1:{port}/cors-preflight',
                origin='http://origin.example', method='POST',
                headers={'X-WebCat-Test': '1'}, body=b'hello',
                allow_private=True, cors_mode='no-cors',
            )
        except NetworkError as exc:
            assert 'no-cors' in str(exc)
        else:
            raise AssertionError('non-simple no-cors request was not blocked')
    finally:
        server.shutdown()
        server.server_close()
