# Style Bible 采样控制面与 Local Reduce 运行时修复报告

日期：2026-04-20

## 1. 本轮目标

本轮修复聚焦两个真实 live 阻塞点：

1. `style_bible_builder.py` 中 story-node sampling cap 只作用于 `source_bundle`，没有继续传递到 router / batching，导致 node1 smoke 在采样已缩到 `24/24/24/24/20` 的情况下，仍然生成 `264` 个 batch。
2. `style_bible_local_reduce` 在真实运行时出现两类协议漂移：
   - `final.rule_rows[*]._reasoning_ref` 被模型误写成 evidence/window ref。
   - `reasoning.entries[*]` 被模型输出成简版形状 `{"_reasoning_ref","text","evidence_refs"}`，而不是标准 `StyleBibleReasoningEntry`。

## 2. 代码改动

### 2.1 控制面：采样 scope 真正回灌到 router / batching

修改文件：

- `src/novel_pipeline_stable/style_bible_builder.py`
- `tests/test_style_bible_router_batching_builder_guards.py`

核心改动：

1. 新增 `_selected_values_from_source_bundle(...)`，从 `style_bible_source_bundle.json` 中抽出 scene/style/chapter/plot/entity 的实际选中主键。
2. 新增 `_filter_style_bible_inputs_to_sampling_scope(...)`，把原始 `StyleBibleInputBundle` 收缩成一个真正的 sampled bundle。
   - `fact_rows`：只保留被采样的 `scene_id`
   - `style_rows`：只保留被采样的 `window_id`
   - `chapter_rows`：保留 sampled chapter，以及 sampled fact/style 实际涉及到的 chapter
   - `plot_rows` / `entity_rows`：按 sampled id 与 sampled chapter scope 收缩
3. `_prepare_style_bible_phase01_artifacts(...)` 改为：
   - 先构建 `source_bundle`
   - 再构建 `filtered_inputs`
   - 用 `filtered_inputs` 去执行 `route_style_bible_inputs(...)`
   - 用过滤后的 routed index 去执行 `plan_style_bible_batches_with_debug(...)`
4. 新增调试工件：
   - `style_bible/sampled_input_scope.json`
   - 用于记录 `original -> filtered` 的数量变化，以及 sampled refs 明细

直接效果：

- 修复前 `node1 caps smoke`：`264` batches
- 修复后 `node1 caps smoke fix01`：`23` batches

### 2.2 运行时兼容：修复 compact reasoning / wrong _reasoning_ref

修改文件：

- `src/novel_pipeline_stable/models.py`
- `tests/test_style_bible_local_reduce_contracts.py`

核心改动：

1. 为 `StyleBibleReasoningEntry` 增加 compact 兼容桥：
   - 支持把 `{"_reasoning_ref","text","evidence_refs"}` 自动归一成标准 reasoning entry
   - `_reasoning_ref -> reasoning_id`
   - `text -> claim`
2. 为 `StyleBibleLocalReducerOutput` 增加 `_reasoning_ref` 自愈：
   - 如果 `final.rule_rows[*]._reasoning_ref` 不是合法 `reasoning_id`
   - 但它与 `evidence_refs` 能高重合命中某个 reasoning entry
   - 则自动回填成正确的 `reasoning_id`
3. 保留“无法通过证据回溯时仍然报错”的 guardrail，避免误吞真正的坏数据

直接效果：

- 真实 `dark_humor` parse-error 原文不再因为 `_reasoning_ref=0001_0002` 被拦死
- reducer 可以跳过无意义 JSON repair 长挂起，直接进入后续 sanitize / assemble

### 2.3 Prompt 合同：明确 reasoning id 与 compact reasoning 禁止项

修改文件：

- `prompts/style_bible_local_reduce.md`

新增硬约束：

1. `final.rule_rows[*]._reasoning_ref` 只能填写 `reasoning.entries[*].reasoning_id`
2. 绝对不能把 window id、scene/source ref、worldbook atom id、evidence ref 直接写进 `_reasoning_ref`
3. `reasoning.entries[*]` 至少要显式填写：
   - `reasoning_id`
   - `claim`
   - `evidence_refs`
4. 如果只是写一句机制判断，必须放进 `claim`，不能只输出简写 `text`

## 3. 测试与验证

### 3.1 单元测试

执行命令：

```bash
python -m unittest tests.test_style_bible_local_reduce_contracts tests.test_style_bible_router_batching_builder_guards tests.test_style_extract_v2_contracts tests.test_canon_builder tests.test_config_env_loading tests.test_style_bible_v2_schema_contracts tests.test_style_bible_hierarchical_reducer
```

结果：

- `71` 个测试全部通过

新增/更新的关键回归：

1. `test_phase01_router_and_batching_only_see_sampled_fact_and_style_inputs`
   - 保证 sampled scene/style 真的进入 routed index / batch plan
2. `test_local_reducer_output_can_recover_reasoning_ref_from_evidence_refs`
   - 保证 `_reasoning_ref` 写错成 evidence ref 时可自动归一
3. `test_local_reducer_output_accepts_compact_reasoning_entry_shape`
   - 保证 compact reasoning entry 能被标准模型兼容
4. `test_local_reducer_output_rejects_dangling_reasoning_ref`
   - 保留无法回溯时必须失败的 guardrail

### 3.2 真实 payload 复验

复验对象：

- `data/live_runs/full_live_from_extract_style_20260419_231949/semantic_versions_node1_caps_smoke_fix01/main_01_kunxu_l1_ch0001_0270/style_bible/_local_reduce/dark_humor/_raw_responses/1051559268_attempt1_parse_error.txt`

复验结论：

1. 该 payload 的问题不是 JSON 语法坏，而是协议漂移：
   - `reasoning.entries[*]` 使用简版 shape
   - `final.rule_rows[*]._reasoning_ref` 使用 evidence/window ref
2. 新兼容桥上线后，该 payload 已可被 `StyleBibleLocalReducerOutput.model_validate(...)` 成功通过

## 4. 当前收益

这轮修复带来的实际收益不是“分数可能会涨”，而是控制面与运行时稳定性都发生了实质改善：

1. story-node cap 终于真正约束住了 phase01 路由与 batching。
2. 真实 live 里最典型的 `_reasoning_ref` 协议漂移不再把整个 bucket 卡死在 repair。
3. local reduce prompt 与模型合同更接近真实运行行为，后续可以在此基础上继续压缩 repair 长尾。

## 5. 仍然存在的长尾问题

截至本报告落盘时，真实 node1 live 仍未完全结束，说明当前剩余瓶颈已经从“phase01 爆量”切换成了“section repair / densify 长尾耗时”。

下一步建议：

1. 继续保留当前 live 产物与日志，用于确认 section repair / densify 的最终耗时分布。
2. 如果 family-survival / identity-shame 这类 repair pass 仍然频繁出现，再单独审 prompt 与 repair 策略，而不是回头怀疑 sampling 控制面。
3. full live 全量 5 节点之前，建议先以 node1 完整跑通为里程碑，再决定是否放大全量。
