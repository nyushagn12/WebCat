from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .app import App
from .js_bridge import QuickJSNGWorker


class Launcher:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("WebCat")
        self.root.geometry("720x560")
        self.root.minsize(640, 470)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self._ui_queue: queue.Queue[tuple[object, tuple, dict]] = queue.Queue()
        self._ui_after: str | None = self.root.after(25, self._poll_ui_queue)
        self._build()

    def _post_ui(self, callback, *args, **kwargs) -> None:
        self._ui_queue.put((callback, args, kwargs))

    def _poll_ui_queue(self) -> None:
        self._ui_after = None
        while True:
            try:
                callback, args, kwargs = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args, **kwargs)
            except Exception as exc:
                messagebox.showerror("WebCat", str(exc), parent=self.root)
        try:
            if self.root.winfo_exists():
                self._ui_after = self.root.after(25, self._poll_ui_queue)
        except tk.TclError:
            self._ui_after = None

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg="#0f172a", padx=34, pady=30)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="WebCat", bg="#0f172a", fg="white", font=("TkDefaultFont", 30, "bold")).pack(anchor="w")
        tk.Label(outer, text="Python HTML + CSS renderer with isolated JavaScript and direct HTTP/HTTPS web loading.",
                 bg="#0f172a", fg="#cbd5e1", font=("TkDefaultFont", 12)).pack(anchor="w", pady=(5, 23))

        panel = tk.Frame(outer, bg="white", padx=24, pady=24)
        panel.pack(fill="both", expand=True)
        tk.Button(panel, text="Open HTML file", command=self.open_file, padx=18, pady=11).pack(fill="x", pady=5)
        tk.Button(panel, text="Open web URL", command=self.open_web, padx=18, pady=11).pack(fill="x", pady=5)
        tk.Button(panel, text="Open example.com", command=self.open_example_site, padx=18, pady=8).pack(fill="x", pady=3)
        tk.Button(panel, text="Run CSS showcase", command=self.open_css_showcase, padx=18, pady=11).pack(fill="x", pady=5)
        tk.Button(panel, text="Run JavaScript + fetch demo", command=self.open_demo, padx=18, pady=11).pack(fill="x", pady=5)
        tk.Button(panel, text="Launch WebCat web test", command=self.launch_web_test, padx=18, pady=11).pack(fill="x", pady=5)
        ttk.Separator(panel).pack(fill="x", pady=17)

        self.runtime_label = tk.Label(panel, text="", bg="white", font=("TkDefaultFont", 11, "bold"), anchor="w")
        self.runtime_label.pack(fill="x")
        self.runtime_detail = tk.Label(panel, text="", bg="white", fg="#4b5563", wraplength=600, justify="left", anchor="w")
        self.runtime_detail.pack(fill="x", pady=(5, 10))
        self._refresh_runtime_text()
        tk.Button(panel, text="Get verified quickjs-ng runtime (no pip)", command=self.install_runtime, padx=12, pady=8).pack(anchor="w")
        tk.Button(panel, text="About the security model", command=self.security_info).pack(anchor="w", pady=(10, 0))

    def _refresh_runtime_text(self) -> None:
        if QuickJSNGWorker.available():
            self.runtime_label.configure(text="JavaScript: quickjs-ng ready", fg="#166534")
            self.runtime_detail.configure(text="WebCat imports it as `quickjs` and runs each page in a separate worker process.")
        else:
            self.runtime_label.configure(text="JavaScript: runtime not installed", fg="#9a3412")
            self.runtime_detail.configure(text="HTML, CSS and web loading still work. The button below downloads a pinned official wheel, verifies SHA-256, and unpacks it into this project without using pip.")

    def open_file(self) -> None:
        path = filedialog.askopenfilename(title="Open local HTML", filetypes=[("HTML", "*.html *.htm"), ("All files", "*.*")])
        if path:
            self.launch(path)

    def open_web(self) -> None:
        self.root.destroy()
        App().run()

    def open_example_site(self) -> None:
        self.root.destroy()
        App(url="https://example.com/").run()

    def _example(self, name: str) -> str:
        return str(Path(__file__).resolve().parent.parent / "examples" / name)

    def open_demo(self) -> None:
        self.launch(self._example("demo.html"))

    def open_css_showcase(self) -> None:
        self.launch(self._example("css-showcase.html"))

    def launch_web_test(self) -> None:
        try:
            from examples.web_test_server import Handler, ThreadingHTTPServer
            from threading import Thread

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            Thread(target=server.serve_forever, daemon=True, name="WebCat-test-server").start()
            url = f"http://127.0.0.1:{server.server_address[1]}/network-demo.html"
            self.root.destroy()
            app = App(url=url)
            original_close = app._on_close

            def close() -> None:
                try:
                    original_close()
                finally:
                    server.shutdown()
                    server.server_close()

            app.root.protocol("WM_DELETE_WINDOW", close)
            app.run()
        except Exception as exc:
            messagebox.showerror("WebCat web test", f"Could not start the local test server.\n\n{exc}", parent=self.root)

    def web_test_info(self) -> None:
        messagebox.showinfo(
            "WebCat local web test",
            "1. Open a terminal in the WebCat project.\n\n"
            "2. Run:\n\npython examples/web_test_server.py\n\n"
            "3. Start WebCat and enter:\n\nhttp://127.0.0.1:8765/network-demo.html\n\n"
            "4. Click “Fetch from server”.\n\n"
            "That page exercises WebCat's own HTTP client, URL resolution, JSON response handling and fetch() Promise bridge.",
            parent=self.root,
        )

    def install_runtime(self) -> None:
        if QuickJSNGWorker.available():
            self._refresh_runtime_text()
            return

        def work():
            try:
                from .vendor.quickjs_ng.bootstrap_runtime import install_here
                install_here()
                ok = True
                err = ""
            except Exception as exc:
                ok = False
                err = str(exc)
            self._post_ui(self._runtime_finished, ok, err)

        self.runtime_label.configure(text="Downloading verified runtime…", fg="#92400e")
        threading.Thread(target=work, daemon=True, name="WebCat-runtime-download").start()

    def _runtime_finished(self, ok: bool, error: str) -> None:
        self._refresh_runtime_text()
        if ok:
            messagebox.showinfo("WebCat", "quickjs-ng has been installed into the WebCat project. Restart WebCat or reopen the page to enable JavaScript.", parent=self.root)
        else:
            messagebox.showerror("WebCat runtime", f"The vendored runtime could not be installed.\n\n{error}", parent=self.root)

    def security_info(self) -> None:
        messagebox.showinfo(
            "WebCat security model",
            "Page JavaScript runs in a separate Python process containing quickjs-ng. Only a small JSON protocol crosses between the worker and the main application.\n\n"
            "Network access is brokered by WebCat: page JS gets fetch(), not arbitrary Python sockets/files. HTTP/HTTPS only, no ambient proxy variables, TLS certificate verification, response-size limits, redirect limits and private-network filtering for page-initiated requests are enforced.\n\n"
            "This is defense-in-depth, not a claim of exploit immunity. Python, Tk/Tcl, the native quickjs-ng extension, the OS and the networking stack remain attack surface.",
            parent=self.root,
        )

    def launch(self, path: str) -> None:
        self.root.destroy()
        App(path).run()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    Launcher().run()
