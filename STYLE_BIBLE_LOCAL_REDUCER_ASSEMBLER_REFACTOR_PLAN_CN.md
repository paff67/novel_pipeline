# Style Bible V2 Local Reducer + Assembler 重构方案

## 1. 背景与目标

当前 Style Bible V2 的分层归约存在一个根本性 I/O 反模式：

- Local Reducer 只负责单桶局部归约，却被要求直接输出完整 `StyleBibleResultV2`
- 这会把一个 300+ 属性、7 层以上的终态蓝图强行塞进单桶任务
- 结果是模型注意力被终态骨架稀释，`text` / `trigger` / `constraint` 的语义锐度下降
- 下游再用 fallback 与 merge 兜底，最终把“模型没提出来”的问题伪装成“系统补出来了”

本次重构目标不是兼容过渡，而是一步到位把 Local Reducer 改成：

1. 单桶只输出强约束的 partial rows
2. 由 Python Assembler 负责把 partial rows 挂载回 `StyleBibleResultV2`
3. 用 `SURFACE_PATH_SPECS` 作为 Schema、校验、归并策略的单一真相源
4. 彻底切断“为凑完整度而注入高风险 fallback”的旧路径
5. 对 sparse bucket、merge conflict、failed bucket 建立结构化降级元数据

## 2. 总体架构

旧链路：

`bucket memo -> Local Reducer -> full StyleBibleResultV2 -> hierarchical merge -> fallback -> final`

新链路：

`bucket memo -> Local Reducer -> LocalRuleRow[] -> Python Assembler -> StyleBibleResultV2 -> final`

核心原则：

- LLM 只负责局部 grounded 提取，不负责装树
- Python 负责确定性的装配、去重、冲突仲裁、降级记录
- Guardrail 不再审查“是否补满骨架”，只审查“是否 grounded 且非空”

## 3. Single Source of Truth

新增中心注册表 `SURFACE_PATH_SPECS`，作为以下能力的统一来源：

- 合法 `surface_path`
- 路径卡位类型：`scalar` / `list`
- 路径必须字段
- 路径归并策略
- 路径容量上限
- 路由类冲突仲裁策略
- 高风险路径标记

建议策略：

- `scalar_pick_one`
  - 用于 `narrative_system.perspective`、`distance`、`temporality`、`voice_contract.narrator_voice`、`inner_monologue_mode`
- `rule_dedupe_union`
  - 用于大部分 trigger/constraint 型规则路径
- `rule_dedupe_aggressive`
  - 用于 `worldbook_binding.rag_worthy`、`worldbook_binding.worldbook_worthy`、`worldbook_binding.routing_hints`
- `append_capped`
  - 用于允许保留多样性的自由规则列表

后续如果要调 `max_items` 或路径开关，优先做成配置可覆盖，但第一阶段先由代码注册表托底。

## 4. Local Reducer 新 I/O 契约

新增模型：

- `SurfacePath`
- `LocalRuleRow`
- `StyleBibleLocalPartialFinal`
- `StyleBibleLocalReducerOutput`

目标形态：

```json
{
  "_scratchpad_cross_validation": [],
  "reasoning": {
    "reasoning_version": "v2.0",
    "style_id": "style.demo",
    "scope": "novel",
    "entries": []
  },
  "final": {
    "style_id": "style.demo",
    "scope": "novel",
    "rule_rows": [
      {
        "rule_id": "resource_pressure__engine_01",
        "surface_path": "narrative_system.engine",
        "text": "当角色推进关键动作时，必须先结算成本和回款窗口，再执行动作。",
        "trigger": "当角色要推进关键动作时",
        "constraint": "必须先结算成本和回款窗口，再执行动作",
        "_reasoning_ref": "reasoning_01",
        "evidence_refs": ["scene:0002_001"],
        "anti_pattern_codes": ["none"]
      }
    ]
  }
}
```

关键约束：

- `surface_path` 必须是强类型枚举，不允许自由字符串
- 每条 row 必须带 `_reasoning_ref`
- 每条 row 必须带 `evidence_refs`
- `rule_id` 在单次 local output 内必须唯一
- routing/worldbook 路径必须强制 `query_feature_matcher + route_target_action`
- negative/pitfall 路径必须强制 `forbidden_action + correction_guideline`

## 5. Validator 设计

### 5.1 行级校验

`LocalRuleRow` 做以下硬校验：

- `surface_path` 必须属于 `SURFACE_PATH_SPECS`
- 路径必须字段不能为空
- `rule_id` 不能为空
- `_reasoning_ref` 不能为空
- `evidence_refs` 不能为空

### 5.2 输出级校验

`StyleBibleLocalReducerOutput` 做以下硬校验：

- `final.rule_rows[*]._reasoning_ref` 必须都能在 `reasoning.entries` 中找到
- `rule_id` 不允许重复
- 允许空输出，但只有在 `reasoning.entries` 与 `rule_rows` 同时为空时才视为 sparse；不能一边有 reasoning 一边没有规则

Fail Fast 原则：

- 宁可在 model validate 阶段重试
- 也不要把悬空引用、脏 path、缺字段 row 泄漏给 assembler

## 6. Client 改造

`client.py` 增加显式控制面参数，例如：

- `response_format_mode`
- `output_contract_mode`

目标：

- Local Reducer 可显式选择 `json_schema`
- Local Reducer 可显式关闭自动追加 blueprint 文本
- 禁止继续靠“根据 response_model 类型猜是否 append blueprint”这种隐式逻辑

本次本地 reducer 的推荐调用：

- `response_model = StyleBibleLocalReducerOutput`
- `response_format_mode = "json_schema"`
- `output_contract_mode = "none"`

这样可以同时做到：

- schema 更小
- prompt 更干净
- 不再把完整终态 blueprint 粘到 system prompt 尾部

## 7. Python Assembler

Assembler 的职责：

1. 按 `surface_path` 收集 row
2. 按 `SURFACE_PATH_SPECS` 归并
3. 统一落到 `StyleBibleResultV2`
4. 补充 `metadata.degradation_status`
5. 输出冲突与降级记录

### 7.1 归并策略

#### `scalar_pick_one`

- 只保留一个候选
- 优先级顺序：
  - critical bucket
  - evidence 数量
  - 结构字段完整度
  - bucket 顺序

#### `rule_dedupe_union`

- 用 `text` / `trigger+constraint` / `matcher+action` 等别名聚类
- 每组选最佳规则为主规则
- `evidence_refs` 与 `anti_pattern_codes` 做并集

#### `rule_dedupe_aggressive`

- 对 routing/worldbook 路径用更激进的 matcher 聚类
- 如果同一 matcher 对应多个冲突的 `route_target_action`：
  - 不做静默覆盖
  - 记录 `assembler_conflicts`
  - 默认丢弃整组，防止错误路由流入终态

#### `append_capped`

- 按优先级顺序追加
- 去掉明显重复项
- 超出上限后截断

## 8. Guardrail 与 Fallback 重定义

### 8.1 旧问题

旧 guardrail 实际在审查“完整骨架”而不是“grounded 有效输出”：

- 强制 list path 达到最小条数
- 强制 optional scalar 必填
- 强制 fallback 把空白 section 人工补满

这会制造三类毒性产物：

- 虚构规则
- 文本拼接污染
- Judge 误以为系统已经稳定，实际只是 fallback 成功

### 8.2 新规则

新的 reducer guardrail 只审查：

- `reasoning.entries` 非空
- 最终规则数非空
- `reduced_ref_count` 非空
- `supporting_evidence` 非空

不再因为：

- 缺若干 section
- 某些 scalar 缺失
- 高风险路径条数不足

而直接判全局失败。

### 8.3 高风险 Fallback

高风险路径不再允许文本 fallback：

- `worldbook_binding.rag_worthy`
- `worldbook_binding.worldbook_worthy`
- `worldbook_binding.routing_hints`
- `expression_system.characterization_rules`
- `negative_rules`

局部证据不够时，宁可为空，也不要伪造。

## 9. 降级元数据

在 `StyleBibleResultV2` 增加：

```json
{
  "metadata": {
    "degradation_status": {
      "mode": "complete | degraded",
      "skipped_sparse_buckets": [],
      "failed_bucket_ids": [],
      "assembler_conflicts": []
    }
  }
}
```

用途：

- Judge 可根据 `mode=degraded` 自动切换 partial run 判定
- 报告层可明确说明哪些 bucket 是 sparse/failed/conflicted
- 避免“明明缺失很多 section，却在外观上像完整成功”

## 10. Prompt 改造

`style_bible_local_reduce.md` 需要同步改成 partial row 导向：

- 强调 LLM 只输出 `final.rule_rows`
- 强调 row 必须指明 `surface_path`
- 强调 routing/worldbook/negative 的结构化字段
- 强调不许为了“像最终 JSON”而补齐其他 section

## 11. 回归测试矩阵

### 11.1 契约测试

- 非法 `surface_path` 必须失败
- dangling `_reasoning_ref` 必须失败
- duplicate `rule_id` 必须失败
- routing row 缺 `route_target_action` 必须失败
- negative row 缺 `correction_guideline` 必须失败

### 11.2 Assembler 测试

- `scalar_pick_one` 只保留最佳标量
- `rule_dedupe_union` 会并集证据
- `append_capped` 会截断且保持优先级
- `rule_dedupe_aggressive` 遇到冲突 action 会丢弃并记录 conflict

### 11.3 Hierarchical Reduce 测试

- 正常双桶 local reduce 可产出最终 JSON
- 非关键 sparse bucket 不应拖垮主链
- critical bucket 真失败应熔断
- final JSON 应写入 `metadata.degradation_status`

## 12. 三条隐患与落地策略

### 12.1 OpenAI `json_schema` 深度与复杂度限制

隐患：

- Structured Outputs 对 schema 深度、属性数、组合复杂度都有硬限制
- 如果 local reducer 继续输出深树，哪怕逻辑正确也会被 API 直接拒绝

策略：

1. Local Reducer 只使用轻量 `StyleBibleLocalReducerOutput`
2. 新增独立 schema 检查脚本，对 local reducer schema 做 API 侧验收
3. 若仍超限，优先进一步压扁中间层，而不是回退到 full blueprint

### 12.2 Payload 上下文膨胀

隐患：

- 如果把过多 scene summary / markers / open questions 塞入 local reduce payload，模型会被摘要噪音牵走

策略：

1. Local reduce 继续以 bucket memo 为主输入
2. 如果后续要补 scene locator，只带 `scene_id + excerpt_hint`
3. 禁止把冗长 fact summary 当作 local reducer 的主阅读对象

### 12.3 避免 Big Bang 部署

隐患：

- 同一轮里同时改模型、prompt、client、reducer、judge、report，容易因为一个小 bug 连环翻车

策略：

1. 先落文档与 SSOT 注册表
2. 再切 Local Reducer 新契约
3. 再接 Python Assembler
4. 再切 Guardrail / degradation metadata
5. 最后补回归测试并跑 Mini Live / Full Live

## 13. 本轮实施范围

本轮代码改造聚焦：

- `SURFACE_PATH_SPECS`
- `LocalRuleRow` / `StyleBibleLocalReducerOutput`
- `client.py` 的显式 output contract 控制
- `style_bible_reducer.py` 的 local partial output + assembler
- `metadata.degradation_status`
- 契约测试与装配器回归测试

不在本轮一次性切入：

- style extract 全链路 scene join
- judge 提示词与评分逻辑大改
- 外部配置化覆盖全部 surface path 参数

## 14. 验收标准

满足以下条件视为本轮重构完成：

1. Hierarchical local reduce 不再要求单桶输出完整终态树
2. 单桶输出能够通过 Python assembler 正确挂回 `StyleBibleResultV2`
3. routing/worldbook 冲突不会被静默吞并
4. sparse bucket 会被结构化记录，而不是伪造规则
5. 回归测试覆盖 strict typing、dangling refs、merge、conflict、degraded mode
