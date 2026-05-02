from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_MANIFEST_FILE = "run_manifest.json"
RUN_MANIFEST_VERSION = "style-bible-run-manifest-v1"
EVALUATION_MANIFEST_FILE = "evaluation_manifest.json"
EVALUATION_MANIFEST_VERSION = "style-bible-evaluation-manifest-v1"
STYLE_BIBLE_SCHEMA_VERSION = "style-bible-result-v2"
STYLE_GOLD_SET_VERSION = "style-gold-set-v1"
STYLE_GOLD_SET_CASE_VERSION = "style-gold-set-case-v1"
STYLE_BIBLE_PROMPT_NAME = "style_bible_local_reduce.md"


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def slugify_identifier(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", clean_text(value).lower()).strip("_")
    return slug or "unknown"


def canonical_json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_payload(payload: Any) -> str:
    return sha256_text(canonical_json_text(payload))


def file_sha256(path: str | Path) -> str:
    target = Path(path).resolve()
    if not target.exists():
        return ""
    return sha256_bytes(target.read_bytes())


def short_hash(value: str, *, length: int = 10) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        return ""
    return cleaned[:length]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_iso_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_timestamp_token(value: str) -> str:
    digits = re.sub(r"[^0-9]", "", clean_text(value))
    return digits[:14] or "unknown"


def try_get_git_commit(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return clean_text(result.stdout)


def build_style_id(output_dir: str | Path, *, story_node_scope: dict[str, Any] | None = None) -> str:
    scope = story_node_scope or {}
    node_id = clean_text(scope.get("node_id"))
    if node_id:
        base = node_id
    else:
        start_chapter = clean_text(scope.get("start_chapter"))
        end_chapter = clean_text(scope.get("end_chapter"))
        if start_chapter and end_chapter:
            base = f"ch{start_chapter}_{end_chapter}"
        else:
            base = Path(output_dir).resolve().name
    return f"style_bible_{slugify_identifier(base)}_v1"


def build_run_id(style_id: str, model_name: str, built_at: str, source_bundle_hash: str) -> str:
    return (
        f"{slugify_identifier(style_id)}__"
        f"{slugify_identifier(model_name)}__"
        f"{short_hash(source_bundle_hash, length=8) or 'nohash'}__"
        f"{normalize_timestamp_token(built_at)}"
    )


def build_evaluation_id(run_id: str, rules_hash: str) -> str:
    return f"{slugify_identifier(run_id or 'style_eval')}__eval__{short_hash(rules_hash, length=8) or 'norules'}"


def prompt_info(prompt_dir: str | Path, prompt_name: str = STYLE_BIBLE_PROMPT_NAME) -> dict[str, str]:
    path = (Path(prompt_dir) / prompt_name).resolve()
    return {
        "name": prompt_name,
        "path": str(path),
        "sha256": file_sha256(path),
    }


def file_info(path: str | Path) -> dict[str, str]:
    target = Path(path).resolve()
    return {
        "path": str(target),
        "sha256": file_sha256(target),
    }


def build_style_bible_run_manifest(
    *,
    project_root: str | Path,
    config_path: str | Path,
    prompt_dir: str | Path,
    facts_dir: str | Path,
    style_dir: str | Path,
    canon_dir: str | Path,
    output_dir: str | Path,
    source_bundle_path: str | Path,
    style_bible_path: str | Path,
    style_bible_payload: dict[str, Any],
    source_bundle: dict[str, Any],
    model_name: str,
    built_at: str,
    request_metrics: dict[str, Any] | None = None,
    usage_metadata: dict[str, Any] | None = None,
    story_node_scope: dict[str, Any] | None = None,
    sampling_mode: str = "",
    routing_mode: str = "",
    batching_mode: str = "",
    sampling_report_path: str | Path | None = None,
    routed_index_path: str | Path | None = None,
    batch_plan_path: str | Path | None = None,
    prompt_name: str = STYLE_BIBLE_PROMPT_NAME,
    extra_output_files: dict[str, str | Path] | None = None,
    extra_hashes: dict[str, str] | None = None,
    backfilled_from_existing_output: bool = False,
) -> dict[str, Any]:
    scope = story_node_scope or {}
    style_id = clean_text(style_bible_payload.get("style_id")) or build_style_id(output_dir, story_node_scope=scope)
    scope_text = clean_text(style_bible_payload.get("scope")) or clean_text(source_bundle.get("scope_hint"))
    source_bundle_hash = sha256_payload(source_bundle)
    style_bible_hash = sha256_payload(style_bible_payload)
    prompt_meta = prompt_info(prompt_dir, prompt_name)
    config_meta = file_info(config_path)
    run_id = build_run_id(style_id, model_name, built_at, source_bundle_hash)
    corpus_stats = source_bundle.get("corpus_stats", {})
    sampling = source_bundle.get("sampling", {})
    output_files = {
        "source_bundle_file": str(Path(source_bundle_path).resolve()),
        "style_bible_file": str(Path(style_bible_path).resolve()),
    }
    if sampling_report_path:
        output_files["sampling_report_file"] = str(Path(sampling_report_path).resolve())
    if routed_index_path:
        output_files["routed_index_file"] = str(Path(routed_index_path).resolve())
    if batch_plan_path:
        output_files["batch_plan_file"] = str(Path(batch_plan_path).resolve())
    if extra_output_files:
        for key, value in extra_output_files.items():
            cleaned_key = clean_text(key)
            if not cleaned_key or value is None:
                continue
            output_files[cleaned_key] = str(Path(value).resolve())

    hashes = {
        "source_bundle_sha256": source_bundle_hash,
        "style_bible_sha256": style_bible_hash,
    }
    if sampling_report_path:
        hashes["sampling_report_sha256"] = file_sha256(sampling_report_path)
    if routed_index_path:
        hashes["routed_index_sha256"] = file_sha256(routed_index_path)
    if batch_plan_path:
        hashes["batch_plan_sha256"] = file_sha256(batch_plan_path)
    if extra_hashes:
        for key, value in extra_hashes.items():
            cleaned_key = clean_text(key)
            cleaned_value = clean_text(value)
            if cleaned_key and cleaned_value:
                hashes[cleaned_key] = cleaned_value

    return {
        "manifest_version": RUN_MANIFEST_VERSION,
        "stage": "build-style-bible",
        "run_id": run_id,
        "style_id": style_id,
        "style_bible_schema_version": STYLE_BIBLE_SCHEMA_VERSION,
        "scope": scope_text,
        "scope_type": clean_text(scope.get("scope_type")) or ("story_node" if clean_text(scope.get("node_id")) else "corpus"),
        "node_id": clean_text(scope.get("node_id")),
        "start_chapter": clean_text(scope.get("start_chapter")),
        "end_chapter": clean_text(scope.get("end_chapter")),
        "model_name": clean_text(model_name),
        "prompt_name": prompt_meta["name"],
        "prompt_path": prompt_meta["path"],
        "prompt_hash": prompt_meta["sha256"],
        "config_path": config_meta["path"],
        "config_hash": config_meta["sha256"],
        "built_at": clean_text(built_at) or utc_now_iso(),
        "git_commit": try_get_git_commit(project_root),
        "project_root": str(Path(project_root).resolve()),
        "input_dirs": {
            "facts_dir": str(Path(facts_dir).resolve()),
            "style_dir": str(Path(style_dir).resolve()),
            "canon_dir": str(Path(canon_dir).resolve()),
        },
        "output_dir": str(Path(output_dir).resolve()),
        "output_files": output_files,
        "hashes": hashes,
        "corpus_stats": corpus_stats if isinstance(corpus_stats, dict) else {},
        "sampling": sampling if isinstance(sampling, dict) else {},
        "sampling_mode": clean_text(sampling_mode),
        "routing_mode": clean_text(routing_mode),
        "batching_mode": clean_text(batching_mode),
        "request_metrics": request_metrics or {},
        "usage_metadata": usage_metadata or {},
        "story_node_scope": scope,
        "provenance": {
            "backfilled_from_existing_output": backfilled_from_existing_output,
        },
    }


def build_style_bible_evaluation_manifest(
    *,
    project_root: str | Path,
    rules_config: str | Path,
    input_dir: str | Path,
    output_dir: str | Path,
    report_path: str | Path,
    markdown_path: str | Path,
    report: dict[str, Any],
    build_run_manifest: dict[str, Any] | None = None,
    build_run_manifest_path: str | Path | None = None,
    backfilled_from_existing_report: bool = False,
) -> dict[str, Any]:
    build_manifest = build_run_manifest or {}
    summary = report.get("summary", {})
    rules_meta = file_info(rules_config)
    report_hash = sha256_payload(report)
    run_id = clean_text(build_manifest.get("run_id"))
    style_id = clean_text(report.get("style_id")) or clean_text(build_manifest.get("style_id"))
    evaluation_id = build_evaluation_id(run_id or style_id or "style_eval", rules_meta["sha256"])

    return {
        "manifest_version": EVALUATION_MANIFEST_VERSION,
        "stage": "evaluate-style-bible",
        "evaluation_id": evaluation_id,
        "run_id": run_id,
        "style_id": style_id,
        "style_bible_schema_version": clean_text(build_manifest.get("style_bible_schema_version"))
        or STYLE_BIBLE_SCHEMA_VERSION,
        "scope": clean_text(report.get("scope")) or clean_text(build_manifest.get("scope")),
        "scope_type": clean_text(build_manifest.get("scope_type")),
        "node_id": clean_text(build_manifest.get("node_id")),
        "model_name": clean_text(build_manifest.get("model_name")),
        "prompt_hash": clean_text(build_manifest.get("prompt_hash")),
        "config_hash": clean_text(build_manifest.get("config_hash")),
        "rules_path": rules_meta["path"],
        "rules_hash": rules_meta["sha256"],
        "evaluated_at": clean_text(report.get("generated_at")) or utc_now_iso(),
        "git_commit": try_get_git_commit(project_root),
        "project_root": str(Path(project_root).resolve()),
        "input_dir": str(Path(input_dir).resolve()),
        "output_dir": str(Path(output_dir).resolve()),
        "output_files": {
            "report_file": str(Path(report_path).resolve()),
            "markdown_file": str(Path(markdown_path).resolve()),
        },
        "status": clean_text(summary.get("status")),
        "overall_score": summary.get("overall_score", 0),
        "max_score": summary.get("max_score", 0),
        "pass_score": summary.get("pass_score", 0),
        "warn_score": summary.get("warn_score", 0),
        "quality_gate_passed": bool(summary.get("quality_gate_passed")),
        "check_counts": summary.get("check_counts", {}),
        "report_hash": report_hash,
        "source_run_manifest_file": str(Path(build_run_manifest_path).resolve()) if build_run_manifest_path else "",
        "provenance": {
            "backfilled_from_existing_report": backfilled_from_existing_report,
        },
    }
