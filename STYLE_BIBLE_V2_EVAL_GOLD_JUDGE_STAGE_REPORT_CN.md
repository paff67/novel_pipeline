# Style Bible V2 Eval / Gold Set / Judge 阶段改造报告

## 1. 本轮范围

本轮完成了三块升级，并做了两段验证：

1. Eval V2：从“查格式”升级为“查 coverage + grounding lineage 的硬闸门”。
2. Gold Set V2：从“全书统查”升级为“按 bucket / batch / trace 靶向基准”。
3. Judge V2：从“只看结果像不像”升级为“看 trace、看推理、看 anti-pattern 抗污染”。

验证分成两段：

1. 先对现有灰度产物做 smoke，确认新 `eval / judge` 模块本身可运行。
2. 再做一次真实 3-bucket 跑批，验证升级链路在真实请求下的表现。

---

## 2. 已完成改造

### 2.1 Eval V2

已完成：

- `src/novel_pipeline_stable/style_bible_evaluator.py`
  - 接入 `style_bible_coverage_report.json`。
  - `bundle_coverage` 改为硬熔断：
    - `scene_routed_ratio`
    - `style_window_routed_ratio`
    - `scene_in_any_batch_ratio`
    - `style_window_in_any_batch_ratio`
    - `core_axis_ids` batched 覆盖为 0
    - `core_bucket_ids` batched 覆盖为 0
  - `grounding_trace_integrity` 强制读取 `style_bible_reduce_trace.json`，并校验：
    - final rule 的 `_reasoning_ref`
    - `evidence_refs`
    - `supporting_evidence[*].source_ref`
    - claim -> trace ref pool 对齐
  - report 升级为 `style-bible-eval-v2`。
  - report source metadata 新增：
    - `coverage_report_file`
    - `reduce_trace_file`

- `src/novel_pipeline_stable/style_bible_builder.py`
  - 保持输出 `sampling_report.json` 的同时，同步写出 `style_bible_coverage_report.json`，供 Eval V2 使用。

- `config/style_bible_eval_rules.toml`
  - 新增 `[coverage_targets]`
  - 显式声明 `core_axis_ids`
  - 显式声明 `core_bucket_ids`

### 2.2 Gold Set V2

已完成：

- 新增默认 V2 Gold Set：
  - `data/eval/style_gold_set/v2/index.json`
  - `data/eval/style_gold_set/v2/cases/*.json`

当前 V2 Gold Set 特征：

- 按 bucket 靶向，而不是按整本书泛查。
- case 粒度升级为：
  - `bucket_targets`
  - `batch_targets`
  - `must_hit_refs`
  - `trace_expectations`
  - `anti_pattern_watchlist`
  - `forbidden_outputs`

本轮先落了 6 个代表性 case，覆盖：

- `resource_pressure`
- `institutional_pipeline`
- `dark_humor`

### 2.3 Judge V2

已完成：

- `src/novel_pipeline_stable/style_bible_judge.py`
  - `GoldSetCase` 扩展支持 V2 字段：
    - `bucket_targets`
    - `batch_targets`
    - `must_hit_refs`
    - `forbidden_outputs`
    - `anti_pattern_watchlist`
    - `trace_expectations`
  - Judge rules 新增 anti-pattern registry 加载。
  - 新增维度 `trace_auditability`：
    - 读取 `style_bible_reasoning.json`
    - 读取 `style_bible_reduce_trace.json`
    - 校验 case 目标 reasoning id / expected refs / trace ref pool / final carry-through
  - `anti_pattern_resistance` 升级：
    - 继续查 `GENERIC_MECHANISM / VAGUE_ROUTING / KEYWORD_STUFFING / UNGROUNDED_WORLDBOOK`
    - 接入 case 级 `forbidden_outputs`
    - 接入 registry 元数据，便于审计
  - report 升级为 `style-bible-judge-v2`
  - report source metadata 新增：
    - `reasoning_file`
    - `reduce_trace_file`

- `config/style_bible_judge_rules.toml`
  - 新增 `trace_auditability` 权重与阈值
  - 新增 `forbidden_output_similarity`
  - 新增 `[resources] anti_pattern_registry_file`

- `config/style_bible_regression_rules.toml`
  - `critical_dimensions` 新增 `trace_auditability`

- `src/novel_pipeline_stable/cli.py`
  - Judge 默认 gold set 切到 `v2/index.json`

---

## 3. 代码级验证

已完成编译验证：

- `python -m compileall D:\card\novel_pipeline\src\novel_pipeline_stable`

结果：

- 通过，无语法错误。

---

## 4. Smoke 验证

先用既有灰度产物验证新模块本身是否可运行：

- 输入：
  - `D:\card\novel_pipeline\data\smoke\style_bible_phase345_gray`
- Eval V2 输出：
  - `D:\card\novel_pipeline\data\smoke\style_bible_phase345_gray_eval_v2`
- Judge V2 输出：
  - `D:\card\novel_pipeline\data\smoke\style_bible_phase345_gray_judge_v2`

结果：

- Eval V2
  - `overall_score = 72.64`
  - `status = fail`
- Judge V2
  - `overall_score = 74.9183`
  - `status = warn`

结论：

- 新 `eval / judge / gold set v2` 模块本身是可运行的。
- `Judge V2` 的新维度 `trace_auditability` 已正常参与计分。
- 这一步证明“升级后的审查器能工作”，不是代码挂死或 schema 不通。

---

## 5. 真实跑批验证

### 5.1 跑批范围

真实跑批范围锁定 3 个代表性 bucket：

- `resource_pressure`
- `institutional_pipeline`
- `dark_humor`

上游 phase0/1 输入复用：

- `D:\card\novel_pipeline\data\smoke\style_bible_phase01_builder\style_bible_routed_index.json`
- `D:\card\novel_pipeline\data\smoke\style_bible_phase01_builder\batch_plan.json`
- `D:\card\novel_pipeline\data\smoke\style_bible_phase01_builder\style_bible_source_bundle.json`

真实跑批输出根目录：

- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2`

### 5.2 实际请求情况

桶内并发配置：

- `max_concurrency = 4`

真实 batch 请求结果：

- 成功 batch：10 个
  - `dark_humor__b01`
  - `dark_humor__b02`
  - `dark_humor__b03`
  - `institutional_pipeline__b01`
  - `institutional_pipeline__b02`
  - `institutional_pipeline__b03`
  - `resource_pressure__b01`
  - `resource_pressure__b02`
  - `resource_pressure__b03`
  - `resource_pressure__b04`

- 失败 batch：3 个
  - `dark_humor__b04`
    - `503 / 503`
  - `institutional_pipeline__b04`
    - `503 / 503`
  - `resource_pressure__b05`
    - `400 / 400`

缓存命中统计：

- 成功请求 `prompt_tokens = 83648`
- `cached_tokens = 0`
- `total_tokens = 149844`
- `prompt_cache_hit_ratio = 0.0`

说明：

- 这次真实跑批没有拿到理想缓存命中。
- 主要原因不是 prompt assembler 代码退化，而是本次真实执行在 batch 级就被上游网关中断，很多同 bucket 的后续请求还没形成稳定可复用前缀复访。

### 5.3 实际 fallback

由于上游 `503` 和单个 batch `400` 使 bucket 级 synthesis 无法闭环，本轮做了一个明确标注的保守兜底：

- 从本地 `_request_cache` 中读取已经成功返回的真实 batch memo。
- 使用现有 builder 内部的本地 merge 逻辑，把成功 batch memo 合并成 3 个 bucket memo。
- 再继续执行：
  - reducer
  - eval v2
  - judge v2

这里没有伪造任何模型结果，仍然只消费真实返回的 batch memo；只是绕过了本轮被网关拦死的 bucket-level synthesis。

生成的 bucket memo：

- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\bucket_build\bucket_memos\resource_pressure.json`
- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\bucket_build\bucket_memos\institutional_pipeline.json`
- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\bucket_build\bucket_memos\dark_humor.json`

### 5.4 Reducer 结果

Reducer 输出目录：

- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\style_bible`

关键产物：

- `style_bible_final.json`
- `style_bible_reasoning.json`
- `style_bible_export_flat.json`
- `style_bible_reduce_trace.json`

实际结果：

- `style_bible_reasoning.json`
  - 仅生成 1 条 reasoning entry
- `style_bible_final.json`
  - `narrative_system / expression_system / aesthetics_system / voice_contract / worldbook_binding / negative_rules / supporting_evidence` 全部为空
- `style_bible_export_flat.json`
  - 同样为空壳

这说明：

- 当前 reducer 在“仅有部分 bucket memo、且 memo coverage 不完整”的情况下，会直接塌缩成空 final。
- 这不是 Eval/Judge V2 的误判，而是真实 candidate 本身几乎没有可用下游结构。

### 5.5 Eval V2 结果

输出：

- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\eval_v2`

结果：

- `overall_score = 22.81`
- `status = fail`

主要熔断点：

- `bundle_coverage`
  - fail
  - `scene_in_any_batch_ratio` 未过线
  - `style_window_in_any_batch_ratio` 未过线
- `grounding_trace_integrity`
  - fail
  - `grounded_final_rule_ratio = 0.0`
- `required_axis_coverage`
  - fail
  - 6 个核心主题组 `hit_count = 0`
- `supporting_evidence`
  - fail
- `actionability`
  - fail
- `routing_hints`
  - fail
- `worldbook_binding`
  - fail

Eval V2 在这次真实跑批里成功做到了两件事：

1. 没有因为 final JSON “长得像 schema”就放行。
2. 在 final 为空壳时，硬性把 coverage / grounding / downstream surfaces 一起熔断。

### 5.6 Judge V2 结果

输出：

- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\judge_v2`

结果：

- `overall_score = 10.1583`
- `status = fail`
- `case_count = 6`
- `pass_case_count = 0`

维度均值：

- `trace_auditability = 6.1583`
- `anti_pattern_resistance = 4.0`
- 其余核心维度基本为 `0`

解释：

- `trace_auditability` 还能拿到一点分，说明 reasoning / reduce trace 并非完全空白。
- 但 final 结构为空，导致：
  - `axis_coverage = 0`
  - `mechanism_specificity = 0`
  - `evidence_faithfulness = 0`
  - `routing_executability = 0`
  - `worldbook_exportability = 0`
  - `rag_atomicity = 0`
  - `prompt_preset_usability = 0`
- `anti_pattern_resistance` 反而是满分，因为空输出天然不会触发泛化废话、空路由、关键词堆砌。

这也正说明 Judge V2 的判法是合理的：

- 它不会因为“没有胡说八道”就把空 candidate 判成好结果。
- trace 还能给一点审计分，但结果层的可用性会被单独打穿。

---

## 6. 本轮核心结论

### 6.1 升级目标完成情况

已完成：

- Eval V2 升级完成，并已实跑验证。
- Gold Set V2 升级完成，并已被 Judge V2 消费。
- Judge V2 升级完成，并已实跑验证。

### 6.2 真实暴露出的系统问题

本轮最关键的真实发现不是 `eval / judge` 有 bug，而是：

1. 上游 batch memo 请求仍受网关稳定性影响。
   - 本轮真实跑批里出现了 `503` 与单个 `400`。

2. reducer 对“部分 bucket memo 覆盖”的韧性不够。
   - 即使已有 3 个 bucket memo、10 个真实 batch memo，reducer 仍可能输出空 final。

3. 空 final 在 Eval V2 / Judge V2 下会被稳定拦截。
   - 这正是这轮升级的价值。

### 6.3 从验证结果看，V2 审查器是否有效

答案是：有效。

证据：

- 对既有灰度 candidate：
  - Eval V2 = `72.64`
  - Judge V2 = `74.9183`
  - 说明升级后的评估器能对“有内容但仍有缺陷”的 candidate 给出中高分而非直接归零。

- 对这次真实 partial run candidate：
  - Eval V2 = `22.81`
  - Judge V2 = `10.1583`
  - 说明升级后的评估器能把“trace 有一点东西，但 final 基本空掉”的 candidate 精确拦下。

---

## 7. 建议的下一步

优先级从高到低：

1. 修 `resource_pressure__b05` 的 `400 Bad Request`
   - 先抓请求体差异，确认是不是 prompt 内容、字符、字段或 provider 限制导致。

2. 给 reducer 加一个“非空 final 最低合同”
   - 如果 `reasoning.entries > 0` 但 `style_bible_final.json` 主要字段全空，应该直接 fail fast，而不是静默写出空壳 final。

3. 给真实跑批补一个“partial run gold set”
   - 当上游只成功了一部分 batch 时，Judge 可切到对应该 coverage 的 subset gold set，避免把上游失败与 candidate 语义失败混在一起。

4. 等网关稳定后，再重跑完整 3-bucket real run
   - 目标不是只看 `trace_auditability`，而是让 `mechanism_specificity / routing_executability / worldbook_exportability` 真正回升。

5. 继续盯缓存命中率
   - 本次真实请求 `prompt_cache_hit_ratio = 0.0`
   - 需要在 provider 稳定、同 bucket 连续请求真的跑完整后再重新测一次。

---

## 8. 关键路径

关键代码：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_judge.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\cli.py`
- `D:\card\novel_pipeline\config\style_bible_eval_rules.toml`
- `D:\card\novel_pipeline\config\style_bible_judge_rules.toml`
- `D:\card\novel_pipeline\config\style_bible_regression_rules.toml`
- `D:\card\novel_pipeline\data\eval\style_gold_set\v2\index.json`

真实跑批输出：

- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\bucket_build`
- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\style_bible`
- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\eval_v2`
- `D:\card\novel_pipeline\data\reports\style_system_upgrade_20260409\candidate_bucket3_v2\judge_v2`

