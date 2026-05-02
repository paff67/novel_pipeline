# Style Bible Section Completeness Control Plane 与 Embedding 接入可行性报告

日期：2026-04-17  
代码分支：`codex/gpt_ceshi`

## 1. 背景结论

结合最新 mini live 测试报告和当前本地代码，项目现在的判断很明确：

1. 主链路不是“跑不通”，而是“质量补齐还没完成”。
2. 目前最高优先级仍然是 `section_completeness`，不是继续折腾 extract/canon。
3. 当前 fail 的根因主要是：
   - 必需 scalar 缺失：`narrative_system.perspective`、`narrative_system.distance`、`narrative_system.temporality`、`voice_contract.narrator_voice`、`voice_contract.inner_monologue_mode`
   - 关键 list section 条数不足
   - 次一级问题是 `routing_hints` 与 `worldbook_binding` 仍偏薄
4. embedding 不是当前 fail 的直接解法，最多只能作为第二阶段的增强器，而不是第一阶段的主修复手段。

## 2. 本次代码改动摘要

本轮已经完成 `section_completeness` 所需的 control plane 改造，核心目标是：让 prompt / reducer / repair 流程围绕“缺什么 section，就定向补什么 section”运行。

### 2.1 新增 section target 控制面

新增文件：

- `config/style_bible_section_targets.toml`
- `src/novel_pipeline_stable/style_bible_section_targets.py`

实现内容：

1. 用 TOML 明确 bucket 到 section 的映射关系。
2. 把 full eval contract 里的 `required_scalars` 和 `minimums` 读进来，作为 repair 的目标来源。
3. 为每个 bucket 定义：
   - `preferred_paths`
   - `scalar_paths`
   - `prompt_hints`
   - `repair_priority`

效果：

- reducer 不再只做“合并已有结果”，而是有了一个明确的补齐目标面板。
- 缺失 section 会被翻译成 bucket-scoped repair 请求，而不是靠 prompt 自发发挥。

### 2.2 扩展 local reducer prompt payload

修改文件：

- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`
- `prompts/style_bible_local_reduce.md`

实现内容：

1. 在 `static_context` 中注入 `section_targets`
2. 在 `dynamic_context` 中注入 `repair_request`
3. 对以下 repair 信息做了规范化：
   - `requested_paths`
   - `missing_scalar_paths`
   - `underfilled_paths`
   - `existing_rows`
   - `target_scalar_candidates`
   - `enum_hints`

效果：

- prompt 终于知道“这轮不是普通 local reduce，而是 repair”
- scalar / voice_contract 不再完全依赖模型自发猜到
- 现有 rows 变成 dedupe hint，而不是让模型重写整个 bucket

### 2.3 在 reducer 中加入 repair pass

修改文件：

- `src/novel_pipeline_stable/style_bible_reducer.py`

实现内容：

1. 引入全局 section gap 计算：
   - `_compute_section_gaps(...)`
2. 引入 bucket 级 repair 请求选择：
   - `_select_repair_requests(...)`
3. 引入定向 repair 执行与回灌：
   - `_run_section_repair_passes(...)`
   - `_merge_local_artifact_with_repair(...)`
4. 把 repair 元数据纳入 artifact / metrics / reduce trace：
   - `repair_pass_count`
   - `repair_used_bucket_count`
   - `repair_passes`

效果：

- 初始 local reduce 稀疏时，系统现在可以再打一轮“定向补齐”
- repair 产物会合并回 bucket artifact，而不是另起炉灶
- 这一步正面对齐了报告里的 `section_completeness` fail

### 2.4 引入 mini / full 双评估 profile

新增文件：

- `config/style_bible_eval_rules_mini.toml`
- `tests/test_style_bible_eval_profiles.py`

实现内容：

1. full profile 继续作为正式 contract
2. mini profile 降低 list minimums 和覆盖要求，用于 mini live 代表集

效果：

- 评估标准更清晰：mini 用 mini，full 用 full
- `section_completeness` 的修复现在有了更合理的对照组

### 2.5 补充测试护栏

修改 / 新增测试：

- `tests/test_style_bible_hierarchical_reducer.py`
- `tests/test_style_bible_v2_schema_contracts.py`
- `tests/test_style_bible_eval_profiles.py`

新增覆盖点：

1. prompt payload 中存在 `section_targets` 和 `repair_request`
2. sparse bucket 仍会被标记 sparse，但允许其他 bucket 做 targeted repair
3. repair pass 可以把 scalar 和 list section 合并回最终 artifact
4. mini profile 与 full profile 的 `section_completeness` 判定差异可被稳定验证

## 3. 当前项目进度判断

### 3.1 已完成

1. `extract-style` 的真实上游基线已经存在
2. `canon -> style_bible -> mini3 reduce -> evaluate -> judge` 的 continuation run 已经打通过
3. `section_completeness` 的 control plane 已经在本地代码层落地
4. 本地单测已通过：
   - `python -m unittest discover -s tests -q`
   - 结果：`58 tests OK`

### 3.2 尚未完成

1. 还没有基于这次新代码，重新跑真实 mini3 / full live evaluation
2. 所以最新报告里的 fail 仍然是“当前线上事实”
3. 我们现在只能说：
   - 代码已经针对根因修复
   - 真实运行层面的效果还需要 rerun 验证

### 3.3 对项目阶段的客观判断

当前项目处于：

**“链路打通后，进入质量控制与 contract 对齐阶段”**

这意味着：

1. 不是基础架构阶段
2. 不是语义检索 / embedding 重构阶段
3. 是最应该做“控制面修复 + 定向回归 + 质量收敛”的阶段

## 4. 下一步工作建议

建议按下面顺序推进。

### P0：立即执行

1. 用当前代码重新跑一次 mini3 continuation evaluate
2. 对比修复前后的 `section_completeness` 子项差异
3. 重点确认：
   - 5 个必需 scalar 是否开始稳定出现
   - `voice_contract` 是否稳定出现
   - `expression_system.*`、`worldbook_binding.*`、`negative_rules` 是否达到最小条数

这是现在最关键的一步，因为它决定这轮 control plane 是“真正修复”还是“只在单测里成立”。

### P1：如果 rerun 后仍有缺口

继续调这三处，但仍然不要回头重构 extract/canon：

1. `style_bible_section_targets.toml`
   - 调 bucket 到 section 的映射
   - 调 `repair_priority`
   - 调 `prompt_hints`
2. `style_bible_local_reduce.md`
   - 收紧 repair 指令
   - 强化 scalar / voice_contract 的枚举式输出要求
3. `style_bible_reducer.py`
   - 收紧 repair request 选择逻辑
   - 调整每轮 repair 的 bucket/path 上限

### P2：在 section_completeness 基本通过后

再回头处理质量薄弱区：

1. `routing_hints` 有用性
2. `worldbook_binding.rag_worthy` 不为 0
3. `worldbook_binding.worldbook_worthy` 的触发精度

### P3：最后再恢复 full-bucket 主线

只有在 mini3 已经稳定通过或接近通过时，才建议继续大推 full-bucket。  
原因很简单：否则你会把“质量缺口”扩散到更大的运行面上，调试成本会急剧上升。

## 5. 对 embedding 的总判断

### 5.1 现在要不要立刻引入？

不建议把 embedding 当作当前阶段的主修复项立刻接入到主链路。

原因：

1. 当前 fail 是 contract completeness 问题，不是语义相似度问题
2. embedding 解决不了“缺 scalar / 缺 voice_contract / list 条数不够”这种硬缺口
3. 现在就把 embedding 塞进 merge / route / judge 的关键路径，会降低可解释性，增加调参噪音

### 5.2 现在引入有没有优势？

有，但只限于“旁路观测”或“低风险增强”。

最现实的优势只有两个：

1. 给 reducer 做柔性去重的候选打分
2. 提前积累相似度阈值和误判样本，为第二阶段做数据准备

所以更准确的结论是：

**可以开始设计 embedding 接口，但不建议现在把 embedding 变成主链路决策器。**

## 6. 四个 embedding 接入方案可行性评估

### 方案 1：Reducer Deduplication

定义：在 reducer / assembler 合并阶段，对候选规则做 embedding，相似度高时柔性去重。

结论：**建议采纳，但放在 `section_completeness` 验证通过之后，以 shadow mode 先接。**

可行性：高  
预期收益：高  
风险：低到中

为什么值得做：

1. 这是最贴近你之前“三条建议”里第 1 条的真实落点
2. 它不改变 extract/canon，也不重写路由主逻辑
3. 只要限制条件足够严，就能明显减少 rule_list 膨胀

建议接入方式：

1. 只在同一 `surface_path` 内比较
2. 必须同时满足：
   - cosine similarity 高于阈值，例如 `> 0.92`
   - `evidence_refs` 有交集
   - `_reasoning_ref` 或 bucket 来源高度接近
3. 第一阶段只做 `candidate_merge_log`
4. 第二阶段再打开 `soft_merge`
5. 不要一开始就做 hard merge

是否现在就做：否  
建议阶段：`Phase 2`

### 方案 2：Semantic Batch Affinity

定义：按语义相似度组织 batch，让 local reduce 看到更一致的上下文。

结论：**可以采纳，但优先级低于方案 1，应该放在 section repair 稳定之后。**

可行性：中  
预期收益：中  
风险：中

优点：

1. 有机会提升 local reduce 的一致性
2. 能减少 batch 内语义跨度过大导致的 prompt 漂移

主要问题：

1. 现在你最核心的问题不是“batch 太散”，而是“目标 section 没被稳定产出”
2. 语义装箱一旦做重，会影响现有 planner 的稳定性和可复现性
3. 需要 embedding 缓存、聚类策略和 batch A/B 评估面板

建议接入方式：

1. 先做 offline 实验，不进主链路
2. 不要一上来就 K-Means 强聚类
3. 先做“同 bucket 内候选 rerank”，而不是全局重排

是否现在就做：否  
建议阶段：`Phase 3`

### 方案 3：Hybrid Routing

定义：在 router 里把 keyword / feature score 和 embedding score 做混合打分。

结论：**暂不建议采纳到主链路。**

可行性：中  
预期收益：中到高  
风险：高

为什么要谨慎：

1. routing 是上游关键控制点
2. 一旦 embedding score 参与主打分，错误会向下游全部扩散
3. 当前没有足够强的离线 gold set 去证明“embedding routing 比现有 routing 更好”

如果未来要做：

1. 只能做 capped bonus，不要替代 keyword/feature
2. 必须设置低权重和置信度阈值
3. 必须先有 offline routing benchmark

是否现在就做：否  
建议阶段：`Phase 4`

### 方案 4：Evaluator / Judge 幻觉校验池

定义：用 claim embedding 去检索源文本，如果 top-k 相似度持续很低，则判为 hallucination 候选。

结论：**建议采纳，但作为 warning-only 的防御层，晚于方案 1。**

可行性：中到高  
预期收益：中  
风险：低到中

为什么值得做：

1. 它不直接改写产出，而是做防御
2. 更适合作为 evaluator / judge 的附加证据
3. 对“规则是不是原文真有支撑”这类判断很有帮助

前提条件：

1. 要先把 source corpus 的切片、索引和 provenance contract 固定下来
2. 要定义 claim 到 source span 的检索粒度
3. 第一阶段只能 warning，不要直接 hard drop

是否现在就做：否  
建议阶段：`Phase 3.5`

## 7. 对你前面三条 embedding 建议的审核结论

### 建议 1：Assembler 合并不足

结论：**原则上采纳，但不在当前阶段直接上主链路 hard merge。**

原因：

1. 方向对
2. 风险低
3. 最适合作为第一批 embedding 试点

建议落点：

- 放在 reducer / assembler 的 dedupe shadow pass

### 建议 2：盘活 `worldbook_binding.rag_worthy`

结论：**方向对，但阶段太靠后。**

原因：

1. 这是下游应用层能力，不是当前评估 fail 的直接修复项
2. 现在 `rag_worthy` 本身还不稳定，先建向量库意义有限

建议落点：

- 等 `rag_worthy` 至少能稳定非 0，并且 `query_feature_matcher` 质量收敛后再接

### 建议 3：冲突发现

结论：**可以做，但先做 warning-only，不建议现在做自动 drop。**

原因：

1. 自然语言“相近”不等于规则“冲突”
2. 没有稳定 action taxonomy 时，很容易误判

建议落点：

- 先做 `assembler_conflicts_suspected`
- 人工 review 一段时间后再决定是否自动化

## 8. 推荐落地路线图

### 阶段 1：当前必须完成

1. rerun mini3 real evaluation
2. 验证 `section_completeness` 改善幅度
3. 继续调 `section_targets + prompt + repair`

### 阶段 2：低风险 embedding 试点

1. reducer dedupe shadow mode
2. 输出 similarity log
3. 人工看误判率

### 阶段 3：质量增强

1. semantic batch affinity offline A/B
2. evaluator/judge retrieval warning
3. `rag_worthy` 质量稳定后再做 vector DB 接入

### 阶段 4：高风险结构增强

1. hybrid routing offline benchmark
2. 通过 benchmark 后，再考虑小权重上线

## 9. 最终建议

一句话总结：

**现在最应该做的是用新 control plane 重新跑 mini3 / evaluation，先把 `section_completeness` 从根因上验证掉；embedding 建议只采纳方案 1 的 shadow mode 设计，不建议马上把 embedding 推进到 router 或主链路决策层。**

