# Style Extract 面向 Style Bible V2 的一步到位重构方案

## 1. 目标

本方案的目标不是给现有 `style extract` 打补丁，也不是设计兼容层，而是把它直接重构成 **Style Bible V2 的原生上游**。

重构完成后的定位如下：

- 保留当前按 `chapter window` 运行的调度壳子
- 废弃旧版 `StyleExtractionResult`
- 让 `style extract` 直接输出 **V2 可机器消费的风格信号对象**
- 让 `router / bucket builder / style bible builder / canon builder` 全部切换到新 contract
- 不保留 `legacy` / `hybrid` / `fallback adapter`

一句话概括：

**保留窗口提取形态，彻底替换语义 contract。**

---

## 2. 为什么必须整体重做

现有链路的问题不是单点 prompt 失真，而是整条 style 提取链路的 contract 已经和 Style Bible V2 的消费方式错位。

### 2.1 扁平字符串结构无法支撑 V2

当前 [StyleExtractionResult](src/novel_pipeline_stable/models.py) 的核心字段几乎都是 `list[str]`：

- `narrative_engine`
- `humor_mechanisms`
- `satire_targets`
- `characterization_mechanisms`
- `dialogue_signature`
- `pacing_pattern`
- `style_fingerprint`

这会导致三个问题：

1. 模型很容易退化成 2 到 8 字标签堆砌。
2. Router 只能把这些字符串重新拼起来做关键字命中。
3. Bucket Builder 拿不到结构化的“触发条件 / 执行动作 / 证据锚点”。

这套结构天然适合“风格摘要”，不适合“规则蒸馏”。

### 2.2 证据链是游离的

当前 `supporting_evidence` 是统一挂在结果底部的数组，字段只有：

- `claim`
- `evidence_text`

这意味着：

- `narrative_engine[0]` 和 `supporting_evidence[3]` 之间没有物理关联
- 下游无法确定某条 evidence 究竟支撑哪条规则
- V2 最重要的 `grounding / trace / route evidence` 没法闭环

### 2.3 标量字段没有被合同化

当前只有一个 `narrator_distance: str`，且 prompt 没有限制输出必须是枚举 token。

这与 V2 已经暴露出来的 scalar 污染问题高度同构。  
如果 style extract 上游继续放任自由文本，下游必然再次出现：

- `perspective` 被解释句污染
- `distance` 被说明句污染
- `temporality` 继续以长句形式漂移
- `inner_monologue_mode` 不稳定

### 2.4 Router 当前仍在吃“拼接文本”

当前 [style_bible_router.py](src/novel_pipeline_stable/style_bible_router.py) 对 style window 的处理方式仍然是：

1. 从多个旧字段抽取字符串
2. 拼成大文本
3. 通过关键字和统计密度推测 axis / bucket

这在 V2 初期可以工作，但已经成为上限瓶颈：

- 它无法区分“主题词”与“执行机制”
- 它无法明确区分“路由提示”与“表面风格”
- 它无法把负向陷阱前置进路由逻辑

### 2.5 Bucket Builder 当前拿不到真正可执行的 style 信号

当前 [style_bible_bucket_builder.py](src/novel_pipeline_stable/style_bible_bucket_builder.py) 对 `<style_window>` 的输出仍然是：

- `narrative_engine`
- `humor_mechanisms`
- `style_fingerprint`
- `supporting_claims`

这会把最难的“结构化归约”压力完全推给 local reduce。  
真正应该在 extract 层就明确表达的内容，比如：

- `query_feature_matcher`
- `route_target_action`
- `forbidden_action`
- `correction_guideline`
- `evidence_refs`

现在都没有原生位置。

---

## 3. 重构后的目标架构

### 3.1 总体原则

- 不改 chapter window 提取粒度
- 不让 extract 阶段直接产出最终 `StyleBibleResultV2`
- extract 阶段只负责产出 **窗口级 V2 风格信号**
- reducer 仍然负责跨窗口、跨桶、跨证据的全局合并

### 3.2 新链路

旧链路：

```text
chapter window
-> StyleExtractionResult
-> router heuristics
-> bucket memo
-> reduce
```

新链路：

```text
chapter window
-> StyleWindowSignalResult
-> explicit routing / bucket priors / scalar contracts
-> bucket memo
-> reduce
```

### 3.3 新 contract 的职责边界

`style extract` 负责：

- 窗口级机制提取
- 局部路由先验
- 标量叙事约束
- 证据索引与证据绑定
- 负向误判边界

`router` 负责：

- 以显式 hints 为主建立 axis / bucket membership
- 用特征分数做辅助，而不是主判据

`bucket builder` 负责：

- 把 style signal 结构化注入 local reduce
- 不再把 style window 当摘要文本，而是当结构化风格线索

`reducer` 负责：

- 跨窗口去重
- 规则升维
- 全局 conflict resolve
- supporting evidence 最终汇总

---

## 4. 新 schema 设计

## 4.1 设计原则

- 彻底废弃旧版 `StyleExtractionResult`
- 不保留兼容字段
- 不允许自由文本标量
- 每条强规则必须携带证据锚点
- 路由信息和负向陷阱必须有一等公民字段
- 新 schema 必须优先满足 OpenAI `strict: true` Structured Outputs 的可接受性，先过 schema preflight，再接入流水线
- schema 采用**主动压扁一层**的策略，避免纯分组容器推高嵌套深度与属性复杂度

## 4.2 核心对象

建议在 `models.py` 中新增如下结构。这里不再保留 `StyleWindowNarrativeSignals / StyleWindowExpressionSignals` 这类纯分组容器，而是直接把规则数组挂在顶层，主动降低 strict schema 的嵌套与复杂度风险：

```python
from typing import Literal
from pydantic import BaseModel, Field


class StyleEvidenceRef(BaseModel):
    evidence_id: str
    source_ref: str
    quote: str


class StyleSignalRule(BaseModel):
    mechanism_label: str
    execution_logic: str
    trigger: str = ""
    constraint: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class StyleRoutingHint(BaseModel):
    axis_id: str = ""
    bucket_id: str = ""
    query_feature_matcher: str
    route_target_action: str
    evidence_ids: list[str] = Field(default_factory=list)


class StyleNegativePitfall(BaseModel):
    forbidden_action: str
    correction_guideline: str
    evidence_ids: list[str] = Field(default_factory=list)


class StyleScalarContracts(BaseModel):
    perspective: Literal["close_third_person", "first_person", "multi_focal", "unknown"] = "unknown"
    distance: Literal["close", "mid", "far", "mixed", "unknown"] = "unknown"
    temporality: Literal["linear_forward", "retrospective_insert", "intercut_parallel", "mixed", "unknown"] = "unknown"
    inner_monologue_mode: Literal["sparse_inline", "embedded", "dense_reflective", "none", "unknown"] = "unknown"


class StyleWindowSignalResult(BaseModel):
    schema_version: Literal["style-window-signal-v2"] = "style-window-signal-v2"
    window_id: str
    chapter_ids: list[str]
    scalar_contracts: StyleScalarContracts = Field(default_factory=StyleScalarContracts)
    narrative_engine_rules: list[StyleSignalRule] = Field(default_factory=list)
    pacing_rules: list[StyleSignalRule] = Field(default_factory=list)
    plot_node_logic_rules: list[StyleSignalRule] = Field(default_factory=list)
    description_rules: list[StyleSignalRule] = Field(default_factory=list)
    dialogue_rules: list[StyleSignalRule] = Field(default_factory=list)
    characterization_rules: list[StyleSignalRule] = Field(default_factory=list)
    sensory_rules: list[StyleSignalRule] = Field(default_factory=list)
    humor_rules: list[StyleSignalRule] = Field(default_factory=list)
    satire_rules: list[StyleSignalRule] = Field(default_factory=list)
    nonstandard_xianxia_rules: list[StyleSignalRule] = Field(default_factory=list)
    narrator_voice_rules: list[StyleSignalRule] = Field(default_factory=list)
    register_mix_rules: list[StyleSignalRule] = Field(default_factory=list)
    negative_pitfalls: list[StyleNegativePitfall] = Field(default_factory=list)
    rag_candidates: list[StyleSignalRule] = Field(default_factory=list)
    worldbook_candidates: list[StyleSignalRule] = Field(default_factory=list)
    routing_hints: list[StyleRoutingHint] = Field(default_factory=list)
    axis_hints: list[str] = Field(default_factory=list)
    bucket_hints: list[str] = Field(default_factory=list)
    evidence_index: list[StyleEvidenceRef] = Field(default_factory=list)
    surface_markers: list[str] = Field(default_factory=list)
```

## 4.3 旧字段迁移表

| 旧字段 | 新字段去向 | 说明 |
| --- | --- | --- |
| `surface_genre` | `surface_markers` | 降级为表层标记，不再参与主 contract |
| `narrative_engine` | `narrative_engine_rules` | 升维为 `StyleSignalRule` |
| `narrator_distance` | `scalar_contracts.perspective / distance / temporality` | 拆成枚举 |
| `humor_mechanisms` | `humor_rules` | 升维为规则对象 |
| `satire_targets` | `satire_rules` | 升维为规则对象 |
| `characterization_mechanisms` | `characterization_rules` | 升维为规则对象 |
| `dialogue_signature` | `dialogue_rules / register_mix_rules` | 拆分 |
| `pacing_pattern` | `pacing_rules` | 升维为规则对象 |
| `emotion_aftertaste` | `narrator_voice_rules` | 不再独立保留 |
| `why_nonstandard_xianxia` | `nonstandard_xianxia_rules` | 升维为规则对象 |
| `style_fingerprint` | `surface_markers` | 仅作调试/摘要参考 |
| `supporting_evidence` | `evidence_index + evidence_ids` | 彻底去掉游离 evidence |

---

## 5. Payload 重构

## 5.1 当前 payload 的不足

当前 `_build_style_payload()` 只提供：

- `window_id`
- `chapter_ids`
- `chapters[].chapter_id`
- `chapters[].title`
- `chapters[].text`

这有两个硬伤：

1. 没有稳定的 scene 级定位引用
2. prompt 里提到 `source_text`，但 payload 实际没有这个字段

## 5.2 新 payload 设计

建议把 `_build_style_payload()` 改成输出：

```python
payload = {
    "window_id": "...",
    "chapter_ids": [...],
    "chapters": [
        {
            "chapter_id": "...",
            "title": "...",
            "source_text": "...",
            "normalization_applied": [...],
        }
    ],
    "scene_locator": [
        {
            "source_ref": "scene:0001_001",
            "chapter_id": "0001",
            "scene_id": "0001_001",
            "start_anchor": "主角推开大门",
            "end_anchor": "他抬头看向反派",
        }
    ],
}
```

### 说明

- chapter 原文仍然是主证据源
- `scene_locator` 只提供最小定位信息，不提供 scene summary / style markers / open questions，避免上游摘要干扰模型直接阅读原文
- `source_ref` 必须与 fact 产物和 style bible trace contract 对齐
- 若无法稳定给出双 anchor，可以退化成 `excerpt_hint`，但仍然禁止携带解释性摘要
- `scene_locator` 的总字符预算必须受控，避免它喧宾夺主压过 `chapters[].source_text`

---

## 6. Prompt 重写原则

现有 `style_extraction.md` 必须整体作废，不能增量修补。

## 6.1 Prompt 的新定位

新 prompt 不再要求模型做“文学风格分析”，而是要求模型输出：

- 作者如何具体推进文本
- 哪些机制可转化为下游规则
- 哪些线索可作为路由触发器
- 哪些误判必须避免
- 哪些证据可回指原文

## 6.2 必须前置的硬性约束

### A. 动作化

所有强规则都必须写成“作者如何写”的执行逻辑，不允许抽象评论句。

- 错误：`通过债务书写压力`
- 正确：`当角色获得进展时，先插入债务、成本或资格结算，再让成果落地`

### B. 证据绑定

每条规则、每条 routing hint、每条 pitfall 必须绑定 `evidence_ids`。

### C. 标量枚举

`perspective / distance / temporality / inner_monologue_mode` 只能输出枚举 token，不得解释。

### D. 路由显式化

`routing_hints` 必须有：

- `query_feature_matcher`
- `route_target_action`

### E. 负向边界显式化

`negative_pitfalls` 必须有：

- `forbidden_action`
- `correction_guideline`

### F. 禁止跨领域过度泛化

如果文本中没有明确的资源压力，就不能为了贴合桶主题把冲突硬解释成账单、债务或成本结算。

### G. 信息不足宁缺毋滥

信息不足时留空，不允许补文学感受句来填 schema。

---

## 7. Client 与结构化输出策略

当前 style 提取使用：

- `response_format = "json_object"`
- client 自动追加 `Output contract`

这在旧 schema 下还能工作，但在新 contract 下风险过高：

- 容易把复杂对象压扁
- 容易触发宽松 coercion
- 容易让标量字段退化成解释文本

## 7.1 新策略

style extract 统一切换为：

- `response_format = "json_schema"`
- `strict = true`

即：

- 不再依赖 `json_object`
- 不再让 client 拼蓝图模板诱导模型输出空壳
- 不再允许 `_coerce_to_model_shape()` 对 style 结果做宽松回收

## 7.2 配置建议

在 `formal_cn_gpt54_stable.toml` 中：

- `response_format = "json_schema"`
- `style_temperature = 0.1`
- `style_max_output_tokens = 4096`
- `window_size = 2`
- `stride = 2`

不改动：

- `style_model = "gpt-5.4"`
- `api_route = "responses"`
- `reasoning_effort = "xhigh"`
- `enable_local_request_cache = true`

---

## 8. Router 重构

## 8.1 现状

当前 `router` 的 style window 逻辑主要集中在：

- `_style_window_text_fragments()`
- `_style_window_features()`
- `_build_routed_items()`

核心问题是：  
style window 仍然被当作“字符串集合”，而不是“结构化风格信号对象”。

## 8.2 新策略

Router 对 style window 的路由改成：

### 一级判断：显式 hints

直接读取：

- `route_priors.axis_hints`
- `route_priors.bucket_hints`
- `worldbook_binding.routing_hints`

这些字段成为 style window 路由的主判据。

### 二级判断：结构化规则密度

通过结构化对象数量和 evidence 覆盖率计算辅助特征：

- institution rule density
- resource pressure rule density
- dark humor rule density
- route hint specificity
- negative pitfall density
- evidence coverage ratio

### 三级判断：关键词仅作兜底

只在极少数 hints 缺失场景下，才允许回退到浅层关键字辅助。

## 8.3 Router 新特征建议

当前 `StyleBibleFeatureMetrics` 不够表达新 style contract。建议新增：

- `route_hint_specificity`
- `negative_guard_density`
- `scalar_contract_completeness`
- `evidence_binding_ratio`
- `institution_rule_density`
- `resource_rule_density`
- `dark_humor_rule_density`

---

## 9. Bucket Builder 重构

## 9.1 现状

当前 `<style_window>` 仅输出摘要和少量文本列表，导致 local reduce 看不到真正有用的结构化信号。

## 9.2 新策略

在 `<style_window>` XML 中，直接输出这些 section：

- `<scalar_contracts>`
- `<route_priors>`
- `<routing_hints>`
- `<negative_pitfalls>`
- `<narrative_rules>`
- `<expression_rules>`
- `<aesthetic_rules>`
- `<voice_rules>`
- `<evidence_index>`

每条规则至少需要：

- `mechanism_label`
- `execution_logic`
- `trigger`
- `constraint`
- `evidence_ids`

每条 routing hint 至少需要：

- `axis_id`
- `bucket_id`
- `query_feature_matcher`
- `route_target_action`
- `evidence_ids`

每条 pitfall 至少需要：

- `forbidden_action`
- `correction_guideline`
- `evidence_ids`

### 结果

这样 local reduce 才能在窗口层直接看到：

- 这条 style signal 想把什么路由到哪里
- 它是如何被原文支持的
- 它明确禁止什么误路由

---

## 10. Style Bible Builder 与 Style Index 重构

## 10.1 Style sample 压缩逻辑必须切换

当前 `_compact_style_window()` 仍在压：

- `surface_genre`
- `narrative_engine`
- `humor_mechanisms`
- `style_fingerprint`

新版本必须改成压缩：

- `route_priors`
- `scalar_contracts`
- `routing_hints`
- `negative_pitfalls`
- `top execution_logic`
- `top evidence refs`

## 10.2 global_style_signals 的摘要口径必须切换

旧摘要口径已经不够用了。新 `global_style_signals` 应该汇总：

- `axis_hint_counts`
- `bucket_hint_counts`
- `routing_target_counts`
- `scalar_contract_counts`
- `top_mechanism_labels`
- `top_negative_pitfalls`

## 10.3 canon builder 的 style_index 必须同步升级

当前 `style_index.json` 只统计：

- `style_fingerprint_counts`
- `window_count`

这不再能反映 V2 风格提取产物的核心价值。

建议升级为：

```json
{
  "window_count": 0,
  "axis_hint_counts": {},
  "bucket_hint_counts": {},
  "routing_target_counts": {},
  "scalar_contract_counts": {
    "perspective": {},
    "distance": {},
    "temporality": {},
    "inner_monologue_mode": {}
  },
  "mechanism_label_counts": {},
  "negative_pitfall_counts": {}
}
```

---

## 11. 文件级改造清单

## 11.1 必改文件

### `src/novel_pipeline_stable/models.py`

- 删除旧版 `StyleExtractionResult`
- 新增：
  - `StyleEvidenceRef`
  - `StyleSignalRule`
  - `StyleRoutingHint`
  - `StyleNegativePitfall`
  - `StyleScalarContracts`
  - `StyleWindowSignalResult`
- 扩展：
  - `StyleBibleFeatureMetrics`

### `src/novel_pipeline_stable/pipelines.py`

- 重写 `_build_style_payload()`
- `extract_style()` 的调度逻辑保留
- `response_model` 改为 `StyleWindowSignalResult`
- 更新 style 内容非空校验逻辑

### `scripts/check_style_window_schema.py`

- 新增独立 schema 预检脚本
- 先做静态 schema 复杂度检查
- 再用当前生产链路同样的 Responses + `json_schema` 发最小真请求验证 schema 可接受性

### `prompts/style_extraction.md`

- 整体重写
- 移除赏析导向
- 强制动作化、证据绑定、路由显式化、标量枚举化

### `config/formal_cn_gpt54_stable.toml`

- `response_format = "json_schema"`
- `style_temperature = 0.1`
- `style_max_output_tokens = 4096`

### `src/novel_pipeline_stable/style_bible_inputs.py`

- 对新 style schema 做强校验
- 旧 schema 直接 fail fast

### `src/novel_pipeline_stable/style_bible_router.py`

- 删除 style window 扁平文本主路由逻辑
- 改为显式 hints 主导
- 关键词仅作为辅助兜底

### `src/novel_pipeline_stable/style_bible_bucket_builder.py`

- 重写 `<style_window>` 序列化逻辑
- 结构化输出 routing hints / pitfall / scalar contracts / evidence index

### `src/novel_pipeline_stable/style_bible_builder.py`

- 重写 `_compact_style_window()`
- 重写 `global_style_signals` 汇总口径

### `src/novel_pipeline_stable/canon_builder.py`

- 升级 `style_index.json` 的统计结构

## 11.2 基本无需改动的文件

### `src/novel_pipeline_stable/client.py`

客户端主体逻辑可以保留。  
真正要改的是配置侧切换到 `json_schema`，而不是继续依赖 `json_object` 模板拼接。

---

## 12. 关键隐患与落地策略

## 12.1 Structured Outputs schema 复杂度风险

隐患：

- OpenAI `strict: true` 的 Structured Outputs 对 JSON Schema 有嵌套层级、属性数和 schema 子集限制
- 如果直接把纯语义分组容器层全部保留下来，schema 很容易在接入前就被拒绝

落地策略：

1. 在正式改流水线前，先新增 `scripts/check_style_window_schema.py`
2. 预检脚本分两层执行：
   - 静态检查：统计嵌套深度、对象属性总数、枚举规模、`additionalProperties: false` 完整性
   - 真请求检查：复用生产链路同样的 Responses + `json_schema` 发送最小 payload 验证 schema 可被 API 接受
3. schema 先主动压扁一层，删除 `StyleWindowNarrativeSignals / StyleWindowExpressionSignals` 这类纯分组容器
4. 如果 schema 仍被拒绝，继续按固定顺序压缩：
   - 先删分组容器层
   - 再合并低价值小对象
   - 最后才压缩 `evidence_ids`

## 12.2 Payload 上下文膨胀风险

隐患：

- 如果把 `scene_summary / style_markers / open_questions` 全塞进 payload，双章窗口下 scene 级辅助信息会迅速膨胀
- 这会让模型更像在读“既有摘要”，而不是直接读原文

落地策略：

1. 把 `scene_catalog` 改成极简 `scene_locator`
2. `scene_locator` 只保留：
   - `source_ref`
   - `chapter_id`
   - `scene_id`
   - `start_anchor`
   - `end_anchor`
3. 不允许携带：
   - `scene_summary`
   - `style_markers`
   - `open_questions`
4. 如果无法稳定切出双 anchor，退化为短 `excerpt_hint`
5. 为 `scene_locator` 加总量预算，确保它始终是“定位信息”，不是“第二份正文”

## 12.3 Big Bang 部署风险

隐患：

- 如果在一次提交里同时改完 schema、extract、router、builder、canon 等全部模块，任何一个小错误都可能引发连锁报错
- 这会让问题定位成本极高

落地策略：

- 不做 runtime 兼容层
- 不做旧 schema fallback
- 但采用**单分支、分阶段、门控式切换**
- 每个阶段都必须先通过局部验收，再进入下一阶段

---

## 13. 切换策略

本方案明确 **不做兼容、不做双轨、不做 adapter**，但也不采用一次性 Big Bang 爆破。

切换方式如下：

1. Phase A：先完成 schema / prompt / preflight
   - 修改 `models.py`
   - 重写 `style_extraction.md`
   - 新增 `scripts/check_style_window_schema.py`
   - 验收标准：schema 能通过静态检查和真实 API preflight
2. Phase B：只接通新的 `extract_style()`
   - 修改 `pipelines.py`
   - 修改配置为 `json_schema`
   - 验收标准：能稳定产出新 `style_window_*.json`
3. Phase C：切换 `style_bible_inputs + router`
   - 修改 `style_bible_inputs.py`
   - 修改 `style_bible_router.py`
   - 验收标准：style window 路由不再以扁平文本拼接为主判据
4. Phase D：切换 builder / canon 汇总层
   - 修改 `style_bible_bucket_builder.py`
   - 修改 `style_bible_builder.py`
   - 修改 `canon_builder.py`
   - 验收标准：bucket memo、source bundle、style index 都能消费新 schema
5. Phase E：整链验证
   - 从 style extract 开始整链重跑
   - 依次执行 `extract-style`、`build-canon`、`build-style-bible`
   - 重点观察 `routing_executability / trace_auditability / worldbook_exportability`
6. 旧 `style_window_*.json` 在切换后统一视为失效产物
7. 不再接受旧 style 结果进入新 V2 链路

---

## 14. 验收标准

重构完成后，至少满足以下标准：

### 14.1 schema 层

- style 输出中不存在旧版 `supporting_evidence` 游离数组
- 所有强规则都带 `evidence_ids`
- 所有 `evidence_ids` 都能在 `evidence_index` 找到闭环
- 所有标量字段 100% 为枚举 token

### 14.2 routing 层

- style window 路由不再以关键词拼接为主
- `axis_hints / bucket_hints / routing_hints` 成为主判据
- `negative_pitfalls` 能显式压制误路由

### 14.3 builder 层

- bucket memo 中 style window 不再是摘要文本块
- local reduce 能直接看到结构化 route signal
- `supporting evidence` 的回指链条完整

### 14.4 质量层

与当前版本相比，重点观察以下指标应显著改善：

- `routing_executability`
- `trace_auditability`
- `worldbook_exportability`
- `rag_atomicity`
- 标量字段污染率

---

## 15. 最终结论

最优解不是“在旧 style extract 上补一个 v2_signals 字段”，也不是“继续保留旧结果再加适配层”。  
最优解是：

**把 style extract 直接重做为 Style Bible V2 的原生信号提取层。**

保留的是：

- `chapter window`
- `manifest / failures / resume`
- 当前稳定的请求调度外壳

重做的是：

- schema
- payload
- prompt
- router 消费方式
- bucket builder 注入方式
- builder / canon 的 style 汇总口径

这样改完以后，`style extract` 就不再是“风格摘要器”，而会变成：

**一个能直接为 Style Bible V2 提供路由、约束、证据和负向边界的结构化风格信号层。**
