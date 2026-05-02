from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_pipeline_stable.io_utils import (
    ensure_dir,
    iter_json_files,
    iter_text_files,
    read_json,
    read_text,
    write_json,
    write_markdown,
    write_text,
)


METADATA_JSON_NAMES = {"manifest.json", "failures.json", "run_status.json"}
FULL_LAYER_PATTERN = re.compile(r"昆墟(?:第)?([零一二三四五六七八九十百千万两\d]+)层")
BARE_LAYER_PATTERN = re.compile(r"第?([零一二三四五六七八九十百千万两\d]+)层")
TRANSITION_HINT_PATTERN = re.compile(r"(进入|来到|抵达|登上|升入|前往|踏入|搬到|去往|赶往|转入)")
FUTURE_HINT_PATTERN = re.compile(r"(以后|之后|将|会|如果|考上|毕业后|高考后|高考结束|半年后|半年)")
TEXT_LOCATION_PATTERN = re.compile(r"([一-龥A-Za-z0-9]{2,12}(?:大学城|大学|高中|工地|厂区|墓地|宿舍|通道|飞升台|手术室|赛场|市))")
CHAPTER_FILENAME_PATTERN = re.compile(r"chapter_(\d+)\.txt$")
GENERIC_LOCATION_NAMES = {
    "昆墟",
    "大学",
    "大学城",
    "高中",
    "学校",
    "教室",
    "食堂",
    "公寓",
    "宿舍",
    "工地",
    "考场",
    "办公室",
    "练功房",
    "练功场",
    "广场",
    "赛场",
    "擂台",
    "墓地",
    "地下墓地",
    "灵界",
}
WINDOW_SIZE = 24
WINDOW_STEP = 4
MIN_MAJOR_NODE_GAP = 100
MIN_PRIMARY_LAYER_SCORE = 4.0
MIN_WINDOW_LAYER_SCORE = 18.0
REQUIRED_FUTURE_WINDOWS = 2
MAX_VALID_LAYER = 36


@dataclass(slots=True)
class ChapterProfile:
    chapter_id: str
    chapter_title: str
    layer_scores: dict[int, float]
    layer_evidence: dict[int, list[dict[str, Any]]]
    location_counter: Counter[str]
    primary_layer: int | None
    primary_layer_score: float
    secondary_layer: int | None
    secondary_layer_score: float


@dataclass(slots=True)
class WindowProfile:
    start_index: int
    end_index: int
    start_chapter: str
    end_chapter: str
    dominant_layer: int | None
    dominant_layer_score: float
    second_layer: int | None
    second_layer_score: float
    top_locations: list[dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _chapter_sort_key(value: Any) -> tuple[int, Any]:
    text = str(value or "").strip()
    if not text:
        return (2, "")
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _extract_chapter_id_from_path(path: Path) -> str:
    match = CHAPTER_FILENAME_PATTERN.fullmatch(path.name)
    if not match:
        raise ValueError(f"Unsupported chapter filename: {path.name}")
    return match.group(1)


def _extract_chapter_title(text: str, fallback: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            return line
    return fallback


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _is_valid_layer(value: int | None) -> bool:
    return value is not None and 1 <= value <= MAX_VALID_LAYER


def _chinese_number_to_int(value: str) -> int | None:
    text = _clean_text(value)
    if not text:
        return None
    if text.isdigit():
        return int(text)

    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}

    total = 0
    section = 0
    number = 0
    seen = False
    for char in text:
        if char in digits:
            number = digits[char]
            seen = True
            continue
        if char in units:
            unit_value = units[char]
            seen = True
            if unit_value == 10000:
                section = (section + max(number, 1)) * unit_value
                total += section
                section = 0
                number = 0
                continue
            section += max(number, 1) * unit_value
            number = 0
            continue
        return None

    if not seen:
        return None
    return total + section + number


def _int_to_chinese(value: int) -> str:
    if value <= 0:
        return str(value)
    digits = "零一二三四五六七八九"
    units = ["", "十", "百", "千"]
    if value < 10:
        return digits[value]
    if value < 20:
        tail = "" if value == 10 else digits[value % 10]
        return f"十{tail}"
    if value < 100:
        tens, ones = divmod(value, 10)
        return f"{digits[tens]}十{digits[ones] if ones else ''}"
    parts: list[str] = []
    remaining = value
    unit_index = 0
    while remaining > 0:
        remaining, current = divmod(remaining, 10)
        if current == 0:
            if parts and parts[0] != digits[0]:
                parts.insert(0, digits[0])
            unit_index += 1
            continue
        piece = f"{digits[current]}{units[unit_index]}"
        parts.insert(0, piece)
        unit_index += 1
    result = "".join(parts).strip("零")
    result = re.sub(r"零+", "零", result)
    result = re.sub(r"^一十", "十", result)
    return result


def _layer_label(layer: int | None) -> str:
    if not _is_valid_layer(layer):
        return ""
    return f"昆墟第{_int_to_chinese(int(layer))}层"


def _normalize_layer_token(value: str) -> int | None:
    layer = _chinese_number_to_int(value)
    if not _is_valid_layer(layer):
        return None
    return layer


def _snippet(text: str, start: int, end: int, *, radius: int = 36) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return _clean_text(text[left:right]).replace("\n", " ")


def _record_layer_evidence(
    text: str,
    *,
    source: str,
    base_weight: float,
    allow_bare_layer: bool,
    layer_scores: dict[int, float],
    layer_evidence: dict[int, list[dict[str, Any]]],
) -> None:
    if not text:
        return

    full_spans: list[tuple[int, int]] = []
    for match in FULL_LAYER_PATTERN.finditer(text):
        layer = _normalize_layer_token(match.group(1))
        if layer is None:
            continue
        snippet = _snippet(text, match.start(), match.end())
        has_transition_hint = bool(TRANSITION_HINT_PATTERN.search(snippet))
        has_future_hint = bool(FUTURE_HINT_PATTERN.search(snippet))
        weight = base_weight + (2.0 if has_transition_hint else 0.0)
        if has_future_hint:
            weight *= 0.35 if has_transition_hint else 0.25
        layer_scores[layer] = layer_scores.get(layer, 0.0) + weight
        layer_evidence.setdefault(layer, []).append(
            {
                "source": source,
                "weight": round(weight, 2),
                "match_text": match.group(0),
                "snippet": snippet,
                "future_hint": has_future_hint,
            }
        )
        full_spans.append((match.start(), match.end()))

    if not allow_bare_layer:
        return

    for match in BARE_LAYER_PATTERN.finditer(text):
        if any(start <= match.start() < end for start, end in full_spans):
            continue
        layer = _normalize_layer_token(match.group(1))
        if layer is None:
            continue
        snippet = _snippet(text, match.start(), match.end())
        if "昆墟" not in text and "昆墟" not in snippet and not TRANSITION_HINT_PATTERN.search(snippet):
            continue
        has_future_hint = bool(FUTURE_HINT_PATTERN.search(snippet))
        weight = max(base_weight * 0.45, 0.6)
        if TRANSITION_HINT_PATTERN.search(snippet):
            weight += 1.0
        if has_future_hint:
            weight *= 0.35
        layer_scores[layer] = layer_scores.get(layer, 0.0) + weight
        layer_evidence.setdefault(layer, []).append(
            {
                "source": f"{source}:implicit",
                "weight": round(weight, 2),
                "match_text": match.group(0),
                "snippet": snippet,
                "future_hint": has_future_hint,
            }
        )


def _is_meaningful_location_name(name: str) -> bool:
    cleaned = _clean_text(name)
    if not cleaned or cleaned in GENERIC_LOCATION_NAMES:
        return False
    if FULL_LAYER_PATTERN.search(cleaned):
        return False
    if BARE_LAYER_PATTERN.fullmatch(cleaned):
        return False
    if cleaned.endswith("层") and _normalize_layer_token(cleaned[:-1].removeprefix("第")) is not None:
        return False
    return True


def _is_major_location_name(name: str) -> bool:
    cleaned = _clean_text(name)
    if not _is_meaningful_location_name(cleaned):
        return False
    if any(token in cleaned for token in ("休息区", "餐区", "公寓内", "房间", "天台", "病房", "观众席", "教室", "食堂", "宿舍区", "手术室")):
        return False
    return any(keyword in cleaned for keyword in ("大学城", "大学", "高中", "通道", "厂区", "工地", "墓地", "市", "层"))


def _build_location_counter(payload: dict[str, Any]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for entity in payload.get("entities", []):
        if not isinstance(entity, dict):
            continue
        if _clean_text(entity.get("entity_type")) != "location":
            continue
        name = _clean_text(entity.get("name"))
        if _is_meaningful_location_name(name):
            counter[name] += 1
    for event in payload.get("events", []):
        if not isinstance(event, dict):
            continue
        location = _clean_text(event.get("location"))
        if _is_meaningful_location_name(location):
            counter[location] += 1
    return counter


def _top_locations(counter: Counter[str], *, limit: int = 5) -> list[dict[str, Any]]:
    rows = [
        {"name": name, "count": count}
        for name, count in counter.most_common()
        if _is_meaningful_location_name(name)
    ]
    return rows[:limit]


def _extract_text_location_counter(chapter_text: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for match in TEXT_LOCATION_PATTERN.finditer(chapter_text):
        name = _clean_text(match.group(1))
        if _is_meaningful_location_name(name):
            counter[name] += 1
    return counter


def _profile_from_inputs(chapter_id: str, chapter_title: str, chapter_text: str, fact_rows: list[dict[str, Any]]) -> ChapterProfile:
    layer_scores: dict[int, float] = {}
    layer_evidence: dict[int, list[dict[str, Any]]] = {}
    location_counter: Counter[str] = Counter()

    _record_layer_evidence(
        chapter_text,
        source="chapter_text",
        base_weight=4.0,
        allow_bare_layer=False,
        layer_scores=layer_scores,
        layer_evidence=layer_evidence,
    )
    location_counter.update(_extract_text_location_counter(chapter_text))

    for payload in fact_rows:
        _record_layer_evidence(
            _clean_text(payload.get("scene_summary")),
            source="scene_summary",
            base_weight=3.0,
            allow_bare_layer=False,
            layer_scores=layer_scores,
            layer_evidence=layer_evidence,
        )
        for entity in payload.get("entities", []):
            if not isinstance(entity, dict):
                continue
            entity_type = _clean_text(entity.get("entity_type"))
            base_weight = 5.0 if entity_type == "location" else 1.5
            allow_bare = entity_type == "location"
            _record_layer_evidence(
                _clean_text(entity.get("name")),
                source=f"entity:{entity_type}:name",
                base_weight=base_weight,
                allow_bare_layer=allow_bare,
                layer_scores=layer_scores,
                layer_evidence=layer_evidence,
            )
            _record_layer_evidence(
                _clean_text(entity.get("role_in_scene")),
                source=f"entity:{entity_type}:role",
                base_weight=1.2,
                allow_bare_layer=False,
                layer_scores=layer_scores,
                layer_evidence=layer_evidence,
            )
        for event in payload.get("events", []):
            if not isinstance(event, dict):
                continue
            _record_layer_evidence(
                _clean_text(event.get("summary")),
                source="event:summary",
                base_weight=2.2,
                allow_bare_layer=False,
                layer_scores=layer_scores,
                layer_evidence=layer_evidence,
            )
            _record_layer_evidence(
                _clean_text(event.get("location")),
                source="event:location",
                base_weight=4.5,
                allow_bare_layer=True,
                layer_scores=layer_scores,
                layer_evidence=layer_evidence,
            )
        for fact in payload.get("facts", []):
            if not isinstance(fact, dict):
                continue
            for key in ("subject", "object"):
                _record_layer_evidence(
                    _clean_text(fact.get(key)),
                    source=f"fact:{key}",
                    base_weight=1.0,
                    allow_bare_layer=False,
                    layer_scores=layer_scores,
                    layer_evidence=layer_evidence,
                )
        for note in payload.get("power_system_notes", []):
            if not isinstance(note, dict):
                continue
            _record_layer_evidence(
                f"{_clean_text(note.get('topic'))} {_clean_text(note.get('note'))}".strip(),
                source="power_system_note",
                base_weight=1.0,
                allow_bare_layer=False,
                layer_scores=layer_scores,
                layer_evidence=layer_evidence,
            )
        for question in payload.get("open_questions", []):
            _record_layer_evidence(
                _clean_text(question),
                source="open_question",
                base_weight=0.8,
                allow_bare_layer=False,
                layer_scores=layer_scores,
                layer_evidence=layer_evidence,
            )
        location_counter.update(_build_location_counter(payload))

    ordered_layers = sorted(layer_scores.items(), key=lambda item: (-item[1], item[0]))
    primary_layer = ordered_layers[0][0] if ordered_layers and ordered_layers[0][1] >= MIN_PRIMARY_LAYER_SCORE else None
    primary_layer_score = ordered_layers[0][1] if primary_layer is not None else 0.0
    secondary_layer = ordered_layers[1][0] if len(ordered_layers) > 1 else None
    secondary_layer_score = ordered_layers[1][1] if len(ordered_layers) > 1 else 0.0

    return ChapterProfile(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        layer_scores=layer_scores,
        layer_evidence=layer_evidence,
        location_counter=location_counter,
        primary_layer=primary_layer,
        primary_layer_score=primary_layer_score,
        secondary_layer=secondary_layer,
        secondary_layer_score=secondary_layer_score,
    )


def _load_fact_rows_by_chapter(facts_dir: str | Path | None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    rows_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scene_file_count = 0
    max_fact_chapter = ""
    if not facts_dir:
        return rows_by_chapter, {"scene_file_count": 0, "chapter_count": 0, "max_fact_chapter": ""}

    for path in iter_json_files(facts_dir):
        if path.name in METADATA_JSON_NAMES:
            continue
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        chapter_id = _clean_text(payload.get("chapter_id"))
        if not chapter_id:
            continue
        rows_by_chapter[chapter_id].append(payload)
        scene_file_count += 1
        if not max_fact_chapter or _chapter_sort_key(chapter_id) > _chapter_sort_key(max_fact_chapter):
            max_fact_chapter = chapter_id

    return rows_by_chapter, {
        "scene_file_count": scene_file_count,
        "chapter_count": len(rows_by_chapter),
        "max_fact_chapter": max_fact_chapter,
    }


def _build_chapter_profiles(chapters_dir: str | Path, facts_dir: str | Path | None) -> tuple[list[ChapterProfile], dict[str, Any]]:
    fact_rows_by_chapter, fact_summary = _load_fact_rows_by_chapter(facts_dir)
    profiles: list[ChapterProfile] = []
    for path in iter_text_files(chapters_dir):
        chapter_id = _extract_chapter_id_from_path(path)
        chapter_text = read_text(path)
        chapter_title = _extract_chapter_title(chapter_text, fallback=f"Chapter {chapter_id}")
        profiles.append(
            _profile_from_inputs(
                chapter_id,
                chapter_title,
                chapter_text,
                fact_rows_by_chapter.get(chapter_id, []),
            )
        )
    profiles.sort(key=lambda item: _chapter_sort_key(item.chapter_id))
    return profiles, fact_summary


def _window_location_counter(chapters: list[ChapterProfile], start_index: int, end_index: int) -> Counter[str]:
    counter: Counter[str] = Counter()
    for profile in chapters[start_index:end_index]:
        counter.update(profile.location_counter)
    return counter


def _build_window_profiles(chapters: list[ChapterProfile]) -> list[WindowProfile]:
    windows: list[WindowProfile] = []
    if not chapters:
        return windows

    for start_index in range(0, len(chapters), WINDOW_STEP):
        end_index = min(start_index + WINDOW_SIZE, len(chapters))
        batch = chapters[start_index:end_index]
        layer_scores: Counter[int] = Counter()
        for profile in batch:
            for layer, score in profile.layer_scores.items():
                layer_scores[layer] += score
        ordered_layers = sorted(layer_scores.items(), key=lambda item: (-item[1], item[0]))
        dominant_layer = ordered_layers[0][0] if ordered_layers and ordered_layers[0][1] >= MIN_WINDOW_LAYER_SCORE else None
        dominant_layer_score = ordered_layers[0][1] if dominant_layer is not None else 0.0
        second_layer = ordered_layers[1][0] if len(ordered_layers) > 1 else None
        second_layer_score = ordered_layers[1][1] if len(ordered_layers) > 1 else 0.0
        location_counter = _window_location_counter(chapters, start_index, end_index)
        windows.append(
            WindowProfile(
                start_index=start_index,
                end_index=end_index,
                start_chapter=batch[0].chapter_id,
                end_chapter=batch[-1].chapter_id,
                dominant_layer=dominant_layer,
                dominant_layer_score=dominant_layer_score,
                second_layer=second_layer,
                second_layer_score=second_layer_score,
                top_locations=_top_locations(location_counter),
            )
        )
        if end_index == len(chapters):
            break
    return windows


def _aggregate_layer_scores(chapters: list[ChapterProfile], start_index: int, end_index: int) -> Counter[int]:
    totals: Counter[int] = Counter()
    for profile in chapters[start_index:end_index]:
        for layer, score in profile.layer_scores.items():
            totals[layer] += score
    return totals


def _dominant_layer_for_range(chapters: list[ChapterProfile], start_index: int, end_index: int) -> tuple[int | None, float, float]:
    totals = _aggregate_layer_scores(chapters, start_index, end_index)
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    if not ordered:
        return None, 0.0, 0.0
    dominant_layer, dominant_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else 0.0
    if dominant_score < MIN_WINDOW_LAYER_SCORE:
        return None, dominant_score, second_score
    return dominant_layer, dominant_score, second_score


def _choose_initial_layer(chapters: list[ChapterProfile]) -> int | None:
    dominant_layer, dominant_score, _ = _dominant_layer_for_range(chapters, 0, min(len(chapters), WINDOW_SIZE * 2))
    if dominant_layer is not None and dominant_score > 0:
        return dominant_layer
    for profile in chapters[: min(len(chapters), WINDOW_SIZE)]:
        if profile.primary_layer is not None:
            return profile.primary_layer
    return None


def _find_transition_chapter(chapters: list[ChapterProfile], start_index: int, end_index: int, layer: int) -> int:
    for index in range(start_index, min(end_index, len(chapters))):
        if chapters[index].layer_scores.get(layer, 0.0) >= MIN_PRIMARY_LAYER_SCORE:
            return index
    return start_index


def _window_persists_with_layer(windows: list[WindowProfile], start_index: int, layer: int) -> bool:
    future = windows[start_index : start_index + REQUIRED_FUTURE_WINDOWS]
    if len(future) < REQUIRED_FUTURE_WINDOWS:
        return False
    return all(window.dominant_layer == layer for window in future)


def _window_persists_locations(windows: list[WindowProfile], start_index: int, location_names: list[str]) -> list[str]:
    filtered_names = [name for name in location_names if _is_major_location_name(name)]
    if not filtered_names:
        return []
    future = windows[start_index : start_index + REQUIRED_FUTURE_WINDOWS]
    if len(future) < REQUIRED_FUTURE_WINDOWS:
        return []
    persistent: list[str] = []
    for name in filtered_names:
        if all(name in {row["name"] for row in window.top_locations} for window in future):
            persistent.append(name)
    return persistent


def _find_transition_chapter_for_locations(
    chapters: list[ChapterProfile],
    start_index: int,
    end_index: int,
    location_names: list[str],
) -> int:
    if not location_names:
        return start_index
    for index in range(start_index, min(end_index, len(chapters))):
        names = set(chapters[index].location_counter.keys())
        if any(name in names for name in location_names):
            return index
    return start_index


def _location_shift_summary(previous_locations: list[dict[str, Any]], next_locations: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    previous_names = [row["name"] for row in previous_locations]
    next_names = [row["name"] for row in next_locations]
    shared = sorted(set(previous_names).intersection(next_names))
    introduced = [name for name in next_names if name not in previous_names]
    return shared, introduced


def _collect_candidate_reasons(
    *,
    node_start: int,
    node_end: int,
    previous_start: int | None,
    previous_end: int | None,
    chapters: list[ChapterProfile],
    windows: list[WindowProfile],
    dominant_layer: int | None,
    boundary_window_index: int | None,
    boundary_type: str | None,
) -> tuple[str, list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    segment = chapters[node_start : node_end + 1]
    layer_evidence: list[dict[str, Any]] = []
    location_counter: Counter[str] = Counter()
    for profile in segment:
        location_counter.update(profile.location_counter)
        if dominant_layer is not None:
            layer_evidence.extend(profile.layer_evidence.get(dominant_layer, []))

    evidence_rows: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    for profile in segment:
        if dominant_layer is None:
            break
        for row in profile.layer_evidence.get(dominant_layer, []):
            signature = f"{profile.chapter_id}:{row.get('source')}:{row.get('snippet')}"
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            evidence_rows.append(
                {
                    "chapter_id": profile.chapter_id,
                    "chapter_title": profile.chapter_title,
                    "source": row.get("source", ""),
                    "weight": row.get("weight", 0),
                    "snippet": row.get("snippet", ""),
                }
            )
            if len(evidence_rows) >= 6:
                break
        if len(evidence_rows) >= 6:
            break

    top_locations = _top_locations(location_counter, limit=4)
    reasons: list[str] = []
    if dominant_layer is not None:
        reasons.append(
            f"该段聚合层级信号以“{_layer_label(dominant_layer)}”为主，说明当前节点更像稳定驻留在这一地图层级。"
        )
    if boundary_window_index is not None and 0 <= boundary_window_index < len(windows):
        current_window = windows[boundary_window_index]
        if boundary_type == "layer_shift":
            reasons.append(
                f"检测窗口 {current_window.start_chapter}-{current_window.end_chapter} 的主导层级已切换，并在后续窗口保持稳定。"
            )
        elif boundary_type == "location_shift":
            reasons.append(
                f"检测窗口 {current_window.start_chapter}-{current_window.end_chapter} 出现稳定的新地点簇，疑似同层内的大地图切换。"
            )
            reasons.append("这一候选更偏向同层内阶段切段，而不是明确的升层信号，需要你人工确认是否值得单独版本化。")
        if boundary_window_index > 0:
            previous_window = windows[boundary_window_index - 1]
            shared, introduced = _location_shift_summary(previous_window.top_locations, current_window.top_locations)
            if introduced:
                reasons.append(
                    f"窗口高频地点出现明显切换，新地点集中在：{' / '.join(introduced[:3])}。"
                )
            elif shared:
                reasons.append(
                    f"窗口前后仍共享部分地点（{' / '.join(shared[:3])}），因此这里更适合视为保守候选，需要人工确认边界。"
                )
    if top_locations:
        reasons.append(
            f"该节点内高频地点实体包括：{' / '.join(row['name'] for row in top_locations[:3])}。"
        )
    if previous_start is None or previous_end is None:
        reasons.append("这是当前语料的起始段，默认作为第一个候选节点起点。")

    confidence = "low"
    if dominant_layer is not None and layer_evidence:
        total_weight = sum(float(row.get("weight", 0) or 0) for row in layer_evidence)
        if total_weight >= 48:
            confidence = "high"
        elif total_weight >= 24:
            confidence = "medium"
    elif top_locations:
        confidence = "medium"

    if boundary_type == "location_shift" and confidence == "high":
        confidence = "medium"

    return confidence, reasons, evidence_rows, top_locations


def _suggest_node_label(dominant_layer: int | None, top_locations: list[dict[str, Any]], *, fallback_rank: int) -> str:
    if dominant_layer is not None:
        if top_locations:
            return f"{_layer_label(dominant_layer)} / {top_locations[0]['name']}阶段"
        return f"{_layer_label(dominant_layer)}阶段"
    if top_locations:
        joined = " / ".join(row["name"] for row in top_locations[:2])
        return f"{joined}阶段"
    return f"故事节点 {fallback_rank}"


def _suggest_node_id(dominant_layer: int | None, rank: int, start_chapter: str, end_chapter: str) -> str:
    if dominant_layer is not None:
        return f"kunxu_l{dominant_layer}_ch{start_chapter}_{end_chapter}"
    return f"story_node_{rank:02d}_ch{start_chapter}_{end_chapter}"


def _build_candidate_nodes(chapters: list[ChapterProfile], windows: list[WindowProfile]) -> list[dict[str, Any]]:
    if not chapters:
        return []

    boundaries: list[dict[str, Any]] = []
    initial_layer = _choose_initial_layer(chapters)
    current_layer = initial_layer
    for window_index, window in enumerate(windows[1:], start=1):
        if window.dominant_layer is None or window.dominant_layer == current_layer:
            continue
        if not _window_persists_with_layer(windows, window_index, window.dominant_layer):
            continue
        transition_index = _find_transition_chapter(chapters, window.start_index, window.end_index, window.dominant_layer)
        if any(abs(transition_index - boundary["chapter_index"]) < MIN_MAJOR_NODE_GAP for boundary in boundaries):
            continue
        boundaries.append(
            {
                "chapter_index": transition_index,
                "window_index": window_index,
                "layer": window.dominant_layer,
                "type": "layer_shift",
            }
        )
        current_layer = window.dominant_layer

    for window_index, window in enumerate(windows[1:], start=1):
        previous_window = windows[window_index - 1]
        _, introduced = _location_shift_summary(previous_window.top_locations, window.top_locations)
        persistent_locations = _window_persists_locations(windows, window_index, introduced[:3])
        if not persistent_locations:
            continue
        introduced_weight = sum(
            int(row.get("count", 0) or 0)
            for row in window.top_locations
            if row.get("name") in persistent_locations
        )
        transition_index = _find_transition_chapter_for_locations(
            chapters,
            window.start_index,
            window.end_index,
            persistent_locations,
        )
        remaining = len(chapters) - transition_index
        if introduced_weight < 8 or transition_index < MIN_MAJOR_NODE_GAP or remaining < 40:
            continue
        if any(abs(transition_index - boundary["chapter_index"]) < MIN_MAJOR_NODE_GAP for boundary in boundaries):
            continue
        boundaries.append(
            {
                "chapter_index": transition_index,
                "window_index": window_index,
                "layer": window.dominant_layer,
                "type": "location_shift",
            }
        )

    boundaries.sort(key=lambda item: item["chapter_index"])
    start_indices = [0] + [boundary["chapter_index"] for boundary in boundaries]
    candidate_nodes: list[dict[str, Any]] = []
    for rank, start_index in enumerate(start_indices, start=1):
        next_start = start_indices[rank] if rank < len(start_indices) else len(chapters)
        end_index = next_start - 1
        dominant_layer, _, _ = _dominant_layer_for_range(chapters, start_index, next_start)
        boundary_window_index = boundaries[rank - 2]["window_index"] if rank > 1 else None
        boundary_type = boundaries[rank - 2]["type"] if rank > 1 else None
        previous_start = start_indices[rank - 2] if rank > 1 else None
        previous_end = start_index - 1 if rank > 1 else None
        confidence, reasons, evidence_rows, top_locations = _collect_candidate_reasons(
            node_start=start_index,
            node_end=end_index,
            previous_start=previous_start,
            previous_end=previous_end,
            chapters=chapters,
            windows=windows,
            dominant_layer=dominant_layer,
            boundary_window_index=boundary_window_index,
            boundary_type=boundary_type,
        )
        start_chapter = chapters[start_index].chapter_id
        end_chapter = chapters[end_index].chapter_id
        label = _suggest_node_label(dominant_layer, top_locations, fallback_rank=rank)
        candidate_nodes.append(
            {
                "candidate_rank": rank,
                "node_id": _suggest_node_id(dominant_layer, rank, start_chapter, end_chapter),
                "label": label,
                "status": "needs_user_confirmation",
                "confirmation_required": True,
                "start_chapter": start_chapter,
                "end_chapter": end_chapter,
                "dominant_layer": dominant_layer,
                "dominant_layer_label": _layer_label(dominant_layer),
                "confidence": confidence,
                "trigger_type": boundary_type or "initial_segment",
                "top_locations": top_locations,
                "reasons": reasons,
                "evidence": evidence_rows,
                "source_window": (
                    {
                        "start_chapter": windows[boundary_window_index].start_chapter,
                        "end_chapter": windows[boundary_window_index].end_chapter,
                    }
                    if boundary_window_index is not None
                    else {
                        "start_chapter": windows[0].start_chapter,
                        "end_chapter": windows[min(len(windows) - 1, 1)].end_chapter if windows else end_chapter,
                    }
                ),
            }
        )
    return candidate_nodes


def _build_markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# 故事节点候选报告",
        "",
        f"- 生成时间：{result.get('generated_at', '')}",
        f"- 章节总数：{result.get('input_summary', {}).get('chapter_count', 0)}",
        f"- fact 覆盖章节数：{result.get('input_summary', {}).get('fact_chapter_count', 0)}",
        f"- fact 当前最远章节：{result.get('input_summary', {}).get('fact_max_chapter', '') or '未知'}",
        "- 注意：以下均为候选节点，未经过人工确认前，不应直接用于节点化构建。",
        "",
        "## 候选节点总览",
        "",
        "| 候选 | 范围 | 建议标签 | 主导层级 | 置信度 |",
        "|---|---|---|---|---|",
    ]
    for node in result.get("candidate_nodes", []):
        lines.append(
            f"| {node.get('candidate_rank', '')} | {node.get('start_chapter', '')}-{node.get('end_chapter', '')} | "
            f"{node.get('label', '')} | {node.get('dominant_layer_label', '') or '未明确'} | {node.get('confidence', '')} |"
        )

    for node in result.get("candidate_nodes", []):
        lines.extend(
            [
                "",
                f"## 候选 {node.get('candidate_rank', '')}: {node.get('label', '')}",
                "",
                f"- `node_id`: `{node.get('node_id', '')}`",
                f"- 建议范围：`{node.get('start_chapter', '')}-{node.get('end_chapter', '')}`",
                f"- 主导层级：{node.get('dominant_layer_label', '') or '未明确'}",
                f"- 置信度：`{node.get('confidence', '')}`",
                "- 状态：`needs_user_confirmation`",
                "- 理由：",
            ]
        )
        for reason in node.get("reasons", []):
            lines.append(f"  - {reason}")
        lines.append("- 代表证据：")
        evidence_rows = node.get("evidence", [])
        if evidence_rows:
            for row in evidence_rows[:6]:
                lines.append(
                    f"  - 章节 {row.get('chapter_id', '')} / {row.get('source', '')}: {row.get('snippet', '')}"
                )
        else:
            lines.append("  - 当前更多依赖窗口级统计与地点切换信号，缺少足够强的逐章层级证据。")
        top_locations = node.get("top_locations", [])
        if top_locations:
            frequency_text = " / ".join(f"{row['name']}({row['count']})" for row in top_locations[:4])
            lines.append(f"- 高频地点：{frequency_text}")

    lines.extend(
        [
            "",
            "## 使用说明",
            "",
            "- 系统已经同时生成 `story_nodes_confirmed.json` 模板文件。",
            "- 只有当目标节点被你明确标记为 `selected=true` 且 `status=confirmed` 之后，`build-canon --story-nodes --node-id ...` 才会放行。",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_confirmation_template(result: dict[str, Any], *, candidates_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "manifest_type": "story_nodes_confirmation",
        "generated_at": _utc_now(),
        "source_candidates_file": str(candidates_path.resolve()),
        "confirmation_status": "pending_user_confirmation",
        "confirmed_by": "",
        "confirmed_at": "",
        "confirmation_required": True,
        "nodes": [
            {
                "node_id": node.get("node_id", ""),
                "label": node.get("label", ""),
                "start_chapter": node.get("start_chapter", ""),
                "end_chapter": node.get("end_chapter", ""),
                "dominant_layer": node.get("dominant_layer"),
                "dominant_layer_label": node.get("dominant_layer_label", ""),
                "selected": False,
                "status": "pending",
                "user_notes": "",
                "reasons": node.get("reasons", []),
            }
            for node in result.get("candidate_nodes", [])
        ],
    }


def detect_story_nodes(chapters_dir: str | Path, facts_dir: str | Path | None, output_dir: str | Path) -> dict[str, Any]:
    chapters_path = Path(chapters_dir).resolve()
    facts_path = Path(facts_dir).resolve() if facts_dir else None
    output_path = ensure_dir(output_dir).resolve()

    chapters, fact_summary = _build_chapter_profiles(chapters_path, facts_path)
    if not chapters:
        raise FileNotFoundError(f"No chapter files found in {chapters_path}")
    windows = _build_window_profiles(chapters)
    candidate_nodes = _build_candidate_nodes(chapters, windows)

    result = {
        "schema_version": 1,
        "manifest_type": "story_node_candidates",
        "generated_at": _utc_now(),
        "confirmation_required": True,
        "input_summary": {
            "chapters_dir": str(chapters_path),
            "facts_dir": str(facts_path) if facts_path else "",
            "chapter_count": len(chapters),
            "first_chapter": chapters[0].chapter_id,
            "last_chapter": chapters[-1].chapter_id,
            "fact_scene_file_count": fact_summary.get("scene_file_count", 0),
            "fact_chapter_count": fact_summary.get("chapter_count", 0),
            "fact_max_chapter": fact_summary.get("max_fact_chapter", ""),
        },
        "detection_parameters": {
            "window_size": WINDOW_SIZE,
            "window_step": WINDOW_STEP,
            "max_valid_layer": MAX_VALID_LAYER,
            "notes": [
                "候选边界基于章节正文 + fact 抽取结果中的层级信号。",
                "build-canon 的节点化运行必须使用已确认 manifest，候选 manifest 不能直接放行。",
                "当前 fact 若未跑完整，后半程节点会更多依赖章节正文，置信度可能偏保守。",
            ],
        },
        "candidate_nodes": candidate_nodes,
    }

    candidates_path = output_path / "story_node_candidates.json"
    report_path = output_path / "story_node_candidates.md"
    confirmed_template_path = output_path / "story_nodes_confirmed.json"
    write_json(candidates_path, result)
    write_markdown(report_path, _build_markdown_report(result))
    write_json(
        confirmed_template_path,
        _build_confirmation_template(result, candidates_path=candidates_path),
    )
    return {
        **result,
        "paths": {
            "candidates_json": str(candidates_path),
            "candidates_markdown": str(report_path),
            "confirmed_template_json": str(confirmed_template_path),
        },
    }


def _load_story_node_manifest(path: str | Path) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Story node manifest must be a JSON object: {Path(path).resolve()}")
    return payload


def load_confirmed_story_node(manifest_path: str | Path, node_id: str) -> dict[str, Any]:
    manifest = _load_story_node_manifest(manifest_path)
    nodes = manifest.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError(f"Story node manifest is missing a valid 'nodes' list: {Path(manifest_path).resolve()}")

    for node in nodes:
        if not isinstance(node, dict):
            continue
        if _clean_text(node.get("node_id")) != _clean_text(node_id):
            continue
        is_selected = bool(node.get("selected"))
        status = _clean_text(node.get("status")).lower()
        if not is_selected or status != "confirmed":
            raise ValueError(
                f"Story node '{node_id}' is not confirmed yet. "
                f"Please set selected=true and status=confirmed in {Path(manifest_path).resolve()} after review."
            )
        return {
            "node_id": _clean_text(node.get("node_id")),
            "label": _clean_text(node.get("label")),
            "start_chapter": _clean_text(node.get("start_chapter")),
            "end_chapter": _clean_text(node.get("end_chapter")),
            "dominant_layer": node.get("dominant_layer"),
            "dominant_layer_label": _clean_text(node.get("dominant_layer_label")),
            "manifest_path": str(Path(manifest_path).resolve()),
            "user_notes": _clean_text(node.get("user_notes")),
        }
    raise KeyError(f"Story node '{node_id}' was not found in {Path(manifest_path).resolve()}")


def chapter_in_range(chapter_id: str, start_chapter: str, end_chapter: str) -> bool:
    return _chapter_sort_key(start_chapter) <= _chapter_sort_key(chapter_id) <= _chapter_sort_key(end_chapter)
