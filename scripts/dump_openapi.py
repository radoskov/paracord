#!/usr/bin/env python3
"""Dump the FastAPI application's OpenAPI schema to a file (SPEC §10).

The committed ``backend/openapi.json`` is the API contract used for frontend/agent client
generation. Regenerate it with ``make openapi``; ``make openapi-check`` fails when it drifts.

Usage:
    python scripts/dump_openapi.py [OUTPUT_PATH]

Writes pretty-printed JSON with stable (sorted) key order so diffs stay clean. Defaults to
``backend/openapi.json`` relative to the repo root.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import app  # noqa: E402


def main() -> None:
    default = Path(__file__).resolve().parents[1] / "backend" / "openapi.json"
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    schema = app.openapi()
    out.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote OpenAPI schema → {out}")


if __name__ == "__main__":
    main()
