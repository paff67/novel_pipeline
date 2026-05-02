# 从小说蒸馏到 RP / Hybrid RAG / 世界书 / 路由的完整蓝图

## 1. 文档目的

这份文档用于统一说明本项目的完整目标、当前 `novel_pipeline_stable` 所处阶段，以及从当前蒸馏系统走向运行时检索系统的正式路线。

截至 2026-04-18，这份蓝图有一个重要更新：

1. 后续检索层不再采用“风格规则 + 世界设定一起塞进单一路径 RAG”的旧思路。
2. 正式采用 Hybrid RAG 路线：
   - 文风与叙事法则：`Style Bible + Embedding` 增强
   - 世界观设定与实体网络：从 `facts/canon` 侧剥离出来，重构为 `GraphRAG`

这意味着：

1. `Style Bible` 继续做强，但职责边界收缩到“怎么写、怎么路由、怎么约束”。
2. 世界知识、实体关系、机构规则、节点事实，不再继续压进 `worldbook_binding` 这类 style-side 字段。

## 1.1 当前代码状态补充（2026-04-18）

当前正式主线仍然是：

1. `novel_pipeline_stable`
2. 正式全量入口脚本：
   - `D:\card\novel_pipeline\scripts\launch_fact_style_full_formal_cn_gpt54_stable.ps1`
3. 节点版入口脚本：
   - `D:\card\novel_pipeline\scripts\run_story_node_pipeline_formal_cn_gpt54_stable.ps1`

当前已经落地的主链路包括：

1. `fact` 抽取
2. `style` 抽取
3. `build-canon`
4. `build-style-bible`
5. `evaluate-style-bible`
6. `judge-style-bible`

最新 `mini3` 重跑结果表明：

1. `mini profile` 下 `section_completeness` 已经通过
2. `full profile` 仍然主要卡在：
   - `section_completeness`
   - `routing_hints`
3. 当前项目的主问题已经不是“链路没跑通”，而是“风格输出还不够厚、路由提示还不够可执行”

> 标记约定：本文件不再保留旧的一体化 RAG 未来项。已被新架构替代的规划，直接从主蓝图中删除，不再继续作为当前路线。

## 1.2 首轮实施状态补充（2026-04-19）

当前路线已经从“只做调研和架构论证”进入“开始按优先级实施”的阶段。

本轮已经正式落地并纳入主蓝图的首批事项是：

1. 新增首轮施工清单：
   - `HYBRID_RAG_IMPLEMENTATION_CHECKLIST_20260419_CN.md`
2. 新增 `World Graph -> GraphRAG BYOG` 导出层：
   - 模块：`src/novel_pipeline_stable/world_graph_graphrag_export.py`
   - CLI：`export-world-graph-graphrag`
3. 新增 `Style Bible` 的 `Ragas-ready` 离线评估 runner：
   - 模块：`src/novel_pipeline_stable/style_bible_ragas_eval.py`
   - CLI：`evaluate-style-bible-ragas`
4. 新增 `Style + World` 检索契约导出层：
   - 模块：`src/novel_pipeline_stable/hybrid_rag_contract.py`
   - CLI：`build-hybrid-rag-contract`
5. 新增运行时前置的 `HybridRetriever` 离线探针：
   - 模块：`src/novel_pipeline_stable/hybrid_retriever.py`
   - CLI：`probe-hybrid-retriever`

这意味着当前蓝图的第一落地点已经明确：

1. 不先大改 `reducer / control plane`
2. 不先整套接入运行时混合检索
3. 先增强：
   - 世界图资产的可导出性
   - 下游条目的可评估性
   - 文档与命令层的正式入口

---

## 2. 最终目标

本项目的最终目标不是“生成一批 JSON”，而是把整部小说转化成一套可以稳定驱动 RP / 世界书 / 路由 / 剧情审批的结构化资产层。

最终形态应当是：

1. 先维护一份单一权威 `Canon`
2. 再从 `Canon` 派生出两条检索平面
   - `Style-RAG`
   - `World GraphRAG`
3. 最后由中间件把检索结果、规则约束和节点状态接到 RP 运行时

核心原则：

1. 不让 `Style Bible` 同时充当风格手册和世界知识主库
2. 不让世界书成为权威真相源
3. 不让 LLM 自由决定剧情推进
4. 不让“单一向量库”掩盖风格知识和世界知识的结构差异

---

## 3. 完整系统架构

```text
原始小说 TXT
  -> 清洗 / 切章
  -> scene 切分
  -> facts 抽取
  -> style 抽取
  -> Canon 权威设定层
      -> Style Bible v2
          -> Style-RAG Export
              -> 风格规则索引
              -> 叙事法则索引
              -> Embedding 增强检索
      -> World Graph Build
          -> 实体节点
          -> 关系边
          -> 事件子图
          -> 规则节点
          -> 社区摘要
              -> GraphRAG Query
                  -> 世界设定检索
                  -> 实体网络检索
                  -> 世界书导出
  -> Runtime Middleware
      -> Style Retriever
      -> World Graph Retriever
      -> Router
      -> Prompt Assembler
      -> Session State / Plot Approval
  -> RP / SillyTavern / 其他前端
```

建议的职责边界：

1. `Canon`：权威设定层
2. `Style Bible`：风格与叙事法则层
3. `Style-RAG`：写法、语气、约束、风格规则召回
4. `World GraphRAG`：实体、关系、机构、门槛、节点事实召回
5. `Middleware`：路由、混合检索、剧情审批、状态回写
6. `前端`：交互层

---

## 4. 分阶段路线

## 4.1 阶段 A：源数据治理

目标：

1. 把原始 TXT 清洗成稳定章节文件
2. 去广告、去作者话、去重复标题、去活动残留
3. 修复坏章、断章、重复章
4. 保持章节编号与来源映射

这是后续全部蒸馏的前置条件。

## 4.2 阶段 B：权威设定层蒸馏

目标：

1. scene 切分
2. facts 抽取
3. style 抽取
4. canon 聚合
5. review panel 生成

这是当前 `novel_pipeline_stable` 已经跑通的主阶段。

## 4.3 阶段 C：Style Bible v2 与质量闸门

目标：

1. 基于 `facts + style + canon` 做跨章节风格综合
2. 输出真正面向下游的 `Style Bible`
3. 用 `evaluate-style-bible` 和 `judge-style-bible` 做质量闸门

当前已实现，但仍处于“补厚输出、修强路由”的收口阶段。

## 4.4 阶段 D：Hybrid RAG 分层

这一步是本蓝图的最新核心更新。

### D1：Style-RAG

目标：

1. 继续使用 `Style Bible v2` 作为风格规则母体
2. 用 Embedding 增强 reducer 去重与运行时召回
3. 把风格规则、叙事法则、voice contract、negative rules 导出成检索资产

这一步不是重写当前链路，而是增强当前链路。

### D2：World GraphRAG

目标：

1. 从 `facts/canon` 侧剥离世界设定检索
2. 把实体、关系、事件、规则、机构、资格门槛做成图资产
3. 让世界书导出改走图层，而不是继续从 `Style Bible` 直接承担

这一步是结构重构，不是把现有字段“再加一点 Embedding”。

## 4.5 阶段 E：运行时中间件

目标：

1. 根据用户问题判断是风格检索、事实检索还是混合检索
2. 调用：
   - `Style Retriever`
   - `World Graph Retriever`
3. 执行剧情节点审批
4. 组装 prompt
5. 生成回复
6. 回写 session state

## 4.6 阶段 F：前端集成

目标：

1. 同步风格预设
2. 同步世界设定世界书
3. 驱动状态栏与调试面板
4. 对接 SillyTavern 或其他前端

---

## 5. 当前已实现、半实现与未实现

### 5.1 已实现

1. 章节清洗、修章、异常扫描
2. 章节导入
3. scene 切分
4. facts 抽取
5. style 抽取
6. canon 构建
7. story node 候选检测与确认流程
8. `build-style-bible`
9. `evaluate-style-bible`
10. `judge-style-bible`
11. 双线程正式续跑与 watchdog
12. 节点版 `build-canon -> build-style-bible -> evaluate-style-bible` 串联

### 5.2 已实现但仍需继续收口

1. `Style Bible` 的 `section_completeness`
   - mini profile 已通过
   - full profile 仍有 list 厚度不足
2. `routing_hints`
   - 当前仍然是最清晰的共同阻塞项
3. `worldbook_binding`
   - 当前更适合作为 style-side 桥接字段
   - 不应继续膨胀成世界知识主库
4. `World Graph` 离线资产层
   - `build-world-graph` 与 `export-world-graph-graphrag` 已落地
   - 当前仍需继续增强 community summary 与后续查询层
5. `HybridRetriever` 前置骨架
   - `build-hybrid-rag-contract` 与 `probe-hybrid-retriever` 已落地
   - 当前仍未进入正式运行时服务化接入

### 5.3 尚未实现

1. `Style-RAG` 正式导出层
2. reducer 语义去重的 Embedding 增强
3. `GraphRAG` 查询层
4. 从图层导出世界书
5. 运行时 `HybridRetriever` 的正式服务化接入
6. FastAPI 中间件
7. ST 端自动同步世界书与变量

---

## 6. Style Bible 在新蓝图中的位置

在旧叙事里，`Style Bible` 经常被想象成“风格 + 世界书 + RAG + 路由”的一揽子中心层。这个定位现在必须收缩。

在新蓝图里，`Style Bible` 的职责是：

1. 叙事系统规则
2. 表达系统规则
3. 审美系统规则
4. voice contract
5. negative rules
6. 风格侧 routing hints

它不再承担：

1. 权威世界设定主库
2. 实体关系主图
3. 机构审批链事实库
4. 节点级世界状态真相源

也就是说：

1. `Style Bible` 继续做强
2. 但只在“怎么写、怎么约束、怎么路由到风格规则”这个边界内做强

---

## 7. 世界书在新蓝图中的位置

后续世界书导出必须分成两种来源：

1. 风格侧世界书
   - 来自 `Style Bible`
   - 负责语气、口吻、禁则、风格 preset
2. 世界设定侧世界书
   - 来自 `World GraphRAG`
   - 负责人物、组织、门槛、地理、资格、剧情节点状态

这一步能显著降低当前 `worldbook_binding` 的职责污染。

---

## 8. 当前最值得做的事

结合最新代码状态和 `mini3` 重跑结果，当前最值得继续推进的是这 6 项：

1. 修 `routing_hints`
2. 把 full profile 下的关键 list section 补到最小厚度
3. 在 reducer 去重层引入 Embedding 增强
4. 把 `Style Bible` 导出成正式 style retrieval 资产
5. 在 canon 层新增关系边与世界规则导出
6. 启动 `build-world-graph`

建议顺序：

```text
先修 Style Bible 质量门
  -> Style-RAG Embedding 增强
  -> Style Retrieval Export
  -> Canon 关系边导出
  -> Build World Graph
  -> GraphRAG Query
  -> Hybrid Retriever
  -> Runtime Middleware
```

## 8.1 2026-04-19 首轮实施入口

在上述大顺序不变的前提下，首轮已经正式开工的入口是：

1. `build-world-graph`
2. `export-world-graph-graphrag`
3. `build-style-bible`
4. `evaluate-style-bible`
5. `judge-style-bible`
6. `evaluate-style-bible-ragas`
7. `build-hybrid-rag-contract`
8. `probe-hybrid-retriever`

这 8 个命令形成的当前最小闭环是：

```text
Canon
  -> Build World Graph
  -> Export GraphRAG BYOG bundle

Style Bible
  -> Eval
  -> Judge
  -> Ragas-ready offline report

Hybrid Retrieval Scaffold
  -> Build Style/World contract
  -> Probe offline HybridRetriever
```

这一步的意义不是“把 Hybrid RAG 全做完”，而是先把后续最关键的三个中间层建出来：

1. `World Graph` 的 GraphRAG 对接资产层
2. `Style Bible` 的 reference-free 评估增强层
3. `Style Lane + World Lane` 的运行时前置契约与探针层

---

## 9. 一句话结论

当前运行中的 `novel_pipeline_stable` 已经不再只是“抽取器”，而是未来 `Hybrid RAG` 的上游资产工厂。

最重要的方向更新是：

1. 风格规则继续走 `Style Bible + Embedding` 增强
2. 世界设定与实体网络必须从 style-side 字段中剥离，重构成独立 `GraphRAG`

这条路线既贴合当前代码现状，也能避免后续系统继续走向职责混乱的一体化检索。
