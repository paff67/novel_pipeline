from __future__ import annotations

import re
from pathlib import Path

from novel_pipeline_stable.chapter_cleanup import sanitize_chapter_content, should_skip_meta_chapter
from novel_pipeline_stable.config import ProjectConfig
from novel_pipeline_stable.io_utils import clear_matching, ensure_dir, iter_text_files, write_json, write_model
from novel_pipeline_stable.models import ChapterDocument, SceneDocument
from novel_pipeline_stable.monitoring import RunTracker


CHAPTER_FILE_PATTERN = re.compile(r"chapter_(\d+)\.txt$")
SENTENCE_PATTERN = re.compile(r".+?(?:[\u3002\uFF01\uFF1F!?\uFF1B;](?:[\u201D\u300D\u300F\u3011\uFF09\"]*)|$)")


def load_chapter_document(path: str | Path) -> ChapterDocument:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    lines = [line.rstrip() for line in text.splitlines()]
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        raise ValueError(f"Chapter file is empty: {file_path}")
    title = non_empty[0].strip()
    body = "\n".join(lines[lines.index(non_empty[0]) + 1 :]).strip()
    body = sanitize_chapter_content(title, body)
    match = CHAPTER_FILE_PATTERN.search(file_path.name)
    chapter_id = match.group(1) if match else file_path.stem
    return ChapterDocument(
        chapter_id=chapter_id,
        title=title,
        text=body,
        source_file=str(file_path),
    )


def split_chapter_into_scenes(chapter: ChapterDocument, config: ProjectConfig) -> list[SceneDocument]:
    min_chars = config.scene_split.min_chars
    target_chars = config.scene_split.target_chars
    max_chars = config.scene_split.max_chars
    paragraphs = _prepare_paragraphs(chapter.text, max_chars)

    scenes: list[SceneDocument] = []
    current: list[str] = []
    current_len = 0
    scene_index = 1

    for paragraph in paragraphs:
        paragraph_len = len(paragraph)
        should_flush = False
        if current and current_len >= min_chars and current_len + paragraph_len > target_chars:
            should_flush = True
        if current and current_len + paragraph_len > max_chars:
            should_flush = True

        if should_flush:
            scene_text = "\n\n".join(current).strip()
            scenes.append(
                SceneDocument(
                    chapter_id=chapter.chapter_id,
                    chapter_title=chapter.title,
                    scene_id=f"{chapter.chapter_id}_{scene_index:03d}",
                    scene_index=scene_index,
                    text=scene_text,
                    char_count=len(scene_text),
                    source_file=chapter.source_file,
                )
            )
            scene_index += 1
            current = []
            current_len = 0

        current.append(paragraph)
        current_len += paragraph_len

    if current:
        scene_text = "\n\n".join(current).strip()
        scenes.append(
            SceneDocument(
                chapter_id=chapter.chapter_id,
                chapter_title=chapter.title,
                scene_id=f"{chapter.chapter_id}_{scene_index:03d}",
                scene_index=scene_index,
                text=scene_text,
                char_count=len(scene_text),
                source_file=chapter.source_file,
            )
        )

    return scenes


def _prepare_paragraphs(text: str, max_chars: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [line.strip() for line in text.splitlines() if line.strip()]

    prepared: list[str] = []
    for paragraph in paragraphs:
        prepared.extend(_split_oversized_paragraph(paragraph, max_chars))
    return prepared


def _split_oversized_paragraph(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]

    sentence_candidates = [item.strip() for item in SENTENCE_PATTERN.findall(paragraph) if item.strip()]
    if len(sentence_candidates) <= 1:
        return _hard_split_text(paragraph, max_chars)

    chunks: list[str] = []
    current = ""
    for sentence in sentence_candidates:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_hard_split_text(sentence, max_chars))
            continue

        candidate = f"{current}{sentence}" if current else sentence
        if current and len(candidate) > max_chars:
            chunks.append(current.strip())
            current = sentence
            continue

        current = candidate

    if current:
        chunks.append(current.strip())

    return [chunk for chunk in chunks if chunk]


def _hard_split_text(text: str, max_chars: int) -> list[str]:
    return [
        text[index : index + max_chars].strip()
        for index in range(0, len(text), max_chars)
        if text[index : index + max_chars].strip()
    ]



def run_scene_split(config: ProjectConfig, input_dir: str | Path, output_dir: str | Path, *, clear: bool = False) -> None:
    input_path = Path(input_dir)
    output_path = ensure_dir(output_dir)
    if clear:
        clear_matching(output_path, "scene_*.json")
        for file_name in ("manifest.json", "run_status.json", "run_log.jsonl", "failures.json"):
            target = output_path / file_name
            if target.exists() and target.is_file():
                target.unlink()
    chapter_files = list(iter_text_files(input_path))
    tracker = RunTracker(
        stage="split-scenes",
        output_dir=output_path,
        total_items=len(chapter_files),
        item_label="chapter",
        source_dir=input_path,
        metadata={
            "scene_split": {
                "min_chars": config.scene_split.min_chars,
                "target_chars": config.scene_split.target_chars,
                "max_chars": config.scene_split.max_chars,
            }
        },
    )
    manifest: list[dict] = []
    generated_scene_count = 0

    try:
        for chapter_file in chapter_files:
            chapter = load_chapter_document(chapter_file)
            if should_skip_meta_chapter(chapter.title, chapter.text):
                tracker.record_skip(chapter_file.name, f"Skipped meta chapter {chapter_file.name}.", chapter_id=chapter.chapter_id)
                print(f"[split-scenes] skip meta chapter {chapter_file.name}")
                continue
            if not chapter.text.strip():
                tracker.record_skip(chapter_file.name, f"Skipped empty chapter {chapter_file.name}.", chapter_id=chapter.chapter_id)
                print(f"[split-scenes] skip empty chapter {chapter_file.name}")
                continue

            scenes = split_chapter_into_scenes(chapter, config)
            for scene in scenes:
                scene_path = output_path / f"scene_{scene.chapter_id}_{scene.scene_index:03d}.json"
                write_model(scene_path, scene)
                manifest.append(
                    {
                        "chapter_id": scene.chapter_id,
                        "chapter_title": scene.chapter_title,
                        "scene_id": scene.scene_id,
                        "scene_index": scene.scene_index,
                        "char_count": scene.char_count,
                        "source_file": scene.source_file,
                        "output_file": scene_path.name,
                    }
                )
            generated_scene_count += len(scenes)
            tracker.record_success(
                chapter_file.name,
                f"Split {chapter_file.name} into {len(scenes)} scene(s).",
                chapter_id=chapter.chapter_id,
                scene_count=len(scenes),
            )

        write_json(output_path / "manifest.json", manifest)
        summary = (
            f"Scene split completed. chapters={len(chapter_files)} "
            f"generated_scenes={generated_scene_count}"
        )
        tracker.finish(summary, generated_scene_count=generated_scene_count, manifest_count=len(manifest))
    except Exception as exc:  # noqa: BLE001
        tracker.fail_run(f"Scene split aborted: {exc}", error_type=type(exc).__name__)
        raise
