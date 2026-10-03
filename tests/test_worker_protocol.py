from __future__ import annotations

import ast
from pathlib import Path


def _bootstrap() -> str:
    source = (Path(__file__).parents[1] / "safehtmltk" / "js_worker.py").read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "BOOTSTRAP"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("BOOTSTRAP constant not found")


def test_worker_acknowledges_events_and_has_fetch_bridge() -> None:
    js = _bootstrap()
    assert 'emit({op:"done",requestId:Number(msg.requestId)' in js
    assert 'op:"network_request"' in js
    assert 'msg.op==="network_response"' in js
    assert 'pendingFetches' in js
