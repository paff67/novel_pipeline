# Phase 2: 真正的架构解构与语义重构 Commit 级施工清单 (True Refactor)

## 0. 总体施工原则
1. **拒绝妥协**：不保留“上帝模型”，不保留手工 JSON 拼凑，彻底移除基于正则的硬门槛。
2. **物理拆分解耦**：针对 `client.py` (>2k行) 和 `style_bible_reducer.py` (>5k行) 等巨石文件，严格按照设计模式（策略模式、管道模式）进行物理拆分。
3. **一步到位**：摒弃 Shadow Mode 的拖延心态，新架构直接接管主链路，依靠严格的 Pydantic 校验和 LLM-as-a-Judge 保证质量。

---

## Commit 01: 彻底解构上帝模型 (Pydantic Polymorphism)
**目标**：拆分 `StyleBibleRuleItem`，建立强类型的 Pydantic 联合类型 (Union)，并移除所有掩盖错误的 `default=""`。
**改动文件**：
- `src/novel_pipeline_stable/models.py`
- `src/novel_pipeline_stable/style_bible_surface_specs.py`
**具体动作**：
- 定义 `StyleBibleRuleBase`。
- 派生 `NarrativeRuleItem`, `WorldbookFactItem`, `RoutingHintItem` 等，确保独有字段（如 `query_feature_matcher`）只存在于特定子类。
- 在 `StyleBibleResultV2` 中使用 `Union` 类型，彻底抛弃旧的宽表模型。

## Commit 02: 纯粹的 Schema as Code (Native JSON Schema)
**目标**：删除 Prompt 中的手写 JSON，由 Pydantic 原生 `model_json_schema()` 动态生成约束并传递给大模型。
**改动文件**：
- `prompts/style_bible_local_reduce.md`
- `prompts/style_bible_section_densify.md`
- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`
**具体动作**：
- 删除 Markdown 中的 JSON 结构描述与伪代码。
- 修改 Assembler，直接反射目标 Pydantic 模型的 Schema。
- 将 API 调用的 `response_format` 强绑定到动态生成的 Schema。

## Commit 03: 中文语义深度绑定 (Deep Semantic Anchoring)
**目标**：清理 TOML 控制面，将中文 `cue` 动态注入到 Pydantic 的 Field description 中。
**改动文件**：
- `config/style_bible_section_targets.toml`
- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`
**具体动作**：
- 清理 TOML 中冗余的英文 keywords，保留并优化精准的中文 `cue` 和 `canonical_description`。
- Assembler 在构建动态 Schema 时，将对应的中文语境描述覆盖注入到 Schema 的字段说明中。

## Commit 04: 斩断正则，全面切入 LLM-as-a-Judge (Semantic Cutover)
**目标**：删除 Evaluator 中的硬编码关键词匹配，提拔 Ragas 为主裁判。
**改动文件**：
- `src/novel_pipeline_stable/style_bible_evaluator.py`
- `src/novel_pipeline_stable/style_bible_ragas_eval.py`
**具体动作**：
- 删掉 `_looks_generic`, `_is_actionable` 等基于 `re.search` 的硬匹配函数。
- 将基于大模型打分的语义指标（Specificity, Actionability）直接接入主干评估流程，以此决定规则的去留。

## Commit 05: 拆解巨石 Reducer (Pipeline Pattern)
**目标**：将 5000+ 行的 `style_bible_reducer.py` 拆分为职责单一的管道模块。
**改动文件**：
- 删除 `src/novel_pipeline_stable/style_bible_reducer.py`
- 新建包 `src/novel_pipeline_stable/style_bible_reduction/`
**具体动作**：
- `sanitizer.py`: 专职负责数据的过滤、清洗和 DropTracker 统计。
- `merger.py`: 专职处理同类规则的合并、冲突解决策略。
- `densifier.py`: 专职依据检索线索补全槽位和打分。
- `orchestrator.py`: 轻量级调度脚本，按顺序串联上述管道。

## Commit 06: 拆解巨石 Client (Strategy Pattern)
**目标**：将 2000+ 行的 `client.py` 拆解为针对不同网关的适配器和独立的纯逻辑工具。
**改动文件**：
- 删除 `src/novel_pipeline_stable/client.py`
- 新建包 `src/novel_pipeline_stable/api_clients/`
- 新建 `src/novel_pipeline_stable/utils/json_repair.py`
**具体动作**：
- `base.py`: 抽象通用的 LLM API 接口规范与标准重试装饰器。
- `openai_adapter.py` / `siliconflow_adapter.py`: 隔离特定于服务商的网关特性（如对 400 错误的特殊处理）。
- `json_repair.py`: 剥离冗长的残缺 JSON 暴力修复代码，使其成为无副作用的纯函数工具。

## Commit 07: 最终端到端集成验证
**目标**：确保新架构彻底跑通全链路。
**动作**：
- 修复因文件移动导致的 Import 错误。
- 执行 `pytest` 全量回归测试。
- 运行 `launch_formal_cn_gpt54_stable.ps1`，确认 Evaluator 基于语义判别产生合理的质量报告。
