# Full Live Node1 运行报告（Control Plane Fix01，2026-04-20）

## 1. 运行范围

- Story node：`main_01_kunxu_l1_ch0001_0270`
- 运行根目录：`D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260420_control_plane_fix01`
- 实际链路：
  - `extract-style --resume`
  - `build-canon`
  - `build-style-bible`
  - `evaluate-style-bible`
  - `evaluate-style-bible-ragas`

关键日志：

- `data/live_runs/full_live_from_extract_style_20260420_control_plane_fix01/logs/01_extract_style.log`
- `data/live_runs/full_live_from_extract_style_20260420_control_plane_fix01/logs/02_story_node_pipeline.log`
- `data/live_runs/full_live_from_extract_style_20260420_control_plane_fix01/logs/03_evaluate_style_bible.log`
- `data/live_runs/full_live_from_extract_style_20260420_control_plane_fix01/logs/04_evaluate_style_bible_ragas.log`

关键产物目录：

- `data/live_runs/full_live_from_extract_style_20260420_control_plane_fix01/semantic_versions/main_01_kunxu_l1_ch0001_0270/style_bible`
- `data/live_runs/full_live_from_extract_style_20260420_control_plane_fix01/semantic_versions/main_01_kunxu_l1_ch0001_0270/style_bible_eval`
- `data/live_runs/full_live_from_extract_style_20260420_control_plane_fix01/semantic_versions/main_01_kunxu_l1_ch0001_0270/style_bible_ragas`

## 2. 总结结论

这次 full live 已经完整跑通，但质量门没有通过。

- `build-style-bible` 成功产出：
  - `style_bible_final.json`
  - `style_bible_reasoning.json`
  - `style_bible_reduce_trace.json`
  - `semantic_dedupe_drop_pairs_aggregate.json`
- `evaluate-style-bible` 结果：
  - `summary.status = fail`
  - `overall_score = 84.1 / 100`
  - `check_counts = pass 7 / warn 1 / fail 3`
- `evaluate-style-bible-ragas` 结果：
  - `total_items = 9`
  - `average_overall_score = 0.5403`
  - `average_grounding_ratio = 1.0`

本轮真正确认生效的点有两类：

1. control plane 已经把 densify 火力从 worldbook 扩到了核心 list path；
2. embedding 批量请求与缓存机制在真实 run 中工作正常，没有退化成逐条 API 调用。

本轮暴露出来的新主问题也很明确：

1. `section_completeness` 仍然失败，核心 list path 依旧只有 `1` 条；
2. `routing_hints` 现在数量达标到 `4` 条，但 evaluator 认为 `4/4` 都太泛，`useful_routing_hint_ratio = 0.0`；
3. densifier 的 `partial` 里已经生成了候选 `rule_rows`，但进入 summary 时 `candidate_filter.candidates = []`，导致所有 densify path 都以 `filtered_empty` 结束。

## 3. 运行耗时

- `build-style-bible` 启动：`2026-04-20 13:18:57`
- `style_bible_final.json` 落盘：`2026-04-20 17:58:07`
- `ragas` 日志落盘：`2026-04-20 17:58:42`
- 本轮全链路总耗时：约 `4 小时 39 分 49 秒`

这轮耗时明显长于之前的 node1 收尾 run，主要成本来自：

1. local reduce + repair 总轮次增加；
2. densify 现在真正打到了 6 条 control-plane 路径，每条都触发了真实结构化生成。

## 4. Phase01：采样与批处理

来自 `sampled_input_scope.json`：

| 类型 | 原始数量 | 过滤后数量 |
| --- | ---: | ---: |
| scene | 1132 | 24 |
| style_window | 135 | 24 |
| chapter | 270 | 66 |
| plot_node | 270 | 85 |
| entity | 2611 | 679 |

来自 `batch_plan.json.coverage_summary`：

- `total_item_count = 48`
- `batched_item_count = 48`
- `unbatched_item_count = 0`
- `batch_count = 23`
- `bucket_count_with_batches = 12`
- `batched_scene_ratio = 1.0`
- `batched_style_window_ratio = 1.0`

代表性 bucket 装箱规模：

| bucket | selected_item_count | batch_count |
| --- | ---: | ---: |
| `exam_screening` | 33 | 3 |
| `dark_humor` | 26 | 4 |
| `body_assetization` | 23 | 2 |
| `commercialized_conflict` | 22 | 2 |
| `institutional_pipeline` | 19 | 3 |
| `identity_shame` | 18 | 2 |
| `resource_pressure` | 13 | 1 |

结论：

- sampling cap 继续稳定地下传到了 router / batching；
- phase01 没有回退到之前的爆量状态。

## 5. Local Reduce / Repair 结果

本轮进入 local reduce 的 bucket 共 `12` 个：

- `asset_repricing`
- `body_assetization`
- `collective_production`
- `commercialized_conflict`
- `contract_sales`
- `dark_humor`
- `exam_screening`
- `family_survival`
- `gray_labor`
- `identity_shame`
- `institutional_pipeline`
- `resource_pressure`

结果概览：

| bucket | status | rule_count | repair_pass_count |
| --- | --- | ---: | ---: |
| `asset_repricing` | success | 11 | 2 |
| `body_assetization` | success | 7 | 2 |
| `collective_production` | success | 4 | 0 |
| `commercialized_conflict` | success | 9 | 2 |
| `contract_sales` | success | 4 | 0 |
| `dark_humor` | success | 7 | 0 |
| `exam_screening` | success | 10 | 2 |
| `family_survival` | success | 7 | 2 |
| `gray_labor` | success | 8 | 2 |
| `identity_shame` | sparse | 0 | 0 |
| `institutional_pipeline` | success | 17 | 2 |
| `resource_pressure` | success | 8 | 2 |

说明：

- 这次 repair 已经不是“偶尔触发”，而是大面积进入 `pass_02`；
- `identity_shame` 仍然是 sparse 空桶；
- 虽然 bucket 侧规则总数不低，但合并后落到最终主树上的核心 list path 仍然普遍只有 `1` 条。

## 6. 最终 Style Bible 厚度

最终 `style_bible_final.json` 中的关键计数：

| 路径 | 数量 |
| --- | ---: |
| `narrative_system.engine` | 1 |
| `narrative_system.pacing_rules` | 1 |
| `narrative_system.plot_node_logic` | 1 |
| `expression_system.description_rules` | 1 |
| `expression_system.dialogue_rules` | 1 |
| `expression_system.characterization_rules` | 1 |
| `expression_system.sensory_rules` | 1 |
| `aesthetics_system.core_axes` | 1 |
| `aesthetics_system.pressure_axes` | 1 |
| `aesthetics_system.humor_recipe` | 1 |
| `aesthetics_system.satire_targets` | 1 |
| `aesthetics_system.nonstandard_xianxia_rules` | 1 |
| `voice_contract.register_mix` | 1 |
| `voice_contract.negative_pitfalls` | 1 |
| `character_arc_rules` | 1 |
| `worldbook_binding.rag_worthy` | 3 |
| `worldbook_binding.worldbook_worthy` | 2 |
| `worldbook_binding.routing_hints` | 4 |
| `negative_rules` | 1 |
| `supporting_evidence` | 20 |
| `reasoning.entries` | 250 |

标量字段已经都有值：

- `narrative_system.perspective = close_third_person`
- `narrative_system.distance = intimate`
- `narrative_system.temporality = linear_forward`
- `voice_contract.narrator_voice = deadpan_procedural`
- `voice_contract.inner_monologue_mode = sparse_inline`

结论：

- scalar 合同已经不再是当前主问题；
- 主问题集中在 list thickness 与 routing/worldbook 的可执行性。

## 7. Densify / Embedding 的真实表现

### 7.1 本轮 densify 实际触发的路径

`section_densify` 本轮真实打到了 6 条路径：

| path | status | kept_rule_count | retrieved_reasoning_count | elapsed_seconds |
| --- | --- | ---: | ---: | ---: |
| `negative_rules` | `filtered_empty` | 0 | 12 | 395.704 |
| `aesthetics_system.core_axes` | `filtered_empty` | 0 | 12 | 375.827 |
| `aesthetics_system.pressure_axes` | `filtered_empty` | 0 | 12 | 436.032 |
| `narrative_system.pacing_rules` | `filtered_empty` | 0 | 10 | 564.864 |
| `expression_system.characterization_rules` | `filtered_empty` | 0 | 10 | 263.578 |
| `expression_system.description_rules` | `filtered_empty` | 0 | 10 | 312.521 |

这说明 control plane 已经真的把 densify 从 worldbook 路径扩到了主战场。

### 7.2 关键新发现：partial 有候选，但 summary 里全被清空

这是本次 full live 最重要的运行发现。

每个 densify path 的 `section_densify_partial.json` 里其实都已经写出了候选规则：

| path | partial.rule_rows | summary.candidate_filter.candidates | 最终状态 |
| --- | ---: | ---: | --- |
| `negative_rules` | 3 | 0 | `filtered_empty` |
| `aesthetics_system.core_axes` | 3 | 0 | `filtered_empty` |
| `aesthetics_system.pressure_axes` | 3 | 0 | `filtered_empty` |
| `narrative_system.pacing_rules` | 2 | 0 | `filtered_empty` |
| `expression_system.characterization_rules` | 2 | 0 | `filtered_empty` |
| `expression_system.description_rules` | 2 | 0 | `filtered_empty` |

这意味着：

1. 不是模型“完全写不出来”；
2. 候选是在 **partial -> candidate ingestion / sanitize / filter** 这一段被整体清空了；
3. 当前最应该优先排查的是 densify 后处理链路，而不是继续只改 prompt 文案。

### 7.3 Embedding 的角色与收益

本轮 embedding 只在 densify 中担任中间件，不参与 local reduce 主生成。

它做了三件事：

1. `missing_slots` 向量检索 query；
2. `reasoning.entries` Top-K 召回；
3. slot coverage / semantic dedupe 过滤。

本轮真实指标可以确认两件事：

- **批量请求是生效的**
  - `negative_rules__reasoning_entries` 一次打了 `250` 条输入
  - 共 `16` 个 batch 请求
  - 总耗时 `59.899s`
- **缓存是生效的**
  - 后续路径的 `reasoning_entries` embedding 全部 `250/250 cache hit`
  - 耗时压缩到 `0.15s ~ 0.77s`

这满足了“不要在 for 循环里逐条打 embedding API”的要求。

### 7.4 Semantic Dedupe 日志

聚合文件：

- `style_bible/semantic_dedupe_drop_pairs_aggregate.json`

结果：

- `pair_file_count = 6`
- `drop_pair_count = 0`

对应路径：

- `_section_densify\aesthetics_system_core_axes\pass_01\semantic_dedupe_drop_pairs.json`
- `_section_densify\aesthetics_system_pressure_axes\pass_01\semantic_dedupe_drop_pairs.json`
- `_section_densify\expression_system_characterization_rules\pass_01\semantic_dedupe_drop_pairs.json`
- `_section_densify\expression_system_description_rules\pass_01\semantic_dedupe_drop_pairs.json`
- `_section_densify\narrative_system_pacing_rules\pass_01\semantic_dedupe_drop_pairs.json`
- `_section_densify\negative_rules\pass_01\semantic_dedupe_drop_pairs.json`

结论：

- 日志导出机制是通的；
- 本轮没有任何规则对被 `_semantic_dedupe_candidates` 真正丢弃；
- 当前 densify 的主要问题发生在 dedupe 之前。

## 8. Offline Eval 结果

### 8.1 总分

`style_eval_report.json.summary`：

- `status = fail`
- `overall_score = 84.1`
- `quality_gate_passed = false`
- `check_counts = pass 7 / warn 1 / fail 3`

### 8.2 Fail 项

#### `section_completeness`

仍然 underfilled 的 12 个主路径：

- `narrative_system.engine` `1 / 3`
- `narrative_system.pacing_rules` `1 / 4`
- `narrative_system.plot_node_logic` `1 / 3`
- `expression_system.description_rules` `1 / 4`
- `expression_system.dialogue_rules` `1 / 4`
- `expression_system.characterization_rules` `1 / 4`
- `expression_system.sensory_rules` `1 / 4`
- `aesthetics_system.core_axes` `1 / 5`
- `aesthetics_system.pressure_axes` `1 / 5`
- `aesthetics_system.humor_recipe` `1 / 4`
- `aesthetics_system.satire_targets` `1 / 4`
- `aesthetics_system.nonstandard_xianxia_rules` `1 / 4`

结论：

- 我们已经把 densify 控制面打到这些路径附近了；
- 但由于 densify 产物在后处理阶段被清空，厚度没有真正回填到最终树。

#### `routing_hints`

- `routing_hint_count = 4`
- `useful_routing_hint_count = 0`
- `useful_routing_hint_ratio = 0.0`

代表性弱例子：

- `要让机构出手，先给它一个可计绩或可盈利的抓手。`
- `学校扶持资源按金额、时长、授权和器具功能拆成资产包条目。`

结论：

- 这次不是数量不足，而是“句式仍偏解释句，不是窄触发 matcher + 路由动作”。

#### `anti_pattern_resistance`

- `violation_ratio = 0.33`

主要违规来源：

- `VAGUE_ROUTING`：`4 / 4`
- `KEYWORD_STUFFING`：`6`
- `UNGROUNDED_WORLDBOOK`：`1`

### 8.3 Warn 项

#### `worldbook_binding`

- `rag_item_count = 3`
- `worldbook_item_count = 2`
- `useful_binding_ratio = 0.8`

说明：

- worldbook 质量不算最差；
- 但厚度仍不足，并且还有 1 条 evaluator 认为偏抽象。

## 9. Ragas 结果

`ragas_report.json.summary`：

- `total_items = 9`
- `item_type_counts = {rag_worthy: 3, routing_hints: 4, worldbook_worthy: 2}`
- `average_overall_score = 0.5403`
- `average_faithfulness_proxy = 0.2632`
- `average_relevance_proxy = 0.3401`
- `average_grounding_ratio = 1.0`
- `pass_count = 0`
- `warn_count = 7`
- `fail_count = 2`

最弱条目：

1. `institutional_pipeline__kpi_routed_authority_response_01`
   - `item_type = routing_hints`
   - `overall_score = 0.4195`
2. `asset_repricing__rag_worthy__songyang_compensation_package_01`
   - `item_type = rag_worthy`
   - `overall_score = 0.4719`

这和 offline eval 给出的结论一致：

- grounding 没问题；
- 真正短板是 faithfulness / relevance / executability。

## 10. 结论与下一步

### 已确认有效的改动

1. `section_targets + prompt payload + repair slicing + densify path selection` 已在真实 run 中生效。
2. densify 不再只打 worldbook，而是开始覆盖 `negative_rules / pacing / description / characterization / core_axes / pressure_axes`。
3. embedding batching + cache 在真实 run 里验证通过。
4. `_semantic_dedupe_candidates` drop pair 日志已完整导出。

### 当前最优先的真正卡点

P0 不是再继续改 extract/canon，而是先修 densify 后处理链。

原因：

1. `section_densify_partial.json` 已经有候选 `rule_rows`；
2. `section_densify_summary.json` 却显示 `candidate_filter.candidates = []`；
3. 最终所有 densify path 都是 `filtered_empty`；
4. 这直接导致 `section_completeness` 没有任何实质改善。

### 下一步建议顺序

1. 先 debug `style_bible_reducer.py` 的 densify 后处理链：
   - 为什么 `partial.rule_rows > 0`，但进入 summary 时 `candidate_filter.candidates = []`
   - 重点看 sanitize / candidate extraction / path-scoped row collection
2. 再收紧 `routing_hints` prompt 合同：
   - evaluator 已经明确判定 `4/4` 都太泛
   - 下一轮必须逼成窄 matcher + 明确 route target action
3. 然后重跑 node1，但不必从 extract-style 重来：
   - 直接复用当前 run 根目录附近的 facts/style/canon
   - 重新起一个新的 style_bible 输出根做增量验证即可

### 当前项目状态判断

项目已经不在“跑不通 / schema 崩 / 卡死”的阶段了。

当前已经进入一个更聚焦的阶段：

- control plane 已经通了；
- embedding 中间件已经通了；
- 真正剩下的是“densify 产物为什么没有被并入最终树”以及“routing/worldbook 为什么还不够可执行”。
