from __future__ import annotations

import json
import os
from pathlib import Path


def config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif os.name == "posix" and os.environ.get("XDG_CONFIG_HOME"):
        base = Path(os.environ["XDG_CONFIG_HOME"])
    else:
        base = Path.home() / ".config"
    return base / "safehtmltk" / "config.json"


def load_qjs_path() -> str | None:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = str(data.get("qjs", "")).strip()
        candidate = Path(value).expanduser().resolve()
        if value and candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return str(candidate)
    except Exception:
        pass
    return None


def save_qjs_path(value: str | None) -> None:
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"qjs": value or ""}, indent=2) + "\n", encoding="utf-8")
    except OSError:
        # Configuration is a convenience; failure must never prevent launching.
        pass
