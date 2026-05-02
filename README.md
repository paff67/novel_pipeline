# Novel Pipeline

A complete OpenAI-compatible extraction project for:

- chapter staging
- scene splitting
- fact extraction
- style extraction
- canon export
- style bible synthesis
- style bible evaluation (offline v1)
- static review panel generation

The pipeline is designed for Chinese web novels and specifically separates:

- factual canon extraction
- style and narrative-engine learning

## Current Mainline (2026-03-31)

- The current formal production path is `novel_pipeline_stable` with `config/formal_cn_gpt54_stable.toml`
- The current formal resume launcher is:
  - `D:\card\novel_pipeline\scripts\launch_fact_style_full_formal_cn_gpt54_stable.ps1`
- The current story-node pipeline launcher is:
  - `D:\card\novel_pipeline\scripts\run_story_node_pipeline_formal_cn_gpt54_stable.ps1`
- The current formal full-input directories are:
  - `D:\card\novel_pipeline\data\experimental\chapters_full_0001_0841_ready_20260330`
  - `D:\card\novel_pipeline\data\experimental\scenes_full_0001_0841_ready_20260330`
- `fact` and `style` now run as separate resumed processes with a shared watchdog
- Story-node runs only proceed for confirmed nodes whose fact coverage is complete, and write under:
  - `D:\card\novel_pipeline\data\semantic_versions_formal_cn_gpt54_stable`
- `fact` single-pass outputs, `fact` two-pass primary outputs, and `style` window outputs now enforce model-level non-empty structured validation
- Empty-shell JSON repaired from gateway glitches is treated as a validation failure so the client can retry/fail over before the pipeline records the item as failed
- The latest user-confirmed main story-node manifest is:
  - `D:\card\novel_pipeline\data\experimental\story_nodes_user_confirmed_main_20260331\story_nodes_confirmed.json`
- Stability notes for the current mainline are tracked in:
  - `D:\card\novel_pipeline\STABILITY_NOTES.md`

The generic workflow below is still valid for manual/local runs, but it is no longer the exact formal production entry path.

For VPS deployment and file sync boundaries, see `VPS_SYNC_GUIDE.md`.

## Style Bible Phase 2 (2026-04-22)

The current `Style Bible v2` mainline in `src/novel_pipeline_stable` has completed the hard cutover:

- concrete rule family models only: `StyleBibleRuleBase`, `NarrativeRuleItem`, `WorldbookFactItem`, `RoutingHintItem`, `NegativeRuleItem`, and `ScalarRuleItem`
- native schema contracts generated directly from Pydantic response models via `model_json_schema()`
- Chinese slot anchoring driven only by `cue` and `canonical_description`
- `style_bible_reduction/` and `api_clients/` package splits replacing the old reducer/client monoliths
- semantic evaluation as the main production gate in `evaluate-style-bible`, with model fallback `semantic_judge_model -> style_bible_model -> style_model`
- `judge-style-bible` retained only for gold-set regression runs

The production path no longer uses shadow-mode quality gates, duplicate evaluation commands, or compatibility aliases for the removed transitional modules.

## 1. Setup

```powershell
cd D:\card\novel_pipeline
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

Fill in these values in `.env`:

- `OPENAI_COMPAT_API_KEY`
- `OPENAI_COMPAT_BASE_URL`

Examples of compatible base URLs:

- `http://127.0.0.1:4000/v1`
- `https://your-proxy.example.com/v1`

## 2. Project structure

```text
novel_pipeline/
  config/
    project.example.toml
    trial_2_5pro.toml
  data/
    chapters/
    scenes/
    extracted/
      facts/
      style/
    canon/
    style_bible/
    review/
  prompts/
    fact_extraction.md
    style_extraction.md
    style_bible_synthesis.md
  src/
    novel_pipeline/
```

## 3. Prompt location

You can view and modify extraction prompts here:

- `D:\card\novel_pipeline\prompts\fact_extraction.md`
- `D:\card\novel_pipeline\prompts\style_extraction.md`

The config key `paths.prompt_dir` controls which prompt directory is used.

## 4. OpenAI-compatible calling mode

The project now uses the OpenAI Python SDK against an OpenAI-compatible endpoint.

Structured output mode is controlled by:

- `response_format = "json_schema"`
- `response_format = "json_object"`

Use `json_schema` when your gateway supports strict structured outputs.
Use `json_object` if your gateway is compatible but does not support JSON schema mode.

Request rate is also configurable through:

- `max_requests_per_minute = 2.0`

The default project and trial configs are now pinned to 2 RPM, which keeps the pipeline safely below 3 RPM in a single-process run.

## 5. Trial config

For the first trial run, use:

- `D:\card\novel_pipeline\config\trial_2_5pro.toml`

This config uses `gemini-2.5-pro` for both fact extraction and style extraction.
It also uses smaller style windows to make validation faster.

## 6. Recommended workflow

### Stage cleaned chapter files into the project

```powershell
novel-pipeline stage-chapters `
  --input-dir "D:\card\cleaned_chapters" `
  --output-dir "D:\card\novel_pipeline\data\chapters" `
  --clear
```

### Split chapters into scenes

```powershell
novel-pipeline split-scenes `
  --config "D:\card\novel_pipeline\config\trial_2_5pro.toml" `
  --input-dir "D:\card\novel_pipeline\data\chapters" `
  --output-dir "D:\card\novel_pipeline\data\scenes"
```

### Trial run on a small sample

Recommended first pass:
- facts: first 30 scenes
- style: first 5 windows

```powershell
novel-pipeline extract-facts `
  --config "D:\card\novel_pipeline\config\trial_2_5pro.toml" `
  --input-dir "D:\card\novel_pipeline\data\scenes" `
  --output-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --limit 30 `
  --resume
```

```powershell
novel-pipeline extract-style `
  --config "D:\card\novel_pipeline\config\trial_2_5pro.toml" `
  --input-dir "D:\card\novel_pipeline\data\chapters" `
  --output-dir "D:\card\novel_pipeline\data\extracted\style" `
  --limit 5 `
  --resume
```

### Build canon assets

```powershell
novel-pipeline build-canon `
  --facts-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --style-dir "D:\card\novel_pipeline\data\extracted\style" `
  --output-dir "D:\card\novel_pipeline\data\canon"
```

### Build the review panel

```powershell
novel-pipeline build-review-panel `
  --facts-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --style-dir "D:\card\novel_pipeline\data\extracted\style" `
  --output-dir "D:\card\novel_pipeline\data\review"
```

Then open:

- `D:\card\novel_pipeline\data\review\review_panel.html`

### Build the synthesized style bible

```powershell
novel-pipeline build-style-bible `
  --config "D:\card\novel_pipeline\config\formal_cn_gpt54_stable.toml" `
  --facts-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --style-dir "D:\card\novel_pipeline\data\extracted\style" `
  --canon-dir "D:\card\novel_pipeline\data\canon" `
  --output-dir "D:\card\novel_pipeline\data\style_bible" `
  --resume
```

### Evaluate the synthesized style bible

`evaluate-style-bible` is the primary semantic quality gate. It validates schema completeness and section completeness first, then applies semantic rule-quality scoring for specificity, actionability, and grounding.

```powershell
novel-pipeline evaluate-style-bible `
  --input-dir "D:\card\novel_pipeline\data\style_bible" `
  --output-dir "D:\card\novel_pipeline\data\style_bible_eval" `
  --resume
```

### Judge the synthesized style bible

`judge-style-bible` is optional gold-set regression tooling. It is no longer part of the main production quality gate.

```powershell
novel-pipeline judge-style-bible `
  --input-dir "D:\card\novel_pipeline\data\style_bible" `
  --output-dir "D:\card\novel_pipeline\data\style_bible_judge" `
  --gold-set-index "D:\card\novel_pipeline\data\eval\style_gold_set\v2\index.json" `
  --judge-config "D:\card\novel_pipeline\config\style_bible_judge_rules.toml" `
  --resume
```

## 7. Outputs

The canon build generates:

- `entities.jsonl`
- `facts.jsonl`
- `events.jsonl`
- `chapter_summaries.jsonl`
- `style_bible.json` (intermediate style-window collection)
- `canon_index.json`

The style bible build generates:

- `style_bible_source_bundle.json`
- `style_bible_final.json`
- `style_bible_reasoning.json`
- `style_bible_reduce_trace.json`
- `_section_densify\...` semantic slot-matching traces and summaries when densify runs
- `story_node_scope.json` (node-scoped builds only)
- `manifest.json`
- `failures.json`

The style bible evaluation generates:

- `style_eval_report.json`
- `style_eval_report.md`
- semantic main-gate scoring blocks inside the report JSON
- `manifest.json`
- `failures.json`

The style bible judge generates:

- `judge_report.json`
- `judge_report.md`
- `judge_rows.jsonl`

The style bible ragas sidecar generates:

- `ragas_report.json`
- `ragas_report.md`
- `ragas_rows.jsonl`
- `ragas_dataset.json`

The review panel build generates:

- `review_data.json`
- `review_panel.html`

## 8. Review workflow

Use the review panel to inspect:

- whether entity extraction is stable
- whether facts include good evidence spans
- whether events are too vague or too broad
- whether style fingerprints actually capture the novel's unusual narrative engine
- whether the model overgeneralizes from too little text

## 9. Notes

- `gemini-2.5-pro` is still a good default model name if your OpenAI-compatible gateway exposes it.
- If you want to run only a subset, use `--limit` and `--start-at`.
- If the model refuses or the network fails, rerun with `--resume`.
- This environment currently has `py.exe` but no installed Python runtime, so the project files are ready but not locally validated here.

