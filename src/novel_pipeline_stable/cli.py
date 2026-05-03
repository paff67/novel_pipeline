from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from novel_pipeline_stable.canon_builder import build_canon
from novel_pipeline_stable.chapter_anomalies import scan_chapter_anomalies
from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient
from novel_pipeline_stable.config import load_project_config, load_stable_project_config
from novel_pipeline_stable.io_utils import clear_matching, copy_tree_files, ensure_dir, read_json, write_json
from novel_pipeline_stable.monitor_server import serve_monitor
from novel_pipeline_stable.monitoring import RunTracker
from novel_pipeline_stable.pipelines import extract_facts, extract_style
from novel_pipeline_stable.review_panel import build_review_panel
from novel_pipeline_stable.splitter import run_scene_split
from novel_pipeline_stable.story_nodes import detect_story_nodes, load_confirmed_story_node
from novel_pipeline_stable.style_bible_builder import run_style_bible_build
from novel_pipeline_stable.hybrid_rag_contract import build_hybrid_rag_contract
from novel_pipeline_stable.hybrid_retriever import run_hybrid_retrieval_probe
from novel_pipeline_stable.style_bible_bucket_builder import build_style_bible_bucket_memos
from novel_pipeline_stable.style_bible_batching import build_style_bible_batch_plan
from novel_pipeline_stable.style_bible_compare import run_style_run_comparison
from novel_pipeline_stable.style_bible_reduction import load_style_bible_bucket_memos, reduce_style_bible_from_bucket_memos
from novel_pipeline_stable.style_bible_router import build_style_bible_routed_index
from novel_pipeline_stable.style_bible_evaluator import run_style_bible_evaluation
from novel_pipeline_stable.style_bible_judge import run_style_bible_judge
from novel_pipeline_stable.style_bible_regression import run_style_quality_regression
from novel_pipeline_stable.text_normalization import scan_suspicious_tokens
from novel_pipeline_stable.world_graph_builder import build_world_graph
from novel_pipeline_stable.world_graph_graphrag_export import export_world_graph_graphrag


def _copy_reduce_support_artifacts(*, source_bundle_path: str | Path, bucket_memo_dir: str | Path, output_dir: str | Path) -> None:
    candidate_roots = [
        Path(source_bundle_path).resolve().parent,
        Path(bucket_memo_dir).resolve().parent,
    ]
    output_root = ensure_dir(output_dir).resolve()
    support_files = (
        "style_bible_coverage_report.json",
        "style_bible_routed_index.json",
        "batch_plan.json",
        "sampling_report.json",
        "planner_debug_report.json",
    )
    for root in candidate_roots:
        for filename in support_files:
            source_path = root / filename
            target_path = output_root / filename
            if not source_path.exists():
                continue
            if source_path.resolve() == target_path.resolve():
                continue
            shutil.copy2(source_path, target_path)


def _default_data_root() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def _default_style_bible_rules_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "style_bible_eval_rules.toml"


def _default_style_gold_set_index_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "eval" / "style_gold_set" / "v2" / "index.json"


def _default_style_bible_judge_rules_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "style_bible_judge_rules.toml"


def _default_style_bible_regression_rules_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "style_bible_regression_rules.toml"


def _default_style_bible_batching_rules_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "style_bible_batching_rules.toml"


def _default_style_bible_router_rules_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "style_bible_router_rules.toml"


def stage_chapters(input_dir: str | Path, output_dir: str | Path, clear: bool) -> None:
    output_path = ensure_dir(output_dir)
    if clear:
        clear_matching(output_path, "chapter_*.txt")
        clear_matching(output_path, "chapters_manifest.txt")
    count = copy_tree_files(input_dir, output_path, "chapter_*.txt")
    print(f"Staged {count} chapter files into {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone stable novel pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser("stage-chapters", help="Copy cleaned chapter files into the stable project")
    stage.add_argument("--input-dir", required=True)
    stage.add_argument("--output-dir", required=True)
    stage.add_argument("--clear", action="store_true")

    split = subparsers.add_parser("split-scenes", help="Split chapters into scene JSON files")
    split.add_argument("--config", required=True)
    split.add_argument("--input-dir", required=True)
    split.add_argument("--output-dir", required=True)
    split.add_argument("--clear", action="store_true")

    probe = subparsers.add_parser("probe-gateway", help="Run connectivity and structured-response probes")
    probe.add_argument("--config", required=True)
    probe.add_argument("--output-dir", required=True)
    probe.add_argument("--model")

    facts = subparsers.add_parser("extract-facts", help="Extract structured facts with the stable client")
    facts.add_argument("--config", required=True)
    facts.add_argument("--input-dir", required=True)
    facts.add_argument("--output-dir", required=True)
    facts.add_argument("--limit", type=int)
    facts.add_argument("--start-at", type=int, default=0)
    facts.add_argument("--resume", action="store_true")

    style = subparsers.add_parser("extract-style", help="Extract style analysis with the stable client")
    style.add_argument("--config", required=True)
    style.add_argument("--input-dir", required=True)
    style.add_argument("--output-dir", required=True)
    style.add_argument("--limit", type=int)
    style.add_argument("--start-at", type=int, default=0)
    style.add_argument("--resume", action="store_true")

    canon = subparsers.add_parser("build-canon", help="Build canon assets from extraction outputs")
    canon.add_argument("--facts-dir", required=True)
    canon.add_argument("--style-dir", required=True)
    canon.add_argument("--output-dir", required=True)
    canon.add_argument("--story-nodes")
    canon.add_argument("--node-id")

    world_graph = subparsers.add_parser("build-world-graph", help="Build offline world graph assets from canon outputs")
    world_graph.add_argument("--canon-dir", required=True)
    world_graph.add_argument("--output-dir", required=True)

    world_graph_graphrag = subparsers.add_parser(
        "export-world-graph-graphrag",
        help="Export GraphRAG BYOG-ready JSONL tables from an offline world graph directory",
    )
    world_graph_graphrag.add_argument("--world-graph-dir", required=True)
    world_graph_graphrag.add_argument("--output-dir", required=True)

    hybrid_contract = subparsers.add_parser(
        "build-hybrid-rag-contract",
        help="Build the offline retrieval contract that defines Style lane + World lane responsibilities",
    )
    hybrid_contract.add_argument("--style-bible-dir", required=True)
    hybrid_contract.add_argument("--world-graph-dir", required=True)
    hybrid_contract.add_argument("--output-dir", required=True)

    hybrid_probe = subparsers.add_parser(
        "probe-hybrid-retriever",
        help="Probe the offline HybridRetriever against Style Bible and World Graph assets",
    )
    hybrid_probe.add_argument("--style-bible-dir", required=True)
    hybrid_probe.add_argument("--world-graph-dir", required=True)
    hybrid_probe.add_argument("--output-dir", required=True)
    hybrid_probe.add_argument("--query", required=True)
    hybrid_probe.add_argument("--config")
    hybrid_probe.add_argument("--route", choices=("style", "world", "hybrid"))
    hybrid_probe.add_argument("--top-k", type=int, default=8)

    story_nodes = subparsers.add_parser("detect-story-nodes", help="Detect candidate story nodes and emit a confirmation template")
    story_nodes.add_argument("--chapters-dir", required=True)
    story_nodes.add_argument("--output-dir", required=True)
    story_nodes.add_argument("--facts-dir")

    review = subparsers.add_parser("build-review-panel", help="Build a static HTML review panel")
    review.add_argument("--facts-dir", required=True)
    review.add_argument("--style-dir", required=True)
    review.add_argument("--output-dir", required=True)

    style_bible = subparsers.add_parser("build-style-bible", help="Build the v2 routed style bible from facts/style/canon")
    style_bible.add_argument("--config", required=True)
    style_bible.add_argument("--facts-dir", required=True)
    style_bible.add_argument("--style-dir", required=True)
    style_bible.add_argument("--canon-dir", required=True)
    style_bible.add_argument("--output-dir", required=True)
    style_bible.add_argument("--scope-label")
    style_bible.add_argument("--max-style-windows", type=int, default=0)
    style_bible.add_argument("--max-scene-samples", type=int, default=0)
    style_bible.add_argument("--max-plot-nodes", type=int, default=0)
    style_bible.add_argument("--max-chapter-summaries", type=int, default=0)
    style_bible.add_argument("--max-entity-samples", type=int, default=0)
    style_bible.add_argument("--routing-rules-config", default=str(_default_style_bible_router_rules_path()))
    style_bible.add_argument("--batching-rules-config", default=str(_default_style_bible_batching_rules_path()))
    style_bible.add_argument("--bucket-build-concurrency", type=int, default=6)
    style_bible.add_argument("--resume", action="store_true")

    routed_index = subparsers.add_parser("route-style-bible-inputs", help="Route full style bible inputs into v2 signal-fusion buckets")
    routed_index.add_argument("--facts-dir", required=True)
    routed_index.add_argument("--style-dir", required=True)
    routed_index.add_argument("--canon-dir", required=True)
    routed_index.add_argument("--output-dir", required=True)
    routed_index.add_argument("--scope-label")
    routed_index.add_argument("--rules-config", default=str(_default_style_bible_router_rules_path()))

    batch_plan = subparsers.add_parser("plan-style-bible-batches", help="Plan v2 routed style bible batches")
    batch_plan.add_argument("--routed-index", required=True)
    batch_plan.add_argument("--output-dir", required=True)
    batch_plan.add_argument("--rules-config", default=str(_default_style_bible_batching_rules_path()))

    bucket_memos = subparsers.add_parser("build-style-bible-bucket-memos", help="Synthesize v2 bucket memos from a routed index + batch plan")
    bucket_memos.add_argument("--config", required=True)
    bucket_memos.add_argument("--facts-dir", required=True)
    bucket_memos.add_argument("--style-dir", required=True)
    bucket_memos.add_argument("--canon-dir", required=True)
    bucket_memos.add_argument("--routed-index", required=True)
    bucket_memos.add_argument("--batch-plan", required=True)
    bucket_memos.add_argument("--output-dir", required=True)
    bucket_memos.add_argument("--bucket-id", action="append", dest="bucket_ids")
    bucket_memos.add_argument("--max-concurrency", type=int, default=6)
    bucket_memos.add_argument("--resume", action="store_true")

    reduce_style_bible = subparsers.add_parser("reduce-style-bible", help="Assemble v2 bucket memos into the final style bible")
    reduce_style_bible.add_argument("--config", required=True)
    reduce_style_bible.add_argument("--source-bundle", required=True)
    reduce_style_bible.add_argument("--bucket-memo-dir", required=True)
    reduce_style_bible.add_argument("--output-dir", required=True)
    reduce_style_bible.add_argument(
        "--resume-local-reduce",
        action="store_true",
        help="Load existing _local_reduce artifacts from --output-dir and continue from global merge/densify/final write.",
    )

    style_bible_eval = subparsers.add_parser("evaluate-style-bible", help="Evaluate a synthesized style bible with the semantic main gate")
    style_bible_eval.add_argument("--config")
    style_bible_eval.add_argument("--input-dir", required=True)
    style_bible_eval.add_argument("--output-dir", required=True)
    style_bible_eval.add_argument("--rules-config", default=str(_default_style_bible_rules_path()))
    style_bible_eval.add_argument("--semantic-judge-model")
    style_bible_eval.add_argument("--resume", action="store_true")

    style_bible_judge = subparsers.add_parser("judge-style-bible", help="Judge a synthesized style bible against gold-set cases")
    style_bible_judge.add_argument("--input-dir", required=True)
    style_bible_judge.add_argument("--output-dir", required=True)
    style_bible_judge.add_argument("--gold-set-index", default=str(_default_style_gold_set_index_path()))
    style_bible_judge.add_argument("--judge-config", default=str(_default_style_bible_judge_rules_path()))
    style_bible_judge.add_argument("--node-id")
    style_bible_judge.add_argument("--evaluation-dir")
    style_bible_judge.add_argument("--resume", action="store_true")

    style_compare = subparsers.add_parser("compare-style-runs", help="Compare two judged style bible runs")
    style_compare.add_argument("--judge-a-dir", required=True)
    style_compare.add_argument("--judge-b-dir", required=True)
    style_compare.add_argument("--output-dir", required=True)
    style_compare.add_argument("--min-delta", type=float, default=1.0)
    style_compare.add_argument("--resume", action="store_true")

    style_regress = subparsers.add_parser("regress-style-quality", help="Check whether a judged candidate regresses against a baseline")
    style_regress.add_argument("--baseline-judge-dir", required=True)
    style_regress.add_argument("--candidate-judge-dir", required=True)
    style_regress.add_argument("--output-dir", required=True)
    style_regress.add_argument("--threshold-config", default=str(_default_style_bible_regression_rules_path()))
    style_regress.add_argument("--resume", action="store_true")

    scan = subparsers.add_parser("scan-suspicious-tokens", help="Scan text for suspicious Latin-letter tokens")
    scan.add_argument("--input-dir", required=True)
    scan.add_argument("--output-file", required=True)
    scan.add_argument("--top", type=int, default=100)
    scan.add_argument("--sample-limit", type=int, default=5)

    anomaly = subparsers.add_parser("scan-chapter-anomalies", help="Scan cleaned chapters for structural anomalies")
    anomaly.add_argument("--input-dir", required=True)
    anomaly.add_argument("--output-file", required=True)
    anomaly.add_argument("--short-body-threshold", type=int, default=120)

    monitor = subparsers.add_parser("serve-monitor", help="Serve the shared monitoring dashboard")
    monitor.add_argument("--data-root", default=str(_default_data_root()))
    monitor.add_argument("--host", default="127.0.0.1")
    monitor.add_argument("--port", type=int, default=8765)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "stage-chapters":
        stage_chapters(args.input_dir, args.output_dir, args.clear)
        return

    if args.command == "split-scenes":
        config = load_project_config(args.config)
        run_scene_split(config, args.input_dir, args.output_dir, clear=args.clear)
        print(f"Scene files written to {Path(args.output_dir).resolve()}")
        return

    if args.command == "probe-gateway":
        config = load_stable_project_config(args.config)
        output_path = ensure_dir(args.output_dir)
        client = StableOpenAICompatibleStructuredClient(config, artifacts_dir=output_path)
        result = client.run_probe(model_name=args.model or config.model.fact_model)
        write_json(output_path / "probe.json", result)
        print(f"Probe written to {(output_path / 'probe.json').resolve()}")
        return

    if args.command == "extract-facts":
        config = load_stable_project_config(args.config)
        extract_facts(
            config,
            args.input_dir,
            args.output_dir,
            limit=args.limit,
            start_at=args.start_at,
            resume=args.resume,
        )
        print(f"Stable fact extraction written to {Path(args.output_dir).resolve()}")
        return

    if args.command == "extract-style":
        config = load_stable_project_config(args.config)
        extract_style(
            config,
            args.input_dir,
            args.output_dir,
            limit=args.limit,
            start_at=args.start_at,
            resume=args.resume,
        )
        print(f"Stable style extraction written to {Path(args.output_dir).resolve()}")
        return

    if args.command == "build-canon":
        if bool(args.story_nodes) != bool(args.node_id):
            raise ValueError("--story-nodes and --node-id must be provided together for node-scoped canon builds.")
        output_path = ensure_dir(args.output_dir)
        story_node = load_confirmed_story_node(args.story_nodes, args.node_id) if args.story_nodes and args.node_id else None
        tracker = RunTracker(
            stage="stable-build-canon",
            output_dir=output_path,
            total_items=1,
            item_label="build",
            source_dir=args.facts_dir,
            metadata={
                "style_dir": str(Path(args.style_dir).resolve()),
                "story_nodes": str(Path(args.story_nodes).resolve()) if args.story_nodes else "",
                "node_id": args.node_id or "",
            },
        )
        try:
            index = build_canon(args.facts_dir, args.style_dir, args.output_dir, story_node=story_node)
            tracker.record_success(
                "stable-build-canon",
                "Canon assets written.",
                entities=index.entity_count,
                facts=index.fact_count,
                events=index.event_count,
                plot_nodes=index.plot_node_count,
            )
            tracker.finish(
                "Stable canon build completed.",
                output_dir=str(Path(args.output_dir).resolve()),
                node_id=story_node.get("node_id", "") if story_node else "",
            )
        except Exception as exc:  # noqa: BLE001
            tracker.fail_run(f"Stable canon build aborted: {exc}", error_type=type(exc).__name__)
            raise
        print(
            "Canon build complete: "
            f"entities={index.entity_count}, facts={index.fact_count}, events={index.event_count}, "
            f"chapter_summaries={index.chapter_summary_count}, style_windows={index.style_window_count}, "
            f"plot_nodes={index.plot_node_count}"
        )
        if story_node:
            print(
                "Story node scope: "
                f"{story_node.get('node_id', '')} "
                f"({story_node.get('start_chapter', '')}-{story_node.get('end_chapter', '')})"
            )
        print(f"Output directory: {Path(args.output_dir).resolve()}")
        return

    if args.command == "build-world-graph":
        output_path = ensure_dir(args.output_dir)
        tracker = RunTracker(
            stage="stable-build-world-graph",
            output_dir=output_path,
            total_items=1,
            item_label="build",
            source_dir=args.canon_dir,
        )
        try:
            result = build_world_graph(args.canon_dir, args.output_dir)
            tracker.record_success(
                "stable-build-world-graph",
                "World graph assets written.",
                nodes=result.node_count,
                edges=result.edge_count,
                communities=result.community_count,
            )
            tracker.finish(
                "Stable world graph build completed.",
                output_dir=str(Path(args.output_dir).resolve()),
                nodes=result.node_count,
                edges=result.edge_count,
                communities=result.community_count,
            )
        except Exception as exc:  # noqa: BLE001
            tracker.fail_run(f"Stable world graph build aborted: {exc}", error_type=type(exc).__name__)
            raise
        print(
            "World graph build complete: "
            f"nodes={result.node_count}, edges={result.edge_count}, communities={result.community_count}"
        )
        print(f"Manifest written to {result.manifest_path}")
        return

    if args.command == "export-world-graph-graphrag":
        output_path = ensure_dir(args.output_dir)
        tracker = RunTracker(
            stage="stable-export-world-graph-graphrag",
            output_dir=output_path,
            total_items=1,
            item_label="export",
            source_dir=args.world_graph_dir,
        )
        try:
            result = export_world_graph_graphrag(args.world_graph_dir, args.output_dir)
            tracker.record_success(
                "stable-export-world-graph-graphrag",
                "GraphRAG BYOG bundle written.",
                entities=result.entity_count,
                relationships=result.relationship_count,
                text_units=result.text_unit_count,
                community_reports=result.community_report_count,
            )
            tracker.finish(
                "GraphRAG BYOG export completed.",
                output_dir=str(Path(args.output_dir).resolve()),
                entities=result.entity_count,
                relationships=result.relationship_count,
                text_units=result.text_unit_count,
                community_reports=result.community_report_count,
            )
        except Exception as exc:  # noqa: BLE001
            tracker.fail_run(f"GraphRAG BYOG export aborted: {exc}", error_type=type(exc).__name__)
            raise
        print(
            "GraphRAG BYOG export complete: "
            f"entities={result.entity_count}, relationships={result.relationship_count}, "
            f"text_units={result.text_unit_count}, community_reports={result.community_report_count}"
        )
        print(f"Manifest written to {result.manifest_path}")
        return

    if args.command == "build-hybrid-rag-contract":
        output_path = ensure_dir(args.output_dir)
        tracker = RunTracker(
            stage="stable-build-hybrid-rag-contract",
            output_dir=output_path,
            total_items=1,
            item_label="contract",
            source_dir=args.style_bible_dir,
            metadata={"world_graph_dir": str(Path(args.world_graph_dir).resolve())},
        )
        try:
            result = build_hybrid_rag_contract(args.style_bible_dir, args.world_graph_dir, args.output_dir)
            tracker.record_success(
                "stable-build-hybrid-rag-contract",
                "Hybrid RAG contract written.",
                contract_path=str(result.contract_path),
            )
            tracker.finish(
                "Hybrid RAG contract completed.",
                output_dir=str(Path(args.output_dir).resolve()),
                contract_path=str(result.contract_path),
            )
        except Exception as exc:  # noqa: BLE001
            tracker.fail_run(f"Hybrid RAG contract aborted: {exc}", error_type=type(exc).__name__)
            raise
        print(f"Hybrid RAG contract written to {result.contract_path}")
        print(f"Markdown report written to {result.markdown_path}")
        return

    if args.command == "probe-hybrid-retriever":
        output_path = ensure_dir(args.output_dir)
        tracker = RunTracker(
            stage="stable-probe-hybrid-retriever",
            output_dir=output_path,
            total_items=1,
            item_label="probe",
            source_dir=args.style_bible_dir,
            metadata={"world_graph_dir": str(Path(args.world_graph_dir).resolve())},
        )
        config = load_stable_project_config(args.config) if args.config else None
        try:
            result = run_hybrid_retrieval_probe(
                query=args.query,
                style_bible_dir=args.style_bible_dir,
                world_graph_dir=args.world_graph_dir,
                output_dir=args.output_dir,
                config=config,
                route_override=args.route or "",
                top_k=args.top_k,
            )
            tracker.record_success(
                "stable-probe-hybrid-retriever",
                "Hybrid retriever probe written.",
                route_decision=result.report.get("route_decision", ""),
                hit_count=len(result.report.get("merged_hits", [])),
            )
            tracker.finish(
                "Hybrid retriever probe completed.",
                output_dir=str(Path(args.output_dir).resolve()),
                route_decision=result.report.get("route_decision", ""),
                hit_count=len(result.report.get("merged_hits", [])),
            )
        except Exception as exc:  # noqa: BLE001
            tracker.fail_run(f"Hybrid retriever probe aborted: {exc}", error_type=type(exc).__name__)
            raise
        print(f"Hybrid retriever probe written to {result.report_path}")
        print(f"Markdown report written to {result.markdown_path}")
        print(f"Route decision: {result.report.get('route_decision', '')}")
        return

    if args.command == "detect-story-nodes":
        output_path = ensure_dir(args.output_dir)
        tracker = RunTracker(
            stage="stable-detect-story-nodes",
            output_dir=output_path,
            total_items=1,
            item_label="analysis",
            source_dir=args.chapters_dir,
            metadata={"facts_dir": str(Path(args.facts_dir).resolve()) if args.facts_dir else ""},
        )
        try:
            result = detect_story_nodes(args.chapters_dir, args.facts_dir, args.output_dir)
            tracker.record_success(
                "stable-detect-story-nodes",
                "Story node candidate files written.",
                candidate_count=len(result.get("candidate_nodes", [])),
            )
            tracker.finish(
                "Story node detection completed.",
                candidate_count=len(result.get("candidate_nodes", [])),
            )
        except Exception as exc:  # noqa: BLE001
            tracker.fail_run(f"Story node detection aborted: {exc}", error_type=type(exc).__name__)
            raise
        print(f"Story node candidates written to {result.get('paths', {}).get('candidates_json', '')}")
        print(f"Markdown report written to {result.get('paths', {}).get('candidates_markdown', '')}")
        print(f"Confirmation template written to {result.get('paths', {}).get('confirmed_template_json', '')}")
        print(f"Candidate count: {len(result.get('candidate_nodes', []))}")
        return

    if args.command == "build-review-panel":
        output_path = ensure_dir(args.output_dir)
        tracker = RunTracker(
            stage="stable-build-review-panel",
            output_dir=output_path,
            total_items=1,
            item_label="build",
            source_dir=args.facts_dir,
            metadata={"style_dir": str(Path(args.style_dir).resolve())},
        )
        try:
            html_path = build_review_panel(args.facts_dir, args.style_dir, args.output_dir)
            tracker.record_success("stable-build-review-panel", "Review panel written.", html_path=str(html_path))
            tracker.finish("Stable review panel build completed.", html_path=str(html_path))
        except Exception as exc:  # noqa: BLE001
            tracker.fail_run(f"Stable review panel build aborted: {exc}", error_type=type(exc).__name__)
            raise
        print(f"Review panel written to {html_path}")
        return

    if args.command == "build-style-bible":
        config = load_stable_project_config(args.config)
        result = run_style_bible_build(
            config,
            args.facts_dir,
            args.style_dir,
            args.canon_dir,
            args.output_dir,
            scope_label=args.scope_label,
            max_style_windows=args.max_style_windows,
            max_scene_samples=args.max_scene_samples,
            max_plot_nodes=args.max_plot_nodes,
            max_chapter_summaries=args.max_chapter_summaries,
            max_entity_samples=args.max_entity_samples,
            routing_rules_config=args.routing_rules_config,
            batching_rules_config=args.batching_rules_config,
            bucket_build_concurrency=args.bucket_build_concurrency,
            resume=args.resume,
        )
        if result is None:
            print(f"Style bible output already exists in {Path(args.output_dir).resolve()}")
        else:
            print(f"Style bible written to {result.output_path}")
            if result.reasoning_path is not None:
                print(f"Reasoning written to {result.reasoning_path}")
            if result.export_flat_path is not None:
                print(f"Flat export written to {result.export_flat_path}")
            print(f"Source bundle written to {result.source_bundle_path}")
            print(f"Routed index written to {result.routed_index_path}")
            print(f"Batch plan written to {result.batch_plan_path}")
            print(f"Sampling report written to {result.sampling_report_path}")
            if result.bucket_memo_dir_path is not None:
                print(f"Bucket memos written to {result.bucket_memo_dir_path}")
            if result.reduce_trace_path is not None:
                print(f"Reduce trace written to {result.reduce_trace_path}")
            print(f"Style ID: {result.record.get('style_id', '')}")
        return

    if args.command == "route-style-bible-inputs":
        routed_index = build_style_bible_routed_index(
            args.facts_dir,
            args.style_dir,
            args.canon_dir,
            args.output_dir,
            scope_label=args.scope_label,
            rules_config=args.rules_config,
        )
        output_path = Path(args.output_dir).resolve() / "style_bible_routed_index.json"
        print(f"Routed index written to {output_path}")
        print(f"Routing mode: {routed_index.routing_mode}")
        if routed_index.rules_config:
            print(f"Rules config: {routed_index.rules_config}")
        print(f"Item count: {len(routed_index.items)}")
        return

    if args.command == "plan-style-bible-batches":
        batch_plan = build_style_bible_batch_plan(
            args.routed_index,
            args.output_dir,
            rules_config=args.rules_config,
        )
        output_path = Path(args.output_dir).resolve() / "batch_plan.json"
        debug_report_path = Path(args.output_dir).resolve() / "planner_debug_report.json"
        print(f"Batch plan written to {output_path}")
        print(f"Planner debug report written to {debug_report_path}")
        print(f"Batch count: {len(batch_plan.batches)}")
        print(f"Unbatched items: {len(batch_plan.unbatched_item_ids)}")
        return

    if args.command == "build-style-bible-bucket-memos":
        config = load_stable_project_config(args.config)
        routed_index = read_json(args.routed_index)
        batch_plan = read_json(args.batch_plan)
        result = build_style_bible_bucket_memos(
            config,
            args.facts_dir,
            args.style_dir,
            args.canon_dir,
            routed_index,
            batch_plan,
            args.output_dir,
            include_bucket_ids=args.bucket_ids or [],
            max_concurrency=args.max_concurrency,
            resume=args.resume,
        )
        print(f"Bucket memo directory: {result.memo_dir}")
        print(f"Prompt bundle directory: {result.prompt_bundle_dir}")
        print(f"Bucket memo count: {len(result.bucket_memos)}")
        print(f"Batch memo count: {len(result.batch_memos)}")
        print(f"Memoed refs: {len(result.memoed_refs)}")
        return

    if args.command == "reduce-style-bible":
        config = load_stable_project_config(args.config)
        source_bundle = read_json(args.source_bundle)
        bucket_memos = load_style_bible_bucket_memos(args.bucket_memo_dir)
        result = reduce_style_bible_from_bucket_memos(
            config,
            source_bundle,
            bucket_memos,
            args.output_dir,
            resume_local_reduce=args.resume_local_reduce,
        )
        _copy_reduce_support_artifacts(
            source_bundle_path=args.source_bundle,
            bucket_memo_dir=args.bucket_memo_dir,
            output_dir=args.output_dir,
        )
        print(f"Style bible written to {result.output_path}")
        print(f"Reasoning bundle written to {result.reasoning_path}")
        print(f"Flat export written to {result.export_flat_path}")
        print(f"Reduce trace written to {result.reduce_trace_path}")
        print(f"Reduced refs: {len(result.reduced_refs)}")
        return

    if args.command == "evaluate-style-bible":
        semantic_judge_model = str(args.semantic_judge_model or "").strip()
        if not semantic_judge_model and args.config:
            semantic_judge_model = load_stable_project_config(args.config).model.resolved_semantic_judge_model
        result = run_style_bible_evaluation(
            args.input_dir,
            args.output_dir,
            rules_config=args.rules_config,
            resume=args.resume,
            semantic_judge_model=semantic_judge_model,
        )
        if result is None:
            print(f"Style bible evaluation output already exists in {Path(args.output_dir).resolve()}")
        else:
            print(f"Style bible evaluation written to {result.report_path}")
            print(f"Markdown report written to {result.markdown_path}")
            print(f"Evaluation status: {result.report.get('summary', {}).get('status', '')}")
            print(f"Evaluation score: {result.report.get('summary', {}).get('overall_score', 0)}")
            print(f"Semantic judge model: {result.report.get('semantic_judge_model', '')}")
            if result.report.get("requested_semantic_judge_model"):
                print(f"Requested semantic judge model: {result.report.get('requested_semantic_judge_model', '')}")
        return

    if args.command == "judge-style-bible":
        result = run_style_bible_judge(
            args.input_dir,
            args.output_dir,
            gold_set_index=args.gold_set_index,
            judge_rules_config=args.judge_config,
            node_id=args.node_id or "",
            evaluation_dir=args.evaluation_dir,
            resume=args.resume,
        )
        if result is None:
            print(f"Style bible judge output already exists in {Path(args.output_dir).resolve()}")
        else:
            print(f"Judge report written to {result.report_path}")
            print(f"Judge markdown written to {result.markdown_path}")
            print(f"Judge rows written to {result.rows_path}")
            print(f"Judge status: {result.report.get('summary', {}).get('status', '')}")
            print(f"Judge score: {result.report.get('summary', {}).get('overall_score', 0)}")
        return

    if args.command == "compare-style-runs":
        result = run_style_run_comparison(
            args.judge_a_dir,
            args.judge_b_dir,
            args.output_dir,
            min_delta=args.min_delta,
            resume=args.resume,
        )
        if result is None:
            print(f"Style compare output already exists in {Path(args.output_dir).resolve()}")
        else:
            print(f"Compare report written to {result.report_path}")
            print(f"Compare markdown written to {result.markdown_path}")
            print(f"Compare rows written to {result.rows_path}")
            print(f"Winner: {result.report.get('summary', {}).get('winner', '')}")
        return

    if args.command == "regress-style-quality":
        result = run_style_quality_regression(
            args.baseline_judge_dir,
            args.candidate_judge_dir,
            args.output_dir,
            threshold_config=args.threshold_config,
            resume=args.resume,
        )
        if result is None:
            print(f"Style regression output already exists in {Path(args.output_dir).resolve()}")
        else:
            print(f"Regression report written to {result.report_path}")
            print(f"Regression markdown written to {result.markdown_path}")
            print(f"Regression rows written to {result.rows_path}")
            print(f"Regression status: {result.report.get('summary', {}).get('status', '')}")
        return

    if args.command == "scan-suspicious-tokens":
        report = scan_suspicious_tokens(args.input_dir, top=args.top, sample_limit=args.sample_limit)
        write_json(args.output_file, report)
        print(f"Suspicious token report written to {Path(args.output_file).resolve()}")
        return

    if args.command == "scan-chapter-anomalies":
        report = scan_chapter_anomalies(args.input_dir, short_body_threshold=args.short_body_threshold)
        write_json(args.output_file, report)
        print(f"Chapter anomaly report written to {Path(args.output_file).resolve()}")
        return

    if args.command == "serve-monitor":
        serve_monitor(args.data_root, args.host, args.port)
        return

    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
