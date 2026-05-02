from __future__ import annotations

import re

from novel_pipeline_stable.source_text_cleanup import is_source_watermark_line, strip_source_site_noise


EMBEDDED_TITLE_PATTERN = re.compile(
    r"(?m)^\s*第\s*[0-9一二三四五六七八九十百千万零两〇]+\s*章[^\n\r]*$"
)
END_MARKER_PATTERN = re.compile(r"(?m)^\s*\((?:本章完|完)\)\s*$")

ACTIVITY_LINE_PATTERNS = [
    re.compile(r"^\s*预告[:：].*月票抽奖"),
    re.compile(r"^\s*活动页面.*?(?:投票|打赏|抽奖)"),
    re.compile(r"^\s*抽奖活动时间[:：]"),
    re.compile(r"^\s*活动时间[:：].*月票"),
    re.compile(r"^\s*活动奖品"),
    re.compile(r"^\s*奖品[:：]"),
    re.compile(r"^\s*中奖信息"),
    re.compile(r"^\s*领奖时"),
    re.compile(r"^\s*\d+号开奖"),
    re.compile(r"^\s*加群方式[:：]"),
    re.compile(r"^\s*QQ群"),
    re.compile(r"^\s*粉丝称号将用"),
    re.compile(r"^\s*小剧场[:：].*(?:抽奖|活动)"),
    re.compile(r".*书评区.*(?:公示帖|置顶).*"),
    re.compile(r".*(?:投月票|月票号|多投多得).*"),
    re.compile(r".*(?:截止领奖|领奖登记|过期不候).*"),
    re.compile(r".*(?:起点客户端|起点app|活动规则页面|活动中心).*"),
    re.compile(r".*月票贷.*"),
    re.compile(r".*月票.*借.*"),
    re.compile(r".*月票兑奖.*"),
    re.compile(r".*书友圈置顶.*"),
    re.compile(r".*(?:起点|诸神之战).*(?:提名|自助餐|纪念月票|投喂).*"),
]

ACTIVITY_INLINE_PATTERNS = [
    re.compile(r"月票抽奖中奖的.*?(?:登记|公示帖).*"),
    re.compile(r"和前20保底的尽快查看书评区置顶的公示帖.*"),
]

META_TITLE_PATTERNS = [
    re.compile(r"上架活动"),
    re.compile(r"福利"),
    re.compile(r"月票抽奖"),
    re.compile(r"活动预告"),
]
META_BODY_KEYWORDS = [
    "活动页面",
    "投票",
    "打赏",
    "点币",
    "月票",
    "保底更新",
    "上架",
]
ACTIVITY_BLOCK_KEYWORDS = [
    "投月票",
    "月票号",
    "领奖",
    "开奖",
    "书评区",
    "公示帖",
    "起点",
    "绝育王",
    "QQ群",
    "活动奖品",
    "抽奖活动时间",
]


def sanitize_chapter_content(title: str, body: str) -> str:
    text = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    text, _ = strip_source_site_noise(text)
    text = _strip_after_end_marker(text)
    text = _strip_embedded_next_title(text)
    text = _strip_trailing_activity_blocks(text)
    text = _strip_inline_activity_fragments(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def should_skip_meta_chapter(title: str, body: str) -> bool:
    if not title.strip():
        return False

    if not any(pattern.search(title) for pattern in META_TITLE_PATTERNS):
        return False

    meta_hits = sum(1 for keyword in META_BODY_KEYWORDS if keyword in body)
    return meta_hits >= 2 or is_activity_block(body.splitlines())


def is_activity_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if is_source_watermark_line(stripped):
        return True
    return any(pattern.search(stripped) for pattern in ACTIVITY_LINE_PATTERNS)


def _strip_after_end_marker(text: str) -> str:
    match = END_MARKER_PATTERN.search(text)
    if not match:
        return text
    return text[: match.start()].strip()


def _strip_trailing_activity_blocks(text: str) -> str:
    lines = text.split("\n")
    while True:
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return ""

        split_index = len(lines)
        while split_index > 0 and lines[split_index - 1].strip():
            split_index -= 1
        candidate = lines[split_index:]
        non_empty = [line for line in candidate if line.strip()]
        if split_index > 0 and non_empty and len(non_empty) >= 2 and is_activity_block(non_empty):
            lines = lines[:split_index]
            continue
        break
    return "\n".join(lines).strip()


def _strip_embedded_next_title(text: str) -> str:
    match = EMBEDDED_TITLE_PATTERN.search(text)
    if not match:
        return text
    return text[: match.start()].strip()


def _strip_inline_activity_fragments(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        updated = line
        for pattern in ACTIVITY_INLINE_PATTERNS:
            updated = pattern.sub("", updated)
        cleaned_lines.append(updated.rstrip())
    return "\n".join(cleaned_lines).strip()


def is_activity_block(lines: list[str]) -> bool:
    non_empty = [line.strip() for line in lines if line.strip()]
    if len(non_empty) < 2:
        return False

    joined = "\n".join(non_empty)
    keyword_hits = sum(1 for keyword in ACTIVITY_BLOCK_KEYWORDS if keyword in joined)
    line_hits = sum(1 for line in non_empty if is_activity_line(line))
    return keyword_hits >= 2 or line_hits >= max(2, len(non_empty) - 1)


