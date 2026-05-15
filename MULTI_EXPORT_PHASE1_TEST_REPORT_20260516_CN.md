# Multi-Export Phase 1 测试报告

生成时间：2026-05-16（Asia/Shanghai）

## 结论

Phase 1 未通过进入 Phase 2 的硬门槛，执行在 Phase 1 停止。

阻断原因不是单元测试或代码编译失败，而是正式 `build-style-bible` 在网关层连续返回 Cloudflare `524 timeout`。本轮没有生成 `style_bible_final.json`，因此无法继续执行 `evaluate-style-bible` 与 `judge-style-bible`。

## Git 前置同步

- 已在 Phase 1 前提交 baseline：`chore: sync multi export implementation plan baseline`
- 已完成 rebase 检查。
- HTTPS push 缺凭据后，确认 SSH 认证可用，并成功推送到 `paff67/novel_pipeline`。
- 本地 `origin` 已切换为同仓库 SSH URL，便于后续 push。

## 已落地的 Phase 1 代码

- 放松 `style_bible_local_reduce.md` 与 `style_bible_section_densify.md` 中 narrative / expression / voice / aesthetics 的硬模板要求。
- 新增确定性 Judge V2 投影导出器：`style_bible_judge_export.py`。
- Style Bible assembly 写出 legacy flat export 后，额外写出 `judge_flat.json`。
- `judge-style-bible` 优先读取 `judge_flat.json`，再 fallback 到 `style_bible_export_flat.json` 与 canonical flatten。
- 新增单元测试覆盖 judge export schema、shape repair、evidence enrichment 与 judge projection 优先级。

## 本地验证

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests -v
```

结果：

- `compileall`：通过
- `unittest`：139/139 通过

## Phase 1 build 测试

执行命令：

```bash
.venv/bin/python -m novel_pipeline_stable build-style-bible \
  --config config/formal_cn_gpt54_stable.toml \
  --facts-dir data/extracted/facts_formal_cn_gpt54_stable \
  --style-dir data/extracted/style_formal_cn_gpt54_stable_main_01_kunxu_l1_ch0001_0270 \
  --canon-dir data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/canon \
  --output-dir data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible_phase1_multi_export_20260516 \
  --scope-label main_01_kunxu_l1_ch0001_0270 \
  --resume
```

输出目录：

`data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible_phase1_multi_export_20260516`

已生成的前置产物：

- `style_bible_source_bundle.json`
- `style_bible_routed_index.json`
- `batch_plan.json`
- `planner_debug_report.json`
- `sampling_report.json`
- `style_bible_coverage_report.json`
- `story_node_scope.json`
- `run_status.json`
- `run_log.jsonl`

未生成：

- `bucket_memos/*.json`
- `style_bible_final.json`
- `style_bible_export_flat.json`
- `judge_flat.json`
- `style_bible_reasoning.json`
- `style_bible_reduce_trace.json`

## 网关失败详情

本轮 build 在 bucket memo 阶段失败：

- 已创建 bucket request 目录：12
- 已记录 error response 文件：42
- HTTP 状态码：全部为 `524`
- 已完成 request metrics 文件：6
- 已成功 bucket memo：0
- 第一批 6 个 bucket 均耗尽 5 次重试，`completed=false`
- 第二批 bucket 已出现同类 524，继续执行已不可能满足 `build failure count = 0`

典型错误：

```text
Server error '524 <none>' for url 'https://api.0-0.pro/v1/responses'
Gateway body: 0-0.pro | 524: A timeout occurred
retry_after_seconds: 120.0
```

请求配置记录显示：

- `model = gpt-5.4`
- `reasoning_effort = xhigh`
- `api_route = responses`
- `response_format_type = json_schema`
- `used_stream = true`
- `temperature_requested = 0.2`
- `temperature_sent = null`
- `temperature_omitted_reason = omitted_for_responses_compatibility`
- `max_attempts = 5`
- `retry_budget_used = true`

## 门槛判定

硬门槛：

- H1 Eval overall >= 0.72：未执行，原因是 build 未生成 final。
- H2 Judge overall >= 75：未执行，原因是 build 未生成 final。
- H3 Judge fail case count = 0：未执行。
- H4 单元测试通过：通过，139/139。
- H5 build failure count = 0：失败，bucket memo 阶段已出现连续 524，且成功 memo 为 0。

判定：Phase 1 不满足进入 Phase 2 条件，按计划停止，不落地 Phase 2。

## 建议

- 等待网关恢复后，用同一输出目录加 `--resume` 重跑 Phase 1 build。
- 如果 524 持续出现，建议单独做网关策略调整：降低并发、减少单请求 prompt 体积、切换备用网关，或为 `/responses` 长推理请求确认 stream/timeout 的网关兼容策略。
- 本轮代码层改动已通过单元测试，可以保留；Phase 2 不应在 build gate 未通过前启动。
