# Full Live Node1 本地代码审查与 Debug 优化方案（2026-04-20）

## 1. 审查目标

基于以下输入，固定一份面向本地代码的审查与 debug 方案：

- 最新错误报告：
  - `D:\card\novel_pipeline\FULL_LIVE_NODE1_LOG_ROOT_CAUSE_REPORT_20260420_CN.md`
- 真实 live 运行产物：
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible\_section_densify\*\pass_01\*.json`
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible\style_bible_final.json`
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible_eval\style_eval_report.json`
- 本地实现代码：
  - `src/novel_pipeline_stable/models.py`
  - `src/novel_pipeline_stable/style_bible_reducer.py`
  - `src/novel_pipeline_stable/style_bible_evaluator.py`
  - `src/novel_pipeline_stable/style_bible_surface_specs.py`
  - `src/novel_pipeline_stable/hybrid_retriever.py`
  - `src/novel_pipeline_stable/style_bible_ragas_eval.py`
  - `prompts/style_bible_section_densify.md`
  - `config/style_bible_section_targets.toml`
  - `tests/test_style_bible_local_reduce_contracts.py`
  - `tests/test_style_bible_hierarchical_reducer.py`
  - `tests/test_style_bible_eval_profiles.py`

## 2. 执行摘要

### 2.1 主结论

当前 full live 的主故障，确实不是数据量不足、embedding 失效或 semantic dedupe 误删，而是：

1. densify 模型产出了 `final.rule_rows`
2. 但被这些 row 引用的 `reasoning.entries` 大量是“只有 `reasoning_id + evidence_refs` 的语义空壳”
3. 模型输出校验层接受了这类空壳
4. reducer 清洗层又把空壳 reasoning 过滤掉
5. rule 再因为失去 `reasoning_ref` 锚点被清空
6. `candidate_rows=[]` 后直接落为 `filtered_empty`

### 2.2 次级结构问题

除 densify 主根因外，还存在三类明显的控制面问题：

- `filtered_empty` 的诊断粒度过粗，掩盖了“入口被清空”和“slot 阈值偏严”这两种完全不同的问题。
- `routing_hints`、`rag_worthy`、`worldbook_worthy` 三条 worldbook 路径的合同已经混线。
- evaluator 仍主要按扁平 text 评估，无法稳定识别 V2 结构字段带来的信息。

### 2.3 测试现状

本地相关 `unittest` 当前是绿的，但测试集没有覆盖这次 live 失效模式。

本地验证命令：

```powershell
& 'D:\card\novel_pipeline\.venv\Scripts\python.exe' -m unittest `
  D:\card\novel_pipeline\tests\test_style_bible_local_reduce_contracts.py `
  D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py `
  D:\card\novel_pipeline\tests\test_style_bible_eval_profiles.py
```

结果：

```text
............................
----------------------------------------------------------------------
Ran 28 tests in 1.048s

OK
```

这说明当前问题不是“已有断言红了没人处理”，而是“测试盲区正好绕开了 live 现场”。

## 3. 发现一：densify reasoning 合同在模型输出层与 reducer 清洗层之间断裂

### 3.1 现场证据

`negative_rules/pass_01/section_densify_partial.json` 中，`reasoning.entries` 为空壳，但 `final.rule_rows` 已经引用这些空壳：

来源：
`D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible\_section_densify\negative_rules\pass_01\section_densify_partial.json`

```json
{
  "reasoning": {
    "entries": [
      {
        "reasoning_id": "exam_screening__r_neg_no_outrage_01",
        "claim": "",
        "observed_commonality": "",
        "mechanism_inference": "",
        "downstream_constraint": "",
        "evidence_refs": ["0165_0166", "0047_0048", "0223_0224", "scene:0187_003", "scene:0216_002"]
      }
    ]
  },
  "final": {
    "rule_rows": [
      {
        "rule_id": "negative_rules__no_generic_bureaucracy__01",
        "_reasoning_ref": "exam_screening__r_neg_no_outrage_01",
        "surface_path": "negative_rules"
      }
    ]
  }
}
```

同一路径 summary 最终直接变成了 `filtered_empty`：

来源：
`D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible\_section_densify\negative_rules\pass_01\section_densify_summary.json`

```json
{
  "status": "filtered_empty",
  "kept_rule_count": 0,
  "retrieved_reasoning_count": 12,
  "semantic_dedupe_drop_count": 0,
  "gray_keep_count": 0
}
```

而且该 summary 同时显示：

- `retrieval.candidate_count = 250`
- slot coverage 已计算出分数

这说明问题发生在 slot filter 之前的更早阶段，不是“候选进入 filter 后全被 slot 阈值打掉”。

### 3.2 对应原有代码

#### A. `StyleBibleReasoningEntry` 允许 compact reasoning 进入，但没有要求语义字段非空

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`

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
        if text and not _clean_model_text(payload.get("claim")):
            payload["claim"] = text
        return payload
```

#### B. `StyleBibleLocalReducerOutput` 只校验 `_reasoning_ref` 可对齐，不校验 reasoning 本身是否可用

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`

```python
@model_validator(mode="after")
def _validate_reasoning_references(self) -> "StyleBibleLocalReducerOutput":
    reasoning_ids = {
        _clean_model_text(entry.reasoning_id)
        for entry in self.reasoning.entries
        if _clean_model_text(entry.reasoning_id)
    }
    seen_rule_ids: set[str] = set()
    for row in self.final.rule_rows:
        rule_id = _clean_model_text(row.rule_id)
        if rule_id in seen_rule_ids:
            raise ValueError(f"Duplicate local reducer rule_id: {rule_id}")
        seen_rule_ids.add(rule_id)

        reasoning_ref = _clean_model_text(row.reasoning_ref)
        if reasoning_ref and reasoning_ref not in reasoning_ids:
            inferred_reasoning_ref = _infer_local_rule_reasoning_ref(
                reasoning_entries=self.reasoning.entries,
                rule=row,
            )
            if inferred_reasoning_ref in reasoning_ids:
                row.reasoning_ref = inferred_reasoning_ref
                reasoning_ref = inferred_reasoning_ref
        if reasoning_ref and reasoning_ref not in reasoning_ids:
            raise ValueError(f"Local reducer row contains unresolved _reasoning_ref: {reasoning_ref}")
    return self
```

#### C. reducer 清洗层会直接丢弃无 `claim / mechanism / observed` 的 reasoning

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`

```python
def _sanitize_reasoning_bundle(
    reasoning: StyleBibleReasoningBundle,
    *,
    style_id_hint: str,
    scope_hint: str,
    memo_ref_pool: set[str],
    reasoning_id_prefix: str = "",
) -> StyleBibleReasoningBundle:
    rows: list[StyleBibleReasoningEntry] = []
    index_by_key: dict[str, int] = {}
    used_ids: set[str] = set()
    for entry in reasoning.entries:
        claim = clean_text(entry.claim) or clean_text(entry.mechanism_inference) or clean_text(entry.observed_commonality)
        refs = [ref for ref in _unique_strings(entry.evidence_refs) if ref in memo_ref_pool]
        if not claim or not refs:
            continue
```

#### D. rule sanitize 又要求 reasoning_ref 必须能回到 surviving reasoning

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`

```python
def _sanitize_rule_item(
    value: Any,
    *,
    path: str,
    item_index: int,
    reasoning_by_id: dict[str, StyleBibleReasoningEntry],
    reasoning_by_text_key: dict[str, str],
    memo_ref_pool: set[str],
    bucket_id_prefix: str = "",
) -> StyleBibleRuleItem | None:
    item = _coerce_rule_item(value)
    if item is None:
        return None
    text = clean_text(item.text)
    if not text:
        return None
    candidate_refs = [ref for ref in _unique_strings(item.evidence_refs) if ref in memo_ref_pool]
    reasoning_ref = clean_text(item.reasoning_ref)
    if bucket_id_prefix and reasoning_ref not in reasoning_by_id:
        scoped_reasoning_ref = _bucket_scoped_identifier(bucket_id_prefix, reasoning_ref, reasoning_ref)
        if scoped_reasoning_ref in reasoning_by_id:
            reasoning_ref = scoped_reasoning_ref
    if reasoning_ref not in reasoning_by_id:
        reasoning_ref = reasoning_by_text_key.get(_normalize_text_key(text), "")
    if reasoning_ref not in reasoning_by_id:
        reasoning_ref = _infer_reasoning_ref_from_evidence_refs(
            rule_refs=candidate_refs,
            reasoning_by_id=reasoning_by_id,
        )
    if reasoning_ref not in reasoning_by_id:
        return None
```

#### E. 一旦 `candidate_rows=[]`，filter 会直接返回空

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`

```python
def _filter_section_densify_candidates(
    *,
    candidate_rows: list[StyleBibleRuleItem],
    existing_rows: list[StyleBibleRuleItem],
    missing_slots: list[SectionSlotSpec],
    path_target: SectionPathTarget,
    max_keep: int,
    embedding_client: StableOpenAICompatibleEmbeddingClient,
    request_key_prefix: str,
    retrieved_reasoning_entries: list[dict[str, Any]] | None = None,
) -> tuple[list[StyleBibleRuleItem], dict[str, Any]]:
    if not candidate_rows or max_keep <= 0:
        return [], {"candidates": [], "kept_rule_ids": [], "semantic_dedupe_drops": []}
```

#### F. summary 只会把“没保留到 row”的路径统一写成 `filtered_empty`

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`

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
}
```

### 3.3 结论

这里是非常明确的合同断裂：

- 模型输出层允许“reasoning 只有 id 和 refs”
- reducer 清洗层却要求 reasoning 至少要有语义内容

因此当前 densify 的问题不是“没生成”，而是“生成后被 sanitize 链路整体清空”。

### 3.4 Debug 方案

#### P0-1. 统一“可用 reasoning”判定

建议新增统一 helper，例如：

- `claim`
- `observed_commonality`
- `mechanism_inference`
- `downstream_constraint`

四者至少一项非空，且 `evidence_refs` 非空，才视为可用 reasoning。

这样可以避免 prompt 明确要求写 `downstream_constraint`，但 sanitizer 完全不看它的情况。

#### P0-2. 在 validator 或 densify pass 提前拦截 semantic-empty shell

两种可选做法：

1. 强校验：
   - 对所有被 `final.rule_rows[*]._reasoning_ref` 引用到的 reasoning，要求满足“可用 reasoning”标准
2. 弱校验 + 显式状态：
   - 允许模型响应先过 schema
   - 但在 `_run_section_densify_pass()` 中显式识别并落状态 `reasoning_sanitized_empty`

建议先用方案 2，兼顾兼容性和可观测性。

#### P0-3. 补 regression tests

必须新增以下用例：

- `reasoning.entries` 只有 `reasoning_id + evidence_refs`，`rule_rows` 非空
- sanitize 后 reasoning 被清空，summary 不应再落为普通 `filtered_empty`
- 若保留 compact reasoning 兼容，则需验证 `text/claim/downstream_constraint` 至少一种可以保住 reasoning

## 4. 发现二：`filtered_empty` 的诊断粒度不足，掩盖了第一现场

### 4.1 现场证据

`negative_rules` 的 summary 同时存在以下信号：

- `status = filtered_empty`
- `retrieved_reasoning_count = 12`
- slot coverage 已经算出多个 `best_score`
- 但 `kept_rule_count = 0`

这意味着“没候选进入 filter”和“候选进来后被阈值打掉”被混成了同一种状态。

### 4.2 对应原有代码

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`

```python
if not candidate_rows or max_keep <= 0:
    return [], {"candidates": [], "kept_rule_ids": [], "semantic_dedupe_drops": []}
```

以及：

```python
summary_record = {
    "status": "success" if kept_rows else "filtered_empty",
    ...
    "candidate_filter": candidate_filter_trace,
}
```

### 4.3 结论

当前 summary 不足以回答这三个关键问题：

1. 是 reasoning 先被清空了？
2. 还是 row 因 reasoning_ref 丢失被清空了？
3. 还是 row 进入 filter 后被 slot/semantic dedupe 打掉了？

### 4.4 Debug 方案

建议在 summary 和 trace 中新增以下字段：

- `candidate_row_count_before_filter`
- `reasoning_entry_count_before_sanitize`
- `reasoning_entry_count_after_sanitize`
- `dropped_reasoning_count`
- `dropped_rule_row_count_before_filter`
- `drop_counts_by_reason`

并把 status 至少拆成：

- `reasoning_sanitized_empty`
- `rule_rows_lost_reasoning_ref`
- `path_mismatch_after_sanitize`
- `slot_miss_after_filter`
- `semantic_dedupe_empty`
- `filtered_empty`（仅保留为兜底）

## 5. 发现三：`routing_hints`、`rag_worthy`、`worldbook_worthy` 的合同已经混线

### 5.1 现场证据

最终产物里，`rag_worthy` 和 `worldbook_worthy` 已经明显被写成“查询触发 + 路由动作”句，而不是纯原子设定：

来源：
`D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible\style_bible_final.json`

```json
{
  "rule_id": "asset_repricing__rag_worthy__foundation_exam_budget_screening_01",
  "text": "当问题询问筑基考试为什么追加报名测试、为什么不再复考道心或为什么只留前二十时，检索相关考试筛选与预算规则事实，并返回节流、筛才和奖励配额的具体逻辑。",
  "query_feature_matcher": "当问题询问筑基考试报名测试的目的、道心为什么不继续考、为什么只留前二十名、专家级功法奖励从何而来或考试规则如何按预算调整时。",
  "route_target_action": "优先检索与筑基考试筛选和预算规则相关的事实，并返回报名测试..."
}
```

```json
{
  "rule_id": "institutional_pipeline__credentialized_access_gate_01",
  "text": "把修仙资格写成证件、使用权与合规审批。",
  "query_feature_matcher": "当查询提到申请、购买或启用灵根、功法、药物、补剂等增强资源，并追问合法性、学籍或使用权时",
  "route_target_action": "路由到 worldbook 中关于学生卡、学籍、使用权和合规审批的稳定设定条目。"
}
```

而 `routing_hints` 中又混入明显偏 worldbook 原子设定的句子：

```json
{
  "rule_id": "exam_screening__exam_screening_routing_usage_rights_01",
  "text": "未成年修仙监管与功法使用权是稳定设定：高天赋学生也必须面对年龄限制、付费使用权、软禁和欠债风险。"
}
```

### 5.2 对应原有代码

#### A. prompt 明确要求 `rag_worthy/worldbook_worthy` 输出原子设定

来源：
`D:\card\novel_pipeline\prompts\style_bible_section_densify.md`

```md
【如果 `target_path` 是 `worldbook_binding.rag_worthy` / `worldbook_binding.worldbook_worthy`】
- `text` 必须是可独立入库的原子设定，不是写作风格评论。
- 一条只写一个机构、门槛、流程、资源、限制或社会常识，不要把多个机制揉成一条总论。
- 直接利用本次检索到的 `retrieved_reasoning_entries` 与真实 `evidence_refs`，把 scene-level evidence 提炼成更稳定的机构 / 流程 / 规则原子；不要等待外部 atom 层，也不要只做 existing row 的近义重写。
```

#### B. surface spec 却把三条路径都要求成 `query_feature_matcher + route_target_action`

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_surface_specs.py`

```python
SurfacePath.WORLDBOOK_RAG_WORTHY: SurfacePathSpec(
    path=SurfacePath.WORLDBOOK_RAG_WORTHY,
    cardinality="list",
    merge_strategy="rule_dedupe_aggressive",
    required_fields=("query_feature_matcher", "route_target_action"),
    max_items=8,
    high_risk=True,
    conflict_policy="drop_group",
    aggressive_group_fields=("query_feature_matcher", "trigger", "text"),
    conflict_field="route_target_action",
),
SurfacePath.WORLDBOOK_WORLDBOOK_WORTHY: SurfacePathSpec(
    path=SurfacePath.WORLDBOOK_WORLDBOOK_WORTHY,
    cardinality="list",
    merge_strategy="rule_dedupe_aggressive",
    required_fields=("query_feature_matcher", "route_target_action"),
    max_items=8,
    high_risk=True,
    conflict_policy="drop_group",
    aggressive_group_fields=("query_feature_matcher", "trigger", "text"),
    conflict_field="route_target_action",
),
SurfacePath.WORLDBOOK_ROUTING_HINTS: SurfacePathSpec(
    path=SurfacePath.WORLDBOOK_ROUTING_HINTS,
    cardinality="list",
    merge_strategy="rule_dedupe_aggressive",
    required_fields=("query_feature_matcher", "route_target_action"),
    max_items=8,
    high_risk=True,
    conflict_policy="drop_group",
    aggressive_group_fields=("query_feature_matcher", "trigger", "text"),
    conflict_field="route_target_action",
),
```

#### C. section targets 的 slot 级配置也把 `rag/worldbook` 写成了路由型 shape

来源：
`D:\card\novel_pipeline\config\style_bible_section_targets.toml`

```toml
[[path_targets]]
path = "worldbook_binding.rag_worthy"
target_count = 4
max_new_rows = 2
downstream_shape = "Emit retrievable rules that can be recalled at generation time."

[[path_targets.slot_specs]]
slot_id = "repayment_retrieval"
downstream_shape = "query_feature_matcher + route_target_action"
fresh_evidence_required = true
```

```toml
[[path_targets]]
path = "worldbook_binding.worldbook_worthy"
target_count = 4
max_new_rows = 2
downstream_shape = "Emit stable worldbook facts or rules that should survive beyond one scene."

[[path_targets.slot_specs]]
slot_id = "institutional_workflow_worldbook"
downstream_shape = "query_feature_matcher + route_target_action"
fresh_evidence_required = true
```

### 5.3 结论

这已经不是单纯 prompt 问题，而是配置层和 schema 层同时把 worldbook 原子和 routing 句混成了一个合同。

### 5.4 Debug 方案

#### P1-1. 拆分三条路径合同

- `worldbook_binding.routing_hints`
  - 保留 `query_feature_matcher + route_target_action`
- `worldbook_binding.rag_worthy`
  - 回到“可直接检索的原子事实/规则”
- `worldbook_binding.worldbook_worthy`
  - 回到“稳定可入库的机构/流程/门槛/资源/限制”

#### P1-2. 同步修改三层定义

必须一起改：

- `prompts/style_bible_section_densify.md`
- `src/novel_pipeline_stable/style_bible_surface_specs.py`
- `config/style_bible_section_targets.toml`

否则模型、schema、slot filter 仍会互相拉扯。

#### P1-3. 消费侧兼容改造

因为以下消费侧当前默认三类 worldbook item 都可能带路由字段，所以合同拆分时要一起兼容：

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\hybrid_retriever.py`

```python
search_parts = [
    text,
    item.get("trigger"),
    item.get("constraint"),
    item.get("query_feature_matcher"),
    item.get("route_target_action"),
    item.get("forbidden_action"),
    item.get("correction_guideline"),
]
```

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_ragas_eval.py`

```python
def _compose_query_text(item_type: str, item: dict[str, Any]) -> str:
    fields = [item.get("query_feature_matcher", ""), item.get("route_target_action", "")]
    if item_type != "routing_hints":
        fields.extend([item.get("trigger", ""), item.get("constraint", "")])
    return " | ".join(_unique_strings(fields))
```

## 6. 发现四：evaluator 仍主要按扁平 text 工作，对 V2 结构字段感知不足

### 6.1 对应原有代码

#### A. evaluator 先把 V2 payload 扁平成 flat 结构

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`

```python
def style_bible_payload_to_flat(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flattened = _flatten_rule_node(payload)
    return flattened if isinstance(flattened, dict) else {}
```

#### B. schema validity 仍基于 legacy `StyleBibleResult`

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`

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
        ...
```

#### C. `routing_hints` useful 判定只看 `text`

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`

```python
def _is_routing_hint_useful(text: str, rules: StyleBibleEvalRules) -> bool:
    normalized = _clean_text(text)
    if len(normalized) < int(rules.thresholds.get("min_item_specific_length", 6)):
        return False
    has_trigger = any(cue in normalized for cue in rules.routing_trigger_cues)
    has_route = any(cue in normalized for cue in rules.routing_route_cues)
    return has_trigger and has_route

def _evaluate_routing_hints(style_bible: StyleBibleResult, rules: StyleBibleEvalRules) -> dict[str, Any]:
    hints = [_clean_text(item) for item in style_bible.worldbook_binding.routing_hints if _clean_text(item)]
    useful = [hint for hint in hints if _is_routing_hint_useful(hint, rules)]
```

#### D. `worldbook_binding` 检查也主要按 text specificity 判断

来源：
`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`

```python
def _evaluate_worldbook_binding(style_bible: StyleBibleResult, rules: StyleBibleEvalRules) -> dict[str, Any]:
    domain_keywords = _flatten_domain_keywords(rules)
    rag_items = [_clean_text(item) for item in style_bible.worldbook_binding.rag_worthy if _clean_text(item)]
    worldbook_items = [_clean_text(item) for item in style_bible.worldbook_binding.worldbook_worthy if _clean_text(item)]
    combined = rag_items + worldbook_items
    useful = [
        item
        for item in combined
        if _looks_specific_binding(item, rules=rules, domain_keywords=domain_keywords)
    ]
```

### 6.2 现场证据

live evaluator 报告中：

- `routing_hint_count = 4`
- `useful_routing_hint_count = 0`
- `useful_routing_hint_ratio = 0.0`

但同一份报告里：

- `rag_item_count = 3`
- `worldbook_item_count = 2`
- `useful_binding_ratio = 0.8`

来源：
`D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible_eval\style_eval_report.json`

这说明 evaluator 现在不能稳定区分：

- 路由句是否可执行
- worldbook 原子是否真的原子
- 结构字段是否与 `text` 同构

### 6.3 Debug 方案

#### P1-4. evaluator 改为直接消费 V2 结构

建议：

- schema validity 直接对 `StyleBibleResultV2` 做 validate
- downstream checks 直接读取 rule item 的结构字段

#### P1-5. `routing_hints` useful 判定改为结构 + 文本双通道

建议至少改成：

- `text` 可执行
  或
- `query_feature_matcher` 非空且 `route_target_action` 非空

并对二者是否同指一个 slot 做额外检查。

#### P1-6. 新增 densify 过程审计检查

建议 evaluator 新增独立 check，例如：

- `densify_control_plane`

直接读取 `_section_densify/*/section_densify_summary.json`，统计：

- `reasoning_sanitized_empty` 次数
- `slot_miss_after_filter` 次数
- `semantic_dedupe_drop` 次数
- `retrieval_nonempty_but_kept_zero` 次数

这样 `section_completeness` 才不会继续把控制面故障伪装成“只是内容偏薄”。

## 7. 发现五：现有测试没有覆盖这次 live 失效模式

### 7.1 对应原有代码

#### A. 现有 contract 测试覆盖了 compact reasoning，但没有覆盖 semantic-empty shell

来源：
`D:\card\novel_pipeline\tests\test_style_bible_local_reduce_contracts.py`

```python
def test_local_reducer_output_accepts_compact_reasoning_entry_shape(self) -> None:
    row = _base_rule_row()
    row["_reasoning_ref"] = "scene:0001_001"
    parsed = StyleBibleLocalReducerOutput.model_validate(
        {
            "reasoning": {
                "reasoning_version": "v2.0",
                "style_id": "style.demo",
                "scope": "novel",
                "entries": [
                    {
                        "_reasoning_ref": "reasoning_03",
                        "text": "收益先被账单截流，再决定角色动作。",
                        "evidence_refs": ["scene:0001_001"],
                    }
                ],
            },
            ...
        }
    )
```

这个测试走的是“compact but semantic-nonempty”路径，不会触发 live 里的空壳 reasoning 问题。

#### B. hierarchical reducer 的 fixture 也基本是 happy path

来源：
`D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py`

```python
def _structured_reducer_response(
    *,
    reasoning_entries: list[dict[str, object]],
    rule_rows: list[dict[str, object]],
    elapsed_seconds: float,
) -> SimpleNamespace:
    parsed = StyleBibleLocalReducerOutput(
        reasoning=StyleBibleReasoningBundle(
            reasoning_version="v2.0",
            style_id="style.demo",
            scope="novel",
            entries=[StyleBibleReasoningEntry.model_validate(row) for row in reasoning_entries],
        ),
        final={
            "style_id": "style.demo",
            "scope": "novel",
            "rule_rows": rule_rows,
        },
    )
```

这里给 reducer 的 reasoning fixture 默认已经是完整语义内容，不会复现 live 中“row 有、reasoning 空”的情况。

### 7.2 Debug 方案

必须新增以下测试：

#### P0 regression tests

- `test_densify_partial_with_semantic_empty_reasoning_shells_is_not_reported_as_generic_filtered_empty`
- `test_sanitize_reasoning_bundle_accepts_downstream_constraint_as_last_resort_if_policy_keeps_it`
- `test_run_section_densify_pass_records_reasoning_sanitized_empty_status`

#### P1 contract tests

- `test_rag_worthy_surface_spec_no_longer_requires_route_fields`
- `test_worldbook_worthy_surface_spec_no_longer_requires_route_fields`
- `test_routing_hint_useful_accepts_structured_fields_even_if_text_is_not_template_perfect`

#### Replay fixture

建议将本次 live 的一个最小化 partial 固化为 fixture，至少保留：

- 一个 semantic-empty reasoning shell
- 一个引用该 reasoning 的 rule row
- 一个 `filtered_empty` summary 预期

## 8. 建议实施顺序

### PR-1：先修 densify P0 合同和诊断

目标：

- 不再让 semantic-empty reasoning shell 悄悄穿过模型层后又在 reducer 被整批吞掉
- 让 summary 能明确说出失败的第一现场

建议修改文件：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`
- `D:\card\novel_pipeline\tests\test_style_bible_local_reduce_contracts.py`
- `D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py`

### PR-2：拆 routing/worldbook 合同

目标：

- `routing_hints` 保留路由句合同
- `rag_worthy/worldbook_worthy` 回到原子设定合同

建议修改文件：

- `D:\card\novel_pipeline\prompts\style_bible_section_densify.md`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_surface_specs.py`
- `D:\card\novel_pipeline\config\style_bible_section_targets.toml`
- `D:\card\novel_pipeline\tests\test_style_bible_v2_schema_contracts.py`

### PR-3：对齐 evaluator 与 downstream 消费侧

目标：

- evaluator 直接消费 V2 结构
- hybrid retrieval / ragas eval 对可选 route fields 兼容

建议修改文件：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\hybrid_retriever.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_ragas_eval.py`

### PR-4：最后再收枚举和阈值边缘问题

目标：

- 清理 `distance=intimate` 这类边缘漂移
- 在 densify 主故障修完后再看 slot threshold 是否还需要调

建议修改文件：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_surface_specs.py`
- `D:\card\novel_pipeline\config\style_bible_section_targets.toml`

## 9. 最小落地清单

### 必做

1. 给 densify 加“semantic-empty reasoning shell”显式诊断
2. 统一 reasoning 可用性判定
3. 补 live regression tests

### 次优先

1. 拆分 routing/worldbook 合同
2. evaluator 改为结构感知
3. hybrid retrieval / ragas eval 做兼容

### 暂缓

1. 直接扩大样本规模
2. 先调 slot 阈值
3. 继续加 embedding 或更多 bucket

## 10. 最终判断

当前最值得优先投入的不是继续扩样本，而是：

1. 修 densify reasoning 合同
2. 修 densify 失败原因诊断
3. 再拆 routing/worldbook 合同

如果这三步不先做，后面继续加 prompt、加 embedding、加 path target，只会继续出现：

> partial 看起来有输出，但最终进不了 final

这份文档固定的是 2026-04-20 当时仓库和 live 产物状态，可作为后续修复 PR 的基线说明。
