#!/usr/bin/env python3
"""Create a zip archive of the current source tree."""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "paracord_source_archive.zip"

with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for path in ROOT.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            zf.write(path, path.relative_to(ROOT.parent))

print(OUT)
