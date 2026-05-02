# 第 2 阶段：架构解构与语义重构正式方案

日期：2026-04-21

适用仓库：`D:\card\novel_pipeline`

文档定位：本文件是 Phase 2 的正式版施工方案。它覆盖模型解构、Prompt 合同、控制面、去 keyword 化、语义评估、可观测性与灰度切换，不是只讨论某一个子题。

---

## 0. 执行摘要

第 1 阶段已经完成止血，重点解决了静默失败、prompt 合同错位、repair 控制面缺失、section completeness 失控、部分 grounding 失真等问题，并建立了 mini/full live 的可观测性基线。

但系统的主干架构仍处于“能跑、可观测、但结构还不健康”的状态。当前最突出的结构性矛盾有四个：

1. 规则模型仍然是单体“上帝模型”，大量不同契约共用一套宽字段。
2. prompt 合同仍主要靠手写说明维持，`Schema as Code` 还没有真正落地。
3. section target 虽已有本地化结构，但中文语义锚点尚未成为运行时 canonical。
4. router / reducer / evaluator / judge / hybrid retriever 仍重度依赖关键词和 cue overlap，系统泛化能力受限。

因此，Phase 2 的核心任务不是继续在单点 prompt 上做微调，而是完成一轮**渐进式架构解构与语义重构**：

- 从“单体模型 + path 补丁校验”升级为“按契约族分型 + path 到模型绑定”。
- 从“手写 prompt 合同”升级为“由模型和控制面派生的紧凑合同”。
- 从“英文/散落 cue 驱动”升级为“中文语义锚点优先的控制面”。
- 从“关键词中心主义”升级为“语义优先、关键词兜底”的组合式判定体系。

本阶段不是大爆炸重写，而是严格分层、可回滚、可灰度、可验证的中期重构。

---

## 1. 当前仓库状态校准

本方案基于当前仓库真实状态制定。下面这些事实需要先写清楚，否则施工会建立在错误前提上。

## 1.1 已经在 Phase 1 局部修过的地方

- `StyleBibleReasoningEntry` 已经不再是空壳。现在必须有实质文本和 `evidence_refs`，否则校验失败。
- `worldbook_binding.rag_worthy` 与 `worldbook_binding.worldbook_worthy` 已经要求 `trigger + constraint`。
- `worldbook_binding.routing_hints` 已经要求 `query_feature_matcher + route_target_action`。
- `style_bible_section_targets.toml` 已经存在 `cue / canonical_description / positive_cues / negative_cues` 结构。
- `section_completeness` 的 control plane、repair-only 定向补齐、densify burn-down 等第一轮护栏已经存在。

结论：Phase 2 不是从废墟重建，而是在已有止血基础上做结构升级。

## 1.2 仍然没有解决的主问题

- `StyleBibleRuleItem` 仍承担绝大多数 path 的统一载体，字段职责混杂。
- `StyleBibleResultV2` 仍然几乎全部挂在 `StyleBibleRuleItem` 这种宽模型上。
- `LocalRuleRow` 当前仍主要依赖 `surface_path -> required_fields` 进行补丁式校验，而不是 path-specific 模型。
- `style_bible_prompt_assembler.py` 会注入 `surface_path_specs`、`section_targets`、`path_target` 等 payload，但不会自动按模型派生紧凑输出合同。
- `style_bible_ragas_eval.py` 当前仍是 proxy evaluator，不是可直接替代现有 Eval/Judge 的主裁判。
- router / evaluator / judge / hybrid retriever 中的 keyword 逻辑仍是系统级刚性依赖，而不是可控 fallback。

## 1.3 本阶段需要修正的过时假设

- 不能再把仓库描述成“全链路都靠空字符串兜底”。部分基础契约已经变硬。
- 不能再把 worldbook 路径描述成“仍然只会输出 route 风格字段”。其字段合同已经部分修正。
- 不能把问题理解成“只改 prompt 就能解决”。当前问题本质是模型、prompt、控制面、评估器的耦合失衡。
- 不能把“去 keyword 化”理解成“删掉所有关键词”。真正要改的是关键词在系统里的角色。

---

## 2. Phase 2 的目标、范围与边界

## 2.1 本阶段目标

1. 解构“上帝模型”，建立 path-specific typed rule family。
2. 让 `surface_path` 与模型契约绑定，替代纯 `required_fields` 表驱动。
3. 实现 `Schema as Code` 的 prompt 合同生成能力。
4. 将 local reduce / densify / repair-only 的 prompt 合同分层，减少状态机混乱。
5. 将 section target 升级为中文语义锚点优先的运行时控制面。
6. 建立统一领域词汇控制面，收口 router / evaluator / judge / hybrid retriever 的碎片词表。
7. 让 router / reducer / evaluator / judge / hybrid retriever 进入 semantic-first 的 shadow mode。
8. 完成一轮可回滚的 selective cutover，为后续 Hybrid RAG、GraphRAG、LLM-as-a-Judge 铺设稳定底座。

## 2.2 本阶段范围

受影响组件主要包括：

- 模型层：
  - `src/novel_pipeline_stable/models.py`
  - `src/novel_pipeline_stable/style_bible_surface_specs.py`
- Prompt/Assembler：
  - `prompts/style_bible_local_reduce.md`
  - `prompts/style_bible_section_densify.md`
  - `src/novel_pipeline_stable/style_bible_prompt_assembler.py`
- 控制面与配置：
  - `config/style_bible_section_targets.toml`
  - 新增 `config/project_domain_vocabulary.toml`
- 运行时逻辑：
  - `src/novel_pipeline_stable/style_bible_router.py`
  - `src/novel_pipeline_stable/style_bible_reducer.py`
  - `src/novel_pipeline_stable/hybrid_retriever.py`
- 评估与裁判：
  - `src/novel_pipeline_stable/style_bible_evaluator.py`
  - `src/novel_pipeline_stable/style_bible_judge.py`
  - `src/novel_pipeline_stable/style_bible_ragas_eval.py`

## 2.3 本阶段明确不做的事

1. 不重写 `extract / canon / fact extraction` 主链。
2. 不一次性把 `StyleBibleResultV2` 改成全量复杂 `Union`。
3. 不直接删除所有关键词逻辑。
4. 不把 `style_bible_ragas_eval.py` 误写成现成的主评估器。
5. 不把 World Graph / GraphRAG 主体实现混入本阶段施工。
6. 不在没有 shadow 验证的情况下，让 semantic score 直接接管所有 hard gate。

---

## 3. 设计原则

## 3.1 控制面优先于 prompt 微调

只要模型契约、path 绑定、slot 语义锚点、eval 口径不稳定，再多 prompt 微调也只能得到脆弱收益。

## 3.2 兼容优先于纯洁重写

Phase 2 的落地要允许旧 payload、旧产物、旧测试逐步迁移。兼容层不是技术债，而是迁移成本的保险丝。

## 3.3 语义优先，但必须受证据约束

所谓 semantic-first，不是让模型“更自由”，而是让判定更靠近语义原型，同时继续接受 evidence、grounding、schema contract 的硬约束。

## 3.4 中文语义锚点优先

当前项目是中文网文域。控制面、slot 语义、机制描述应优先使用贴近中文语境的 canonical cue 与描述，而不是英文翻译腔。

## 3.5 可观测性先于 cutover

任何判定链在接管业务之前，都必须先以 shadow mode 并行输出日志与对照报告。

---

## 4. 实施方案

## 4.1 工作流 A：解构“上帝模型”

### 背景

当前 `StyleBibleRuleItem` 把 narrative、worldbook、routing、negative、scalar 等不同形态的规则全部塞进同一个宽模型中。这导致：

- 字段职责混杂
- 默认空值泛滥
- 验证只能放到运行后补丁式兜底
- 评估器和 reducer 难以根据结构做稳定推断

### 目标

建立按**字段契约形状**划分的 typed rule family，而不是继续让所有 path 共用同一宽模型。

### 第一批建议模型族

- `ConstraintRuleRow`
- `RoutingHintRuleRow`
- `NegativeRuleRow`
- `ScalarRuleRow`

### 落地方式

1. 在 `models.py` 中新增 typed row family。
2. 保留 `StyleBibleRuleItem` 作为兼容 DTO。
3. 不要求 LLM 输出新的 discriminator 字段，仍以 `surface_path` 作为运行期判别上下文。
4. 优先改 `LocalRuleRow` 的解析与校验入口，不先碰最终持久化结构。

### 验收标准

- 新规则可按 path-specific 模型校验。
- 旧 payload 仍可读。
- 不破坏现有 `style_bible_final.json` 读写。

---

## 4.2 工作流 B：将 `surface_path` 绑定到契约模型

### 背景

当前 `surface_path` 与字段校验仍主要依赖 `required_fields` 列表。它适合止血，不适合长期演进。

### 目标

让 `surface_path` 不再只是一个字符串标签，而是直接绑定到：

- rule family
- row model
- enum source
- merge strategy
- conflict policy

### 落地方式

在 `style_bible_surface_specs.py` 的 `SurfacePathSpec` 上新增：

- `rule_family`
- `row_model`
- `enum_source`

并让 `LocalRuleRow` 或其 adapter：

1. 先根据 `surface_path` 查找 spec。
2. 再按 spec 指定的 `row_model` 校验。
3. 最后回填为统一运行时视图。

### 验收标准

- path-specific 合同与模型绑定完成。
- `required_fields` 从唯一约束来源降级为兼容补充。

---

## 4.3 工作流 C：实现 `Schema as Code`

### 背景

当前 prompt assembler 能注入运行时 payload，但不会自动生成紧凑型 schema 合同。结果是：

- prompt 文本
- assembler payload
- Pydantic 校验

三者之间仍可能缓慢漂移。

### 目标

让 prompt 输出合同由模型与控制面自动派生。

### 推荐实现

在 `style_bible_prompt_assembler.py` 中新增：

- `build_compact_contract_fragment()`
- `build_path_contract_fragment(surface_path)`
- `build_repair_contract_fragment(repair_request)`

### 注入内容

每个合同片段只保留最小必要信息：

- 合法 path
- 必填字段
- alias
- 枚举候选
- canonical token
- 最小合法 row 示例
- 明确禁止项

### 关键约束

- `local reduce` 只拿当前 bucket 相关 path 的合同切片。
- `repair-only` 只拿 repair 涉及的更窄合同。
- `densify` 只拿 `target_path` 的单路径合同。
- 不把原始 `model_json_schema()` 全量扔进 prompt。

### 验收标准

- 模型定义、prompt 合同、运行时校验三者同源。
- prompt snapshot 稳定。

---

## 4.4 工作流 D：修正 Prompt 状态机与运行模式边界

### 背景

当前 local reduce prompt 里已经出现“首次抽取”和“repair-only 定向补齐”的认知混叠风险。即使用户没有显式要求在本阶段优先改 prompt，这一项仍应纳入正式方案，因为它直接影响后续 `Schema as Code` 的落地质量。

### 目标

让 prompt 明确区分以下模式：

- first pass
- repair-only
- densify

### 主要动作

1. 将 local reduce 中的 repair-only 指令物理隔离。
2. 将标量枚举约束与 alias 映射绑定到 runtime 合同，而不是埋在长段自然语言里。
3. 将 densify 的 slot 核销、burn-down、单 slot 增量策略单独放在 densify prompt 中。
4. 删除重复 schema 指令与重复红线，避免注意力被稀释。

### 验收标准

- local reduce、repair-only、densify 三种模式可通过合同测试稳定区分。
- prompt 合同测试补绿。

---

## 4.5 工作流 E：将 section target 升级为中文语义锚点控制面

### 背景

当前 `style_bible_section_targets.toml` 已经不是空白，但很多 slot 的 canonical 语义仍不够中文化，且长 cue 列表容易造成噪音。

### 目标

让 slot 的运行时语义锚点以中文 `cue + canonical_description` 为主，而不是依赖英文 cue 列表驱动。

### 主要动作

1. 审计所有 `slot_specs`。
2. 要求每个 slot 至少具备：
   - `slot_id`
   - 中文 `cue`
   - 中文 `canonical_description`
   - `downstream_shape`
   - `fresh_evidence_required`
3. 如需保留英文说明，增加辅助字段，而不是继续让英文描述承担主锚点职责。
4. prompt 注入时优先只传最小语义锚点集。
5. `positive_cues / negative_cues` 保留给 reducer/evaluator 作为辅助信号，不再成为 prompt 主体。

### 验收标准

- slot 语义更贴近中文网文机制表达。
- densify 命中率更依赖 slot 语义，而不是纯 cue overlap。

---

## 4.6 工作流 F：去 keyword 化改造

这不是本阶段的全部内容，但它是其中一条必须落地的主线。

### 4.6.1 “去 keyword 化”到底指什么

这里的“去 keyword 化”不是删除关键词，而是把关键词从“主裁判”降级为：

- lexical prior
- 轻量召回提示
- anti-regression 护栏
- fallback 兜底

把真正的主判断逐步迁移到：

- path-specific 合同
- 语义原型匹配
- evidence overlap
- semantic eval / judge sidecar

### 4.6.2 第一轮要做的事

新增统一控制面：

- `config/project_domain_vocabulary.toml`
- `src/novel_pipeline_stable/project_domain_vocabulary.py`

把以下碎片信息收口：

- axis vocabulary
- route cues
- generic patterns
- safe cues
- mechanism prototypes
- anti-stuffing vocabulary

### 4.6.3 模块级改造方向

#### Router

从“关键词 + feature score”过渡到：

- `semantic_axis_score`
- `feature_score`
- `lexical_prior_score`

#### Reducer / Densify

从“cue overlap 辅助 embedding”过渡到：

- `semantic_slot_score`
- `evidence_overlap_score`
- `cue_score`
- `combined_score`

其中 `cue_score` 降级为 prior 和解释字段。

#### Evaluator

保留 lexical evaluator，但新增 semantic sidecar：

- specificity
- actionability
- groundedness
- routing_utility
- genericness

#### Judge

从 `must_include_any / should_include_any` 的关键词匹配，过渡到：

- semantic prototype similarity
- grounding
- anti-genericness

#### Hybrid Retriever

从 cue route 主导，过渡到：

- semantic intent classifier
- lexical route baseline
- 双通道并行记录

### 4.6.4 验收标准

- keyword 逻辑被统一收口。
- router / evaluator / judge / hybrid retriever 可以并行输出 lexical 与 semantic 信号。
- 至少一个模块完成 semantic-first shadow mode。

---

## 4.7 工作流 G：升级语义评估与 Judge 体系

### 背景

当前 `style_bible_ragas_eval.py` 是 “Ragas-ready proxy evaluator”，并不能直接替代主链评估与裁判逻辑。

### 目标

把它升级为 semantic eval sidecar 的统一承载层，并逐步让 evaluator/judge 接入语义维度。

### 主要动作

1. 为 evaluator / judge 增加语义 sidecar 指标。
2. 为 `style_bible_ragas_eval.py` 增加 provider 接口或统一语义评分入口。
3. 输出对照报告，而不是直接替换现有硬门槛。

### 推荐指标

- `specificity_semantic`
- `actionability_semantic`
- `faithfulness_semantic`
- `groundedness_semantic`
- `genericness_semantic`
- `routing_utility_semantic`

### 验收标准

- semantic eval 报告可与现有 eval/judge 并行输出。
- 语义指标和人工审核在代表 bucket 上具有可接受一致性。

---

## 4.8 工作流 H：可观测性、灰度与 cutover

### 背景

如果没有可观测性，semantic-first 会把系统推入“更智能但更不可调试”的状态。

### 目标

在任何 cutover 之前，把所有新判定链都跑成 shadow mode，并保留可解释日志。

### 必须记录的信号

- lexical_prior_score
- semantic_score
- evidence_overlap_score
- feature_score
- final_decision_source

### 灰度顺序

1. 先在日志中并行记录。
2. 再输出 shadow diff 报告。
3. 再做小范围 selective cutover。
4. 仅在对照稳定后扩大范围。

### 验收标准

- 每个新决策链都能解释“为什么判定如此”。
- cutover 有明确 feature flag 和回滚路径。

---

## 5. 里程碑

## M1：模型与 path 合同分型完成

- typed row family 落地
- `surface_path -> row_model` 绑定落地
- local reduce / densify 仍兼容旧产物

## M2：Schema as Code 完成

- assembler 可生成紧凑合同
- prompt 合同 snapshot 补绿

## M3：Prompt 状态机分层完成

- first pass / repair-only / densify 合同分离
- prompt 合同测试补绿

## M4：section target 语义锚点完成

- slot 审计完成
- 中文 `cue / canonical_description` 成为主锚点

## M5：去 keyword 化基建完成

- centralized vocabulary 落地
- lexical signals 统一读取
- semantic shadow signals 能输出

## M6：首轮 semantic-first cutover 完成

- 至少一个模块完成 selective cutover
- mini/full live 无明显回退

---

## 6. 测试与验证计划

## 6.1 单元测试

优先扩展并复用现有测试：

- `tests/test_style_bible_v2_schema_contracts.py`
- `tests/test_style_bible_local_reduce_contracts.py`
- `tests/test_style_bible_eval_profiles.py`
- `tests/test_style_bible_ragas_eval.py`
- `tests/test_hybrid_retriever.py`
- `tests/test_style_bible_judge_scope_aware.py`

建议新增：

- `tests/test_style_bible_rule_family_models.py`
- `tests/test_style_bible_prompt_contract_generation.py`
- `tests/test_project_domain_vocabulary.py`
- `tests/test_semantic_shadow_scores.py`

## 6.2 Snapshot / Contract 测试

必须补：

- local reduce prompt snapshot
- repair-only prompt snapshot
- densify prompt snapshot
- path contract snapshot
- backward-compat payload snapshot

## 6.3 集成验证顺序

1. 先做 typed row family + adapter。
2. 再做 path contract binding。
3. 再做 compact contract schema。
4. 再做 prompt 状态机拆分。
5. 再做 section target 审计。
6. 再做 vocabulary centralization。
7. 再做 semantic shadow mode。
8. 最后做 selective cutover。

## 6.4 Live 验证

- 先跑 mini live，选 3 个代表 bucket。
- 再跑 full live shadow mode。
- 最后才跑首轮 cutover full live。

## 6.5 验收指标

- `scalar_contract_pass_rate` 提升。
- `section_completeness` 不回退。
- `useful_routing_hint_ratio` 不回退。
- `rag_worthy / worldbook_worthy` groundedness 不回退。
- semantic sidecar 与人工审核一致性优于纯关键词基线。

---

## 7. 风险与回滚

## 7.1 主要风险

1. typed rule family 一次性改太多，导致旧 payload 不兼容。
2. prompt 合同过长，反而稀释模型注意力。
3. section target 中文化不充分，导致 slot 语义锚点仍模糊。
4. 误把“去 keyword 化”做成“去可解释性”。
5. 语义 sidecar 指标看起来更高级，但与人工判断不一致。

## 7.2 回滚策略

1. typed row family 通过 feature flag 回退到 legacy parser。
2. compact contract builder 保留 legacy prompt mode。
3. semantic sidecar 初期只写日志，不参与 hard gate。
4. centralized vocabulary 初期只做统一读取，不先删旧常量。
5. selective cutover 必须具备单独关闭开关。

---

## 8. 完成定义

Phase 2 结束不以“写完文档”或“加了几个类”为标准，而以以下条件同时满足为准：

1. path-specific typed contract 已进入主链。
2. prompt 合同由模型和控制面自动派生。
3. local reduce / repair-only / densify 的模式边界清晰。
4. section target 的中文语义锚点成为运行时主锚点。
5. keyword 逻辑已从散落 hard-code 收口为统一控制面。
6. 至少一个模块完成 semantic-first 的可回滚 cutover。
7. mini/full live 验证无明显回退。

---

## 9. 结论

这轮 Phase 2 的实质，不是“继续修 prompt”，也不是“简单上 embedding”，而是把系统从一套由宽模型、手写合同、散落词表和 keyword if/else 撑起来的流水线，升级为：

- 结构有分型
- 合同有同源
- 控制面有语义锚点
- 评估有 shadow mode
- 运行有回滚路径

的中期稳定架构。

只有在这个基础上，后续的 Hybrid RAG、World Graph、GraphRAG、真正的 LLM-as-a-Judge 才能稳定挂接，而不会继续堆在一套脆弱的 keyword 中心体系上。

---

## 10. 2026-04-21 实施结果回写

## 10.1 范围与实现状态

- Phase 2 施工已严格限定在 `D:\card\novel_pipeline`。
- typed rule family、`surface_path -> rule_family / row_model / enum_source` 绑定、`Schema as Code`、prompt 状态机分层、中文语义锚点、统一词汇控制面、semantic sidecar、shadow telemetry 与 selective cutover feature flag 均已落地到正式代码。
- 兼容层、lexical fallback、shadow telemetry 与回滚路径均保留；`StyleBibleResultV2` 外部形状未做大爆炸 Union 重写。
- README、README_CN 与本阶段 workflow 文档已同步到同一套术语口径。

## 10.2 测试回写

- 核心回归已通过：`tests/test_style_bible_v2_schema_contracts.py`、`tests/test_style_bible_local_reduce_contracts.py`、`tests/test_style_bible_eval_profiles.py`、`tests/test_style_bible_ragas_eval.py`、`tests/test_hybrid_retriever.py`、`tests/test_style_bible_judge_scope_aware.py`，共 45 项通过。
- Phase 2 新增与停点测试已通过：`tests/test_style_bible_rule_family_models.py`、`tests/test_style_bible_prompt_contract_generation.py`、`tests/test_project_domain_vocabulary.py`、`tests/test_style_bible_section_targets.py`、`tests/test_style_bible_router_batching_builder_guards.py`、`tests/test_semantic_shadow_scores.py`、`tests/test_style_bible_hierarchical_reducer.py`、`tests/test_style_bible_build_resume.py`，共 48 项通过。
- 本阶段实跑合计 93 项测试通过，未出现新增红测。

## 10.3 Full Live 验收回写

- `extract-style` 正式链产物已在 `D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable` 落盘并复核。
- `build-canon` 正式链产物已在 `D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable` 落盘并复核。
- `build-style-bible` 已于 2026-04-21 20:22:44（Asia/Shanghai）完成，`run_status.json` 为 `completed`，`success_count = 1`，`failure_count = 0`。
- `style_bible_formal_cn_gpt54_stable` 正式输出已生成：`style_bible_final.json`、`style_bible_reasoning.json`、`style_bible_export_flat.json`、`style_bible_reduce_trace.json`、`run_manifest.json`、`_local_reduce`、`_section_densify`、`semantic_dedupe_drop_pairs_aggregate.json`。
- `evaluate-style-bible` 命令成功退出并生成 `style_eval_report.json` / `style_eval_report.md`。报告摘要：`status = fail`，`overall_score = 84.57`，`quality_gate_passed = false`。
- `judge-style-bible` 命令成功退出并生成 `judge_report.json` / `judge_rows.jsonl` / `judge_report.md`。报告摘要：`status = warn`，`overall_score = 67.99`，`quality_gate_passed = false`。
- `evaluate-style-bible-ragas` 命令成功退出并生成 `ragas_report.json` / `ragas_dataset.json` / `ragas_rows.jsonl` / `ragas_report.md`。报告摘要：18 项样本，`pass = 9`，`warn = 9`，`fail = 0`。

## 10.4 Shadow / Cutover 状态

- Full live 期间 `semantic_shadow_enabled = true`。
- `router_semantic_cutover_enabled = false`，`selective_cutover_target = router`。
- evaluator / judge / ragas 的 `final_decision_source` 分别为 `legacy_eval_with_semantic_sidecar`、`legacy_judge_with_semantic_sidecar`、`legacy_ragas_with_semantic_sidecar`。
- 因正式链仍以 legacy 决策路径配合 semantic sidecar 运行，本次 full live 不需要触发 semantic-first 回滚。

## 10.5 结论

- Phase 2 计划内的代码改造、测试回归、README 同步与从 `extract-style` 起步的 full live 主链均已实际执行完成。
- 正式命令链全部成功退出，强制验收产物完整生成，shadow / cutover 运行状态与“默认 shadow-only、保留 lexical fallback”的设计一致。
- 需要继续跟踪的开放项不是运行时崩溃，而是质量阈值：`style_eval_report` 仍为 `fail`、`judge_report` 仍为 `warn`。这部分应作为后续质量优化与规则调优任务继续消化，但不构成“命令失败或产物缺失”型阻塞。
