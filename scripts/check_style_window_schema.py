from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient
from novel_pipeline_stable.config import load_stable_project_config
from novel_pipeline_stable.models import STYLE_WINDOW_SIGNAL_SCHEMA_VERSION, StyleWindowSignalResult
from novel_pipeline_stable.prompting import load_prompt


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "formal_cn_gpt54_stable.toml"


def _probe_payload() -> dict[str, Any]:
    return {
        "window_id": "schema_probe_0001_0002",
        "chapter_ids": ["0001", "0002"],
        "chapters": [
            {
                "chapter_id": "0001",
                "title": "第一章 试运行",
                "source_text": "主角刚拿到资源奖励，转头就被流程通知追缴费用。她嘴上很平静，心里却在迅速计算还能撑几天。",
            },
            {
                "chapter_id": "0002",
                "title": "第二章 程序先于情绪",
                "source_text": "导师没有先安慰她，而是先递来一张补录清单。清单上的每一项都像笑话，但谁也没笑。",
            },
        ],
        "scene_locator": [
            {
                "source_ref": "scene:0001_001",
                "chapter_id": "0001",
                "scene_id": "0001_001",
                "start_anchor": "主角刚拿到资源奖励",
                "end_anchor": "还能撑几天",
            },
            {
                "source_ref": "scene:0002_001",
                "chapter_id": "0002",
                "scene_id": "0002_001",
                "start_anchor": "导师没有先安慰她",
                "end_anchor": "谁也没笑",
            },
        ],
    }


def _collect_schema_stats(schema: Any) -> dict[str, Any]:
    stats = {
        "max_depth": 0,
        "object_count": 0,
        "array_count": 0,
        "property_count": 0,
        "definition_count": len(schema.get("$defs", {})) if isinstance(schema, dict) else 0,
        "union_count": 0,
        "missing_additional_properties_false_paths": [],
        "missing_full_required_paths": [],
    }

    def visit(node: Any, *, path: str, depth: int) -> None:
        if not isinstance(node, dict):
            if isinstance(node, list):
                for index, item in enumerate(node):
                    visit(item, path=f"{path}[{index}]", depth=depth)
            return

        node_type = node.get("type")
        if node_type == "object" or "properties" in node:
            stats["object_count"] += 1
            stats["max_depth"] = max(stats["max_depth"], depth)
            if node.get("additionalProperties", True) is not False:
                stats["missing_additional_properties_false_paths"].append(path)
            properties = node.get("properties", {})
            if isinstance(properties, dict):
                stats["property_count"] += len(properties)
                required = node.get("required", [])
                if isinstance(required, list):
                    missing_required = sorted(key for key in properties if key not in set(required))
                else:
                    missing_required = sorted(properties)
                if missing_required:
                    stats["missing_full_required_paths"].append(
                        {"path": path, "missing_keys": missing_required}
                    )
                for key, value in properties.items():
                    visit(value, path=f"{path}.properties.{key}", depth=depth + 1)
        if node_type == "array":
            stats["array_count"] += 1
            stats["max_depth"] = max(stats["max_depth"], depth)
            visit(node.get("items"), path=f"{path}.items", depth=depth + 1)

        for union_key in ("allOf", "anyOf", "oneOf"):
            union_value = node.get(union_key)
            if isinstance(union_value, list):
                stats["union_count"] += 1
                for index, value in enumerate(union_value):
                    visit(value, path=f"{path}.{union_key}[{index}]", depth=depth + 1)

        definitions = node.get("$defs", {})
        if isinstance(definitions, dict):
            for key, value in definitions.items():
                visit(value, path=f"{path}.$defs.{key}", depth=depth + 1)

        for key, value in node.items():
            if key in {"properties", "items", "$defs", "allOf", "anyOf", "oneOf"}:
                continue
            if isinstance(value, dict):
                visit(value, path=f"{path}.{key}", depth=depth + 1)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    visit(item, path=f"{path}.{key}[{index}]", depth=depth + 1)

    visit(schema, path="$", depth=1)
    stats["missing_additional_properties_false_paths"] = stats["missing_additional_properties_false_paths"][:32]
    stats["missing_full_required_paths"] = stats["missing_full_required_paths"][:32]
    return stats


def _default_artifacts_dir() -> Path:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return Path("C:/sbtests") / f"{timestamp}_style_schema_preflight"


def _run_api_probe(config_path: Path, *, artifacts_dir: Path | None, model_name: str | None) -> dict[str, Any]:
    config = load_stable_project_config(config_path)
    if not config.gateways and (not config.api_key or not config.base_url):
        return {
            "ok": False,
            "skipped": True,
            "reason": "missing_openai_compatible_credentials",
        }

    resolved_artifacts_dir = (artifacts_dir or _default_artifacts_dir()).resolve()
    client = StableOpenAICompatibleStructuredClient(config, artifacts_dir=resolved_artifacts_dir)
    response = client.generate_structured(
        request_key="style_schema_preflight",
        model_name=model_name or config.model.style_model,
        response_model=StyleWindowSignalResult,
        system_instruction=load_prompt(config.prompt_dir, "style_extraction.md"),
        user_payload=_probe_payload(),
        temperature=0.0,
        max_output_tokens=min(int(config.model.style_max_output_tokens), 1024),
        response_format_mode="json_schema",
    )
    parsed = response.parsed.model_dump(mode="json")
    return {
        "ok": True,
        "skipped": False,
        "artifacts_dir": str(resolved_artifacts_dir),
        "model_name": response.model_name,
        "schema_version": parsed.get("schema_version", ""),
        "usage_metadata": response.usage_metadata,
        "request_metrics": response.request_metrics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the strict json_schema contract for StyleWindowSignalResult.")
    parser.add_argument("--config", type=Path, default=_default_config_path(), help="Path to the stable project config.")
    parser.add_argument("--model", default="", help="Optional override model for the probe request.")
    parser.add_argument("--skip-api", action="store_true", help="Only run static schema analysis without calling the API.")
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help="Artifacts directory for the API probe. Defaults to C:\\sbtests\\<timestamp>_style_schema_preflight.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path.")
    args = parser.parse_args(argv)

    schema = StyleWindowSignalResult.model_json_schema(by_alias=True)
    report: dict[str, Any] = {
        "schema_version": STYLE_WINDOW_SIGNAL_SCHEMA_VERSION,
        "config_path": str(args.config.resolve()),
        "schema_stats": _collect_schema_stats(schema),
        "schema": schema,
    }

    static_exit_code = 0
    schema_stats = report["schema_stats"]
    if isinstance(schema_stats, dict):
        if schema_stats.get("missing_additional_properties_false_paths"):
            static_exit_code = 1
        if schema_stats.get("missing_full_required_paths"):
            static_exit_code = 1

    api_exit_code = 0
    if not args.skip_api:
        try:
            report["api_probe"] = _run_api_probe(
                args.config.resolve(),
                artifacts_dir=args.artifacts_dir,
                model_name=args.model or None,
            )
        except Exception as exc:  # noqa: BLE001
            report["api_probe"] = {
                "ok": False,
                "skipped": False,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
            api_exit_code = 1

    payload_text = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload_text)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload_text + "\n", encoding="utf-8")

    api_probe = report.get("api_probe", {})
    if isinstance(api_probe, dict) and api_probe.get("skipped"):
        return static_exit_code
    return max(static_exit_code, api_exit_code)


if __name__ == "__main__":
    sys.exit(main())
