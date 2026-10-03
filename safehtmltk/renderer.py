from __future__ import annotations

from dataclasses import dataclass
import math
import re
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont
from typing import Callable

from .css import Stylesheet, css_color, css_length, css_px, parse_declarations
from .dom import Document, Node


BLOCK_DEFAULTS = {
    "html", "body", "main", "section", "article", "header", "footer", "nav", "aside", "div", "form",
    "ul", "ol", "li", "p", "pre", "blockquote", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "fieldset",
    "figure", "figcaption", "dl", "dt", "dd", "details", "summary", "address",
}
INLINE_TAGS = {"span", "strong", "b", "em", "i", "small", "label", "code", "a", "abbr", "mark", "s", "u", "sup", "sub"}
CONTROL_TAGS = {"button", "input", "textarea", "select"}
SKIP_TAGS = {"head", "script", "style", "title", "link", "meta", "template"}


@dataclass
class Box:
    node: Node
    style: dict[str, str]
    x: float
    y: float
    width: float
    height: float
    content_x: float
    content_y: float
    content_width: float
    content_height: float
    widget: tk.Widget | None = None


class Renderer:
    """Small CSS box/layout renderer drawn on a Tk Canvas.

    Generic HTML is rendered as vector rectangles + text on the canvas. Form
    controls use native Tk widgets over the canvas. This avoids the old Frame/pack
    hierarchy where padding, backgrounds, borders and flex sizing were largely
    invisible.
    """

    def __init__(self, root: tk.Misc, document: Document, stylesheet: Stylesheet,
                 on_event: Callable[[dict], None]):
        self.root = root
        self.document = document
        self.stylesheet = stylesheet
        self.on_event = on_event
        self.widgets: dict[str, tk.Widget] = {}
        self.widget_nodes: dict[str, Node] = {}
        self.vars: dict[str, tk.Variable] = {}
        self.boxes: dict[str, Box] = {}
        self._font_cache: dict[tuple, tkfont.Font] = {}
        self._item_nodes: dict[int, str] = {}
        self._hovered: set[str] = set()
        self.canvas = tk.Canvas(root, highlightthickness=0, bd=0, bg="white", xscrollincrement=20, yscrollincrement=20)
        self.vsb = ttk.Scrollbar(root, orient="vertical", command=self.canvas.yview)
        self.hsb = ttk.Scrollbar(root, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=1)
        self.canvas.bind("<Configure>", lambda _e: self.render())
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(3, "units"))
        self.render()

    def widget_for(self, node_id: str):
        return self.widgets.get(node_id)

    def destroy(self) -> None:
        for widget in list(self.widgets.values()):
            try:
                widget.destroy()
            except tk.TclError:
                pass
        self.widgets.clear()
        self.widget_nodes.clear()
        self.vars.clear()
        self.boxes.clear()
        self.canvas.destroy()
        self.vsb.destroy()
        self.hsb.destroy()

    def render(self) -> None:
        try:
            width = max(360, self.canvas.winfo_width())
            if width <= 1:
                width = max(360, self.root.winfo_width())
            self._clear_canvas()
            html_root = self._find_html()
            if html_root is not None:
                children = [html_root]
            else:
                body = self._find_body()
                children = [body] if body is not None else [c for c in self.document.root.children if c.tag not in SKIP_TAGS]
            y = 0.0
            max_right = float(width)
            max_bottom = 0.0
            for node in children:
                if node.tag in SKIP_TAGS:
                    continue
                box = self._layout_block(node, 0, y, float(width), None)
                if box:
                    max_right = max(max_right, box.x + box.width)
                    max_bottom = max(max_bottom, box.y + box.height)
                    margin_bottom = self._box_lengths(box.style, "margin", float(width), self._font_size(box.style), self._vw(), self._vh())[2]
                    y = box.y + box.height + margin_bottom
            self.canvas.configure(scrollregion=(0, 0, max(0, max_right + 20), max(0, max_bottom + 20)))
        except tk.TclError:
            pass

    def _clear_canvas(self) -> None:
        for widget in list(self.widgets.values()):
            try:
                widget.destroy()
            except tk.TclError:
                pass
        self.widgets.clear()
        self.widget_nodes.clear()
        self.vars.clear()
        self.boxes.clear()
        self._item_nodes.clear()
        self.canvas.delete("all")

    def _find_html(self) -> Node | None:
        for node in self.document.root.iter():
            if node.tag == "html":
                return node
        return None

    def _find_body(self) -> Node | None:
        for node in self.document.root.iter():
            if node.tag == "body":
                return node
        return None

    def _style(self, node: Node, parent_style: dict[str, str] | None) -> dict[str, str]:
        style = self.stylesheet.compute(node, parent_style)
        parent_size = self._font_size(parent_style) if parent_style else 16
        raw_size = style.get("font-size", "16px")
        if raw_size:
            resolved = css_length(raw_size, parent_size, rem=16, em=parent_size, vw=self._vw(), vh=self._vh())
            if resolved is not None:
                style["font-size"] = f"{int(round(resolved))}px"
        return style

    def _layout_block(self, node: Node, x: float, y: float, containing_width: float,
                      parent_style: dict[str, str] | None) -> Box | None:
        if node.tag == "#text" or node.tag in SKIP_TAGS:
            return None
        style = self._style(node, parent_style)
        if style.get("display", "block").lower() == "none" or style.get("visibility") == "hidden":
            return None

        viewport_w = max(360.0, float(self.canvas.winfo_width() or 1000))
        viewport_h = max(240.0, float(self.canvas.winfo_height() or 800))
        font = self._font_for(style)
        font_size = int(font.cget("size"))
        margin = self._box_lengths(style, "margin", containing_width, font_size, viewport_w, viewport_h)
        margin_auto_left = style.get("margin-left", "0").strip().lower() == "auto"
        margin_auto_right = style.get("margin-right", "0").strip().lower() == "auto"
        padding = self._box_lengths(style, "padding", containing_width, font_size, viewport_w, viewport_h)
        border = self._border_lengths(style, containing_width, font_size, viewport_w, viewport_h)
        x0 = x + margin[3]
        y0 = y + margin[0]
        available = max(1.0, containing_width - margin[1] - margin[3])
        extra = border[1] + border[3] + padding[1] + padding[3]
        box_sizing = style.get("box-sizing", "content-box").lower()
        width_spec = self._resolve_dimension(style.get("width"), available, font_size, viewport_w, viewport_h)
        max_width = self._resolve_dimension(style.get("max-width"), available, font_size, viewport_w, viewport_h)
        min_width = self._resolve_dimension(style.get("min-width"), 0, font_size, viewport_w, viewport_h)
        if width_spec is None:
            # CSS auto width fills the containing block; padding/border are inside it.
            content_width = max(1.0, available - extra)
            total_width = available
        elif box_sizing == "border-box":
            total_width = max(1.0, width_spec)
            content_width = max(1.0, total_width - extra)
        else:
            content_width = max(1.0, width_spec)
            total_width = content_width + extra
        if max_width is not None:
            content_width = min(content_width, max_width)
            total_width = content_width + extra if box_sizing != "border-box" else min(total_width, max_width)
            if box_sizing == "border-box":
                content_width = max(1.0, total_width - extra)
        if min_width is not None:
            content_width = max(content_width, min_width)
            total_width = content_width + extra if box_sizing != "border-box" else max(total_width, min_width)
            if box_sizing == "border-box":
                content_width = max(1.0, total_width - extra)
        width = max(1.0, total_width)
        if margin_auto_left and margin_auto_right and width < containing_width:
            x0 = x + (containing_width - width) / 2
        elif margin_auto_left and width < containing_width:
            x0 = x + max(0, containing_width - width - margin[1])

        display = style.get("display", "block").lower()
        if node.tag in CONTROL_TAGS:
            return self._layout_control(node, x0, y0, width, style, padding, border, font_size, viewport_w, viewport_h)
        if node.tag in {"img", "video", "audio", "canvas"}:
            return self._layout_replaced(node, x0, y0, width, style, padding, border, font_size, viewport_w, viewport_h)

        if display in {"inline", "inline-block"}:
            intrinsic_w, intrinsic_h = self._intrinsic_inline(node, content_width, style)
            if style.get("width") is None:
                content_width = max(1.0, intrinsic_w - border_box_extra if box_sizing == "border-box" else intrinsic_w)
                width = intrinsic_w if box_sizing == "border-box" else intrinsic_w + border_box_extra
        children = [c for c in node.children if c.tag not in SKIP_TAGS]

        content_top = y0 + border[0] + padding[0]
        content_left = x0 + border[3] + padding[3]
        content_height = 0.0
        if display == "flex":
            content_height = self._layout_flex_children(node, children, content_left, content_top,
                                                        content_width, style)
        else:
            content_height = self._layout_flow_children(node, children, content_left, content_top,
                                                        content_width, style)

        specified_height = self._resolve_dimension(style.get("height"), max(1.0, content_height), font_size, viewport_w, viewport_h)
        if specified_height is None:
            inner_height = content_height
        else:
            inner_height = max(0.0, specified_height - (border[0] + border[2] + padding[0] + padding[2])
                               if box_sizing == "border-box" else specified_height)
            content_height = inner_height
        min_h = self._resolve_dimension(style.get("min-height"), 0, font_size, viewport_w, viewport_h)
        if min_h is not None:
            content_height = max(content_height, min_h)
        total_h = content_height + border[0] + border[2] + padding[0] + padding[2]
        if display == "inline-block" and specified_height is None:
            total_h = max(total_h, font.metrics("-linespace") + padding[0] + padding[2] + border[0] + border[2])

        box = Box(node=node, style=style, x=x0, y=y0, width=width, height=total_h,
                  content_x=content_left, content_y=content_top,
                  content_width=content_width, content_height=content_height)
        self.boxes[node.node_id] = box
        self._draw_box(box)
        if node.tag == "li":
            self._draw_list_marker(box, node)
        return box

    def _layout_flow_children(self, node: Node, children: list[Node], x: float, y: float,
                              width: float, parent_style: dict[str, str]) -> float:
        cursor_y = y
        inline_nodes: list[Node] = []
        max_right = x
        def flush_inline() -> None:
            nonlocal cursor_y, inline_nodes, max_right
            if not inline_nodes:
                return
            h = self._layout_inline_runs(inline_nodes, x, cursor_y, width, parent_style)
            cursor_y += h
            inline_nodes = []
        for child in children:
            if child.tag == "#text" or self._is_inline_node(child):
                inline_nodes.append(child)
                continue
            flush_inline()
            cstyle = self._style(child, parent_style)
            cm = self._box_lengths(cstyle, "margin", width, self._font_size(parent_style), self._vw(), self._vh())
            box = self._layout_block(child, x, cursor_y, width, parent_style)
            if box:
                cursor_y = box.y + box.height + cm[2]
                max_right = max(max_right, box.x + box.width)
        flush_inline()
        if cursor_y <= y:
            return self._line_height(parent_style)
        return cursor_y - y

    def _layout_inline_runs(self, nodes: list[Node], x: float, y: float, width: float,
                            parent_style: dict[str, str]) -> float:
        runs: list[tuple[str, dict[str, str], Node]] = []
        for node in nodes:
            self._collect_inline_runs(node, parent_style, runs)
        if not runs:
            return 0
        lines: list[list[tuple[str, dict[str, str], Node, int, int]]] = []
        current: list[tuple[str, dict[str, str], Node, int, int]] = []
        line_w = 0.0
        line_h = 0.0
        max_w = max(1.0, width)
        white_space = parent_style.get("white-space", "normal")
        for text, style, node in runs:
            font = self._font_for(style)
            if "\n" in text:
                pieces = text.split("\n")
            else:
                pieces = [text]
            for pi, piece in enumerate(pieces):
                if pi:
                    if current:
                        lines.append(current)
                    current = []
                    line_w = 0
                    line_h = 0
                tokens = [piece] if white_space in {"pre", "pre-wrap"} else re.findall(r"\S+|\s+", piece)
                for token in tokens:
                    if not token:
                        continue
                    token_w = font.measure(token)
                    token_h = self._font_line_height(style)
                    if white_space not in {"pre", "pre-wrap"} and token.isspace() and not current:
                        continue
                    if white_space not in {"nowrap", "pre", "pre-wrap"} and current and line_w + token_w > max_w and not token.isspace():
                        lines.append(current)
                        current = []
                        line_w = 0
                        line_h = 0
                    if white_space not in {"pre", "pre-wrap"} and token.isspace() and line_w + token_w > max_w:
                        continue
                    current.append((token, style, node, font.measure(""), int(token_w)))
                    line_w += token_w
                    line_h = max(line_h, token_h)
        if current:
            lines.append(current)
        if not lines:
            return 0

        y_cursor = y
        align = parent_style.get("text-align", "start").lower()
        for line in lines:
            line_width = sum(item[4] for item in line)
            start_x = x
            if align in {"center", "middle"}:
                start_x = x + max(0, (width - line_width) / 2)
            elif align in {"right", "end"}:
                start_x = x + max(0, width - line_width)
            cursor = start_x
            line_height = max(self._font_line_height(item[1]) for item in line)
            for token, style, node, _, token_width in line:
                if token.isspace() and not token.strip():
                    cursor += token_width
                    continue
                font = self._font_for(style)
                fill = self._text_color(style)
                pad = self._box_lengths(style, "padding", width, self._font_size(style), self._vw(), self._vh())
                border = self._border_lengths(style, width, self._font_size(style), self._vw(), self._vh())
                token_w = token_width + pad[1] + pad[3] + border[1] + border[3]
                token_h = line_height + pad[0] + pad[2] + border[0] + border[2]
                bg = self._background_color(style)
                if bg and bg != "transparent":
                    self.canvas.create_rectangle(cursor, y_cursor, cursor + token_w, y_cursor + token_h, fill=bg, outline="")
                if any(border):
                    self.canvas.create_rectangle(cursor, y_cursor, cursor + token_w, y_cursor + token_h,
                                                 outline=css_color(style.get("border-top-color", "#d1d5db"), "#d1d5db"), width=max(1, int(border[0] or border[1] or border[2] or border[3])))
                item = self.canvas.create_text(cursor + pad[3] + border[3], y_cursor + pad[0] + border[0], text=token, anchor="nw", font=font, fill=fill)
                self._item_nodes[item] = node.node_id
                if node.node_id and node.tag == "a":
                    self.canvas.tag_bind(item, "<Button-1>", lambda _e, n=node: self._fire(n, "click"))
                cursor += token_w
            y_cursor += line_height
        return y_cursor - y

    def _collect_inline_runs(self, node: Node, parent_style: dict[str, str], out: list[tuple[str, dict[str, str], Node]], owner: Node | None = None) -> None:
        if node.tag == "#text":
            if node.text:
                out.append((node.text, parent_style, owner or node))
            return
        if node.tag in SKIP_TAGS:
            return
        style = self._style(node, parent_style)
        display = style.get("display", "inline").lower()
        if node.tag == "br":
            out.append(("\n", style, node))
            return
        if display not in {"inline", "inline-block"}:
            return
        if node.tag in CONTROL_TAGS:
            return
        if not node.children:
            txt = node.attrs.get("alt", "") or ""
            if txt:
                out.append((txt, style, node))
            return
        for child in node.children:
            self._collect_inline_runs(child, style, out, owner=node)

    def _layout_flex_children(self, node: Node, children: list[Node], x: float, y: float,
                              width: float, style: dict[str, str]) -> float:
        direction = style.get("flex-direction", "row").lower()
        row = direction != "column" and direction != "column-reverse"
        reverse = direction in {"row-reverse", "column-reverse"}
        gap = self._resolve_dimension(style.get("column-gap" if row else "row-gap", style.get("gap", "0px")), width, self._font_size(style), self._vw(), self._vh()) or 0
        items = []
        for child in children:
            if child.tag == "#text" and not child.text.strip():
                continue
            cstyle = self._style(child, style)
            if cstyle.get("display", "block").lower() == "none":
                continue
            intrinsic = self._intrinsic_size(child, width, cstyle)
            basis = self._resolve_dimension(cstyle.get("flex-basis"), width if row else max(1, intrinsic[1]), self._font_size(cstyle), self._vw(), self._vh())
            if basis is None or basis < 0:
                basis = intrinsic[0] if row else intrinsic[1]
            grow = float(cstyle.get("flex-grow", "0") or 0)
            try:
                grow = max(0.0, grow)
            except ValueError:
                grow = 0
            shrink = float(cstyle.get("flex-shrink", "1") or 1)
            items.append((child, cstyle, float(basis), intrinsic, grow, max(0, shrink)))
        if reverse:
            items.reverse()
        total_basis = sum(i[2] for i in items) + max(0, len(items) - 1) * gap
        free = (width - total_basis) if row else max(0, total_basis - width)
        sizes = [i[2] for i in items]
        if row and free > 0:
            grow_total = sum(i[4] for i in items)
            if grow_total:
                for idx, item in enumerate(items):
                    sizes[idx] += free * item[4] / grow_total
        elif row and free < 0:
            shrink_total = sum(i[5] * i[2] for i in items)
            if shrink_total:
                deficit = -free
                for idx, item in enumerate(items):
                    sizes[idx] = max(1.0, sizes[idx] - deficit * (item[5] * item[2]) / shrink_total)
        if not row:
            sizes = [i[2] for i in items]

        justify = style.get("justify-content", "flex-start").lower()
        main_space = width if row else max(1.0, width)
        occupied = sum(sizes) + max(0, len(items) - 1) * gap
        extra = max(0.0, main_space - occupied)
        leading = 0.0
        between = gap
        if justify in {"center", "middle"}:
            leading = extra / 2
        elif justify in {"flex-end", "end"}:
            leading = extra
        elif justify == "space-between" and len(items) > 1:
            between = gap + extra / (len(items) - 1)
        elif justify == "space-around" and items:
            between = gap + extra / len(items)
            leading = between / 2
        elif justify == "space-evenly" and items:
            between = gap + extra / (len(items) + 1)
            leading = between

        cursor = (x if row else y) + leading
        line_cross = max((i[3][1] if row else i[3][0]) for i in items) if items else 0.0
        actual_cross = 0.0
        for idx, (child, cstyle, _basis, intrinsic, _grow, _shrink) in enumerate(items):
            main = sizes[idx]
            intrinsic_cross = intrinsic[1] if row else intrinsic[0]
            align = cstyle.get("align-self", style.get("align-items", "stretch")).lower()
            cross = line_cross if align == "stretch" else intrinsic_cross
            available_cross = line_cross
            if align in {"center", "middle"}:
                cross_pos = (y if row else x) + max(0, (available_cross - cross) / 2)
            elif align in {"flex-end", "end"}:
                cross_pos = (y if row else x) + max(0, available_cross - cross)
            else:
                cross_pos = y if row else x
            child_box = self._layout_block(child, cursor if row else cross_pos,
                                           cross_pos if row else cursor,
                                           main if row else max(1.0, available_cross), style)
            if child_box:
                actual_cross = max(actual_cross, child_box.height if row else child_box.width)
            cursor += main + between
        return max(line_cross, actual_cross)

    def _layout_control(self, node: Node, x: float, y: float, width: float, style: dict[str, str],
                        padding: tuple[float, float, float, float], border: tuple[float, float, float, float],
                        font_size: int, vw: float, vh: float) -> Box:
        widget = self._make_control(node, style, font_size)
        self._remember(node, widget)
        req_w = max(40, widget.winfo_reqwidth())
        req_h = max(24, widget.winfo_reqheight())
        box_sizing = style.get("box-sizing", "content-box")
        specified_w = self._resolve_dimension(style.get("width"), width, font_size, vw, vh)
        inner_w = specified_w if specified_w is not None else req_w
        total_w = inner_w + border[1] + border[3] + padding[1] + padding[3]
        if box_sizing == "border-box" and specified_w is not None:
            total_w = specified_w
            inner_w = max(1, total_w - border[1] - border[3] - padding[1] - padding[3])
        specified_h = self._resolve_dimension(style.get("height"), req_h, font_size, vw, vh)
        total_h = specified_h if specified_h is not None and box_sizing == "border-box" else (specified_h or req_h)
        if box_sizing != "border-box":
            total_h += border[0] + border[2] + padding[0] + padding[2]
        total_w = max(20, total_w)
        total_h = max(20, total_h)
        box = Box(node=node, style=style, x=x, y=y, width=total_w, height=total_h,
                  content_x=x + border[3] + padding[3], content_y=y + border[0] + padding[0],
                  content_width=max(1, inner_w), content_height=max(1, total_h - border[0] - border[2] - padding[0] - padding[2]), widget=widget)
        self.boxes[node.node_id] = box
        self._draw_box(box)
        self._place_widget(widget, x, y, total_w, total_h)
        return box

    def _layout_replaced(self, node: Node, x: float, y: float, width: float, style: dict[str, str],
                         padding: tuple[float, float, float, float], border: tuple[float, float, float, float],
                         font_size: int, vw: float, vh: float) -> Box:
        requested_w = self._resolve_dimension(style.get("width"), width, font_size, vw, vh)
        requested_h = self._resolve_dimension(style.get("height"), 180, font_size, vw, vh)
        w = requested_w or min(width, 420)
        h = requested_h or 160
        label = node.attrs.get("alt") or node.attrs.get("title") or f"[{node.tag}]"
        box = Box(node=node, style=style, x=x, y=y, width=w, height=h,
                  content_x=x + border[3] + padding[3], content_y=y + border[0] + padding[0],
                  content_width=max(1, w - border[1] - border[3] - padding[1] - padding[3]),
                  content_height=max(1, h - border[0] - border[2] - padding[0] - padding[2]))
        self.boxes[node.node_id] = box
        self._draw_box(box)
        bg = self._background_color(style)
        if not bg or bg == "transparent": bg = "#f3f4f6"
        rect = self.canvas.create_rectangle(x + border[3], y + border[0], x + w - border[1], y + h - border[2], fill=bg, outline="")
        self._item_nodes[rect] = node.node_id
        text = self.canvas.create_text(x + w/2, y + h/2, text=label, anchor="center", justify="center",
                                       width=max(40, w - 20), font=self._font_for(style), fill=self._text_color(style))
        self._item_nodes[text] = node.node_id
        return box

    def _make_control(self, node: Node, style: dict[str, str], font_size: int) -> tk.Widget:
        font = self._font_for(style)
        tag = node.tag
        if tag == "button":
            text = self._visible_text(node) or node.attrs.get("value", "Button")
            w = tk.Button(self.canvas, text=text, command=lambda n=node: self._fire(n, "click"), font=font, cursor=style.get("cursor", "hand2"), relief="flat")
        elif tag == "input":
            typ = node.attrs.get("type", "text").lower()
            if typ == "checkbox":
                var = tk.BooleanVar(value="checked" in node.attrs)
                self.vars[node.node_id] = var
                w = tk.Checkbutton(self.canvas, text=node.attrs.get("aria-label", ""), variable=var, font=font,
                                   anchor="w", command=lambda n=node: self._fire(n, "change"))
            elif typ == "radio":
                group = node.attrs.get("name", "__radio__")
                var = self.vars.get(f"radio:{group}")
                if not isinstance(var, tk.StringVar):
                    var = tk.StringVar(value="")
                    self.vars[f"radio:{group}"] = var
                value = node.attrs.get("value", "on")
                w = tk.Radiobutton(self.canvas, text=node.attrs.get("aria-label", ""), variable=var, value=value,
                                    font=font, anchor="w", command=lambda n=node: self._fire(n, "change"))
                if "checked" in node.attrs:
                    var.set(value)
            elif typ in {"button", "submit", "reset"}:
                w = tk.Button(self.canvas, text=node.attrs.get("value", "Button"), command=lambda n=node: self._fire(n, "click"), font=font, relief="flat")
            elif typ == "range":
                var = tk.DoubleVar(value=float(node.attrs.get("value", node.attrs.get("min", "0")) or 0))
                self.vars[node.node_id] = var
                w = tk.Scale(self.canvas, variable=var, from_=float(node.attrs.get("min", "0") or 0),
                             to=float(node.attrs.get("max", "100") or 100), orient="horizontal",
                             resolution=float(node.attrs.get("step", "1") or 1), showvalue=True,
                             command=lambda _v, n=node: self._sync_input(n, "input"))
            else:
                show = "•" if typ == "password" else ""
                w = tk.Entry(self.canvas, show=show, font=font)
                initial = node.attrs.get("value", "")
                if initial:
                    w.insert(0, initial)
                placeholder = node.attrs.get("placeholder")
                if placeholder and not initial:
                    w.insert(0, placeholder)
                    w.configure(fg="#6b7280")
                    w.bind("<FocusIn>", lambda _e, widget=w, ph=placeholder: self._placeholder_in(widget, ph), add="+")
                    w.bind("<FocusOut>", lambda _e, widget=w, ph=placeholder: self._placeholder_out(widget, ph), add="+")
                w.bind("<KeyRelease>", lambda _e, n=node: self._sync_input(n, "input"), add="+")
        elif tag == "textarea":
            w = tk.Text(self.canvas, wrap="word", undo=True, font=font, relief="solid", bd=1)
            text = node.text_content() or node.attrs.get("value", "")
            if text:
                w.insert("1.0", text)
            w.bind("<KeyRelease>", lambda _e, n=node: self._sync_input(n, "input"), add="+")
        else:  # select
            options = [c for c in node.children if c.tag == "option"]
            values = [self._visible_text(c) for c in options]
            selected_index = next((i for i, c in enumerate(options) if "selected" in c.attrs), 0)
            selected = values[selected_index] if values else ""
            var = tk.StringVar(value=node.attrs.get("value", selected))
            self.vars[node.node_id] = var
            w = ttk.Combobox(self.canvas, textvariable=var, values=values, state="readonly", font=font)
            w.bind("<<ComboboxSelected>>", lambda _e, n=node: self._sync_input(n, "change"))
        self._apply_widget_style(w, style, node)
        self._apply_disabled(node, w)
        return w

    def _apply_widget_style(self, w: tk.Widget, style: dict[str, str], node: Node) -> None:
        bg = self._background_color(style)
        fg = self._text_color(style)
        font = self._font_for(style)
        cfg: dict[str, object] = {"font": font}
        if bg:
            cfg["background"] = bg
            cfg["activebackground"] = bg
        if fg:
            cfg["foreground"] = fg
            cfg["activeforeground"] = fg
        cursor = style.get("cursor")
        if cursor in {"arrow", "hand2", "xterm", "crosshair"}:
            cfg["cursor"] = cursor
        try:
            w.configure(**cfg)
        except tk.TclError:
            pass
        if isinstance(w, tk.Entry):
            try:
                w.configure(insertbackground=fg or "#111827")
            except tk.TclError:
                pass

    def _place_widget(self, widget: tk.Widget, x: float, y: float, width: float, height: float) -> None:
        self.canvas.create_window(x, y, anchor="nw", window=widget, width=max(1, int(width)), height=max(1, int(height)))

    def _draw_box(self, box: Box) -> None:
        style = box.style
        bg = self._background_color(style)
        border = self._border_lengths(style, box.content_width, self._font_size(style), self._vw(), self._vh())
        radii = css_px(style.get("border-radius", "0px"), 0, self._font_size(style))
        if bg and bg != "transparent":
            item = self.canvas.create_rounded_rectangle(box.x, box.y, box.x + box.width, box.y + box.height,
                                                        radius=max(0, min(radii, int(min(box.width, box.height) / 2))),
                                                        fill=bg, outline="") if hasattr(self.canvas, "create_rounded_rectangle") else self.canvas.create_rectangle(box.x, box.y, box.x + box.width, box.y + box.height, fill=bg, outline="")
            self._item_nodes[item] = box.node.node_id
            self.canvas.tag_lower(item)
        if any(border):
            left = css_color(style.get("border-left-color", "#d1d5db"), "#d1d5db")
            right = css_color(style.get("border-right-color", left), left)
            top = css_color(style.get("border-top-color", left), left)
            bottom = css_color(style.get("border-bottom-color", left), left)
            # Canvas has no independent border sides, draw four rectangles.
            if border[0] > 0:
                item = self.canvas.create_rectangle(box.x, box.y, box.x + box.width, box.y + border[0], fill=top, outline="")
                self.canvas.tag_lower(item)
            if border[2] > 0:
                item = self.canvas.create_rectangle(box.x, box.y + box.height - border[2], box.x + box.width, box.y + box.height, fill=bottom, outline="")
                self.canvas.tag_lower(item)
            if border[3] > 0:
                item = self.canvas.create_rectangle(box.x, box.y, box.x + border[3], box.y + box.height, fill=left, outline="")
                self.canvas.tag_lower(item)
            if border[1] > 0:
                item = self.canvas.create_rectangle(box.x + box.width - border[1], box.y, box.x + box.width, box.y + box.height, fill=right, outline="")
                self.canvas.tag_lower(item)

    def _draw_list_marker(self, box: Box, node: Node) -> None:
        parent = node.parent
        if parent is None:
            return
        idx = 1
        count = 0
        for child in parent.children:
            if child.tag == "#text":
                continue
            count += 1
            if child is node:
                idx = count
                break
        marker = "•" if parent.tag != "ol" else f"{idx}."
        font = self._font_for(box.style)
        item = self.canvas.create_text(box.x - 18, box.y, text=marker, anchor="ne", font=font, fill=self._text_color(box.style))
        self._item_nodes[item] = node.node_id

    def _on_canvas_click(self, event) -> None:
        item = self.canvas.find_withtag("current")
        node_id = self._item_nodes.get(item[0]) if item else None
        if node_id:
            node = self.document.get_node(node_id)
            if node and node.tag not in CONTROL_TAGS:
                self._fire(node, "click")

    def _on_motion(self, event) -> None:
        item = self.canvas.find_withtag("current")
        current = self._item_nodes.get(item[0]) if item else None
        if current:
            self.canvas.configure(cursor="hand2" if self.document.get_node(current) else "arrow")
        else:
            self.canvas.configure(cursor="arrow")

    def _on_leave(self, _event) -> None:
        self.canvas.configure(cursor="arrow")

    def _remember(self, node: Node, widget: tk.Widget) -> None:
        self.widgets[node.node_id] = widget
        self.widget_nodes[node.node_id] = node

    def _fire(self, node: Node, typ: str) -> None:
        self._sync_input(node, typ, notify=False)
        self.on_event({"type": typ, "target": node.node_id, "value": node.attrs.get("value", ""), "checked": "checked" in node.attrs})

    def _sync_input(self, node: Node, typ: str, notify: bool = True) -> None:
        w = self.widgets.get(node.node_id)
        if w is None:
            return
        try:
            if isinstance(w, tk.Entry):
                val = w.get()
                if node.attrs.get("placeholder") and val == node.attrs.get("placeholder"):
                    val = ""
                node.attrs["value"] = val
            elif isinstance(w, tk.Text):
                node.attrs["value"] = w.get("1.0", "end-1c")
            elif isinstance(w, ttk.Combobox):
                node.attrs["value"] = self.vars[node.node_id].get()
            elif isinstance(w, tk.Checkbutton):
                checked = bool(self.vars[node.node_id].get())
                if checked:
                    node.attrs["checked"] = "checked"
                else:
                    node.attrs.pop("checked", None)
            elif isinstance(w, tk.Scale):
                node.attrs["value"] = str(self.vars[node.node_id].get())
        except tk.TclError:
            return
        if notify:
            self.on_event({"type": typ, "target": node.node_id, "value": node.attrs.get("value", ""), "checked": "checked" in node.attrs})

    def apply_mutation(self, msg: dict) -> None:
        op = msg.get("op")
        node_id = str(msg.get("id", ""))
        if op == "insert_node":
            parent_id = str(msg.get("parentId", ""))
            parent = self.document.get_node(parent_id)
            raw = msg.get("node") or {}
            if not parent:
                return
            new = Node(tag=str(raw.get("tag", "div")).lower(), attrs={str(k).lower(): str(v) for k, v in (raw.get("attrs") or {}).items()}, node_id=str(raw.get("id", "")))
            if raw.get("text"):
                new.append(Node(tag="#text", text=str(raw["text"])))
            parent.append(new)
            self.document.register(new)
        elif op == "remove_node":
            node = self.document.get_node(node_id)
            if node is not None:
                if node.parent:
                    node.parent.children = [c for c in node.parent.children if c is not node]
                self.document.unregister(node)
        else:
            node = self.document.get_node(node_id) if node_id else None
            if node is None and op not in {"focus", "click"}:
                return
            w = self.widgets.get(node_id)
            if op == "set_text" and node:
                node.set_text_content(str(msg.get("value", "")))
            elif op == "set_value" and node:
                node.attrs["value"] = str(msg.get("value", ""))
            elif op == "set_checked" and node:
                val = bool(msg.get("value"))
                if val: node.attrs["checked"] = "checked"
                else: node.attrs.pop("checked", None)
            elif op == "set_attr" and node:
                node.set_attr(str(msg.get("name", "")), str(msg.get("value", "")))
            elif op == "remove_attr" and node:
                node.remove_attr(str(msg.get("name", "")))
            elif op == "set_style" and node:
                name = str(msg.get("name", "")).lower()
                value = str(msg.get("value", ""))
                declarations = parse_declarations(node.attrs.get("style", ""))
                declarations[name] = value
                node.attrs["style"] = "; ".join(f"{k}: {v}" for k, v in declarations.items())
            elif op == "set_html" and node:
                from .parser import parse_document
                raw_doc, _css, _assets = parse_document(str(msg.get("value", "")))
                node.children = raw_doc.root.children
                for child in node.children:
                    child.parent = node
                self.document.register(node)
            elif op == "insert_html" and node:
                from .parser import parse_document
                raw_doc, _css, _assets = parse_document(str(msg.get("value", "")))
                for child in raw_doc.root.children:
                    child.parent = node
                    node.children.append(child)
                self.document.register(node)
            elif op == "focus" and w:
                try: w.focus_set()
                except tk.TclError: pass
            elif op == "click" and w and hasattr(w, "invoke"):
                try: w.invoke()
                except tk.TclError: pass

    def refresh_after_mutations(self) -> None:
        self.render()

    def _is_inline_node(self, node: Node) -> bool:
        if node.tag in INLINE_TAGS:
            return True
        if node.tag == "br":
            return True
        style = self._style(node, None)
        return style.get("display", "block").lower() in {"inline", "inline-block"} and node.tag not in CONTROL_TAGS

    def _intrinsic_inline(self, node: Node, width: float, style: dict[str, str]) -> tuple[float, float]:
        text = self._visible_text(node)
        font = self._font_for(style)
        line_h = self._font_line_height(style)
        return min(width, max(1, font.measure(text))), line_h

    def _intrinsic_size(self, node: Node, width: float, style: dict[str, str]) -> tuple[float, float]:
        if node.tag in CONTROL_TAGS:
            typ = node.attrs.get("type", "text").lower()
            if node.tag == "button" or typ in {"button", "submit", "reset"}:
                return max(90, self._font_for(style).measure(self._visible_text(node) or node.attrs.get("value", "Button")) + 28), 34
            if typ in {"checkbox", "radio"}:
                return 140, 28
            if typ == "range":
                return 220, 42
            if node.tag == "textarea":
                return 320, 100
            if node.tag == "select":
                return 220, 32
            return 240, 32
        text = self._visible_text(node)
        font = self._font_for(style)
        if text:
            lines = max(1, math.ceil(font.measure(text) / max(1, width)))
            return min(width, max(20, font.measure(text))), lines * self._font_line_height(style)
        return 80, self._font_line_height(style)

    def _apply_disabled(self, node: Node, w: tk.Widget) -> None:
        if "disabled" not in node.attrs:
            return
        try:
            w.configure(state="disabled")
        except tk.TclError:
            try:
                w.state(["disabled"])
            except Exception:
                pass

    def _placeholder_in(self, widget: tk.Entry, placeholder: str) -> None:
        try:
            if widget.get() == placeholder:
                widget.delete(0, tk.END)
                widget.configure(fg="#111827")
        except tk.TclError:
            pass

    def _placeholder_out(self, widget: tk.Entry, placeholder: str) -> None:
        try:
            if not widget.get():
                widget.insert(0, placeholder)
                widget.configure(fg="#6b7280")
        except tk.TclError:
            pass

    def _set_widget_text(self, node: Node, value: str) -> None:
        w = self.widgets.get(node.node_id)
        if not w:
            return
        try:
            if isinstance(w, tk.Entry):
                w.delete(0, tk.END); w.insert(0, value)
            elif isinstance(w, tk.Text):
                w.delete("1.0", tk.END); w.insert("1.0", value)
        except tk.TclError:
            pass

    def _visible_text(self, node: Node) -> str:
        return re.sub(r"\s+", " ", node.text_content()).strip()

    def _font_for(self, style: dict[str, str]) -> tkfont.Font:
        size = self._font_size(style)
        weight = style.get("font-weight", "400")
        weight_name = "bold" if str(weight).lower() in {"bold", "600", "700", "800", "900"} else "normal"
        family = style.get("font-family", "TkDefaultFont").split(",")[0].strip().strip("\"'") or "TkDefaultFont"
        slant = "italic" if style.get("font-style", "normal").lower() in {"italic", "oblique"} else "roman"
        underline = "underline" in style.get("text-decoration", "")
        key = (family, size, weight_name, slant, underline)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = tkfont.Font(family=family, size=size, weight=weight_name, slant=slant, underline=underline)
            except tk.TclError:
                self._font_cache[key] = tkfont.Font(family="TkDefaultFont", size=size, weight=weight_name, slant=slant, underline=underline)
        return self._font_cache[key]

    def _font_size(self, style: dict[str, str]) -> int:
        parent_size = 16
        raw = style.get("font-size", "16px")
        n = css_length(raw, parent_size, rem=16, em=parent_size, vw=self._vw(), vh=self._vh())
        return max(6, int(round(n if n is not None else parent_size)))

    def _font_line_height(self, style: dict[str, str]) -> int:
        size = self._font_size(style)
        raw = style.get("line-height", "normal")
        if raw == "normal":
            return max(10, int(round(size * 1.2)))
        n = css_length(raw, size, rem=16, em=size, vw=self._vw(), vh=self._vh())
        return max(size, int(round(n if n is not None else size * 1.2)))

    def _line_height(self, style: dict[str, str]) -> float:
        return float(self._font_line_height(style))

    def _text_color(self, style: dict[str, str]) -> str:
        return css_color(style.get("color", "#111827"), "#111827")

    def _background_color(self, style: dict[str, str]) -> str:
        raw = style.get("background-color", style.get("background", "transparent"))
        return css_color(raw, "transparent")

    def _border_lengths(self, style: dict[str, str], base: float, em: int, vw: float, vh: float) -> tuple[float, float, float, float]:
        return tuple(self._resolve_dimension(style.get(f"border-{side}-width", "0px"), base, em, vw, vh) or 0 for side in ("top", "right", "bottom", "left"))  # type: ignore[return-value]

    def _box_lengths(self, style: dict[str, str], kind: str, base: float, em: int, vw: float, vh: float) -> tuple[float, float, float, float]:
        vals = tuple(self._resolve_dimension(style.get(f"{kind}-{side}", "0px"), base, em, vw, vh) or 0 for side in ("top", "right", "bottom", "left"))
        return vals  # type: ignore[return-value]

    def _resolve_dimension(self, raw: str | None, base: float, em: int, vw: float, vh: float) -> float | None:
        if raw is None:
            return None
        return css_length(raw, base, rem=16, em=em, vw=vw, vh=vh)

    def _vw(self) -> int:
        return max(360, int(self.canvas.winfo_width() or 1000))

    def _vh(self) -> int:
        return max(240, int(self.canvas.winfo_height() or 800))
