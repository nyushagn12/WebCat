from __future__ import annotations

import argparse

from .app import App
from .launcher import Launcher


def main() -> None:
    parser = argparse.ArgumentParser(description="Render HTML/CSS/JS with WebCat")
    parser.add_argument("file", nargs="?", help="HTML file; omit to open the graphical launcher")
    parser.add_argument("--js", help="Additional JavaScript file")
    parser.add_argument("--no-js", action="store_true", help="Disable page JavaScript")
    args = parser.parse_args()
    if not args.file:
        Launcher().run()
        return
    App(args.file, js_path=args.js, qjs=args.qjs, no_js=args.no_js).run()
