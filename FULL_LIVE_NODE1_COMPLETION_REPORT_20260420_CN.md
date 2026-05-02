# Full Live Node1 运行报告（2026-04-20）

## 1. 结论摘要

- 本次完成了 `main_01_kunxu_l1_ch0001_0270` 这个 story node 的全链路实跑与收尾评估。
- 运行链路覆盖：`extract-style` 产物参与的 `build-canon -> build-style-bible -> evaluate-style-bible -> evaluate-style-bible-ragas`。
- `style_bible` 构建成功，最终输出已完整落盘：`style_bible_final.json`、`style_bible_reasoning.json`、`style_bible_export_flat.json`、`style_bible_reduce_trace.json`、`semantic_dedupe_drop_pairs_aggregate.json`。
- `evaluate-style-bible` 得分为 **87 / 100**，但 **quality gate 仍为 fail**；唯一 fail 项仍然是 `section_completeness`。
- `evaluate-style-bible-ragas` 已跑通，`routing_hints + rag_worthy + worldbook_worthy` 共 **9** 条，`average_overall_score = 0.6036`，`average_grounding_ratio = 1.0`，但 `faithfulness_proxy` 仍偏低。
- 本轮确认生效的关键修复：
  - sampling cap 真正下传到 router / batching；
  - local reduce 兼容 compact reasoning shape 与错误的 `_reasoning_ref`；
  - JSON repair 产生的空占位壳对象会在入模前自动折叠为空数组，不再卡死 repair；
  - `style_bible_ragas_eval.py` 修复了新 ref 合并时的 `KeyError`。
- 需要特别说明：本次完成的是 **full-live 根目录下 node1 的全链路稳定化实跑**，不是 5 个 confirmed story nodes 的全量批跑。按本次 node1 耗时估算，全量 5 节点串行执行预计需要 10 小时以上，建议在当前 node1 方案继续收敛后再批量开跑。

## 2. 本轮关键代码修复

### 2.1 已用于本次成功运行的修复

- `src/novel_pipeline_stable/style_bible_builder.py`
  - 修复 phase01 control plane：sampling 上限不再只影响 `style_bible_source_bundle`，而是实际下传到 router / batching 输入。
  - 新增 `sampled_input_scope.json`，用于记录真实进入 style bible 阶段的采样范围。

- `src/novel_pipeline_stable/models.py`
  - `StyleBibleReasoningEntry` 兼容 compact shape（如 `{"_reasoning_ref","text","evidence_refs"}`）。
  - `StyleBibleLocalReducerOutput` 会尝试从 `evidence_refs` 自动恢复错误填成 window/evidence ref 的 `_reasoning_ref`。
  - 新增空壳折叠逻辑：repair 生成的空占位 JSON（空 `surface_path` / 空 `rule_rows` 壳对象）会在入模前被清理为真正的空数组。

- `prompts/style_bible_local_reduce.md`
  - 收紧合同：`_reasoning_ref` 必须来自 `reasoning.entries[*].reasoning_id`。
  - `reasoning.entries[*]` 必须显式输出 `reasoning_id / claim / evidence_refs`。

### 2.2 本轮补充修复

- `src/novel_pipeline_stable/client.py`
  - JSON repair 规则补强：无法恢复的 list item 直接删除，不允许再输出空 placeholder object。

- `src/novel_pipeline_stable/style_bible_ragas_eval.py`
  - `_append_context()` 改为 `setdefault`，避免 reduce trace 引入新 ref 时触发 `KeyError`。

- 测试补绿
  - `tests/test_style_bible_local_reduce_contracts.py`
  - `tests/test_style_bible_ragas_eval.py`

## 3. 运行范围与配置

### 3.1 运行对象

- Story node: `main_01_kunxu_l1_ch0001_0270`
- 标签：`一层阶段`
- 章节范围：`0001-0270`

### 3.2 运行根目录

- Full live 根目录：
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949`
- 本次稳定化实跑输出目录：
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270`

### 3.3 模型与网关

- Style Bible 生成模型：`gpt-5.4`
- Embedding 模型：`Qwen/Qwen3-Embedding-8B`
- Embedding 网关：`.env` 中配置的 SiliconFlow 兼容网关

### 3.4 运行耗时

- `run_status.json` 记录：
  - `started_at`: `2026-04-19T20:23:48.281863Z`
  - `finished_at`: `2026-04-19T22:46:55.054296Z`
- 换算为上海时间：
  - 开始：`2026-04-20 04:23:48`
  - 完成：`2026-04-20 06:46:55`
- 总耗时：约 **2 小时 23 分钟**

## 4. Phase01 / Sampling / Batching

本轮 control plane 修复后的 phase01 压缩效果如下：

| 类型 | 原始数量 | 过滤后数量 |
| --- | ---: | ---: |
| scene | 1132 | 24 |
| style_window | 135 | 24 |
| chapter | 270 | 66 |
| plot_node | 270 | 85 |
| entity | 2611 | 679 |

补充说明：

- `scene/style` 被严格压缩到采样上限；
- `chapter/plot/entity` 会在采样 scene/style 的章节范围内做扩展保留，避免支持证据链被截断；
- 本次 `batch_plan.json` 生成了 **23 个 batch**，明显低于修复前的 264，phase01 爆炸问题已被抑制。

## 5. Local Reduce 与 Repair 情况

### 5.1 Local Reduce 总体结果

- Local reduce bucket 总数：`12`
- 使用 repair 的 bucket 数：`6`
- repair 总轮次：`6`
- 稀疏 bucket 数：`1`
- 稀疏 bucket：`collective_production`
- 非关键 bucket 无致命失败，`failures.json` 为空

### 5.2 触发 repair 的 bucket

- `body_assetization`
- `exam_screening`
- `family_survival`
- `gray_labor`
- `identity_shame`
- `resource_pressure`

### 5.3 各 bucket 最终 rule_count

| bucket | rule_count |
| --- | ---: |
| asset_repricing | 6 |
| body_assetization | 11 |
| collective_production | 0 |
| commercialized_conflict | 9 |
| contract_sales | 6 |
| dark_humor | 6 |
| exam_screening | 12 |
| family_survival | 9 |
| gray_labor | 10 |
| identity_shame | 9 |
| institutional_pipeline | 11 |
| resource_pressure | 13 |

### 5.4 Repair 阶段的真实改进

本轮最关键的稳定性收益是：

- 旧代码里，`family_survival` 的 repair 会把非法输出“修复”成一个空 JSON 壳对象；
- 新代码会把这类空壳在 `StyleBibleLocalReducerOutput` 入模前折叠为空数组；
- 因此 repair 不再因空 `surface_path` 而导致 Pydantic 校验中断；
- 本次续跑已经实测通过了之前会卡住的 `body_assetization` 与后续多桶 repair。

## 6. Section Densify 与 Embedding 实际作用

### 6.1 本次 embedding 具体做了什么

本次 embedding **没有参与 local reduce 主生成**，而是作为 `section_densify` 的检索与过滤中间件，主要承担两类角色：

1. **Slot Query 检索探针**
   - 把缺失 slot 描述转成向量；
   - 在全局 `reasoning.entries` 中做 Top-K 召回；
   - 让 densifier 只看到与当前缺失槽位最相关的 reasoning 证据。

2. **Slot Coverage / Semantic Filter 护栏**
   - 用向量相似度判断候选规则是否真的命中目标 slot；
   - 未达到阈值的候选不会被保留到最终 `style_bible_final.json`；
   - 同时记录 `semantic_dedupe_drop_pairs.json` 与聚合文件。

### 6.2 本次 densify 目标与结果

本轮 densify 实际只打到了两个 worldbook 方向路径：

| target_path | actual_count | target_count | status |
| --- | ---: | ---: | --- |
| `worldbook_binding.routing_hints` | 2 | 4 | `filtered_empty` |
| `worldbook_binding.worldbook_worthy` | 3 | 4 | `filtered_empty` |

### 6.3 为什么 densify 最终没留下新条目

这次不是“模型完全没产出”，而是：

- densifier prompt 的 `partial` 里 **确实生成了候选 rule_rows**；
- 但候选经过 slot coverage 过滤后，**没有任何一条达到阈值**，最终 `kept_rule_count = 0`；
- 因而两个 pass 都以 `filtered_empty` 结束，没有增量写回最终 Style Bible。

两个 densify pass 的代表性信息：

- `worldbook_binding.routing_hints`
  - `retrieved_reasoning_count = 10`
  - LLM 调用耗时：`476.963s`
  - 最佳 slot 匹配分数区间：`0.4669 ~ 0.6263`
  - 阈值：`0.8`

- `worldbook_binding.worldbook_worthy`
  - `retrieved_reasoning_count = 12`
  - LLM 调用耗时：`417.086s`
  - 最佳 slot 匹配分数区间：`0.4060 ~ 0.5203`
  - 阈值：`0.8`

### 6.4 本次 embedding 请求表现

以 `worldbook_binding.routing_hints` 为例：

- `slot_queries`
  - 4 个 query
  - 全部 cache hit
  - 耗时 `0.001s`

- `reasoning_entries`
  - 157 条 reasoning entries
  - 首次全量 embedding
  - 按 `max_batch_size=16` 做批处理
  - 10 个 batch 全部成功
  - 总耗时 `13.774s`

第二个 densify path 对同一批 reasoning entries 已实现 **全量缓存命中**：

- `cache_hit_count = 157`
- `total_elapsed_seconds = 0.066s`

这说明：

- `StableOpenAICompatibleEmbeddingClient` 的 **批量请求** 已生效；
- 本地磁盘缓存 + 内存缓存也已生效；
- 没有出现“for 循环逐条发 embedding API”的退化情况。

### 6.5 Semantic Dedupe 结果

- `semantic_dedupe_drop_pairs_aggregate.json`
  - `pair_file_count = 2`
  - `drop_pair_count = 0`

本次 densify 的候选最后没有进入“被语义去重丢弃”的阶段，而是在更前面的 slot / quality filter 阶段就被清空了。

## 7. 最终 Style Bible 产物概览

### 7.1 关键条目数量

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
| `worldbook_binding.rag_worthy` | 4 |
| `worldbook_binding.worldbook_worthy` | 3 |
| `worldbook_binding.routing_hints` | 2 |
| `negative_rules` | 1 |
| `supporting_evidence` | 20 |
| `reasoning.entries` | 157 |

### 7.2 标量字段状态

标量路径本轮是正常的：

- `narrative_system.perspective`
- `narrative_system.distance`
- `narrative_system.temporality`
- `voice_contract.narrator_voice`
- `voice_contract.inner_monologue_mode`

`section_completeness` 的失败已经不再来自 scalar 缺失，而是来自 **list thickness 不足**。

## 8. Offline Eval 结果

### 8.1 `evaluate-style-bible`

- 评估输出目录：
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible_eval`

- 总结：
  - `status = fail`
  - `overall_score = 87.0 / 100`
  - `quality_gate_passed = false`
  - `check_counts = pass: 7 / warn: 3 / fail: 1`

### 8.2 唯一 fail 项：`section_completeness`

失败信息：

- `message`: `Some sections exist but are thinner than the configured minimums.`
- `scalar_field_completion_ratio = 1.0`
- `minimum_field_completion_ratio = 0.0526`

仍然低于 minimum 的路径：

- `narrative_system.engine`：`1 / 3`
- `narrative_system.pacing_rules`：`1 / 4`
- `narrative_system.plot_node_logic`：`1 / 3`
- `expression_system.description_rules`：`1 / 4`
- `expression_system.dialogue_rules`：`1 / 4`
- `expression_system.characterization_rules`：`1 / 4`
- `expression_system.sensory_rules`：`1 / 4`
- `aesthetics_system.core_axes`：`1 / 5`
- `aesthetics_system.pressure_axes`：`1 / 5`
- `aesthetics_system.humor_recipe`：`1 / 4`
- `aesthetics_system.satire_targets`：`1 / 4`
- `aesthetics_system.nonstandard_xianxia_rules`：`1 / 4`

### 8.3 Warn 项

- `routing_hints`
  - `routing_hint_count = 2`
  - `useful_routing_hint_count = 1`
  - `useful_routing_hint_ratio = 0.5`
  - 仍有 1 条过于宽泛

- `worldbook_binding`
  - `rag_item_count = 4`
  - `worldbook_item_count = 3`
  - `useful_binding_ratio = 0.7143`
  - 仍有条目偏抽象、偏说明句，而不是原子可入库设定

- `anti_pattern_resistance`
  - `violation_ratio = 0.2314`
  - 主要可见问题：
    - `VAGUE_ROUTING`
    - `KEYWORD_STUFFING`
    - `UNGROUNDED_WORLDBOOK`

## 9. Ragas-ready 结果

### 9.1 总体指标

- 输出目录：
  - `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible_ragas`

- Summary：
  - `total_items = 9`
  - `item_type_counts = {rag_worthy: 4, routing_hints: 2, worldbook_worthy: 3}`
  - `average_overall_score = 0.6036`
  - `average_faithfulness_proxy = 0.2787`
  - `average_relevance_proxy = 0.5686`
  - `average_grounding_ratio = 1.0`
  - `pass_count = 0`
  - `warn_count = 8`
  - `fail_count = 1`

### 9.2 结果解读

- **优点**：`grounding_ratio = 1.0`
  - 说明 `evidence_refs` 与 source bundle / reduce trace 的 ref 对齐是好的；
  - 当前问题不是“完全编造证据引用”。

- **短板**：`faithfulness_proxy` 偏低
  - 说明条目虽然有 ref，但文本表达仍然偏“泛化总结”，不是直接贴着原始证据写；
  - 这与 `evaluate-style-bible` 给出的 `worldbook_binding` / `routing_hints` 警告是一致的。

### 9.3 最弱条目

最低分条目是：

- `commercialized_conflict__commercialized_conflict_rag_special_mana_scoring_01`
  - `item_type = rag_worthy`
  - `overall_score = 0.4956`
  - `status = fail`

它的问题不是 ref 错，而是条目文本离上下文证据的语义贴合度仍然不够。

## 10. 这次 run 暴露出的真实问题

### 10.1 `section_completeness` 还没被真正解决

这次 run 的核心结论很明确：

- **scalar 合同已经基本修绿**；
- 但 **核心 list sections 仍然只有 1 条**；
- 所以当前 fail 的重心已经不再是 “字段缺失”，而是 “厚度不够”。

### 10.2 当前 densifier 只碰了 worldbook 路径，没有补到真正缺分最严重的核心 list

本次 eval fail 的 12 个 underfilled path 全部来自：

- `narrative_system.*`
- `expression_system.*`
- `aesthetics_system.*`

但 densify 实际只跑了：

- `worldbook_binding.routing_hints`
- `worldbook_binding.worldbook_worthy`

这意味着当前 control plane 还没有把 densifier 的火力真正打到最需要补厚的主战场。

### 10.3 densifier 已经能“写东西”，但 filter 太硬，导致留不下来

从 `section_densify_partial.json` 可以看到：

- densifier 并不是空跑；
- 它确实写出了候选 `rule_rows`；
- 但这些候选在 slot coverage / anti-pattern filter 之后被全部清空。

所以现在的问题更像是：

- prompt 有一定产出能力；
- 但 slot 定义、阈值、filter 规则与目标表达方式还没有对齐。

### 10.4 `routing_hints` / `worldbook_worthy` 仍然偏“解释句”，不够“原子规则”

评估与 ragas 都指向同一个问题：

- route 条目里有一部分还是“宽主题说明”；
- worldbook 条目里有一部分还是“剧情事件概述”；
- 这会导致 downstream 虽然能“检索到”，但不能稳定驱动后续动作。

## 11. 下一步建议（按优先级）

### P1. 把 densify 目标从 worldbook 扩到所有 underfilled 主路径

优先把 `section_targets` / `repair-only` 定向补齐范围扩展到：

- `narrative_system.engine`
- `narrative_system.pacing_rules`
- `narrative_system.plot_node_logic`
- `expression_system.description_rules`
- `expression_system.dialogue_rules`
- `expression_system.characterization_rules`
- `expression_system.sensory_rules`
- `aesthetics_system.core_axes`
- `aesthetics_system.pressure_axes`
- `aesthetics_system.humor_recipe`
- `aesthetics_system.satire_targets`
- `aesthetics_system.nonstandard_xianxia_rules`

否则 `section_completeness` 这一项不会真正翻绿。

### P2. 强化 Local Reduce：允许同一路径输出多条 grounded row，而不是默认压缩成 1 条

本次几乎所有 list path 都停在 1 条，说明 local reduce 仍在“抽主轴”，而不是“按最小条数做厚度蒸馏”。

建议：

- prompt 中为关键 list path 明示 `target_count`；
- 要求按“不同子机制 / 不同触发条件 / 不同下游动作”拆成多条；
- repair-only pass 专门补充“同路径下未覆盖子机制”。

### P3. 调整 densify filter：不要只靠 0.8 的 slot 阈值一刀切

当前两条 densify path 的 best score 只有 `0.406 ~ 0.626`，离 `0.8` 差距过大，导致候选全部被刷掉。

建议：

- 不要直接把阈值全局降得太低；
- 先加一个“中间带”：
  - `>= 0.8`：直接通过；
  - `0.6 ~ 0.8`：如果 evidence overlap 强、query_feature_matcher 结构达标，则允许进入 judge / second-pass refinement；
  - `< 0.6`：直接丢弃。

### P4. 重写 `routing_hints` / `worldbook_worthy` 的“可执行合同”

目标不是再多写主题句，而是写成：

- `routing_hints`
  - 触发条件必须窄
  - 路由目标必须是具体条目名/条目簇
  - 必须写清返回什么信息

- `worldbook_worthy`
  - 必须是制度、门槛、配额、费用、资格、流程、器官/资源机制等原子条目
  - 禁止剧情事件摘要伪装成 worldbook 条目

### P5. 在当前 node1 方案收敛后，再批跑剩余 4 个 confirmed story nodes

当前 confirmed main nodes 共 5 个：

- `main_01_kunxu_l1_ch0001_0270`
- `main_02_kunxu_l2_civil_ch0271_0510`
- `main_03_kunxu_l2_artificer_league_ch0511_0654`
- `main_04_post_league_ch0655_0738`
- `main_05_sect_arc_ch0739_0841`

建议不要现在立刻全量串行批跑，而是：

1. 先把 `section_completeness` 真正修到绿；
2. 再做 5 节点 full batch；
3. 最后再统一比较各 node 的 eval / ragas 结果。

## 12. 关键工件路径

### 12.1 Style Bible 主产物

- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\style_bible_final.json`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\style_bible_reasoning.json`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\style_bible_export_flat.json`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\style_bible_reduce_trace.json`

### 12.2 Phase01 / Sampling / Routing

- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\sampled_input_scope.json`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\style_bible_routed_index.json`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\batch_plan.json`

### 12.3 Local Reduce / Repair 细粒度日志

- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\_local_reduce\`

其中每个 bucket 下都保留了：

- `request_metrics.jsonl`
- `_raw_responses\`
- `_repair_passes\pass_01\`

### 12.4 Densify / Embedding / Semantic Dedupe

- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\_section_densify\embedding_request_metrics.jsonl`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible\semantic_dedupe_drop_pairs_aggregate.json`

### 12.5 评估结果

- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible_eval\style_eval_report.json`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible_eval\style_eval_report.md`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible_ragas\ragas_report.json`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\semantic_versions_node1_caps_smoke_fix01\main_01_kunxu_l1_ch0001_0270\style_bible_ragas\ragas_report.md`

### 12.6 Shell 级日志

- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\logs\05_node1_caps_smoke_fix01.log`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\logs\06_node1_caps_smoke_fix01_resume.log`
- `D:\card\novel_pipeline\data\live_runs\full_live_from_extract_style_20260419_231949\logs\07_node1_caps_smoke_fix01_resume_after_placeholder_fix.log`

## 13. 当前状态判断

可以把当前项目状态总结为：

- **control plane / sampling / local reduce repair 稳定性已明显改善；**
- **node1 已经能完整跑通到 eval + ragas；**
- **scalar 合同基本稳定；**
- **真正剩下的主问题已经收敛到“核心 list thickness 不足 + worldbook/routing 条目可执行性不足”。**

换句话说，当前项目已经从“跑不通 / 卡死 / schema 崩”阶段，进入了更聚焦的“如何把输出变厚、变原子、变可驱动下游”的阶段。
