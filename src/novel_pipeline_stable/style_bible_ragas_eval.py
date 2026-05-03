from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import ensure_dir, read_json, write_json, write_jsonl, write_markdown
from novel_pipeline_stable.models import StyleBibleResultV2
from novel_pipeline_stable.style_bible_contracts import REDUCE_TRACE_FILE
from novel_pipeline_stable.style_bible_judge import _semantic_similarity


STYLE_BIBLE_FILE = "style_bible_final.json"
SOURCE_BUNDLE_FILE = "style_bible_source_bundle.json"
SEMANTIC_ROWS_JSONL_FILE = "semantic_rows.jsonl"
SEMANTIC_DATASET_JSON_FILE = "semantic_dataset.json"
SEMANTIC_REPORT_JSON_FILE = "semantic_report.json"
SEMANTIC_REPORT_MD_FILE = "semantic_report.md"
SEMANTIC_REPORT_VERSION = "style-bible-semantic-eval-v1"
DEFAULT_WEIGHTS = {
    "specificity": 0.34,
    "actionability": 0.33,
    "grounding": 0.33,
}
DEFAULT_THRESHOLDS = {
    "row_pass_score": 0.72,
    "row_warn_score": 0.55,
}
CONTEXT_TEXT_KEYS = (
    "evidence_text",
    "claim",
    "summary",
    "scene_summary",
    "text",
    "note",
    "change",
    "observed_commonality",
    "mechanism_inference",
    "downstream_constraint",
)


@dataclass(slots=True)
class StyleBibleSemanticEvalArtifacts:
    rows: list[dict[str, Any]]
    dataset_rows: list[dict[str, Any]]
    report: dict[str, Any]


@dataclass(slots=True)
class StyleBibleSemanticEvalResult:
    report_path: Path
    markdown_path: Path
    rows_path: Path
    dataset_path: Path
    report: dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _unique_strings(values: list[Any]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        rows.append(cleaned)
    return rows


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _status_for_score(score: float, *, thresholds: dict[str, float]) -> str:
    if score >= float(thresholds.get("row_pass_score", DEFAULT_THRESHOLDS["row_pass_score"])):
        return "pass"
    if score >= float(thresholds.get("row_warn_score", DEFAULT_THRESHOLDS["row_warn_score"])):
        return "warn"
    return "fail"


def _preferred_refs(payload: dict[str, Any]) -> list[str]:
    refs = _unique_strings(
        [
            *list(payload.get("source_refs", []) or []),
            *list(payload.get("evidence_refs", []) or []),
        ]
    )
    source_ref = _clean_text(payload.get("source_ref"))
    if source_ref:
        refs.append(source_ref)
    scene_id = _clean_text(payload.get("scene_id"))
    if scene_id:
        refs.append(f"scene:{scene_id}")
    chapter_id = _clean_text(payload.get("chapter_id"))
    if chapter_id:
        refs.append(f"chapter:{chapter_id}")
    window_id = _clean_text(payload.get("window_id"))
    if window_id:
        refs.append(window_id)
    return _unique_strings(refs)


def _collect_reference_pool(payload: Any) -> set[str]:
    refs: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for ref in _preferred_refs(node):
                refs.add(ref)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return refs


def _append_context(index: dict[str, list[str]], ref: str, text: str) -> None:
    cleaned_ref = _clean_text(ref)
    cleaned_text = _clean_text(text)
    if not cleaned_ref or not cleaned_text:
        return
    bucket = index.setdefault(cleaned_ref, [])
    if cleaned_text not in bucket:
        bucket.append(cleaned_text)


def _collect_context_index(payload: Any) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            refs = _preferred_refs(node)
            texts = _unique_strings([node.get(key, "") for key in CONTEXT_TEXT_KEYS])
            if refs and texts:
                for ref in refs:
                    for text in texts:
                        _append_context(index, ref, text)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return index


def _row_family(row: dict[str, Any]) -> str:
    if _clean_text(row.get("query_feature_matcher")) or _clean_text(row.get("route_target_action")):
        return "routing_hint"
    if _clean_text(row.get("forbidden_action")) or _clean_text(row.get("correction_guideline")):
        return "negative"
    if _clean_text(row.get("trigger")) or _clean_text(row.get("constraint")):
        if _clean_text(row.get("path")).startswith("worldbook_binding."):
            return "worldbook_fact"
        return "narrative"
    return "scalar"


def _structured_anchor_text(row: dict[str, Any]) -> str:
    family = _row_family(row)
    if family == "routing_hint":
        return " | ".join(
            _unique_strings(
                [
                    row.get("query_feature_matcher", ""),
                    row.get("route_target_action", ""),
                ]
            )
        )
    if family == "negative":
        return " | ".join(
            _unique_strings(
                [
                    row.get("forbidden_action", ""),
                    row.get("correction_guideline", ""),
                ]
            )
        )
    if family in {"narrative", "worldbook_fact"}:
        return " | ".join(
            _unique_strings(
                [
                    row.get("trigger", ""),
                    row.get("constraint", ""),
                ]
            )
        )
    return _clean_text(row.get("text"))


def _contract_completeness(row: dict[str, Any]) -> float:
    family = _row_family(row)
    if family == "routing_hint":
        required = ("query_feature_matcher", "route_target_action")
    elif family == "negative":
        required = ("forbidden_action", "correction_guideline")
    elif family in {"narrative", "worldbook_fact"}:
        required = ("trigger", "constraint")
    else:
        required = ("text",)
    filled = sum(1 for field_name in required if _clean_text(row.get(field_name)))
    return round(filled / max(len(required), 1), 4)


def _top_similarity(source_text: str, candidates: list[str], *, top_k: int = 3) -> float:
    cleaned_source = _clean_text(source_text)
    cleaned_candidates = [_clean_text(candidate) for candidate in candidates if _clean_text(candidate)]
    if not cleaned_source or not cleaned_candidates:
        return 0.0
    scores = sorted((_semantic_similarity(cleaned_source, candidate) for candidate in cleaned_candidates), reverse=True)
    return _average(scores[: min(top_k, len(scores))])


def _specificity_score(row: dict[str, Any], context_texts: list[str]) -> float:
    text = _clean_text(row.get("text"))
    anchor = _structured_anchor_text(row)
    contract_score = _contract_completeness(row)
    anchor_similarity = _semantic_similarity(text, anchor) if text and anchor else contract_score
    context_similarity = _top_similarity(anchor or text, context_texts)
    evidence_density = min(len(row.get("evidence_refs", []) or []) / 2.0, 1.0)
    return round((0.5 * anchor_similarity) + (0.3 * context_similarity) + (0.2 * evidence_density), 4)


def _actionability_score(row: dict[str, Any]) -> float:
    contract_score = _contract_completeness(row)
    text = _clean_text(row.get("text"))
    anchor = _structured_anchor_text(row)
    anchor_alignment = _semantic_similarity(text, anchor) if text and anchor else contract_score
    return round((0.7 * contract_score) + (0.3 * anchor_alignment), 4)


def _grounding_score(row: dict[str, Any], *, context_texts: list[str], reference_pool: set[str]) -> tuple[float, int]:
    evidence_refs = [
        _clean_text(ref)
        for ref in row.get("evidence_refs", [])
        if _clean_text(ref)
    ]
    valid_ref_count = sum(1 for ref in evidence_refs if ref in reference_pool)
    ref_ratio = round(valid_ref_count / len(evidence_refs), 4) if evidence_refs else 0.0
    context_similarity = _top_similarity(_clean_text(row.get("text")), context_texts)
    return round((0.55 * ref_ratio) + (0.45 * context_similarity), 4), valid_ref_count


def _iter_rule_rows(payload: Any, *, path: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if path in {"supporting_evidence", "metadata"}:
            return rows
        if "rule_id" in payload and "text" in payload and isinstance(payload.get("evidence_refs"), list):
            rows.append(
                {
                    "path": path,
                    "rule_id": _clean_text(payload.get("rule_id")) or path or "rule",
                    "text": _clean_text(payload.get("text")),
                    "reasoning_ref": _clean_text(payload.get("_reasoning_ref") or payload.get("reasoning_ref")),
                    "evidence_refs": _unique_strings(list(payload.get("evidence_refs", []) or [])),
                    "query_feature_matcher": _clean_text(payload.get("query_feature_matcher")),
                    "route_target_action": _clean_text(payload.get("route_target_action")),
                    "trigger": _clean_text(payload.get("trigger")),
                    "constraint": _clean_text(payload.get("constraint")),
                    "forbidden_action": _clean_text(payload.get("forbidden_action")),
                    "correction_guideline": _clean_text(payload.get("correction_guideline")),
                }
            )
            return rows
        for key, value in payload.items():
            if key in {"supporting_evidence", "metadata"}:
                continue
            child_path = f"{path}.{key}" if path else str(key)
            rows.extend(_iter_rule_rows(value, path=child_path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            rows.extend(_iter_rule_rows(item, path=f"{path}[{index}]"))
    return rows


def _rule_dataset_row(metric: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": metric["rule_id"],
        "path": metric["path"],
        "user_input": metric["anchor_text"] or metric["text"],
        "response": metric["text"],
        "retrieved_contexts": metric["context_texts"],
        "semantic_scores": {
            "specificity": metric["specificity"],
            "actionability": metric["actionability"],
            "grounding": metric["grounding"],
            "overall_score": metric["overall_score"],
            "status": metric["status"],
        },
    }


def build_style_bible_semantic_report(
    style_bible_payload: dict[str, Any],
    source_bundle: dict[str, Any],
    reduce_trace_payload: dict[str, Any] | None = None,
    *,
    weights: dict[str, float] | None = None,
    thresholds: dict[str, float] | None = None,
    semantic_judge_model: str = "",
) -> StyleBibleSemanticEvalArtifacts:
    parsed = StyleBibleResultV2.model_validate(style_bible_payload)
    normalized_payload = parsed.model_dump(mode="json", by_alias=True)
    resolved_weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    resolved_thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    reduce_trace = reduce_trace_payload or {}

    reference_pool = _collect_reference_pool(source_bundle)
    if reduce_trace:
        reference_pool.update(_collect_reference_pool(reduce_trace))
    context_index = _collect_context_index(source_bundle)
    if reduce_trace:
        reduce_trace_context_index = _collect_context_index(reduce_trace)
        for ref, texts in reduce_trace_context_index.items():
            for text in texts:
                _append_context(context_index, ref, text)

    metrics: list[dict[str, Any]] = []
    for row in _iter_rule_rows(normalized_payload):
        evidence_refs = list(row.get("evidence_refs", []) or [])
        context_texts = _unique_strings([text for ref in evidence_refs for text in context_index.get(ref, [])])
        specificity = _specificity_score(row, context_texts)
        actionability = _actionability_score(row)
        grounding, valid_ref_count = _grounding_score(
            row,
            context_texts=context_texts,
            reference_pool=reference_pool,
        )
        overall_score = round(
            (float(resolved_weights.get("specificity", 0.0)) * specificity)
            + (float(resolved_weights.get("actionability", 0.0)) * actionability)
            + (float(resolved_weights.get("grounding", 0.0)) * grounding),
            4,
        )
        status = _status_for_score(overall_score, thresholds=resolved_thresholds)
        metrics.append(
            {
                "rule_id": row["rule_id"],
                "path": row["path"],
                "rule_family": _row_family(row),
                "text": row["text"],
                "anchor_text": _structured_anchor_text(row),
                "reasoning_ref": row["reasoning_ref"],
                "evidence_refs": evidence_refs,
                "context_texts": context_texts,
                "context_text_count": len(context_texts),
                "valid_ref_count": valid_ref_count,
                "specificity": specificity,
                "actionability": actionability,
                "grounding": grounding,
                "overall_score": overall_score,
                "status": status,
            }
        )

    specificity_scores = [float(row["specificity"]) for row in metrics]
    actionability_scores = [float(row["actionability"]) for row in metrics]
    grounding_scores = [float(row["grounding"]) for row in metrics]
    overall_scores = [float(row["overall_score"]) for row in metrics]
    weak_rules = sorted(metrics, key=lambda row: (float(row["overall_score"]), row["rule_id"]))[:5]
    summary = {
        "total_rules": len(metrics),
        "pass_count": sum(1 for row in metrics if row["status"] == "pass"),
        "warn_count": sum(1 for row in metrics if row["status"] == "warn"),
        "fail_count": sum(1 for row in metrics if row["status"] == "fail"),
        "average_specificity": _average(specificity_scores),
        "average_actionability": _average(actionability_scores),
        "average_grounding": _average(grounding_scores),
        "average_overall_score": _average(overall_scores),
        "status": _status_for_score(_average(overall_scores), thresholds=resolved_thresholds) if metrics else "fail",
        "weak_rules": weak_rules,
    }
    report = {
        "report_version": SEMANTIC_REPORT_VERSION,
        "generated_at": _utc_now_iso(),
        "style_id": _clean_text(parsed.style_id),
        "scope": _clean_text(parsed.scope),
        "judge_model_name": "offline_semantic_rule_engine",
        "requested_semantic_judge_model": _clean_text(semantic_judge_model),
        "decision_source": "offline_semantic_rule_engine",
        "weights": resolved_weights,
        "thresholds": resolved_thresholds,
        "summary": summary,
        "rows": metrics,
        "source_files": {
            "style_bible_file": STYLE_BIBLE_FILE,
            "source_bundle_file": SOURCE_BUNDLE_FILE,
            "reduce_trace_file": REDUCE_TRACE_FILE if reduce_trace else "",
        },
    }
    dataset_rows = [_rule_dataset_row(metric) for metric in metrics]
    return StyleBibleSemanticEvalArtifacts(rows=metrics, dataset_rows=dataset_rows, report=report)


def _build_markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    weak_rules = summary.get("weak_rules", [])
    lines = [
        "# Style Bible Semantic Evaluation Report",
        "",
        f"- generated_at: {report.get('generated_at', '')}",
        f"- report_version: {report.get('report_version', '')}",
        f"- judge_model_name: {report.get('judge_model_name', '')}",
        f"- requested_semantic_judge_model: {report.get('requested_semantic_judge_model', '')}",
        f"- decision_source: {report.get('decision_source', '')}",
        f"- total_rules: {summary.get('total_rules', 0)}",
        f"- average_specificity: {summary.get('average_specificity', 0.0)}",
        f"- average_actionability: {summary.get('average_actionability', 0.0)}",
        f"- average_grounding: {summary.get('average_grounding', 0.0)}",
        f"- average_overall_score: {summary.get('average_overall_score', 0.0)}",
        f"- status: {summary.get('status', '')}",
        "",
        "## Weak Rules",
        "",
    ]
    if not weak_rules:
        lines.append("- none")
    else:
        for row in weak_rules:
            lines.append(
                f"- `{row.get('path', '')}` / `{row.get('rule_id', '')}` / "
                f"overall={row.get('overall_score', 0.0)} / text={row.get('text', '')}"
            )
    return "\n".join(lines) + "\n"


def run_style_bible_semantic_eval(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    resume: bool = False,
    weights: dict[str, float] | None = None,
    thresholds: dict[str, float] | None = None,
    semantic_judge_model: str = "",
) -> StyleBibleSemanticEvalResult | None:
    source_dir = Path(input_dir).resolve()
    output_path = ensure_dir(output_dir).resolve()
    report_path = output_path / SEMANTIC_REPORT_JSON_FILE
    markdown_path = output_path / SEMANTIC_REPORT_MD_FILE
    rows_path = output_path / SEMANTIC_ROWS_JSONL_FILE
    dataset_path = output_path / SEMANTIC_DATASET_JSON_FILE

    if resume and report_path.exists() and markdown_path.exists() and rows_path.exists() and dataset_path.exists():
        return None

    style_bible_path = source_dir / STYLE_BIBLE_FILE
    source_bundle_path = source_dir / SOURCE_BUNDLE_FILE
    reduce_trace_path = source_dir / REDUCE_TRACE_FILE
    if not style_bible_path.exists():
        raise FileNotFoundError(f"Style bible file not found: {style_bible_path}")
    if not source_bundle_path.exists():
        raise FileNotFoundError(f"Style bible source bundle not found: {source_bundle_path}")

    style_bible_payload = read_json(style_bible_path)
    source_bundle = read_json(source_bundle_path)
    reduce_trace_payload = read_json(reduce_trace_path) if reduce_trace_path.exists() else {}
    if not isinstance(style_bible_payload, dict):
        raise ValueError(f"Style bible payload must be an object: {style_bible_path}")
    if not isinstance(source_bundle, dict):
        raise ValueError(f"Source bundle payload must be an object: {source_bundle_path}")
    if reduce_trace_payload and not isinstance(reduce_trace_payload, dict):
        raise ValueError(f"Reduce trace payload must be an object: {reduce_trace_path}")

    artifacts = build_style_bible_semantic_report(
        style_bible_payload,
        source_bundle,
        reduce_trace_payload,
        weights=weights,
        thresholds=thresholds,
        semantic_judge_model=semantic_judge_model,
    )
    write_jsonl(rows_path, artifacts.rows)
    write_json(dataset_path, {"report_version": SEMANTIC_REPORT_VERSION, "rows": artifacts.dataset_rows})
    write_json(report_path, artifacts.report)
    write_markdown(markdown_path, _build_markdown_report(artifacts.report))
    return StyleBibleSemanticEvalResult(
        report_path=report_path,
        markdown_path=markdown_path,
        rows_path=rows_path,
        dataset_path=dataset_path,
        report=artifacts.report,
    )


__all__ = [
    "SEMANTIC_DATASET_JSON_FILE",
    "SEMANTIC_REPORT_JSON_FILE",
    "SEMANTIC_REPORT_MD_FILE",
    "SEMANTIC_REPORT_VERSION",
    "SEMANTIC_ROWS_JSONL_FILE",
    "StyleBibleSemanticEvalArtifacts",
    "StyleBibleSemanticEvalResult",
    "build_style_bible_semantic_report",
    "run_style_bible_semantic_eval",
]
