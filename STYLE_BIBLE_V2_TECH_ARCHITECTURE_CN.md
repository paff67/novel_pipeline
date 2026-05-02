# Style Bible V2 当前技术架构说明

更新时间：2026-04-18

## 1. 文档目的

本文档描述当前 `Style Bible v2` 的实际技术架构，以及它在新一版 `Hybrid RAG` 总体架构中的技术边界。

本文档只回答两件事：

1. 当前 `Style Bible v2` 是怎么工作的
2. 它接下来该怎样演化到 `Style-RAG`，以及哪些职责应该交给后续 `GraphRAG`

## 2. 系统目标

`Style Bible v2` 的目标不是再做一次“抽几段样本直接总结”的流程，而是构建一条：

1. 可路由
2. 可分桶
3. 可批处理
4. 可续跑
5. 可审计
6. 可评测
7. 可导出为 Style-RAG 上游资产

的多阶段风格蒸馏流水线。

截至当前版本，它的正式定位是：

1. 风格与叙事法则提炼层
2. Style-RAG 的上游控制平面

它不再承担：

1. 世界知识主检索层
2. 实体关系图层
3. 权威世界书主导出层

## 3. 顶层架构

当前主入口在：

1. `src/novel_pipeline_stable/style_bible_builder.py`
2. `src/novel_pipeline_stable/cli.py`

典型命令链：

1. `build-style-bible`
2. `evaluate-style-bible`
3. `judge-style-bible`

当前系统依赖的主要技术栈：

1. Python
2. Pydantic
3. TOML 配置
4. JSON / JSONL 产物合同
5. `ThreadPoolExecutor`
6. OpenAI-compatible Responses API
7. 自定义 request metrics / usage metadata / run manifest

## 4. 当前主流水线分层

### 4.1 Phase 0：输入装配与可观测性

核心文件：

1. `src/novel_pipeline_stable/style_bible_inputs.py`
2. `src/novel_pipeline_stable/style_bible_builder.py`
3. `src/novel_pipeline_stable/monitoring.py`

职责：

1. 读取 `facts`、`style`、`canon`
2. 生成 `style_bible_source_bundle.json`
3. 汇总 `run_manifest.json`
4. 汇总 request metrics / usage metadata

当前输入 bundle 主要包括：

1. `fact_rows`
2. `style_rows`
3. `chapter_rows`
4. `plot_rows`
5. `entity_rows`
6. `canon_index`
7. `style_index`
8. `story_node_scope`

技术判断：

1. 这套输入对风格蒸馏已经足够
2. 但它还不是图检索输入，因为当前缺少显式的关系边、图社区和规则节点资产

### 4.2 Phase 1：Semantic Router

核心文件：

1. `src/novel_pipeline_stable/style_bible_router.py`

职责：

1. 将全量 `scene/style_window` 映射为 `StyleBibleRoutedItem`
2. 计算轴线与 bucket membership
3. 输出 `style_bible_routed_index.json`

当前定位：

1. Router 是风格语料组织层
2. 它服务于 `Style Bible` 的 batch 规划
3. 它不是未来世界事实查询的路由器

### 4.3 Phase 2：Batch Planner

核心文件：

1. `src/novel_pipeline_stable/style_bible_batching.py`
2. `config/style_bible_batching_rules.toml`

职责：

1. 从 routed items 规划 batch
2. 输出 `batch_plan.json`
3. 输出 `planner_debug_report.json`
4. 平衡 scene / style_window 装箱结构

### 4.4 Phase 3：Prompt Assembler 与 Control Plane

核心文件：

1. `src/novel_pipeline_stable/style_bible_prompt_assembler.py`
2. `src/novel_pipeline_stable/style_bible_section_targets.py`
3. `prompts/style_bible_local_reduce.md`

职责：

1. 组装 reducer / local reduce prompt
2. 注入 `section_targets`
3. 注入 `repair_request`
4. 注入 anti-pattern

这一步是当前 `section_completeness` 修复能够落地的关键。

### 4.5 Phase 4：Bucket Memo Executor

核心文件：

1. `src/novel_pipeline_stable/style_bible_bucket_builder.py`

职责：

1. 按 batch 调用 LLM 生成 bucket memo
2. 执行 sanitize
3. 记录 request metrics
4. 支持 `--resume`

### 4.6 Phase 5：Reducer 与 Grounding

核心文件：

1. `src/novel_pipeline_stable/style_bible_reducer.py`
2. `src/novel_pipeline_stable/style_bible_surface_specs.py`

职责：

1. 读取 `bucket_memos/*.json`
2. 生成：
   - `style_bible_reasoning.json`
   - `style_bible_reduce_trace.json`
   - `style_bible_final.json`
   - `style_bible_export_flat.json`
3. 执行 grounded merge
4. 执行 repair 后装配
5. 维护 evidence lineage

当前 reducer 的关键事实：

1. 现有去重仍以 `_normalize_text_key()` 为核心
2. surface spec 已经区分：
   - `rule_dedupe_union`
   - `rule_dedupe_aggressive`
   - `append_capped`
   - `scalar_pick_one`
3. `rag_worthy / worldbook_worthy / routing_hints` 已被标成高风险路径

技术判断：

1. reducer 是最适合接入 Style-RAG 语义去重的切入点
2. 不应该从 router 或 judge 开始做大面积 Embedding 替换

## 5. Eval / Judge 当前口径

### 5.1 Eval

核心文件：

1. `src/novel_pipeline_stable/style_bible_evaluator.py`
2. `config/style_bible_eval_rules.toml`
3. `config/style_bible_eval_rules_mini.toml`

当前主要检查：

1. `schema_validity`
2. `bundle_coverage`
3. `grounding_trace_integrity`
4. `section_completeness`
5. `required_axis_coverage`
6. `supporting_evidence`
7. `actionability`
8. `routing_hints`
9. `worldbook_binding`
10. `generic_language`
11. `anti_pattern_resistance`

### 5.2 Judge

核心文件：

1. `src/novel_pipeline_stable/style_bible_judge.py`
2. `config/style_bible_judge_rules.toml`

Judge 负责：

1. 语义裁判
2. Gold set 命中评估
3. 风格规则可执行性评估
4. `routing_executability`
5. `worldbook_exportability`
6. `anti_pattern_resistance`

这意味着当前系统已经具备 Style-RAG 需要的质量门，而 GraphRAG 侧的评估口径还没有建立。

## 6. 最新质量状态

基于最新 `mini3` 重跑结果，当前最准确的技术状态是：

1. mini profile 下 `section_completeness` 已经通过
2. full profile 下已不再缺 scalar，但仍缺 list 厚度
3. `routing_hints` 在 full / mini 下都仍然失败
4. `worldbook_binding` 在 mini 下已过，但 full 下仍偏薄

这说明当前技术收口点已经很明确：

1. 不是再改 extract/canon
2. 不是先上统一向量库
3. 而是先把 `Style Bible` 作为 Style-RAG 母体补厚、补稳、补可执行

## 7. 新的系统边界

在 `Hybrid RAG` 总体方案下，`Style Bible v2` 的边界正式变为：

### 7.1 它负责什么

1. 叙事法则
2. 表达法则
3. 审美轴线
4. voice contract
5. negative rules
6. 风格侧 routing hints
7. Style-RAG 检索资产导出

### 7.2 它不再负责什么

1. 权威实体关系主图
2. 世界规则图谱
3. 机构审批链事实主库
4. 世界设定的最终 worldbook 导出主路径

这也意味着当前 schema 里的：

1. `rag_worthy`
2. `worldbook_worthy`
3. `routing_hints`

应被理解为 Style-RAG 桥接字段，而不是未来世界知识图层的永久数据结构。

## 8. 后续技术路线

### 8.1 先修当前 Style Bible 主阻塞

优先级最高的仍然是：

1. `routing_hints`
2. full profile 的 `section_completeness`

没有这一步，后续 Style-RAG 的召回质量不会稳定。

### 8.2 再接 Style-RAG Embedding 增强

建议切入点：

1. `style_bible_reducer.py`
2. `style_bible_surface_specs.py`

建议做法：

1. 保留字面归并
2. 只对高风险或高价值路径叠加语义去重
3. 继续保留 evidence lineage 和 reasoning lineage

### 8.3 世界设定改走 GraphRAG

建议新增：

1. `world_graph_builder.py`
2. `world_graph_query.py`
3. `world_graph_export.py`

建议先在 `canon_builder.py` 补出图原材料：

1. `relations.jsonl`
2. `power_system_notes.jsonl`
3. `entity_mentions.jsonl`
4. 节点范围感知的事实 scope

### 8.4 最后接运行时 Hybrid Retriever

未来运行时应该显式拆成：

1. `StyleRetriever`
2. `WorldGraphRetriever`
3. `HybridRetriever`

而不是继续依赖一个模糊的“RAG 检索层”。

## 9. 当前已知问题

### 9.1 当前最关键的问题已经不是路由覆盖，而是输出质量门

当前最重要的技术风险不是“batch 没吃进去”，而是：

1. `routing_hints` 太模糊
2. full profile 的多个关键 section 太薄
3. `worldbook_binding` 仍然承担了过多未来职责想象

### 9.2 世界图原材料还没有正式资产化

虽然 facts 层已经有：

1. `relationship_changes`
2. `power_system_notes`

但它们还没有在 canon 层变成正式输出，因此 GraphRAG 仍处在“有原料、没图层”的状态。

### 9.3 当前还没有 Style-RAG / GraphRAG 的导出合同

这意味着：

1. 当前 `style_bible_final.json` 仍然是风格主产物
2. 但还没有正式 retrieval export
3. 后续必须补专门的检索资产层，而不是直接让运行时读取 `style_bible_final.json`

## 10. 一句话总结

当前 `Style Bible v2` 已经是一条完整的风格蒸馏流水线；它接下来的正确演进方向不是继续承担世界知识系统，而是先把自己收敛成稳定的 Style-RAG 上游，再把世界观设定与实体网络交给后续 GraphRAG 体系。

