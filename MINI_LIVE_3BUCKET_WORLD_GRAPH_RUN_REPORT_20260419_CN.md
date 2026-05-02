# Style Bible v2 三桶 Mini Live 与 World Graph 离线资产层运行报告

## 1. 执行结论

本轮工作已经完成两件事：

1. 以 3 个最有代表性的桶 `resource_pressure`、`institutional_pipeline`、`dark_humor` 跑完一次真实 mini live，并完成 mini/full 双评估。
2. 已经开工并落地 World Graph Build 的第一版离线资产层，能从 canon 产出图节点、图边、社区、节点摘要与别名索引。

本轮的核心结论如下：

- `World Graph` 离线层已经打通，构建成功，回归测试通过，可以作为后续 `GraphRAG`/图查询层的上游资产层继续推进。
- `Style Bible v2` 的新 prompt 与 densifier 确实改善了下游可执行性，尤其是 `routing_hints` 的句式质量已经明显提升。
- 但本轮 **没有解决 `section_completeness` 这个最高优先级问题**。`mini eval` 仍然只剩这一项 fail，说明当前最该继续打的不是 extract/canon，而仍然是 `local_reduce + section_densify + control plane`。
- `embedding` 这轮已经真实参与运行，而且批量请求与缓存正常工作；但它没有成为失败原因。真正的失败点不是向量基础设施，而是：
  - 标量字段没有稳定产出；
  - 关键 list section 仍然偏薄；
  - `rag_worthy` 仍为 `0`；
  - densifier 生成的候选没有通过 slot coverage 过滤。

一句话判断：**World Graph 离线层已经进入“可继续扩建”状态；Style Bible v2 仍处于“prompt 合同改善了，但 section completeness 还没过关”的阶段。**

## 2. 本次运行范围

### 2.1 运行根目录

`D:\card\novel_pipeline\data\reports\style_system_upgrade_20260419\20260419T021643_mini_live_3bucket_prompt_refresh_worldgraph`

### 2.2 选取范围

- 代表桶：
  - `resource_pressure`
  - `institutional_pipeline`
  - `dark_humor`
- 章节窗口：`0736-0741`
- 章节数：`6`
- scene 数：`44`
- style window 数：`3`

### 2.3 这次“从哪一步开始跑”

这次不是从原始文本重新跑 `style_extract` prompt，而是 **复用上一轮已经跑出来的真实 `style_extract` 产物**，然后继续完成：

- `build-canon`
- `build-world-graph`
- style bible phase01 artifacts
- `build-style-bible-bucket-memos`
- `reduce-style-bible`
- `eval_full`
- `eval_mini`

这样做的原因是：本轮验证重点是我们刚刚更新过的 `style_bible_local_reduce.md`、`style_bible_section_densify.md`、`section_targets`、`embedding densify` 与 reducer 行为，而不是重新验证 extract prompt 本身。

换句话说，本轮结论针对的是：

- `Style Bible v2 reducer/control plane` 能否补厚；
- `section_densify + embedding` 能否提高 `routing_hints / rag_worthy / worldbook_worthy`；
- `World Graph` 离线资产层是否能落地。

## 3. 本轮实际落地的代码改动

### 3.1 World Graph 离线资产层

- `src/novel_pipeline_stable/canon_builder.py`
  - 新增导出：
    - `relationship_changes.jsonl`
    - `power_system_notes.jsonl`
- `src/novel_pipeline_stable/models.py`
  - `CanonIndex` 新增：
    - `relationship_change_count`
    - `power_system_note_count`
- `src/novel_pipeline_stable/world_graph_builder.py`
  - 新增 World Graph 离线构建器，产出：
    - `world_graph_nodes.jsonl`
    - `world_graph_edges.jsonl`
    - `world_graph_communities.jsonl`
    - `world_graph_node_summaries.jsonl`
    - `world_graph_alias_index.json`
    - `world_graph_manifest.json`
- `src/novel_pipeline_stable/cli.py`
  - 新增 `build-world-graph` CLI 命令

### 3.2 Style Bible v2 control plane / densifier / embedding

- `config/style_bible_section_targets.toml`
  - 为 `routing_hints / rag_worthy / worldbook_worthy` 建立 `path_targets + slot_specs + downstream_shape`
- `prompts/style_bible_local_reduce.md`
  - 加入 `if/else` 式的 first-pass / repair mode 状态机隔离
  - 加强 scalar enum 合法值约束
  - 补强 `negative_rules` 模板约束
- `prompts/style_bible_section_densify.md`
  - 加入 slot-first densify 合同
  - 明确 `routing_hints / rag_worthy / worldbook_worthy` 的下游格式要求
- `src/novel_pipeline_stable/embedding_client.py`
  - 接入 `StableOpenAICompatibleEmbeddingClient`
  - 支持批量请求
  - 支持本地内存缓存与落盘缓存
- `src/novel_pipeline_stable/style_bible_reducer.py`
  - 新增 section densify 请求选择
  - 新增基于 embedding 的 slot 查询召回
  - 新增候选规则的 slot coverage 验证
  - 新增 semantic dedupe 候选丢弃日志
  - 新增聚合日志 `semantic_dedupe_drop_pairs_aggregate.json`

## 4. 回归测试结果

本轮再次确认以下回归测试通过：

1. `python -m pytest D:\card\novel_pipeline\tests\test_world_graph_builder.py -q`
   - 结果：`1 passed`
2. `python -m pytest D:\card\novel_pipeline\tests\test_style_bible_v2_schema_contracts.py -q`
   - 结果：`20 passed`
3. `python -m pytest D:\card\novel_pipeline\tests\test_style_bible_eval_profiles.py -q`
   - 结果：`3 passed`

说明当前代码层面至少满足：

- World Graph 离线资产层可构建；
- Style Bible v2 schema 合同没有被这轮改坏；
- mini/full eval profile 的判定逻辑仍然一致。

## 5. 本轮运行过程与阶段产物

### 5.1 Phase01 与 batching

- `style_bible_routed_index.json` 中 routed items 数：`47`
- `batch_plan.json` 中 batch 数：`16`
- `planner_debug_report.json` 显示：
  - scene item：`44`
  - style window item：`3`
  - batched item：`47`
  - `scene_in_any_batch_ratio = 1.0`
  - `style_window_in_any_batch_ratio = 1.0`

这说明前半段路由与装箱没有问题，coverage 很完整。

### 5.2 Bucket memos

- bucket memo 文件数：`3`
- batch memo 数：`4`

三桶 memo 均成功生成，说明上游 `memo` 阶段足以为 local reduce 提供材料。

### 5.3 Local Reduce

三个桶的 local reduce 状态如下：

| 桶 | 状态 | reasoning entries | rule 数 | 备注 |
| --- | --- | ---: | ---: | --- |
| `resource_pressure` | 成功 | 11 | 9 | 触发过 1 次 repair pass，并成功写回 |
| `institutional_pipeline` | 成功 | 4 | 9 | 基础 local reduce 成功，但 repair pass 目录不完整 |
| `dark_humor` | 成功 | 5 | 7 | 无 repair pass |

额外说明：

- `resource_pressure` 的 repair pass 成功，且明确尝试补：
  - `narrative_system.distance`
  - `narrative_system.temporality`
  - `voice_contract.inner_monologue_mode`
  - 以及若干 underfilled list path
- `institutional_pipeline` 的 repair pass 发生异常，中间只留下：
  - `_raw_responses/e0_attempt1_parse_error.txt`
  - 没有 `local_final.json`
  - 没有 `local_reasoning.json`

### 5.4 Reducer 卡住与断点恢复

这轮 fresh reduce 在 `institutional_pipeline` repair pass 时出现挂住，现场做了以下处理：

- 发现基础 local reduce 结果其实已经存在；
- 杀掉挂住的 reducer 进程；
- 改用 `--resume-local-reduce` 从断点续跑；
- 续跑成功完成最终 assemble、section densify 与 eval。

这说明当前 reducer 的恢复路径是可用的，但也说明：

- `repair pass` 仍存在稳定性隐患；
- 当前 prompt / parser 仍可能在 repair 模式下输出不稳定 JSON。

## 6. World Graph 离线资产层结果

### 6.1 Canon 输入统计

来自 `world_graph_manifest.json` 的 canon 统计：

- entities：`208`
- facts：`650`
- events：`225`
- chapter summaries：`6`
- style windows：`3`
- plot nodes：`6`
- relationship changes：`123`
- power system notes：`148`

### 6.2 World Graph 输出统计

- nodes：`2184`
- edges：`3485`
- communities：`6`
- node summaries：`2184`

节点类型分布：

- `entity`: `208`
- `event`: `225`
- `fact`: `650`
- `literal_endpoint`: `947`
- `plot_node`: `6`
- `power_rule`: `148`

边类型分布：

- `about_topic`: `148`
- `contains_event`: `225`
- `fact_relation`: `650`
- `fact_targets`: `650`
- `focuses_on`: `243`
- `located_in`: `68`
- `occurs_at`: `107`
- `participates_in`: `621`
- `relationship_change`: `123`
- `states_fact`: `650`

解析与别名统计：

- `endpoint_resolution_exact`: `1240`
- `endpoint_resolution_literal`: `1120`
- `endpoint_resolution_ambiguous`: `378`

### 6.3 本阶段的意义

这一层已经不是概念验证，而是一个真实可产出的离线资产层。它已经具备：

- 世界设定离线结构化落盘；
- 事件、事实、关系变化、能力规则的图化表达；
- 节点摘要与社区摘要的预备结构；
- alias 索引；
- 后续接入 `D1 / Qdrant / GraphRAG 查询层` 的数据源。

目前还没有做的是：

- 运行时图检索；
- GraphRAG prompt 编排；
- Qdrant 向量索引与图查询联合召回；
- 更强的 alias 消歧与实体归并。

所以当前可以判断为：**World Graph Build 的离线资产层已经“起盘成功”，下一步可以进入资产标准化与检索层设计。**

## 7. 最终 Style Bible 成品概况

来自 `style_bible_final.json` 的最终 section 计数：

| 路径 | 实际条数 |
| --- | ---: |
| `narrative_system.engine` | 1 |
| `narrative_system.pacing_rules` | 1 |
| `narrative_system.plot_node_logic` | 1 |
| `expression_system.description_rules` | 1 |
| `expression_system.dialogue_rules` | 1 |
| `expression_system.characterization_rules` | 1 |
| `expression_system.sensory_rules` | 0 |
| `aesthetics_system.core_axes` | 0 |
| `aesthetics_system.pressure_axes` | 1 |
| `aesthetics_system.humor_recipe` | 1 |
| `aesthetics_system.satire_targets` | 1 |
| `aesthetics_system.nonstandard_xianxia_rules` | 0 |
| `voice_contract.register_mix` | 1 |
| `voice_contract.negative_pitfalls` | 1 |
| `character_arc_rules` | 1 |
| `worldbook_binding.routing_hints` | 2 |
| `worldbook_binding.worldbook_worthy` | 1 |
| `worldbook_binding.rag_worthy` | 0 |
| `negative_rules` | 1 |
| `supporting_evidence` | 12 |

缺失的标量字段：

- `narrative_system.perspective`
- `narrative_system.distance`
- `narrative_system.temporality`
- `voice_contract.narrator_voice`
- `voice_contract.inner_monologue_mode`

这组现状与评估结果完全一致：**最终成品不是“完全没内容”，而是“合同骨架已经建立，但厚度还远远不够”。**

## 8. Densifier 与 Embedding 这轮具体做了什么

### 8.1 本轮 embedding 的角色

本轮 embedding 不是独立功能，而是作为 `section_densify` 的中间件，承担了三件事：

1. **缺失 slot 的语义检索**
   - 用 slot 描述向量去检索最相关的 `reasoning_entries`
2. **候选规则的 slot coverage 验证**
   - 不是模型写了就收，而是必须和 slot 语义足够接近
3. **semantic dedupe**
   - 检查新规则是否只是对已有规则的同义改写

### 8.2 实际运行到的 densify 路径

本轮总共跑了 3 个 densify pass：

- `worldbook_binding.rag_worthy`
- `worldbook_binding.worldbook_worthy`
- `worldbook_binding.routing_hints`

三个 pass 的最终状态全部是：

- `filtered_empty`

这非常关键。它说明：

- densifier **确实运行了**
- embedding **确实参与了**
- 但 **候选没有通过过滤**

### 8.3 这轮不是“被 semantic dedupe 吃掉”

本轮聚合日志：

- `semantic_dedupe_drop_pairs_aggregate.json`
  - `pair_file_count = 3`
  - `drop_pair_count = 0`

三个子日志文件也全部是空数组：

- `worldbook_binding_rag_worthy/pass_01/semantic_dedupe_drop_pairs.json`
- `worldbook_binding_routing_hints/pass_01/semantic_dedupe_drop_pairs.json`
- `worldbook_binding_worldbook_worthy/pass_01/semantic_dedupe_drop_pairs.json`

这说明本轮失败**不是**因为 embedding 去重过于激进，而是因为 densifier 候选根本没能达到 slot 覆盖要求。

### 8.4 批量请求与缓存是否生效

生效了，而且工作方式是对的。

#### `routing_hints` pass

- slot query 输入：`4`
  - `cache_hit_count = 4`
- reasoning entry 输入：`20`
  - `cache_hit_count = 5`
  - `cache_miss_count = 15`
  - `batched_text_count = 15`
  - 上游只发了 **1 次批量 embedding 请求**
  - 请求耗时约 `1.666s`

#### `rag_worthy` pass

- reasoning entry 输入：`5`
  - `cache_miss_count = 5`
  - `batched_text_count = 5`
  - 上游只发了 **1 次批量 embedding 请求**
  - 请求耗时约 `1.56s`

#### `worldbook_worthy` pass

- reasoning entry 输入：`5`
  - `cache_hit_count = 5`
  - 本地缓存直接命中
  - 总耗时约 `0.003s`

结论：

- 当前 `StableOpenAICompatibleEmbeddingClient` 的 **批量请求是工作的**
- 本地缓存与内存缓存 **是工作的**
- 没有发生“在 for 循环里一条一条发 embedding 请求”的低效问题
- 本轮真正的慢点不在 embedding，而在 `gpt-5.4` densifier 本身

### 8.5 这轮 densifier 为什么没补进去

#### `worldbook_binding.rag_worthy`

- `target_count = 4`
- `actual_count = 0`
- 缺失 slot：
  - `repayment_retrieval`
  - `approval_chain_retrieval`
  - `screening_threshold_retrieval`
  - `body_modification_retrieval`
- `kept_rule_count = 0`

而且 4 个 slot 的 `best_score` 全部是 `0.0`。

这说明当前 densifier 根本没有产出任何能触发 `rag_worthy` 槽位核销的候选。

#### `worldbook_binding.worldbook_worthy`

- `target_count = 4`
- `actual_count = 1`
- `kept_rule_count = 0`
- 最接近的已有规则得分：
  - `0.4605`
  - `0.3602`
  - `0.5451`
  - `0.33`
- 阈值：`0.8`

说明现在 surviving 的唯一 `worldbook_worthy` 规则虽有可执行性，但还远没有覆盖到 control plane 设定的 4 个槽位。

#### `worldbook_binding.routing_hints`

- `target_count = 4`
- `actual_count = 2`
- `kept_rule_count = 0`
- 最佳已有规则命中分：
  - `0.5771`
  - `0.4823`
  - `0.511`
  - `0.4644`
- 阈值：`0.8`

说明当前留下来的 2 条 `routing_hints` 是有用的，但不够“分槽位”、不够“模块化”，所以 densifier 认为它们并没有真正填上剩余的 slot。

### 8.6 一个重要的性能观察

这轮 densifier 的时间成本主要来自 LLM，不来自 embedding：

- `rag_worthy` densify 总耗时约 `348s`
- `worldbook_worthy` densify 总耗时约 `239s`
- `routing_hints` densify 总耗时约 `742s`
- 与之对比，单次 embedding 批量请求基本在 `1.5s` 左右

结论：

- 现在没有必要优先优化 embedding 模型或向量请求速度；
- 更应该优先优化：
  - densifier prompt 的可控性
  - slot 设计
  - candidate 过滤策略
  - repair pass 稳定性

## 9. 代表性产物检查

### 9.1 最终保留下来的 `routing_hints`

本轮最终留下 2 条 `routing_hints`，质量是明显比之前好的：

1. `dark_humor__routing__postwar_investigation_to_propaganda__01`
   - 核心含义：
     - 当用户追问“为什么不继续追责 / 为什么马上进入表彰直播 / 宣传如何压过调查”时
     - 系统应优先路由到“调查终止 + 表彰流程 + 跨层直播材料”
   - 这已经是可执行路由，而不是泛泛主题词

2. `institutional_pipeline__routing__divine_takeover_material`
   - 核心含义：
     - 当用户追问“为什么要原地待命 / 交文档数据权限 / 谁接管项目或人物 / 神降后为何先调查移交”时
     - 系统应路由到“天庭与宗门接管流程材料”

### 9.2 最终保留下来的 `worldbook_worthy`

只留下 1 条：

- `institutional_pipeline__worldbook__sect_recruitment_flow`
  - 核心含义：
    - 当用户询问下层修士如何进入宗门时
    - 应把宗门录用解释成“先分流、再选岗、再考试、再统一培训”的制度流程

这条本身是好的，也能驱动下游，但量还是太薄。

### 9.3 `rag_worthy` 仍然为 0

这意味着当前 Style Bible v2 在“给生成引擎提供可检索、可即时召回的执行约束”这件事上，仍然没有完成最关键的一步。

所以本轮虽然证明：

- prompt 已经更懂 downstream shape
- routing hints 质量变好了

但还不能说：

- `Hybrid RAG` 的 Style Bible 侧已经 ready

现在更准确的说法应该是：

- **`routing_hints` 已经从“泛提示”升级到“可执行路由”**
- **`worldbook_worthy` 开始有第一条像样的库项**
- **`rag_worthy` 仍然没起飞**

## 10. 评估结果分析

### 10.1 Mini Eval

`eval_mini/style_eval_report.json`

- 状态：`fail`
- 分数：`84.61`
- 唯一 fail：`section_completeness`

mini profile 下的核心缺口：

- 缺失标量：
  - `narrative_system.perspective`
  - `narrative_system.distance`
  - `narrative_system.temporality`
  - `voice_contract.narrator_voice`
  - `voice_contract.inner_monologue_mode`
- 仍偏薄的 section：
  - `expression_system.sensory_rules = 0 / 1`
  - `aesthetics_system.core_axes = 0 / 1`
  - `aesthetics_system.nonstandard_xianxia_rules = 0 / 1`
  - `worldbook_binding.rag_worthy = 0 / 1`

mini profile 下的积极信号：

- `routing_hint_count = 2`
- `useful_routing_hint_ratio = 1.0`
- `worldbook_binding` 虽然只有 `1` 条，但 useful ratio 为 `1.0`

结论：

**mini eval 已经不是“质量崩坏”，而是“成品结构骨架不错，但关键 section 还没补齐”。**

### 10.2 Full Eval

`eval_full/style_eval_report.json`

- 状态：`fail`
- 分数：`74.69`
- fail 项：
  - `section_completeness`
  - `required_axis_coverage`

full profile 下的 section 缺口更明显：

- `narrative_system.engine = 1 / 3`
- `narrative_system.pacing_rules = 1 / 4`
- `narrative_system.plot_node_logic = 1 / 3`
- `expression_system.description_rules = 1 / 4`
- `expression_system.dialogue_rules = 1 / 4`
- `expression_system.characterization_rules = 1 / 4`
- `expression_system.sensory_rules = 0 / 4`
- `aesthetics_system.core_axes = 0 / 5`
- `aesthetics_system.pressure_axes = 1 / 5`
- `aesthetics_system.humor_recipe = 1 / 4`
- `aesthetics_system.satire_targets = 1 / 4`
- `aesthetics_system.nonstandard_xianxia_rules = 0 / 4`

关于 `required_axis_coverage`，这里需要客观看待：

- full profile 是面向完整风格圣经的
- 这轮只选了 3 个代表桶做 mini live
- 所以 full eval 的 thematic axes fail **有一部分是 scope 限制造成的**

也就是说：

- `required_axis_coverage` 在这轮 full fail 中不能全部视为 prompt 回退
- 但 `section_completeness` 的 fail 是真实核心问题，不能回避

## 11. 为什么说“prompt 有帮助，但还不够”

这轮不是没有进步，进步点其实已经很明确：

### 已经改善的部分

1. `routing_hints` 的句式已经明显可执行
2. `worldbook_worthy` 已经出现首条像样条目
3. densifier + embedding 运行链路打通
4. semantic dedupe 日志、聚合日志、slot coverage 报告都能落盘
5. reducer 支持从中断后断点续跑

### 还没解决的部分

1. scalar fields 仍不稳定
2. list thickness 明显不够
3. `rag_worthy` 仍然为 `0`
4. repair pass 稳定性仍不足
5. densifier 候选仍无法穿过 slot coverage 过滤

所以这轮的正确结论不是“方案无效”，也不是“已经成功”，而是：

**control plane、prompt 合同、embedding 中间件都已经把系统推到了“接近可用”的位置，但真正的过关点仍然是 section completeness。**

## 12. 对下一步工作的具体建议

### 12.1 最高优先级：继续打 `section_completeness`

接下来最该做的仍然是：

1. 稳定补出 5 个缺失标量
   - `narrative_system.perspective`
   - `narrative_system.distance`
   - `narrative_system.temporality`
   - `voice_contract.narrator_voice`
   - `voice_contract.inner_monologue_mode`
2. 把以下 list section 至少补到 mini profile 要求：
   - `expression_system.sensory_rules`
   - `aesthetics_system.core_axes`
   - `aesthetics_system.nonstandard_xianxia_rules`
   - `worldbook_binding.rag_worthy`

如果这一关不绿，继续大规模扩展下游 RAG 只会把“空骨架”接到更复杂的检索系统上。

### 12.2 针对 densifier 的具体修正方向

本轮数据表明 densifier 的问题不是“没跑”，而是“跑完后进不来”。建议下一步这么改：

1. `rag_worthy` 不要再用过泛 slot 描述
   - 现在四个 slot 太抽象，导致模型即使写出了近似内容，也经常核销失败
2. `retrieved_reasoning_entries` 要按 slot 分桶
   - 不要把 `resource_pressure` 的高相似条目持续喂给 `worldbook_worthy / routing_hints`
   - `screening / body_modification / approval_chain` 这类 slot 需要强制引入对应桶材料
3. 降低“先产出再被判死”的概率
   - 现在更像是 densifier 写了，但 matcher/route_target_action/slot alignment 不够强，结果直接被过滤
4. 单独给 `rag_worthy` 做更严格模板
   - 强制产出“当查询 X 时 -> 检索 Y -> 返回 Z”形式
   - 并要求 evidence 至少覆盖一个实体/流程/阈值/制度规则

### 12.3 关于 embedding：现在应该继续保留，而不是撤回

这轮数据已经证明：

- embedding 基础设施稳定；
- 批量请求和缓存有效；
- 去重没有误伤；
- 检索延迟很低；
- 真正的慢点在 LLM，而不在 embedding。

因此建议：

- **继续保留 embedding 增强**
- 但不要把“评估没过”归咎于 embedding
- 下一步应优先调：
  - slot specs
  - densify prompt
  - retrieval candidate pool
  - repair 流程

### 12.4 关于 full eval 的正确使用方式

如果下一轮仍只跑 3 桶 mini live，那么：

- `mini eval` 才是主看板
- `full eval` 只作为诊断看板

如果下一轮目标是“让 full eval 也绿”，就不该只选 3 桶，而应该至少把以下主题补进来：

- `exam_screening`
- `body_assetization`
- `family_survival` 或其他能稳定承载 `family_labor` 的桶

### 12.5 World Graph 离线层的后续工作建议

World Graph 离线层可以继续并行推进，建议顺序如下：

1. 先继续做离线资产标准化
   - 稳定 node id / edge id
   - 统一 alias 归并规则
   - 明确 community summary 的 schema
2. 再补检索层接口
   - 图查询入口
   - 节点/边过滤条件
   - community 级摘要召回
3. 最后再接 `GraphRAG / D1 / Qdrant`
   - 图结构与向量检索联合召回
   - 世界设定与实体网络走 GraphRAG
   - 风格约束与叙事实务规则继续走 Style Bible + embedding 增强

我的建议是：

- **Style Bible v2 的 `section_completeness` 修补，继续作为当前主线**
- **World Graph 离线资产层，可以并行继续做，但暂时不要过早进入运行时 GraphRAG 接口层**

这样不会让主线被稀释，也能保证图层持续积累资产。

## 13. 本轮产物索引

### 13.1 关键运行产物

- run root  
  `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260419\20260419T021643_mini_live_3bucket_prompt_refresh_worldgraph`

- 最终 Style Bible  
  `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260419\20260419T021643_mini_live_3bucket_prompt_refresh_worldgraph\style_bible\style_bible_final.json`

- reducer trace  
  `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260419\20260419T021643_mini_live_3bucket_prompt_refresh_worldgraph\style_bible\style_bible_reduce_trace.json`

- mini eval  
  `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260419\20260419T021643_mini_live_3bucket_prompt_refresh_worldgraph\eval_mini\style_eval_report.json`

- full eval  
  `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260419\20260419T021643_mini_live_3bucket_prompt_refresh_worldgraph\eval_full\style_eval_report.json`

- world graph manifest  
  `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260419\20260419T021643_mini_live_3bucket_prompt_refresh_worldgraph\world_graph\world_graph_manifest.json`

- semantic dedupe 聚合日志  
  `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260419\20260419T021643_mini_live_3bucket_prompt_refresh_worldgraph\style_bible\semantic_dedupe_drop_pairs_aggregate.json`

### 13.2 本报告文件

`D:\card\novel_pipeline\MINI_LIVE_3BUCKET_WORLD_GRAPH_RUN_REPORT_20260419_CN.md`

## 14. 最终判断

如果只回答“这轮是否有价值”，答案是肯定的。

因为它已经把三个重要问题回答清楚了：

1. `embedding` 是否真正接入并稳定工作了？
   - 是，而且批量请求与缓存正常，0 误伤。
2. `section_densify` 是否真的在补 `routing_hints / rag_worthy / worldbook_worthy`？
   - 是，但目前候选过不了 slot coverage。
3. `World Graph Build` 的离线资产层能不能先起盘？
   - 能，而且已经落盘成体系化资产。

但如果问“Style Bible v2 是否已经能靠这版 prompt 解决 `rag_worthy/worldbook_worthy` 可执行性和 list thickness`？”

答案还不能说是。

更准确的结论是：

- `routing_hints` 已经显著进步；
- `worldbook_worthy` 刚刚起势；
- `rag_worthy` 仍是最清晰的硬阻塞；
- `section_completeness` 仍然是当前主战场。

