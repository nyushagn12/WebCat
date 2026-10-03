from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional
import html as html_mod
import re


VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    parent: Optional["Node"] = None
    text: str = ""
    node_id: str = ""

    def append(self, child: "Node") -> None:
        child.parent = self
        self.children.append(child)

    def iter(self) -> Iterator["Node"]:
        yield self
        for child in self.children:
            yield from child.iter()

    def text_content(self) -> str:
        if self.tag == "#text":
            return self.text
        return "".join(c.text_content() for c in self.children)

    def set_text_content(self, value: str) -> None:
        self.children = [Node(tag="#text", text=str(value), parent=self)] if value else []

    def get_attr(self, name: str, default: str = "") -> str:
        return self.attrs.get(name.lower(), default)

    def set_attr(self, name: str, value: str) -> None:
        self.attrs[str(name).lower()] = str(value)

    def remove_attr(self, name: str) -> None:
        self.attrs.pop(str(name).lower(), None)

    def has_class(self, cls: str) -> bool:
        return cls in self.attrs.get("class", "").split()


class Document:
    def __init__(self, root: Node):
        self.root = root
        self.by_id: dict[str, Node] = {}
        self.by_key: dict[str, Node] = {}
        self._index()

    def _index(self) -> None:
        self.by_id.clear()
        self.by_key.clear()
        for node in self.root.iter():
            if node.node_id:
                self.by_key[node.node_id] = node
            dom_id = node.attrs.get("id", "") if node.tag != "#text" else ""
            if dom_id:
                self.by_id[dom_id] = node

    def register(self, node: Node) -> None:
        for current in node.iter():
            if current.node_id:
                self.by_key[current.node_id] = current
            dom_id = current.attrs.get("id", "") if current.tag != "#text" else ""
            if dom_id:
                self.by_id[dom_id] = current

    def unregister(self, node: Node) -> None:
        for current in node.iter():
            if current.node_id:
                self.by_key.pop(current.node_id, None)
            dom_id = current.attrs.get("id", "") if current.tag != "#text" else ""
            if dom_id:
                self.by_id.pop(dom_id, None)

    def get_element_by_id(self, node_id: str) -> Optional[Node]:
        return self.by_id.get(node_id)

    def get_node(self, node_key: str) -> Optional[Node]:
        return self.by_key.get(node_key)

    def query_selector_all(self, selector: str) -> list[Node]:
        from .css import selector_matches
        selectors = [part.strip() for part in selector.split(",") if part.strip()]
        if not selectors:
            return []
        results: list[Node] = []
        for node in self.root.iter():
            if node.tag == "#text":
                continue
            if any(selector_matches(node, part) for part in selectors):
                results.append(node)
        return results

    def query_selector(self, selector: str) -> Optional[Node]:
        nodes = self.query_selector_all(selector)
        return nodes[0] if nodes else None

    def snapshot(self) -> list[dict]:
        out: list[dict] = []
        for n in self.root.iter():
            if n.tag == "#text":
                continue
            out.append({
                "id": n.node_id,
                "htmlId": n.attrs.get("id", ""),
                "tag": n.tag,
                "attrs": dict(n.attrs),
                "text": n.text_content(),
                "value": n.attrs.get("value", ""),
                "checked": "checked" in n.attrs,
                "parentId": n.parent.node_id if n.parent is not None else "",
            })
        return out


def matches_simple_selector(node: Node, selector: str) -> bool:
    if node.tag == "#text":
        return False
    selector = selector.strip()
    if selector == "*":
        return True
    tag = re.match(r"^[a-zA-Z][\w-]*", selector)
    if tag and node.tag != tag.group(0).lower():
        return False
    sid = re.search(r"#([\w-]+)", selector)
    if sid and node.attrs.get("id", "") != sid.group(1):
        return False
    for cls in re.findall(r"\.([\w-]+)", selector):
        if not node.has_class(cls):
            return False
    return True


def html_to_text(fragment: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", "", fragment))
