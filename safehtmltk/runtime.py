from __future__ import annotations

from pathlib import Path
import importlib.metadata
import sys


def activate_vendored_quickjs() -> Path | None:
    """Put an unpacked vendored quickjs-ng wheel ahead of site-packages."""
    root = Path(__file__).resolve().parent
    deps = root / "vendor" / "pydeps"
    if not deps.is_dir():
        return None
    candidates = sorted((p for p in deps.iterdir() if p.is_dir()), reverse=True)
    for candidate in candidates:
        package = candidate / "quickjs"
        if package.is_dir() and (any(package.glob("_quickjs*.so")) or any(package.glob("_quickjs*.pyd"))):
            path = str(candidate)
            if path not in sys.path:
                sys.path.insert(0, path)
            return candidate
    return None


def quickjs_available() -> bool:
    activate_vendored_quickjs()
    try:
        import quickjs  # noqa: F401
    except Exception:
        return False
    try:
        version = importlib.metadata.version("quickjs-ng")
        return version.startswith("0.17.")
    except importlib.metadata.PackageNotFoundError:
        # A vendored unpacked wheel may not be visible through some metadata
        # implementations; import success after vendor activation is enough.
        return bool(activate_vendored_quickjs())
