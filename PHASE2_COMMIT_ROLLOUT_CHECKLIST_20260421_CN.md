# 第 2 阶段按 Commit 粒度拆分的正式施工清单

日期：2026-04-21

适用仓库：`D:\card\novel_pipeline`

配套正式方案：

- `PHASE2_ARCH_DECONSTRUCTION_AND_DEKEYWORDIZATION_PLAN_20260421_CN.md`

文档定位：本文件不是单独的“去 keyword 化清单”，而是 Phase 2 的完整 commit 级施工清单。它覆盖模型、path 绑定、prompt 合同、prompt 状态机、控制面、去 keyword 化、评估器、可观测性与文档更新。

---

## 0. 总体施工原则

1. 每个 commit 只解决一个主问题。
2. 每个 commit 必须有最小测试或验证动作。
3. 兼容层先落地，再推动主链切换。
4. 去 keyword 化先“集中”和“并行”，后“替换”和“cutover”。
5. 任何 semantic-first 判定在接管业务前，都必须先经过 shadow mode。

---

## Commit 01：引入 typed rule family

### 目标

为不同 surface path 规则建立 path-specific typed row models，结束所有 path 共用一个宽模型的状态。

### 主要改动

- `src/novel_pipeline_stable/models.py`

### 具体动作

- 新增：
  - `ConstraintRuleRow`
  - `RoutingHintRuleRow`
  - `NegativeRuleRow`
  - `ScalarRuleRow`
- 保留 `StyleBibleRuleItem` 作为兼容 DTO。

### 验证

- 新增 `tests/test_style_bible_rule_family_models.py`
- 回归 `tests/test_style_bible_v2_schema_contracts.py`

### 建议 commit message

- `feat(style-bible): add typed rule family models`

---

## Commit 02：将 `surface_path` 绑定到 rule family / row model

### 目标

把 `surface_path -> required_fields` 升级为 `surface_path -> row_model + rule_family + enum_source`。

### 主要改动

- `src/novel_pipeline_stable/style_bible_surface_specs.py`
- `src/novel_pipeline_stable/models.py`

### 具体动作

- 扩展 `SurfacePathSpec`。
- 用 path-specific 模型替代纯 `required_fields` 校验。
- 保留兼容 adapter。

### 验证

- 回归 `tests/test_style_bible_local_reduce_contracts.py`
- 新增 backward-compat payload tests

### 建议 commit message

- `refactor(style-bible): bind surface paths to typed rule models`

---

## Commit 03：为 `LocalRuleRow` 接入 path-specific 校验工厂

### 目标

把 path-specific 绑定真正接进 local reduce 的运行时入口。

### 主要改动

- `src/novel_pipeline_stable/models.py`
- 可能新增 adapter / factory 文件

### 具体动作

- 先根据 `surface_path` 查找 spec。
- 再实例化对应 `row_model`。
- 再回填统一运行时视图。

### 验证

- 回归 `tests/test_style_bible_local_reduce_contracts.py`
- 旧 payload 兼容性验证

### 建议 commit message

- `refactor(style-bible): validate local rows through path-specific factories`

---

## Commit 04：新增 compact schema contract builder

### 目标

让 prompt 合同由模型和控制面自动派生。

### 主要改动

- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`

### 具体动作

- 新增：
  - `build_compact_contract_fragment()`
  - `build_path_contract_fragment()`
  - `build_repair_contract_fragment()`
- 输出最小必要合同：
  - 必填字段
  - alias
  - 枚举候选
  - canonical token
  - 禁止项

### 验证

- 新增 `tests/test_style_bible_prompt_contract_generation.py`

### 建议 commit message

- `feat(prompt): add compact schema contract builder`

---

## Commit 05：让 assembler 按模式注入合同切片

### 目标

让 local reduce / repair-only / densify 获取不同粒度的合同切片，而不是共享一套大而杂的说明。

### 主要改动

- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`

### 具体动作

- local reduce 只注入请求 path 的合同切片。
- repair-only 只注入 repair 目标的窄合同。
- densify 只注入单路径合同。

### 验证

- prompt snapshot tests

### 建议 commit message

- `refactor(prompt): inject mode-specific contract fragments`

---

## Commit 06：瘦身 local reduce prompt，隔离 repair-only 状态机

### 目标

解决首次抽取和 repair-only 定向补齐混写导致的状态机混乱。

### 主要改动

- `prompts/style_bible_local_reduce.md`

### 具体动作

- 将 repair-only 逻辑物理隔离。
- 删除重复 schema 指令。
- 明确 scratchpad 与输出边界。
- 把标量枚举、alias 映射的主体职责交给 runtime 合同。

### 验证

- 回归 `tests/test_style_bible_local_reduce_contracts.py`
- prompt snapshot

### 建议 commit message

- `refactor(prompt): isolate repair-only flow in local reduce prompt`

---

## Commit 07：瘦身 densify prompt，保留单 slot 增量与 burn-down 逻辑

### 目标

让 densify 只承担“针对单路径缺口补增量”的职责。

### 主要改动

- `prompts/style_bible_section_densify.md`

### 具体动作

- 删除重复 schema 描述。
- 保留：
  - 单 slot 增量
  - burned evidence 不可复写
  - slot 核销
  - retrieved reasoning grounding

### 验证

- densify prompt snapshot
- 回归 reducer/densify 合同测试

### 建议 commit message

- `refactor(prompt): slim densify prompt around slot-bounded increments`

---

## Commit 08：审计并规范 section target 的中文语义锚点

### 目标

让 `cue / canonical_description` 成为 slot 的中文 canonical 语义来源。

### 主要改动

- `config/style_bible_section_targets.toml`
- `src/novel_pipeline_stable/style_bible_section_targets.py`
- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`

### 具体动作

- 检查每个 slot 是否具备：
  - `slot_id`
  - 中文 `cue`
  - 中文 `canonical_description`
  - `downstream_shape`
  - `fresh_evidence_required`
- prompt 注入时仅传最小语义锚点集。

### 验证

- section target loader tests
- densify bundle payload snapshot

### 建议 commit message

- `refactor(control-plane): normalize chinese semantic anchors for section targets`

---

## Commit 09：建立统一领域词汇控制面

### 目标

把散落在多个模块中的 keyword / cue / generic pattern 收口到统一配置。

### 主要改动

- 新增 `config/project_domain_vocabulary.toml`
- 新增 `src/novel_pipeline_stable/project_domain_vocabulary.py`

### 纳管内容

- axis vocabulary
- route cues
- generic patterns
- safe cues
- mechanism prototypes
- anti-stuffing vocabulary

### 验证

- 新增 `tests/test_project_domain_vocabulary.py`

### 建议 commit message

- `feat(config): centralize domain vocabulary and semantic prototypes`

---

## Commit 10：router / hybrid retriever 改为读取统一词汇控制面

### 目标

先完成词汇控制面的统一读取，不先改变主行为。

### 主要改动

- `src/novel_pipeline_stable/style_bible_router.py`
- `src/novel_pipeline_stable/hybrid_retriever.py`

### 具体动作

- 用共享 loader 替换内嵌词表常量。
- 保持 lexical route 行为基本不变。
- 增加日志字段：
  - `lexical_prior_score`
  - `matched_vocab_ids`

### 验证

- 回归 `tests/test_style_bible_router_batching_builder_guards.py`
- 回归 `tests/test_hybrid_retriever.py`

### 建议 commit message

- `refactor(router): load lexical priors from shared domain vocabulary`

---

## Commit 11：为 router 引入 semantic-first shadow scoring

### 目标

让 router 从关键词主导，过渡到语义优先、关键词兜底的组合打分。

### 主要改动

- `src/novel_pipeline_stable/style_bible_router.py`

### 具体动作

- 增加：
  - `semantic_axis_score`
  - `feature_score`
  - `lexical_prior_score`
- 输出组合分和 shadow 日志。
- 初期不改变最终路由决策。

### 验证

- router scoring tests
- 代表 bucket smoke

### 建议 commit message

- `feat(router): add semantic-first shadow scoring`

---

## Commit 12：为 reducer / densify 引入 semantic slot alignment 日志

### 目标

让 slot 匹配从 cue overlap 主导，过渡到语义槽位对齐主导。

### 主要改动

- `src/novel_pipeline_stable/style_bible_reducer.py`

### 具体动作

- 显式记录：
  - `semantic_slot_score`
  - `evidence_overlap_score`
  - `cue_score`
  - `combined_score`
- 初期不直接修改硬阈值。

### 验证

- 回归 `tests/test_style_bible_hierarchical_reducer.py`
- mini live 3 bucket

### 建议 commit message

- `feat(reducer): add semantic slot alignment shadow telemetry`

---

## Commit 13：为 evaluator / judge 接入 semantic sidecar

### 目标

保留现有 lexical evaluator，同时引入语义 sidecar 评分。

### 主要改动

- `src/novel_pipeline_stable/style_bible_evaluator.py`
- `src/novel_pipeline_stable/style_bible_judge.py`

### 具体动作

- 新增或补强：
  - `specificity_semantic`
  - `actionability_semantic`
  - `groundedness_semantic`
  - `routing_utility_semantic`
  - `genericness_semantic`
- 不立即替换原 hard gate。

### 验证

- 回归 `tests/test_style_bible_eval_profiles.py`
- 回归 `tests/test_style_bible_judge_scope_aware.py`
- 新增 `tests/test_semantic_shadow_scores.py`

### 建议 commit message

- `feat(eval): add semantic sidecar metrics for evaluator and judge`

---

## Commit 14：升级 `style_bible_ragas_eval.py` 为统一 semantic eval sidecar 报告层

### 目标

让 `style_bible_ragas_eval.py` 从 proxy-only 工具，升级为统一语义评估承载层，但仍不直接接管主裁判职责。

### 主要改动

- `src/novel_pipeline_stable/style_bible_ragas_eval.py`

### 具体动作

- 统一接入语义指标输出。
- 支持后续 provider 扩展。
- 输出 sidecar 报告，作为和现有 evaluator/judge 的对照层。

### 验证

- 回归 `tests/test_style_bible_ragas_eval.py`

### 建议 commit message

- `feat(ragas): promote proxy eval into semantic sidecar report layer`

---

## Commit 15：为 semantic-first 判定链补可观测性与 feature flags

### 目标

在任何 cutover 之前，确保新的语义判定链可解释、可灰度、可回滚。

### 主要改动

- router / reducer / evaluator / judge 相关文件
- 配置文件

### 具体动作

- 统一输出：
  - `semantic_score`
  - `lexical_prior_score`
  - `evidence_overlap_score`
  - `final_decision_source`
- 增加 feature flags。

### 验证

- shadow report smoke
- config regression tests

### 建议 commit message

- `feat(runtime): add feature flags and telemetry for semantic-first decisions`

---

## Commit 16：首轮 selective cutover

### 目标

在 shadow mode 稳定后，只选择一个最成熟的判定链做 semantic-first 切换。

### 主要改动

- 取决于 shadow 验证结果

### 具体动作

- 用 feature flag 控制 cutover。
- 只切一条判定链。
- 保留 lexical fallback。

### 验证

- mini live
- full live
- 人工 spot check

### 建议 commit message

- `feat(runtime): enable first semantic-first cutover behind feature flag`

---

## Commit 17：更新蓝图、README 与 Phase 2 文档

### 目标

把 Phase 2 的结果同步到项目文档，避免代码与文档认知分叉。

### 主要改动

- `README_CN.md`
- 蓝图文档
- Phase 2 报告文档

### 需明确写入的内容

- typed rule family
- `Schema as Code`
- prompt 状态机分层
- section target 中文语义锚点
- 去 keyword 化的真实含义
- semantic sidecar 与 cutover 策略

### 建议 commit message

- `docs: update blueprint and readme for phase2 semantic architecture`

---

## 1. 建议停点

## 停点 A：Commit 01-05 之后

检查点：

- typed rule family 是否稳定
- path-specific 合同是否稳定
- prompt contract builder 是否没有放大噪音

## 停点 B：Commit 06-10 之后

检查点：

- prompt 状态机是否更清晰
- section target 语义锚点是否明显改善
- centralized vocabulary 是否没有引入行为回退

## 停点 C：Commit 11-15 之后

检查点：

- semantic shadow logs 是否可解释
- semantic sidecar 与人工判断是否一致
- 是否具备 cutover 条件

## 停点 D：Commit 16 之后

检查点：

- selective cutover 是否没有明显 live 回退

---

## 2. 这份清单如何体现完整的 Phase 2

这份清单不是把 Phase 2 缩成“去 keyword 化”一件事，而是把完整施工拆成了五条并行但可串行落地的主线：

1. 结构主线：
   - Commit 01-03
   - 解构上帝模型，建立 path-specific typed contract

2. Prompt/Assembler 主线：
   - Commit 04-07
   - 实现 `Schema as Code`，修正 prompt 状态机

3. 控制面主线：
   - Commit 08-09
   - 完成 slot 中文语义锚点与词汇控制面收口

4. 语义重构主线：
   - Commit 10-16
   - 完成去 keyword 化、semantic sidecar、shadow mode、selective cutover

5. 文档与认知同步主线：
   - Commit 17
   - 更新蓝图、README、Phase 2 文档

也就是说，“去 keyword 化”只是第 4 条主线中的一部分，不是 Phase 2 的全部。

---

## 3. 推荐执行顺序

1. Commit 01-05
2. Commit 06-10
3. Commit 11-15
4. Commit 16
5. Commit 17

结论：Phase 2 的成功标志不是“删掉了多少关键词”，而是模型、prompt、控制面、评估器和运行时切换机制都进入了同一套语义重构轨道。

---

## 4. 2026-04-21 完成状态回写

## 4.1 Commit 落地状态

- Commit 01-03：已完成。`models.py` 与 `style_bible_surface_specs.py` 已接入 typed rule family、path-specific row model 绑定与兼容工厂校验。
- Commit 04-07：已完成。`style_bible_prompt_assembler.py` 已提供 compact contract builder，`style_bible_local_reduce.md` 与 `style_bible_section_densify.md` 已按 local reduce / repair-only / densify 分层。
- Commit 08-10：已完成。section target 中文语义锚点已规范化；`project_domain_vocabulary.toml` 与 `project_domain_vocabulary.py` 已接入 router / hybrid retriever 的共享 lexical priors。
- Commit 11-16：已完成。router / reducer / evaluator / judge / ragas 均已输出 semantic sidecar 与 observability 字段；selective cutover 仍由 feature flag 保护，默认保持 shadow-only + lexical fallback。
- Commit 17：已完成。README、README_CN、Phase 2 蓝图与本 checklist 已同步到同一术语与运行口径。

## 4.2 停点复核结果

- Stop A（Commit 01-05）：已完成。合同、typed model、prompt contract 相关回归通过。
- Stop B（Commit 06-10）：已完成。prompt 状态机、section target loader、shared vocabulary 与 router / retriever 回归通过。
- Stop C（Commit 11-15）：已完成。semantic shadow scores、hierarchical reducer、eval / judge / ragas 相关回归通过。
- Stop D（Commit 16）：已完成。router selective cutover 仍挂在 feature flag 后，正式链保持 lexical fallback，不存在未受控接管。

## 4.3 本阶段测试实绩

- 核心回归：45 项通过。
- 新增与停点测试：48 项通过。
- 合计：93 项通过。

## 4.4 Full Live 主链回写

- `extract-style` 正式产物目录：`D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable`，已复核存在。
- `build-canon` 正式产物目录：`D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable`，已复核存在。
- `build-style-bible`：已于 2026-04-21 20:22:44（Asia/Shanghai）成功完成，生成 `style_bible_final.json`、`style_bible_reasoning.json`、`style_bible_reduce_trace.json`、`run_manifest.json`、`_local_reduce`、`_section_densify` 等正式产物。
- `evaluate-style-bible`：命令成功退出，生成 `style_eval_report.json` / `style_eval_report.md`，报告 `status = fail`，`overall_score = 84.57`。
- `judge-style-bible`：命令成功退出，生成 `judge_report.json` / `judge_rows.jsonl` / `judge_report.md`，报告 `status = warn`，`overall_score = 67.99`。
- `evaluate-style-bible-ragas`：命令成功退出，生成 `ragas_report.json` / `ragas_dataset.json` / `ragas_rows.jsonl` / `ragas_report.md`，18 项样本中 `pass = 9`、`warn = 9`、`fail = 0`。

## 4.5 验收结论

- 本 checklist 约束的 17 个 commit 目标、4 个停点复核、README / 蓝图同步与从 `extract-style` 起步的 full live 主链均已执行。
- 命令成功退出与产物完整性这两类强制验收条件已满足。
- 运行时决策仍保持 `router_semantic_cutover_enabled = false` 的 shadow-only 形态，因此本次 full live 不需要做 semantic-first feature flag 回滚。
- 剩余开放项为质量阈值而非链路故障：`style_eval_report` 未过 gate、`judge_report` 为 `warn`。后续若继续提升质量，应优先围绕 actionability、anti-pattern resistance、worldbook exportability 与 rag atomicity 做迭代，而不是回退本次 Phase 2 架构落地。
