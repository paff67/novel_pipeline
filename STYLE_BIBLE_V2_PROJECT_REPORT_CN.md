# Style Bible V2 项目架构报告

## 1. 项目目标

`Style Bible v2` 的目标不是再做一次“大样本文风摘要”，而是把整套风格蒸馏流程改造成一个：

1. 可路由
2. 可分桶
3. 可批处理
4. 可续跑
5. 可审计
6. 可评测
7. 可作为 Style-RAG 上游资产层

的多阶段流水线。

截至 2026-04-18，本项目有一个关键定位更新：

1. `Style Bible v2` 继续作为主线系统保留。
2. 但它的职责被正式限定为“文风与叙事法则控制平面”。
3. 它不再承担未来世界知识主检索层的职责。

换句话说，`Style Bible v2` 将成为 `Hybrid RAG` 里的 Style-RAG 上游，而不是未来一切检索的总入口。

## 2. 当前全链路总览

当前主入口仍在：

1. `src/novel_pipeline_stable/style_bible_builder.py`
2. `src/novel_pipeline_stable/cli.py`

现有链路可概括为：

1. 读取 `facts/style/canon`
2. 生成 `style_bible_source_bundle.json`
3. 路由全量 scene / style_window
4. 规划 batch
5. 生成 bucket memos
6. reducer 生成：
   - `style_bible_reasoning.json`
   - `style_bible_reduce_trace.json`
   - `style_bible_final.json`
   - `style_bible_export_flat.json`
7. 用 Eval / Judge 做质量闸门

这条链路现在已经具备工程流水线形态，而不是概念验证。

## 3. 当前模块分工

### 3.1 Orchestrator

文件：`src/novel_pipeline_stable/style_bible_builder.py`

职责：

1. 串联 build 主流程
2. 写 `manifest.json`、`failures.json`、`run_manifest.json`
3. 汇总 `request_metrics` 与 `usage_metadata`
4. 支持 `--resume`

### 3.2 Router

文件：`src/novel_pipeline_stable/style_bible_router.py`

职责：

1. 将全量 `scene/style_window` 路由为 `StyleBibleRoutedItem`
2. 计算轴线分数与 bucket membership
3. 生成 coverage 报告

现实定位：

1. Router 负责风格语料组织
2. 它不是未来世界知识查询层

### 3.3 Batch Planner

文件：`src/novel_pipeline_stable/style_bible_batching.py`

职责：

1. 基于 routed index 规划 batch
2. 平衡 scene / style_window 装箱
3. 输出 `batch_plan.json` 与 `planner_debug_report.json`

### 3.4 Prompt Assembler

文件：`src/novel_pipeline_stable/style_bible_prompt_assembler.py`

职责：

1. 组装 bucket synthesis prompt
2. 组装 reducer prompt
3. 注入 anti-pattern
4. 注入 `section_targets` 与 `repair_request`

### 3.5 Reducer

文件：`src/novel_pipeline_stable/style_bible_reducer.py`

职责：

1. 合并 bucket memos
2. 构造 grounded final
3. 维护 reasoning / trace / evidence lineage
4. 执行 surface-level rule merge
5. 执行 repair 后的二次装配

当前判断：

1. reducer 已经是最适合接入 Style-RAG Embedding 增强去重的位置
2. 当前 rule merge 仍以 `_normalize_text_key()` 为主

### 3.6 Eval / Judge

文件：

1. `src/novel_pipeline_stable/style_bible_evaluator.py`
2. `src/novel_pipeline_stable/style_bible_judge.py`

职责：

1. 代码级完整性和硬门校验
2. 语义裁判
3. 检查 `section_completeness`
4. 检查 `routing_hints`
5. 检查 `worldbook_binding`
6. 检查 `anti_pattern_resistance`

这套评估链路是当前 Style-RAG 路线的重要基础，因为它提供了“检索可用性”而不是仅仅“结构合法性”的判断。

## 4. 最新阶段判断

基于最新 `mini3` 重跑结果，当前项目状态应当这样描述：

1. 当前主矛盾不是“生成链路没打通”
2. 当前主矛盾是“输出已经能产出，但还不够厚、不够稳、不够可路由”

最新信号如下：

1. full profile：`82.98`
2. mini profile：`95.48`
3. mini profile 下 `section_completeness` 已通过
4. 共同阻塞项仍然是 `routing_hints`
5. full profile 仍需补足多个关键 list section 的最小条数

这说明：

1. 当前 `Style Bible v2` 已经具备进入 Style-RAG 阶段的基础
2. 但必须先把 `routing_hints` 和 full list depth 做稳

## 5. 当前系统在 Hybrid RAG 中的正式角色

在新的总体架构里，`Style Bible v2` 的正式角色应固定为：

1. 风格规则蒸馏器
2. 叙事法则控制平面
3. Style-RAG 的母体资产层
4. 风格侧 routing hints 的生成层

它不再负责：

1. 世界设定主库
2. 实体关系网络
3. 世界事实检索主路径
4. 图谱型 worldbook 导出主路径

这一定义很重要，因为当前 schema 里的：

1. `worldbook_binding.rag_worthy`
2. `worldbook_binding.worldbook_worthy`
3. `worldbook_binding.routing_hints`

仍然保留，但它们的职责应收缩为：

1. 风格侧桥接字段
2. Style-RAG 召回提示
3. 世界图层未落地前的过渡接口

而不是继续承担未来世界知识检索的全部责任。

## 6. 为什么不能继续把世界知识塞进 Style Bible

原因不是“字段不够”，而是知识结构根本不同。

`Style Bible` 更适合承载：

1. 叙事写法
2. 表达约束
3. voice contract
4. negative rules
5. 风格路由条件

世界知识系统更需要承载：

1. 实体节点
2. 关系边
3. 事件时序
4. 机构规则链
5. 章节 / 节点 scope 下的事实状态

如果继续混在一起，会带来三个问题：

1. 风格检索和事实检索互相污染
2. `worldbook_binding` 字段继续膨胀
3. 评估器会持续把“风格桥接字段”误当成“权威世界知识层”来纠结

因此，项目后续应明确拆成：

1. `Style Bible -> Style-RAG`
2. `Canon/Facts -> World GraphRAG`

## 7. 对当前代码的直接启示

### 7.1 当前最适合保留并增强的部分

1. `style_bible_reducer.py`
   - 作为语义去重和 Style-RAG 导出插入点
2. `style_bible_evaluator.py`
   - 继续作为 Style-RAG 质量门
3. `style_bible_judge.py`
   - 继续负责风格语义质量判定
4. `section_targets + repair pass`
   - 继续用于把 Style Bible 输出补齐到可检索状态

### 7.2 当前最不该继续膨胀的部分

1. `worldbook_binding`
   - 不应继续承担世界知识主库
2. `routing_hints`
   - 不应写成抽象主题句
   - 应改成可执行路由条件

## 8. 下一步优先级

建议按以下顺序推进：

1. 修 `routing_hints`
2. 补 full profile 的 list section 厚度
3. 给 reducer 接入 Embedding 增强去重
4. 导出 Style-RAG 资产
5. 在 canon 层补关系图原材料
6. 启动 GraphRAG 构建

## 9. 一句话总结

当前 `Style Bible v2` 已经是一条可运行、可评估、可续跑的风格蒸馏流水线；它接下来的正确演进方向不是继续包办一切检索，而是收缩职责、做强 Style-RAG，并把世界设定检索正式让渡给后续的 GraphRAG。

