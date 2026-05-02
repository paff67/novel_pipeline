# 混合检索路线（Hybrid RAG）可行性报告与后续技术路线
日期：2026-04-18  
代码分支：`codex/gpt_ceshi`  
适用范围：`novel_pipeline_stable` 当前主线，以及后续 RP / RAG / 世界书 / 路由运行时的检索层改造

## 1. 结论摘要

基于当前项目代码状态和最新 `mini3` 重跑结果，后续检索路线采用 Hybrid RAG 是合理且可落地的。

推荐的最终拆分是：

1. 文风与叙事法则：保留现有 `Style Bible v2` 主链路，继续以 `facts + style + canon -> style_bible_final` 为核心，再用 Embedding 做增强型去重、检索和运行时召回。
2. 世界观设定与实体网络：不要继续塞进 `Style Bible` 或 `worldbook_binding` 做“伪统一检索”，而应从 `facts/canon` 侧剥离出来，重构成独立的 GraphRAG 层。

这是当前阶段最稳妥的工程选择，原因有三点：

1. `Style Bible` 已经具备结构化 schema、评估器、Judge、repair pass 和局部去重装配机制，做 Embedding 增强属于低风险增量改造。
2. 世界观侧已经有 `entities / facts / events / relationship_changes / power_system_notes / plot_nodes` 这些原材料，但尚未形成真正可查询的关系图；如果继续强塞进单一向量库，只会让“风格规则”和“事实设定”互相污染。
3. 最新评估已经明确告诉我们：当前系统的主要阻塞在 `section_completeness`、`routing_hints` 和 full profile 的 list 厚度，不在“缺一个统一向量库”；因此需要先做边界拆分，再做检索增强，而不是直接上“大一统 Embedding 方案”。

结论上：

1. `Style-RAG + GraphRAG` 的双层检索路线应当采纳。
2. 当前最优切入顺序是：
   - 先修 `routing_hints` 和 full `section_completeness`
   - 再给 Style-RAG 接 Embedding
   - 最后启动世界观 GraphRAG 剥离与重构
3. 不建议现在把 `worldbook_binding` 当作最终世界知识检索层继续堆复杂度。

## 2. 当前项目状态判断

### 2.1 已经比较成熟的部分

当前仓库已经具备一条相对完整的 `Style Bible v2` 流水线：

1. `build-style-bible`
2. `evaluate-style-bible`
3. `judge-style-bible`
4. `style_bible_source_bundle.json`
5. `style_bible_reasoning.json`
6. `style_bible_reduce_trace.json`
7. `style_bible_final.json`
8. `style_bible_export_flat.json`

最新 `mini3` 重跑也说明这条链路正在收敛：

1. full profile：`82.98`，仍然 `fail`
2. mini profile：`95.48`，仍然 `fail`
3. `section_completeness` 已在 mini profile 下修到 `pass`
4. 当前共同阻塞项集中在 `routing_hints`

这说明风格蒸馏主链路已经不再是“抽不出来”，而是“如何让输出更厚、更能驱动下游”。

### 2.2 适合做 Style-RAG 的现有基础

当前代码里已经有几个非常关键的基础设施，直接支持 Style-RAG：

1. `style_bible_reducer.py` 已经有显式的 rule merge / dedupe 机制。
2. 现有规则去重主要依赖 `_normalize_text_key()` 和 surface spec 中的 `rule_dedupe_union` / `rule_dedupe_aggressive`。
3. `StyleBibleRuleItem` 已经天然包含：
   - `trigger`
   - `constraint`
   - `query_feature_matcher`
   - `route_target_action`
   - `_reasoning_ref`
   - `evidence_refs`
4. `style_bible_evaluator.py` 和 `style_bible_judge.py` 已经把 `routing_hints` / `rag_worthy` / `worldbook_binding` 作为下游可用性检查项。

这意味着：

1. 当前风格规则已经有较稳定的 schema。
2. 当前 reducer 已经有确定的插入点可以加 Embedding。
3. 当前评估器已经能为 Style-RAG 的可用性提供质量闸门。

### 2.3 适合做 GraphRAG 的现有基础

当前世界设定侧虽然还没有正式图层，但原材料其实已经比较全：

1. fact 抽取层已经输出：
   - `entities`
   - `events`
   - `facts`
   - `relationship_changes`
   - `power_system_notes`
2. canon builder 已经聚合并输出：
   - `entities.jsonl`
   - `facts.jsonl`
   - `events.jsonl`
   - `chapter_summaries.jsonl`
   - `plot_nodes_draft.jsonl`
3. story node 体系已经提供节点级 scope 和章节范围。

但它还没有真正变成 GraphRAG，缺口主要在三处：

1. `relationship_changes` 和 `power_system_notes` 还没有在 canon 层聚合成独立的图资产。
2. 当前 `style_bible_inputs.py` 只把 `fact_rows / style_rows / chapter_rows / plot_rows / entity_rows` 装进 source bundle，没有图边、图社区、实体时间态这些结构。
3. 当前没有专门的 `world graph build / export / query` CLI，也没有世界设定专用评估口径。

这意味着 GraphRAG 不是从零开始，但也绝不是“开个向量库就完成”。

## 3. 为什么应该采用 Hybrid RAG，而不是继续走单路 RAG

### 3.1 风格规则和世界设定不是同一种知识

风格规则回答的是：

1. 怎么写
2. 什么语气
3. 什么触发条件下该召回哪类叙事法则
4. 哪些表达要避免

世界设定回答的是：

1. 谁和谁是什么关系
2. 什么机构、规则、资格、资源限制真实存在
3. 某个时间段内哪些事实成立
4. 某个节点前后实体状态如何变化

这两类知识的检索结构天然不同：

1. 风格规则更接近规则检索、相似约束检索、写法召回。
2. 世界设定更接近实体对齐、路径查询、局部子图召回、时序事实追踪。

如果强行统一到单一路径，会出现两个问题：

1. 风格规则被世界事实稀释，向量近邻会混出大量“事实相似但写法无关”的噪声。
2. 世界设定被风格文本污染，回答事实问题时容易召回“会怎么写”而不是“真实发生了什么”。

### 3.2 当前 `worldbook_binding` 更像过渡接口，不该继续膨胀成事实主库

从现有 schema 看，`worldbook_binding.rag_worthy`、`worldbook_worthy`、`routing_hints` 本质上仍然是风格蒸馏产物里的“下游桥接字段”。它们适合做：

1. 风格线索的路由提示
2. 可以入库的高价值短规则
3. 写作时的风格预设召回

但它们不适合承担：

1. 权威实体图谱
2. 关系演化网络
3. 世界规则主库
4. 剧情节点事实审计

因此，继续把世界知识塞进 `worldbook_binding`，只会让它越来越像“半结构化垃圾场”。

### 3.3 GraphRAG 最适合接管世界设定层

GraphRAG 的好处不在“更高级”，而在它更匹配世界设定的真实结构：

1. 实体是节点
2. 关系变化是边
3. 章节与主节点是 scope
4. 事件是时序化子图
5. 机构、资格、门槛、资源规则可以被整理成图上的类型化边和规则节点

这正好契合本项目的小说世界知识结构。

## 4. 可行性评估

### 4.1 总体可行性判断

| 子方案 | 可行性 | 风险 | 原因 |
| --- | --- | --- | --- |
| Style-RAG（维持现状 + Embedding 增强） | 高 | 低 | 已有 Style Bible schema、reducer merge、Eval/Judge，可直接增量接入 |
| World GraphRAG（从 facts/canon 剥离） | 中高 | 中 | 原材料齐，但缺图导出、关系聚合、图评估与运行时查询层 |
| 单一路径统一向量库 | 低 | 高 | 会混淆风格规则与事实设定，且不能解决世界关系查询问题 |

### 4.2 Style-RAG 的可行性

判断：高可行。

原因：

1. 现有 `StyleBibleRuleItem` 已经足够结构化。
2. reducer 的 rule merge 正好有 Embedding 去重插入点。
3. Style-RAG 可以完全作为增强层接入，不必推翻当前主链路。
4. 即使 Embedding 子系统暂时关闭，当前系统仍然能按原逻辑运行。

主要风险：

1. 阈值调不好会误合并风格相近但约束不同的规则。
2. 如果过早把 Embedding 拉进 router/batching/judge 全链路，会放大系统复杂度。

因此建议先从 reducer dedupe 和 runtime retrieval 两个位置进入，不要一步到位全链替换。

### 4.3 GraphRAG 的可行性

判断：中高可行。

原因：

1. 事实层已具备实体、事实、事件、关系变化、世界规则备注等原材料。
2. 节点版 scope 已经存在，非常适合做局部子图检索和分区构建。
3. 世界设定本来就比风格规则更适合图结构。

主要风险：

1. canon 层目前没有单独导出关系边和世界规则边。
2. 实体归并现在主要还是名字归并，GraphRAG 对实体消歧的要求更高。
3. 如果一开始就绑定重型外部图库，工程复杂度会陡增。

因此建议 GraphRAG 的第一版先走“离线图资产 + 文件级查询 + 本地社区摘要”的轻量路线，不要一开始就押注重型运行时服务。

## 5. 目标架构

推荐的目标架构如下：

```text
原始小说
  -> 清洗 / 切章 / 切 scene
  -> fact 抽取
  -> style 抽取
  -> Canon 构建
      -> Style Bible v2
          -> Style-RAG Export
              -> 风格规则索引
              -> 风格证据索引
              -> Embedding 检索增强
      -> World Graph Build
          -> 实体节点
          -> 关系边
          -> 事件子图
          -> 规则节点
          -> 社区摘要
              -> GraphRAG Query
                  -> 世界设定检索
                  -> 实体网络检索
                  -> 节点级世界书导出
  -> Runtime Hybrid Retriever
      -> Style Retriever
      -> World Graph Retriever
      -> Router / Prompt Assembler
  -> RP / 世界书 / 中间件
```

职责边界建议固定为：

1. `Canon`：权威事实底座
2. `Style Bible`：风格与叙事规则底座
3. `Style-RAG`：写法、语气、叙事法则检索
4. `GraphRAG`：实体、设定、关系、节点事实检索
5. `Router / Middleware`：决定当前问题该优先走 Style 还是 World

## 6. 基于现有项目的后续实现改造路线

### Phase 0：先把 Style Bible 当前质量门修稳

这是 Hybrid RAG 的前置条件，不是可选项。

优先做两件事：

1. 修 `worldbook_binding.routing_hints`
   - 强制输出“触发条件 + 路由目标节点 + 召回动作”
   - 让 `useful_routing_hint_ratio` 先从 `0.0` 拉起来
2. 把 full profile 下的 list section 补到最小条数
   - `core_axes`
   - `pressure_axes`
   - `pacing_rules`
   - `description_rules`
   - `dialogue_rules`
   - `characterization_rules`
   - `sensory_rules`
   - `nonstandard_xianxia_rules`

没有这一步，后面的 Style-RAG 接 Embedding 只会放大薄输出的问题。

### Phase 1：Style-RAG 接 Embedding，但只做增强，不做替换

建议的代码切入点：

1. `src/novel_pipeline_stable/style_bible_reducer.py`
   - 在 `_group_rule_candidates()` 或 `_merge_group_rule_item()` 上游增加语义近邻分组
   - 保留现有 `_normalize_text_key()` 作为第一道精确去重
   - Embedding 只处理“字面不等但高度近义”的候选
2. `src/novel_pipeline_stable/style_bible_surface_specs.py`
   - 为 `rag_worthy / worldbook_worthy / routing_hints` 增加更清晰的高风险合并策略注释与阈值配置入口
3. 新增独立导出层
   - 建议新增 `style_bible_retrieval_export.py`
   - 输出 `style_retrieval_rules.jsonl`
   - 输出 `style_retrieval_embeddings_manifest.json`

建议的导出粒度：

1. rule item 级
2. supporting evidence 级
3. bucket / axis 摘要级

建议的检索打分：

1. lexical score
2. matcher overlap score
3. embedding similarity
4. bucket prior / axis prior

也就是说，Style-RAG 走“混合检索”，不是纯向量近邻。

### Phase 2：把世界观图原材料从 canon 层显式导出来

这是 GraphRAG 的真正起点。

当前最值得补的不是图库，而是图资产合同。

建议新增或修改：

1. `src/novel_pipeline_stable/canon_builder.py`
   - 新增导出：
     - `relations.jsonl`
     - `power_system_notes.jsonl`
     - `entity_mentions.jsonl`
     - `fact_scopes.jsonl`
2. `src/novel_pipeline_stable/models.py`
   - 增加图资产模型：
     - `GraphEntityNode`
     - `GraphRelationEdge`
     - `GraphEventNode`
     - `GraphRuleNode`
     - `GraphCommunitySummary`
3. 新增 CLI
   - `build-world-graph`
   - `export-worldbook-from-graph`

这一步的目标不是把 GraphRAG 跑起来，而是让图资产第一次成为正式产物，而不是散落在 facts JSON 中的隐含信息。

### Phase 3：构建第一版 GraphRAG

建议先做轻量版，不绑定重型运行时依赖。

第一版 GraphRAG 可以采用：

1. 图节点和边先落到 JSONL
2. 离线构建社区摘要和节点摘要
3. 查询时先做：
   - 实体命中
   - 章节 / story node scope 过滤
   - 邻域扩展
   - 社区摘要拼装

建议新增：

1. `src/novel_pipeline_stable/world_graph_builder.py`
2. `src/novel_pipeline_stable/world_graph_query.py`
3. `src/novel_pipeline_stable/world_graph_export.py`

GraphRAG 第一版的查询目标应聚焦在：

1. 人物关系
2. 阵营关系
3. 机构审批链
4. 资格 / 资源 / 成本规则
5. 某节点范围内的事实状态

### Phase 4：运行时混合检索器

当前项目未来的运行时不要只有一个“RAG 命令”，而应该显式分成两个检索器：

1. `StyleRetriever`
2. `WorldGraphRetriever`

再由一个轻量路由层决定：

1. 当前问题是“怎么写”还是“事实是什么”
2. 是需要风格约束、事实约束，还是两者都要
3. 是否需要节点范围或章节范围过滤

建议新增：

1. `src/novel_pipeline_stable/hybrid_retriever.py`
2. `src/novel_pipeline_stable/runtime_router.py`

### Phase 5：世界书导出改走 GraphRAG，不再从 Style Bible 直接承担

后续 `worldbook` 导出应做职责拆分：

1. 风格相关世界书预设
   - 仍可从 `Style Bible` 导出
   - 只负责语气、禁则、写作习惯、特定题材写法
2. 世界设定世界书
   - 改由 GraphRAG 导出
   - 负责实体、组织、规则、资格门槛、地理关系、剧情节点状态

这一步会直接降低 `Style Bible` 的职责膨胀。

## 7. 建议的文件级改造清单

### 7.1 需要修改的现有文件

1. `src/novel_pipeline_stable/style_bible_reducer.py`
   - 加入 Embedding 增强去重的插桩点
2. `src/novel_pipeline_stable/style_bible_evaluator.py`
   - 新增 Style-RAG 导出可用性检查
3. `src/novel_pipeline_stable/style_bible_judge.py`
   - 增加 style retrieval 命中质量与 routing executability 的强化评估
4. `src/novel_pipeline_stable/canon_builder.py`
   - 显式导出关系图原材料
5. `src/novel_pipeline_stable/style_bible_inputs.py`
   - 明确把 Style Bible 输入边界限定在 style-side，避免后续继续向世界图职责膨胀

### 7.2 建议新增的文件

1. `src/novel_pipeline_stable/style_bible_retrieval_export.py`
2. `src/novel_pipeline_stable/world_graph_builder.py`
3. `src/novel_pipeline_stable/world_graph_query.py`
4. `src/novel_pipeline_stable/world_graph_export.py`
5. `src/novel_pipeline_stable/hybrid_retriever.py`
6. `config/style_retrieval_rules.toml`
7. `config/world_graph_rules.toml`

## 8. 风险与控制策略

### 8.1 Style-RAG 的主要风险

风险：

1. 语义去重过强，误合并相近但不等价的规则
2. 把当前尚未修稳的薄 section 直接做向量索引，导致检索结果看似高级但实际空心

控制策略：

1. 先保留字面去重，再叠语义去重
2. 只对高风险路径设置更高阈值
3. 所有语义合并都必须保留 evidence lineage

### 8.2 GraphRAG 的主要风险

风险：

1. 实体消歧不足会把多个角色或机构混成同一个节点
2. 关系边如果没有时间态，会导致节点状态串期
3. 图资产如果直接绑定运行时数据库，会拖慢初版交付

控制策略：

1. 第一版先做文件级图资产，不急着服务化
2. 每条边都保留 `source_ref / chapter_id / scene_id / node_scope`
3. 用 story node scope 作为天然分区边界

## 9. 推荐执行顺序

建议按以下顺序推进：

1. 修 `routing_hints`
2. 补 full `section_completeness`
3. 接入 Style-RAG 的 Embedding 增强去重
4. 导出 style retrieval index
5. 在 canon 层补关系边 / 规则边 / world graph 原材料
6. 实现轻量版 `build-world-graph`
7. 实现 `HybridRetriever`
8. 最后再决定是否把 GraphRAG 绑定到独立运行时服务

## 10. 最终判断

从当前项目阶段来看，Hybrid RAG 不是“锦上添花”的探索项，而是防止后续系统继续职责混乱的必要架构修正。

最准确的判断是：

1. `Style Bible` 应继续做强，但只负责风格与叙事法则。
2. `Embedding` 应先作为 Style-RAG 的增强器，而不是当前项目的总开关。
3. 世界设定、实体网络、关系与规则应尽快从 style-side worldbook 字段中剥离，转向独立 GraphRAG。

这条路线既尊重当前代码已经跑通的部分，也避免把后续世界知识系统继续做成一个不可维护的“大 JSON + 大向量库”。

