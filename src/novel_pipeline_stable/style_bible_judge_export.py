from __future__ import annotations

import copy
import re
from typing import Any, Iterable

from novel_pipeline_stable.models import StyleBibleResult, style_bible_payload_to_flat


RULE_LIST_PATHS: tuple[str, ...] = (
    "narrative_system.engine",
    "narrative_system.pacing_rules",
    "narrative_system.plot_node_logic",
    "expression_system.description_rules",
    "expression_system.dialogue_rules",
    "expression_system.characterization_rules",
    "expression_system.sensory_rules",
    "aesthetics_system.core_axes",
    "aesthetics_system.pressure_axes",
    "aesthetics_system.humor_recipe",
    "aesthetics_system.satire_targets",
    "aesthetics_system.nonstandard_xianxia_rules",
    "voice_contract.register_mix",
    "voice_contract.negative_pitfalls",
    "character_arc_rules",
    "worldbook_binding.rag_worthy",
    "worldbook_binding.worldbook_worthy",
    "worldbook_binding.routing_hints",
    "negative_rules",
)

SCALAR_RULE_PATHS: tuple[str, ...] = (
    "narrative_system.perspective",
    "narrative_system.distance",
    "narrative_system.temporality",
    "voice_contract.narrator_voice",
    "voice_contract.inner_monologue_mode",
)

TRIGGER_CUES = ("当", "如果", "若", "出现", "遇到", "凡是", "涉及")
ROUTE_CUES = ("路由到", "路由至", "进入", "归到")
WORLDBOOK_ANCHORS = ("机构", "规则", "门槛", "资格", "资源", "制度", "节点", "世界书")
ENGLISH_LEAD_RE = re.compile(r"^\s*(When|If|Route|Store|Must|Do not)\b", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"[。；;]\s*")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        items.append(cleaned)
    return items


def _get_path(root: dict[str, Any], path: str) -> Any:
    current: Any = root
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _set_path(root: dict[str, Any], path: str, value: Any) -> None:
    current: Any = root
    segments = path.split(".")
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[segments[-1]] = value


def _rule_text(payload: dict[str, Any]) -> str:
    text = _clean_text(payload.get("text"))
    if text:
        return text
    pairs = (
        ("trigger", "constraint"),
        ("query_feature_matcher", "route_target_action"),
        ("forbidden_action", "correction_guideline"),
    )
    for left_key, right_key in pairs:
        left = _clean_text(payload.get(left_key))
        right = _clean_text(payload.get(right_key))
        if left and right:
            return f"{left}；{right}"
        if left or right:
            return left or right
    return ""


def _ensure_trigger_prefix(text: str) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    if any(cue in cleaned[:10] for cue in TRIGGER_CUES):
        return cleaned
    return f"当{cleaned.lstrip('，,；;：:。')}"


def _ensure_route_cue(text: str) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    if any(cue in cleaned for cue in ROUTE_CUES):
        return cleaned
    return f"路由到{cleaned.lstrip('，,；;：:。')}"


def _ensure_worldbook_anchor(text: str) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    if any(anchor in cleaned for anchor in WORLDBOOK_ANCHORS):
        return cleaned
    first_clause = SENTENCE_SPLIT_RE.split(cleaned, maxsplit=1)[0].strip(" ，,；;：:")
    topic = first_clause[:12] if first_clause else "制度接口"
    return f"{topic}规则：{cleaned}"


def _shorten_atomic_part(text: str, limit: int) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip("，,；;：:。")


def _ensure_rag_atomic(text: str) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    body = cleaned
    for prefix in ("可检索原子：", "可检索原子:"):
        if body.startswith(prefix):
            body = body[len(prefix):].strip()
            break
    parts = [part.strip(" ，,；;：:。") for part in re.split(r"\s*(?:->|→|=>)\s*", body) if part.strip()]
    if len(parts) >= 3:
        trigger, constraint, action = parts[:3]
    elif len(parts) == 2:
        trigger, constraint = parts
        action = constraint
    else:
        fragments = [part for part in re.split(r"[，,；;。]", body, maxsplit=2) if part.strip()]
        trigger = fragments[0] if fragments else body
        constraint = fragments[1] if len(fragments) > 1 else body
        action = fragments[2] if len(fragments) > 2 else constraint
    atomic = (
        f"资源规则：{_shorten_atomic_part(trigger, 12)}"
        f"->{_shorten_atomic_part(constraint, 10)}"
        f"->{_shorten_atomic_part(action, 10)}"
    )
    return atomic[:40].rstrip("，,；;：:。")


def _process_rule_item(item: Any, *, path: str) -> Any | None:
    if not isinstance(item, dict):
        text = _clean_text(item)
        if not text or ENGLISH_LEAD_RE.match(text):
            return None
        if path.endswith("worldbook_worthy"):
            return _ensure_trigger_prefix(_ensure_worldbook_anchor(text))
        if path.endswith("rag_worthy"):
            return _ensure_rag_atomic(text)
        if path.endswith("routing_hints"):
            return _ensure_trigger_prefix(_ensure_route_cue(text))
        return _ensure_trigger_prefix(text)

    payload = dict(item)
    text = _rule_text(payload)
    if not text or ENGLISH_LEAD_RE.match(text):
        return None

    if path.endswith("routing_hints"):
        matcher = _ensure_trigger_prefix(_clean_text(payload.get("query_feature_matcher")) or text)
        action = _ensure_route_cue(_clean_text(payload.get("route_target_action")) or text)
        payload["query_feature_matcher"] = matcher
        payload["route_target_action"] = action
        payload["text"] = f"{matcher}，{action}"
        return payload

    if path.endswith("worldbook_worthy"):
        payload["text"] = _ensure_trigger_prefix(_ensure_worldbook_anchor(text))
        return payload

    if path.endswith("rag_worthy"):
        payload["text"] = _ensure_rag_atomic(text)
        return payload

    payload["text"] = _ensure_trigger_prefix(text)
    return payload


def _reasoning_ref_map(reasoning_record: dict[str, Any] | None) -> dict[str, list[str]]:
    if not isinstance(reasoning_record, dict):
        return {}
    rows: dict[str, list[str]] = {}
    entries = reasoning_record.get("entries", [])
    if not isinstance(entries, list):
        return rows
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        reasoning_id = _clean_text(
            entry.get("reasoning_id") or entry.get("_reasoning_ref") or entry.get("reasoning_ref")
        )
        if not reasoning_id:
            continue
        refs = entry.get("evidence_refs", [])
        rows[reasoning_id] = _unique_strings(refs if isinstance(refs, list) else [])
    return rows


def _source_ref_text_map(source_bundle: dict[str, Any] | None) -> dict[str, str]:
    rows: dict[str, str] = {}

    def remember(ref: str, text: str) -> None:
        cleaned_ref = _clean_text(ref)
        cleaned_text = _clean_text(text)
        if cleaned_ref and cleaned_text and cleaned_ref not in rows:
            rows[cleaned_ref] = cleaned_text

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            source_ref = _clean_text(node.get("source_ref"))
            if source_ref:
                remember(
                    source_ref,
                    node.get("quote")
                    or node.get("evidence_text")
                    or node.get("text")
                    or node.get("scene_summary")
                    or node.get("note")
                    or node.get("explanation"),
                )
            scene_id = _clean_text(node.get("scene_id"))
            if scene_id:
                remember(f"scene:{scene_id}", node.get("scene_summary") or node.get("text") or node.get("evidence_text"))
            window_id = _clean_text(node.get("window_id"))
            if window_id:
                remember(window_id, node.get("summary") or node.get("text") or node.get("schema_version"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    if isinstance(source_bundle, dict):
        walk(source_bundle)
    return rows


def _iter_rule_payloads(record: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for path in RULE_LIST_PATHS:
        value = _get_path(record, path)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    rows.append((path, item))
        elif isinstance(value, dict):
            rows.append((path, value))
    for path in SCALAR_RULE_PATHS:
        value = _get_path(record, path)
        if isinstance(value, dict):
            rows.append((path, value))
    return rows


def _rule_refs(rule: dict[str, Any], reasoning_refs: dict[str, list[str]]) -> list[str]:
    direct_refs = rule.get("evidence_refs", [])
    refs = list(direct_refs) if isinstance(direct_refs, list) else []
    reasoning_ref = _clean_text(rule.get("_reasoning_ref") or rule.get("reasoning_ref"))
    refs.extend(reasoning_refs.get(reasoning_ref, []))
    return _unique_strings(refs)


def _build_supporting_evidence(
    record: dict[str, Any],
    *,
    source_bundle: dict[str, Any] | None,
    reasoning_record: dict[str, Any] | None,
    limit: int = 18,
) -> list[dict[str, str]]:
    source_texts = _source_ref_text_map(source_bundle)
    reasoning_refs = _reasoning_ref_map(reasoning_record)
    candidates: list[dict[str, str]] = []

    rule_rows = _iter_rule_payloads(record)
    max_ref_depth = max((len(_rule_refs(rule, reasoning_refs)) for _, rule in rule_rows), default=0)
    for ref_index in range(min(max_ref_depth, 4)):
        for path, rule in rule_rows:
            refs = _rule_refs(rule, reasoning_refs)
            if ref_index >= len(refs):
                continue
            source_ref = refs[ref_index]
            text = _rule_text(rule)
            rule_id = _clean_text(rule.get("rule_id")) or path
            candidates.append(
                {
                    "claim": f"{rule_id}：{text[:90]}",
                    "evidence_text": source_texts.get(source_ref, text[:120]),
                    "source_ref": source_ref,
                }
            )

    existing = record.get("supporting_evidence", [])
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, dict):
                continue
            source_ref = _clean_text(item.get("source_ref"))
            if not source_ref:
                continue
            candidates.append(
                {
                    "claim": _clean_text(item.get("claim")),
                    "evidence_text": _clean_text(item.get("evidence_text")) or source_texts.get(source_ref, ""),
                    "source_ref": source_ref,
                }
            )

    seen_refs: set[str] = set()
    rows: list[dict[str, str]] = []
    for item in candidates:
        source_ref = _clean_text(item.get("source_ref"))
        claim = _clean_text(item.get("claim"))
        evidence_text = _clean_text(item.get("evidence_text")) or claim
        if not source_ref or not claim or source_ref in seen_refs:
            continue
        seen_refs.add(source_ref)
        rows.append({"claim": claim, "evidence_text": evidence_text, "source_ref": source_ref})
        if len(rows) >= limit:
            break
    return rows


def build_judge_flat(
    final_record: dict[str, Any],
    source_bundle: dict[str, Any] | None = None,
    reasoning_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the deterministic Judge V2 projection without changing the canonical master."""

    if not isinstance(final_record, dict):
        raise ValueError("final_record must be a dict.")
    record = copy.deepcopy(final_record)

    for path in RULE_LIST_PATHS:
        value = _get_path(record, path)
        if isinstance(value, list):
            processed = [_process_rule_item(item, path=path) for item in value]
            _set_path(record, path, [item for item in processed if item not in (None, "", {}, [])])
        elif isinstance(value, dict):
            processed_item = _process_rule_item(value, path=path)
            if processed_item is not None:
                _set_path(record, path, processed_item)

    for path in SCALAR_RULE_PATHS:
        value = _get_path(record, path)
        if isinstance(value, dict):
            processed_item = _process_rule_item(value, path=path)
            if processed_item is not None:
                _set_path(record, path, processed_item)

    flat_payload = style_bible_payload_to_flat(record)
    flat_payload["supporting_evidence"] = _build_supporting_evidence(
        record,
        source_bundle=source_bundle,
        reasoning_record=reasoning_record,
    )
    flat_model = StyleBibleResult.model_validate(flat_payload)
    return flat_model.model_dump(mode="json")


__all__ = ["build_judge_flat"]
