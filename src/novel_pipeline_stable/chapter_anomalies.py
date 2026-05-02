from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from novel_pipeline_stable.chapter_cleanup import is_activity_line


CHAPTER_FILE_PATTERN = re.compile(r"chapter_(\d+)\.txt$", re.IGNORECASE)
TITLE_NUMBER_PATTERN = re.compile(r"第\s*([0-9一二三四五六七八九十百千万零两〇]+)\s*章")
EMBEDDED_TITLE_PATTERN = re.compile(
    r"(?m)^\s*第\s*[0-9一二三四五六七八九十百千万零两〇]+\s*章[^\n\r]*$"
)
END_MARKER_PATTERN = re.compile(r"(?m)^\s*\((?:本章完|完)\)\s*$")

TITLE_META_PATTERNS = [
    re.compile(r"还月票贷"),
    re.compile(r"月票贷"),
    re.compile(r"求月票"),
    re.compile(r"活动"),
    re.compile(r"福利"),
]

ACTIVITY_RESIDUE_PATTERNS = [
    re.compile(r"月票贷"),
    re.compile(r"诸神之战"),
    re.compile(r"专属纪念月票"),
    re.compile(r"帮忙点个提名"),
    re.compile(r"书友圈置顶"),
    re.compile(r"月票兑奖"),
    re.compile(r"继续借月票"),
    re.compile(r"努力还月票贷"),
    re.compile(r"投喂点.*月票"),
]

CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
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
CHINESE_UNITS = {
    "十": 10,
    "百": 100,
    "千": 1000,
    "万": 10000,
}


def scan_chapter_anomalies(
    input_dir: str | Path,
    *,
    short_body_threshold: int = 120,
) -> dict[str, Any]:
    base = Path(input_dir)
    records: list[dict[str, Any]] = []

    title_groups: dict[str, list[str]] = defaultdict(list)
    body_hash_groups: dict[str, list[str]] = defaultdict(list)
    title_number_groups: dict[int, list[str]] = defaultdict(list)

    empty_files: list[str] = []
    title_only_files: list[dict[str, Any]] = []
    short_body_files: list[dict[str, Any]] = []
    embedded_title_files: list[dict[str, Any]] = []
    end_marker_files: list[dict[str, Any]] = []
    meta_title_files: list[dict[str, Any]] = []
    activity_residue_files: list[dict[str, Any]] = []
    missing_title_number_files: list[dict[str, Any]] = []

    for path in sorted(base.glob("chapter_*.txt")):
        record = _analyze_chapter_file(path, short_body_threshold=short_body_threshold)
        records.append(record)

        if record["is_empty"]:
            empty_files.append(path.name)
            continue

        title_groups[record["normalized_title"]].append(path.name)
        title_number = record["title_number"]
        if title_number is None:
            missing_title_number_files.append(
                {"file": path.name, "title": record["title"]}
            )
        else:
            title_number_groups[title_number].append(path.name)

        if record["body_hash"]:
            body_hash_groups[record["body_hash"]].append(path.name)

        if record["is_title_only"]:
            title_only_files.append({"file": path.name, "title": record["title"]})
        if record["is_short_body"]:
            short_body_files.append(
                {
                    "file": path.name,
                    "title": record["title"],
                    "body_char_count": record["body_char_count"],
                }
            )
        if record["embedded_titles"]:
            embedded_title_files.append(
                {
                    "file": path.name,
                    "title": record["title"],
                    "embedded_titles": record["embedded_titles"],
                }
            )
        if record["has_end_marker"]:
            end_marker_files.append({"file": path.name, "title": record["title"]})
        if record["title_meta_markers"]:
            meta_title_files.append(
                {
                    "file": path.name,
                    "title": record["title"],
                    "title_meta_markers": record["title_meta_markers"],
                }
            )
        if record["activity_residue_lines"]:
            activity_residue_files.append(
                {
                    "file": path.name,
                    "title": record["title"],
                    "activity_residue_lines": record["activity_residue_lines"],
                }
            )

    title_duplicates = [
        {"normalized_title": title, "files": files}
        for title, files in title_groups.items()
        if len(files) > 1
    ]
    body_duplicates = [
        {
            "files": files,
            "titles": [
                next(record["title"] for record in records if record["file"] == file_name)
                for file_name in files
            ],
        }
        for body_hash, files in body_hash_groups.items()
        if body_hash and len(files) > 1
    ]

    sequence_issues = _scan_title_number_sequence(records)
    duplicate_title_numbers = [
        {"title_number": number, "files": files}
        for number, files in sorted(title_number_groups.items())
        if len(files) > 1
    ]

    return {
        "input_dir": str(base.resolve()),
        "file_count": len(records),
        "short_body_threshold": short_body_threshold,
        "summary": {
            "empty_files": len(empty_files),
            "title_only_files": len(title_only_files),
            "short_body_files": len(short_body_files),
            "embedded_title_files": len(embedded_title_files),
            "end_marker_files": len(end_marker_files),
            "meta_title_files": len(meta_title_files),
            "activity_residue_files": len(activity_residue_files),
            "duplicate_titles": len(title_duplicates),
            "duplicate_title_numbers": len(duplicate_title_numbers),
            "duplicate_bodies": len(body_duplicates),
            "missing_title_number_files": len(missing_title_number_files),
            "sequence_gaps": len(sequence_issues["gaps"]),
            "sequence_duplicates": len(sequence_issues["duplicates"]),
            "sequence_regressions": len(sequence_issues["regressions"]),
        },
        "empty_files": empty_files,
        "title_only_files": title_only_files,
        "short_body_files": short_body_files,
        "embedded_title_files": embedded_title_files,
        "end_marker_files": end_marker_files,
        "meta_title_files": meta_title_files,
        "activity_residue_files": activity_residue_files,
        "missing_title_number_files": missing_title_number_files,
        "duplicate_titles": title_duplicates,
        "duplicate_title_numbers": duplicate_title_numbers,
        "duplicate_bodies": body_duplicates,
        "sequence_issues": sequence_issues,
        "records": records,
    }


def _analyze_chapter_file(path: Path, *, short_body_threshold: int) -> dict[str, Any]:
    raw_text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    text = raw_text.strip("\n")
    lines = text.split("\n") if text else []
    non_empty = [line.strip() for line in lines if line.strip()]

    file_match = CHAPTER_FILE_PATTERN.search(path.name)
    file_index = int(file_match.group(1)) if file_match else None

    if not non_empty:
        return {
            "file": path.name,
            "file_index": file_index,
            "title": "",
            "normalized_title": "",
            "title_number": None,
            "body_char_count": 0,
            "line_count": len(lines),
            "is_empty": True,
            "is_title_only": False,
            "is_short_body": False,
            "has_end_marker": False,
            "embedded_titles": [],
            "noise_keyword_hits": {},
            "body_hash": "",
        }

    title = non_empty[0]
    title_index = lines.index(next(line for line in lines if line.strip()))
    body = "\n".join(lines[title_index + 1 :]).strip()
    embedded_titles = [
        match.group(0).strip()
        for match in EMBEDDED_TITLE_PATTERN.finditer(body)
        if match.group(0).strip() and match.group(0).strip() != title.strip()
    ]
    normalized_body = _normalize_body(body)
    title_meta_markers = [
        pattern.pattern for pattern in TITLE_META_PATTERNS if pattern.search(title)
    ]
    activity_residue_lines = []
    for line_number, line in enumerate(lines[title_index + 1 :], start=title_index + 2):
        stripped = line.strip()
        if not stripped:
            continue
        if is_activity_line(stripped) or any(
            pattern.search(stripped) for pattern in ACTIVITY_RESIDUE_PATTERNS
        ):
            activity_residue_lines.append(
                {
                    "line_number": line_number,
                    "line": stripped,
                }
            )

    return {
        "file": path.name,
        "file_index": file_index,
        "title": title,
        "normalized_title": _normalize_title(title),
        "title_number": _extract_title_number(title),
        "body_char_count": len(normalized_body),
        "line_count": len(lines),
        "is_empty": False,
        "is_title_only": not bool(normalized_body),
        "is_short_body": bool(normalized_body) and len(normalized_body) < short_body_threshold,
        "has_end_marker": bool(END_MARKER_PATTERN.search(body)),
        "embedded_titles": embedded_titles,
        "title_meta_markers": title_meta_markers,
        "activity_residue_lines": activity_residue_lines,
        "body_hash": hashlib.md5(normalized_body.encode("utf-8")).hexdigest() if normalized_body else "",
    }


def _scan_title_number_sequence(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    gaps: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []

    numbered_records = [record for record in records if record["title_number"] is not None]
    for previous, current in zip(numbered_records, numbered_records[1:]):
        prev_num = previous["title_number"]
        curr_num = current["title_number"]
        if curr_num == prev_num:
            duplicates.append(
                {
                    "previous_file": previous["file"],
                    "current_file": current["file"],
                    "title_number": curr_num,
                    "previous_title": previous["title"],
                    "current_title": current["title"],
                }
            )
        elif curr_num < prev_num:
            regressions.append(
                {
                    "previous_file": previous["file"],
                    "current_file": current["file"],
                    "previous_title_number": prev_num,
                    "current_title_number": curr_num,
                    "previous_title": previous["title"],
                    "current_title": current["title"],
                }
            )
        elif curr_num > prev_num + 1:
            gaps.append(
                {
                    "previous_file": previous["file"],
                    "current_file": current["file"],
                    "after_title_number": prev_num,
                    "before_title_number": curr_num,
                    "missing_title_numbers": list(range(prev_num + 1, curr_num)),
                    "previous_title": previous["title"],
                    "current_title": current["title"],
                }
            )

    return {
        "gaps": gaps,
        "duplicates": duplicates,
        "regressions": regressions,
    }


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", title).strip()


def _normalize_body(body: str) -> str:
    return re.sub(r"\s+", "", body).strip()


def _extract_title_number(title: str) -> int | None:
    match = TITLE_NUMBER_PATTERN.search(title)
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        return int(token)
    return _chinese_number_to_int(token)


def _chinese_number_to_int(token: str) -> int | None:
    if not token:
        return None

    total = 0
    section = 0
    number = 0

    for char in token:
        if char in CHINESE_DIGITS:
            number = CHINESE_DIGITS[char]
            continue

        unit = CHINESE_UNITS.get(char)
        if unit is None:
            return None

        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
            continue

        if number == 0:
            number = 1
        section += number * unit
        number = 0

    return total + section + number


