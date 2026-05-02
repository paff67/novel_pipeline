from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_pipeline_stable.source_text_cleanup import strip_source_site_noise


LATIN_TOKEN_PATTERN = re.compile(r"[A-Za-z]{2,}")
GG_PATTERN = re.compile(r"(?<![A-Za-z])GG(?![A-Za-z])")

# Only place high-confidence, human-reviewed replacements here.
# These rules are applied to the model-facing text, not the stored source corpus.
HIGH_CONFIDENCE_REPLACEMENTS: list[tuple[str, re.Pattern[str], str]] = [
    ("gg_to_ad", GG_PATTERN, "广告"),
]


@dataclass
class AppliedNormalization:
    rule_id: str
    original: str
    replacement: str
    count: int


@dataclass
class NormalizedText:
    text: str
    applied: list[AppliedNormalization]


def normalize_for_extraction(text: str) -> NormalizedText:
    normalized, source_noise_stats = strip_source_site_noise(text)
    applied: list[AppliedNormalization] = []

    if source_noise_stats.total_removed_count:
        applied.append(
            AppliedNormalization(
                rule_id="source_site_watermark_cleanup",
                original="source_watermark_patterns",
                replacement="",
                count=source_noise_stats.total_removed_count,
            )
        )

    for rule_id, pattern, replacement in HIGH_CONFIDENCE_REPLACEMENTS:
        matches = len(pattern.findall(normalized))
        if not matches:
            continue
        normalized = pattern.sub(replacement, normalized)
        applied.append(
            AppliedNormalization(
                rule_id=rule_id,
                original=pattern.pattern,
                replacement=replacement,
                count=matches,
            )
        )

    return NormalizedText(text=normalized, applied=applied)


def scan_suspicious_tokens(
    input_dir: str | Path,
    *,
    top: int = 100,
    sample_limit: int = 5,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for path in sorted(Path(input_dir).glob("chapter_*.txt")):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in LATIN_TOKEN_PATTERN.finditer(line):
                token = match.group(0)
                if not _touches_cjk(line, match.start(), match.end()):
                    continue

                counts[token] += 1
                if len(samples[token]) < sample_limit:
                    samples[token].append(
                        {
                            "file": path.name,
                            "line_number": line_number,
                            "line": line.strip(),
                        }
                    )

    token_rows = []
    for token, count in counts.most_common(top):
        token_rows.append(
            {
                "token": token,
                "count": count,
                "normalized_by_default": any(token == _token_from_pattern(pattern) for _, pattern, _ in HIGH_CONFIDENCE_REPLACEMENTS),
                "samples": samples[token],
            }
        )

    return {
        "input_dir": str(Path(input_dir).resolve()),
        "top": top,
        "sample_limit": sample_limit,
        "tokens": token_rows,
    }


def _touches_cjk(text: str, start: int, end: int) -> bool:
    left = text[start - 1] if start > 0 else ""
    right = text[end] if end < len(text) else ""
    return _is_cjk(left) or _is_cjk(right)


def _is_cjk(char: str) -> bool:
    return bool(char) and "\u4e00" <= char <= "\u9fff"


def _token_from_pattern(pattern: re.Pattern[str]) -> str:
    if pattern.pattern == GG_PATTERN.pattern:
        return "GG"
    return pattern.pattern

