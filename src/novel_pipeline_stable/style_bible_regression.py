from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import ensure_dir, write_json, write_jsonl, write_markdown, write_text
from novel_pipeline_stable.monitoring import RunTracker, utc_timestamp
from novel_pipeline_stable.style_bible_compare import (
    JUDGE_REPORT_JSON_FILE,
    compare_judge_outputs,
    _clean_text,  # type: ignore[attr-defined]
    _load_judge_bundle,  # type: ignore[attr-defined]
)


REGRESSION_REPORT_JSON_FILE = "style_regression_report.json"
REGRESSION_REPORT_MD_FILE = "style_regression_report.md"
REGRESSION_ROWS_JSONL_FILE = "style_regression_rows.jsonl"


@dataclass(slots=True)
class RegressionRules:
    rules_path: Path
    thresholds: dict[str, float]
    hard_gates: dict[str, bool]
    critical_dimensions: list[str]


@dataclass(slots=True)
class RegressionResult:
    report_path: Path
    markdown_path: Path
    rows_path: Path
    report: dict[str, Any]


def _load_regression_rules(rules_path: str | Path) -> RegressionRules:
    path = Path(rules_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Regression rules config not found: {path}")
    payload = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    thresholds = payload.get("thresholds", {})
    hard_gates = payload.get("hard_gates", {})
    critical_dimensions = payload.get("critical_dimensions", {})
    return RegressionRules(
        rules_path=path,
        thresholds={str(key): float(value or 0) for key, value in thresholds.items()},
        hard_gates={str(key): bool(value) for key, value in hard_gates.items()},
        critical_dimensions=[_clean_text(item) for item in critical_dimensions.get("ids", []) if _clean_text(item)],
    )


def _dimension_max_scores(rows: list[dict[str, Any]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        dimension = _clean_text(row.get("dimension")) or _clean_text(row.get("criterion_id"))
        max_score = float(row.get("max_score", 0) or 0)
        if dimension and max_score > 0 and dimension not in scores:
            scores[dimension] = max_score
    return scores


def _score_pct(score: float, max_score: float) -> float:
    if max_score <= 0:
        return 0.0
    return round((float(score) / max_score) * 100.0, 2)


def _build_markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Style Regression Report",
        "",
        f"- node_id: `{_clean_text(report.get('node_id'))}`",
        f"- status: `{_clean_text(summary.get('status'))}`",
        f"- allow_downstream_export: `{summary.get('allow_downstream_export', False)}`",
        f"- judge_score_drop: `{summary.get('judge_score_drop', 0)}`",
        f"- eval_score_drop: `{summary.get('eval_score_drop', 0)}`",
        f"- fail_case_increase: `{summary.get('fail_case_increase', 0)}`",
        "",
    ]
    if report.get("hard_gate_failures"):
        lines.extend(["## Hard Gate Failures", ""])
        for item in report.get("hard_gate_failures", []):
            lines.append(f"- {item}")
        lines.append("")
    if report.get("soft_regressions"):
        lines.extend(["## Soft Regressions", ""])
        for item in report.get("soft_regressions", []):
            lines.append(f"- {item}")
        lines.append("")
    lines.extend(
        [
            "## Critical Dimension Deltas",
            "",
            "| dimension | baseline % | candidate % | delta |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in report.get("critical_dimension_deltas", []):
        lines.append(
            f"| `{row.get('dimension', '')}` | {row.get('baseline_score', 0)} | {row.get('candidate_score', 0)} | {row.get('delta', 0)} |"
        )
    return "\n".join(lines) + "\n"


def run_style_quality_regression(
    baseline_judge_dir: str | Path,
    candidate_judge_dir: str | Path,
    output_dir: str | Path,
    *,
    threshold_config: str | Path,
    resume: bool = False,
) -> RegressionResult | None:
    output_path = ensure_dir(output_dir).resolve()
    report_path = output_path / REGRESSION_REPORT_JSON_FILE
    markdown_path = output_path / REGRESSION_REPORT_MD_FILE
    rows_path = output_path / REGRESSION_ROWS_JSONL_FILE
    if resume and report_path.exists() and markdown_path.exists() and rows_path.exists():
        return None

    tracker = RunTracker(
        stage="stable-regress-style-quality",
        output_dir=output_path,
        total_items=1,
        item_label="regression",
        metadata={
            "baseline_judge_dir": str(Path(baseline_judge_dir).resolve()),
            "candidate_judge_dir": str(Path(candidate_judge_dir).resolve()),
            "threshold_config": str(Path(threshold_config).resolve()),
        },
    )
    try:
        rules = _load_regression_rules(threshold_config)
        baseline_root, baseline_report, baseline_rows = _load_judge_bundle(baseline_judge_dir)
        candidate_root, candidate_report, candidate_rows = _load_judge_bundle(candidate_judge_dir)
        compare_report, compare_rows = compare_judge_outputs(
            judge_a_report=baseline_report,
            judge_a_rows=baseline_rows,
            judge_b_report=candidate_report,
            judge_b_rows=candidate_rows,
            min_delta=0.0,
        )

        baseline_summary = baseline_report.get("summary", {})
        candidate_summary = candidate_report.get("summary", {})
        baseline_eval = baseline_report.get("rule_evaluation_summary", {})
        candidate_eval = candidate_report.get("rule_evaluation_summary", {})
        baseline_dimension_max = _dimension_max_scores(baseline_rows)
        candidate_dimension_max = _dimension_max_scores(candidate_rows)

        judge_score_drop = round(
            float(baseline_summary.get("overall_score", 0) or 0) - float(candidate_summary.get("overall_score", 0) or 0),
            2,
        )
        eval_score_drop = round(
            float(baseline_eval.get("overall_score", 0) or 0) - float(candidate_eval.get("overall_score", 0) or 0),
            2,
        )
        fail_case_increase = int(candidate_summary.get("fail_case_count", 0) or 0) - int(
            baseline_summary.get("fail_case_count", 0) or 0
        )
        pass_case_ratio = float(candidate_summary.get("pass_case_ratio", 0) or 0)

        hard_gate_failures: list[str] = []
        if rules.hard_gates.get("require_candidate_eval_quality_gate", True) and candidate_eval:
            if not bool(candidate_eval.get("quality_gate_passed", False)):
                hard_gate_failures.append("Candidate offline rule-eval quality gate is not passed.")
        if rules.hard_gates.get("require_candidate_judge_quality_gate", True):
            if not bool(candidate_summary.get("quality_gate_passed", False)):
                hard_gate_failures.append("Candidate judge quality gate is not passed.")
        if pass_case_ratio < float(rules.thresholds.get("min_candidate_pass_case_ratio", 0.55)):
            hard_gate_failures.append("Candidate pass_case_ratio is below the configured minimum.")

        critical_dimension_deltas: list[dict[str, Any]] = []
        soft_regressions: list[str] = []
        severity = 0
        for dimension in rules.critical_dimensions:
            baseline_raw_score = float(baseline_summary.get("dimension_scores", {}).get(dimension, 0) or 0)
            candidate_raw_score = float(candidate_summary.get("dimension_scores", {}).get(dimension, 0) or 0)
            dimension_max = max(candidate_dimension_max.get(dimension, 0.0), baseline_dimension_max.get(dimension, 0.0))
            baseline_score = _score_pct(baseline_raw_score, dimension_max)
            candidate_score = _score_pct(candidate_raw_score, dimension_max)
            delta = round(candidate_score - baseline_score, 2)
            critical_dimension_deltas.append(
                {
                    "dimension": dimension,
                    "baseline_score": baseline_score,
                    "candidate_score": candidate_score,
                    "delta": delta,
                    "baseline_raw_score": baseline_raw_score,
                    "candidate_raw_score": candidate_raw_score,
                    "max_score": dimension_max,
                }
            )
            if candidate_score < float(rules.thresholds.get("min_candidate_critical_dimension_score", 55.0)):
                hard_gate_failures.append(f"Candidate critical dimension `{dimension}` is below minimum score percent.")
            if -delta > float(rules.thresholds.get("max_critical_dimension_drop", 8.0)):
                severity += 1
                soft_regressions.append(f"Critical dimension `{dimension}` dropped by {-delta} percentage points.")
            elif -delta > float(rules.thresholds.get("warn_critical_dimension_drop", 4.0)):
                severity += 0.5
                soft_regressions.append(
                    f"Critical dimension `{dimension}` has a noticeable drop of {-delta} percentage points."
                )

        if judge_score_drop > float(rules.thresholds.get("max_judge_score_drop", 5.0)):
            severity += 1
            soft_regressions.append(f"Judge overall score dropped by {judge_score_drop}.")
        elif judge_score_drop > float(rules.thresholds.get("warn_judge_score_drop", 2.5)):
            severity += 0.5
            soft_regressions.append(f"Judge overall score has a mild drop of {judge_score_drop}.")

        if eval_score_drop > float(rules.thresholds.get("max_eval_score_drop", 5.0)):
            severity += 1
            soft_regressions.append(f"Offline rule-eval score dropped by {eval_score_drop}.")
        elif eval_score_drop > float(rules.thresholds.get("warn_eval_score_drop", 2.5)):
            severity += 0.5
            soft_regressions.append(f"Offline rule-eval score has a mild drop of {eval_score_drop}.")

        if fail_case_increase > float(rules.thresholds.get("max_fail_case_increase", 1)):
            severity += 1
            soft_regressions.append(f"Fail case count increased by {fail_case_increase}.")
        elif fail_case_increase > float(rules.thresholds.get("warn_fail_case_increase", 0)):
            severity += 0.5
            soft_regressions.append(f"Fail case count increased by {fail_case_increase}.")

        case_regressions: list[dict[str, Any]] = []
        for row in compare_report.get("case_deltas", []):
            baseline_case_score = float(row.get("score_a", 0) or 0)
            candidate_case_score = float(row.get("score_b", 0) or 0)
            candidate_delta = round(candidate_case_score - baseline_case_score, 2)
            if candidate_delta < 0 and abs(candidate_delta) > float(rules.thresholds.get("warn_case_score_drop", 6.0)):
                case_regressions.append(
                    {
                        **row,
                        "candidate_delta": candidate_delta,
                    }
                )
        for row in case_regressions:
            delta = abs(float(row.get("candidate_delta", 0) or 0))
            if delta > float(rules.thresholds.get("max_case_score_drop", 12.0)):
                severity += 1
                soft_regressions.append(f"Case `{row.get('case_id', '')}` dropped by {delta}.")
            else:
                severity += 0.5
                soft_regressions.append(f"Case `{row.get('case_id', '')}` has a mild drop of {delta}.")

        if hard_gate_failures:
            status = "fail"
        elif severity >= float(rules.thresholds.get("severity_fail", 3)):
            status = "fail"
        elif severity >= float(rules.thresholds.get("severity_warn", 1)):
            status = "warn"
        else:
            status = "pass"

        report = {
            "regression_version": "style-regression-v1",
            "generated_at": utc_timestamp(),
            "node_id": _clean_text(baseline_report.get("node_id")) or _clean_text(candidate_report.get("node_id")),
            "baseline": {
                "style_id": _clean_text(baseline_report.get("style_id")),
                "run_id": _clean_text(baseline_report.get("run_id")),
                "judge_summary": baseline_summary,
                "rule_evaluation_summary": baseline_eval,
            },
            "candidate": {
                "style_id": _clean_text(candidate_report.get("style_id")),
                "run_id": _clean_text(candidate_report.get("run_id")),
                "judge_summary": candidate_summary,
                "rule_evaluation_summary": candidate_eval,
            },
            "summary": {
                "status": status,
                "allow_downstream_export": status == "pass",
                "judge_score_drop": judge_score_drop,
                "eval_score_drop": eval_score_drop,
                "fail_case_increase": fail_case_increase,
                "candidate_pass_case_ratio": pass_case_ratio,
                "critical_dimension_score_unit": "percent_of_dimension_max",
                "severity": severity,
            },
            "hard_gate_failures": hard_gate_failures,
            "soft_regressions": soft_regressions,
            "critical_dimension_deltas": critical_dimension_deltas,
            "compare_summary": compare_report.get("summary", {}),
            "source_files": {
                "baseline_judge_dir": str(baseline_root),
                "candidate_judge_dir": str(candidate_root),
                "baseline_judge_report_file": str(baseline_root / JUDGE_REPORT_JSON_FILE),
                "candidate_judge_report_file": str(candidate_root / JUDGE_REPORT_JSON_FILE),
                "threshold_config_file": str(rules.rules_path),
            },
        }

        write_json(report_path, report)
        write_jsonl(rows_path, compare_rows)
        write_markdown(markdown_path, _build_markdown_report(report))
        tracker.record_success(
            "regress-style-quality",
            "Style regression report written.",
            status=status,
            severity=severity,
        )
        tracker.finish(
            "Style quality regression completed.",
            report_file=str(report_path),
            rows_file=str(rows_path),
        )
        return RegressionResult(
            report_path=report_path,
            markdown_path=markdown_path,
            rows_path=rows_path,
            report=report,
        )
    except Exception as exc:  # noqa: BLE001
        tracker.fail_run(f"Style quality regression aborted: {exc}", error_type=type(exc).__name__)
        raise
