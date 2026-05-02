from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from novel_pipeline_stable.models import STYLE_WINDOW_SIGNAL_SCHEMA_VERSION, StyleWindowSignalResult


LEGACY_STYLE_WINDOW_KEYS = frozenset(
    {
        "surface_genre",
        "narrative_engine",
        "narrator_distance",
        "humor_mechanisms",
        "satire_targets",
        "characterization_mechanisms",
        "dialogue_signature",
        "pacing_pattern",
        "emotion_aftertaste",
        "why_nonstandard_xianxia",
        "style_fingerprint",
        "supporting_evidence",
    }
)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_strings(values: Any, *, limit: int | None = None) -> list[str]:
    if not isinstance(values, list):
        return []
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(cleaned)
        if limit is not None and len(results) >= limit:
            break
    return results


def _slugify_label(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower()).strip("_")
    return cleaned or fallback


def _legacy_window_source_ref(window_id: str) -> str:
    return f"style_window:{window_id}"


def is_legacy_style_window_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if _clean_text(payload.get("schema_version")) == STYLE_WINDOW_SIGNAL_SCHEMA_VERSION:
        return False
    window_id = _clean_text(payload.get("window_id"))
    chapter_ids = payload.get("chapter_ids")
    if not window_id or not isinstance(chapter_ids, list) or not chapter_ids:
        return False
    return any(key in payload for key in LEGACY_STYLE_WINDOW_KEYS)


def _default_scalar_contracts(payload: dict[str, Any]) -> dict[str, str]:
    narrator_distance = _clean_text(payload.get("narrator_distance"))
    pacing_text = " ".join(_compact_strings(payload.get("pacing_pattern"), limit=4))
    dialogue_text = " ".join(_compact_strings(payload.get("dialogue_signature"), limit=4))

    perspective = "unspecified"
    if "第一人称" in narrator_distance:
        perspective = "first_person"
    elif "多视角" in narrator_distance or "群像" in narrator_distance or "轮换" in narrator_distance:
        perspective = "multi_pov"
    elif "全知" in narrator_distance or "上帝视角" in narrator_distance:
        perspective = "omniscient_third_person"
    elif "第三人称" in narrator_distance:
        perspective = "close_third_person"
    elif "镜头" in narrator_distance or "客观" in narrator_distance:
        perspective = "objective_camera"

    close_hit = any(token in narrator_distance for token in ("近距离", "贴近", "紧贴", "贴身"))
    intimate_hit = any(token in narrator_distance for token in ("体感", "内心", "心理", "焦虑", "吐槽"))
    far_hit = any(token in narrator_distance for token in ("拉远", "远距离", "俯瞰", "概括"))
    if (close_hit or intimate_hit) and far_hit:
        distance = "mixed"
    elif intimate_hit:
        distance = "intimate"
    elif close_hit:
        distance = "close"
    elif far_hit:
        distance = "far"
    elif "中距离" in narrator_distance:
        distance = "medium"
    else:
        distance = "unspecified"

    if any(token in pacing_text for token in ("倒叙", "回忆", "闪回")):
        temporality = "flashback_insert"
    elif any(token in pacing_text for token in ("插叙", "穿插", "并行", "切换")):
        temporality = "intercut"
    elif any(token in pacing_text for token in ("回望", "追述", "框架")):
        temporality = "retrospective_frame"
    elif pacing_text:
        temporality = "linear_forward"
    else:
        temporality = "unspecified"

    inner_text = f"{narrator_distance} {dialogue_text}"
    if any(token in inner_text for token in ("内心", "心里", "体感", "焦虑", "吐槽", "念头", "心理")):
        inner_monologue_mode = "embedded"
    elif any(token in inner_text for token in ("概括", "总结", "汇报")):
        inner_monologue_mode = "summary_report"
    else:
        inner_monologue_mode = "unspecified"

    return {
        "perspective": perspective,
        "distance": distance,
        "temporality": temporality,
        "inner_monologue_mode": inner_monologue_mode,
    }


def _legacy_evidence_index(payload: dict[str, Any]) -> list[dict[str, Any]]:
    window_id = _clean_text(payload.get("window_id"))
    source_ref = _legacy_window_source_ref(window_id)
    evidence_rows: list[dict[str, Any]] = []
    supporting_evidence = payload.get("supporting_evidence", [])
    if isinstance(supporting_evidence, list):
        for index, item in enumerate(supporting_evidence, start=1):
            if not isinstance(item, dict):
                continue
            claim = _clean_text(item.get("claim"))
            quote = _clean_text(item.get("evidence_text")) or claim
            if not quote:
                continue
            evidence_rows.append(
                {
                    "evidence_id": f"e{index:02d}",
                    "source_ref": source_ref,
                    "quote": quote,
                }
            )
    if evidence_rows:
        return evidence_rows

    backup_quotes = (
        _compact_strings(payload.get("style_fingerprint"), limit=3)
        or _compact_strings(payload.get("narrative_engine"), limit=3)
        or _compact_strings(payload.get("surface_genre"), limit=3)
    )
    for index, quote in enumerate(backup_quotes, start=1):
        evidence_rows.append(
            {
                "evidence_id": f"e{index:02d}",
                "source_ref": source_ref,
                "quote": quote,
            }
        )
    return evidence_rows


def _default_evidence_ids(evidence_index: list[dict[str, Any]]) -> list[str]:
    evidence_ids = [_clean_text(row.get("evidence_id")) for row in evidence_index if _clean_text(row.get("evidence_id"))]
    return evidence_ids[: min(len(evidence_ids), 3)] or ["e01"]


def _rule_rows_from_legacy_texts(
    values: Any,
    *,
    prefix: str,
    trigger: str,
    evidence_ids: list[str],
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, text in enumerate(_compact_strings(values, limit=limit), start=1):
        rows.append(
            {
                "mechanism_label": _slugify_label(f"{prefix}_{index:02d}", f"{prefix}_{index:02d}"),
                "execution_logic": text,
                "trigger": trigger,
                "constraint": text,
                "evidence_ids": list(evidence_ids),
            }
        )
    return rows


def _legacy_negative_pitfalls(evidence_ids: list[str]) -> list[dict[str, Any]]:
    if not evidence_ids:
        return []
    return [
        {
            "forbidden_action": "把制度压力、债务代价和身体成本改写成空泛热血或抽象修仙爽感。",
            "correction_guideline": "保留资格门槛、费用链条、身体代价与冷面吐槽这些可执行的具体机制。",
            "evidence_ids": list(evidence_ids),
        }
    ]


def legacy_style_window_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    evidence_index = _legacy_evidence_index(payload)
    evidence_ids = _default_evidence_ids(evidence_index)

    narrator_voice_texts = []
    narrator_distance = _clean_text(payload.get("narrator_distance"))
    if narrator_distance:
        narrator_voice_texts.append(narrator_distance)
    narrator_voice_texts.extend(_compact_strings(payload.get("emotion_aftertaste"), limit=2))

    surface_markers = _compact_strings(payload.get("surface_genre"), limit=4)
    surface_markers.extend(_compact_strings(payload.get("style_fingerprint"), limit=8))
    surface_markers.extend(_compact_strings(payload.get("emotion_aftertaste"), limit=2))
    surface_markers = _compact_strings(surface_markers, limit=12)

    normalized = {
        "schema_version": STYLE_WINDOW_SIGNAL_SCHEMA_VERSION,
        "window_id": _clean_text(payload.get("window_id")),
        "chapter_ids": _compact_strings(payload.get("chapter_ids"), limit=16),
        "source_chapter_titles": _compact_strings(payload.get("source_chapter_titles"), limit=16),
        "scalar_contracts": _default_scalar_contracts(payload),
        "surface_markers": surface_markers,
        "narrative_engine_rules": _rule_rows_from_legacy_texts(
            payload.get("narrative_engine"),
            prefix="legacy_narrative_engine",
            trigger="when the chapter advances through a dominant pressure chain or institutional gate",
            evidence_ids=evidence_ids,
            limit=6,
        ),
        "pacing_rules": _rule_rows_from_legacy_texts(
            payload.get("pacing_pattern"),
            prefix="legacy_pacing",
            trigger="when the chapter needs to escalate pressure, gate progress, or reset the scene rhythm",
            evidence_ids=evidence_ids,
            limit=6,
        ),
        "plot_node_logic_rules": [],
        "description_rules": _rule_rows_from_legacy_texts(
            payload.get("style_fingerprint"),
            prefix="legacy_description",
            trigger="when description needs to externalize institutions, debt, or bodily cost through concrete details",
            evidence_ids=evidence_ids,
            limit=4,
        ),
        "dialogue_rules": _rule_rows_from_legacy_texts(
            payload.get("dialogue_signature"),
            prefix="legacy_dialogue",
            trigger="when dialogue carries screening, sales, debt, or process pressure",
            evidence_ids=evidence_ids,
            limit=6,
        ),
        "characterization_rules": _rule_rows_from_legacy_texts(
            payload.get("characterization_mechanisms"),
            prefix="legacy_characterization",
            trigger="when characterization must emerge through money, shame, body cost, or family burden",
            evidence_ids=evidence_ids,
            limit=6,
        ),
        "sensory_rules": [],
        "humor_rules": _rule_rows_from_legacy_texts(
            payload.get("humor_mechanisms"),
            prefix="legacy_humor",
            trigger="when absurd institutional or bodily cost should land as deadpan or black humor",
            evidence_ids=evidence_ids,
            limit=6,
        ),
        "satire_rules": _rule_rows_from_legacy_texts(
            payload.get("satire_targets"),
            prefix="legacy_satire",
            trigger="when the text exposes a target institution, class mechanism, or commodified pressure system",
            evidence_ids=evidence_ids,
            limit=6,
        ),
        "nonstandard_xianxia_rules": _rule_rows_from_legacy_texts(
            payload.get("why_nonstandard_xianxia"),
            prefix="legacy_nonstandard_xianxia",
            trigger="when cultivation is reframed through education, debt, labor, or institutional survival logic",
            evidence_ids=evidence_ids,
            limit=6,
        ),
        "narrator_voice_rules": _rule_rows_from_legacy_texts(
            narrator_voice_texts,
            prefix="legacy_narrator_voice",
            trigger="when narration needs to toggle between close pressure, cold institutional summary, and aftertaste",
            evidence_ids=evidence_ids,
            limit=4,
        ),
        "register_mix_rules": [],
        "negative_pitfalls": _legacy_negative_pitfalls(evidence_ids),
        "rag_candidates": [],
        "worldbook_candidates": [],
        "routing_hints": [],
        "axis_hints": [],
        "bucket_hints": [],
        "evidence_index": evidence_index,
    }
    return normalized


def normalize_style_window_payload(payload: dict[str, Any], *, source_path: Path | None = None) -> dict[str, Any]:
    source_label = str(source_path) if source_path is not None else "<memory>"
    try:
        parsed = StyleWindowSignalResult.model_validate(payload)
        return parsed.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        if not is_legacy_style_window_payload(payload):
            schema_version = _clean_text(payload.get("schema_version"))
            if schema_version != STYLE_WINDOW_SIGNAL_SCHEMA_VERSION:
                raise ValueError(
                    f"Unsupported style window schema in {source_label}. "
                    f"Expected {STYLE_WINDOW_SIGNAL_SCHEMA_VERSION}, got {schema_version or 'missing'}."
                ) from exc
            raise ValueError(f"Invalid style window payload in {source_label}: {exc}") from exc

    normalized_payload = legacy_style_window_to_v2(payload)
    try:
        parsed = StyleWindowSignalResult.model_validate(normalized_payload)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Legacy style window could not be normalized in {source_label}: {exc}") from exc
    return parsed.model_dump(mode="json")


__all__ = [
    "is_legacy_style_window_payload",
    "legacy_style_window_to_v2",
    "normalize_style_window_payload",
]
