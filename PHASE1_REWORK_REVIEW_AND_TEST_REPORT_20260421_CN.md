# Phase 1 Rework 复审与测试报告

日期：2026-04-21  
审查对象：当前 `novel_pipeline` 本地代码（rework 后版本）  
对照基线：`novel_pipeline_architectural_refactor_plan.md` 的 Phase 1 验收要求

## 1. 结论

这轮 rework 已经把上次最核心的两个阻塞点修掉了：

- `StyleBibleReasoningEntry` 的 compact entry 回归已修复
- `worldbook_binding.rag_worthy / worldbook_worthy` 的 config shape 已与 prompt / surface spec 对齐
- V2 evaluator 主路径已不再复现上次的 `str has no attribute query_feature_matcher` 崩溃
- 关键测试与全量 `unittest discover` 目前均通过

但严格按 Phase 1 原计划验收，**仍不能判定为 100% 完成**。剩余问题主要有两类：

1. `style_bible_reducer.py` 的 observability 仍未彻底收口，summary status 依旧统一落成 `filtered_empty`
2. `style_bible_evaluator.py` 的 legacy fallback 路径仍然存在运行时崩溃

因此本轮结论是：

- **Phase 1 主体能力已基本完成**
- **但严格按计划条款，仍有收尾缺口，暂不建议宣告“完全完成”**

## 2. 测试验证结果

### 2.1 Phase 1 关键测试组

执行：

```powershell
& 'D:\card\novel_pipeline\.venv\Scripts\python.exe' -m unittest `
  D:\card\novel_pipeline\tests\test_style_bible_local_reduce_contracts.py `
  D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py `
  D:\card\novel_pipeline\tests\test_style_bible_eval_profiles.py `
  D:\card\novel_pipeline\tests\test_style_bible_v2_schema_contracts.py `
  D:\card\novel_pipeline\tests\test_style_bible_ragas_eval.py `
  D:\card\novel_pipeline\tests\test_style_extract_v2_contracts.py `
  D:\card\novel_pipeline\tests\test_style_bible_router_batching_builder_guards.py `
  D:\card\novel_pipeline\tests\test_hybrid_rag_contract.py `
  D:\card\novel_pipeline\tests\test_hybrid_retriever.py
```

结果：

```text
Ran 76 tests in 1.460s
OK
```

### 2.2 全量 unittest discover

执行：

```powershell
& 'D:\card\novel_pipeline\.venv\Scripts\python.exe' -m unittest discover -s D:\card\novel_pipeline\tests
```

结果：

```text
Ran 90 tests in 3.615s
OK
```

### 2.3 最小复现实验

#### 实验 A：上次失败的 `StyleBibleReasoningEntry` 是否恢复

执行结果：

```text
reasoning_01
combat endings must land through residue
['scene:0141_003']
```

以及 compact alias 形态：

```text
reasoning_03
settlement happens before the action is approved
```

说明 `reasoning_id <- _reasoning_ref` 与 `claim <- text` 的补水逻辑已经恢复。

#### 实验 B：V2 evaluator 入口是否恢复

对最小 V2 payload 执行 `evaluate_style_bible(...)`，结果：

```text
summary.status = fail
schema_validity.status = pass
```

这里的 `fail` 是因为样本故意做得很薄，不满足质量阈值；关键点是：

- evaluator **不再崩溃**
- schema gate 已能正确走 V2 路径

#### 实验 C：legacy fallback 是否仍然断路

对最小 legacy payload 执行 `evaluate_style_bible(...)`，结果：

```text
AttributeError
'str' object has no attribute 'query_feature_matcher'
```

这说明 evaluator 的 fallback 兼容路径仍然没有完全收口。

## 3. 已确认修复的内容

### 3.1 `StyleBibleReasoningEntry` 回归已修复

位置：

- `src/novel_pipeline_stable/models.py:466`

当前原始代码：

```python
@model_validator(mode="before")
@classmethod
def _hydrate_compact_reasoning_entry(cls, value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    payload = dict(value)
    reasoning_id = _clean_model_text(
        payload.get("reasoning_id") or payload.get("_reasoning_ref") or payload.get("reasoning_ref")
    )
    text = _clean_model_text(payload.get("claim") or payload.get("text"))
    if reasoning_id:
        payload["reasoning_id"] = reasoning_id
    if text:
        payload["claim"] = text
    return payload
```

结论：

- 上轮导致 12 个错误的核心回归已经修复
- 相关测试当前全部通过

### 3.2 `worldbook` 与 `routing` 的 config shape 已对齐

位置：

- `config/style_bible_section_targets.toml:1186`
- `config/style_bible_section_targets.toml:1248`

当前原始代码：

```toml
[[path_targets]]
path = "worldbook_binding.rag_worthy"

[[path_targets.slot_specs]]
slot_id = "repayment_retrieval"
downstream_shape = "trigger + constraint"
```

```toml
[[path_targets]]
path = "worldbook_binding.worldbook_worthy"

[[path_targets.slot_specs]]
slot_id = "institutional_workflow_worldbook"
downstream_shape = "trigger + constraint"
```

程序化读取配置得到的实际结果：

```text
worldbook_binding.routing_hints
['matcher + route_target_action', ...]

worldbook_binding.rag_worthy
['trigger + constraint', ...]

worldbook_binding.worldbook_worthy
['trigger + constraint', ...]
```

结论：

- Step 1.3 相比上轮已经显著推进
- `surface_specs` / `prompt` / `section_targets` 在这部分已经基本对齐

### 3.3 `style_bible_ragas_eval.py` 的 contract scoring 已同步到新 contract

位置：

- `src/novel_pipeline_stable/style_bible_ragas_eval.py:136`

当前原始代码：

```python
def _contract_score(item_type: str, item: dict[str, Any]) -> float:
    matcher = _clean_text(item.get("query_feature_matcher"))
    route_action = _clean_text(item.get("route_target_action"))
    trigger = _clean_text(item.get("trigger"))
    constraint = _clean_text(item.get("constraint"))

    if item_type == "routing_hints":
        score = 0.0
        if matcher:
            score += 0.5
        if route_action:
            score += 0.5
        return round(score, 4)

    if trigger and constraint:
        return 1.0
    if trigger or constraint:
        return 0.5
    return 0.0
```

结论：

- 这部分已不再继续奖励旧的 matcher-only worldbook/rag 形态

## 4. 当前 Findings（按严重度排序）

### Finding 1：evaluator 的 legacy fallback 仍然会在 routing/worldbook 检查处崩溃

位置：

- `src/novel_pipeline_stable/style_bible_evaluator.py:454`
- `src/novel_pipeline_stable/style_bible_evaluator.py:1378`
- `src/novel_pipeline_stable/style_bible_evaluator.py:1503`

影响：

- V2 主路径已修复，但 legacy fallback 仍不可用
- 只要 `_evaluate_schema_validity()` 返回的是旧 `StyleBibleResult`，后续仍会把它直接送入 V2-only 的 `_evaluate_routing_hints()` / `_evaluate_worldbook_binding()`
- 当前最小 legacy payload 仍可稳定复现 `AttributeError`

当前原始代码：

```python
def _evaluate_schema_validity(
    style_bible_payload: dict[str, Any],
    source_bundle: dict[str, Any],
    rules: StyleBibleEvalRules,
) -> tuple[dict[str, Any], StyleBibleResult | StyleBibleResultV2 | None]:
    errors: list[str] = []
    model: StyleBibleResult | StyleBibleResultV2 | None = None
    try:
        model = StyleBibleResultV2.model_validate(style_bible_payload)
    except ValidationError:
        try:
            model = StyleBibleResult.model_validate(style_bible_payload)
        except ValidationError as exc:
            ...
```

```python
schema_check, parsed_style_bible = _evaluate_schema_validity(style_bible_payload, source_bundle, rules)
is_v2 = isinstance(parsed_style_bible, StyleBibleResultV2)
...
checks = [
    schema_check,
    ...
    _evaluate_routing_hints(parsed_style_bible, rules),
    _evaluate_worldbook_binding(parsed_style_bible, rules),
    ...
]
```

说明：

- 如果项目已经明确**不再需要** legacy `StyleBibleResult` 兼容，这个问题可以降级为 cleanup
- 但从当前实现看，代码显式保留了 fallback，因此它现在仍然是一个真实 bug

---

### Finding 2：`filtered_empty` 仍未真正被替换，Step 1.2 的 observability 只做完了一半

位置：

- `src/novel_pipeline_stable/style_bible_reducer.py:146`
- `src/novel_pipeline_stable/style_bible_reducer.py:3136`
- `src/novel_pipeline_stable/style_bible_reducer.py:4187`

影响：

- `DropTracker` 和 `_determine_empty_status()` 已经存在
- `_sanitize_rule_rows_for_path()` 也已支持 tracker
- 但 local reduce summary 与 section densify summary 仍然直接写死 `filtered_empty`
- 结果是：drop counters 有了，但最终状态仍无法直观看到“为什么空了”

当前原始代码：

```python
def _determine_empty_status(tracker: DropTracker | None = None, candidate_filter_trace: dict[str, Any] | None = None) -> str:
    if candidate_filter_trace and candidate_filter_trace.get("semantic_dedupe_drop_count", 0) > 0:
        return "candidate_filtered_by_semantic_dedupe"
    if candidate_filter_trace and candidate_filter_trace.get("slot_mismatch_drop_count", 0) > 0:
        return "candidate_filtered_by_slot_mismatch"
    if not tracker or not tracker.counters:
        return "filtered_empty"
    
    max_reason = max(tracker.counters.items(), key=lambda x: x[1])[0]
    return f"filtered_{max_reason}"
```

```python
"status": "success" if len(_iter_final_rule_items(final_result)) > 0 else "filtered_empty",
```

```python
"status": "success" if kept_rows else "filtered_empty",
```

进一步看 section densify 调用点：

```python
candidate_rows = _sanitize_rule_rows_for_path(
    response.parsed,
    target_path=request.path,
    reasoning_bundle=reasoning_bundle,
    memo_ref_pool=grounding_ref_pool,
    bucket_id_prefix=densify_bucket_id,
)
```

虽然 `_sanitize_rule_rows_for_path()` 已经支持 `tracker`，这里仍未传入，因此 densify 路径的 drop cause 也没有真正接到 summary status。

结论：

- Step 1.2 现在是“统计层已做，状态层未完成”
- 这不是阻塞主流程的 P0/P1 问题，但严格按计划要求，仍算未完全交付

---

### Finding 3：evaluator 入口级自动化测试仍缺位，导致 fallback 崩溃没有被现有 90 个测试捕获

证据：

- 本次 `unittest discover` 全绿，但我对 `evaluate_style_bible(...)` 做的最小 legacy 复现实验仍然会崩溃
- 当前测试集中可见的 evaluator 相关覆盖主要还是 helper 级别，例如 `tests/test_style_bible_eval_profiles.py`

位置：

- `tests/test_style_bible_eval_profiles.py`

影响：

- 当前测试能很好覆盖 reducer / schema / ragas / retriever
- 但还没有直接覆盖 evaluator 的真实入口行为
- 这就是为什么 V2 主路径修好了，但 legacy fallback 断路仍然可以漏进当前版本

结论：

- 这是测试缺口，不是主功能故障
- 但建议尽快补上入口级测试，尤其是：
  - 最小 V2 payload
  - legacy fallback payload

## 5. Phase 1 完成度再评估

| Step | 当前状态 | 说明 |
| --- | --- | --- |
| Step 1.1 | 已完成 | reasoning contract 回归已修复，相关测试通过 |
| Step 1.2 | 部分完成 | tracker 有了，但 summary status 仍未完成显式化 |
| Step 1.3 | 基本完成 | prompt / schema / config 已基本对齐 |
| Step 1.4 | 基本完成但有尾巴 | V2 evaluator 主路径已恢复；legacy fallback 仍断路 |

综合判断：

- **如果按“当前 V2 主流程是否可用”来验收：可以认为已经基本达标**
- **如果按原计划逐条严格验收：Phase 1 仍有 Step 1.2 与 legacy fallback 两个尾项未完全收口**

## 6. 建议的收尾顺序

### 优先级 A：收尾 evaluator fallback

可选策略二选一：

1. 真正支持 fallback  
   做法：在 `evaluate_style_bible()` 中分支处理 `StyleBibleResultV2` 与 `StyleBibleResult`

2. 明确放弃 fallback  
   做法：删除 legacy fallback，统一只接受 V2，并同步删掉相应兼容代码

### 优先级 B：把 `_determine_empty_status()` 真正接入 summary

建议至少替换这两个位置：

- `local_reduce_summary.json`
- `section_densify_summary.json`

并让 densify 调用链把 `tracker` 贯通进去。

### 优先级 C：补 evaluator 入口测试

建议新增两类测试：

1. `evaluate_style_bible()` 的最小 V2 入口测试
2. 若保留 fallback，则新增最小 legacy 入口测试

## 7. 本次复审涉及的关键文件

- `D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_ragas_eval.py`
- `D:\card\novel_pipeline\config\style_bible_section_targets.toml`
- `D:\card\novel_pipeline\tests\test_style_bible_local_reduce_contracts.py`
- `D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py`
- `D:\card\novel_pipeline\tests\test_style_bible_eval_profiles.py`
- `D:\card\novel_pipeline\tests\test_style_bible_v2_schema_contracts.py`
- `D:\card\novel_pipeline\tests\test_style_bible_ragas_eval.py`
- `D:\card\novel_pipeline\tests\test_style_extract_v2_contracts.py`
- `D:\card\novel_pipeline\tests\test_style_bible_router_batching_builder_guards.py`
- `D:\card\novel_pipeline\tests\test_hybrid_rag_contract.py`
- `D:\card\novel_pipeline\tests\test_hybrid_retriever.py`
