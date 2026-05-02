## Stable Runtime Notes

### 2026-03-31

- `novel_pipeline_stable` now rejects empty-shell structured results for both `fact` and `style` at the model-validation layer.
- `fact` protection covers:
  - single-pass `FactExtractionResult`
  - two-pass primary `FactExtractionPrimaryPassResult`
- `style` protection covers `StyleExtractionResult`.
- This matters because some OpenAI-compatible gateways occasionally return empty content or malformed JSON that the repair step can normalize into a syntactically valid but semantically empty object.
- With model-level non-empty validation in place, those responses are surfaced as validation failures inside the client, which allows the normal retry and gateway failover path to run before the pipeline records a scene/window failure.
- The later pipeline-level content checks remain in place as a second guard rail.

### Current formal production lane

- Config: `D:\card\novel_pipeline\config\formal_cn_gpt54_stable.toml`
- Route: `/responses`
- Model: `gpt-5.4`
- Reasoning effort: `xhigh`
- Fact gateways: allowed indexes `1,3`, primary gateway index `1`
- Style gateways: allowed indexes `2,3`; the production launcher sets original gateway index `2` as the primary style gateway and keeps gateway `3` as backup
- RPM limit: disabled with `NOVEL_PIPELINE_MAX_RPM=0`
