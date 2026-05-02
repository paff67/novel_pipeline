from __future__ import annotations

import re
from dataclasses import dataclass


SOURCE_WATERMARK_PROMO_KEYWORDS = (
    "本书首发",
    "全手打无错站",
    "读小说就上",
    "看书网伴你闲",
    "读好书选",
    "书海量",
    "超好用",
)
SOURCE_WATERMARK_TAIL_KEYWORDS = (
    "超方便",
    "超顺畅",
    "超赞",
    "任你挑",
    "随时享",
)
SOURCE_WATERMARK_LINE_PATTERNS = [
    re.compile(r"^\s*本书首发[^\n\r]{0,160}$"),
    re.compile(r"^\s*(?:本书首发\s*)?(?:读小说就上|看书网伴你闲|读好书选|书海量|超好用)[^\n\r]{0,160}$"),
    re.compile(r"^\s*[0-9①②③④⑤⑥⑦⑧⑨⑩?.,， ]{6,}[^\n\r]{0,120}全手打无错站\s*$"),
]
SOURCE_WATERMARK_INLINE_PATTERNS = [
    re.compile(r"本书首发[^\n\r]{0,120}(?:全手打无错站|超方便|超顺畅|超赞|任你挑|随时享|,\s*的)"),
    re.compile(r"(?:读小说就上|看书网伴你闲|读好书选|书海量|超好用)[^\n\r]{0,120}(?:全手打无错站|超方便|超顺畅|超赞|任你挑|随时享)"),
]


@dataclass(frozen=True, slots=True)
class SourceNoiseCleanupStats:
    removed_line_count: int = 0
    removed_fragment_count: int = 0

    @property
    def total_removed_count(self) -> int:
        return int(self.removed_line_count + self.removed_fragment_count)


def is_source_watermark_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if "全手打无错站" in stripped:
        return True
    if "本书首发" in stripped and ("?" in stripped or "①" in stripped or "101" in stripped):
        return True
    if any(keyword in stripped for keyword in SOURCE_WATERMARK_PROMO_KEYWORDS):
        if "?" in stripped or any(keyword in stripped for keyword in SOURCE_WATERMARK_TAIL_KEYWORDS):
            return True
    return any(pattern.search(stripped) for pattern in SOURCE_WATERMARK_LINE_PATTERNS)


def has_source_site_noise(text: str) -> bool:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return False
    if any(keyword in normalized for keyword in SOURCE_WATERMARK_PROMO_KEYWORDS):
        return True
    if any(keyword in normalized for keyword in SOURCE_WATERMARK_TAIL_KEYWORDS):
        return True
    return any(is_source_watermark_line(line) for line in normalized.split("\n"))


def strip_source_site_noise(text: str) -> tuple[str, SourceNoiseCleanupStats]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized or not has_source_site_noise(normalized):
        return normalized, SourceNoiseCleanupStats()

    kept_lines: list[str] = []
    removed_line_count = 0
    for line in normalized.split("\n"):
        if is_source_watermark_line(line):
            removed_line_count += 1
            continue
        kept_lines.append(line)

    cleaned = "\n".join(kept_lines)
    removed_fragment_count = 0
    for pattern in SOURCE_WATERMARK_INLINE_PATTERNS:
        cleaned, count = pattern.subn("", cleaned)
        removed_fragment_count += count

    if removed_line_count <= 0 and removed_fragment_count <= 0:
        return normalized, SourceNoiseCleanupStats()

    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, SourceNoiseCleanupStats(
        removed_line_count=removed_line_count,
        removed_fragment_count=removed_fragment_count,
    )
