# Node1 全流程运行报告 2026-05-03

## 1. 报告结论

本次执行目标是：

1. 重跑 4 个空壳 fact 产物。
2. 修复 local reduce 动态 response schema 问题。
3. 修复 `AttributeError: 'str' object has no attribute 'value'`。
4. 对 `NarrativeRuleItem` / `WorldbookFactItem` 缺失 `trigger` / `constraint` 增加 sanitize / repair 兜底。
5. 继续执行 `build-style-bible -> evaluate-style-bible -> judge-style-bible`。

最终结果：

- 4 个空壳 fact 已全部重跑成功，并通过 `FactExtractionResult` 模型校验。
- local reduce 相关代码问题已修复，测试覆盖已补充。
- Style Bible 构建流程完成，`build-style-bible` 状态为 `completed`。
- `evaluate-style-bible` 已完成，但质量门失败，主要原因是 section completeness 不足。
- `judge-style-bible` 已完成，但质量门失败，6 个 judge case 全部 fail。
- 这次的主要剩余问题不是流程中断，而是最终 Style Bible 被压缩得过稀，导致覆盖率和 judge 分数显著不足。

## 2. 运行环境与约束

运行环境：

- 工作目录：`/opt/novel_pipeline`
- 操作系统：Ubuntu 24.04 64 Bit VPS
- Python 运行环境：项目虚拟环境 `.venv`
- 监控服务：`127.0.0.1:9120`
- NPM：由 Docker Compose 部署，本次未改动 Docker/NPM 网络结构

API 运行假设：

- 正式主线继续采用动态调用模式，不恢复固定 RPM。
- 用户确认请求 URL 为 `https://api.0-0.pro/v1`。
- 目标模型为 `gpt-5.4`，推理强度目标为 `xhigh`。
- 本报告不包含任何 `.env` 密钥或敏感 token。

## 3. 本次代码修复

### 3.1 local reduce 多路径 response schema

修复文件：

- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`

问题：

- local reduce 在一个 response schema 中包含多个 `surface_path` 时，旧逻辑会生成 discriminated union / `oneOf` 风格 schema。
- 当前网关与严格 schema 模式下，这种 schema 容易触发兼容性问题，造成 local reduce 请求失败或降级压力过大。

修复：

- 单路径时继续使用原来的路径专用 row model。
- 多路径时改为生成单一 strict multi-surface row model。
- `surface_path` 保留枚举约束。
- 非当前路径适用字段使用空字符串，避免 `oneOf`。
- schema description 仍保留 path target、slot hints、scalar allowed values 等关键提示。

相关测试：

- `test_local_reduce_multi_path_schema_avoids_one_of`
- `test_style_bible_bucket_memo_response_format_uses_openai_strict_schema`

### 3.2 `surface_path` 字符串兼容

修复文件：

- `src/novel_pipeline_stable/models.py`

问题：

- local reduce 动态子模型中，`surface_path` 有时会以字符串进入 validator。
- 旧代码直接访问 `self.surface_path.value`，导致：

```text
AttributeError: 'str' object has no attribute 'value'
```

修复：

- 新增 `_surface_path_enum()`，统一把 `str | SurfacePath` 规范化为 `SurfacePath`。
- `_LocalNarrativeRuleRow`、`_LocalWorldbookFactRow`、`_LocalRoutingHintRow`、`_LocalNegativeRuleRow`、`_LocalScalarRuleRow` 均使用该 helper。

相关测试：

- `test_local_rule_row_accepts_str_surface_path_in_dynamic_submodel`

### 3.3 NarrativeRuleItem / WorldbookFactItem repair

修复文件：

- `src/novel_pipeline_stable/models.py`

问题：

- LLM 在结构化输出中有时只给 `text`，漏掉 `trigger` / `constraint`。
- 对 `NarrativeRuleItem` 和 `WorldbookFactItem` 来说，这会导致模型校验失败。

修复：

- 新增 `_derive_trigger_constraint_from_text()`。
- 新增 `_repair_trigger_constraint_payload()`。
- `NarrativeRuleItem` / `WorldbookFactItem` 增加 `model_validator(mode="before")`。
- 当 `trigger` / `constraint` 缺失时，从 `text` 或结构化字段中推导兜底。

示例：

```json
{
  "text": "当收益出现时，必须先结算债务。"
}
```

会被修复为：

```json
{
  "trigger": "当收益出现时",
  "constraint": "必须先结算债务。"
}
```

相关测试：

- `test_narrative_rule_repairs_missing_trigger_and_constraint_from_text`

### 3.4 网关 524 与 responses/schema fallback 兜底

修复文件：

- `src/novel_pipeline_stable/api_clients/base.py`

修复点：

- 将 Cloudflare / 网关类状态码 `520` 到 `524` 纳入 retry bonus。
- `524` 被归类为 retryable。
- 对 `/responses + stream=false` 继续尊重配置，只有网关明确要求 stream 时才切回 stream。
- schema 模式遇到明确的 `invalid_json_schema` 类错误时，可 fallback 到 `json_object + blueprint`。
- `/responses` temperature omission 会进入 metrics 和 cache key 计算，避免未实际发送的 temperature 造成缓存碎片。

相关测试：

- `test_gateway_524_uses_retry_and_upstream_bonus_budget`
- `test_timeout_errors_use_upstream_retry_bonus_budget`
- `test_responses_stream_false_respects_config_until_gateway_requires_stream`
- `test_responses_restore_stream_detects_gateway_body_text`
- `test_invalid_json_schema_can_fallback_to_json_object_blueprint`
- `test_responses_temperature_omission_drives_cache_signature`

## 4. 验证命令与结果

已执行：

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests -v
```

结果：

```text
Ran 134 tests in 0.719s
OK
```

结论：

- 代码语法编译通过。
- 全量单元测试通过。
- 本轮新增修复点均有回归覆盖。

## 5. Fact 空壳重跑

### 5.1 输入与输出

输入 scene 目录：

```text
data/scenes_full_0001_0841_ready_20260330
```

输出 fact 目录：

```text
data/extracted/facts_formal_cn_gpt54_stable
```

状态文件：

```text
data/extracted/facts_formal_cn_gpt54_stable/run_status.json
```

### 5.2 重跑对象

本次识别并重跑的 4 个空壳 fact：

| 文件 | scene_id | chapter_id | entities | events | facts | artifact_fingerprint | 模型校验 |
|---|---:|---:|---:|---:|---:|---|---|
| `scene_0013_002.json` | `0013_002` | `0013` | 12 | 6 | 16 | yes | pass |
| `scene_0437_001.json` | `0437_001` | `0437` | 12 | 6 | 16 | yes | pass |
| `scene_0437_002.json` | `0437_002` | `0437` | 12 | 6 | 20 | yes | pass |
| `scene_0437_003.json` | `0437_003` | `0437` | 12 | 6 | 16 | yes | pass |

### 5.3 Fact 运行状态

`run_status.json` 关键字段：

```json
{
  "stage": "stable-extract-facts",
  "status": "completed",
  "processed_items": 4,
  "success_count": 4,
  "failure_count": 0,
  "outstanding_failures": 0,
  "pending_items": 0,
  "started_at": "2026-05-03T06:15:05.678629Z",
  "finished_at": "2026-05-03T06:28:34.057289Z"
}
```

结论：

- 4 个目标 fact 全部成功。
- 没有 outstanding failures。
- 没有 pending items。
- 新产物均有 `artifact_fingerprint`。

### 5.4 请求路径与 fallback 说明

本次优先尝试：

- `/responses`
- `json_schema`
- `gpt-5.4`
- `xhigh`

实际运行中，网关多次返回 Cloudflare / gateway `524` timeout。为保证流程推进，最终使用同一网关与同一模型，切换到：

- `chat_completions`
- `json_schema`
- forced two-pass

完成 4 个空壳 fact 重跑。

这属于运行层 fallback，不代表业务降级为无 schema 自由文本。最终 fact 产物已通过 Pydantic 模型校验。

## 6. Node1 下游全流程

节点 ID：

```text
main_01_kunxu_l1_ch0001_0270
```

总状态文件：

```text
data/runtime/node1_pipeline_status.json
```

状态摘要：

```json
{
  "updated_at": "2026-05-03T06:54:11.184209Z",
  "stage": "done",
  "status": "completed",
  "node_id": "main_01_kunxu_l1_ch0001_0270"
}
```

核心目录：

| 类型 | 路径 |
|---|---|
| merged style | `/opt/novel_pipeline/data/extracted/style_formal_cn_gpt54_stable_main_01_kunxu_l1_ch0001_0270` |
| canon | `/opt/novel_pipeline/data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/canon` |
| style bible | `/opt/novel_pipeline/data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible` |
| eval | `/opt/novel_pipeline/data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible_eval` |
| judge | `/opt/novel_pipeline/data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible_judge` |

## 7. Canon 产物

Canon 目录：

```text
data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/canon
```

主要产物：

| 文件 | 说明 |
|---|---|
| `facts.jsonl` | fact 汇总 |
| `events.jsonl` | event 汇总 |
| `entities.jsonl` | entity 汇总 |
| `relationship_changes.jsonl` | 关系变化 |
| `chapter_summaries.jsonl` | 章节摘要 |
| `plot_nodes_draft.jsonl` | plot nodes draft |
| `power_system_notes.jsonl` | power system notes |
| `style_bible.json` | canon 阶段 style bible 输入汇总 |
| `style_index.json` | style index |

说明：

- 本次用户要求从已有 facts 数据继续跑第一节点下游流程。
- Canon 目录作为后续 style bible 构建的语义版本输入。

## 8. Style Bible 构建结果

### 8.1 构建状态

状态文件：

```text
data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible/run_status.json
```

状态摘要：

```json
{
  "stage": "stable-build-style-bible",
  "status": "completed",
  "processed_items": 1,
  "success_count": 1,
  "failure_count": 0,
  "pending_items": 0,
  "started_at": "2026-05-03T06:29:06.320890Z",
  "finished_at": "2026-05-03T06:54:03.201763Z"
}
```

结论：

- `build-style-bible` 成功完成。
- 没有 build 阶段失败项。

### 8.2 核心产物

Style Bible 目录：

```text
data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible
```

核心产物：

| 文件 | 大小/角色 |
|---|---|
| `style_bible_final.json` | 最终 Style Bible，约 28 KB |
| `style_bible_reasoning.json` | reasoning bundle，约 99 KB |
| `style_bible_export_flat.json` | flat export，约 10 KB |
| `style_bible_reduce_trace.json` | reduce trace，约 690 KB |
| `style_bible_source_bundle.json` | source bundle，约 15 MB |
| `style_bible_routed_index.json` | routed index，约 18 MB |
| `manifest.json` | build manifest，约 945 KB |
| `run_manifest.json` | run manifest，约 901 KB |
| `sampling_report.json` | sampling report，约 470 KB |
| `style_bible_coverage_report.json` | coverage report，约 470 KB |
| `failures.json` | 当前为空 JSON 数组 |

### 8.3 Final Style Bible 摘要

最终文件：

```text
data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible/style_bible_final.json
```

关键字段：

```json
{
  "style_id": "style_bible_main_01_kunxu_l1_ch0001_0270_v1",
  "scope": "main_01_kunxu_l1_ch0001_0270",
  "artifact_fingerprint": true,
  "supporting_evidence_count": 13,
  "degradation_status": {
    "mode": "complete",
    "skipped_sparse_buckets": [],
    "failed_bucket_ids": [],
    "assembler_conflicts": []
  }
}
```

规则数量摘要：

| section path | count |
|---|---:|
| `narrative_system.engine` | 1 |
| `narrative_system.pacing_rules` | 1 |
| `narrative_system.plot_node_logic` | 1 |
| `expression_system.description_rules` | 1 |
| `expression_system.dialogue_rules` | 1 |
| `expression_system.characterization_rules` | 1 |
| `expression_system.sensory_rules` | 1 |
| `worldbook_binding.rag_worthy` | 1 |
| `worldbook_binding.worldbook_worthy` | 2 |
| `worldbook_binding.routing_hints` | 2 |
| `negative_rules` | 1 |

同时，required scalar 均存在：

- `narrative_system.perspective`
- `narrative_system.distance`
- `narrative_system.temporality`
- 以及配置要求的其他 scalar contract 项

### 8.4 Local reduce bucket 结果

local reduce 成功 bucket 数：

```text
13 / 13
```

各 bucket local final rule 数：

| bucket | status | local final rule count |
|---|---|---:|
| `asset_repricing` | success | 7 |
| `body_assetization` | success | 10 |
| `collective_production` | success | 5 |
| `commercialized_conflict` | success | 6 |
| `contract_sales` | success | 1 |
| `dark_humor` | success | 8 |
| `exam_screening` | success | 12 |
| `family_survival` | success | 6 |
| `gray_labor` | success | 8 |
| `identity_shame` | success | 8 |
| `institutional_pipeline` | success | 6 |
| `orphanage` | success | 1 |
| `resource_pressure` | success | 8 |

结论：

- local reduce 本身不是失败点。
- 13 个 bucket 均有成功 local artifact。
- 后续 final assembly / densify / dedupe 阶段把很多 local rules 压缩到最终各 section 的 1 到 2 条。

## 9. Evaluate Style Bible 结果

报告文件：

```text
data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible_eval/style_eval_report.json
```

### 9.1 总体结果

```json
{
  "status": "fail",
  "overall_score": 0.8849,
  "max_score": 1.0
}
```

semantic judge 命名状态：

```json
{
  "semantic_judge_model": "offline_semantic_rule_engine",
  "decision_source": "offline_semantic_rule_engine",
  "requested_semantic_judge_model": "gpt-5.4"
}
```

结论：

- 离线 semantic judge 命名修正已经生效。
- 报告没有把用户传入的 `gpt-5.4` 伪装成实际 judge。

### 9.2 检查项

| check_id | category | status | score | max_score | 说明 |
|---|---|---:|---:|---:|---|
| `schema_validity` | schema | pass | 1.0000 | 1.0000 | Style Bible 通过严格 `StyleBibleResultV2` 校验 |
| `section_completeness` | coverage | fail | 0.2692 | 1.0000 | section 数量覆盖不足 |
| `semantic_rule_quality` | semantic | pass | 0.8849 | 1.0000 | 单条规则语义质量尚可 |

### 9.3 section completeness 明细

关键统计：

```json
{
  "required_scalar_hit_count": 7,
  "required_scalar_total": 7,
  "minimum_path_hit_count": 0,
  "minimum_path_total": 19,
  "completeness_ratio": 0.2692,
  "missing_scalars": []
}
```

含义：

- required scalars 全部命中。
- 19 个需要最低数量的 rule path 中，0 个达到最低数量要求。
- 失败原因集中在最终规则数量过少，而不是 schema 不合法。

主要 underfilled paths：

| path | actual | minimum | deficit |
|---|---:|---:|---:|
| `narrative_system.engine` | 1 | 3 | 2 |
| `narrative_system.pacing_rules` | 1 | 4 | 3 |
| `narrative_system.plot_node_logic` | 1 | 3 | 2 |
| `expression_system.description_rules` | 1 | 4 | 3 |
| `expression_system.dialogue_rules` | 1 | 4 | 3 |
| `expression_system.characterization_rules` | 1 | 4 | 3 |
| `expression_system.sensory_rules` | 1 | 4 | 3 |
| `aesthetics_system.core_axes` | 1 | 5 | 4 |
| `aesthetics_system.pressure_axes` | 1 | 5 | 4 |
| `aesthetics_system.humor_recipe` | 1 | 4 | 3 |
| `aesthetics_system.satire_targets` | 1 | 4 | 3 |
| `aesthetics_system.nonstandard_xianxia_rules` | 1 | 4 | 3 |
| `voice_contract.register_mix` | 1 | 4 | 3 |
| `voice_contract.negative_pitfalls` | 1 | 4 | 3 |
| `character_arc_rules` | 1 | 4 | 3 |
| `worldbook_binding.rag_worthy` | 1 | 4 | 3 |
| `worldbook_binding.worldbook_worthy` | 2 | 4 | 2 |
| `worldbook_binding.routing_hints` | 2 | 4 | 2 |
| `negative_rules` | 1 | 6 | 5 |

### 9.4 semantic quality 明细

semantic rule quality：

```json
{
  "total_rules": 26,
  "average_specificity": 0.8367,
  "average_actionability": 0.9609,
  "average_grounding": 0.8586
}
```

解读：

- 最终留下来的 26 条规则，单条质量并不差。
- 失败点是覆盖数量不足，不是每条规则都泛化或不可用。

## 10. Judge Style Bible 结果

报告文件：

```text
data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible_judge/judge_report.json
```

### 10.1 总体结果

```json
{
  "status": "fail",
  "overall_score": 30.5717,
  "max_score": 100.0,
  "overall_ratio": 0.3057,
  "pass_score": 75.0,
  "warn_score": 60.0,
  "case_count": 6,
  "applicable_case_count": 6,
  "pass_case_count": 0,
  "warn_case_count": 0,
  "fail_case_count": 6,
  "quality_gate_passed": false
}
```

结论：

- judge 流程执行完成。
- judge 质量门未通过。
- 6 个 applicable case 全部 fail。

### 10.2 维度分数

| dimension | score |
|---|---:|
| `axis_coverage` | 2.0017 |
| `mechanism_specificity` | 1.9633 |
| `evidence_faithfulness` | 8.4750 |
| `trace_auditability` | 7.9600 |
| `routing_executability` | 2.8600 |
| `worldbook_exportability` | 3.4417 |
| `rag_atomicity` | 2.5650 |
| `prompt_preset_usability` | 0.2400 |
| `anti_genericity` | 0.6667 |
| `anti_pattern_resistance` | 0.3983 |

主要短板：

- `prompt_preset_usability` 极低，说明最终 Style Bible 难以直接转化为可执行 prompt preset。
- `anti_pattern_resistance` 极低，说明对泛化、百科式、套路化输出的抵抗不足。
- `anti_genericity` 极低，说明最终规则仍容易被 judge 识别为泛化。
- `mechanism_specificity` 与 `axis_coverage` 很低，说明 bucket 机制没有足够展开。
- `routing_executability` 与 `worldbook_exportability` 不足，说明 RAG / worldbook 路由层的可执行性不够。

### 10.3 Case 结果

| case_id | scope_type | bucket | status | score | ratio |
|---|---|---|---|---:|---:|
| `main_01_dark_humor_goal_driven_exposition_006` | bucket_batch | `dark_humor` | fail | 24.68 | 0.2468 |
| `main_01_dark_humor_recipe_005` | bucket_batch | `dark_humor` | fail | 26.50 | 0.2650 |
| `main_01_institutional_pipeline_procedural_cruelty_003` | bucket_batch | `institutional_pipeline` | fail | 28.63 | 0.2863 |
| `main_01_institutional_pipeline_qualification_chain_004` | bucket_batch | `institutional_pipeline` | fail | 47.08 | 0.4708 |
| `main_01_resource_pressure_cost_settlement_001` | bucket_batch | `resource_pressure` | fail | 28.97 | 0.2897 |
| `main_01_resource_pressure_threshold_routing_002` | bucket_batch | `resource_pressure` | fail | 27.57 | 0.2757 |

## 11. 当前产物质量判断

### 11.1 流程完整性

流程完整性：通过。

依据：

- fact 重跑完成。
- build-style-bible 完成。
- evaluate-style-bible 完成。
- judge-style-bible 完成。
- 全流程状态文件为 `stage=done` / `status=completed`。
- 没有残留的 pipeline 运行进程。

### 11.2 结构合法性

结构合法性：通过。

依据：

- fact 产物通过 `FactExtractionResult` 校验。
- Style Bible 通过严格 `StyleBibleResultV2` 校验。
- `style_bible_final.json` 带有 `artifact_fingerprint`。
- build degradation status 为 `complete`，无 failed bucket、无 sparse bucket、无 assembler conflicts。

### 11.3 内容覆盖质量

内容覆盖质量：不通过。

依据：

- `section_completeness=0.2692`。
- required scalar 全中，但 19 个 minimum rule path 无一达标。
- final 中大量 section 只有 1 条规则。
- local reduce 13 个 bucket 原本产出 86 条左右 local final rules，但最终 Style Bible 只有 26 条 rules。

初步判断：

- local reduce 已经能生成规则。
- final assembly / semantic dedupe / section densify 的保留策略过严或目标约束不足。
- 当前 pipeline 在“生成合格结构”上成功，在“保留足够覆盖面”上失败。

### 11.4 Judge 可用性

Judge 可用性：报告有效，质量门失败可信。

依据：

- 6 个 case 均 applicable。
- 低分维度与 eval 的 section completeness 失败互相印证。
- `evidence_faithfulness` 与 `trace_auditability` 相对更高，说明不是完全无法追踪，而是机制和可执行规则不足。

## 12. 关键风险

### 12.1 最终 assembly 过度压缩

local reduce bucket 产物数量明显多于最终 Style Bible。最终规则压缩后，每个 section 大多只剩 1 条，直接触发 section completeness 失败。

风险：

- 即使 build 成功，Style Bible 对后续 RAG / prompt / rewrite 的指导力不足。
- judge 会持续在 axis coverage、mechanism specificity、prompt preset usability 上低分。

### 12.2 Densify 没有真正补齐 section minimum

虽然 section densify 阶段存在产物和 embedding metrics，但最终仍未补齐 minimum path target。

风险：

- densify 可能被 dedupe 或 final merge 再次压掉。
- densify 的目标函数可能偏“语义去重”，而不是“每个 section 达到最低可用数量”。

### 12.3 Routing 与 worldbook 表达不足

最终产物：

- `worldbook_binding.rag_worthy`: 1
- `worldbook_binding.worldbook_worthy`: 2
- `worldbook_binding.routing_hints`: 2

这些数量低于 eval minimum，也被 judge 的 routing/worldbook 维度压分。

风险：

- Hybrid RAG 或 worldbook export 后续即便能跑，也缺乏足够 routing hints。
- 单条 routing rule 容易过泛，不能覆盖 case 中的具体触发器。

### 12.4 Prompt preset 可用性不足

`prompt_preset_usability=0.24` 是最弱维度之一。

风险：

- Style Bible 很难直接转化为稳定的写作/改写 prompt。
- 后续 LLM 使用时可能仍然依赖泛化风格词，而不是机制化约束。

## 13. 建议修复路线

### P0：让 final assembly 尊重 section minimum

目标：

- final 输出必须满足 eval profile 中的 minimum path count。
- 如果 local reduce 有足够候选，不能被 final assembly 压到 1 条。

建议：

1. 在 final merge 后增加 hard gate：统计 underfilled paths。
2. 对 underfilled path 从 local reduce candidates 中回填。
3. 回填时只做同 path 内去重，不跨 section 过度去重。
4. section minimum 未满足时，build 状态不要只标记 `completed`，至少写入 quality warning 或 `completed_with_quality_failures`。

### P1：重调 section densify 的目标函数

目标：

- densify 的第一优先级从“补一点增量”改成“补齐每个 section minimum”。

建议：

1. densify 输入显式携带每个 path 的 `actual_count`、`minimum`、`deficit`。
2. densify 输出按 deficit 生成，不允许只生成 1 条泛化规则。
3. densify 结果进入 final 前，增加 path-aware keep budget。
4. 对 `negative_rules`、`routing_hints`、`worldbook_worthy` 单独加最低保留保护。

### P1：减少 semantic dedupe 的误杀

目标：

- 保留同一机制下不同触发器、不同应用场景、不同 surface path 的规则。

建议：

1. dedupe key 加入 `surface_path`、`bucket_id`、`trigger`、`constraint`。
2. 对 path target 不同的候选，不做全局 drop。
3. 对 routing/worldbook/negative rules 设置更高保留阈值。
4. 将 dedupe drop pairs 的 reason 汇总到 build report，并标明被 drop 的 section。

### P2：针对 judge 维度补专门规则族

目标：

- 提升 judge 的机制性和 prompt 可用性。

建议：

1. `dark_humor` 增加制度去魅、收费/客服/赔偿/广告语言等机制规则。
2. `institutional_pipeline` 增加流程残酷、资格链、通知/表单/审核口吻规则。
3. `resource_pressure` 增加成本闭环、价格/资格/倒计时触发器、路由目标节点规则。
4. `prompt_preset_usability` 增加可直接用于 prompt 的 action verbs 和 forbidden patterns。

## 14. 可复现命令

代码验证：

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m unittest discover -s tests -v
```

产物状态检查：

```bash
jq '{stage,status,processed_items,success_count,failure_count,outstanding_failures,pending_items,started_at,finished_at}' \
  data/extracted/facts_formal_cn_gpt54_stable/run_status.json

jq '{updated_at,stage,status,node_id,style_bible_dir,eval_dir,judge_dir}' \
  data/runtime/node1_pipeline_status.json

jq '.summary' \
  data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible_eval/style_eval_report.json

jq '.summary' \
  data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/style_bible_judge/judge_report.json
```

## 15. 最终状态

最终状态可以概括为：

```text
Runtime pipeline: completed
Fact retry: completed, 4/4 success
Style Bible build: completed
Eval: completed, quality gate failed
Judge: completed, quality gate failed
Unit tests: 134 passed
Primary remaining issue: final Style Bible coverage too sparse
```

本次产物可以作为调试下一轮 section completeness / final assembly / judge quality 的基线，但不建议作为正式可用 Style Bible 直接进入后续生产链路。
