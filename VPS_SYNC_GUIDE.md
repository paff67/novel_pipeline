# VPS Sync Guide

This project can run on a VPS with only the Python package source, runtime configs, prompts, and the data you want to process or resume. Do not upload the local virtualenv, Python bytecode caches, local secrets, or old debug scratch files.

## Required Runtime Files

Sync these files and directories for a fresh install:

```text
pyproject.toml
README.md
README_CN.md
src/
config/
prompts/
scripts/
```

`src/`, `config/`, and `prompts/` are the real runtime core. `scripts/` is useful as operational reference, but many existing scripts contain Windows paths and should be adapted or replaced with Linux shell/systemd commands on the VPS.

## Required Secrets

Do not sync the local `.env` file. Create a fresh `.env` on the VPS from `.env.example` and fill in the gateway credentials there:

```text
OPENAI_COMPAT_API_KEY=...
OPENAI_COMPAT_BASE_URL=...
```

If you use multiple gateways in `config/formal_cn_gpt54_stable.toml`, verify the VPS environment names and API keys match the config.

## Data To Sync

For a fresh run, sync only the source/input data you need:

```text
data/experimental/chapters_full_0001_0841_ready_20260330/
data/experimental/scenes_full_0001_0841_ready_20260330/
data/experimental/story_nodes_user_confirmed_main_20260331/
```

For resume or audit runs, also sync the existing artifacts you want to reuse:

```text
data/extracted/facts_formal_cn_gpt54_stable/
data/extracted/style_formal_cn_gpt54_stable/
data/canon_formal_cn_gpt54_stable/
data/semantic_versions_formal_cn_gpt54_stable/
```

If storage is tight, prefer syncing facts/style/canon first, then regenerate node-scoped Style Bible outputs on the VPS.

## Optional Files

Sync these only when you need development or regression checks on the VPS:

```text
tests/
PHASE2_TRUE_REFACTOR_WORKFLOW.md
PHASE2_TRUE_REFACTOR_COMMIT_CHECKLIST.md
STABILITY_NOTES.md
STYLE_GOLD_SET_GUIDE_CN.md
STYLE_EVAL_CONTRACT_CN.md
```

The many dated `*_REPORT_*.md`, `*_PLAN_*.md`, and old implementation checklist files are historical records. They are not required for runtime.

## Do Not Sync

Exclude these by default:

```text
.env
.venv/
.pytest_cache/
**/__pycache__/
*.pyc
fix.py
fix_models.py
fix_specs.py
update_models.py
data/logs/
data/reports/
data/**/_raw_responses/
data/**/_request_cache/
data/**/style_bible_refactor_smoke_*/
```

Keep `_request_cache/` only if you deliberately want exact cached LLM request reuse. Otherwise it is large, local, and safe to rebuild.

## Rsync Example

From a Linux/macOS shell or WSL:

```bash
rsync -av --delete \
  --exclude '.env' \
  --exclude '.venv/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'fix.py' \
  --exclude 'fix_models.py' \
  --exclude 'fix_specs.py' \
  --exclude 'update_models.py' \
  --exclude 'data/logs/' \
  --exclude 'data/reports/' \
  --exclude 'data/**/_raw_responses/' \
  --exclude 'data/**/_request_cache/' \
  D:/card/novel_pipeline/ user@your-vps:/opt/novel_pipeline/
```

For PowerShell with `scp`, create a clean staging directory first so excluded files do not get uploaded accidentally.

## VPS Setup

On the VPS:

```bash
cd /opt/novel_pipeline
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
cp .env.example .env
```

Start the monitor:

```bash
novel-pipeline serve-monitor --data-root /opt/novel_pipeline/data --host 0.0.0.0 --port 8765
```

Put the monitor behind Nginx with basic auth or a private VPN. The monitor exposes local run metadata and file paths, so it should not be public without access control.

## Current Runtime Caveat

The refactored code passes the current test suite, but existing formal Style Bible artifacts may still be in the old schema. Fresh node rebuilds can fail if a critical local-reduce bucket returns an empty sparse result. The latest observed blocker was:

```text
Critical bucket local reduce produced a sparse result: dark_humor
```

Treat old `style_bible/` directories as reusable inputs for inspection, not as guaranteed valid outputs for the new semantic evaluator.
