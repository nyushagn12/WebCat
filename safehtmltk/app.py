from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import queue
from pathlib import Path
import ipaddress
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit, urlunsplit

from .css import Stylesheet
from .dom import Document
from .js_bridge import JSConfig, QuickJSNGWorker
from .network import NetworkError, WebResponse, fetch as fetch_url, origin_of, resolve_url, same_origin
from .parser import PageAssets, parse_document
from .renderer import Renderer


MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_ASSET_BYTES = 4 * 1024 * 1024
MAX_NETWORK_ASSET_BYTES = 4 * 1024 * 1024


class App:
    def __init__(self, html_path: str | None = None, js_path: str | None = None,
                 qjs: str | None = None, no_js: bool = False, url: str | None = None):
        # qjs is accepted for API compatibility; quickjs-ng is now the managed backend.
        self.html_path = Path(html_path).resolve() if html_path else None
        self.js_path = Path(js_path).resolve() if js_path else None
        self.page_url = url or (self.html_path.as_uri() if self.html_path else None)
        self.no_js = no_js
        self.root = tk.Tk()
        self.root.title("WebCat")
        self.root.geometry("1180x800")
        self.root.minsize(820, 560)
        self.renderer: Renderer | None = None
        self.document: Document | None = None
        self.worker: QuickJSNGWorker | None = None
        self._timer_handles: dict[int, str] = {}
        self._interval_active: set[int] = set()
        self._pump_after: str | None = None
        self._net = ThreadPoolExecutor(max_workers=4, thread_name_prefix="WebCat-net")
        self._ui_queue: queue.Queue[tuple[object, tuple, dict]] = queue.Queue()
        self._ui_after: str | None = None
        self._page_generation = 0
        self._loading = False
        self._explicit_local_hosts: set[str] = set()
        self._history: list[str] = []
        self._history_index = -1
        self.status = tk.StringVar(value="Enter an URL or open an HTML file")
        self.page_title = tk.StringVar(value="WebCat")
        self.js_status = tk.StringVar(value="JavaScript: checking…")
        self.address = tk.StringVar(value=self.page_url or "")
        self._build_chrome()
        self.root.bind("<Control-o>", lambda _e: self.open_file())
        self.root.bind("<Control-l>", lambda _e: self.focus_address())
        self.root.bind("<Control-r>", lambda _e: self.reload())
        self.root.bind("<F5>", lambda _e: self.reload())
        self.root.bind("<Alt-Left>", lambda _e: self.go_back())
        self.root.bind("<Alt-Right>", lambda _e: self.go_forward())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._ui_after = self.root.after(25, self._poll_ui_queue)

        if self.page_url and self.page_url.startswith(("http://", "https://")):
            self._navigate(self.page_url, add_history=True, user_initiated=True)
        elif self.html_path:
            self.load_file(self.html_path, add_history=True)
        else:
            self._show_welcome()

    # ------------------------------------------------------------------
    # Chrome / controls
    # ------------------------------------------------------------------

    def _build_chrome(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        top = ttk.Frame(self.root, padding=(8, 7))
        top.pack(fill="x")
        ttk.Button(top, text="←", width=3, command=self.go_back).pack(side="left", padx=(0, 3))
        ttk.Button(top, text="→", width=3, command=self.go_forward).pack(side="left", padx=(0, 6))
        ttk.Button(top, text="↻", width=3, command=self.reload).pack(side="left", padx=(0, 6))
        entry = ttk.Entry(top, textvariable=self.address)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        entry.bind("<Return>", lambda _e: self.go_address())
        self.address_entry = entry
        ttk.Button(top, text="Go", command=self.go_address).pack(side="left", padx=(0, 7))
        ttk.Button(top, text="Open HTML", command=self.open_file).pack(side="left", padx=3)

        second = ttk.Frame(self.root, padding=(8, 0, 8, 7))
        second.pack(fill="x")
        ttk.Button(second, text="Demo", command=self.open_demo).pack(side="left", padx=(0, 4))
        ttk.Button(second, text="CSS showcase", command=self.open_css_showcase).pack(side="left", padx=4)
        ttk.Button(second, text="Logs", command=self.toggle_logs).pack(side="left", padx=4)
        ttk.Button(second, text="JavaScript runtime", command=self.js_setup).pack(side="left", padx=4)
        ttk.Label(second, textvariable=self.page_title, font=("TkDefaultFont", 11, "bold")).pack(side="left", padx=14)
        ttk.Label(second, textvariable=self.js_status, anchor="e").pack(side="right", padx=4)

        self.main = ttk.Frame(self.root)
        self.main.pack(fill="both", expand=True)
        self.view = ttk.Frame(self.main)
        self.view.pack(fill="both", expand=True)

        self.log_frame = ttk.Frame(self.main, padding=(8, 4))
        self.log_text = tk.Text(self.log_frame, height=7, wrap="word", state="disabled", bg="#111827", fg="#e5e7eb")
        self.log_text.pack(fill="both", expand=True)
        self.logs_visible = False

        ttk.Frame(self.root, height=1).pack(fill="x")
        status_bar = ttk.Frame(self.root, padding=(8, 4))
        status_bar.pack(fill="x")
        ttk.Label(status_bar, textvariable=self.status, anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Label(status_bar, text="Ctrl+L address  •  Ctrl+O file  •  Ctrl+R/F5 reload", foreground="#6b7280").pack(side="right")

    def focus_address(self) -> None:
        self.address_entry.focus_set()
        self.address_entry.selection_range(0, tk.END)

    def go_address(self) -> None:
        raw = self.address.get().strip()
        if not raw:
            return
        if "://" not in raw and not raw.startswith("file:"):
            raw = "https://" + raw
        self._navigate(raw, add_history=True, user_initiated=True)

    def go_back(self) -> None:
        if self._history_index <= 0:
            return
        self._history_index -= 1
        self._navigate(self._history[self._history_index], add_history=False, user_initiated=True)

    def go_forward(self) -> None:
        if self._history_index >= len(self._history) - 1:
            return
        self._history_index += 1
        self._navigate(self._history[self._history_index], add_history=False, user_initiated=True)

    # ------------------------------------------------------------------
    # Welcome / examples
    # ------------------------------------------------------------------

    def _clear_view(self) -> None:
        if self.renderer:
            try:
                self.renderer.destroy()
            except Exception:
                pass
            self.renderer = None
        for child in list(self.view.winfo_children()):
            try:
                child.destroy()
            except tk.TclError:
                pass

    def _show_welcome(self) -> None:
        self._clear_view()
        box = tk.Frame(self.view, bg="#f8fafc", padx=48, pady=56)
        box.pack(fill="both", expand=True)
        tk.Label(box, text="WebCat", bg="#f8fafc", fg="#0f172a", font=("TkDefaultFont", 32, "bold")).pack(pady=(0, 8))
        tk.Label(box, text="A small Python web engine: HTML + CSS are rendered natively, JavaScript runs in isolated quickjs-ng.",
                 bg="#f8fafc", fg="#475569", font=("TkDefaultFont", 13)).pack(pady=(0, 24))
        tk.Button(box, text="Open an HTML file", command=self.open_file, padx=22, pady=10).pack(pady=6)
        tk.Button(box, text="Open a web URL", command=self._welcome_web, padx=22, pady=10).pack(pady=6)
        tk.Button(box, text="Open example.com", command=lambda: self._navigate("https://example.com/", add_history=True, user_initiated=True), padx=22, pady=8).pack(pady=4)
        tk.Button(box, text="Run the CSS showcase", command=self.open_css_showcase, padx=22, pady=10).pack(pady=6)
        tk.Button(box, text="Run the JavaScript + fetch demo", command=self.open_demo, padx=22, pady=10).pack(pady=6)
        detail = ("JavaScript: quickjs-ng available (imported as quickjs)."
                  if QuickJSNGWorker.available() else
                  "JavaScript: runtime not found. Use JavaScript runtime to install a pinned vendored wheel without pip.")
        tk.Label(box, text=detail, bg="#f8fafc", fg="#64748b", wraplength=720, justify="center").pack(pady=(22, 0))

    def _welcome_web(self) -> None:
        self.focus_address()
        self.address.set("https://")

    def open_demo(self) -> None:
        demo = Path(__file__).resolve().parent.parent / "examples" / "demo.html"
        self.load_file(demo, add_history=True)

    def open_css_showcase(self) -> None:
        path = Path(__file__).resolve().parent.parent / "examples" / "css-showcase.html"
        self.load_file(path, add_history=True)

    # ------------------------------------------------------------------
    # Runtime setup
    # ------------------------------------------------------------------

    def js_setup(self) -> None:
        if QuickJSNGWorker.available():
            messagebox.showinfo(
                "WebCat JavaScript",
                "quickjs-ng is ready.\n\nWebCat imports the module as `quickjs` and runs page JavaScript in a separate child process.\n\nThe project also supports a pinned vendored wheel under vendor/pydeps/.",
                parent=self.root,
            )
            return
        messagebox.showinfo(
            "WebCat JavaScript runtime",
            "No quickjs-ng runtime is currently available.\n\nUse the WebCat launcher\'s \"Get verified quickjs-ng runtime (no pip)\" button, or run:\n\npython -m safehtmltk.vendor.quickjs_ng.bootstrap_runtime\n\nWebCat downloads only a pinned official wheel and verifies its SHA-256 before unpacking it.",
            parent=self.root,
        )

    # ------------------------------------------------------------------
    # Local documents
    # ------------------------------------------------------------------

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Open local HTML",
            filetypes=[("HTML", "*.html *.htm"), ("All files", "*.*")],
        )
        if path:
            self.load_file(Path(path).resolve(), add_history=True)

    def reload(self) -> None:
        if self.html_path and self.page_url and self.page_url.startswith("file:"):
            self.load_file(self.html_path, add_history=False)
        elif self.page_url:
            self._navigate(self.page_url, add_history=False, user_initiated=True, reload=True)

    def _read_bytes(self, path: Path, limit: int) -> bytes:
        data = path.read_bytes()
        if len(data) > limit:
            raise ValueError(f"{path.name} is larger than the WebCat limit of {limit // (1024 * 1024)} MiB")
        return data

    def _read_text(self, path: Path, limit: int) -> str:
        return self._read_bytes(path, limit).decode("utf-8-sig", errors="replace")

    def _local_asset(self, base: Path, ref: str) -> Path | None:
        ref = ref.strip().replace("\\", "/")
        if not ref or ref.startswith(("http://", "https://", "//", "data:", "javascript:")):
            return None
        candidate = (base / ref.split("#", 1)[0].split("?", 1)[0]).resolve()
        try:
            candidate.relative_to(base.resolve())
        except ValueError:
            self._log(f"Blocked local asset outside page directory: {ref}")
            return None
        return candidate if candidate.is_file() else None

    # ------------------------------------------------------------------
    # Web documents
    # ------------------------------------------------------------------

    def _navigate(self, target: str, *, add_history: bool, user_initiated: bool, replace: bool = False,
                  reload: bool = False) -> None:
        target = target.strip()
        if target.startswith("file:"):
            from urllib.parse import unquote
            p = urlsplit(target)
            if p.netloc not in {"", "localhost"}:
                messagebox.showerror("WebCat", "Only local file:// URLs are supported.", parent=self.root)
                return
            path = Path(unquote(p.path)).resolve()
            self.load_file(path, add_history=add_history, history_replace=replace)
            return
        if not target.startswith(("http://", "https://")):
            target = "https://" + target
        try:
            parts = urlsplit(target)
            if parts.username or parts.password or not parts.hostname:
                raise NetworkError("Invalid web URL")
        except ValueError as exc:
            messagebox.showerror("WebCat", str(exc), parent=self.root)
            return

        if user_initiated:
            host = (urlsplit(target).hostname or "").lower()
            try:
                ip = ipaddress.ip_address(host)
                if not ip.is_global:
                    self._explicit_local_hosts.add(host)
            except ValueError:
                if host == "localhost":
                    self._explicit_local_hosts.add(host)

        if add_history:
            if replace and self._history:
                self._history[self._history_index] = target
            else:
                self._history = self._history[: self._history_index + 1]
                self._history.append(target)
                self._history_index = len(self._history) - 1

        self._begin_page_load(target)
        generation = self._page_generation
        host = (urlsplit(target).hostname or "").lower()
        allow_private = False
        if user_initiated:
            try:
                ip = ipaddress.ip_address(host)
                allow_private = not ip.is_global
            except ValueError:
                allow_private = host == "localhost"
        self.status.set(f"Loading {target} …")
        self.address.set(target)
        self._loading = True

        future = self._net.submit(
            fetch_url,
            target,
            timeout=15.0,
            allow_private=allow_private,
        )
        future.add_done_callback(lambda f, gen=generation, url=target: self._post_ui(self._web_loaded, gen, url, f))

    def _begin_page_load(self, target: str) -> None:
        self._page_generation += 1
        self._stop_js()
        self._clear_view()
        self.page_url = target
        self.html_path = None

    def _web_loaded(self, generation: int, requested_url: str, future: Future) -> None:
        if generation != self._page_generation:
            return
        self._loading = False
        try:
            response: WebResponse = future.result()
            content_type = response.content_type
            if response.status < 200 or response.status >= 400:
                raise NetworkError(f"Server returned HTTP {response.status} {response.reason}".strip())
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type and not requested_url.lower().split("?", 1)[0].endswith((".html", ".htm", "/")):
                raise NetworkError(f"WebCat expected an HTML document, got {content_type or 'unknown content type'}")
            self._load_document_text(
                response.text(),
                base_dir=None,
                page_url=response.url,
                response_url=response.url,
                remote=True,
                source_label=response.url,
            )
        except Exception as exc:
            self.page_title.set("WebCat")
            self.status.set("Web request failed")
            self.js_status.set("JavaScript: —")
            self._log(f"Web load error: {exc}")
            self._show_error_page(requested_url, str(exc))

    def _show_error_page(self, url: str, error: str) -> None:
        html = f"""<!doctype html><html><head><title>WebCat error</title><style>
        body{{font-family:sans-serif;background:#f8fafc;color:#0f172a;padding:40px}}
        .box{{max-width:780px;margin:auto;background:white;border:1px solid #cbd5e1;border-radius:12px;padding:28px}}
        code{{white-space:pre-wrap}}
        </style></head><body><div class=box><h1>Could not load page</h1><p>{escape(url)}</p><p><code>{escape(error)}</code></p></div></body></html>"""
        try:
            document, css, _assets = parse_document(html)
            self.document = document
            self.renderer = Renderer(self.view, document, Stylesheet(css), self._event)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Document and assets
    # ------------------------------------------------------------------

    def _load_assets(self, base_dir: Path | None, base_url: str, assets: PageAssets, *, remote: bool, load_js: bool = True) -> tuple[str, str]:
        css = ""
        js_parts: list[str] = []
        explicit_local = urlsplit(base_url).hostname or ""
        same_site_private = explicit_local in self._explicit_local_hosts

        for href in assets.stylesheets:
            if remote:
                try:
                    url = resolve_url(base_url, href)
                    if url.startswith(("http://", "https://")):
                        resp = fetch_url(url, timeout=15.0, allow_private=same_site_private)
                        if resp.status < 400:
                            css += "\n" + resp.text()[:MAX_NETWORK_ASSET_BYTES]
                        else:
                            self._log(f"CSS HTTP {resp.status}: {url}")
                except Exception as exc:
                    self._log(f"CSS web asset error: {href}: {exc}")
            elif base_dir is not None:
                path = self._local_asset(base_dir, href)
                if path:
                    try:
                        css += "\n" + self._read_text(path, MAX_ASSET_BYTES)
                    except Exception as exc:
                        self._log(f"CSS asset error: {path.name}: {exc}")

        if not load_js:
            return css, ""

        for src in assets.scripts:
            if remote:
                try:
                    url = resolve_url(base_url, src)
                    if url.startswith(("http://", "https://")):
                        resp = fetch_url(url, timeout=15.0, allow_private=same_site_private)
                        if resp.status < 400:
                            js_parts.append(resp.text()[:MAX_NETWORK_ASSET_BYTES])
                        else:
                            self._log(f"JS HTTP {resp.status}: {url}")
                except Exception as exc:
                    self._log(f"JS web asset error: {src}: {exc}")
            elif base_dir is not None:
                path = self._local_asset(base_dir, src)
                if path:
                    try:
                        js_parts.append(self._read_text(path, MAX_ASSET_BYTES))
                    except Exception as exc:
                        self._log(f"JS asset error: {path.name}: {exc}")

        if self.js_path:
            try:
                self.js_path = self.js_path.resolve()
                js_parts.append(self._read_text(self.js_path, MAX_ASSET_BYTES))
            except Exception as exc:
                self._log(f"Extra JS error: {exc}")
        return css, "\n".join(js_parts)

    def _load_document_text(self, source: str, *, base_dir: Path | None, page_url: str,
                            response_url: str, remote: bool, source_label: str,
                            add_history: bool = False, history_replace: bool = False) -> None:
        self._stop_js()
        self._clear_view()
        try:
            document, inline_css, assets = parse_document(source)
            load_js = not self.no_js and bool(QuickJSNGWorker.available())
            external_css, external_js = self._load_assets(base_dir, response_url, assets, remote=remote, load_js=load_js)
            css_source = inline_css + "\n" + external_css
            js_source = assets.inline_script + "\n" + external_js
        except Exception as exc:
            self.status.set("Could not parse document")
            self._log(f"Parse error: {exc}")
            self._show_error_page(source_label, str(exc))
            return

        self.document = document
        self.page_url = response_url
        self.address.set(response_url)
        title = assets.title or source_label
        self.page_title.set(title)
        self.root.title(f"{title} — WebCat")
        self.renderer = Renderer(self.view, document, Stylesheet(css_source), self._event)
        self.status.set(f"Rendered {source_label}  •  HTML/CSS native renderer")
        self._log(f"Loaded {source_label}")

        if self.no_js or not js_source.strip():
            self.js_status.set("JavaScript: off" if self.no_js else "JavaScript: none")
            return

        if not QuickJSNGWorker.available():
            self.js_status.set("JavaScript: unavailable")
            self._log("JavaScript not started: quickjs-ng runtime unavailable")
            return

        try:
            self.worker = QuickJSNGWorker(JSConfig())
            self.worker.start(document.snapshot(), js_source, location=response_url)
            self.js_status.set("JavaScript: isolated quickjs-ng worker")
            self._drain_js()
            self._schedule_pump()
            self.status.set(f"Rendered {source_label}  •  JS isolated + web fetch broker active")
        except Exception as exc:
            self.worker = None
            self.js_status.set("JavaScript: startup failed")
            self._log(f"JavaScript startup error: {exc}")

    def load_file(self, path: Path, *, add_history: bool = False, history_replace: bool = False) -> None:
        if not path.is_file():
            messagebox.showerror("WebCat", f"File not found:\n{path}", parent=self.root)
            return
        self._page_generation += 1
        self._stop_js()
        self._clear_view()
        try:
            source = self._read_text(path, MAX_HTML_BYTES)
        except Exception as exc:
            messagebox.showerror("WebCat", f"Could not open:\n{path}\n\n{exc}", parent=self.root)
            return
        self.html_path = path.resolve()
        self.page_url = self.html_path.as_uri()
        if add_history:
            value = self.page_url
            if history_replace and self._history:
                self._history[self._history_index] = value
            else:
                self._history = self._history[: self._history_index + 1]
                self._history.append(value)
                self._history_index = len(self._history) - 1
        self.address.set(self.page_url)
        self._load_document_text(
            source,
            base_dir=self.html_path.parent,
            page_url=self.page_url,
            response_url=self.page_url,
            remote=False,
            source_label=self.html_path.name,
        )

    # ------------------------------------------------------------------
    # JS host event loop / browser services
    # ------------------------------------------------------------------

    def _post_ui(self, callback, *args, **kwargs) -> None:
        """Queue a callback for execution by Tk's main thread."""
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
                self._log(f"Background operation error: {exc}")
        try:
            if self.root.winfo_exists():
                self._ui_after = self.root.after(25, self._poll_ui_queue)
        except tk.TclError:
            self._ui_after = None

    def _schedule_pump(self) -> None:
        if self._pump_after:
            try:
                self.root.after_cancel(self._pump_after)
            except tk.TclError:
                pass
        if self.worker:
            self._pump_after = self.root.after(25, self._pump_js)

    def _pump_js(self) -> None:
        self._pump_after = None
        if not self.worker:
            return
        self._drain_js()
        if self.worker:
            self._schedule_pump()

    def _event(self, event: dict) -> None:
        if not self.worker:
            return
        try:
            self.worker.event(event)
            messages = self._drain_js()
            if event.get("type") == "click" and event.get("href"):
                prevented = any(m.get("op") == "done" and m.get("defaultPrevented") for m in messages)
                if not prevented:
                    href = str(event["href"])
                    target = resolve_url(self.page_url or "about:blank", href) if self.page_url else href
                    self._navigate(target, add_history=True, user_initiated=False)
        except Exception as exc:
            self._log(f"JavaScript event error: {exc}")

    def _drain_js(self) -> list[dict]:
        if not self.worker or not self.renderer:
            return []
        messages = self.worker.poll(0.0)
        mutated = False
        returned: list[dict] = []
        for msg in messages:
            returned.append(msg)
            op = msg.get("op")
            if op in {"set_text", "set_value", "set_checked", "set_attr", "remove_attr", "set_style", "set_html", "insert_node", "insert_html", "remove_node", "focus", "click"}:
                self.renderer.apply_mutation(msg)
                mutated = mutated or op not in {"focus", "click"}
            elif op == "alert":
                messagebox.showinfo("JavaScript", msg.get("value", ""), parent=self.root)
            elif op == "console":
                self._log(f"[js:{msg.get('level', 'log')}] {msg.get('value', '')}")
            elif op in {"error", "worker_stderr"}:
                self._log(f"[js:{'error' if op == 'error' else 'stderr'}] {msg.get('value', '')}")
            elif op in {"set_timeout", "set_interval"}:
                timer_id = int(msg["timerId"])
                ms = int(msg.get("ms", 0 if op == "set_timeout" else 10))
                if op == "set_interval":
                    self._interval_active.add(timer_id)
                self._timer_handles[timer_id] = self.root.after(
                    max(0, ms),
                    lambda tid=timer_id, interval=(op == "set_interval"), delay=max(1, ms): self._fire_timer(tid, interval, delay),
                )
            elif op in {"clear_timeout", "clear_interval"}:
                tid = int(msg.get("timerId", 0))
                self._interval_active.discard(tid)
                handle = self._timer_handles.pop(tid, None)
                if handle:
                    try:
                        self.root.after_cancel(handle)
                    except tk.TclError:
                        pass
            elif op == "network_request":
                self._start_js_network_request(msg)
            elif op == "navigate":
                raw_target = str(msg.get("url", ""))
                target = resolve_url(self.page_url or "about:blank", raw_target)
                self._navigate(target, add_history=not bool(msg.get("replace")), user_initiated=False, replace=bool(msg.get("replace")))
            elif op == "reload":
                self.reload()
        if mutated and self.renderer:
            self.renderer.refresh_after_mutations()
        return returned

    def _start_js_network_request(self, msg: dict) -> None:
        if not self.page_url or not self.page_url.startswith(("http://", "https://")):
            self._send_network_error(int(msg.get("requestId", 0)), "fetch() is available only for web pages (http/https)")
            return
        request_id = int(msg.get("requestId", 0))
        method = str(msg.get("method", "GET")).upper()
        headers = msg.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        body_text = msg.get("body")
        body = b"" if body_text is None else str(body_text).encode("utf-8")
        request_url = resolve_url(self.page_url, str(msg.get("url", "")))
        mode = str(msg.get("mode", "cors")).lower()
        credentials = str(msg.get("credentials", "omit")).lower()
        if credentials != "omit":
            self._send_network_error(request_id, "WebCat does not implement credentialed fetch/cookies")
            return
        origin = str(msg.get("origin") or origin_of(self.page_url))
        same = same_origin(self.page_url, request_url)
        # Private targets are only permitted for explicitly user-selected local origins.
        target_host = (urlsplit(request_url).hostname or "").lower()
        page_host = (urlsplit(self.page_url).hostname or "").lower()
        allow_private = page_host in self._explicit_local_hosts and target_host == page_host
        if mode == "same-origin" and not same:
            self._send_network_error(request_id, "fetch blocked by same-origin policy")
            return
        future = self._net.submit(
            self._fetch_for_page,
            request_url,
            method,
            headers,
            body,
            origin,
            same,
            allow_private,
            mode,
        )
        generation = self._page_generation
        future.add_done_callback(lambda f, gen=generation, rid=request_id: self._post_ui(self._js_network_done, gen, rid, f))

    @staticmethod
    def _fetch_for_page(url: str, method: str, headers: dict, body: bytes, origin: str,
                        same: bool, allow_private: bool, mode: str) -> dict:
        try:
            response = fetch_url(
                url,
                origin=(origin if not same else None),
                method=method,
                headers={str(k): str(v) for k, v in headers.items()},
                body=body,
                timeout=15.0,
                allow_private=allow_private,
                require_cors=(mode != "no-cors" and not same),
                cors_mode=mode,
            )
            if mode == "no-cors" and not same:
                return {
                    "response": {
                        "url": response.url,
                        "status": 0,
                        "statusText": "",
                        "headers": {},
                        "body": "",
                        "redirected": response.url != url,
                        "type": "opaque",
                    }
                }
            response_text = response.text()
            return {
                "response": {
                    "url": response.url,
                    "status": response.status,
                    "statusText": response.reason,
                    "headers": response.headers,
                    "body": response_text,
                    "redirected": response.url != url,
                    "type": "basic" if same else "cors",
                }
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _js_network_done(self, generation: int, request_id: int, future: Future) -> None:
        if generation != self._page_generation or not self.worker:
            return
        try:
            payload = future.result()
        except Exception as exc:
            payload = {"error": str(exc)}
        try:
            self.worker.network_response(request_id, payload)
            self._drain_js()
        except Exception as exc:
            self._log(f"Network response delivery error: {exc}")

    def _send_network_error(self, request_id: int, error: str) -> None:
        if not self.worker:
            return
        try:
            self.worker.network_response(request_id, {"error": error})
            self._drain_js()
        except Exception as exc:
            self._log(f"Could not reject fetch(): {exc}")

    def _fire_timer(self, timer_id: int, interval: bool, delay: int = 0) -> None:
        self._timer_handles.pop(timer_id, None)
        if interval and timer_id not in self._interval_active:
            return
        if not self.worker:
            return
        try:
            self.worker.timer(timer_id, interval)
            self._drain_js()
        except Exception as exc:
            self._log(f"JavaScript timer error: {exc}")
            return
        if interval and self.worker and timer_id in self._interval_active:
            self._timer_handles[timer_id] = self.root.after(delay, lambda tid=timer_id, d=delay: self._fire_timer(tid, True, d))

    def _stop_js(self) -> None:
        if self._pump_after:
            try:
                self.root.after_cancel(self._pump_after)
            except tk.TclError:
                pass
            self._pump_after = None
        for handle in list(self._timer_handles.values()):
            try:
                self.root.after_cancel(handle)
            except tk.TclError:
                pass
        self._timer_handles.clear()
        self._interval_active.clear()
        if self.worker:
            self.worker.close()
            self.worker = None

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def toggle_logs(self) -> None:
        self.logs_visible = not self.logs_visible
        if self.logs_visible:
            self.log_frame.pack(fill="both", expand=False, before=self.view)
        else:
            self.log_frame.pack_forget()

    def _log(self, message: str) -> None:
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message.rstrip() + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()

    def _on_close(self) -> None:
        if self._ui_after:
            try:
                self.root.after_cancel(self._ui_after)
            except tk.TclError:
                pass
            self._ui_after = None
        self._stop_js()
        try:
            self._net.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._net.shutdown(wait=False)
        self.root.destroy()
