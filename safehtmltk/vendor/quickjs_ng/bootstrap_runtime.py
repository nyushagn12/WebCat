from __future__ import annotations

import hashlib
from pathlib import Path
import platform
import sys
import urllib.request
import zipfile

VERSION = "0.17.0.1"
# Pinned official PyPI artifacts. Add more platform triples as needed.
WHEELS = {
    ("Linux", "x86_64"): (
        "quickjs_ng-0.17.0.1-cp310-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
        "https://files.pythonhosted.org/packages/db/d7/95ad035994e03478ad83275895ea218c3c1c7adc821aa1e2f3de4ed38d72/quickjs_ng-0.17.0.1-cp310-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
        "a1f7352b7e508346c8254f5ae790799898ebd1b2df2538bf9b7070a757faf903",
    ),
    ("Windows", "AMD64"): (
        "quickjs_ng-0.17.0.1-cp310-abi3-win_amd64.whl",
        "https://files.pythonhosted.org/packages/5b/67/623df580c13e7cc3bbce5b15c41658dfb753d99839fab25695eef99df34a/quickjs_ng-0.17.0.1-cp310-abi3-win_amd64.whl",
        "355511615baa17bc98092680f068e3536ea1ee5a650b16f59bf6871f497f3780",
    ),
    ("Darwin", "arm64"): (
        "quickjs_ng-0.17.0.1-cp310-abi3-macosx_11_0_arm64.whl",
        "https://files.pythonhosted.org/packages/29/6e/8934b81ccb8974c57a385bd1ea63f5d46acef25a7a0ec93850a591966656/quickjs_ng-0.17.0.1-cp310-abi3-macosx_11_0_arm64.whl",
        "330f6bdb61ebf8102d650af799dd093924f34cc8be449fed97102147e133f685",
    ),
}


def install_here() -> Path:
    key = (platform.system(), platform.machine())
    if key not in WHEELS:
        raise RuntimeError(f"No pre-pinned quickjs-ng wheel for {key}")
    filename, url, expected = WHEELS[key]
    root = Path(__file__).resolve().parents[1]
    archive = root / filename
    deps = root / "pydeps"
    deps.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {filename} ...")
    with urllib.request.urlopen(url, timeout=120) as r, archive.open("wb") as fh:
        while True:
            chunk = r.read(128 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 mismatch for {filename}: {actual}")

    target = deps / VERSION
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)
    archive.unlink(missing_ok=True)
    print(f"Installed vendored runtime in {target}")
    return target


if __name__ == "__main__":
    install_here()
