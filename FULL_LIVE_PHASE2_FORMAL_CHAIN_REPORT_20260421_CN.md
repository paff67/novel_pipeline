# Phase 2 Full Live 运行结果报告（2026-04-21）

参考 `FULL_LIVE_NODE1_COMPLETION_REPORT_20260420_CN.md` 的报告维度整理。本报告覆盖本次正式链从 `extract-style` 起跑后的完整执行结果：

`extract-style -> build-canon -> build-style-bible -> evaluate-style-bible -> judge-style-bible -> evaluate-style-bible-ragas`

## 1. 结论摘要

- 本次 Phase 2 full live 已按计划从 `extract-style` 起跑，并完整跑通正式链 6 个阶段。
- 命令执行层面全部成功退出，强制验收要求的核心产物均已生成落盘。
- `extract-style` 本次以 `resume=True` 执行，结果为 `420/420 skipped`，说明正式 style 产物已存在且可直接复用；`build-canon`、`build-style-bible`、`evaluate-style-bible`、`judge-style-bible`、`evaluate-style-bible-ragas` 均成功完成。
- `build-style-bible` 正式输出完整，包括 `style_bible_final.json`、`style_bible_reasoning.json`、`style_bible_export_flat.json`、`style_bible_reduce_trace.json`、`semantic_dedupe_drop_pairs_aggregate.json`、`_local_reduce`、`_section_densify` 等关键工件。
- 运行时语义链路仍保持 **shadow-only**：
  - `semantic_shadow_enabled = true`
  - `router_semantic_cutover_enabled = false`
  - `selective_cutover_target = "router"`
  - `final_decision_source` 仍分别为 `legacy_eval_with_semantic_sidecar`、`legacy_judge_with_semantic_sidecar`、`legacy_ragas_with_semantic_sidecar`
- 质量门结论如下：
  - `evaluate-style-bible`: `fail`，`84.57 / 100`
  - `judge-style-bible`: `warn`，`67.99 / 100`
  - `evaluate-style-bible-ragas`: `0 fail`，`average_overall_score = 0.7251`
- 因此，本次 full live 的结论是：
  - **命令链与产物链通过**
  - **Phase 2 语义侧车与兼容层运行稳定**
  - **正式质量门仍需继续调优，当前不应打开 router semantic cutover**

## 2. 本轮关键实现背景

本次 formal full live 验证的是 Phase 2 完整落地后的正式链稳定性，重点覆盖以下实现面：

- typed rule family 已落地，`surface_path -> rule_family / row_model / enum_source` 合同已接入运行时。
- prompt 组装已切换为 compact contract builder + mode-specific contract slices，不再整包注入 path schema。
- 中文语义锚点与统一词汇控制面已接入 router / retriever / evaluator sidecar。
- semantic sidecar、feature flags、lexical fallback、rollback 路径均已保留并生效。
- 本次 formal live 默认仍使用 shadow-only 语义观测，不让 semantic-first 直接接管正式决策。

## 3. 运行范围与配置

### 3.1 运行对象

- 运行范围不是单一 smoke node，而是正式全量语料范围。
- Style Bible build scope 为：
  - `chapters 0001-0840 / facts+style+canon joint distillation`
- build manifest 中 `node_id` 为空，说明本轮 build 不是单 node 限定构建。
- judge 层 `node_id = main_01_kunxu_l1_ch0001_0270`，来源是 gold set 的单目标 case 推断，不代表 build scope 被缩成单 node。

### 3.2 输入与输出

- facts 输入目录：
  - `D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable`
- 本次 style 运行输入目录：
  - `D:\card\novel_pipeline\data\experimental\chapters_full_0001_0841_ready_20260330`
- style 输出目录：
  - `D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable`
- canon 输出目录：
  - `D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable`
- style bible 输出目录：
  - `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable`
- eval 输出目录：
  - `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_eval`
- judge 输出目录：
  - `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_judge`
- ragas 输出目录：
  - `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_ragas`

### 3.3 模型与运行策略

- style bible 生成模型：`gpt-5.4`
- build manifest prompt：`style_bible_local_reduce.md`
- batch planner strategy：`dynamic_fair_round_robin_v2`
- token budget：
  - `token_budget = 16000`
  - `scene_token_quota = 10000`
  - `style_window_token_quota = 6000`

### 3.4 运行时间线

以下时间统一换算为北京时间 `+08:00`，并以各阶段自己的状态文件或报告时间戳为准：

| 阶段 | 开始时间 | 完成时间 | 备注 |
| --- | --- | --- | --- |
| `extract-style` | `2026-04-21 03:13:31` | `2026-04-21 03:13:38` | `resume=True`，`420/420 skipped` |
| `build-canon` | `2026-04-21 03:13:47` | `2026-04-21 03:13:54` | 正式 canon 重建成功 |
| `build-style-bible` | `2026-04-21 16:35:42` | `2026-04-21 20:22:44` | 正式 style bible 构建完成 |
| `evaluate-style-bible` | - | `2026-04-21 20:26:10` | 报告生成成功 |
| `judge-style-bible` | - | `2026-04-21 20:26:28` | 报告生成成功 |
| `evaluate-style-bible-ragas` | - | `2026-04-21 20:26:09` | 报告生成成功 |

阶段耗时：

- `extract-style`：约 `6.58s`
- `build-canon`：约 `6.73s`
- `build-style-bible`：约 `3h 47m 02s`

说明：

- `extract-style` 和 `build-canon` 运行时间很短，是因为本次 formal live 复用了既有正式产物基础，并通过正式命令验证可恢复性与兼容性。
- `build-style-bible` 是本次 full live 的主要耗时阶段。

## 4. Phase01 / Sampling / Batching

### 4.1 全量范围与过滤后范围

来自 `sampled_input_scope.json`：

| 类型 | original | filtered |
| --- | ---: | ---: |
| scene | 3983 | 3980 |
| style_window | 420 | 420 |
| chapter | 841 | 840 |
| plot_node | 841 | 841 |
| entity | 8099 | 8099 |

### 4.2 Batching 覆盖情况

来自 `batch_plan.json` 与 `sampling_report.json`：

- `total_item_count = 4400`
- `batched_item_count = 4067`
- `unbatched_item_count = 333`
- `batch_count = 341`
- `bucket_count_with_batches = 13`
- `batched_scene_ratio = 0.9163`
- `batched_style_window_ratio = 1.0`

### 4.3 Stage Coverage

| stage | scene_ratio | style_window_ratio | chapter_ratio | axis_coverage_ratio | bucket_coverage_ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| total | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| sampled | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| routed | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| batched | 0.9163 | 1.0 | 1.0 | 1.0 | 1.0 |
| memoed | 0.5349 | 0.9333 | 0.9917 | 1.0 | 1.0 |
| reduced | 0.0847 | 0.2452 | 0.4655 | 1.0 | 1.0 |

说明：

- 这是一次正式全量蒸馏，不是小样本 caps smoke，因此 `selected_item_count` 与 `batch_count` 明显高于此前 node1 smoke 报告。
- batched / memoed / reduced 的 scene ratio 逐步下降，符合“全量证据 -> per-bucket memo -> hierarchical reduce” 的蒸馏收敛过程，不构成异常。

### 4.4 典型 bucket 选中体量

按 `selected_item_count` 排序的前 10 个 bucket：

| bucket | selected_item_count |
| --- | ---: |
| `exam_screening` | 3238 |
| `family_survival` | 2271 |
| `body_assetization` | 2005 |
| `dark_humor` | 1803 |
| `commercialized_conflict` | 1770 |
| `resource_pressure` | 1735 |
| `gray_labor` | 1630 |
| `institutional_pipeline` | 833 |
| `identity_shame` | 767 |
| `contract_sales` | 488 |

## 5. Local Reduce 与 Repair 情况

### 5.1 总体结果

- local reduce bucket 总数：`13`
- sparse bucket 数：`0`
- failed bucket 数：`0`
- repair bucket 数：`10`
- repair 总 pass 数：`16`
- `failures.json = []`

critical bucket 标记为：

- `dark_humor`
- `institutional_pipeline`
- `resource_pressure`

### 5.2 触发 repair 的 bucket

| bucket | repair_pass_count | final_rule_count |
| --- | ---: | ---: |
| `asset_repricing` | 1 | 9 |
| `body_assetization` | 2 | 9 |
| `commercialized_conflict` | 2 | 14 |
| `exam_screening` | 1 | 13 |
| `family_survival` | 2 | 9 |
| `gray_labor` | 2 | 10 |
| `identity_shame` | 1 | 7 |
| `institutional_pipeline` | 2 | 13 |
| `orphanage` | 1 | 7 |
| `resource_pressure` | 2 | 12 |

### 5.3 各 bucket 最终 rule_count

| bucket | rule_count |
| --- | ---: |
| `asset_repricing` | 9 |
| `body_assetization` | 9 |
| `collective_production` | 9 |
| `commercialized_conflict` | 14 |
| `contract_sales` | 10 |
| `dark_humor` | 8 |
| `exam_screening` | 13 |
| `family_survival` | 9 |
| `gray_labor` | 10 |
| `identity_shame` | 7 |
| `institutional_pipeline` | 13 |
| `orphanage` | 7 |
| `resource_pressure` | 12 |

### 5.4 Reducer 语义观测

来自 `style_bible_reduce_trace.json`：

- `semantic_score = 0.0492`
- `lexical_prior_score = 0.7927`
- `evidence_overlap_score = 1.0`
- `final_decision_source = legacy_reduce_with_semantic_sidecar`

解释：

- reducer 阶段已输出语义侧车观测值，但最终组装决策仍由 legacy 链承担主导。
- evidence overlap 在 reduce 层是稳定的，说明主问题不是“缺 grounding ref”，而更像是“最终规则厚度和可执行性不足”。

## 6. Section Densify / Embedding / Semantic Dedupe

### 6.1 总体结果

- densify 共覆盖 `6` 条 underfilled 主路径，每条跑了 `2` 个 pass，总计 `12` 次尝试。
- 其中：
  - `8` 次请求成功
  - `4` 次请求失败
- successful pass 的 `retrieved_reasoning_count` 主要集中在 `10` 或 `12`。

### 6.2 各 densify 路径结果

| 路径 | pass_01 | pass_02 | 说明 |
| --- | --- | --- | --- |
| `negative_rules` | success, kept `2` | success, kept `2` | 请求成功，但最终主产物厚度未显著抬升 |
| `aesthetics_system.core_axes` | request_failed | request_failed | 两个 pass 都失败 |
| `aesthetics_system.pressure_axes` | success, kept `3` | success, kept `3` | 请求成功，但最终 eval 仍判 underfilled |
| `narrative_system.pacing_rules` | success, kept `1` | success, kept `1` | 请求成功 |
| `expression_system.characterization_rules` | success, kept `2` | request_failed | 部分成功 |
| `expression_system.description_rules` | request_failed | success, kept `2` | 部分成功 |

### 6.3 Semantic Dedupe

来自 `semantic_dedupe_drop_pairs_aggregate.json`：

- `pair_file_count = 8`
- `drop_pair_count = 0`

说明：

- 本次 densify 的主要问题不是 semantic dedupe 误删。
- 结合最终 `style_bible_final.json` 与 eval 结果看，densify 虽然有请求层成功与候选保留，但没有把主 list 厚度真正推到配置要求的最小条数。这是基于最终产物与 eval 结果做出的运行后判断。

## 7. 最终 Style Bible 产物概览

### 7.1 关键路径数量

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
| `worldbook_binding.rag_worthy` | 5 |
| `worldbook_binding.worldbook_worthy` | 5 |
| `worldbook_binding.routing_hints` | 8 |
| `negative_rules` | 1 |
| `supporting_evidence` | 20 |

### 7.2 标量字段状态

以下标量字段均存在：

- `narrative_system.perspective`
- `narrative_system.distance`
- `narrative_system.temporality`
- `voice_contract.narrator_voice`
- `voice_contract.inner_monologue_mode`

### 7.3 证据与推理规模

来自 eval key metrics：

- `chapter_count = 841`
- `scene_count = 3983`
- `style_window_count = 420`
- `supporting_evidence_count = 20`
- `reasoning_entry_count = 181`

## 8. Offline Eval 结果

### 8.1 `evaluate-style-bible`

- `status = fail`
- `overall_score = 84.57 / 100`
- `quality_gate_passed = false`
- `check_counts = pass: 8 / warn: 0 / fail: 3`

semantic observability：

- `semantic_score = 0.051`
- `lexical_prior_score = 0.4079`
- `evidence_overlap_score = 0.2905`
- `final_decision_source = legacy_eval_with_semantic_sidecar`

### 8.2 三个 fail 项

#### `section_completeness`

- `score = 8.68 / 15`
- 问题不是 scalar 缺失，而是核心 list 普遍 underfilled。
- underfilled 字段包括：
  - `narrative_system.engine`，`1 / 3`
  - `narrative_system.pacing_rules`，`1 / 4`
  - `narrative_system.plot_node_logic`，`1 / 3`
  - `expression_system.description_rules`，`1 / 4`
  - `expression_system.dialogue_rules`，`1 / 4`
  - `expression_system.characterization_rules`，`1 / 4`
  - `expression_system.sensory_rules`，`1 / 4`
  - `aesthetics_system.core_axes`，`1 / 5`
  - `aesthetics_system.pressure_axes`，`1 / 5`
  - `aesthetics_system.humor_recipe`，`1 / 4`
  - `aesthetics_system.satire_targets`，`1 / 4`
  - `aesthetics_system.nonstandard_xianxia_rules`，`1 / 4`

#### `actionability`

- `score = 0.0 / 8`
- `candidate_rule_count = 19`
- `actionable_rule_count = 0`
- `actionable_ratio = 0.0`

这说明当前主规则虽然已具备 schema 和 grounding 外壳，但多数文本仍更接近“解释句”而不是“可执行规则句”。

#### `anti_pattern_resistance`

- `score = 1.16 / 2`
- `violation_ratio = 0.4209`
- 主要命中的 pattern：
  - `VAGUE_ROUTING`：`75 / 83`
  - `KEYWORD_STUFFING`：`9 / 449`
  - `UNGROUNDED_WORLDBOOK`：`76 / 100`

### 8.3 已经稳定通过的项

- `required_axis_coverage = pass`
  - `6 / 6` 必选 thematic groups 全覆盖
- `supporting_evidence = pass`
  - `supporting_evidence_count = 20`
  - `valid_source_ref_ratio = 1.0`
- `routing_hints = pass`
  - `routing_hint_count = 8`
  - `useful_routing_hint_count = 8`
  - `useful_routing_hint_ratio = 1.0`
- `worldbook_binding = pass`
  - `rag_item_count = 5`
  - `worldbook_item_count = 5`
  - `useful_binding_ratio = 0.9`
- `generic_language = pass`
  - `generic_item_count = 0`
  - `generic_item_ratio = 0.0`

## 9. Judge 结果

### 9.1 总体指标

- `status = warn`
- `overall_score = 67.99 / 100`
- `quality_gate_passed = false`
- `case_count = 6`
- `applicable_case_count = 5`
- `warn_case_count = 5`
- `fail_case_count = 0`

semantic observability：

- `semantic_score = 0.0515`
- `lexical_prior_score = 0.805`
- `evidence_overlap_score = 0.8546`
- `final_decision_source = legacy_judge_with_semantic_sidecar`

### 9.2 各 case 结果

| case_id | status | score | bucket_targets |
| --- | --- | ---: | --- |
| `main_01_dark_humor_goal_driven_exposition_006` | warn | 61.50 | `dark_humor` |
| `main_01_dark_humor_recipe_005` | warn | 73.40 | `dark_humor` |
| `main_01_institutional_pipeline_procedural_cruelty_003` | not_applicable | 0 | `institutional_pipeline` |
| `main_01_institutional_pipeline_qualification_chain_004` | warn | 69.25 | `institutional_pipeline` |
| `main_01_resource_pressure_cost_settlement_001` | warn | 63.44 | `resource_pressure` |
| `main_01_resource_pressure_threshold_routing_002` | warn | 72.36 | `resource_pressure` |

### 9.3 维度分数

| dimension | score |
| --- | ---: |
| `axis_coverage` | 8.000 |
| `mechanism_specificity` | 14.802 |
| `evidence_faithfulness` | 8.526 |
| `trace_auditability` | 8.758 |
| `routing_executability` | 6.566 |
| `worldbook_exportability` | 5.470 |
| `rag_atomicity` | 3.274 |
| `prompt_preset_usability` | 4.914 |
| `anti_genericity` | 4.000 |
| `anti_pattern_resistance` | 3.680 |

解读：

- judge 侧没有出现 outright fail case，但 `5/5 applicable` 全部停留在 warn，说明“结构上可用，但距离 gold-set 级稳定可执行还有差距”。
- 相对薄弱维度集中在：
  - `worldbook_exportability`
  - `rag_atomicity`
  - `prompt_preset_usability`
  - `anti_pattern_resistance`

## 10. Ragas-ready 结果

### 10.1 总体指标

- `total_items = 18`
- `rag_worthy = 5`
- `routing_hints = 8`
- `worldbook_worthy = 5`
- `average_overall_score = 0.7251`
- `average_faithfulness_proxy = 0.3128`
- `average_relevance_proxy = 1.0`
- `average_grounding_ratio = 1.0`
- `pass_count = 9`
- `warn_count = 9`
- `fail_count = 0`

semantic observability：

- `semantic_score = 0.072`
- `lexical_prior_score = 1.0`
- `evidence_overlap_score = 1.0`
- `final_decision_source = legacy_ragas_with_semantic_sidecar`

### 10.2 结果解读

- 当前 ragas-ready 层最稳定的是 relevance 和 grounding。
- 主要短板仍是 `faithfulness_proxy` 偏低，这与 judge / eval 中暴露出的“规则句解释化、worldbook 原子度不足、可执行性不足”相互印证。
- 从 item 分布看，worldbook / routing 数量已经不再是主问题，主问题是条目的“忠实度、原子度和下游可执行密度”。

### 10.3 最弱条目样例

以下条目都为 `warn`，并且 `faithfulness_proxy` 明显偏低：

- `exam_screening__worldbook_binding__worldbook_worthy__rule_01`
  - `overall_score = 0.6354`
  - `faithfulness_proxy = 0.0886`
- `commercialized_conflict__worldbook_binding__rag_worthy__body_ownership_market__02`
  - `overall_score = 0.6360`
  - `faithfulness_proxy = 0.0900`
- `body_assetization__worldbook_binding__rag_worthy__body_assetization__rule_01`
  - `overall_score = 0.6402`
  - `faithfulness_proxy = 0.1005`
- `commercialized_conflict__worldbook_binding__routing_hints__body_ownership_query__02`
  - `overall_score = 0.6506`
  - `faithfulness_proxy = 0.1265`
- `exam_screening__worldbook_binding__routing_hints__rule_01`
  - `overall_score = 0.6688`
  - `faithfulness_proxy = 0.1719`

## 11. 本次 run 暴露出的真实问题

### 11.1 正式主链已经稳定，但质量门仍未收敛

- 本次不再是“跑不通”问题。
- 当前问题已经收敛到“跑得通，但产物质量还没有达到正式 gate”。

### 11.2 `section_completeness` 仍是主阻塞项

- scalar 字段已经齐了。
- 主阻塞项已经清晰收敛为核心 rule list 厚度不足。
- densify 虽有成功请求，但没有把 underfilled 主路径真正推到配置要求的最小条数。

### 11.3 `actionability` 是本轮最硬的结构性短板

- `19` 个候选 rule-bearing sections 中，`0` 个被 evaluator 判定为 actionable。
- 这说明 prompt contract 虽然已经分层，但最终文本表达还没有从“解释语气”彻底切到“可执行规则语气”。

### 11.4 `anti_pattern_resistance` 的主问题不是 generic praise，而是 vague routing 与 worldbook 颗粒度

- `generic_language` 已经完全受控。
- 当前碰撞主要集中在：
  - `VAGUE_ROUTING`
  - `UNGROUNDED_WORLDBOOK`
  - 少量 `KEYWORD_STUFFING`

### 11.5 当前不应打开 semantic cutover

- 本次 live 全程保持 shadow-only。
- 既然 eval / judge gate 仍未过，就不应把 router semantic cutover 从 `false` 切成 `true`。
- 因此本轮不存在“semantic-first 回滚失败”的问题，当前策略本身是正确的。

## 12. 下一步建议（按优先级）

### P1. 先补 section completeness，而不是继续扩运行范围

- 优先补齐：
  - `narrative_system.engine`
  - `narrative_system.pacing_rules`
  - `narrative_system.plot_node_logic`
  - `expression_system.*_rules`
  - `aesthetics_system.*`
- 这些路径当前全部停在 `1 / N`，是 eval fail 的直接来源。

### P2. 把主规则全面改写成“动作化合同句”

- 目标不是再加解释，而是把规则写成真正可执行的：
  - “当……时”
  - “优先……”
  - “不要……”
  - “出现……时路由到……”

### P3. 定点清理 `VAGUE_ROUTING` 与 `UNGROUNDED_WORLDBOOK`

- routing hints 数量已经够了，下一步应清理模糊触发条件和空泛目标动作。
- worldbook 条目也不是数量不够，而是要提高 atomicity 与 faithfulness。

### P4. 在质量门未收敛前，继续维持 shadow-only

- 保持：
  - `semantic_shadow_enabled = true`
  - `router_semantic_cutover_enabled = false`
- 先用 sidecar 继续观测，不让 semantic-first 接管正式路由。

## 13. 关键工件路径

### 13.1 Style / Canon / Build 主产物

- `D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable`
- `D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\style_bible_final.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\style_bible_reasoning.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\style_bible_export_flat.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\style_bible_reduce_trace.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\semantic_dedupe_drop_pairs_aggregate.json`

### 13.2 Local Reduce / Densify / Sampling

- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\sampled_input_scope.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\sampling_report.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\batch_plan.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\_local_reduce`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable\_section_densify`

### 13.3 Eval / Judge / Ragas

- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_eval\style_eval_report.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_eval\style_eval_report.md`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_judge\judge_report.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_judge\judge_rows.jsonl`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_judge\judge_report.md`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_ragas\ragas_report.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_ragas\ragas_dataset.json`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_ragas\ragas_rows.jsonl`
- `D:\card\novel_pipeline\data\style_bible_formal_cn_gpt54_stable_ragas\ragas_report.md`

## 14. 当前状态判断

- **Full live 命令链：通过**
- **正式主产物完整性：通过**
- **Phase 2 shadow telemetry / compatibility / fallback：通过**
- **semantic cutover gating：未开启，且当前继续保持关闭是正确选择**
- **质量门：未通过，后续仍需调优**

最终判断：

本次 Phase 2 formal full live 已经完成“从 `extract-style` 起跑的正式链稳定性验收”，证明当前实现可以稳定跑通正式链、保留兼容层与回滚路径，并输出完整的 eval / judge / ragas 侧车报告；但从产物质量角度看，`section_completeness`、`actionability`、`anti_pattern_resistance`、`worldbook_exportability`、`rag_atomicity` 仍是下一轮调优的核心工作面。
