#!/usr/bin/env python3
from __future__ import annotations

from _bootstrap import bootstrap

bootstrap()

from paperpilot.update_paper_memory import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
