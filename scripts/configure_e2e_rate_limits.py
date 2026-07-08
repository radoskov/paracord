#!/usr/bin/env python3
"""Configure local E2E rate limits.

This script is intentionally local/container-only. Do not expose it as an HTTP
endpoint.
"""

from __future__ import annotations

import os
import sys

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.app_config import update_rate_limits
from app.models.app_config import (
    _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN,
    _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN,
)

DEFAULT_PER_CLIENT = _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN
DEFAULT_GLOBAL = _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN

E2E_PER_CLIENT = 10_000
E2E_GLOBAL = 10_000


def _refuse_production() -> None:
    settings = get_settings()
    environment = settings.environment.lower()

    if environment not in {"development", "test"} and os.environ.get(
        "ALLOW_PRODUCTION_E2E_RATE_LIMIT_CONFIG"
    ) != "1":
        raise SystemExit(
            "Refusing to modify rate limits outside development/test. "
            f"PARACORD_ENV={settings.environment!r}"
        )


def _set_limits(per_client: int, global_limit: int) -> None:
    with SessionLocal() as db:
        update_rate_limits(
            db,
            per_client_per_min=per_client,
            global_per_min=global_limit,
            actor_user_id=None,
        )
        db.commit()


def main() -> int:
    _refuse_production()

    mode = sys.argv[1] if len(sys.argv) > 1 else "enable"

    if mode == "enable":
        _set_limits(E2E_PER_CLIENT, E2E_GLOBAL)
        print(
            "Configured E2E rate limits: "
            f"per_client_per_min={E2E_PER_CLIENT}, "
            f"global_per_min={E2E_GLOBAL}"
        )
        return 0

    if mode == "reset":
        _set_limits(DEFAULT_PER_CLIENT, DEFAULT_GLOBAL)
        print(
            "Reset rate limits: "
            f"per_client_per_min={DEFAULT_PER_CLIENT}, "
            f"global_per_min={DEFAULT_GLOBAL}"
        )
        return 0

    raise SystemExit("Usage: configure_e2e_rate_limits.py [enable|reset]")


if __name__ == "__main__":
    raise SystemExit(main())
