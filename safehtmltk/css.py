from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class Rule:
    selectors: tuple[str, ...]
    declarations: dict[str, str]
    order: int


INHERITED_PROPERTIES = {
    "color", "font-family", "font-size", "font-weight", "font-style",
    "line-height", "text-align", "text-decoration", "letter-spacing",
    "word-spacing", "white-space", "visibility",
}

DEFAULTS = {
    "display": "block",
    "color": "#111827",
    "background-color": "transparent",
    "font-family": "TkDefaultFont",
    "font-size": "16px",
    "font-weight": "400",
    "font-style": "normal",
    "line-height": "normal",
    "text-align": "start",
    "text-decoration": "none",
    "white-space": "normal",
    "visibility": "visible",
    "box-sizing": "content-box",
    "margin-top": "0px", "margin-right": "0px", "margin-bottom": "0px", "margin-left": "0px",
    "padding-top": "0px", "padding-right": "0px", "padding-bottom": "0px", "padding-left": "0px",
    "border-top-width": "0px", "border-right-width": "0px", "border-bottom-width": "0px", "border-left-width": "0px",
    "border-top-style": "none", "border-right-style": "none", "border-bottom-style": "none", "border-left-style": "none",
    "border-top-color": "#d1d5db", "border-right-color": "#d1d5db", "border-bottom-color": "#d1d5db", "border-left-color": "#d1d5db",
    "flex-direction": "row", "flex-wrap": "nowrap", "justify-content": "flex-start", "align-items": "stretch",
    "align-content": "stretch", "gap": "0px", "row-gap": "0px", "column-gap": "0px",
    "flex-grow": "0", "flex-shrink": "1", "flex-basis": "auto",
    "overflow": "visible", "overflow-x": "visible", "overflow-y": "visible",
    "opacity": "1", "border-radius": "0px",
}

TAG_DEFAULTS = {
    "html": {"display": "block", "font-size": "16px"},
    "body": {"display": "block", "margin-top": "8px", "margin-right": "8px", "margin-bottom": "8px", "margin-left": "8px"},
    "h1": {"font-size": "2em", "font-weight": "700", "margin-top": ".67em", "margin-bottom": ".67em"},
    "h2": {"font-size": "1.5em", "font-weight": "700", "margin-top": ".83em", "margin-bottom": ".83em"},
    "h3": {"font-size": "1.17em", "font-weight": "700", "margin-top": "1em", "margin-bottom": "1em"},
    "h4": {"font-weight": "700", "margin-top": "1.33em", "margin-bottom": "1.33em"},
    "h5": {"font-size": ".83em", "font-weight": "700", "margin-top": "1.67em", "margin-bottom": "1.67em"},
    "h6": {"font-size": ".67em", "font-weight": "700", "margin-top": "2.33em", "margin-bottom": "2.33em"},
    "p": {"margin-top": "1em", "margin-bottom": "1em"},
    "ul": {"margin-top": "1em", "margin-bottom": "1em", "padding-left": "40px"},
    "ol": {"margin-top": "1em", "margin-bottom": "1em", "padding-left": "40px"},
    "li": {"display": "list-item"},
    "a": {"color": "#2563eb", "text-decoration": "underline"},
    "strong": {"font-weight": "700"},
    "b": {"font-weight": "700"},
    "em": {"font-style": "italic"},
    "i": {"font-style": "italic"},
    "small": {"font-size": ".875em"},
    "code": {"font-family": "TkFixedFont"},
    "pre": {"font-family": "TkFixedFont", "white-space": "pre", "margin-top": "1em", "margin-bottom": "1em"},
    "button": {"display": "inline-block"},
    "input": {"display": "inline-block"},
    "textarea": {"display": "inline-block"},
    "select": {"display": "inline-block"},
}


def _split_top_level(source: str, delimiter: str = ",") -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    depth = 0
    for ch in source:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == delimiter and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf).strip())
    return [x for x in out if x]


def _split_declarations(body: str) -> list[str]:
    return _split_top_level(body, ";")


def parse_css(source: str) -> list[Rule]:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    rules: list[Rule] = []
    order = 0
    pos = 0
    while pos < len(source):
        brace = source.find("{", pos)
        if brace < 0:
            break
        header = source[pos:brace].strip()
        depth = 1
        i = brace + 1
        quote = ""
        while i < len(source) and depth:
            ch = source[i]
            if quote:
                if ch == quote and source[i - 1] != "\\":
                    quote = ""
            elif ch in "'\"":
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        body = source[brace + 1:i - 1] if depth == 0 else source[brace + 1:]
        pos = i
        if header.startswith("@"):
            # @media/@supports blocks are intentionally treated conservatively.
            # Their nested rules are not applied because evaluating conditions
            # would make the CSS engine less predictable.
            continue
        selectors = tuple(_split_top_level(header, ","))
        declarations = parse_declarations(body)
        if selectors and declarations:
            rules.append(Rule(selectors, declarations, order))
            order += 1
    return rules


def parse_declarations(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for piece in _split_declarations(body):
        if ":" not in piece:
            continue
        k, v = piece.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if k and v:
            out[k] = v
    return expand_shorthands(out)


def expand_shorthands(declarations: dict[str, str]) -> dict[str, str]:
    out = dict(declarations)

    if "margin" in declarations:
        a = css_box_values(declarations["margin"])
        keys = ("margin-top", "margin-right", "margin-bottom", "margin-left")
        out.update({k: v for k, v in zip(keys, a)})
    if "padding" in declarations:
        a = css_box_values(declarations["padding"])
        keys = ("padding-top", "padding-right", "padding-bottom", "padding-left")
        out.update({k: v for k, v in zip(keys, a)})

    for side in ("top", "right", "bottom", "left"):
        key = f"border-{side}"
        if key in declarations:
            parts = _split_top_level(declarations[key], " ")
            # _split_top_level cannot distinguish multiple spaces as separators,
            # so normalize this shorthand with a regex tokenization below.
            parts = re.findall(r"(?:[^\s()]|\([^)]*\))+", declarations[key])
            if parts:
                for part in parts:
                    low = part.lower()
                    if low in {"none", "solid", "dashed", "dotted", "double", "groove", "ridge", "inset", "outset"}:
                        out[f"border-{side}-style"] = low
                    elif css_length_like(part):
                        out[f"border-{side}-width"] = part
                    else:
                        out[f"border-{side}-color"] = part

    if "border" in declarations:
        parts = re.findall(r"(?:[^\s()]|\([^)]*\))+", declarations["border"])
        for side in ("top", "right", "bottom", "left"):
            for part in parts:
                low = part.lower()
                if low in {"none", "solid", "dashed", "dotted", "double", "groove", "ridge", "inset", "outset"}:
                    out[f"border-{side}-style"] = low
                elif css_length_like(part):
                    out[f"border-{side}-width"] = part
                else:
                    out[f"border-{side}-color"] = part

    for prop, prefix in (("border-width", "border-{side}-width"), ("border-style", "border-{side}-style"), ("border-color", "border-{side}-color")):
        if prop in declarations:
            vals = css_box_values(declarations[prop])
            for side, val in zip(("top", "right", "bottom", "left"), vals):
                out[prefix.format(side=side)] = val

    if "font" in declarations:
        font_tokens = re.findall(r"(?:[^\s()]|\([^)]*\))+", declarations["font"])
        for token in font_tokens:
            low = token.lower()
            if low in {"normal", "bold", "bolder", "lighter", "italic", "oblique"} or low.isdigit():
                if low in {"italic", "oblique"}: out["font-style"] = low
                elif low in {"bold", "bolder", "lighter"} or low.isdigit(): out["font-weight"] = token
            elif re.match(r"^[+-]?(?:\d*\.\d+|\d+)(?:px|em|rem|pt|%)?(?:/.*)?$", token, re.I):
                out["font-size"] = token.split("/", 1)[0]
            elif out.get("font-size"):
                out["font-family"] = token

    if "background" in declarations and "background-color" not in declarations:
        # Safe subset: accept a plain color as background-color.
        bg = declarations["background"].strip()
        if is_color(bg):
            out["background-color"] = bg

    if "flex" in declarations:
        parts = declarations["flex"].split()
        if parts:
            if parts[0] == "none":
                out.update({"flex-grow": "0", "flex-shrink": "0", "flex-basis": "auto"})
            elif parts[0] == "auto":
                out.update({"flex-grow": "1", "flex-shrink": "1", "flex-basis": "auto"})
            else:
                nums = [p for p in parts if re.fullmatch(r"[+-]?(?:\d*\.\d+|\d+)", p)]
                if nums:
                    out["flex-grow"] = nums[0]
                    if len(nums) > 1:
                        out["flex-shrink"] = nums[1]
                nonnums = [p for p in parts if p not in nums]
                if nonnums:
                    out["flex-basis"] = nonnums[-1]

    return out


def _selector_tokenize(selector: str) -> list[str]:
    selector = re.sub(r"\s*([>+~])\s*", r" \1 ", selector.strip())
    tokens: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    pending_space = False
    for ch in selector:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in "[(:":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch.isspace() and depth == 0:
            if buf:
                tokens.append("".join(buf))
                buf = []
            pending_space = bool(tokens)
        else:
            if pending_space and ch not in ">+~":
                tokens.append(" ")
            pending_space = False
            buf.append(ch)
            if ch in ">+~" and depth == 0:
                if buf[:-1]:
                    tokens.append("".join(buf[:-1]))
                    buf = []
                tokens.append(ch)
    if buf:
        tokens.append("".join(buf))
    return [x for x in tokens if x]


def selector_matches(node, selector: str) -> bool:
    tokens = _selector_tokenize(selector)
    if not tokens:
        return False
    # Evaluate from right to left. The supported combinators are descendant and child.
    idx = len(tokens) - 1
    if tokens[idx] in {">", "+", "~", " "}:
        return False
    if not matches_simple(node, tokens[idx]):
        return False
    current = node
    idx -= 1
    combinator = " "
    while idx >= 0:
        token = tokens[idx]
        if token in {">", "+", "~", " "}:
            combinator = token
            idx -= 1
            continue
        if combinator == ">":
            current = current.parent
            if current is None or current.tag == "#text" or not matches_simple(current, token):
                return False
        elif combinator == "+":
            sib = previous_element_sibling(current)
            if sib is None or not matches_simple(sib, token):
                return False
            current = sib
        elif combinator == "~":
            sib = previous_element_sibling(current)
            found = False
            while sib is not None:
                if matches_simple(sib, token):
                    found = True
                    current = sib
                    break
                sib = previous_element_sibling(sib)
            if not found:
                return False
        else:
            anc = current.parent
            found = False
            while anc is not None:
                if anc.tag != "#text" and matches_simple(anc, token):
                    found = True
                    current = anc
                    break
                anc = anc.parent
            if not found:
                return False
        idx -= 1
        combinator = " "
    return True


def previous_element_sibling(node) -> object | None:
    parent = getattr(node, "parent", None)
    if parent is None:
        return None
    prev = None
    for child in parent.children:
        if child is node:
            return prev
        if child.tag != "#text":
            prev = child
    return None


def matches_simple(node, selector: str) -> bool:
    if node.tag == "#text":
        return False
    selector = selector.strip()
    if not selector:
        return False
    # Pseudo-classes are handled only when they are static/document-safe.
    for pseudo in re.findall(r":([\w-]+)(?:\([^)]*\))?", selector):
        if pseudo not in {"root", "first-child", "last-child", "checked", "disabled", "empty"}:
            return False
        if pseudo == "root" and getattr(node, "parent", None) is not None and node.parent.tag != "document":
            return False
        if pseudo == "first-child" and previous_element_sibling(node) is not None:
            return False
        if pseudo == "last-child":
            elems = [c for c in node.parent.children if c.tag != "#text"] if node.parent else []
            if not elems or elems[-1] is not node:
                return False
        if pseudo == "checked" and "checked" not in node.attrs:
            return False
        if pseudo == "disabled" and "disabled" not in node.attrs:
            return False
        if pseudo == "empty" and (node.children or node.text_content()):
            return False
    selector = re.sub(r":[\w-]+(?:\([^)]*\))?", "", selector)

    tag = re.match(r"^([a-zA-Z][\w-]*|\*)", selector)
    if tag and tag.group(1) != "*" and node.tag.lower() != tag.group(1).lower():
        return False
    sid = re.search(r"#([\w-]+)", selector)
    if sid and node.attrs.get("id", "") != sid.group(1):
        return False
    for cls in re.findall(r"\.([\w-]+)", selector):
        if cls not in node.attrs.get("class", "").split():
            return False
    for m in re.finditer(r"\[\s*([\w:-]+)(?:\s*(\^=|\$=|\*=|~=|\|=|!=|=)\s*(?:\"([^\"]*)\"|'([^']*)'|([^\]]+)))?\s*\]", selector):
        name, op = m.group(1).lower(), m.group(2)
        wanted = next((x for x in m.groups()[2:] if x is not None), "").strip()
        actual = node.attrs.get(name)
        if op is None:
            if actual is None:
                return False
        elif actual is None:
            return False
        elif op == "=" and actual != wanted:
            return False
        elif op == "!=" and actual == wanted:
            return False
        elif op == "^=" and not actual.startswith(wanted):
            return False
        elif op == "$=" and not actual.endswith(wanted):
            return False
        elif op == "*=" and wanted not in actual:
            return False
        elif op == "~=" and wanted not in actual.split():
            return False
        elif op == "|=" and not (actual == wanted or actual.startswith(wanted + "-")):
            return False
    return True


def specificity(selector: str) -> tuple[int, int, int]:
    # IDs > classes/attributes/pseudo-classes > elements.
    ids = len(re.findall(r"#[\w-]+", selector))
    attrs = len(re.findall(r"\[[^\]]+\]", selector))
    pseudos = len(re.findall(r":[\w-]+", selector))
    classes = len(re.findall(r"\.[\w-]+", selector))
    elements = len([x for x in re.findall(r"(?:^|[ >+~])([a-zA-Z][\w-]*)", selector) if x not in {"where", "is", "not"}])
    return ids, classes + attrs + pseudos, elements


class Stylesheet:
    def __init__(self, source: str):
        self.rules = parse_css(source)

    def style_for(self, node) -> dict[str, str]:
        return self.compute(node, None)

    def matched_declarations(self, node) -> dict[str, str]:
        matched: list[tuple[tuple[int, int, int], int, dict[str, str]]] = []
        for rule in self.rules:
            for selector in rule.selectors:
                if selector_matches(node, selector):
                    matched.append((specificity(selector), rule.order, rule.declarations))
                    break
        matched.sort(key=lambda x: (x[0], x[1]))
        result: dict[str, str] = {}
        for _, _, declarations in matched:
            result.update(declarations)
        result.update(parse_declarations(node.attrs.get("style", "")))
        return result

    def compute(self, node, parent_style: dict[str, str] | None, *, root_font_size: int = 16) -> dict[str, str]:
        parent_style = parent_style or {}
        result = dict(DEFAULTS)
        result.update(TAG_DEFAULTS.get(node.tag, {}))
        for prop in INHERITED_PROPERTIES:
            if prop in parent_style:
                result[prop] = parent_style[prop]
        # Custom properties inherit.
        for key, value in parent_style.items():
            if key.startswith("--"):
                result[key] = value
        result.update(self.matched_declarations(node))
        variables = {k: v for k, v in result.items() if k.startswith("--")}
        for key, value in list(result.items()):
            if isinstance(value, str) and "var(" in value:
                result[key] = resolve_vars(value, variables)
        if result.get("background", "").strip() and result.get("background-color", "transparent") == "transparent":
            bg = result.get("background", "").strip()
            if is_color(bg):
                result["background-color"] = bg
        # Convenience normalization for camelCase-style properties arriving through JS.
        normalized = {}
        for key, value in result.items():
            normalized[normalize_property(key)] = value
        return normalized


def normalize_property(name: str) -> str:
    table = {
        "backgroundcolor": "background-color",
        "fontsize": "font-size", "fontweight": "font-weight", "fontfamily": "font-family",
        "fontstyle": "font-style", "lineheight": "line-height", "textalign": "text-align",
        "textdecoration": "text-decoration", "letterspacing": "letter-spacing", "wordspacing": "word-spacing",
        "flexdirection": "flex-direction", "flexwrap": "flex-wrap", "justifycontent": "justify-content",
        "alignitems": "align-items", "aligncontent": "align-content", "flexgrow": "flex-grow",
        "flexshrink": "flex-shrink", "flexbasis": "flex-basis", "boxsizing": "box-sizing",
        "borderradius": "border-radius", "overflowx": "overflow-x", "overflowy": "overflow-y",
    }
    return table.get(name.lower(), name.lower())


def resolve_vars(value: str, variables: dict[str, str]) -> str:
    pattern = re.compile(r"var\(\s*(--[\w-]+)(?:\s*,\s*([^)]*))?\)")
    previous = None
    result = value
    for _ in range(8):
        if result == previous:
            break
        previous = result
        def repl(match):
            key = match.group(1)
            fallback = match.group(2)
            return variables.get(key, fallback or "")
        result = pattern.sub(repl, result)
    return result


def css_length_like(value: str) -> bool:
    return bool(re.fullmatch(r"[+-]?(?:\d*\.\d+|\d+)(?:px|em|rem|%|pt|pc|in|cm|mm|vw|vh|vmin|vmax|ch|ex)?", value.strip(), re.I))


def is_color(value: str) -> bool:
    v = value.strip().lower()
    if v in {"transparent", "currentcolor"}:
        return True
    if re.fullmatch(r"#[0-9a-f]{3,8}", v):
        return True
    if re.fullmatch(r"(?:rgb|rgba|hsl|hsla)\([^)]*\)", v):
        return True
    return bool(re.fullmatch(r"[a-z][\w-]*", v))


def css_box_values(value: str) -> tuple[str, str, str, str]:
    parts = re.findall(r"(?:[^\s()]|\([^)]*\))+", value.strip())
    if not parts:
        return "0", "0", "0", "0"
    if len(parts) == 1:
        return parts[0], parts[0], parts[0], parts[0]
    if len(parts) == 2:
        return parts[0], parts[1], parts[0], parts[1]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2], parts[1]
    return parts[0], parts[1], parts[2], parts[3]


def parse_calc(value: str, base: float, rem: float, em: float, vw: float, vh: float) -> float | None:
    m = re.fullmatch(r"calc\(\s*(.+?)\s*\)", value.strip(), re.I)
    if not m:
        return None
    expr = m.group(1)
    tokens = re.findall(r"[+-]?[^+-]+", expr.replace(" ", ""))
    if not tokens:
        return None
    total = 0.0
    for token in tokens:
        sign = 1.0
        if token.startswith("+"):
            token = token[1:]
        elif token.startswith("-"):
            sign = -1.0
            token = token[1:]
        n = css_length(token, base, rem=rem, em=em, vw=vw, vh=vh)
        if n is None:
            return None
        total += sign * n
    return total


def css_length(value: str, base: float = 0, *, rem: float = 16, em: float = 16, vw: float = 1000, vh: float = 800) -> float | None:
    v = (value or "").strip().lower()
    if not v or v == "auto":
        return None
    calc = parse_calc(v, base, rem, em, vw, vh)
    if calc is not None:
        return calc
    m = re.fullmatch(r"([+-]?(?:\d*\.\d+|\d+))(px|%|rem|em|pt|pc|in|cm|mm|vw|vh|vmin|vmax|ch|ex)?", v)
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2) or "px"
    if unit == "px": return n
    if unit == "%": return base * n / 100.0
    if unit == "rem": return rem * n
    if unit == "em": return em * n
    if unit == "pt": return n * 96 / 72
    if unit == "pc": return n * 16
    if unit == "in": return n * 96
    if unit == "cm": return n * 96 / 2.54
    if unit == "mm": return n * 96 / 25.4
    if unit == "vw": return vw * n / 100
    if unit == "vh": return vh * n / 100
    if unit == "vmin": return min(vw, vh) * n / 100
    if unit == "vmax": return max(vw, vh) * n / 100
    if unit == "ch": return em * 0.5 * n
    if unit == "ex": return em * 0.5 * n
    return None


def css_px(value: str, default: int = 0, base: int = 16, rem: int = 16, vw: int = 1000, vh: int = 800) -> int:
    n = css_length(value, base, rem=rem, em=base, vw=vw, vh=vh)
    return default if n is None else int(round(n))


def css_color(value: str, default: str) -> str:
    value = (value or "").strip()
    if not value or value.lower() in {"transparent", "currentcolor"}:
        return default if value.lower() == "transparent" else value
    if re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?(?:[0-9a-fA-F]{2})?", value):
        if len(value) == 4:
            return "#" + "".join(ch * 2 for ch in value[1:])
        return value[:7]
    rgb = re.fullmatch(r"rgba?\(\s*([+-]?\d+(?:\.\d+)?)\s*[, ]\s*([+-]?\d+(?:\.\d+)?)\s*[, ]\s*([+-]?\d+(?:\.\d+)?)(?:\s*[,/]\s*([\d.]+))?\s*\)", value, re.I)
    if rgb:
        vals = [max(0, min(255, int(float(rgb.group(i))))) for i in range(1, 4)]
        return "#%02x%02x%02x" % tuple(vals)
    return value if re.fullmatch(r"[a-zA-Z][\w-]*", value) else default


def css_box(value: str, default: int = 0, base: int = 16) -> tuple[int, int, int, int]:
    vals = css_box_values(value)
    return tuple(css_px(x, default, base=base) for x in vals)  # type: ignore[return-value]
