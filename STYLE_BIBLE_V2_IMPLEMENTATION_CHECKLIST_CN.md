# Style Bible V2 工程实施清单

更新时间：2026-04-09（已同步至 Phase 5 + 缓存机制接入）

## 1. 目标与结论

本清单用于把当前 `style_bible` 流水线从“固定上限采样 + 单次总结”升级为：

- Semantic Routing / Batching
- CoT + Grounding 的结构化证据绑定
- 动态 Anti-Patterns 注入
- 高对比度 Prompt Bundle
- Coverage / Grounding / Anti-Pattern 感知的 Eval / Judge / Regression

### 当前结论

当前 `style_bible_final.json` 的主要问题不在模型上下文，而在输入端：

- `scene_signal_samples`：1132 -> 24
- `style_window_samples`：135 -> 24
- 单条 sample 内部还会再次裁剪
- `evaluate-style-bible` 目前不会对“输入覆盖率异常低”做硬熔断

因此，V2 的首要目标不是继续微调 `style_bible_synthesis.md`，而是升级：

1. `source_bundle` 的构建逻辑
2. `style_bible` 的中间产物链路
3. 评测体系对覆盖率与血缘追踪的检查

### 当前实施状态（2026-04-09）

- [x] Phase 0-5 主链路已接通：可观测性、semantic router、semantic batching、bucket memos、grounded reducer、anti-pattern 动态注入、cache affinity、`--resume` 已落地。
- [x] `style_bible_final.json` 已升级为 `style-bible-result-v2`，并同步生成 `style_bible_reasoning.json` 与 `style_bible_export_flat.json`。
- [x] evaluator / judge / rules config 已支持 v2 产物、grounding trace 与 anti-pattern 维度。
- [ ] 72 个 Batch 的全量缓存验收尚未完成；目前只有 3 个代表 bucket 的灰度验证结果。

---

## 2. 必守红线

### 红线 A：禁止继续使用单阶段 24/24 截断作为主路径

- 不允许再依赖 `--max-style-windows=24`、`--max-scene-samples=24` 作为默认生产策略。
- 可以保留为 debug / smoke-test 模式，但必须显式标注 `sampling_mode=debug_small`.

### 红线 B：禁止让最终规则脱离证据

- 任何进入最终 `style_bible` 的规则，都必须带有可审计的证据血缘。
- 不允许只在 Prompt 中“要求 grounded”，但产物 schema 无法表达 grounded。

### 红线 C：禁止使用数组序号 JSONPath 做推理挂载

- 不允许再设计 `target_path = narrative_system.engine[0]` 这种脆弱指针。
- 推理层与结果层的绑定必须通过稳定 ID 完成。

### 红线 D：禁止把完整 Anti-Pattern 库一次性塞进 System Prompt

- 必须按 bucket / task 动态注入。
- 必须限制负例注入条数和总 token 预算。

### 红线 E：Eval 必须感知输入覆盖率

- 如果 Bundle Coverage 异常低，Eval 必须直接 Fail。
- 不允许“格式正确但输入残缺”的产物进入 downstream export。

---

## 3. V2 目标架构

### 阶段 A：全量路由索引

输入：

- facts
- style windows
- canon

输出：

- `style_bible_routed_index.json`
- `style_bible_feature_index.json`
- `sampling_report.json`

职责：

- 对全量 `scene` / `style_window` 做特征提取
- 多标签语义归桶
- 计算 coverage、冗余、信息增益

### 阶段 B：Semantic Batching + Bucket Memos

输入：

- routed index
- feature index
- 全局统计

输出：

- `batch_plan.json`
- `bucket_prompt_bundle.xml`
- `bucket_memos/*.json`

职责：

- 按 token budget 和信息增益打包
- 对每个 bucket 生成 grounded memo
- 保持证据 ID 数组不丢失

### 阶段 C：Grounded Reducer

输入：

- bucket memos
- global summaries
- selected anti-pattern examples

输出：

- `style_bible_reasoning.json`
- `style_bible_final.json`
- `style_bible_export_flat.json`

职责：

- 合并 bucket memos
- 生成最终规则
- 输出 reasoning 层与 final 层的稳定血缘绑定

### 阶段 D：Eval / Judge / Regression V2

输入：

- final
- reasoning
- routed index
- sampling report / coverage summary
- gold set

输出：

- coverage-aware eval
- grounding-aware judge
- regression gate

职责：

- 检查 coverage
- 检查 grounded 血缘
- 检查 anti-pattern 防线
- 阻止残缺 bundle 进入 downstream

---

## 4. 四个高危陷阱与硬防线

## 陷阱一：Map-Reduce 阶段的证据链断裂

### 风险

从 `bucket_memos` 合并到 `style_bible_final.json` 时，模型可能保留抽象结论、丢失底层 `source_ref`。

### 强制实现

- [x] 阶段 B 的 memo schema 中，证据字段只能是 `evidence_refs: list[str]`
- [x] `evidence_refs` 只允许绝对 ID，例如 `scene:0141_003`、`0211_0212`
- [x] 禁止在 memo 中写自然语言证据描述替代 ID
- [x] 阶段 C reducer 只允许消费 memo 中的 `evidence_refs`
- [x] 最终规则必须带 `evidence_refs`
- [x] 任何没有 `evidence_refs` 的最终规则直接判为无效

### Schema 约束

推荐阶段 B memo 结构：

```json
{
  "memo_id": "bucket_exam_screening_001",
  "bucket_id": "exam_screening",
  "rule_candidates": [
    {
      "candidate_id": "exam_screening_rule_01",
      "text": "考试首先承担预算化筛选功能，而不是单纯证明实力。",
      "evidence_refs": ["0141_0142", "scene:0141_003", "scene:0142_002"]
    }
  ]
}
```

### Eval / Judge 检查

- [x] `final_rule.evidence_refs` 必须非空
- [x] `final_rule.evidence_refs` 必须全部存在于 routed index / bundle reference pool
- [x] `final_rule.evidence_refs` 必须来自 reasoning 层或 memo 层，不能凭空生成
- [x] 发现游离规则时直接 Fail

---

## 陷阱二：CoT 并行输出时的指针漂移

### 风险

如果 reasoning 层用 JSONPath 或数组下标去指向 final 层，数组顺序一变就会崩。

### 强制实现

- [x] 废弃 `target_path = narrative_system.engine[0]` 这类绝对路径设计
- [x] reasoning 层只维护稳定 `reasoning_id`
- [x] final 层规则对象新增 `"_reasoning_ref"`
- [x] final 层规则对象同时携带 `rule_id`
- [x] 每个 `rule_id` 与 `reasoning_id` 的映射必须稳定可校验

### 推荐血缘结构

#### reasoning 层

```json
{
  "reasoning_id": "engine_01",
  "bucket_id": "resource_pressure",
  "observed_commonality": "角色在关键推进前总先结算资源与资格成本。",
  "mechanism_inference": "资源结算先于成长抉择，是稳定叙事引擎。",
  "downstream_constraint": "后续 RP 不可写成无成本机缘。",
  "evidence_refs": ["scene:0039_001", "0141_0142", "0211_0212"]
}
```

#### final 层

```json
{
  "rule_id": "engine_rule_01",
  "text": "使用“资源结算先于成长抉择”的推进结构。",
  "_reasoning_ref": "engine_01",
  "evidence_refs": ["scene:0039_001", "0141_0142", "0211_0212"]
}
```

### 兼容性评估

当前 `style-bible-result-v1` 的大量字段是 `list[str]`，无法挂载 `_reasoning_ref`。

因此建议：

- [x] 升级为 `style-bible-result-v2`
- [x] rule-bearing arrays 改成 `list[StyleBibleRuleItem]`
- [x] 同时输出一个兼容旧下游的 `style_bible_export_flat.json`

### Eval / Judge 检查

- [x] 所有 `_reasoning_ref` 必须能在 reasoning 层找到
- [x] 不允许 dangling refs
- [ ] 不允许 orphan reasoning entries 大量存在
- [x] `final.evidence_refs` 必须与 `reasoning.evidence_refs` 精确一致或满足严格透传规则

---

## 陷阱三：反向约束库的上下文膨胀

### 风险

Anti-Pattern 库越来越大后，如果全部塞进 System Prompt，会导致 token 膨胀和注意力失焦。

### 强制实现

- [x] Anti-Pattern 库版本化存储
- [x] Prompt 组装时按 bucket 动态注入
- [x] 每次只注入与当前任务高相关的错误码
- [x] 为负例注入设置硬 token budget
- [x] Reducer 只注入全局高频错误，不注入全部历史错误

### 推荐文件

- [x] `prompts/style_bible_antipatterns_cn.md`
- [x] `config/style_bible_antipattern_registry.json`
- [x] `src/novel_pipeline_stable/style_bible_prompt_assembler.py`

### 推荐 registry 结构

```json
{
  "VAGUE_ROUTING": {
    "tags": ["routing", "generic", "resource_pressure"],
    "bad_output": "当剧情进入紧张阶段时触发。",
    "why_bad": "没有触发条件，没有路由目标，没有可观测信号。",
    "good_pattern": "当角色债务压力上升且面临高阶层压迫时，路由到资源危机节点。"
  }
}
```

### 动态注入策略

- [x] 阶段 A 的 bucket 标签作为 Anti-Pattern 过滤条件
- [x] 每个 bucket 最多注入 4-8 条负例
- [x] 每个 prompt 的负例预算建议控制在 1200-2000 tokens 内
- [x] reducer 仅注入跨 bucket 通用错误，如 `GENERIC_MECHANISM`、`KEYWORD_STUFFING`

### Eval / Judge 检查

- [x] 新增 `anti_pattern_resistance`
- [x] 明确检查 `GENERIC_MECHANISM`
- [x] 明确检查 `VAGUE_ROUTING`
- [x] 明确检查 `KEYWORD_STUFFING`
- [x] 明确检查 `UNGROUNDED_WORLDBOOK`

---

## 陷阱四：评测体系的唯结果论盲区

### 风险

如果 Eval 只看最终 JSON 是否“像样”，而不检查 routed / batched / selected 的实际覆盖率，那么残缺输入也可能过关。

### 强制实现

- [x] Eval V2 新增 `bundle_coverage` 维度
- [x] Eval V2 读取 `sampling_report.json` / coverage summary
- [x] 对 routed / batched 覆盖做硬门槛
- [x] 当 coverage 低于阈值时直接 Fail，不给 downstream export

### 建议熔断指标

- [x] `scene_routed_ratio < 0.95` -> Fail
- [x] `style_window_routed_ratio < 0.95` -> Fail
- [x] `scene_in_any_batch_ratio < 0.70` -> Fail
- [x] `style_window_in_any_batch_ratio < 0.90` -> Fail
- [ ] 任一核心轴线 `axis_coverage_ratio == 0` -> Fail
- [ ] 任一核心 bucket `selected_item_count == 0` -> Fail

### 说明

V2 不应再以“选了多少 raw scene”作为唯一目标，而应以“是否被语义路由、是否进入 batch、是否进入 memo、是否进入 final grounding 链”作为完整覆盖路径。

---

## 5. Schema 决策

## 5.1 推荐采用 V2 双产物结构

- [x] `style_bible_reasoning.json`
- [x] `style_bible_final.json`
- [x] `style_bible_export_flat.json`

### 为什么不是只保留一个 final

- reasoning 层是审计资产，不适合直接给 downstream 使用
- final 层是消费资产，需要紧凑、稳定
- flat export 负责兼容当前字符串型下游

## 5.2 推荐新增模型

### 新模型

- [x] `StyleBibleRuleItem`
- [x] `StyleBibleReasoningEntry`
- [x] `StyleBibleReasoningBundle`
- [x] `StyleBibleResultV2`
- [x] `StyleBibleCoverageReport`
- [x] `StyleBibleBatchPlan`
- [x] `StyleBibleBucketMemo`

### 不建议

- [ ] 不建议把长自由文本 CoT 直接塞进 final
- [ ] 不建议继续使用 `list[str]` 承载最终规则并企图外挂血缘
- [ ] 不建议让 reasoning 层反向锁定 final 数组下标

---

## 6. 输入结构升级

## 6.1 Source of Truth 仍然是 JSON

- [ ] 保持 `style_bible_source_bundle.json` 作为源数据
- [ ] 保持 routed index / feature index / sampling report 用 JSON 持久化

## 6.2 Prompt Consumption 使用高对比度结构

- [ ] 新增 `style_bible_prompt_bundle.xml`
- [ ] XML 仅作为模型消费视图，不作为源数据真相

### 推荐 XML 区块

- [ ] `<global_stats>`
- [ ] `<bucket id="...">`
- [ ] `<scene ref="scene:...">`
- [ ] `<style_window ref="...">`
- [ ] `<anti_patterns>`
- [ ] `<coverage_summary>`

---

## 7. 最大信息熵采样 / Batching 方案

## 7.1 路由特征

每个 `scene` / `style_window` 至少提取：

- [ ] `entity_density`
- [ ] `relationship_change_density`
- [ ] `institution_density`
- [ ] `resource_pressure_density`
- [ ] `body_modification_density`
- [ ] `dark_humor_signal`
- [ ] `sales_pitch_signal`
- [ ] `contract_signal`
- [ ] `conflict_intensity`
- [ ] `chapter_position`
- [ ] `evidence_density`

## 7.2 多标签分桶

- [ ] 每个 item 可进入多个 bucket
- [ ] 每个 item 必须保留 `bucket_membership_confidence`
- [ ] 不允许单标签硬归类导致覆盖缺失

## 7.3 Batch 打包评分

建议初始评分函数：

`batch_score = 0.30 * axis_novelty + 0.20 * evidence_density + 0.15 * entity_novelty + 0.15 * conflict_intensity + 0.10 * institution_density + 0.10 * voice_novelty - redundancy_penalty`

### 打包规则

- [ ] 优先补齐 bucket 内未覆盖轴线
- [ ] 对章节过度集中的 item 降权
- [ ] 对重复证据链降权
- [ ] 对高冲突但低信息 item 设保护阈值
- [ ] 对高信息但低显著 item 设保底选入

---

## 8. 代码实施清单

## Phase 0：可观测性与合同

- [x] 新增 `src/novel_pipeline_stable/style_bible_contracts.py`
- [x] 新增 `StyleBibleCoverageReport` schema
- [x] 在 `style_bible_builder.py` 中输出 `sampling_report.json`
- [x] 在 `run_manifest.json` 中补充 `sampling_mode`、`routing_mode`、`batching_mode`
- [x] 从 API `usage` 中解析 `prompt_tokens_details.cached_tokens` / `input_tokens_details.cached_tokens`
- [x] 在 `sampling_report.json` 中输出 `prompt_tokens`、`cached_tokens`、`total_tokens`
- [x] 在 `sampling_report.json` 中输出核心缓存指标 `overall_cache_hit_ratio`
- [x] 在 `sampling_report.json` 中输出 `ttft_summary`

目标文件：

- `src/novel_pipeline_stable/models.py`
- `src/novel_pipeline_stable/style_bible_builder.py`
- `src/novel_pipeline_stable/style_eval_contract.py`

验收：

- [x] 运行后可看到 coverage report
- [x] report 中能区分 routed / batched / memoed / reduced 覆盖率
- [x] report 中能区分缓存命中指标与 TTFT 摘要

## Phase 1：Semantic Router

- [x] 新增 `src/novel_pipeline_stable/style_bible_router.py`
- [x] 从 facts / style / canon 构建 feature index
- [x] 生成 `style_bible_routed_index.json`
- [x] 支持多标签 bucket membership

目标文件：

- `src/novel_pipeline_stable/style_bible_router.py`
- `src/novel_pipeline_stable/cli.py`

CLI 建议：

- [x] `route-style-bible-inputs`

验收：

- [x] `scene_routed_ratio >= 0.95`
- [x] `style_window_routed_ratio >= 0.95`

## Phase 2：Batch Planner

- [x] 新增 `src/novel_pipeline_stable/style_bible_batching.py`
- [x] 基于 routed index 生成 `batch_plan.json`
- [x] 支持 token budget packing
- [x] 支持 diversity / novelty / redundancy 控制
- [x] `batch_plan.json` 按 `bucket_id` 排序 / 分组，并产出 `bucket_execution_order`
- [x] batch 级别写入 `cache_affinity_key = bucket_id`

目标文件：

- `src/novel_pipeline_stable/style_bible_batching.py`
- `config/style_bible_batching_rules.toml`
- `src/novel_pipeline_stable/cli.py`

CLI 建议：

- [x] `plan-style-bible-batches`

验收：

- [x] 每个核心 bucket 至少 1 个 batch
- [ ] `style_window_in_any_batch_ratio >= 0.90`
- [ ] `scene_in_any_batch_ratio >= 0.70`

## Phase 3：Bucket Memo Synthesis

- [x] 新增 `prompts/style_bible_bucket_synthesis.md`
- [x] 新增 `src/novel_pipeline_stable/style_bible_bucket_builder.py`
- [x] 生成 `bucket_memos/*.json`
- [x] memo 只允许绝对 ID 数组作为证据
- [x] bucket synthesis prompt 经 `style_bible_prompt_assembler.py` 按“静态 -> 动态”顺序组装
- [x] Prompt 前半部分禁止插入 `batch_id` / 时间戳 / Request UUID 等动态标识
- [x] prompt 红线强制 `JSON only`、`evidence_refs` 只允许复制 XML `ref`
- [x] 拒绝策略已接通：没有强机制时输出 `rule_candidates: []`
- [x] builder 并发固定为 4-7 的受控区间
- [x] `build-style-bible-bucket-memos --resume` 可跳过已有 `bucket_memos/*.json` 并恢复 request metrics
- [x] ThreadPoolExecutor 已实现缓存亲和性：同一 bucket 的 batch 固定分配到同一 worker slot / gateway 连续执行

目标文件：

- `prompts/style_bible_bucket_synthesis.md`
- `src/novel_pipeline_stable/style_bible_bucket_builder.py`
- `src/novel_pipeline_stable/models.py`

验收：

- [x] 每个 memo 的 rule candidate 都有 `evidence_refs`
- [x] `evidence_refs` 全部能在 routed index 中解析

## Phase 4：Grounded Reducer

- [x] 新增 `prompts/style_bible_reduce.md`
- [x] 新增 `src/novel_pipeline_stable/style_bible_reducer.py`
- [x] 输出 reasoning + final + flat export
- [x] final 规则对象携带 `_reasoning_ref`
- [x] final 规则对象携带 `evidence_refs`

目标文件：

- `prompts/style_bible_reduce.md`
- `src/novel_pipeline_stable/style_bible_reducer.py`
- `src/novel_pipeline_stable/models.py`

验收：

- [x] 不存在无 `_reasoning_ref` 的 final 规则
- [x] 不存在无 `evidence_refs` 的 final 规则
- [ ] `final -> reasoning -> evidence` 链可完整追踪

## Phase 5：Anti-Patterns 动态注入

- [x] 新增 `prompts/style_bible_antipatterns_cn.md`
- [x] 新增 `config/style_bible_antipattern_registry.json`
- [x] 新增 `src/novel_pipeline_stable/style_bible_prompt_assembler.py`
- [x] 为 bucket synthesis / reducer 提供按需 Few-Shot 注入
- [x] 负例预算硬编码为 1200-2000 tokens
- [x] 每个 bucket 最多注入 4-8 条最高相关负例
- [x] reducer 仅注入跨 bucket 通用负例

目标文件：

- `prompts/style_bible_antipatterns_cn.md`
- `config/style_bible_antipattern_registry.json`
- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`

验收：

- [x] 单次注入的 anti-pattern token 预算受控
- [x] reducer 不会注入全量负例库

## Phase 6：Eval / Judge / Regression V2

- [x] 升级 `style_bible_evaluator.py`
- [x] 升级 `style_bible_judge.py`
- [ ] 升级 `style_bible_regression.py`
- [x] 新增 `bundle_coverage`
- [x] 新增 `grounding_trace_integrity / grounding_consistency`
- [x] 新增 `anti_pattern_resistance`

目标文件：

- `src/novel_pipeline_stable/style_bible_evaluator.py`
- `src/novel_pipeline_stable/style_bible_judge.py`
- `src/novel_pipeline_stable/style_bible_regression.py`
- `config/style_bible_eval_rules.toml`
- `config/style_bible_judge_rules.toml`
- `config/style_bible_regression_rules.toml`

验收：

- [x] coverage 异常低时 Eval 直接 Fail
- [x] dangling `_reasoning_ref` 直接 Fail
- [x] ungrounded final rules 直接 Fail

---

## 9. 重点文件改动路径

### 需要修改

- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_builder.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_judge.py`
- [ ] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_regression.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_contracts.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_eval_contract.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\client.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\cli.py`
- [x] `D:\card\novel_pipeline\config\style_bible_eval_rules.toml`
- [x] `D:\card\novel_pipeline\config\style_bible_judge_rules.toml`
- [x] `D:\card\novel_pipeline\config\style_bible_regression_rules.toml`
- [ ] `D:\card\novel_pipeline\prompts\style_bible_synthesis.md`

### 建议新增

- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_router.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_batching.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_bucket_builder.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`
- [x] `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_prompt_assembler.py`
- [x] `D:\card\novel_pipeline\prompts\style_bible_bucket_synthesis.md`
- [x] `D:\card\novel_pipeline\prompts\style_bible_reduce.md`
- [x] `D:\card\novel_pipeline\prompts\style_bible_antipatterns_cn.md`
- [x] `D:\card\novel_pipeline\config\style_bible_batching_rules.toml`
- [x] `D:\card\novel_pipeline\config\style_bible_antipattern_registry.json`

---

## 10. 最小可行落地顺序

推荐顺序：

1. Phase 0：coverage report
2. Phase 1：router
3. Phase 2：batch planner
4. Phase 3：bucket memo
5. Phase 4：grounded reducer + v2 schema
6. Phase 5：dynamic anti-pattern injection
7. Phase 6：eval/judge/regression v2

### 不推荐顺序

- 先把 `max-scene-samples` 从 24 提到 200，再观察
- 先给 prompt 塞更多负例
- 先调 judge 权重而不修输入 coverage

这些都只能缓解症状，不能解决源头问题。

---

## 11. 验收门槛

### 功能性验收

- [x] style bible 生产路径支持 semantic routing / batching
- [x] reducer 输出带稳定血缘
- [x] anti-pattern 注入支持动态选择
- [x] eval/judge 感知 coverage / grounding

### 数据性验收

- [x] `scene_routed_ratio >= 0.95`
- [x] `style_window_routed_ratio >= 0.95`
- [ ] `style_window_in_any_batch_ratio >= 0.90`
- [ ] `scene_in_any_batch_ratio >= 0.70`
- [ ] `grounded_final_rule_ratio == 1.0`
- [x] `dangling_reasoning_ref_count == 0`
- [x] `ungrounded_rule_count == 0`
- [ ] `overall_cache_hit_ratio >= 0.70`
- [ ] 同 bucket 后续请求的 TTFT 相比首个请求下降 50% 以上

当前 3 bucket 灰度值：

- `overall_cache_hit_ratio = 0.0153`
- `dark_humor` 的 TTFT 降幅为 `41%`
- `institutional_pipeline` 与 `resource_pressure` 的 follow-up TTFT 未出现下降
- 因此缓存验收仍未通过，必须在 72 batch 全量运行中继续复核

### 质量验收

- [ ] `judge-style-bible` 不再依赖手工修改产物刷分
- [ ] `compare-style-runs` 能在 grounded 产物之间拉开差距
- [ ] `regress-style-quality` 能对 coverage / grounding 的退化做熔断

---

## 12. 实施建议

### 推荐策略

- 先做可观测性，再做 batching，不要反过来
- 先锁 Schema，再写 Prompt，不要反过来
- 先把血缘打通，再谈风格分数优化

### 现实评估

这套方案是可行的，但它是一次明确的 `v2` 架构升级，不是小修补。

其本质是：

- 从单步 synthesis 升级为分层 synthesis
- 从字符串规则升级为可追踪规则对象
- 从“结果评审”升级为“输入覆盖 + 推理血缘 + 结果质量”的联合评审

如果要稳妥推进，建议以 `style-bible-result-v2` 并行落地，不要直接覆盖现有 v1 生产链。
