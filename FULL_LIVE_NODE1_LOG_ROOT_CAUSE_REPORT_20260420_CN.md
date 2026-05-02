# Full Live 实时日志问题排查报告（2026-04-20）

## 1. 排查范围

- 运行根目录：`D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01`
- Node1 目录：`D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270`
- 粗粒度日志：
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\logs\01_extract_style.log`
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\logs\02_story_node_pipeline.log`
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\logs\03_evaluate_style_bible.log`
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\logs\04_evaluate_style_bible_ragas.log`
- 细粒度运行证据：
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible\_section_densify\*\pass_01\section_densify_partial.json`
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible\_section_densify\*\pass_01\section_densify_summary.json`
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible\semantic_dedupe_drop_pairs_aggregate.json`
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible\_section_densify\embedding_request_metrics.jsonl`
- 评估结果：
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible_eval\style_eval_report.json`
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01\semantic_versions\main_01_kunxu_l1_ch0001_0270\style_bible_ragas\ragas_report.json`

## 2. 结论摘要

这次 full live 的主问题，不是数据量不够，也不是 embedding 没生效，更不是 semantic dedupe 误删，而是 **densify 的输出合同在“模型输出层”和“reducer 清洗层”之间断裂了**。

最关键的根因链条是：

1. `section_densify_partial.json` 里已经生成了新的 `final.rule_rows`。
2. 但同一个 partial 里的 `reasoning.entries` 大量是“有 `reasoning_id` 和 `evidence_refs`，但 `claim / observed_commonality / mechanism_inference` 全空”的壳。
3. `StyleBibleLocalReducerOutput` 的模型校验只要求 `_reasoning_ref` 可对齐，不要求 reasoning 内容非空，所以这类空壳能合法通过。
4. 进入 reducer 后，`_sanitize_reasoning_bundle()` 会把这类空 reasoning 整体过滤掉。
5. 随后 `_sanitize_rule_item()` 找不到对应的 `reasoning_ref`，导致 `candidate_rows` 变成空数组。
6. `_filter_section_densify_candidates()` 一进门就因为 `candidate_rows=[]` 直接返回空，最终所有 densify 路径都落成 `filtered_empty`。

所以，当前 `section_completeness` 的失败，本质上是 **densify 后处理链把候选入口清空了**，而不是单纯的 slot 阈值偏严。

## 3. 发现一：Densify 不是“没生成”，而是“生成后在 sanitize 链路里被清空”

### 3.1 日志证据

以 `negative_rules` 为代表：

- `section_densify_partial.json` 中已经有 3 条 `final.rule_rows`
- 但对应 `reasoning.entries` 是空壳，形态类似：

```json
{
  "reasoning_id": "exam_screening__r_neg_no_outrage_01",
  "claim": "",
  "observed_commonality": "",
  "mechanism_inference": "",
  "downstream_constraint": "",
  "evidence_refs": ["0165_0166", "0047_0048", "0223_0224"]
}
```

- 同一路径的 `section_densify_summary.json` 最终是：

```json
{
  "status": "filtered_empty",
  "kept_rule_count": 0,
  "candidate_filter": {
    "candidates": [],
    "kept_rule_ids": [],
    "semantic_dedupe_drops": []
  }
}
```

同样的模式在以下 6 条 densify 路径都复现了：

- `negative_rules`
- `aesthetics_system.core_axes`
- `aesthetics_system.pressure_axes`
- `narrative_system.pacing_rules`
- `expression_system.characterization_rules`
- `expression_system.description_rules`

这说明问题不是某一个 slot 或某一个桶偶发失效，而是 densify 输出合同的系统性失配。

### 3.2 对应原代码

`D:\card\novel_pipeline\src\novel_pipeline_stable\models.py:665-690`

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

`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py:942-957`

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
    ...
    for entry in reasoning.entries:
        claim = clean_text(entry.claim) or clean_text(entry.mechanism_inference) or clean_text(entry.observed_commonality)
        refs = [ref for ref in _unique_strings(entry.evidence_refs) if ref in memo_ref_pool]
        if not claim or not refs:
            continue
```

`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py:1104-1134`

```python
def _sanitize_rule_item(...):
    ...
    reasoning_ref = clean_text(item.reasoning_ref)
    ...
    if reasoning_ref not in reasoning_by_id:
        reasoning_ref = reasoning_by_text_key.get(_normalize_text_key(text), "")
    if reasoning_ref not in reasoning_by_id:
        reasoning_ref = _infer_reasoning_ref_from_evidence_refs(...)
    if reasoning_ref not in reasoning_by_id:
        return None
```

`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py:2029-2055`

```python
def _sanitize_rule_rows_for_path(...):
    reasoning_by_id, reasoning_by_text_key = _reasoning_lookup(reasoning_bundle)
    sanitized_rows: list[StyleBibleRuleItem] = []
    ...
    for item_index, row in enumerate(partial_output.final.rule_rows, start=1):
        sanitized = _sanitize_local_rule_row(...)
        if sanitized is None:
            continue
        path, rule = sanitized
        if clean_text(path) != normalized_target_path:
            continue
        sanitized_rows.append(rule)
    return sanitized_rows
```

`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py:3750-3751`

```python
if not candidate_rows or max_keep <= 0:
    return [], {"candidates": [], "kept_rule_ids": [], "semantic_dedupe_drops": []}
```

### 3.3 根因分析

这里有一个非常明确的“前宽后严”的合同断裂：

- 模型输出层允许 reasoning 条目只填 `reasoning_id + evidence_refs`
- `StyleBibleLocalReducerOutput` 只校验 `_reasoning_ref` 能否对齐，不校验 reasoning 的语义内容是否非空
- reducer 清洗层却把 reasoning 的 `claim / mechanism / observed` 视为硬条件

结果就是：

- partial 看起来“有东西”
- 但一进 sanitize，reasoning 先被清空
- rule 再因为失去 `reasoning_ref` 锚点被清空
- 最后 candidate filter 连入场机会都没有

这就是本轮 `densify partial 有输出，但 summary 里 candidate_filter 为空` 的直接根因。

### 3.4 为什么这比 slot 阈值更像主根因

`section_densify_summary.json` 里确实还能看到大量 `best_slot_score < slot_match_threshold` 的记录，例如：

- `negative_rules` 最大 slot 分数约 `0.6594`
- `aesthetics_system.core_axes` 最大 slot 分数约 `0.6505`
- `narrative_system.pacing_rules` 最大 slot 分数约 `0.6237`

但这只是第二层问题。

因为当前日志显示的不是“候选进入 filter 后因 slot 不达标被 drop”，而是更早的：

- `candidate_filter.candidates = []`

这只有在 `candidate_rows` 本身已经为空时才会发生。也就是说，**slot 阈值偏严是真问题，但不是这次 densify 全灭的第一现场。**

## 4. 发现二：`section_completeness` 的失败主要来自 densify 后处理链，而不是原始数据量不足

### 4.1 日志证据

`style_eval_report.json` 中：

- `section_completeness = fail`
- 多个核心 list path 仍只有 `1` 条
- 但同时：
  - `chapter_count = 270`
  - `scene_count = 1132`
  - `style_window_count = 135`
  - `reasoning_entry_count = 250`

`02_story_node_pipeline.log` 中的 canon 产量也不低：

- `entities=2611`
- `facts=18123`
- `events=6196`

`section_densify_summary.json` 中每条 densify path 的 `retrieval.candidate_count` 也都是 `250`。

### 4.2 根因分析

这说明本轮不是“无米之炊”。

当前更像是：

- 上游已经给了足够多的证据
- 检索层也成功把 250 条 reasoning 候选召回到了 densify
- densify 本身也确实写出了新 `rule_rows`
- 但后处理链把这些候选全部吞掉了

所以，现在去盲目扩大测试规模，并不能直接解决 `section_completeness` 的主故障。优先级应该先放在 **densify 合同修复**，而不是继续扩大样本。

## 5. 发现三：`routing_hints` 已达到数量下限，但 evaluator 只看 `text`，没有真正利用结构字段

### 5.1 日志证据

`style_eval_report.json`：

- `routing_hint_count = 4`
- `useful_routing_hint_count = 0`
- `useful_routing_hint_ratio = 0.0`

但最终产物 `style_bible_final.json` 里的 `routing_hints` 实际上并不是完全没结构。例如：

```json
{
  "text": "要让机构出手，先给它一个可计绩或可盈利的抓手。",
  "query_feature_matcher": "当查询提到要让学校、部门、神明或监考方介入一件事，并追问怎么让对方愿意出手或追责时",
  "route_target_action": "路由到以考核口径、收支底线和创收目标为核心的 KPI 型机构响应规则..."
}
```

也就是说，字段层面已经有一定可用信息，但文本层面的主句仍然偏“主题总结化”。

### 5.2 对应原代码

`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py:429-435`

```python
def _is_routing_hint_useful(text: str, rules: StyleBibleEvalRules) -> bool:
    normalized = _clean_text(text)
    if len(normalized) < int(rules.thresholds.get("min_item_specific_length", 6)):
        return False
    has_trigger = any(cue in normalized for cue in rules.routing_trigger_cues)
    has_route = any(cue in normalized for cue in rules.routing_route_cues)
    return has_trigger and has_route
```

`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py:1003-1005`

```python
def _evaluate_routing_hints(style_bible: StyleBibleResult, rules: StyleBibleEvalRules) -> dict[str, Any]:
    hints = [_clean_text(item) for item in style_bible.worldbook_binding.routing_hints if _clean_text(item)]
    useful = [hint for hint in hints if _is_routing_hint_useful(hint, rules)]
```

### 5.3 根因分析

这里的结构性问题有两层：

1. 生成侧虽然已经填了 `query_feature_matcher` 和 `route_target_action`，但 `text` 还不够稳定地落成“当……时，路由到……”句式。
2. 评估侧完全按 `text` 判 useful，不看结构字段，所以即便字段齐了，只要 `text` 不是标准句式，也会被判成 `0 useful`。

因此，当前 `routing_hints` 的失败，不是简单的“没有路由信息”，而是 **生成合同和评估合同没有对齐**。

## 6. 发现四：`rag_worthy / worldbook_worthy` 的 surface spec 仍在把原子设定往“路由句”上推

### 6.1 日志证据

最终产物中的一些 `rag_worthy` 条目，文本形态已经明显偏向查询路由句，而不是原子设定。例如：

```json
{
  "text": "当问题询问筑基考试为什么追加报名测试……检索相关考试筛选与预算规则事实，并返回……",
  "query_feature_matcher": "...",
  "route_target_action": "..."
}
```

这类写法更像 `routing_hints`，不像“可独立入库的世界观原子”。

### 6.2 对应原代码

`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_surface_specs.py:245-271`

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
```

### 6.3 根因分析

如果 `rag_worthy` / `worldbook_worthy` 的 schema 必须带：

- `query_feature_matcher`
- `route_target_action`

那模型天然就会把它们写成“查询入口 + 返回动作”的路由句，而不是“制度 / 规则 / 门槛 / 机制”的世界观原子。

这会直接带来两个后果：

1. `worldbook_binding` 更容易偏广义、偏泛化，难以稳定沉淀成可入库条目。
2. `routing_hints` 和 `rag_worthy` 的边界被抹平，造成两个路径相互污染。

这不是单纯 prompt 能完全解决的问题，已经属于 surface contract 的架构层混线。

## 7. 发现五：scalar 合同大体补绿，但 `distance` 仍保留 enum 漂移口子

### 7.1 日志证据

最终 `style_bible_final.json` 中：

- `perspective = close_third_person`
- `distance = intimate`
- `temporality = linear_forward`
- `narrator_voice = deadpan_procedural`
- `inner_monologue_mode = sparse_inline`

其中 `distance = intimate` 虽然是合法枚举，但和当前希望收敛的 `close` 仍有漂移。

### 7.2 对应原代码

`D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_surface_specs.py:348-358`

```python
SurfacePath.NARRATIVE_DISTANCE.value: ScalarEnumSpec(
    path=SurfacePath.NARRATIVE_DISTANCE,
    allowed_values=("intimate", "close", "medium", "far", "mixed"),
    default_value="close",
    value_aliases=(
        ("very_close", "intimate"),
        ("intimate close", "intimate"),
        ("贴身", "intimate"),
        ("贴身近距", "intimate"),
        ("near", "close"),
        ("near close", "close"),
```

### 7.3 根因分析

这不是本轮 P0 主故障，但它说明：

- scalar repair 已经把“缺失值”问题修掉了
- 但 canonical enum 还没有完全收口

如果评估或下游只接受 `close`，那 `intimate` 这个合法值会继续制造边缘不稳定。

## 8. 已排除的误因

### 8.1 不是 Embedding 没工作

`embedding_request_metrics.jsonl` 已经证明：

- `section_densify__negative_rules__01__reasoning_entries`
  - `input_count = 250`
  - 按 16 条一批发起请求
  - `total_elapsed_seconds = 59.899`
- 后续路径大量出现：
  - `cache_hit_count = 250`
  - `total_elapsed_seconds ≈ 0.15`

这说明：

- 没有退化成 for 循环单条请求
- 本地缓存和批量请求都在工作

### 8.2 不是 Semantic Dedupe 在误删

`semantic_dedupe_drop_pairs_aggregate.json`：

- `pair_file_count = 6`
- `drop_pair_count = 0`

所以本轮 densify 清空，不是 `_semantic_dedupe_candidates` 造成的。

### 8.3 不是原始数据太少

本轮 canon 与 style 侧数据量已经足够大：

- scene `1132`
- style window `135`
- facts `18123`
- entities `2611`
- densify 检索候选 `250`

当前更像是控制面/清洗链路问题，而不是原料不足。

## 9. 修复优先级建议

### P0：先修 densify 合同断裂

1. 在 `StyleBibleReasoningEntry` 或 `StyleBibleLocalReducerOutput` 的 validator 中，加硬约束：
   - `claim / observed_commonality / mechanism_inference` 至少一项非空
2. 在 `_run_section_densify_pass()` 中加显式诊断：
   - 如果 `partial.final.rule_rows` 非空，但 `candidate_rows` 为空，单独打 `reasoning_sanitized_empty` 状态，不要混成普通 `filtered_empty`
3. 在 reducer 中保留“为什么 row 被清空”的 trace，至少区分：
   - reasoning 缺失
   - path 不匹配
   - slot miss
   - semantic dedupe

### P1：修 `routing_hints` 的生成合同和评估合同

1. 生成侧继续强制 `text` 与 `query_feature_matcher + route_target_action` 同构
2. evaluator 不要只看 `text`，要把结构字段一起纳入 useful 判定

### P1：拆开 `rag_worthy/worldbook_worthy` 与 `routing_hints` 的 surface contract

1. `rag_worthy/worldbook_worthy` 不应再强依赖 `query_feature_matcher + route_target_action`
2. 这两条路径应该回到“原子事实 / 机制 / 门槛 / 流程”的入库合同
3. `routing_hints` 才保留查询触发与路由动作

### P2：收紧 scalar enum

1. 如果团队希望 `distance` 只收敛到 `close`
2. 就应从枚举层收掉 `intimate`，或者在 alias 层统一映射到 `close`

## 10. 最终判断

本轮最优先修的，不是继续折腾 extract/canon，也不是盲目扩大数据量，而是：

1. **修 densify 的 reasoning 输出合同**
2. **修 reducer 对 densify 失败原因的显式诊断**
3. **修 routing/worldbook 的 surface contract 分层**

如果这三个点不先修，后续再加 prompt、再加 embedding、再加桶数，都会继续出现“partial 有输出，但最后进不了 final”的假忙碌现象。
