from safehtmltk.parser import parse_document, parse_html
from safehtmltk.css import Stylesheet, selector_matches


def test_parse_and_css():
    doc, css, js = parse_html('''<html><head><style>#x { color: red; } .a { font-size: 20px; }</style><script>let x=1</script></head><body><p id="x" class="a">Hi</p></body></html>''')
    assert js.strip() == "let x=1"
    node = doc.get_element_by_id("x")
    assert node is not None
    style = Stylesheet(css).style_for(node)
    assert style["color"] == "red"
    assert style["font-size"] == "20px"
    assert doc.query_selector("#x") is node


def test_selector_features():
    doc, _, _ = parse_html('<div class="wrap"><p class="a b" data-kind="feature">One</p><span>Two</span></div>')
    node = doc.query_selector("p.a")
    assert node is not None
    assert selector_matches(node, '.wrap > p[data-kind="feature"]')
    assert len(doc.query_selector_all("p.a, span")) == 2


def test_external_assets_and_snapshot_parents():
    doc, css, assets = parse_document('''<html><head><link rel="stylesheet" href="style.css"><script src="app.js"></script></head><body><section id="main"><p>Hi</p></section></body></html>''')
    assert css == ""
    assert assets.stylesheets == ("style.css",)
    assert assets.scripts == ("app.js",)
    rows = {row["htmlId"]: row for row in doc.snapshot() if row["htmlId"]}
    assert rows["main"]["parentId"] == next(row["id"] for row in doc.snapshot() if row["htmlId"] == "" and row["tag"] == "body")


def test_css_variables_and_box_shorthands():
    doc, css, _ = parse_document('<html><body><div id="x" class="card">Hello</div></body></html>')
    sheet = Stylesheet('''html { --panel: #ffffff; } .card { background: var(--panel); padding: 8px 12px; border: 2px solid #123456; margin: 4px auto; max-width: 500px; }''')
    html = next(n for n in doc.root.iter() if n.tag == "html")
    body = next(n for n in doc.root.iter() if n.tag == "body")
    inherited = sheet.compute(html, None)
    body_style = sheet.compute(body, inherited)
    card = doc.query_selector('#x')
    card_style = sheet.compute(card, body_style)
    assert card_style["background-color"] == "#ffffff"
    assert card_style["padding-left"] == "12px"
    assert card_style["border-left-width"] == "2px"
