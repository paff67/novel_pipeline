# Style Bible Grounding / Scope-Aware Refactor Plan

## 目标

本轮重构针对 4 个剩余缺口做一次成体系修复：

1. `allowed_refs` 没有进入 reducer 的 grounding 引用池。
2. sparse bucket 仍在发起 LLM 请求后才被判定，缺少 preflight skip。
3. `reduce_trace` 只有 reasoning 级证据图，没有 final rule 级血缘和合并事件。
4. Judge 对 mini / degraded run 不具备作用域感知，仍按固定分母扣分。

## 已吸收的高价值补充策略

### 1. `_grounding_ref_pool`：深度遍历与正名

本次不再沿用语义含糊的 `_memo_ref_pool`。新的 grounding 池明确表示“当前 bucket 在物理层面被允许引用的证据全集”，并且深度遍历：

- `memo.allowed_refs`
- `memo.rule_candidates[].evidence_refs`
- `memo.batch_memos[].allowed_refs`
- `memo.batch_memos[].rule_candidates[].evidence_refs`

落地原则：

- reducer 内部统一按 `grounding` 语义思考和收口。
- trace 对外写出 `grounding_ref_pool`。
- 为避免现有 reader 立即断裂，trace 暂保留 `memo_ref_pool` 作为同值兼容别名。

### 2. Structured Preflight Skip：不伪造 Response，直接产出合法 Artifact

preflight 判定左移到 hierarchical dispatch loop，避免用“伪造 LLM Response”的方式绕过控制流。

判定输入：

- `candidate_count`
- `grounding_ref_count`
- `batch_memo_count`
- `item_count`

命中 sparse preflight 后：

- 不调用 `client.generate_structured()`
- 直接构造合法的 sparse `LocalReduceArtifact`
- 直接写出：
  - `local_partial.json`
  - `local_final.json`
  - `local_reasoning.json`
  - `local_reduce_trace.json`
  - `local_reduce_summary.json`

这保证了 skip 路径与正常路径共享同一套 artifact 契约，而不是额外维护一套“假响应对象”。

### 3. Judge 双层作用域评测：Case Scope + Dynamic Denominator

Judge 不再使用粗放的全局 `valid_run_ref_pool` 扣分，而是为每个 gold case 构造 `CaseScopeContext`：

- 从 `reduce_trace.local_reduces` 读取 bucket / batch / grounding refs
- 结合 `failed_bucket_ids` / `skipped_sparse_bucket_ids`
- 结合 `metadata.degradation_status`
- 收窄成 case 自己的 `case_scope_ref_pool`

评分策略：

- `effective_expected_refs = expected_ref_set ∩ case_scope_ref_pool`
- 若目标 scope 根本不在本次 run 中，返回 `status = "not_applicable"`，并把 `max_score = 0`
- summary 只按 applicable case / applicable dimension 聚合
- 报告显式区分：
  - 真失败
  - `not_applicable`

### 4. Trace 埋点下钻到真实 merge 接缝

per-rule provenance 不在抽象“Assembler 层”补，而是在真实 merge 发生的物理位置埋点：

- `_merge_rule_lists(...)`
- `_resolve_scalar_candidates(...)`
- `_assemble_path_value_from_candidates(...)`

新增 trace 结构：

- `rule_lineage_map`
- `merge_events`

核心字段：

- `final_rule_id`
- `surface_path`
- `kept_bucket_id`
- `source_bucket_ids`
- `source_kind`
- `reasoning_ref`
- `merged_evidence_refs`
- `origin_rule_ids`
- `conflict_history`

这样最终可以回答三类关键问题：

1. 这条规则来自哪个 bucket。
2. 这条规则是模型直接产出，还是 assembler 合并后保留下来的。
3. 合并时吞掉了哪些 origin rule。

## 代码落地范围

### Reducer

文件：`src/novel_pipeline_stable/style_bible_reducer.py`

落地内容：

- `_grounding_ref_pool`
- `LocalReducePreflightDecision`
- direct sparse artifact builder
- `grounding_ref_pool` 写入 reduce trace / local summaries
- `rule_lineage_map`
- `merge_events`
- `local_reduces` trace 行补充：
  - `memo_id`
  - `batch_ids`
  - `grounding_ref_pool`
  - `preflight`

### Models

文件：`src/novel_pipeline_stable/models.py`

落地内容：

- `StyleBibleRuleLineageEntry`
- `StyleBibleMergeEvent`

### Judge

文件：`src/novel_pipeline_stable/style_bible_judge.py`

落地内容：

- `CaseScopeContext`
- `not_applicable` 维度状态
- case 级 scope-aware 判定
- dynamic denominator summary
- markdown / json report 中补充：
  - `overall_ratio`
  - `applicable_case_count`
  - `not_applicable_case_count`

### Evaluator

文件：`src/novel_pipeline_stable/style_bible_evaluator.py`

落地内容：

- grounding trace 读取优先支持 `grounding_ref_pool`
- 兼容旧 `memo_ref_pool`

## 回归验证

### 新增 / 更新的测试点

- `allowed_refs` 在 `rule_candidates=[]` 时仍进入 grounding 池
- preflight sparse bucket 不触发模型调用
- hierarchical reduce 产出 `rule_lineage_map`
- Judge 对 out-of-scope case 返回 `not_applicable`
- Judge 对 skipped sparse target bucket 返回 `not_applicable`
- summary 仅按 applicable case 聚合

### 回归命令

```text
$env:PYTHONPATH='D:\card\novel_pipeline\src'; python -m unittest tests.test_style_bible_local_reduce_contracts tests.test_style_bible_hierarchical_reducer tests.test_style_bible_v2_schema_contracts tests.test_style_extract_v2_contracts tests.test_style_bible_judge_scope_aware
```

### 当前结果

```text
.........................................
----------------------------------------------------------------------
Ran 41 tests in 0.324s

OK
```

## 仍需后续验证

代码契约和回归测试已经通过，但这份修复仍缺最后一环：

- 需要基于当前分支重新跑一轮真实 mini live
- 然后再决定是否直接推进 full live

原因很简单：本轮已经改动了 reducer trace、judge 聚合、sparse skip 行为，真实运行产物和报告链路仍需要用实跑数据确认一次。
