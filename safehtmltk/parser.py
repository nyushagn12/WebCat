from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

from .dom import Document, Node, VOID_TAGS


@dataclass(frozen=True)
class PageAssets:
    """Resources referenced by a page.

    The parser records URLs; the application decides whether each resource is
    a safe local asset or an allowed HTTP/HTTPS subresource.
    """

    stylesheets: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    inline_script: str = ""
    title: str = ""


class Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", node_id="__document__")
        self.stack: list[Node] = [self.root]
        self.styles: list[str] = []
        self.scripts: list[str] = []
        self.script_srcs: list[str] = []
        self.stylesheet_links: list[str] = []
        self.title_parts: list[str] = []
        self.in_style = False
        self.in_script = False
        self.in_title = False
        self._next_node_id = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {k.lower(): (v if v is not None else "") for k, v in attrs}

        if tag == "link" and attr_map.get("rel", "").lower() == "stylesheet":
            href = attr_map.get("href", "").strip()
            if href:
                self.stylesheet_links.append(href)

        if tag == "script":
            src = attr_map.get("src", "").strip()
            script_type = attr_map.get("type", "").strip().lower()
            if src and script_type not in {"module", "text/module"}:
                self.script_srcs.append(src)
            self.in_script = True

        node_key = f"__node_{self._next_node_id}"
        self._next_node_id += 1
        node = Node(tag=tag, attrs=attr_map, node_id=node_key)
        self.stack[-1].append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

        if tag == "style":
            self.in_style = True
        elif tag == "title":
            self.in_title = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS and self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break
        if tag == "style":
            self.in_style = False
        elif tag == "script":
            self.in_script = False
        elif tag == "title":
            self.in_title = False
        
    def handle_data(self, data: str) -> None:
        if self.in_style:
            self.styles.append(data)
            return
        if self.in_script:
            current = self.stack[-1] if self.stack and self.stack[-1].tag == "script" else None
            if current is None or not current.attrs.get("src"):
                self.scripts.append(data)
            return
        if self.in_title:
            self.title_parts.append(data)
        if data:
            self.stack[-1].append(Node(tag="#text", text=data))

    def result_with_assets(self) -> tuple[Document, str, PageAssets]:
        doc = Document(self.root)
        assets = PageAssets(
            stylesheets=tuple(self.stylesheet_links),
            scripts=tuple(self.script_srcs),
            inline_script="\n".join(self.scripts),
            title=" ".join(" ".join(self.title_parts).split()),
        )
        return doc, "\n".join(self.styles), assets

    def result(self) -> tuple[Document, str, str]:
        doc, css, assets = self.result_with_assets()
        return doc, css, assets.inline_script


def parse_document(source: str) -> tuple[Document, str, PageAssets]:
    p = Parser()
    p.feed(source)
    p.close()
    return p.result_with_assets()


def parse_html(source: str) -> tuple[Document, str, str]:
    """Backwards-compatible helper used by the first prototype API."""
    doc, css, assets = parse_document(source)
    return doc, css, assets.inline_script
