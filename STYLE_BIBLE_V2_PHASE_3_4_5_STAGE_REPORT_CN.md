# Style Bible V2 Phase 3 + Phase 4 + Phase 5 阶段改造报告

更新时间：2026-04-09

## 1. 本阶段范围

本阶段在 Phase 0/1/2 的基础上，继续完成以下改造：

- Phase 3：Bucket Memo Synthesis
- Phase 4：Grounded Reducer + V2 final schema
- Phase 5：Anti-Patterns 动态注入引擎
- 侵入式缓存机制接入
  - Prompt 严格按“静态 -> 动态”顺序组装
  - Batch Planner / Builder 引入 cache affinity
  - `sampling_report.json` 记录缓存命中率与 TTFT

本阶段目标不是继续加大 sample 数量，而是把 `style_bible` 主链路真正改成：

`semantic routing -> semantic batching -> bucket memos -> grounded reducer -> reasoning/final/export_flat`

---

## 2. 已完成改造

### 2.1 缓存机制接入

已完成以下缓存友好与可观测性改造：

- `src/novel_pipeline_stable/client.py`
  - 从 API `usage` 中解析 `prompt_tokens`、`total_tokens`
  - 兼容解析 `prompt_tokens_details.cached_tokens` / `input_tokens_details.cached_tokens`
- `src/novel_pipeline_stable/style_bible_builder.py`
  - 汇总 bucket 阶段与 reducer 阶段的 token 使用情况
  - 在 `sampling_report.json` 中输出：
    - `prompt_tokens`
    - `cached_tokens`
    - `total_tokens`
    - `overall_cache_hit_ratio`
    - `ttft_summary`
- `src/novel_pipeline_stable/style_bible_batching.py`
  - `batch_plan.json` 中的 batch 已按 `bucket_id` 排序
  - 新增 `bucket_execution_order`
  - 每个 batch 写入 `cache_affinity_key = bucket_id`
- `src/novel_pipeline_stable/style_bible_bucket_builder.py`
  - 并发池固定为 4-7 的受控区间，默认 6
  - 采用 `ThreadPoolExecutor` + contiguous bucket assignment
  - 同一 bucket 的多个 batch 固定分配到同一 `worker_slot` / gateway，连续执行
  - 不再把 72 个 batch 无脑随机打散丢进线程池
- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`
  - Prompt 组装顺序固定为：
    1. `system_prompt`
    2. `global_settings`
    3. `anti_pattern_context`
    4. `prompt_bundle_xml` / `reduce_bundle`
    5. `runtime_identifiers`
  - Prompt 前半部分不再插入时间戳、request UUID 等动态变量
  - reducer 端 anti-pattern 注入已收紧为“只允许跨 bucket 通用负例”

### 2.2 Phase 3：Bucket Memo Synthesis

新增：

- `prompts/style_bible_bucket_synthesis.md`
- `src/novel_pipeline_stable/style_bible_bucket_builder.py`

本阶段已经完成：

- 从 `batch_plan.json` 生成 `bucket_prompt_bundles/*.xml`
- 对每个 batch 合成 `StyleBibleBucketBatchMemo`
- 再聚合成 `bucket_memos/*.json`
- 支持 `build-style-bible-bucket-memos --resume`
  - 若中途中断，下次启动会跳过已有 `bucket_memos/*.json`
  - 并从 `_bucket_requests/*/request_metrics.jsonl` 恢复已有请求统计

Prompt 红线已写死：

- 只允许输出 JSON
- `evidence_refs` 绝对禁止自然语言证据
- `evidence_refs` 只能原封不动复制输入 XML 中的 `ref` 属性值
- 如果当前 batch 没有发现强机制，必须输出 `rule_candidates: []`

### 2.3 Phase 4：Grounded Reducer + V2 Schema

新增：

- `prompts/style_bible_reduce.md`
- `src/novel_pipeline_stable/style_bible_reducer.py`

修改：

- `src/novel_pipeline_stable/models.py`
- `src/novel_pipeline_stable/style_bible_builder.py`

当前 reducer 已切换为：

- 输入不再直接依赖旧的 24/24 raw sample 汇总
- 改为消费 `bucket_memos/*.json`
- 产出三类正式文件：
  - `style_bible_reasoning.json`
  - `style_bible_final.json`
  - `style_bible_export_flat.json`

其中：

- `style_bible_reasoning.json` 负责审计与 claim-evidence map
- `style_bible_final.json` 升级为 `style-bible-result-v2`
- `style_bible_export_flat.json` 负责兼容仍然期待 `list[str]` 的旧下游

当前 final 规则对象已具备：

- `rule_id`
- `_reasoning_ref`
- `evidence_refs`

Reducer 还会输出：

- `style_bible_reduce_trace.json`

用于记录 `evidence_map` 与 `memo_ref_pool`，方便审计 reducer 是否只消费 memo 阶段已经出现过的合法 ref。

### 2.4 Phase 5：Anti-Patterns 动态注入引擎

新增：

- `prompts/style_bible_antipatterns_cn.md`
- `config/style_bible_antipattern_registry.json`
- `src/novel_pipeline_stable/style_bible_prompt_assembler.py`

当前注入引擎已经具备：

- bucket 级动态注入
- reducer 级通用负例注入
- 负例预算硬控制
  - 每个 prompt 预算 `1200-2000` tokens
  - 每个 bucket 最多注入 `4-8` 条负例
- 高相关优先排序
- anti-pattern 代码、token estimate、assembly order 的可审计输出

### 2.5 Eval / Judge 兼容收口

本轮顺手完成了 v2 产物兼容收口：

- `src/novel_pipeline_stable/style_bible_evaluator.py`
- `src/novel_pipeline_stable/style_bible_judge.py`
- `config/style_bible_eval_rules.toml`
- `config/style_bible_judge_rules.toml`
- `config/style_bible_regression_rules.toml`

当前 evaluator / judge 已支持：

- v2 final 经 flat normalization 进入旧检查链
- grounding trace 检查
- `bundle_coverage`
- `anti_pattern_resistance`

---

## 3. 关键文件变更

本阶段关键新增文件：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_prompt_assembler.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_bucket_builder.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`
- `D:\card\novel_pipeline\prompts\style_bible_bucket_synthesis.md`
- `D:\card\novel_pipeline\prompts\style_bible_reduce.md`
- `D:\card\novel_pipeline\prompts\style_bible_antipatterns_cn.md`
- `D:\card\novel_pipeline\config\style_bible_antipattern_registry.json`

本阶段关键修改文件：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_builder.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_batching.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_router.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_judge.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_eval_contract.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\client.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\cli.py`

---

## 4. 灰度测试

### 4.1 编译检查

已执行：

- `python -m compileall D:\card\novel_pipeline\src\novel_pipeline_stable`

结果：

- 通过

### 4.2 Bucket Memo 灰度测试

灰度 bucket：

- `resource_pressure`
- `institutional_pipeline`
- `dark_humor`

灰度输出目录：

- `D:\card\novel_pipeline\data\smoke\style_bible_phase345_gray`

执行策略：

- 并发数固定 `4`
- 不做全量 72 batch 暴力并发

结果：

- 生成 bucket memo：`3` 个
- 生成 batch memo：`18` 个
- `memoed refs`：`54`
- 代表性 bucket 覆盖到资源压力、制度流程、黑色幽默三类高差异机制

### 4.3 Resume 验证

对同一灰度目录再次执行 `build-style-bible-bucket-memos --resume`：

- 已存在的 `bucket_memos/*.json` 能被正确跳过
- request metrics 会被恢复并并入最终统计
- 本次 resume 验证耗时约 `4.3s`

说明 bucket 级断点续跑已经可用。

### 4.4 Reducer 灰度测试

同一灰度目录已成功生成：

- `style_bible_reasoning.json`
- `style_bible_final.json`
- `style_bible_export_flat.json`
- `style_bible_reduce_trace.json`

关键数字：

- reasoning entries：`13`
- flat export 规则总数：`42`
- reduce trace `evidence_map`：`13`
- reduce trace `memo_ref_pool`：`43`

### 4.5 Evaluator / Judge 冒烟

评测目录：

- eval：`D:\card\novel_pipeline\data\smoke\style_bible_phase345_gray_eval`
- judge：`D:\card\novel_pipeline\data\smoke\style_bible_phase345_gray_judge`

Evaluator 结果：

- `status = fail`
- `overall_score = 70.97`
- 主要 fail 项：
  - `bundle_coverage`
  - `grounding_trace_integrity`
  - `section_completeness`

Judge 结果：

- `status = fail`
- `overall_score = 55.3144`
- `case_fail_count = 16`

Judge 维度得分：

- `axis_coverage = 9.8438`
- `mechanism_specificity = 9.1762`
- `evidence_faithfulness = 8.2837`
- `routing_executability = 4.63`
- `worldbook_exportability = 4.7725`
- `rag_atomicity = 3.3838`
- `prompt_preset_usability = 4.1744`
- `anti_genericity = 6.0`
- `anti_pattern_resistance = 5.05`

说明：

- 这是 3 bucket partial gray run，不是 72 batch 全量生产结果
- coverage 失败基本符合预期
- grounding trace 仍需在全量正式跑批里继续复核

---

## 5. 缓存与 TTFT 观测

灰度 `sampling_report.json` 已回填真实缓存统计：

- `request_count = 19`
- `prompt_tokens = 183716`
- `cached_tokens = 2816`
- `total_tokens = 403103`
- `overall_cache_hit_ratio = 0.0153`

按 bucket 的 TTFT 结果：

- `dark_humor`
  - 首个 batch：`266.04s`
  - 后续均值：`156.962s`
  - 降幅：`41%`
- `institutional_pipeline`
  - 首个 batch：`145.953s`
  - 后续均值：`174.19s`
  - 降幅：`-19.35%`
- `resource_pressure`
  - 首个 batch：`169.209s`
  - 后续均值：`222.755s`
  - 降幅：`-31.64%`

当前结论：

- 缓存埋点、Prompt 排序、cache affinity、resume 已全部接通
- 但灰度 run 的实际缓存命中率远未达到验收门槛 `0.70`
- TTFT 也只有 `dark_humor` 出现下降，且降幅未超过 `50%`

因此，缓存机制的“实现完成”和“数据验收通过”目前必须明确区分：

- 实现完成：是
- 数据达标：否

---

## 6. 阶段结论与遗留

### 6.1 本阶段结论

Phase 3 / 4 / 5 及其缓存机制接入已经完成工程落地，当前链路具备：

- bucket memo map-reduce
- v2 final + reasoning + flat export 三产物
- anti-pattern 动态注入
- 4-7 受控并发
- cache affinity
- `--resume`
- cache / TTFT 可观测性

这意味着 `style_bible` 已经从旧的“24/24 sample + 单步 synthesis”转成了真正的分层蒸馏链路。

### 6.2 当前遗留

仍然存在以下未完成事项：

- 还没有执行 72 batch 的全量正式跑批
- 缓存验收门槛尚未通过
  - `overall_cache_hit_ratio >= 0.70` 未达成
  - follow-up TTFT 降幅 `> 50%` 未达成
- 3 bucket gray run 的 evaluator / judge 失败不能直接视为代码回归，但也不能视为最终质量通过
- grounding trace 需要在全量运行中再做一次正式复核

### 6.3 下一步建议

下一步建议直接做两件事：

1. 跑一次 72 batch 全量 `build-style-bible`
2. 以全量结果复核以下硬门槛：
   - `style_window_in_any_batch_ratio >= 0.90`
   - `scene_in_any_batch_ratio >= 0.70`
   - `grounded_final_rule_ratio == 1.0`
   - `overall_cache_hit_ratio >= 0.70`
   - 同 bucket follow-up TTFT 降幅 `> 50%`

如果全量缓存命中仍旧明显偏低，再回头排查：

- provider 侧 prompt cache 行为
- anti-pattern 注入内容是否仍有过大差异
- reducer prompt 的动态部分是否仍然过重
- bucket 分配粒度是否还可以继续收紧
