from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import ensure_dir, read_json, write_json, write_jsonl, write_markdown, write_text
from novel_pipeline_stable.monitoring import RunTracker, utc_timestamp


JUDGE_REPORT_JSON_FILE = "judge_report.json"
JUDGE_ROWS_JSONL_FILE = "judge_rows.jsonl"
COMPARE_REPORT_JSON_FILE = "style_compare_report.json"
COMPARE_REPORT_MD_FILE = "style_compare_report.md"
COMPARE_ROWS_JSONL_FILE = "style_compare_rows.jsonl"


@dataclass(slots=True)
class CompareResult:
    report_path: Path
    markdown_path: Path
    rows_path: Path
    report: dict[str, Any]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _load_judge_bundle(judge_dir: str | Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    root = Path(judge_dir).resolve()
    report_path = root / JUDGE_REPORT_JSON_FILE
    rows_path = root / JUDGE_ROWS_JSONL_FILE
    if not report_path.exists():
        raise FileNotFoundError(f"Judge report not found: {report_path}")
    if not rows_path.exists():
        raise FileNotFoundError(f"Judge rows not found: {rows_path}")
    report = read_json(report_path)
    if not isinstance(report, dict):
        raise ValueError(f"Judge report must be a JSON object: {report_path}")
    rows = _read_jsonl(rows_path)
    return root, report, rows


def _winner_from_delta(delta: float, *, min_delta: float) -> str:
    if delta > min_delta:
        return "a"
    if delta < -min_delta:
        return "b"
    return "tie"


def compare_judge_outputs(
    *,
    judge_a_report: dict[str, Any],
    judge_a_rows: list[dict[str, Any]],
    judge_b_report: dict[str, Any],
    judge_b_rows: list[dict[str, Any]],
    min_delta: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    node_a = _clean_text(judge_a_report.get("node_id"))
    node_b = _clean_text(judge_b_report.get("node_id"))
    if node_a and node_b and node_a != node_b:
        raise ValueError(f"Judge reports belong to different nodes: {node_a} vs {node_b}")

    rows_a = {str(row.get("row_id")): row for row in judge_a_rows if _clean_text(row.get("row_id"))}
    rows_b = {str(row.get("row_id")): row for row in judge_b_rows if _clean_text(row.get("row_id"))}
    common_row_ids = sorted(set(rows_a) & set(rows_b))
    compare_rows: list[dict[str, Any]] = []
    wins_a = 0
    wins_b = 0
    ties = 0

    for row_id in common_row_ids:
        row_a = rows_a[row_id]
        row_b = rows_b[row_id]
        score_a = float(row_a.get("score", 0) or 0)
        score_b = float(row_b.get("score", 0) or 0)
        ratio_a = float(row_a.get("ratio", 0) or 0)
        ratio_b = float(row_b.get("ratio", 0) or 0)
        delta = round(score_a - score_b, 2)
        ratio_delta = round(ratio_a - ratio_b, 4)
        winner = _winner_from_delta(delta, min_delta=min_delta)
        if winner == "a":
            wins_a += 1
        elif winner == "b":
            wins_b += 1
        else:
            ties += 1
        compare_rows.append(
            {
                "row_id": row_id,
                "case_id": _clean_text(row_a.get("case_id")),
                "dimension": _clean_text(row_a.get("dimension")),
                "score_a": score_a,
                "score_b": score_b,
                "delta": delta,
                "ratio_a": ratio_a,
                "ratio_b": ratio_b,
                "ratio_delta": ratio_delta,
                "winner": winner,
                "status_a": _clean_text(row_a.get("status")),
                "status_b": _clean_text(row_b.get("status")),
            }
        )

    case_rows_a = {
        _clean_text(item.get("case_id")): item
        for item in judge_a_report.get("case_results", [])
        if isinstance(item, dict) and _clean_text(item.get("case_id"))
    }
    case_rows_b = {
        _clean_text(item.get("case_id")): item
        for item in judge_b_report.get("case_results", [])
        if isinstance(item, dict) and _clean_text(item.get("case_id"))
    }
    common_case_ids = sorted(set(case_rows_a) & set(case_rows_b))
    case_deltas: list[dict[str, Any]] = []
    for case_id in common_case_ids:
        case_a = case_rows_a[case_id]
        case_b = case_rows_b[case_id]
        score_a = float(case_a.get("score", 0) or 0)
        score_b = float(case_b.get("score", 0) or 0)
        case_deltas.append(
            {
                "case_id": case_id,
                "score_a": score_a,
                "score_b": score_b,
                "delta": round(score_a - score_b, 2),
                "status_a": _clean_text(case_a.get("status")),
                "status_b": _clean_text(case_b.get("status")),
            }
        )
    case_deltas.sort(key=lambda row: abs(float(row.get("delta", 0) or 0)), reverse=True)

    dimension_scores_a = judge_a_report.get("summary", {}).get("dimension_scores", {})
    dimension_scores_b = judge_b_report.get("summary", {}).get("dimension_scores", {})
    common_dimensions = sorted(set(dimension_scores_a) & set(dimension_scores_b))
    dimension_deltas = []
    for dimension in common_dimensions:
        score_a = float(dimension_scores_a.get(dimension, 0) or 0)
        score_b = float(dimension_scores_b.get(dimension, 0) or 0)
        dimension_deltas.append(
            {
                "dimension": dimension,
                "score_a": score_a,
                "score_b": score_b,
                "delta": round(score_a - score_b, 2),
            }
        )
    dimension_deltas.sort(key=lambda row: abs(float(row.get("delta", 0) or 0)), reverse=True)

    eval_summary_a = judge_a_report.get("rule_evaluation_summary", {})
    eval_summary_b = judge_b_report.get("rule_evaluation_summary", {})
    eval_score_a = float(eval_summary_a.get("overall_score", 0) or 0)
    eval_score_b = float(eval_summary_b.get("overall_score", 0) or 0)
    judge_score_a = float(judge_a_report.get("summary", {}).get("overall_score", 0) or 0)
    judge_score_b = float(judge_b_report.get("summary", {}).get("overall_score", 0) or 0)
    pairwise_total = len(common_row_ids)
    pairwise_win_rate_a = 0.0 if pairwise_total == 0 else round(wins_a / pairwise_total, 4)
    pairwise_win_rate_b = 0.0 if pairwise_total == 0 else round(wins_b / pairwise_total, 4)

    overall_delta = round(judge_score_a - judge_score_b, 2)
    if pairwise_win_rate_a > pairwise_win_rate_b:
        winner = "a"
    elif pairwise_win_rate_b > pairwise_win_rate_a:
        winner = "b"
    else:
        winner = _winner_from_delta(overall_delta, min_delta=min_delta)

    risk_flags: list[str] = []
    if winner == "a" and eval_score_a < eval_score_b:
        risk_flags.append("run_a wins on judge criteria, but its offline rule-eval score is lower.")
    if winner == "b" and eval_score_b < eval_score_a:
        risk_flags.append("run_b wins on judge criteria, but its offline rule-eval score is lower.")
    if winner == "a" and not bool(eval_summary_a.get("quality_gate_passed", True)):
        risk_flags.append("run_a wins pairwise, but its offline rule gate is not passed.")
    if winner == "b" and not bool(eval_summary_b.get("quality_gate_passed", True)):
        risk_flags.append("run_b wins pairwise, but its offline rule gate is not passed.")

    summary = {
        "winner": winner,
        "pairwise_row_count": pairwise_total,
        "wins_a": wins_a,
        "wins_b": wins_b,
        "ties": ties,
        "pairwise_win_rate_a": pairwise_win_rate_a,
        "pairwise_win_rate_b": pairwise_win_rate_b,
        "judge_score_a": judge_score_a,
        "judge_score_b": judge_score_b,
        "judge_score_delta": overall_delta,
        "eval_score_a": eval_score_a,
        "eval_score_b": eval_score_b,
        "eval_score_delta": round(eval_score_a - eval_score_b, 2),
        "pass_case_delta": int(judge_a_report.get("summary", {}).get("pass_case_count", 0) or 0)
        - int(judge_b_report.get("summary", {}).get("pass_case_count", 0) or 0),
        "fail_case_delta": int(judge_a_report.get("summary", {}).get("fail_case_count", 0) or 0)
        - int(judge_b_report.get("summary", {}).get("fail_case_count", 0) or 0),
    }
    report = {
        "compare_version": "style-compare-v1",
        "generated_at": utc_timestamp(),
        "node_id": node_a or node_b,
        "run_a": {
            "style_id": _clean_text(judge_a_report.get("style_id")),
            "run_id": _clean_text(judge_a_report.get("run_id")),
            "judge_summary": judge_a_report.get("summary", {}),
            "rule_evaluation_summary": eval_summary_a,
        },
        "run_b": {
            "style_id": _clean_text(judge_b_report.get("style_id")),
            "run_id": _clean_text(judge_b_report.get("run_id")),
            "judge_summary": judge_b_report.get("summary", {}),
            "rule_evaluation_summary": eval_summary_b,
        },
        "summary": summary,
        "dimension_deltas": dimension_deltas,
        "case_deltas": case_deltas,
        "risk_flags": risk_flags,
    }
    return report, compare_rows


def _build_markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Style Compare Report",
        "",
        f"- node_id: `{_clean_text(report.get('node_id'))}`",
        f"- winner: `{_clean_text(summary.get('winner'))}`",
        f"- pairwise rows: `{summary.get('pairwise_row_count', 0)}`",
        f"- judge_score_delta: `{summary.get('judge_score_delta', 0)}`",
        f"- eval_score_delta: `{summary.get('eval_score_delta', 0)}`",
        "",
        "## Dimension Deltas",
        "",
        "| dimension | score_a | score_b | delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in report.get("dimension_deltas", []):
        lines.append(
            f"| `{row.get('dimension', '')}` | {row.get('score_a', 0)} | {row.get('score_b', 0)} | {row.get('delta', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Largest Case Deltas",
            "",
            "| case_id | score_a | score_b | delta |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in report.get("case_deltas", [])[:10]:
        lines.append(
            f"| `{row.get('case_id', '')}` | {row.get('score_a', 0)} | {row.get('score_b', 0)} | {row.get('delta', 0)} |"
        )
    if report.get("risk_flags"):
        lines.extend(["", "## Risk Flags", ""])
        for flag in report.get("risk_flags", []):
            lines.append(f"- {flag}")
    return "\n".join(lines) + "\n"


def run_style_run_comparison(
    judge_a_dir: str | Path,
    judge_b_dir: str | Path,
    output_dir: str | Path,
    *,
    min_delta: float = 1.0,
    resume: bool = False,
) -> CompareResult | None:
    output_path = ensure_dir(output_dir).resolve()
    report_path = output_path / COMPARE_REPORT_JSON_FILE
    markdown_path = output_path / COMPARE_REPORT_MD_FILE
    rows_path = output_path / COMPARE_ROWS_JSONL_FILE
    if resume and report_path.exists() and markdown_path.exists() and rows_path.exists():
        return None

    tracker = RunTracker(
        stage="stable-compare-style-runs",
        output_dir=output_path,
        total_items=1,
        item_label="compare",
        metadata={
            "judge_a_dir": str(Path(judge_a_dir).resolve()),
            "judge_b_dir": str(Path(judge_b_dir).resolve()),
        },
    )
    try:
        judge_a_root, judge_a_report, judge_a_rows = _load_judge_bundle(judge_a_dir)
        judge_b_root, judge_b_report, judge_b_rows = _load_judge_bundle(judge_b_dir)
        report, compare_rows = compare_judge_outputs(
            judge_a_report=judge_a_report,
            judge_a_rows=judge_a_rows,
            judge_b_report=judge_b_report,
            judge_b_rows=judge_b_rows,
            min_delta=min_delta,
        )
        report["source_files"] = {
            "judge_a_dir": str(judge_a_root),
            "judge_b_dir": str(judge_b_root),
            "judge_a_report_file": str(judge_a_root / JUDGE_REPORT_JSON_FILE),
            "judge_b_report_file": str(judge_b_root / JUDGE_REPORT_JSON_FILE),
        }
        write_json(report_path, report)
        write_jsonl(rows_path, compare_rows)
        write_markdown(markdown_path, _build_markdown_report(report))
        tracker.record_success(
            "compare-style-runs",
            "Pairwise style comparison written.",
            winner=report.get("summary", {}).get("winner", ""),
            pairwise_row_count=report.get("summary", {}).get("pairwise_row_count", 0),
        )
        tracker.finish(
            "Style run comparison completed.",
            report_file=str(report_path),
            rows_file=str(rows_path),
        )
        return CompareResult(report_path=report_path, markdown_path=markdown_path, rows_path=rows_path, report=report)
    except Exception as exc:  # noqa: BLE001
        tracker.fail_run(f"Style run comparison aborted: {exc}", error_type=type(exc).__name__)
        raise
