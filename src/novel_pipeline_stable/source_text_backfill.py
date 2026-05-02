from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import ensure_dir, read_json, read_jsonl, write_json, write_jsonl, write_text
from novel_pipeline_stable.source_text_cleanup import strip_source_site_noise


@dataclass(slots=True)
class CleanupStats:
    changed: bool = False
    changed_string_fields: int = 0
    removed_line_count: int = 0
    removed_fragment_count: int = 0
    changed_char_delta: int = 0

    def merge(self, other: "CleanupStats") -> None:
        self.changed = self.changed or other.changed
        self.changed_string_fields += int(other.changed_string_fields)
        self.removed_line_count += int(other.removed_line_count)
        self.removed_fragment_count += int(other.removed_fragment_count)
        self.changed_char_delta += int(other.changed_char_delta)

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "changed": self.changed,
            "changed_string_fields": self.changed_string_fields,
            "removed_line_count": self.removed_line_count,
            "removed_fragment_count": self.removed_fragment_count,
            "changed_char_delta": self.changed_char_delta,
        }


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_value(value: Any) -> tuple[Any, CleanupStats]:
    if isinstance(value, str):
        cleaned, noise_stats = strip_source_site_noise(value)
        changed = cleaned != value
        return cleaned, CleanupStats(
            changed=changed,
            changed_string_fields=1 if changed else 0,
            removed_line_count=noise_stats.removed_line_count,
            removed_fragment_count=noise_stats.removed_fragment_count,
            changed_char_delta=len(cleaned) - len(value) if changed else 0,
        )
    if isinstance(value, list):
        items: list[Any] = []
        totals = CleanupStats()
        for item in value:
            cleaned_item, item_stats = _clean_value(item)
            items.append(cleaned_item)
            totals.merge(item_stats)
        return items, totals
    if isinstance(value, dict):
        payload: dict[str, Any] = {}
        totals = CleanupStats()
        for key, item in value.items():
            cleaned_item, item_stats = _clean_value(item)
            payload[key] = cleaned_item
            totals.merge(item_stats)
        return payload, totals
    return value, CleanupStats()


def _rewrite_chapter_files(chapters_dir: Path) -> dict[str, Any]:
    files = sorted(chapters_dir.glob("chapter_*.txt"))
    changed_files: list[dict[str, Any]] = []
    removed_line_count = 0
    removed_fragment_count = 0
    total_delta = 0

    for path in files:
        original = path.read_text(encoding="utf-8-sig")
        cleaned, stats = strip_source_site_noise(original)
        if cleaned == original:
            continue
        write_text(path, cleaned)
        changed_files.append(
            {
                "file": path.name,
                "removed_line_count": stats.removed_line_count,
                "removed_fragment_count": stats.removed_fragment_count,
                "char_delta": len(cleaned) - len(original),
            }
        )
        removed_line_count += int(stats.removed_line_count)
        removed_fragment_count += int(stats.removed_fragment_count)
        total_delta += len(cleaned) - len(original)

    return {
        "scanned_file_count": len(files),
        "changed_file_count": len(changed_files),
        "removed_line_count": removed_line_count,
        "removed_fragment_count": removed_fragment_count,
        "changed_char_delta": total_delta,
        "changed_files": changed_files,
    }


def _rewrite_scene_files(scenes_dir: Path) -> dict[str, Any]:
    files = sorted(scenes_dir.glob("scene_*.json"))
    changed_files: list[dict[str, Any]] = []
    removed_line_count = 0
    removed_fragment_count = 0
    total_delta = 0

    for path in files:
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        original_text = str(payload.get("text", ""))
        cleaned_text, stats = strip_source_site_noise(original_text)
        if cleaned_text == original_text:
            continue
        payload["text"] = cleaned_text
        payload["char_count"] = len(cleaned_text)
        write_json(path, payload)
        changed_files.append(
            {
                "file": path.name,
                "scene_id": payload.get("scene_id", ""),
                "removed_line_count": stats.removed_line_count,
                "removed_fragment_count": stats.removed_fragment_count,
                "char_delta": len(cleaned_text) - len(original_text),
            }
        )
        removed_line_count += int(stats.removed_line_count)
        removed_fragment_count += int(stats.removed_fragment_count)
        total_delta += len(cleaned_text) - len(original_text)

    return {
        "scanned_file_count": len(files),
        "changed_file_count": len(changed_files),
        "removed_line_count": removed_line_count,
        "removed_fragment_count": removed_fragment_count,
        "changed_char_delta": total_delta,
        "changed_files": changed_files,
    }


def _rewrite_canon_file(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".jsonl":
        rows = read_jsonl(path)
        cleaned_rows: list[dict[str, Any]] = []
        totals = CleanupStats()
        for row in rows:
            cleaned_row, row_stats = _clean_value(row)
            if isinstance(cleaned_row, dict):
                cleaned_rows.append(cleaned_row)
            else:
                cleaned_rows.append(row)
            totals.merge(row_stats)
        if totals.changed:
            write_jsonl(path, cleaned_rows)
        return {
            "file": path.name,
            "format": "jsonl",
            **totals.as_dict(),
        }

    payload = read_json(path)
    cleaned_payload, totals = _clean_value(payload)
    if totals.changed:
        write_json(path, cleaned_payload)
    return {
        "file": path.name,
        "format": "json",
        **totals.as_dict(),
    }


def _rewrite_canon_dirs(data_root: Path) -> dict[str, Any]:
    canon_dirs = sorted(path for path in data_root.glob("canon_*") if path.is_dir())
    file_reports: list[dict[str, Any]] = []
    totals = CleanupStats()

    for canon_dir in canon_dirs:
        for path in sorted(canon_dir.glob("*")):
            if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
                continue
            report = _rewrite_canon_file(path)
            report["directory"] = canon_dir.name
            file_reports.append(report)
            totals.merge(
                CleanupStats(
                    changed=bool(report["changed"]),
                    changed_string_fields=int(report["changed_string_fields"]),
                    removed_line_count=int(report["removed_line_count"]),
                    removed_fragment_count=int(report["removed_fragment_count"]),
                    changed_char_delta=int(report["changed_char_delta"]),
                )
            )

    return {
        "scanned_directory_count": len(canon_dirs),
        "scanned_file_count": len(file_reports),
        "changed_file_count": len([report for report in file_reports if report["changed"]]),
        "changed_string_fields": totals.changed_string_fields,
        "removed_line_count": totals.removed_line_count,
        "removed_fragment_count": totals.removed_fragment_count,
        "changed_char_delta": totals.changed_char_delta,
        "file_reports": file_reports,
    }


def run_source_text_backfill(
    *,
    data_root: str | Path,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(data_root)
    chapters_report = _rewrite_chapter_files(root / "chapters")
    scenes_report = _rewrite_scene_files(root / "scenes")
    canon_report = _rewrite_canon_dirs(root)
    report = {
        "generated_at": _timestamp(),
        "data_root": str(root.resolve()),
        "chapters": chapters_report,
        "scenes": scenes_report,
        "canon": canon_report,
        "totals": {
            "changed_file_count": int(chapters_report["changed_file_count"])
            + int(scenes_report["changed_file_count"])
            + int(canon_report["changed_file_count"]),
            "removed_line_count": int(chapters_report["removed_line_count"])
            + int(scenes_report["removed_line_count"])
            + int(canon_report["removed_line_count"]),
            "removed_fragment_count": int(chapters_report["removed_fragment_count"])
            + int(scenes_report["removed_fragment_count"])
            + int(canon_report["removed_fragment_count"]),
            "changed_char_delta": int(chapters_report["changed_char_delta"])
            + int(scenes_report["changed_char_delta"])
            + int(canon_report["changed_char_delta"]),
        },
    }
    if report_path is not None:
        target = Path(report_path)
        ensure_dir(target.parent)
        write_json(target, report)
    return report
