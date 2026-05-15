from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = "config/paper-daily-config.yaml"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config(config_path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("Missing dependency: pyyaml. Run `python -m pip install pyyaml`.") from exc
    path = Path(config_path)
    if not path.is_absolute():
        path = project_root() / path
    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    paths = config.setdefault("paths", {})
    paths.setdefault("project_root", str(project_root()))
    return config


def get_nested(config: dict[str, Any], dotted_key: str, default: Any = "") -> Any:
    current: Any = config
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def env_key(dotted_key: str) -> str:
    return "PAPERPILOT_" + dotted_key.upper().replace(".", "_")


def config_value(config: dict[str, Any], dotted_key: str, default: Any = "") -> Any:
    override = os.getenv(env_key(dotted_key))
    if override is not None:
        return override
    return get_nested(config, dotted_key, default)


def resolve_path(config: dict[str, Any], dotted_key: str, default: str = "") -> Path:
    value = str(config_value(config, dotted_key, default) or default)
    if not value:
        return Path("")
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    root = Path(str(config_value(config, "paths.project_root", project_root()))).expanduser()
    return root / path


def run_dir(config: dict[str, Any], date: str) -> Path:
    return resolve_path(config, "paths.runs_dir", "runs") / date


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument(
        "--get",
        action="append",
        required=True,
        help="Dotted config key, e.g. paths.logs_dir. Repeat to print multiple values.",
    )
    parser.add_argument("--path", action="store_true", help="Resolve value as a project-relative path")
    args = parser.parse_args()

    config = load_config(args.config)
    for key in args.get:
        if args.path:
            print(resolve_path(config, key))
        else:
            print(config_value(config, key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
