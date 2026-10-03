# Bundled quickjs-ng runtime

WebCat imports the Python module as `quickjs`; the distribution that provides
that module is `quickjs-ng`.

The upstream project publishes pre-built `abi3` wheels for CPython 3.10+ on
Linux, Windows and macOS. WebCat's loader looks first in `vendor/pydeps/` for
a matching unpacked wheel, then falls back to the normal Python environment.

This source distribution contains the vendoring metadata and loader, but the
native wheels are intentionally not fabricated here: a wheel contains a
platform-specific compiled extension and must be obtained intact from the
upstream release. `vendor/quickjs_ng/bootstrap_runtime.py` can download and
verify a pinned wheel without invoking pip when a network connection is
available.
