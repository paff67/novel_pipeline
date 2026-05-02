from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from novel_pipeline_stable.io_utils import ensure_dir, read_json, write_json, write_markdown
from novel_pipeline_stable.models import StyleBibleResultV2
from novel_pipeline_stable.monitoring import RunTracker, utc_timestamp
from novel_pipeline_stable.style_bible_contracts import COVERAGE_REPORT_FILE, REDUCE_TRACE_FILE, SAMPLING_REPORT_FILE
from novel_pipeline_stable.style_bible_ragas_eval import build_style_bible_semantic_report
from novel_pipeline_stable.style_eval_contract import (
    EVALUATION_MANIFEST_FILE,
    RUN_MANIFEST_FILE,
    STYLE_BIBLE_SCHEMA_VERSION,
    build_style_bible_evaluation_manifest,
    sha256_payload,
)


STYLE_BIBLE_FILE = "style_bible_final.json"
SOURCE_BUNDLE_FILE = "style_bible_source_bundle.json"
REPORT_JSON_FILE = "style_eval_report.json"
REPORT_MD_FILE = "style_eval_report.md"


@dataclass(slots=True)
class StyleBibleEvalRules:
    rules_path: Path
    pass_score: float
    warn_score: float
    weights: dict[str, float]
    thresholds: dict[str, float]
    required_scalar_fields: list[str]
    minimums: dict[str, int]
    core_axis_ids: list[str]
    core_bucket_ids: list[str]


@dataclass(slots=True)
class StyleBibleEvaluationResult:
    report_path: Path
    markdown_path: Path
    report: dict[str, Any]
    evaluation_manifest_path: Path | None = None
    evaluation_manifest: dict[str, Any] | None = None


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _load_build_run_manifest(source_dir: Path) -> tuple[Path | None, dict[str, Any] | None]:
    path = source_dir / RUN_MANIFEST_FILE
    if not path.exists():
        return None, None
    payload = read_json(path)
    return path, payload if isinstance(payload, dict) else None


def _load_rules(rules_path: str | Path) -> StyleBibleEvalRules:
    path = Path(rules_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Rules config not found: {path}")

    payload = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    scoring = payload.get("scoring", {})
    thresholds = payload.get("thresholds", {})
    coverage_targets = payload.get("coverage_targets", {})
    required_scalars = payload.get("required_scalars", {})
    minimums = payload.get("minimums", {})
    return StyleBibleEvalRules(
        rules_path=path,
        pass_score=float(scoring.get("pass_score", 0.72) or 0.72),
        warn_score=float(scoring.get("warn_score", 0.55) or 0.55),
        weights={str(key): float(value or 0.0) for key, value in (payload.get("weights", {}) or {}).items()},
        thresholds={str(key): float(value or 0.0) for key, value in thresholds.items()},
        required_scalar_fields=[_clean_text(item) for item in required_scalars.get("fields", []) if _clean_text(item)],
        minimums={str(key): int(value or 0) for key, value in minimums.items() if _clean_text(key)},
        core_axis_ids=[_clean_text(item) for item in coverage_targets.get("core_axis_ids", []) if _clean_text(item)],
        core_bucket_ids=[_clean_text(item) for item in coverage_targets.get("core_bucket_ids", []) if _clean_text(item)],
    )


def _get_nested(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _count_value_items(value: Any) -> int:
    if isinstance(value, list):
        return len([item for item in value if item not in (None, "", [], {})])
    if isinstance(value, str):
        return 1 if _clean_text(value) else 0
    if isinstance(value, dict):
        return 1 if value else 0
    return 0


def _status_for_ratio(ratio: float, *, pass_ratio: float, warn_ratio: float) -> str:
    if ratio >= pass_ratio:
        return "pass"
    if ratio >= warn_ratio:
        return "warn"
    return "fail"


def _evaluate_section_completeness(style_bible_payload: dict[str, Any], rules: StyleBibleEvalRules) -> dict[str, Any]:
    scalar_hits = 0
    missing_scalars: list[str] = []
    for path in rules.required_scalar_fields:
        value = _get_nested(style_bible_payload, path)
        if _count_value_items(value) > 0:
            scalar_hits += 1
        else:
            missing_scalars.append(path)

    minimum_hits = 0
    underfilled_paths: list[dict[str, Any]] = []
    for path, minimum in rules.minimums.items():
        actual_count = _count_value_items(_get_nested(style_bible_payload, path))
        if actual_count >= minimum:
            minimum_hits += 1
            continue
        underfilled_paths.append(
            {
                "path": path,
                "actual_count": actual_count,
                "minimum": int(minimum),
                "deficit": max(int(minimum) - int(actual_count), 0),
            }
        )

    total_targets = len(rules.required_scalar_fields) + len(rules.minimums)
    completeness_ratio = round((scalar_hits + minimum_hits) / max(total_targets, 1), 4)
    status = _status_for_ratio(
        completeness_ratio,
        pass_ratio=float(rules.thresholds.get("section_pass_ratio", 0.9) or 0.9),
        warn_ratio=float(rules.thresholds.get("section_warn_ratio", 0.65) or 0.65),
    )
    return {
        "check_id": "section_completeness",
        "category": "coverage",
        "status": status,
        "score": completeness_ratio,
        "max_score": 1.0,
        "message": "Measured required scalars plus minimum rule counts against the configured section profile.",
        "details": {
            "required_scalar_hit_count": scalar_hits,
            "required_scalar_total": len(rules.required_scalar_fields),
            "minimum_path_hit_count": minimum_hits,
            "minimum_path_total": len(rules.minimums),
            "completeness_ratio": completeness_ratio,
            "missing_scalars": missing_scalars,
            "underfilled_paths": underfilled_paths,
        },
        "recommendation": (
            "Backfill missing scalar tokens and underfilled rule families before relying on the semantic gate."
            if status != "pass"
            else ""
        ),
    }


def _evaluate_schema_validity(style_bible_payload: dict[str, Any]) -> tuple[dict[str, Any], StyleBibleResultV2 | None]:
    try:
        parsed = StyleBibleResultV2.model_validate(style_bible_payload)
    except ValidationError as exc:
        return (
            {
                "check_id": "schema_validity",
                "category": "schema",
                "status": "fail",
                "score": 0.0,
                "max_score": 1.0,
                "message": "Style bible payload failed strict StyleBibleResultV2 validation.",
                "details": {
                    "error_count": len(exc.errors()),
                    "errors": exc.errors(),
                },
                "recommendation": "Fix the strict schema violations before running semantic evaluation.",
            },
            None,
        )

    return (
        {
            "check_id": "schema_validity",
            "category": "schema",
            "status": "pass",
            "score": 1.0,
            "max_score": 1.0,
            "message": "Style bible payload passed strict StyleBibleResultV2 validation.",
            "details": {},
            "recommendation": "",
        },
        parsed,
    )


def _build_markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    checks = report.get("checks", [])
    semantic_summary = (report.get("semantic_evaluation", {}) or {}).get("summary", {})
    lines = [
        "# Style Bible Evaluation Report",
        "",
        f"- generated_at: {report.get('generated_at', '')}",
        f"- evaluation_version: {report.get('evaluation_version', '')}",
        f"- style_id: {report.get('style_id', '')}",
        f"- scope: {report.get('scope', '')}",
        f"- semantic_judge_model: {report.get('semantic_judge_model', '')}",
        f"- overall_status: {summary.get('status', '')}",
        f"- overall_score: {summary.get('overall_score', 0.0)}",
        f"- semantic_average_specificity: {semantic_summary.get('average_specificity', 0.0)}",
        f"- semantic_average_actionability: {semantic_summary.get('average_actionability', 0.0)}",
        f"- semantic_average_grounding: {semantic_summary.get('average_grounding', 0.0)}",
        "",
        "## Checks",
        "",
    ]
    for check in checks:
        lines.append(
            f"- `{check.get('check_id', '')}`: {check.get('status', '')} "
            f"(score={check.get('score', 0.0)}/{check.get('max_score', 0.0)})"
        )
    weak_rules = semantic_summary.get("weak_rules", [])
    lines.extend(["", "## Weak Rules", ""])
    if not weak_rules:
        lines.append("- none")
    else:
        for row in weak_rules:
            lines.append(
                f"- `{row.get('path', '')}` / `{row.get('rule_id', '')}` / "
                f"overall={row.get('overall_score', 0.0)} / text={row.get('text', '')}"
            )
    return "\n".join(lines) + "\n"


def evaluate_style_bible(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    rules_config: str | Path,
    semantic_judge_model: str = "",
) -> StyleBibleEvaluationResult:
    source_dir = Path(input_dir).resolve()
    output_path = ensure_dir(output_dir)
    style_bible_path = source_dir / STYLE_BIBLE_FILE
    source_bundle_path = source_dir / SOURCE_BUNDLE_FILE
    reduce_trace_path = source_dir / REDUCE_TRACE_FILE
    if not style_bible_path.exists():
        raise FileNotFoundError(f"Missing style bible file: {style_bible_path}")
    if not source_bundle_path.exists():
        raise FileNotFoundError(f"Missing source bundle file: {source_bundle_path}")

    style_bible_payload = read_json(style_bible_path)
    source_bundle = read_json(source_bundle_path)
    reduce_trace_payload = read_json(reduce_trace_path) if reduce_trace_path.exists() else {}
    if not isinstance(style_bible_payload, dict):
        raise ValueError(f"Style bible payload must be a JSON object: {style_bible_path}")
    if not isinstance(source_bundle, dict):
        raise ValueError(f"Source bundle payload must be a JSON object: {source_bundle_path}")
    if reduce_trace_payload and not isinstance(reduce_trace_payload, dict):
        raise ValueError(f"Reduce trace payload must be a JSON object: {reduce_trace_path}")

    rules = _load_rules(rules_config)
    schema_check, parsed = _evaluate_schema_validity(style_bible_payload)
    section_check = (
        _evaluate_section_completeness(style_bible_payload, rules)
        if parsed is not None
        else {
            "check_id": "section_completeness",
            "category": "coverage",
            "status": "fail",
            "score": 0.0,
            "max_score": 1.0,
            "message": "Skipped because schema validation failed.",
            "details": {},
            "recommendation": "",
        }
    )
    semantic_artifacts = (
        build_style_bible_semantic_report(
            style_bible_payload,
            source_bundle,
            reduce_trace_payload if isinstance(reduce_trace_payload, dict) else {},
            weights={key: value for key, value in rules.weights.items() if key in {"specificity", "actionability", "grounding"}},
            thresholds={key: value for key, value in rules.thresholds.items() if key.startswith("row_")},
            semantic_judge_model=semantic_judge_model,
        )
        if parsed is not None
        else None
    )
    semantic_summary = semantic_artifacts.report.get("summary", {}) if semantic_artifacts is not None else {}
    semantic_check = {
        "check_id": "semantic_rule_quality",
        "category": "semantic",
        "status": semantic_summary.get("status", "fail"),
        "score": float(semantic_summary.get("average_overall_score", 0.0) or 0.0),
        "max_score": 1.0,
        "message": "Primary gate driven by per-rule semantic specificity, actionability, and grounding.",
        "details": {
            "total_rules": semantic_summary.get("total_rules", 0),
            "average_specificity": semantic_summary.get("average_specificity", 0.0),
            "average_actionability": semantic_summary.get("average_actionability", 0.0),
            "average_grounding": semantic_summary.get("average_grounding", 0.0),
        },
        "recommendation": (
            "Improve low-scoring rules before accepting this style bible as the main path."
            if semantic_summary.get("status") != "pass"
            else ""
        ),
    }
    checks = [schema_check, section_check, semantic_check]

    overall_status = semantic_check["status"]
    if schema_check["status"] != "pass" or section_check["status"] == "fail":
        overall_status = "fail"
    elif section_check["status"] == "warn" and overall_status == "pass":
        overall_status = "warn"

    build_run_manifest_path, build_run_manifest = _load_build_run_manifest(source_dir)
    report = {
        "evaluation_version": "style-bible-eval-v3",
        "generated_at": utc_timestamp(),
        "input_dir": str(source_dir),
        "output_dir": str(output_path.resolve()),
        "rules_config": str(rules.rules_path),
        "style_id": _clean_text(style_bible_payload.get("style_id")),
        "scope": _clean_text(style_bible_payload.get("scope")),
        "semantic_judge_model": _clean_text(semantic_judge_model) or "offline_semantic_judge",
        "style_bible_schema_version": (
            _clean_text(build_run_manifest.get("style_bible_schema_version")) if isinstance(build_run_manifest, dict) else ""
        ) or STYLE_BIBLE_SCHEMA_VERSION,
        "hashes": {
            "style_bible_sha256": sha256_payload(style_bible_payload),
            "source_bundle_sha256": sha256_payload(source_bundle),
        },
        "summary": {
            "status": overall_status,
            "overall_score": float(semantic_summary.get("average_overall_score", 0.0) or 0.0),
            "max_score": 1.0,
        },
        "checks": checks,
        "semantic_evaluation": semantic_artifacts.report if semantic_artifacts is not None else {},
        "source_files": {
            "style_bible_file": str(style_bible_path),
            "source_bundle_file": str(source_bundle_path),
            "reduce_trace_file": str(reduce_trace_path) if reduce_trace_path.exists() else "",
            "coverage_report_file": str(source_dir / COVERAGE_REPORT_FILE) if (source_dir / COVERAGE_REPORT_FILE).exists() else "",
            "sampling_report_file": str(source_dir / SAMPLING_REPORT_FILE) if (source_dir / SAMPLING_REPORT_FILE).exists() else "",
            "build_run_manifest_file": str(build_run_manifest_path) if build_run_manifest_path else "",
        },
    }

    report_path = output_path / REPORT_JSON_FILE
    markdown_path = output_path / REPORT_MD_FILE
    write_json(report_path, report)
    write_markdown(markdown_path, _build_markdown_report(report))

    project_root = str(Path(rules_config).resolve().parent.parent)
    if isinstance(build_run_manifest, dict) and _clean_text(build_run_manifest.get("project_root")):
        project_root = _clean_text(build_run_manifest.get("project_root"))
    evaluation_manifest = build_style_bible_evaluation_manifest(
        project_root=project_root,
        rules_config=rules_config,
        input_dir=source_dir,
        output_dir=output_path,
        report_path=report_path,
        markdown_path=markdown_path,
        report=report,
        build_run_manifest=build_run_manifest,
        build_run_manifest_path=build_run_manifest_path,
    )
    evaluation_manifest_path = output_path / EVALUATION_MANIFEST_FILE
    write_json(evaluation_manifest_path, evaluation_manifest)
    return StyleBibleEvaluationResult(
        report_path=report_path,
        markdown_path=markdown_path,
        report=report,
        evaluation_manifest_path=evaluation_manifest_path,
        evaluation_manifest=evaluation_manifest,
    )


def run_style_bible_evaluation(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    rules_config: str | Path,
    resume: bool = False,
    semantic_judge_model: str = "",
) -> StyleBibleEvaluationResult | None:
    source_dir = Path(input_dir).resolve()
    output_path = ensure_dir(output_dir)
    report_path = output_path / REPORT_JSON_FILE
    manifest_path = output_path / "manifest.json"
    failures_path = output_path / "failures.json"
    tracker = RunTracker(
        stage="stable-evaluate-style-bible",
        output_dir=output_path,
        total_items=1,
        item_label="evaluation",
        source_dir=source_dir,
        metadata={
            "input_dir": str(source_dir),
            "rules_config": str(Path(rules_config).resolve()),
            "resume": resume,
            "semantic_judge_model": _clean_text(semantic_judge_model),
        },
    )

    if resume and report_path.exists():
        tracker.record_skip("style_bible_eval", f"Skipped existing output for {report_path.name}.", file_name=report_path.name)
        tracker.finish("Style bible evaluation skipped.", report_file=str(report_path.resolve()))
        return None

    try:
        result = evaluate_style_bible(
            input_dir,
            output_path,
            rules_config=rules_config,
            semantic_judge_model=semantic_judge_model,
        )
        write_json(
            manifest_path,
            [
                {
                    "evaluation_id": "style_bible_eval",
                    "report_file": result.report_path.name,
                    "markdown_file": result.markdown_path.name,
                    "style_id": result.report.get("style_id", ""),
                    "scope": result.report.get("scope", ""),
                    "status": result.report.get("summary", {}).get("status", ""),
                    "overall_score": result.report.get("summary", {}).get("overall_score", 0.0),
                    "semantic_judge_model": result.report.get("semantic_judge_model", ""),
                    "evaluation_manifest_file": result.evaluation_manifest_path.name if result.evaluation_manifest_path else "",
                }
            ],
        )
        write_json(failures_path, [])
        tracker.record_success(
            "style_bible_eval",
            f"Wrote {result.report_path.name}.",
            report_file=result.report_path.name,
            markdown_file=result.markdown_path.name,
            status=result.report.get("summary", {}).get("status", ""),
            overall_score=result.report.get("summary", {}).get("overall_score", 0.0),
        )
        tracker.finish(
            "Style bible evaluation completed.",
            report_file=str(result.report_path.resolve()),
            markdown_file=str(result.markdown_path.resolve()),
        )
        return result
    except Exception as exc:  # noqa: BLE001
        write_json(
            failures_path,
            [
                {
                    "evaluation_id": "style_bible_eval",
                    "report_file": report_path.name,
                    "rules_config": str(Path(rules_config).resolve()),
                    "semantic_judge_model": _clean_text(semantic_judge_model),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            ],
        )
        tracker.record_failure("style_bible_eval", f"Style bible evaluation failed: {exc}", error_type=type(exc).__name__)
        tracker.fail_run(f"Style bible evaluation aborted: {exc}", error_type=type(exc).__name__)
        raise


__all__ = [
    "REPORT_JSON_FILE",
    "REPORT_MD_FILE",
    "StyleBibleEvaluationResult",
    "StyleBibleEvalRules",
    "_evaluate_section_completeness",
    "_load_rules",
    "evaluate_style_bible",
    "run_style_bible_evaluation",
]
