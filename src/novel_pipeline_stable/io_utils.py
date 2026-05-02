from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel


def _fs_path(path: str | Path) -> Path:
    target = Path(path)
    if os.name != "nt":
        return target
    raw = str(target.resolve(strict=False))
    if raw.startswith("\\\\?\\"):
        return Path(raw)
    if raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + raw.lstrip("\\"))
    return Path("\\\\?\\" + raw)


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    _fs_path(target).mkdir(parents=True, exist_ok=True)
    return target


def read_text(path: str | Path) -> str:
    return _fs_path(path).read_text(encoding="utf-8-sig")


def write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    _atomic_write_text(target, text)


def write_markdown(path: str | Path, text: str) -> None:
    target = Path(path)
    _atomic_write_text(target, text, encoding="utf-8-sig")


def write_json(path: str | Path, payload: dict | list) -> None:
    target = Path(path)
    _atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2))


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> None:
    target = Path(path)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    text = "\n".join(lines)
    if text:
        text = f"{text}\n"
    _atomic_write_text(target, text)


def write_model(path: str | Path, model: BaseModel) -> None:
    write_json(path, model.model_dump(mode="json"))


def read_json(path: str | Path) -> dict | list:
    return json.loads(_fs_path(path).read_text(encoding="utf-8-sig"))


def _atomic_write_text(target: Path, text: str, *, encoding: str = "utf-8") -> None:
    fs_target = _fs_path(target)
    ensure_dir(fs_target.parent)
    temp_path = fs_target.with_name(f".{target.name}.{time.time_ns()}.tmp")
    try:
        temp_path.write_text(text, encoding=encoding, newline="\n")
        for attempt in range(6):
            try:
                os.replace(temp_path, fs_target)
                break
            except PermissionError:
                if attempt >= 5:
                    raise
                time.sleep(0.1 * (attempt + 1))
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def read_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    for raw_line in _fs_path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def iter_json_files(directory: str | Path) -> Iterable[Path]:
    yield from sorted(Path(directory).glob("*.json"))


def iter_text_files(directory: str | Path) -> Iterable[Path]:
    yield from sorted(Path(directory).glob("chapter_*.txt"))


def clear_matching(directory: str | Path, pattern: str) -> None:
    base = Path(directory)
    if not base.exists():
        return
    for path in base.glob(pattern):
        if path.is_file():
            path.unlink()


def copy_tree_files(input_dir: str | Path, output_dir: str | Path, pattern: str = "*.txt") -> int:
    src = Path(input_dir)
    dst = ensure_dir(output_dir)
    count = 0
    for path in sorted(src.glob(pattern)):
        if not path.is_file():
            continue
        shutil.copy2(path, dst / path.name)
        count += 1
    return count


