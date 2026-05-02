from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_DOMAIN_VOCABULARY_FILE = "project_domain_vocabulary.toml"


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _config_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config"


def default_project_domain_vocabulary_path() -> Path:
    return (_config_dir() / DEFAULT_PROJECT_DOMAIN_VOCABULARY_FILE).resolve()


def _cleaned_unique(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        rows.append(cleaned)
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class VocabularyEntry:
    entry_id: str
    cue: str = ""
    canonical_description: str = ""
    terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectDomainVocabulary:
    source_path: Path
    version: str
    axis_vocabulary: dict[str, VocabularyEntry]
    bucket_vocabulary: dict[str, VocabularyEntry]
    route_cues: dict[str, tuple[str, ...]]
    signal_keyword_sets: dict[str, tuple[str, ...]]
    generic_patterns: tuple[str, ...]
    safe_cues: tuple[str, ...]
    mechanism_prototypes: dict[str, tuple[str, ...]]
    anti_stuffing_vocabulary: tuple[str, ...]

    def axis_terms(self, axis_id: str) -> tuple[str, ...]:
        return self.axis_vocabulary.get(_clean_text(axis_id), VocabularyEntry("")).terms

    def bucket_terms(self, bucket_id: str) -> tuple[str, ...]:
        return self.bucket_vocabulary.get(_clean_text(bucket_id), VocabularyEntry("")).terms

    def route_terms(self, lane: str) -> tuple[str, ...]:
        return self.route_cues.get(_clean_text(lane), ())

    def signal_terms(self, signal_id: str) -> tuple[str, ...]:
        return self.signal_keyword_sets.get(_clean_text(signal_id), ())


def _load_entry_map(payload: Any) -> dict[str, VocabularyEntry]:
    rows = payload if isinstance(payload, dict) else {}
    entries: dict[str, VocabularyEntry] = {}
    for entry_id, row in rows.items():
        normalized_id = _clean_text(entry_id)
        if not normalized_id:
            continue
        item = row if isinstance(row, dict) else {}
        entries[normalized_id] = VocabularyEntry(
            entry_id=normalized_id,
            cue=_clean_text(item.get("cue")),
            canonical_description=_clean_text(item.get("canonical_description")),
            terms=_cleaned_unique(item.get("terms", [])),
        )
    return entries


def _load_term_map(payload: Any) -> dict[str, tuple[str, ...]]:
    rows = payload if isinstance(payload, dict) else {}
    result: dict[str, tuple[str, ...]] = {}
    for key, value in rows.items():
        normalized_key = _clean_text(key)
        if not normalized_key:
            continue
        if isinstance(value, dict):
            result[normalized_key] = _cleaned_unique(value.get("terms", []))
            continue
        result[normalized_key] = _cleaned_unique(value)
    return result


def load_project_domain_vocabulary(path: str | Path | None = None) -> ProjectDomainVocabulary:
    target = Path(path).resolve() if path else default_project_domain_vocabulary_path()
    if not target.exists():
        raise FileNotFoundError(f"Project domain vocabulary config not found: {target}")

    payload = tomllib.loads(target.read_text(encoding="utf-8-sig"))
    meta = payload.get("meta", {})
    return ProjectDomainVocabulary(
        source_path=target,
        version=_clean_text(meta.get("version")) or "project-domain-vocabulary-v1",
        axis_vocabulary=_load_entry_map(payload.get("axes", {})),
        bucket_vocabulary=_load_entry_map(payload.get("buckets", {})),
        route_cues=_load_term_map(payload.get("route_cues", {})),
        signal_keyword_sets=_load_term_map(payload.get("signals", {})),
        generic_patterns=_cleaned_unique((payload.get("generic_patterns") or {}).get("terms", [])),
        safe_cues=_cleaned_unique((payload.get("safe_cues") or {}).get("terms", [])),
        mechanism_prototypes=_load_term_map(payload.get("mechanism_prototypes", {})),
        anti_stuffing_vocabulary=_cleaned_unique((payload.get("anti_stuffing") or {}).get("terms", [])),
    )


__all__ = [
    "DEFAULT_PROJECT_DOMAIN_VOCABULARY_FILE",
    "ProjectDomainVocabulary",
    "VocabularyEntry",
    "default_project_domain_vocabulary_path",
    "load_project_domain_vocabulary",
]
