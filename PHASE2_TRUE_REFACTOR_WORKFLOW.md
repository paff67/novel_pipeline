# Phase 2: 真正的架构解构与语义重构工作流 (True Refactor Workflow)

## 0. 核心定位
之前的过渡方案（20260421版）过于保守，妥协于“兼容旧产物”和“Shadow Mode”，导致架构债未被实质性清除。本工作流旨在**彻底拔除**上帝模型、硬编码 Schema 和正则化评估，拒绝“半衰期”的过渡态。

---

## Workflow 1: 彻底解构上帝模型 (Pydantic Polymorphism)
**目标：** 铲除 `StyleBibleRuleItem` 这个无所不包的 God Model，使用原生的 Pydantic 多态特性重写数据结构。

1. **基类提取：** 定义 `StyleBibleRuleBase(BaseModel)`，仅保留 `rule_id` 和 `text` 等真正的共有字段。
2. **派生具体模型：**
   - `NarrativeRuleItem`: 增加 `trigger`, `constraint`, `correction_guideline`。
   - `WorldbookFactItem`: 增加 `trigger`, `constraint`，严禁出现路由字段。
   - `RoutingHintItem`: 仅保留 `query_feature_matcher`, `route_target_action`。
3. **重写 V2 Schema：** 修改 `StyleBibleResultV2`，使用 `Union` 类型定义字段。例如：`routing_hints: list[RoutingHintItem]`。
4. **清理默认值：** 彻底剔除掩盖问题的 `default=""`，让缺字段的畸形输出在 Pydantic 实例化瞬间直接抛出 `ValidationError`。

---

## Workflow 2: 纯粹的 Schema as Code (Native JSON Schema)
**目标：** 消灭手写 Contract Builder 这种“二次翻译”的中间层，直接让 Pydantic Schema 接管大模型输出。

1. **清理 Prompt：** 删掉 `.md` 文件中所有关于“你应该输出什么 JSON 结构”的自然语言描述和伪代码。
2. **直接反射：** 在 `style_bible_prompt_assembler.py` 中，不再手写 `build_compact_contract_fragment()`，而是直接调用目标子模型的 `model_json_schema()`。
3. **结构化输出绑定：** 优先利用 OpenAI 兼容网关的 `response_format = {"type": "json_schema", "json_schema": ...}` 特性，从 API 协议层强制大模型必须按照 Pydantic 定义的结构返回数据。

---

## Workflow 3: 中文语义深度绑定 (Deep Semantic Anchoring)
**目标：** 让中文 `cue` 成为大模型理解槽位的唯一依据。

1. **清洗 TOML：** 确保 `style_bible_section_targets.toml` 中的每一个槽位都有且仅有高质量的中文 `cue` 和 `canonical_description`，抛弃冗长的英文 `positive_cues` 列表。
2. **Schema 动态注入：** Assembler 读取 TOML 后，动态修改 Pydantic 模型的 `Field(description="...")`，把中文 `cue` 直接塞进发给大模型的 JSON Schema 定义里。让模型在看 Schema 的同时就能接收到强大的中文语境锚定。

---

## Workflow 4: 斩断正则，全面切入 LLM-as-a-Judge (Semantic Cutover)
**目标：** 终结基于硬编码 Keyword 的“查字典”式评估，直接启用语义裁判。

1. **大清洗：** 删除 `style_bible_evaluator.py` 中所有基于 `re.search` 和 `in` 的匹配函数（如 `_looks_generic`, `_is_actionable`）。
2. **提拔 Ragas：** 将 `style_bible_ragas_eval.py` 从“旁路参考 (Sidecar)”直接提拔为“主裁判 (Main Judge)”。
3. **语义打分：** 引入轻量的 LLM 调用（如 Qwen 小模型），输入 Rule 和 Source Text，让模型按 1-5 分输出“具体性 (Specificity)”和“可执行性 (Actionability)”。
4. **硬性 Cutover：** 废弃 Shadow Mode。系统的生杀大权直接交由 LLM-as-a-Judge 的评分结果决定。

---

## 验收标准 (Definition of Done)
1. 代码中再无全局通用的 `StyleBibleRuleItem` 定义。
2. `style_bible_evaluator.py` 中不再包含任何预设的业务 Keyword 列表。
3. Prompt 中不再有任何 JSON 格式示例。
4. Pipeline 能够通过全量测试，且产出的 Rule 具备极高的领域特异性（Xianxia-specific）。
