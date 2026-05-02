from __future__ import annotations

import json
from pathlib import Path


def load_prompt(prompt_dir: str | Path, name: str) -> str:
    path = Path(prompt_dir) / name
    return path.read_text(encoding="utf-8")


def build_json_user_prompt(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


