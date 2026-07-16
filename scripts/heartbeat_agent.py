#!/usr/bin/env python3
from __future__ import annotations

try:
    from heartbeat_protocol import main
except ModuleNotFoundError:
    from scripts.heartbeat_protocol import main


if __name__ == "__main__":
    raise SystemExit(main())
