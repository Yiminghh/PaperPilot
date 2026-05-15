#!/usr/bin/env python3
from __future__ import annotations

from _bootstrap import bootstrap

bootstrap()

from paperpilot.analyze_recommendation_feedback import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
