from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import ensure_dir, read_json


RUN_STATUS_FILE = "run_status.json"
RUN_LOG_FILE = "run_log.jsonl"
MANIFEST_FILE = "manifest.json"
FAILURES_FILE = "failures.json"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    last_error: PermissionError | None = None
    for attempt in range(6):
        try:
            temp_path.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt == 5:
                raise
            time.sleep(0.2 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = read_json(path)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def _is_partial_selection(payload: dict[str, Any]) -> bool:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return False
    start_at = _safe_int(metadata.get("start_at"), 0)
    limit = metadata.get("limit")
    return start_at > 0 or limit not in {None, "", 0}


def _matches_output_file(stage: str, file_name: str) -> bool:
    if stage == "stable-extract-facts":
        return file_name.startswith("scene_") and file_name.endswith(".json")
    if stage == "stable-extract-style":
        return file_name.startswith("style_window_") and file_name.endswith(".json")
    return False


def _count_output_files(run_dir: Path, *, stage: str, manifest_rows: list[dict[str, Any]]) -> int:
    manifest_outputs = {
        output_name
        for row in manifest_rows
        for output_name in [str(row.get("output_file", "")).strip()]
        if output_name and (run_dir / output_name).exists()
    }
    if manifest_outputs:
        return len(manifest_outputs)
    if not run_dir.exists():
        return 0
    return sum(
        1
        for path in run_dir.iterdir()
        if path.is_file() and _matches_output_file(stage, path.name)
    )


def _enrich_run_payload(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload

    enriched = dict(payload)
    enriched["session_processed_items"] = _safe_int(payload.get("processed_items"), 0)
    enriched["session_success_count"] = _safe_int(payload.get("success_count"), 0)
    enriched["session_failure_count"] = _safe_int(payload.get("failure_count"), 0)
    enriched["session_skipped_count"] = _safe_int(payload.get("skipped_count"), 0)
    enriched["counter_source"] = "run_status"

    if _is_partial_selection(payload):
        return enriched

    manifest_rows = _read_json_list(run_dir / MANIFEST_FILE)
    failure_rows = _read_json_list(run_dir / FAILURES_FILE)
    output_files_count = _count_output_files(
        run_dir,
        stage=str(payload.get("stage", "")),
        manifest_rows=manifest_rows,
    )

    manifest_count = len(manifest_rows)
    failure_count = len(failure_rows)
    if manifest_count == 0 and failure_count == 0 and output_files_count == 0:
        return enriched

    success_count = max(manifest_count, output_files_count)
    total_items = _safe_int(payload.get("total_items"), 0)
    processed_items = success_count + failure_count
    pending_items = max(total_items - processed_items, 0) if total_items > 0 else 0
    skipped_count = max(success_count - enriched["session_success_count"], 0)

    enriched["counter_source"] = "artifacts"
    enriched["manifest_count"] = manifest_count
    enriched["output_files_count"] = output_files_count
    enriched["success_count"] = success_count
    enriched["failure_count"] = failure_count
    enriched["processed_items"] = processed_items
    enriched["pending_items"] = pending_items
    enriched["skipped_count"] = skipped_count
    if total_items > 0:
        enriched["progress_ratio"] = round(min(processed_items / total_items, 1.0), 6)
    elif str(enriched.get("status", "")) == "completed":
        enriched["progress_ratio"] = 1.0
    else:
        enriched["progress_ratio"] = 0.0

    return enriched


class RunTracker:
    def __init__(
        self,
        *,
        stage: str,
        output_dir: str | Path,
        total_items: int,
        item_label: str = "item",
        source_dir: str | Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.output_dir = ensure_dir(output_dir).resolve()
        self.status_path = self.output_dir / RUN_STATUS_FILE
        self.log_path = self.output_dir / RUN_LOG_FILE
        now = utc_timestamp()
        self.state: dict[str, Any] = {
            "run_id": f"{stage}:{self.output_dir}",
            "stage": stage,
            "status": "running",
            "item_label": item_label,
            "total_items": max(int(total_items), 0),
            "processed_items": 0,
            "success_count": 0,
            "failure_count": 0,
            "skipped_count": 0,
            "pending_items": max(int(total_items), 0),
            "progress_ratio": 0.0,
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "current_item": "",
            "last_message": "",
            "output_dir": str(self.output_dir),
            "source_dir": str(Path(source_dir).resolve()) if source_dir else "",
            "metadata": metadata or {},
            "pid": os.getpid(),
            "host": socket.gethostname(),
        }
        self._write_status()
        self.log("Run started.", event="started")

    def _refresh_progress(self) -> None:
        total = int(self.state.get("total_items", 0))
        processed = int(self.state.get("processed_items", 0))
        self.state["pending_items"] = max(total - processed, 0) if total else 0
        if total > 0:
            self.state["progress_ratio"] = round(min(processed / total, 1.0), 6)
        elif self.state.get("status") == "completed":
            self.state["progress_ratio"] = 1.0
        else:
            self.state["progress_ratio"] = 0.0
        self.state["updated_at"] = utc_timestamp()

    def _write_status(self) -> None:
        self._refresh_progress()
        _write_json_atomic(self.status_path, self.state)

    def log(self, message: str, *, level: str = "info", event: str = "log", **fields: Any) -> None:
        timestamp = utc_timestamp()
        entry = {
            "timestamp": timestamp,
            "level": level,
            "event": event,
            "message": message,
            **fields,
        }
        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False))
            handle.write("\n")
        self.state["last_message"] = message
        if fields.get("item"):
            self.state["current_item"] = str(fields["item"])
        self.state["updated_at"] = timestamp
        self._write_status()

    def _record_outcome(self, *, outcome: str, item: str, message: str, level: str = "info", **fields: Any) -> None:
        self.state["processed_items"] = int(self.state.get("processed_items", 0)) + 1
        if outcome == "success":
            self.state["success_count"] = int(self.state.get("success_count", 0)) + 1
        elif outcome == "failure":
            self.state["failure_count"] = int(self.state.get("failure_count", 0)) + 1
        elif outcome == "skipped":
            self.state["skipped_count"] = int(self.state.get("skipped_count", 0)) + 1
        self.state["current_item"] = item
        self.log(message, level=level, event=outcome, item=item, **fields)

    def record_success(self, item: str, message: str, **fields: Any) -> None:
        self._record_outcome(outcome="success", item=item, message=message, level="info", **fields)

    def record_failure(self, item: str, message: str, **fields: Any) -> None:
        self._record_outcome(outcome="failure", item=item, message=message, level="error", **fields)

    def record_skip(self, item: str, message: str, **fields: Any) -> None:
        self._record_outcome(outcome="skipped", item=item, message=message, level="warning", **fields)

    def finish(self, message: str = "Run completed.", *, status: str = "completed", **fields: Any) -> None:
        self.state.update(fields)
        self.state["status"] = status
        self.state["finished_at"] = utc_timestamp()
        self.log(message, level="info", event="completed", **fields)
        self.state["progress_ratio"] = 1.0 if int(self.state.get("total_items", 0)) == 0 else self.state.get("progress_ratio", 0.0)
        self._write_status()

    def fail_run(self, message: str, *, error_type: str = "RuntimeError", **fields: Any) -> None:
        self.state["status"] = "failed"
        self.state["finished_at"] = utc_timestamp()
        self.state["metadata"] = {
            **dict(self.state.get("metadata", {})),
            "error_type": error_type,
            "error_message": message,
        }
        self.log(message, level="error", event="run_failed", error_type=error_type, **fields)
        self._write_status()


def discover_runs(data_root: str | Path) -> list[dict[str, Any]]:
    root = Path(data_root).resolve()
    if not root.exists():
        return []

    runs: list[dict[str, Any]] = []
    for status_path in root.rglob(RUN_STATUS_FILE):
        try:
            payload = read_json(status_path)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        payload = _enrich_run_payload(status_path.parent.resolve(), payload)
        try:
            relative_dir = status_path.parent.resolve().relative_to(root)
            relative_text = relative_dir.as_posix()
        except Exception:
            relative_text = status_path.parent.name
        payload["output_dir_relative"] = relative_text
        payload["log_available"] = (status_path.parent / RUN_LOG_FILE).exists()
        payload["failures_available"] = (status_path.parent / FAILURES_FILE).exists()
        runs.append(payload)

    runs.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
    return runs


def resolve_run_dir(data_root: str | Path, relative_output_dir: str) -> Path:
    root = Path(data_root).resolve()
    run_dir = (root / Path(relative_output_dir)).resolve()
    if not run_dir.is_relative_to(root):
        raise ValueError(f"Run path escapes data root: {relative_output_dir}")
    return run_dir


def read_log_tail(run_dir: str | Path, limit: int = 200) -> list[dict[str, Any]]:
    path = Path(run_dir) / RUN_LOG_FILE
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    result: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"timestamp": "", "level": "info", "event": "raw", "message": line}
        if isinstance(payload, dict):
            result.append(payload)
    return result


def read_failures_preview(run_dir: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    path = Path(run_dir) / FAILURES_FILE
    if not path.exists():
        return []
    try:
        payload = read_json(path)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    preview = []
    for row in payload[:limit]:
        if isinstance(row, dict):
            preview.append(row)
    return preview


def read_run_detail(data_root: str | Path, relative_output_dir: str, *, log_limit: int = 200, failure_limit: int = 20) -> dict[str, Any]:
    run_dir = resolve_run_dir(data_root, relative_output_dir)
    status_path = run_dir / RUN_STATUS_FILE
    if not status_path.exists():
        raise FileNotFoundError(f"Run status not found: {relative_output_dir}")
    status_payload = read_json(status_path)
    if not isinstance(status_payload, dict):
        raise ValueError(f"Invalid run status payload: {relative_output_dir}")
    status_payload = _enrich_run_payload(run_dir, status_payload)
    status_payload["output_dir_relative"] = relative_output_dir.replace('\\', '/')
    return {
        "run": status_payload,
        "logs": read_log_tail(run_dir, limit=log_limit),
        "failures": read_failures_preview(run_dir, limit=failure_limit),
    }
