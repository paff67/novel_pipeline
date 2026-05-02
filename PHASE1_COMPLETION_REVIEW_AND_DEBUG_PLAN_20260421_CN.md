# Phase 1 完成度审查与 Debug 优化方案

日期：2026-04-21  
审查范围：`novel_pipeline_architectural_refactor_plan.md` 中的 Phase 1（Step 1.1 ~ 1.4）

## 1. 审查结论

当前代码**尚未完成 Phase 1**。

- Step 1.1：**未完成且出现回归**
- Step 1.2：**部分完成**
- Step 1.3：**部分完成**
- Step 1.4：**未完成，且 evaluator 当前存在可复现的运行时崩溃路径**

本次审查同时执行了 Phase 1 相关关键测试组与最小复现实验：

```powershell
& 'D:\card\novel_pipeline\.venv\Scripts\python.exe' -m unittest `
  D:\card\novel_pipeline\tests\test_style_bible_local_reduce_contracts.py `
  D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py `
  D:\card\novel_pipeline\tests\test_style_bible_eval_profiles.py `
  D:\card\novel_pipeline\tests\test_style_bible_v2_schema_contracts.py `
  D:\card\novel_pipeline\tests\test_style_bible_ragas_eval.py `
  D:\card\novel_pipeline\tests\test_style_extract_v2_contracts.py `
  D:\card\novel_pipeline\tests\test_style_bible_router_batching_builder_guards.py
```

结果：

- `Ran 73 tests in 0.361s`
- `FAILED (errors=12)`
- 12 个错误全部落在 `StyleBibleReasoningEntry` 当前 validator 回归上

额外最小复现还确认了 evaluator 的 V2 路径在当前实现下会直接崩溃：

- `StyleBibleReasoningEntry.model_validate({...})` -> `ValidationError`
- `evaluate_style_bible(...)` 输入最小 V2 payload -> `AttributeError: 'str' object has no attribute 'query_feature_matcher'`

## 2. Phase 1 对照表

| Phase 1 Step | 计划要求 | 当前状态 | 结论 |
| --- | --- | --- | --- |
| Step 1.1 | 收紧 Pydantic reasoning 合同，尽早失败 | 已增加 `after validator`，但 `before validator` 回归为返回 `None`；compact reasoning shape 直接失效 | 未完成 |
| Step 1.2 | 增加 granular drop tracking 与明确 empty status | 已引入 `DropTracker`，但 summary 仍使用 `filtered_empty`，section densify 也未把 tracker 打通到状态层 | 部分完成 |
| Step 1.3 | 对齐 prompt / schema / config 中 worldbook 与 routing 的契约 | `surface_specs` 和 prompt 已基本对齐；`style_bible_section_targets.toml` 仍把 `rag_worthy/worldbook_worthy` 保留为 route-shaped | 部分完成 |
| Step 1.4 | evaluator 面向 V2 结构化对象，不再依赖 flatten | 只改了局部检查函数；主入口仍先 flatten 再校验 legacy schema，并会把旧模型送进 V2 检查函数 | 未完成 |

## 3. 主要 Findings（按严重度排序）

### Finding 1：`StyleBibleReasoningEntry` 的 `before validator` 已经回归为返回 `None`，导致 compact reasoning entry 全部失效

位置：

- `src/novel_pipeline_stable/models.py:466`
- `src/novel_pipeline_stable/models.py:478`

影响：

- `StyleBibleReasoningEntry` 无法正常接收 dict 输入
- `StyleBibleLocalReducerOutput` 中的 `reasoning.entries[*]` 会在 Pydantic 校验阶段直接报错
- 已实测导致 73 个关键测试中的 12 个报错
- 这也意味着 Step 1.1 不仅没有完成，还引入了新的阻断性回归

当前原始代码：

```python
class StyleBibleReasoningEntry(BaseModel):
    reasoning_id: str = ""
    bucket_id: str = ""
    axis_ids: list[str] = Field(default_factory=list)
    claim: str = ""
    observed_commonality: str = ""
    mechanism_inference: str = ""
    downstream_constraint: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    anti_pattern_codes: list[str] = Field(default_factory=list)

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
    @model_validator(mode="after")
    def _ensure_meaningful_content(self) -> "StyleBibleReasoningEntry":
        has_text = any(
            _clean_model_text(val)
            for val in (self.claim, self.observed_commonality, self.mechanism_inference)
        )
        has_refs = any(_clean_model_text(ref) for ref in self.evidence_refs)
        if not has_text or not has_refs:
            raise ValueError(
                f"ReasoningEntry {self.reasoning_id} must have substantive content and evidence refs. "
                f"Content found: {has_text}, Refs found: {has_refs}"
            )
        return self
```

问题点非常明确：

- 缺少 `if text: payload["claim"] = text`
- 缺少 `return payload`

这会让 `before validator` 对 dict 输入隐式返回 `None`，随后触发：

```text
Input should be a valid dictionary or instance of StyleBibleReasoningEntry
```

最小复现：

```python
StyleBibleReasoningEntry.model_validate(
    {
        "reasoning_id": "reasoning_01",
        "claim": "combat endings must land through residue",
        "evidence_refs": ["scene:0141_003"],
    }
)
```

实际结果：

```text
ValidationError
1 validation error for StyleBibleReasoningEntry
  Input should be a valid dictionary or instance of StyleBibleReasoningEntry
```

---

### Finding 2：主 evaluator 仍然走 “flatten -> legacy schema -> V2 checks” 的混合路径，既没有完成结构化升级，也存在实际崩溃路径

位置：

- `src/novel_pipeline_stable/style_bible_evaluator.py:454`
- `src/novel_pipeline_stable/style_bible_evaluator.py:1003`
- `src/novel_pipeline_stable/style_bible_evaluator.py:1053`
- `src/novel_pipeline_stable/style_bible_evaluator.py:1375`
- `src/novel_pipeline_stable/models.py:303`
- `src/novel_pipeline_stable/models.py:315`

影响：

- Step 1.4 “deprecate flattening for V2 payloads” 实际并未完成
- evaluator 入口仍优先把 V2 payload flatten 成字符串化结构
- `_evaluate_schema_validity()` 仍然只校验 legacy `StyleBibleResult`
- 后续 `_evaluate_routing_hints()` / `_evaluate_worldbook_binding()` 却按 `StyleBibleResultV2` / `StyleBibleRuleItem` 去读字段
- 最小端到端复现实测会直接抛 `AttributeError`

当前原始代码 1：schema gate 仍然只返回 legacy `StyleBibleResult`

```python
def _evaluate_schema_validity(
    style_bible_payload: dict[str, Any],
    source_bundle: dict[str, Any],
    rules: StyleBibleEvalRules,
) -> tuple[dict[str, Any], StyleBibleResult | None]:
    errors: list[str] = []
    model: StyleBibleResult | None = None
    try:
        model = StyleBibleResult.model_validate(style_bible_payload)
    except ValidationError as exc:
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", []))
            errors.append(f"{location}: {item.get('msg', 'validation error')}")
```

当前原始代码 2：主入口仍旧优先 flatten

```python
rules = _load_rules(rules_config)
normalized_payload = export_flat_payload if export_flat_payload else style_bible_payload_to_flat(style_bible_payload)
if not normalized_payload:
    normalized_payload = style_bible_payload
schema_check, parsed_style_bible = _evaluate_schema_validity(normalized_payload, source_bundle, rules)
```

当前原始代码 3：后续检查又把 `parsed_style_bible` 当成 V2 结构对象使用

```python
checks = [
    schema_check,
    _evaluate_bundle_coverage(coverage_report, rules),
    _evaluate_grounding_trace_integrity(
        style_bible_payload,
        reasoning_payload if isinstance(reasoning_payload, dict) else {},
        reduce_trace_payload if isinstance(reduce_trace_payload, dict) else {},
        source_bundle,
        rules,
    ),
    _evaluate_section_completeness(normalized_payload, rules),
    _evaluate_required_axis_coverage(normalized_payload, rules),
    _evaluate_supporting_evidence(parsed_style_bible, source_bundle, rules),
    _evaluate_actionability(normalized_payload, rules),
    _evaluate_routing_hints(parsed_style_bible, rules),
    _evaluate_worldbook_binding(parsed_style_bible, rules),
    _evaluate_generic_language(normalized_payload, rules),
    _evaluate_anti_pattern_resistance(normalized_payload, rules),
]
```

当前原始代码 4：legacy schema 的 `worldbook_binding` 仍然是 `list[str]`

```python
class StyleBibleWorldbookBinding(BaseModel):
    rag_worthy: list[str] = Field(default_factory=list)
    worldbook_worthy: list[str] = Field(default_factory=list)
    routing_hints: list[str] = Field(default_factory=list)


class StyleBibleResult(BaseModel):
    style_id: str = ""
    scope: str = ""
    narrative_system: StyleBibleNarrativeSystem = Field(default_factory=StyleBibleNarrativeSystem)
    expression_system: StyleBibleExpressionSystem = Field(default_factory=StyleBibleExpressionSystem)
    aesthetics_system: StyleBibleAestheticsSystem = Field(default_factory=StyleBibleAestheticsSystem)
    voice_contract: StyleBibleVoiceContract = Field(default_factory=StyleBibleVoiceContract)
    character_arc_rules: list[str] = Field(default_factory=list)
    worldbook_binding: StyleBibleWorldbookBinding = Field(default_factory=StyleBibleWorldbookBinding)
    negative_rules: list[str] = Field(default_factory=list)
    supporting_evidence: list[StyleBibleEvidence] = Field(default_factory=list)
```

当前原始代码 5：V2 检查函数按结构化 rule 读字段

```python
def _evaluate_routing_hints(style_bible: StyleBibleResultV2, rules: StyleBibleEvalRules) -> dict[str, Any]:
    items = list(style_bible.worldbook_binding.routing_hints)
    useful_count = 0
    for item in items:
        matcher = _clean_text(item.query_feature_matcher)
        target = _clean_text(item.route_target_action)
        if matcher and target:
            useful_count += 1
        elif _is_routing_hint_useful(_clean_text(item.text), rules):
            useful_count += 1
```

最小端到端复现实验结果：

```text
AttributeError
'str' object has no attribute 'query_feature_matcher'
```

这说明当前 evaluator 不是“质量还不够好”，而是存在明确的运行时断裂。

---

### Finding 3：`worldbook_binding.rag_worthy` / `worldbook_binding.worldbook_worthy` 的 config 仍保留旧 route-shaped slot contract，和 prompt/schema 继续错位

位置：

- `src/novel_pipeline_stable/style_bible_surface_specs.py:245`
- `config/style_bible_section_targets.toml:1186`
- `config/style_bible_section_targets.toml:1248`
- `prompts/style_bible_section_densify.md:36`
- `prompts/style_bible_section_densify.md:41`

影响：

- Step 1.3 只完成了一部分
- `surface_specs` 已把两个 worldbook 路径要求切到 `trigger + constraint`
- prompt 也明确把 `routing_hints` 与 `rag/worldbook` 分离
- 但 `style_bible_section_targets.toml` 里的 slot contract 仍把 `rag/worldbook` 写成 `query_feature_matcher + route_target_action`
- 实际效果是：prompt、schema、config 三者仍然没有完全对齐

当前原始代码 1：`surface_specs` 已改成 `trigger + constraint`

```python
SurfacePath.WORLDBOOK_RAG_WORTHY: SurfacePathSpec(
    path=SurfacePath.WORLDBOOK_RAG_WORTHY,
    cardinality="list",
    merge_strategy="rule_dedupe_aggressive",
    required_fields=("trigger", "constraint"),
    max_items=8,
    high_risk=True,
    conflict_policy="drop_group",
    aggressive_group_fields=("trigger", "text"),
    conflict_field="constraint",
),
SurfacePath.WORLDBOOK_WORLDBOOK_WORTHY: SurfacePathSpec(
    path=SurfacePath.WORLDBOOK_WORLDBOOK_WORTHY,
    cardinality="list",
    merge_strategy="rule_dedupe_aggressive",
    required_fields=("trigger", "constraint"),
    max_items=8,
    high_risk=True,
    conflict_policy="drop_group",
    aggressive_group_fields=("trigger", "text"),
    conflict_field="constraint",
),
```

当前原始代码 2：prompt 已明确区分 routing 与 worldbook

```md
【如果 `target_path` 是 `worldbook_binding.routing_hints`】
- `text` 必须落成“当……时，路由到……并优先返回……”这类可执行句式。
- `query_feature_matcher` 要写具体情境，`route_target_action` 要写清检索目标与返回重点。

【如果 `target_path` 是 `worldbook_binding.rag_worthy` / `worldbook_binding.worldbook_worthy`】
- `text` 必须是可独立入库的原子设定，不是写作风格评论。
- 一条只写一个机构、门槛、流程、资源、限制或社会常识，不要把多个机制揉成一条总论。
- 直接利用本次检索到的 `retrieved_reasoning_entries` 与真实 `evidence_refs`，把 scene-level evidence 提炼成更稳定的机构 / 流程 / 规则原子。
```

当前原始代码 3：但 config 里的两个 worldbook path 仍旧要求 route-shaped slot

```toml
[[path_targets]]
path = "worldbook_binding.rag_worthy"
target_count = 4
max_new_rows = 2
retrieval_top_k = 12
downstream_shape = "Emit retrievable rules that can be recalled at generation time."

[[path_targets.slot_specs]]
slot_id = "repayment_retrieval"
downstream_shape = "query_feature_matcher + route_target_action"

[[path_targets.slot_specs]]
slot_id = "approval_chain_retrieval"
downstream_shape = "query_feature_matcher + route_target_action"
```

```toml
[[path_targets]]
path = "worldbook_binding.worldbook_worthy"
target_count = 4
max_new_rows = 2
retrieval_top_k = 12
downstream_shape = "Emit stable worldbook facts or rules that should survive beyond one scene."

[[path_targets.slot_specs]]
slot_id = "institutional_workflow_worldbook"
downstream_shape = "query_feature_matcher + route_target_action"

[[path_targets.slot_specs]]
slot_id = "repayment_economy_worldbook"
downstream_shape = "query_feature_matcher + route_target_action"
```

程序化读取配置得到的实际结果也是：

```text
PATH worldbook_binding.rag_worthy
slot_shapes ['query_feature_matcher + route_target_action', ...]

PATH worldbook_binding.worldbook_worthy
slot_shapes ['query_feature_matcher + route_target_action', ...]
```

---

### Finding 4：observability 只做了一半，`filtered_empty` 仍未被替换成明确状态，section densify 也没有把 tracker 打通

位置：

- `src/novel_pipeline_stable/style_bible_reducer.py:136`
- `src/novel_pipeline_stable/style_bible_reducer.py:953`
- `src/novel_pipeline_stable/style_bible_reducer.py:1121`
- `src/novel_pipeline_stable/style_bible_reducer.py:2063`
- `src/novel_pipeline_stable/style_bible_reducer.py:3095`
- `src/novel_pipeline_stable/style_bible_reducer.py:4145`

影响：

- Step 1.2 只完成了 “记录一些 drop counters”，没有完成 “让 empty outcome 具备明确状态语义”
- local reduce summary 仍然只有 `success` / `filtered_empty`
- section densify summary 仍然只有 `success` / `filtered_empty`
- `_sanitize_rule_rows_for_path()` 也没有接收 tracker，导致 densify pass 没法记录更细的 rule-level drop cause

当前原始代码 1：`DropTracker` 已存在

```python
@dataclass(slots=True)
class DropTracker:
    counters: dict[str, int] = field(default_factory=dict)

    def track(self, reason: str, count: int = 1):
        self.counters[reason] = self.counters.get(reason, 0) + count

    def dump(self) -> dict[str, int]:
        return dict(self.counters)
```

当前原始代码 2：sanitization 已经能记录细粒度原因

```python
if not claim or not refs:
    if tracker:
        if not claim:
            tracker.track("reasoning_missing_claim")
        if not refs:
            tracker.track("reasoning_missing_refs")
    continue
```

```python
if item is None:
    if tracker:
        tracker.track(f"{path}_rule_coerce_failed")
    return None
text = clean_text(item.text)
if not text:
    if tracker:
        tracker.track(f"{path}_rule_missing_text")
    return None
```

当前原始代码 3：但 local reduce summary 仍然写死 `filtered_empty`

```python
write_json(
    bucket_output_dir / "local_reduce_summary.json",
    {
        "status": "success" if len(_iter_final_rule_items(final_result)) > 0 else "filtered_empty",
        "bucket_id": clean_text(bucket_memo.bucket_id),
        "memo_id": clean_text(bucket_memo.memo_id),
        "batch_ids": batch_ids,
        "style_id": clean_text(record.get("style_id")),
        "scope": clean_text(record.get("scope")),
        "reasoning_entry_count": len(reasoning_bundle.entries),
        "rule_count": len(_iter_final_rule_items(final_result)),
        "reduced_ref_count": len(reduced_refs),
        "grounding_ref_count": len(grounding_ref_pool),
        "assembler_conflict_count": len(assembler_conflicts),
        "drop_stats": tracker.dump(),
        "request_metrics": request_metrics,
        "usage_metadata": usage_metadata,
    },
)
```

当前原始代码 4：section densify summary 也仍然写死 `filtered_empty`

```python
summary_record = {
    "status": "success" if kept_rows else "filtered_empty",
    "target_path": request.path,
    "actual_count": int(request.actual_count),
    "target_count": int(request.target_count),
    "deficit": int(request.deficit),
    "missing_slot_ids": [slot.slot_id for slot in missing_slots],
    "kept_rule_count": len(kept_rows),
    "retrieved_reasoning_count": len(densify_bundle.get("retrieved_reasoning_entries", [])),
    "semantic_dedupe_drop_count": int(candidate_filter_trace.get("semantic_dedupe_drop_count", 0) or 0),
    "gray_keep_count": int(candidate_filter_trace.get("gray_keep_count", 0) or 0),
    "request_metrics": request_metrics,
    "usage_metadata": usage_metadata,
    "slot_coverage": slot_coverage_trace,
    "retrieval": retrieval_trace,
    "candidate_filter": candidate_filter_trace,
    "output_dir": str(output_dir.resolve()),
}
```

当前原始代码 5：section densify 使用的 `_sanitize_rule_rows_for_path()` 根本没有 tracker 参数

```python
def _sanitize_rule_rows_for_path(
    partial_output: StyleBibleLocalReducerOutput,
    *,
    target_path: str,
    reasoning_bundle: StyleBibleReasoningBundle,
    memo_ref_pool: set[str],
    bucket_id_prefix: str,
) -> list[StyleBibleRuleItem]:
    reasoning_by_id, reasoning_by_text_key = _reasoning_lookup(reasoning_bundle)
    sanitized_rows: list[StyleBibleRuleItem] = []
    normalized_target_path = clean_text(target_path)
    for item_index, row in enumerate(partial_output.final.rule_rows, start=1):
        sanitized = _sanitize_local_rule_row(
            row,
            item_index=item_index,
            reasoning_by_id=reasoning_by_id,
            reasoning_by_text_key=reasoning_by_text_key,
            memo_ref_pool=memo_ref_pool,
            bucket_id_prefix=bucket_id_prefix,
        )
        if sanitized is None:
            continue
        path, rule = sanitized
        if clean_text(path) != normalized_target_path:
            continue
        sanitized_rows.append(rule)
    return sanitized_rows
```

这说明当前 reducer 已经有“统计器”，但还没有真正完成 plan 要求的“状态可解释化”。

## 4. 补充风险与测试缺口

### 4.1 `style_bible_ragas_eval.py` 仍对 worldbook/rag 的 `query_feature_matcher` 给更高 contract score

位置：

- `src/novel_pipeline_stable/style_bible_ragas_eval.py:156`

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

    if matcher:
        return 1.0
    if trigger and constraint:
        return 0.7
    if trigger or constraint:
        return 0.45
    return 0.0
```

风险说明：

- 如果 `rag_worthy/worldbook_worthy` 的 Phase 1 目标是朝 `trigger + constraint` 或“原子事实”收敛，那么当前离线评估仍然在奖励旧 matcher 形状
- 这会让评估与目标契约之间继续出现偏差

### 4.2 当前没有 evaluator 入口级别的自动化测试

现状：

- `tests/test_style_bible_eval_profiles.py` 只覆盖了局部 helper
- 仓库中没有直接覆盖 `evaluate_style_bible()` / `run_style_bible_evaluation()` 的用例

风险说明：

- `flatten -> legacy schema -> V2 checks` 这种断裂路径能够直接进入主分支，说明当前测试尚未覆盖 evaluator 的真实入口

## 5. 建议的 Debug / 优化顺序

### 优先级 P0：先修复模型层回归，恢复 reasoning 合同

1. 修复 `StyleBibleReasoningEntry._hydrate_compact_reasoning_entry()`
2. 补回：
   - `if text: payload["claim"] = text`
   - `return payload`
3. 追加最小测试：
   - `StyleBibleReasoningEntry.model_validate({...})`
   - `StyleBibleLocalReducerOutput` 接收 compact reasoning shape
   - reasoning ref 依赖 evidence refs 自动回填

### 优先级 P1：把 evaluator 彻底切成 V2 主路径

1. 在入口处优先识别 V2 payload，而不是先 flatten
2. V2 payload 应直接 `StyleBibleResultV2.model_validate(style_bible_payload)`
3. 只有 legacy/V1 数据才允许继续走 `style_bible_payload_to_flat() + StyleBibleResult`
4. `_evaluate_section_completeness / _evaluate_required_axis_coverage / _evaluate_actionability / _evaluate_generic_language / _evaluate_anti_pattern_resistance`
   需要决定：
   - 是接受 V2 原始 payload
   - 还是显式只对 legacy flat payload 使用
5. 增加 evaluator 入口级测试，覆盖最小 V2 payload 中的：
   - routing_hints
   - rag_worthy
   - worldbook_worthy

### 优先级 P1：同步 worldbook config，彻底完成 prompt / schema / config 对齐

1. 修改 `config/style_bible_section_targets.toml`
2. 将：
   - `worldbook_binding.rag_worthy`
   - `worldbook_binding.worldbook_worthy`
   的 `slot_specs[*].downstream_shape`
   从 `query_feature_matcher + route_target_action`
   调整为与当前 schema/prompt 一致的契约
3. 同步补测试：
   - 断言 `load_style_bible_section_targets()` 读出的 shape 与计划一致

### 优先级 P2：补全 reducer observability 的“状态层”

1. 为 local reduce / section densify 定义明确状态枚举，例如：
   - `reasoning_sanitized_empty`
   - `rule_rows_lost_reasoning_ref`
   - `rule_rows_lost_evidence_refs`
   - `candidate_filtered_by_semantic_dedupe`
   - `candidate_filtered_by_slot_mismatch`
2. 让 `_sanitize_rule_rows_for_path()` 接受 tracker
3. 让 section densify 的 summary status 由 tracker / candidate_filter 共同决定，而不是只看 `kept_rows`
4. 保留 `drop_stats`，但不要再把最终空结果统一折叠成 `filtered_empty`

### 优先级 P2：校准 `ragas` 离线评估的 contract scoring

1. 明确 `rag/worldbook` 的最终 contract
2. 如果最终 contract 不再偏向 matcher，则调整 `_contract_score()`
3. 更新 `tests/test_style_bible_ragas_eval.py`，避免继续用 matcher-only 的 worldbook/rag 示例作为默认正例

## 6. 建议的回归验证顺序

建议修复后按以下顺序回归：

1. 先跑当前这组 73 个 Phase 1 关键测试
2. 再补跑 evaluator 入口级新增测试
3. 再执行一次小样本 smoke test，重点观察：
   - 是否还会出现 `StyleBibleReasoningEntry` 校验错误
   - evaluator 是否能完整输出评估报告
   - local reduce / section densify summary 是否已经不再统一落成 `filtered_empty`

## 7. 本次审查涉及的核心文件

- `D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_surface_specs.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_ragas_eval.py`
- `D:\card\novel_pipeline\config\style_bible_section_targets.toml`
- `D:\card\novel_pipeline\prompts\style_bible_section_densify.md`
- `D:\card\novel_pipeline\tests\test_style_bible_local_reduce_contracts.py`
- `D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py`
- `D:\card\novel_pipeline\tests\test_style_bible_eval_profiles.py`
- `D:\card\novel_pipeline\tests\test_style_bible_v2_schema_contracts.py`
- `D:\card\novel_pipeline\tests\test_style_bible_ragas_eval.py`
- `D:\card\novel_pipeline\tests\test_style_extract_v2_contracts.py`
- `D:\card\novel_pipeline\tests\test_style_bible_router_batching_builder_guards.py`
