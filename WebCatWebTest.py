from __future__ import annotations

from threading import Thread

from examples.web_test_server import Handler, ThreadingHTTPServer
from safehtmltk.app import App


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True, name="WebCat-test-server")
    thread.start()
    port = server.server_address[1]
    app = App(url=f"http://127.0.0.1:{port}/network-demo.html")
    original_close = app._on_close

    def close() -> None:
        try:
            original_close()
        finally:
            server.shutdown()
            server.server_close()

    app.root.protocol("WM_DELETE_WINDOW", close)
    app.run()


if __name__ == "__main__":
    main()
