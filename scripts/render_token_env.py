#!/usr/bin/env python3
import json
import sys
from pathlib import Path


def flatten(prefix, value, output):
    if isinstance(value, dict):
        for key, child in value.items():
            next_prefix = f"{prefix}_{key}" if prefix else key
            flatten(next_prefix, child, output)
    elif isinstance(value, list):
        output.append((prefix.upper(), ",".join(str(item) for item in value)))
    else:
        output.append((prefix.upper(), str(value).lower() if isinstance(value, bool) else str(value)))


def main() -> int:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/token_optimization.json")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    output = []
    flatten("", data, output)
    for key, value in output:
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
