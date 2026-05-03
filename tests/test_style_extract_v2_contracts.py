from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from novel_pipeline_stable.api_clients import StableOpenAICompatibleStructuredClient
import novel_pipeline_stable.pipelines as pipelines
from novel_pipeline_stable.config import (
    ModelConfig,
    PathConfig,
    ProjectConfig,
    SceneSplitConfig,
    StabilityConfig,
    StyleBibleReduceConfig,
    StyleWindowConfig,
    StableProjectConfig,
)
from novel_pipeline_stable.models import (
    ChapterDocument,
    STYLE_WINDOW_SIGNAL_SCHEMA_VERSION,
    StyleWindowSignalResult,
)
from novel_pipeline_stable.pipelines import _build_style_payload, _style_content_stats
from novel_pipeline_stable.style_bible_builder import (
    _build_style_index_summary,
    _build_style_signal_summary,
    build_style_bible_source_bundle,
)
from novel_pipeline_stable.style_bible_inputs import StyleBibleInputBundle, load_style_bible_inputs
from novel_pipeline_stable.style_bible_router import route_style_bible_inputs
from novel_pipeline_stable.style_window_normalization import normalize_style_window_payload


def _make_config(project_root: Path) -> StableProjectConfig:
    base = ProjectConfig(
        project_root=project_root,
        model=ModelConfig(),
        scene_split=SceneSplitConfig(min_chars=10, target_chars=20, max_chars=60),
        style_windows=StyleWindowConfig(window_size=2, stride=2),
        paths=PathConfig(prompt_dir="prompts"),
        style_bible_reduce=StyleBibleReduceConfig(),
    )
    return StableProjectConfig(
        base=base,
        stability=StabilityConfig(),
        config_path=project_root / "dummy.toml",
    )


def _sample_style_row() -> dict:
    return {
        "schema_version": STYLE_WINDOW_SIGNAL_SCHEMA_VERSION,
        "window_id": "0001_0002",
        "chapter_ids": ["0001", "0002"],
        "source_chapter_titles": ["Chapter 1", "Chapter 2"],
        "scalar_contracts": {
            "perspective": "close_third_person",
            "distance": "close",
            "temporality": "linear_forward",
            "inner_monologue_mode": "embedded",
        },
        "surface_markers": ["dry institutional tone", "cost-before-reward"],
        "narrative_engine_rules": [
            {
                "mechanism_label": "cost_first",
                "execution_logic": "Insert a settlement or fee notice immediately after any gain.",
                "trigger": "when the protagonist receives a resource or breakthrough",
                "constraint": "show the payment logic before celebration",
                "evidence_ids": ["ev1", "ev2"],
            }
        ],
        "pacing_rules": [],
        "plot_node_logic_rules": [],
        "description_rules": [],
        "dialogue_rules": [
            {
                "mechanism_label": "bureaucratic_dialogue",
                "execution_logic": "Use dialogue to deliver process and qualification constraints before comfort.",
                "trigger": "when a notice or review interrupts the scene",
                "constraint": "keep the tone procedural rather than emotional",
                "evidence_ids": ["ev2"],
            }
        ],
        "characterization_rules": [],
        "sensory_rules": [],
        "humor_rules": [
            {
                "mechanism_label": "deadpan_gap",
                "execution_logic": "Let a flat tone describe an absurd punishment or fee chain so the mismatch creates humor.",
                "trigger": "when the system action is already absurd on its own",
                "constraint": "do not rely on exaggerated shouting",
                "evidence_ids": ["ev2"],
            }
        ],
        "satire_rules": [],
        "nonstandard_xianxia_rules": [
            {
                "mechanism_label": "process_cultivation",
                "execution_logic": "Rewrite cultivation gains as filings, ledgers, and settlement steps instead of simple payoff.",
                "trigger": "when cultivation resources or advancement appear",
                "constraint": "anchor the passage in institutional steps and checklists",
                "evidence_ids": ["ev1", "ev2"],
            }
        ],
        "narrator_voice_rules": [
            {
                "mechanism_label": "deadpan_procedural_voice",
                "execution_logic": "Keep the narration in a deadpan procedural report tone even when the outcome is absurd.",
                "trigger": "when the institution speaks through forms, notices, or approvals",
                "constraint": "stay emotionally flat and procedural instead of lyrical",
                "evidence_ids": ["ev2"],
            }
        ],
        "register_mix_rules": [],
        "negative_pitfalls": [
            {
                "forbidden_action": "generalize the institutional conflict into a vague mood statement",
                "correction_guideline": "stay with concrete notices, settlements, filings, and qualification steps",
                "evidence_ids": ["ev2"],
            }
        ],
        "rag_candidates": [
            {
                "axis_id": "",
                "bucket_id": "",
                "query_feature_matcher": "a reward is followed immediately by a settlement or fee notice",
                "route_target_action": "retrieve style priors for cost-triggered payoff reversal",
                "evidence_ids": ["ev1"],
            }
        ],
        "worldbook_candidates": [
            {
                "axis_id": "institutional_absurdity",
                "bucket_id": "institutional_pipeline",
                "query_feature_matcher": "checklists, notices, qualification review, and filing steps drive the scene",
                "route_target_action": "bind an institutional pipeline worldbook entry",
                "evidence_ids": ["ev2"],
            }
        ],
        "routing_hints": [
            {
                "axis_id": "institutional_absurdity",
                "bucket_id": "institutional_pipeline",
                "query_feature_matcher": "process artifacts advance the conflict before character emotion does",
                "route_target_action": "prioritize routing to institutional_pipeline",
                "evidence_ids": ["ev2"],
            }
        ],
        "axis_hints": [
            {"axis_id": "dark_humor", "evidence_ids": ["ev2"]},
            {"axis_id": "institutional_absurdity", "evidence_ids": ["ev2"]},
        ],
        "bucket_hints": [
            {"bucket_id": "institutional_pipeline", "evidence_ids": ["ev2"]},
            {"bucket_id": "dark_humor", "evidence_ids": ["ev2"]},
        ],
        "evidence_index": [
            {
                "evidence_id": "ev1",
                "source_ref": "scene:0001_001",
                "quote": "The reward arrives, and the fee notice arrives immediately after it.",
            },
            {
                "evidence_id": "ev2",
                "source_ref": "scene:0002_001",
                "quote": "The mentor hands over a filing sheet before offering any comfort.",
            },
        ],
    }


def _sample_legacy_style_row() -> dict:
    return {
        "window_id": "0001_0002",
        "chapter_ids": ["0001", "0002"],
        "source_chapter_titles": ["Chapter 1", "Chapter 2"],
        "surface_genre": ["赛博修仙校园文", "底层学生负债求学文"],
        "narrative_engine": [
            "叙事通过资格门槛、债务压力和制度化流程推进，而不是传统奇遇升级。",
            "每一次看似上升的选择都会暴露更重的消费与羞耻成本。",
        ],
        "narrator_distance": "近距离第三人称，紧贴主角体感和吐槽，但在介绍规则时会短暂拉远。",
        "humor_mechanisms": [
            "把修仙设定和现代教育金融术语硬拼，形成冷面黑色幽默。",
        ],
        "satire_targets": [
            "教育筛选制度与资源决定胜负的社会结构。",
        ],
        "characterization_mechanisms": [
            "人物通过花钱、欠债、身体代价与羞耻反应显形。",
        ],
        "dialogue_signature": [
            "对话像流程核验和催收，压迫感来自不给人物解释空间。",
        ],
        "pacing_pattern": [
            "开篇用面试与筛选快速建立规则，再用连续代价推进压力链。",
        ],
        "emotion_aftertaste": [
            "总体余味是窒息后的冷笑。",
        ],
        "why_nonstandard_xianxia": [
            "修仙门槛被改写成教育资格、消费能力和负债承受力。",
        ],
        "style_fingerprint": [
            "高频使用修仙术语和现代制度消费词的并置句法。",
        ],
        "supporting_evidence": [
            {
                "claim": "资格筛选和债务压力共同推动剧情。",
                "evidence_text": "面试、补习、贷款与身体代价反复出现，构成连续的制度压力链。",
            }
        ],
    }


class StyleExtractV2ContractsTest(unittest.TestCase):
    def test_style_window_signal_result_requires_grounded_evidence_refs(self) -> None:
        payload = _sample_style_row()
        parsed = StyleWindowSignalResult.model_validate(payload)

        self.assertEqual(parsed.schema_version, STYLE_WINDOW_SIGNAL_SCHEMA_VERSION)
        self.assertEqual(parsed.scalar_contracts.perspective, "close_third_person")
        self.assertEqual(parsed.routing_hints[0].bucket_id, "institutional_pipeline")

        broken_payload = _sample_style_row()
        broken_payload["routing_hints"][0]["evidence_ids"] = ["missing_ev"]
        with self.assertRaises(ValueError):
            StyleWindowSignalResult.model_validate(broken_payload)

    def test_style_window_signal_schema_requires_all_object_properties(self) -> None:
        schema = StyleWindowSignalResult.model_json_schema(by_alias=True)
        missing: list[tuple[str, list[str]]] = []

        def visit(node: object, path: str) -> None:
            if not isinstance(node, dict):
                return

            properties = node.get("properties", {})
            if isinstance(properties, dict):
                required = set(node.get("required", []))
                missing_keys = sorted(key for key in properties if key not in required)
                if missing_keys:
                    missing.append((path, missing_keys))
                for key, value in properties.items():
                    visit(value, f"{path}.properties.{key}")

            items = node.get("items")
            if isinstance(items, dict):
                visit(items, f"{path}.items")

            definitions = node.get("$defs", {})
            if isinstance(definitions, dict):
                for key, value in definitions.items():
                    visit(value, f"{path}.$defs.{key}")

        visit(schema, "$")
        self.assertEqual(missing, [])

    def test_client_can_force_json_schema_for_style_extract_when_project_default_is_json_object(self) -> None:
        client = object.__new__(StableOpenAICompatibleStructuredClient)
        config = _make_config(Path.cwd())
        config.base.model.response_format = "json_object"
        client.project_config = config

        response_format = StableOpenAICompatibleStructuredClient._build_response_format(
            client,
            StyleWindowSignalResult,
            response_format_mode="json_schema",
        )
        system_instruction = StableOpenAICompatibleStructuredClient._compose_system_instruction(
            client,
            "system prompt body",
            StyleWindowSignalResult,
            response_format_mode="json_schema",
        )

        self.assertEqual(response_format["type"], "json_schema")
        self.assertEqual(response_format["json_schema"]["name"], "StyleWindowSignalResult")
        self.assertEqual(system_instruction, "system prompt body")

    def test_extract_style_uses_strict_schema_for_style_signal_contract(self) -> None:
        class FakeStyleClient:
            calls: list[dict] = []

            def __init__(self, *_: object, **__: object) -> None:
                pass

            def generate_structured(self, **kwargs: object) -> SimpleNamespace:
                self.__class__.calls.append(dict(kwargs))
                return SimpleNamespace(
                    parsed=StyleWindowSignalResult.model_validate(_sample_style_row()),
                    model_name=str(kwargs.get("model_name", "")),
                    usage_metadata={},
                    request_metrics={"total_elapsed_seconds": 0.01, "response_chars": 256},
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prompt_dir = root / "prompts"
            input_dir = root / "chapters"
            output_dir = root / "style"
            prompt_dir.mkdir()
            input_dir.mkdir()
            (prompt_dir / "style_extraction.md").write_text("Extract style JSON.", encoding="utf-8")
            (input_dir / "chapter_0001.txt").write_text("Chapter 1\nA reward arrives. A fee follows.", encoding="utf-8")
            (input_dir / "chapter_0002.txt").write_text("Chapter 2\nA notice interrupts the lesson.", encoding="utf-8")

            config = _make_config(root)
            config.base.model.response_format = "json_object"
            original_client = pipelines.StableOpenAICompatibleStructuredClient
            try:
                pipelines.StableOpenAICompatibleStructuredClient = FakeStyleClient  # type: ignore[assignment]
                pipelines.extract_style(config, input_dir, output_dir, resume=False)
            finally:
                pipelines.StableOpenAICompatibleStructuredClient = original_client

            self.assertEqual(FakeStyleClient.calls[0]["response_format_mode"], "json_schema")
            self.assertEqual(FakeStyleClient.calls[0]["output_contract_mode"], "blueprint")
            self.assertTrue((output_dir / "style_window_0001_0002.json").exists())

    def test_style_window_signal_result_tolerates_recoverable_model_omissions(self) -> None:
        payload = _sample_style_row()
        payload.pop("source_chapter_titles")
        payload["scalar_contracts"] = {
            "perspective": "mixed",
            "distance": "",
            "temporality": "",
            "inner_monologue_mode": "",
        }

        parsed = StyleWindowSignalResult.model_validate(payload)

        self.assertEqual(parsed.source_chapter_titles, [])
        self.assertEqual(parsed.scalar_contracts.perspective, "multi_pov")
        self.assertEqual(parsed.scalar_contracts.distance, "unspecified")

    def test_style_window_normalization_ignores_runtime_metadata(self) -> None:
        payload = _sample_style_row()
        payload["artifact_fingerprint"] = {"sha256": "runtime-only"}

        normalized = normalize_style_window_payload(payload)

        self.assertEqual(normalized["window_id"], payload["window_id"])
        self.assertNotIn("artifact_fingerprint", normalized)

    def test_build_style_payload_emits_source_text_and_scene_locator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _make_config(Path(temp_dir))
            chapters = [
                ChapterDocument(
                    chapter_id="0001",
                    title="Chapter 1",
                    text="A reward arrives. A fee notice follows immediately.\n\nShe keeps moving while calculating the remaining balance.",
                    source_file="chapter_0001.txt",
                ),
                ChapterDocument(
                    chapter_id="0002",
                    title="Chapter 2",
                    text="The mentor hands over a filing sheet before offering comfort.\n\nEvery line reads like a joke, but nobody laughs.",
                    source_file="chapter_0002.txt",
                ),
            ]

            payload, applied_rules = _build_style_payload(config, chapters)

        self.assertEqual(payload["window_id"], "0001_0002")
        self.assertEqual(payload["chapters"][0]["chapter_id"], "0001")
        self.assertIn("source_text", payload["chapters"][0])
        self.assertNotIn("text", payload["chapters"][0])
        self.assertTrue(payload["scene_locator"])
        self.assertTrue(payload["scene_locator"][0]["source_ref"].startswith("scene:"))
        self.assertTrue(payload["scene_locator"][0]["start_anchor"])
        self.assertTrue(payload["scene_locator"][0]["end_anchor"])
        self.assertIsInstance(applied_rules, list)

    def test_style_content_stats_counts_new_signal_shape(self) -> None:
        stats = _style_content_stats(_sample_style_row())

        self.assertEqual(stats["narrative_engine_rules"], 1)
        self.assertEqual(stats["routing_hints"], 1)
        self.assertEqual(stats["evidence_index"], 2)
        self.assertGreater(stats["signal_total"], 0)

    def test_load_style_bible_inputs_normalizes_legacy_style_window_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            facts_dir = root / "facts"
            style_dir = root / "style"
            canon_dir = root / "canon"
            facts_dir.mkdir()
            style_dir.mkdir()
            canon_dir.mkdir()

            (facts_dir / "scene_0001_001.json").write_text(
                json.dumps(
                    {
                        "chapter_id": "0001",
                        "scene_id": "0001_001",
                        "scene_summary": "A notice interrupts the protagonist.",
                        "entities": [],
                        "events": [],
                        "facts": [],
                        "relationship_changes": [],
                        "power_system_notes": [],
                        "style_markers": [],
                        "open_questions": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (style_dir / "style_window_0001_0002.json").write_text(
                json.dumps(_sample_legacy_style_row(), ensure_ascii=False),
                encoding="utf-8",
            )
            (canon_dir / "chapter_summaries.jsonl").write_text(
                json.dumps(
                    {
                        "chapter_id": "0001",
                        "chapter_title": "Chapter 1",
                        "scene_count": 1,
                        "scene_summaries": ["A notice interrupts the protagonist."],
                        "open_questions": [],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            bundle = load_style_bible_inputs(facts_dir, style_dir, canon_dir)

        self.assertEqual(bundle.style_rows[0]["schema_version"], STYLE_WINDOW_SIGNAL_SCHEMA_VERSION)
        self.assertEqual(bundle.style_rows[0]["scalar_contracts"]["perspective"], "close_third_person")
        self.assertTrue(bundle.style_rows[0]["narrative_engine_rules"])
        self.assertTrue(bundle.style_rows[0]["evidence_index"])

    def test_load_style_bible_inputs_rejects_unknown_style_window_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            facts_dir = root / "facts"
            style_dir = root / "style"
            canon_dir = root / "canon"
            facts_dir.mkdir()
            style_dir.mkdir()
            canon_dir.mkdir()

            (facts_dir / "scene_0001_001.json").write_text(
                json.dumps(
                    {
                        "chapter_id": "0001",
                        "scene_id": "0001_001",
                        "scene_summary": "A notice interrupts the protagonist.",
                        "entities": [],
                        "events": [],
                        "facts": [],
                        "relationship_changes": [],
                        "power_system_notes": [],
                        "style_markers": [],
                        "open_questions": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (style_dir / "style_window_0001_0002.json").write_text(
                json.dumps(
                    {
                        "window_id": "0001_0002",
                        "chapter_ids": ["0001", "0002"],
                        "mystery_field": ["unknown style payload"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (canon_dir / "chapter_summaries.jsonl").write_text(
                json.dumps(
                    {
                        "chapter_id": "0001",
                        "chapter_title": "Chapter 1",
                        "scene_count": 1,
                        "scene_summaries": ["A notice interrupts the protagonist."],
                        "open_questions": [],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as ctx:
                load_style_bible_inputs(facts_dir, style_dir, canon_dir)

        self.assertIn(STYLE_WINDOW_SIGNAL_SCHEMA_VERSION, str(ctx.exception))

    def test_load_style_bible_inputs_requires_real_chapter_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            facts_dir = root / "facts"
            style_dir = root / "style"
            canon_dir = root / "canon"
            facts_dir.mkdir()
            style_dir.mkdir()
            canon_dir.mkdir()

            (facts_dir / "scene_0001_001.json").write_text(
                json.dumps(
                    {
                        "chapter_id": "0001",
                        "scene_id": "0001_001",
                        "scene_summary": "A notice interrupts the protagonist.",
                        "entities": [],
                        "events": [],
                        "facts": [],
                        "relationship_changes": [],
                        "power_system_notes": [],
                        "style_markers": [],
                        "open_questions": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (style_dir / "style_window_0001_0002.json").write_text(
                json.dumps(_sample_style_row(), ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaises(FileNotFoundError) as ctx:
                load_style_bible_inputs(facts_dir, style_dir, canon_dir)

        self.assertIn("chapter_summaries.jsonl", str(ctx.exception))

    def test_router_prefers_structured_axis_and_bucket_hints(self) -> None:
        style_row = _sample_style_row()
        inputs = StyleBibleInputBundle(
            fact_rows=[],
            style_rows=[style_row],
            chapter_rows=[
                {
                    "chapter_id": "0001",
                    "chapter_title": "Chapter 1",
                    "scene_count": 1,
                    "scene_summaries": ["A reward turns into a process debt."],
                    "open_questions": [],
                },
                {
                    "chapter_id": "0002",
                    "chapter_title": "Chapter 2",
                    "scene_count": 1,
                    "scene_summaries": ["A filing checklist overrides comfort."],
                    "open_questions": [],
                },
            ],
            plot_rows=[],
            entity_rows=[],
            canon_index={},
            style_index={},
            story_node_scope=None,
        )

        routed_index = route_style_bible_inputs(inputs, scope_hint="test style window scope")
        item = next(row for row in routed_index.items if row.item_type == "style_window")
        bucket_ids = [row.bucket_id for row in item.bucket_memberships]

        self.assertIn("dark_humor", item.axes)
        self.assertIn("institutional_absurdity", item.axes)
        self.assertIn("institutional_pipeline", bucket_ids)

    def test_builder_summaries_use_new_signal_fields(self) -> None:
        style_row = _sample_style_row()
        signal_summary = _build_style_signal_summary([style_row])
        index_summary = _build_style_index_summary(
            {
                "window_count": 1,
                "axis_hint_counts": {"dark_humor": 1},
                "bucket_hint_counts": {"institutional_pipeline": 1},
                "routing_target_counts": {"prioritize routing to institutional_pipeline": 1},
                "mechanism_label_counts": {"cost_first": 1},
                "negative_pitfall_counts": {"generalize the institutional conflict into a vague mood statement": 1},
                "scalar_contract_counts": {
                    "perspective": {"close_third_person": 1},
                    "distance": {"close": 1},
                    "temporality": {"linear_forward": 1},
                    "inner_monologue_mode": {"embedded": 1},
                },
            }
        )

        self.assertEqual(signal_summary["narrative_engine_labels"][0]["value"], "cost_first")
        self.assertIn(
            "prioritize routing to institutional_pipeline",
            [row["value"] for row in signal_summary["routing_target_actions"]],
        )
        self.assertEqual(index_summary["top_axis_hints"][0]["value"], "dark_humor")
        self.assertEqual(index_summary["scalar_contracts"]["perspective"][0]["value"], "close_third_person")

    def test_source_bundle_builds_worldbook_atoms_and_canonical_scalar_candidates(self) -> None:
        style_row = _sample_style_row()
        inputs = StyleBibleInputBundle(
            fact_rows=[
                {
                    "chapter_id": "0001",
                    "scene_id": "0001_001",
                    "scene_summary": "A reward turns into a debt notice.",
                    "entities": [],
                    "events": [
                        {
                            "name": "Fee Notice Arrives",
                            "summary": "A settlement notice lands immediately after the reward.",
                            "participants": ["Lin Qing"],
                            "location": "Outer Office",
                        }
                    ],
                    "facts": [
                        {
                            "subject": "Outer Office",
                            "predicate": "requires",
                            "object": "settlement before release",
                        }
                    ],
                    "relationship_changes": [],
                    "power_system_notes": [
                        {
                            "topic": "settlement",
                            "note": "Any gain is delayed until the fee ledger clears.",
                        }
                    ],
                    "style_markers": [],
                    "open_questions": [],
                }
            ],
            style_rows=[style_row],
            chapter_rows=[
                {
                    "chapter_id": "0001",
                    "chapter_title": "Chapter 1",
                    "scene_count": 1,
                    "scene_summaries": ["A settlement notice interrupts the reward."],
                    "open_questions": ["Who clears the release?"],
                }
            ],
            plot_rows=[
                {
                    "node_id": "plot_0001",
                    "chapter_id": "0001",
                    "title": "Settlement Gate",
                    "summary": "Institutional clearance controls access to the reward.",
                    "event_names": ["Fee Notice Arrives"],
                    "participants": ["Lin Qing"],
                    "locations": ["Outer Office"],
                    "scene_ids": ["0001_001"],
                    "plot_relevance_hint": "high",
                    "open_questions": ["Who clears the release?"],
                }
            ],
            entity_rows=[
                {
                    "entity_id": "entity_outer_office",
                    "name": "Outer Office",
                    "entity_type": "faction",
                    "aliases": ["Settlement Desk"],
                    "first_seen_chapter": "0001",
                    "supporting_scene_ids": ["0001_001"],
                    "notes": ["Controls release and settlement approvals."],
                }
            ],
            canon_index={},
            style_index={},
            story_node_scope=None,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = build_style_bible_source_bundle(
                "",
                "",
                "",
                output_dir=Path(tmpdir),
                inputs=inputs,
            )

        self.assertEqual(bundle["global_style_signals"]["scalar_contracts"]["inner_monologue_mode"][0]["value"], "sparse_inline")
        self.assertEqual(bundle["global_style_signals"]["scalar_contracts"]["narrator_voice"][0]["value"], "deadpan_procedural")
        atom_types = {row["atom_type"] for row in bundle["worldbook_atom_candidates"]}
        self.assertIn("fact", atom_types)
        self.assertIn("plot_node", atom_types)
        self.assertIn("entity", atom_types)


if __name__ == "__main__":
    unittest.main()
