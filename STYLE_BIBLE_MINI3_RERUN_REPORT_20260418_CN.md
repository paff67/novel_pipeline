# Style Bible Mini3 重跑评估运行报告
日期：2026-04-18  
代码分支：`codex/gpt_ceshi`  
报告范围：基于当前工作区代码版本，重跑 `mini3` 产物并执行 `full` / `mini` 双评估，记录运行策略、结果变化、阻塞项与下一步建议。

## 1. 本次运行结论

本次重跑已经验证：当前这版代码对 `section_completeness` 的主问题修复是有效的，尤其是 `scalar`、`voice_contract` 和若干关键 list section 的定向补齐已经跑通。

结论可以概括为四点：

1. `mini` 评估配置下，`section_completeness` 已经从 fail 修到 pass，说明 control plane + prompt payload + repair pass 的主链路已经生效。
2. `full` 评估配置下，总分从上一版的 `76.64` 提升到 `82.98`，提升 `+6.34` 分，但仍然因为 `section_completeness` 和 `routing_hints` 未过而整体 `fail`。
3. 当前最大剩余问题已经不再是“缺 scalar”，而是“full contract 要求的 list 深度不够”，以及 `routing_hints` 句式仍然过于模糊。
4. 这次失败的主矛盾已经从“抽不出来”转成“抽得出来，但还不够厚、不够可路由”，这说明项目已经进入结构补厚阶段，而不是基础修复阶段。

## 2. 运行目标与产物

本次目标：

1. 用当前代码版本重新生成 `mini3` 最终 Style Bible。
2. 分别使用 `full` 与 `mini` 两套评估配置执行 `evaluate`。
3. 输出一份可归档的运行结果报告，明确当前进度和下一步优先级。

主要产物目录：

1. 最终运行根目录：`C:\sbtests\m3fix_20260418T013131`
2. 最终 mini3 产物：`C:\sbtests\m3fix_20260418T013131\mini3`
3. full 评估结果：`C:\sbtests\m3fix_20260418T013131\eval_full\style_eval_report.json`
4. mini 评估结果：`C:\sbtests\m3fix_20260418T013131\eval_mini\style_eval_report.json`
5. 重跑摘要：`C:\sbtests\m3fix_20260418T013131\rerun_summary.json`

使用的评估配置：

1. full profile：`D:\card\novel_pipeline\config\style_bible_eval_rules.toml`
2. mini profile：`D:\card\novel_pipeline\config\style_bible_eval_rules_mini.toml`

## 3. 为什么原始路径重跑会卡住

这次重跑过程中，直接沿用长路径输出根目录会在 repair 阶段表现出明显异常。根因不是 reducer 逻辑本身崩掉，而是 Windows 路径长度把复制后的本地请求缓存路径顶到了边界，导致缓存文件在新目录下名义上存在、实际上不可用，进而触发缓存失效并退回实时模型调用，造成运行时间和稳定性急剧恶化。

本地复核到的关键现象如下：

1. 原始可用缓存路径示例：
   - `institutional_pipeline` 请求缓存路径长度 `254`，`exists=True`
   - `resource_pressure` 请求缓存路径长度 `249`，`exists=True`
2. 重试目录中的复制后路径示例：
   - `institutional_pipeline` 请求缓存路径长度 `260`，`exists=False`
   - `resource_pressure` 请求缓存路径长度 `255`，`exists=True`
3. 这说明问题不是“所有缓存都坏了”，而是“部分关键缓存跨过 Windows 边界后失效”，从而让 repair pass 不再走本地缓存，而被迫重打模型。

因此，这次成功运行采用了短路径策略：

1. 输出根目录改为：`C:\sbtests\m3fix_20260418T013131`
2. repair 临时目录改为：`C:\sbtmp\m3fx_20260418T013131\...`
3. 从已经完成的 base local reduce 产物继续，而不是重新整轮跑 extract / batch / reduce

这一步是本次运行能稳定落地的关键。

## 4. 实际采用的重跑策略

本次没有回头折腾 extract/canon，而是直接围绕 `section_completeness` 做“短路径 + 定向 repair”的最小闭环：

1. 基础 local reduce 产物来自已有目录：
   `C:\sbtests\20260417T193048_styleextract_minilive_cn_gpt54_realextract_continue\style_bible_mini3_rerun_sectionfix_20260418T001007\_local_reduce`
2. 在当前代码版本下装载已有 bucket 结果。
3. 不做大而全 repair，而是拆成 4 次小范围 targeted repair。
4. repair 后重新组装 final style bible，再跑 full / mini 两套 evaluate。

本次定向 repair 覆盖路径如下。

第 1 轮，`institutional_pipeline`：

1. `narrative_system.perspective`
2. `narrative_system.temporality`
3. `voice_contract.narrator_voice`
4. `aesthetics_system.core_axes`

第 2 轮，`resource_pressure`：

1. `narrative_system.distance`
2. `voice_contract.inner_monologue_mode`
3. `aesthetics_system.pressure_axes`
4. `character_arc_rules`

第 3 轮，`resource_pressure`：

1. `expression_system.sensory_rules`
2. `worldbook_binding.rag_worthy`

第 4 轮，`institutional_pipeline`：

1. `aesthetics_system.nonstandard_xianxia_rules`

这 4 轮 repair 正好对应此前最影响 `section_completeness` 的缺口字段。

## 5. 运行结果

### 5.1 分数变化

| 评估对象 | 配置 | 总分 | 状态 | 说明 |
| --- | --- | ---: | --- | --- |
| 上一版基线 | mini3 旧评估 | 76.64 | fail | 主要卡在 `section_completeness` |
| 本次重跑 | full profile | 82.98 | fail | 分数已过 80，但质量门仍未通过 |
| 本次重跑 | mini profile | 95.48 | fail | 只剩 `routing_hints` 未过 |

本次相对上一版基线，full profile 总分提升了 `+6.34` 分。

### 5.2 关键正向结果

1. `section_completeness`
   - mini profile：`pass`
   - full profile：不再缺失 scalar，但仍因 list 条数不足而 `fail`
2. `schema_validity`：`pass`
3. `bundle_coverage`：`pass`
4. `grounding_trace_integrity`：`pass`
5. `required_axis_coverage`：`pass`
6. `supporting_evidence`：`pass`
7. `actionability`：`pass`
8. `worldbook_binding`
   - mini profile：`pass`
   - full profile：`warn`

这说明当前版本的核心增益是：

1. 证据链没有被 repair 打坏。
2. scalar / voice_contract 已经能被稳定补齐。
3. mini contract 下的最小 section 覆盖已经能稳定达标。

### 5.3 关键运行指标

1. repair pass 次数：`4`
2. 使用 repair 的 bucket 数：`2`
3. 最终 supporting evidence 数：`14`
4. 最终 reasoning entry 数：`15`
5. grounding check 中 final rule 数：`26`
6. 总耗时：`5343.322` 秒，约 `89.1` 分钟
7. 总 token：`493,841`
8. 平均 TTFT：`304.935` 秒

## 6. 当前还没过的地方

### 6.1 full profile 仍然卡在 section_completeness

这是当前最重要的残余问题。它已经不是“字段缺失”，而是“full profile 的最小条数要求更高，而目前很多 list 只有 1 条”。

full profile 下仍然 underfilled 的字段如下：

1. `narrative_system.engine`：`1 / 3`
2. `narrative_system.pacing_rules`：`1 / 4`
3. `narrative_system.plot_node_logic`：`1 / 3`
4. `expression_system.description_rules`：`1 / 4`
5. `expression_system.dialogue_rules`：`1 / 4`
6. `expression_system.characterization_rules`：`1 / 4`
7. `expression_system.sensory_rules`：`1 / 4`
8. `aesthetics_system.core_axes`：`1 / 5`
9. `aesthetics_system.pressure_axes`：`1 / 5`
10. `aesthetics_system.humor_recipe`：`1 / 4`
11. `aesthetics_system.satire_targets`：`1 / 4`
12. `aesthetics_system.nonstandard_xianxia_rules`：`1 / 4`

所以 full profile 的剩余工作非常清晰：不是继续补 scalar，而是把这些 section 从“有一条”扩成“达到 contract 要求的最小厚度”。

### 6.2 routing_hints 是 mini / full 共同阻塞项

`routing_hints` 在 full 和 mini 两套 profile 下都失败，且 `useful_routing_hint_ratio = 0.0`，这是目前最清晰的下一个修复目标。

问题不在数量，而在句式和结构：

1. 当前有 `3` 条 routing hint。
2. 但 `3` 条都被判定为弱提示。
3. 评估器给出的核心意见是：现在写法更像“主题提醒”或“检索建议”，而不是“可执行的路由条件 + 明确的目标节点”。

也就是说，当前版本虽然已经把 `routing_hints` 产出来了，但还没有把它们写成真正可供下游分流使用的控制信号。

### 6.3 anti_pattern_resistance 仍有警告

当前仍有两个明显的弱点：

1. `VAGUE_ROUTING`
   - 3/3 命中，全部来自 `worldbook_binding.routing_hints`
2. `KEYWORD_STUFFING`
   - supporting evidence 中仍有少量“把过多线索堆进单条 claim”的表达

这说明后续在修 `routing_hints` 的同时，最好一起收紧 supporting evidence 的句式密度，避免为了补证据而写成大段堆词。

## 7. 这次重跑证明了什么

从项目阶段判断上看，这次运行已经给出比较清楚的信号：

1. 当前代码版本的 `section_targets + prompt payload + repair pass + mini/full eval profile` 是有效的。
2. 当前项目已经走出“基础结构不稳定”的阶段，进入“下游可用性与内容厚度提升”的阶段。
3. 当前最值得继续投入的方向，不是再回头改 extract/canon，也不是马上引入 embedding，而是把 control plane 继续往前推进到两件事：
   - 让 `routing_hints` 变成真正可路由的结构化提示
   - 让 full profile 要求的关键 rule list 达到最小条数

换句话说，这次重跑已经证明：`section_completeness` 的修复方向是对的，但它目前只完成了“补齐最低骨架”，还没有完成“补厚成正式 contract”。

## 8. 下一步建议

建议按下面的顺序继续推进。

### 优先级 P0：修 routing_hints

目标不是“再多写几条主题句”，而是强制输出成标准句式：

1. 触发条件：用户问题里出现什么组合特征
2. 路由目标：命中的节点、section、bucket 或知识簇是什么
3. 目标动作：优先召回什么证据或规则

如果这一步修好，mini profile 很可能直接转绿，且也会同步改善 `anti_pattern_resistance`。

### 优先级 P1：把 full profile 的 list section 补到最小厚度

建议优先扩这几组：

1. `aesthetics_system.core_axes`
2. `aesthetics_system.pressure_axes`
3. `aesthetics_system.nonstandard_xianxia_rules`
4. `narrative_system.pacing_rules`
5. `expression_system.description_rules`
6. `expression_system.dialogue_rules`
7. `expression_system.characterization_rules`
8. `expression_system.sensory_rules`

原因很简单：这些 section 同时影响 `section_completeness`、下游可用性和风格可解释性。

### 优先级 P2：清理 keyword stuffing

这不是当前主阻塞，但最好在下一轮 repair / reducer merge 时顺手做掉：

1. 限制单条 supporting evidence claim 的信息密度
2. 避免把多个 scene 和多个结论硬塞进一条 claim
3. 让 claim 更接近“单条判断 + 直接证据”的结构

### 关于 embedding 的阶段判断

结合这次重跑结果，embedding 依然不是当前最高优先级。

原因是：

1. 当前 fail 的直接原因已经非常明确，且都是 control plane 可以继续解决的问题。
2. `routing_hints`、full list depth、evidence 句式收紧，都不依赖 embedding 才能做出可见收益。
3. 如果现在就把 embedding 接到 reducer dedupe / batching / routing / evaluator，会引入新的阈值、缓存、索引和离线构建复杂度，但不会直接解决这次评估里的主 fail。

因此更合理的阶段判断是：

1. 先把 `routing_hints` 和 full `section_completeness` 做到稳定通过。
2. 再把 embedding 作为第二阶段增强，优先切入 `Reducer Deduplication`，而不是现在就做大面积系统性接入。

## 9. 最终判断

如果用一句话总结本次运行结果，那就是：

当前版本已经把 `section_completeness` 从“核心失败原因”推进成“只剩 full contract 厚度不足”，这是明确进展；项目下一步不该回头修 extract/canon，也不该提前把精力转去 embedding，而应该先把 `routing_hints` 和 full list depth 两个收口点做穿。

从工程节奏上看，这是一次有效重跑，且结果足以支持继续沿着当前 control plane 方向推进。

