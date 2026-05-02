# Style Bible V2 Phase 0 + Phase 1 阶段改造报告

日期：2026-04-09

## 1. 本阶段范围

本次仅完成施工清单中的 Phase 0 和 Phase 1：

- Phase 0：先做可观测性
  - 在 `style_bible_builder.py` 集成 `sampling_report.json`
  - 先把当前 `bucket / chapter / axis` 的覆盖率量出来
- Phase 1：上 Semantic Router + Batch Planner
  - 新增 `style_bible_router.py`
  - 新增 `style_bible_batching.py`
  - 产出 `style_bible_routed_index.json`、`batch_plan.json`
  - 明确保持 `style_bible_final.json` final schema 不变

## 2. 本阶段完成情况

已完成：

- 新增 `src/novel_pipeline_stable/style_bible_contracts.py`
  - 固化 canonical axis / bucket 目录
  - 固化 Phase 0/1 产物文件名与 mode 常量
- 新增 `src/novel_pipeline_stable/style_bible_inputs.py`
  - 统一 facts/style/canon 输入加载
  - 统一 scope hint 构造
  - 补充对空 metadata JSON 的跳过逻辑
- 扩展 `src/novel_pipeline_stable/models.py`
  - 新增 `StyleBibleSamplingReport`
  - 新增 `StyleBibleRoutedIndex`
  - 新增 `StyleBibleBatchPlan`
  - 新增覆盖率、batch、bucket membership 等中间模型
- 新增 `src/novel_pipeline_stable/style_bible_router.py`
  - 对 scene / style window 做 heuristic semantic routing
  - 输出 axis_scores、bucket_memberships、support_refs、coverage rows
- 新增 `src/novel_pipeline_stable/style_bible_batching.py`
  - 基于 routed items 做 heuristic batch planning
  - 输出 bucket summaries、batch 列表、unbatched items
- 新增 `config/style_bible_batching_rules.toml`
  - 把 token budget、batch 上限、评分权重外置
- 修改 `src/novel_pipeline_stable/style_eval_contract.py`
  - 在不升 manifest version 的前提下，新增 sampling/routing/batching mode
  - 新增 routed_index / batch_plan / sampling_report 的 output_files 与 hashes
- 修改 `src/novel_pipeline_stable/style_bible_builder.py`
  - 在正式 synthesis 前统一产出：
    - `style_bible_source_bundle.json`
    - `style_bible_routed_index.json`
    - `batch_plan.json`
    - `sampling_report.json`
  - 保持 final synthesis prompt 和 final schema 不变
- 修改 `src/novel_pipeline_stable/cli.py`
  - 新增 `route-style-bible-inputs`
  - 新增 `plan-style-bible-batches`
  - `build-style-bible` 新增 `--batching-rules-config`

## 3. 新增/变更产物

正式构建或前置 helper 现在会产出：

- `style_bible_source_bundle.json`
- `style_bible_routed_index.json`
- `batch_plan.json`
- `sampling_report.json`
- `run_manifest.json` 中附带：
  - `sampling_mode`
  - `routing_mode`
  - `batching_mode`
  - 对应文件路径与哈希

本阶段 mode 固定为：

- `sampling_mode = debug_small`（当前 24/24 legacy sample 配置）
- `routing_mode = semantic_router_heuristic_v1`
- `batching_mode = semantic_batch_planner_v1`

## 4. 真实数据冒烟结果

使用数据：

- facts：`data/extracted/facts_formal_cn_gpt54_stable`
- style：`data/extracted/style_formal_cn_gpt54_stable`
- canon：`data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/canon`

冒烟输出目录：

- route/batch CLI：`data/smoke/style_bible_phase01_route`
- builder Phase 0/1 helper：`data/smoke/style_bible_phase01_builder`

验证结果：

- `route-style-bible-inputs` 成功产出 `style_bible_routed_index.json`
  - routed item 总数：1267
- `plan-style-bible-batches` 成功产出 `batch_plan.json`
  - batch 总数：72
  - unbatched items：1014
- builder 前置 helper 成功串出 4 个中间产物：
  - `style_bible_source_bundle.json`
  - `style_bible_routed_index.json`
  - `batch_plan.json`
  - `sampling_report.json`

## 5. 当前覆盖率快照

语料基线：

- scene：1132
- style window：135
- chapter：270
- active axes：10 / 10
- active buckets：12 / 12
- router 未明确分配 axis/bucket 的 item：2

Legacy sampled 阶段：

- sampled scene：24 / 1132 = 2.12%
- sampled style window：24 / 135 = 17.78%
- sampled chapter：24 / 270 = 8.89%
- sampled axis coverage：10 / 10 = 100%
- sampled bucket coverage：12 / 12 = 100%

Routed 阶段：

- routed scene：1132 / 1132 = 100%
- routed style window：135 / 135 = 100%
- routed chapter：270 / 270 = 100%
- routed axis coverage：10 / 10 = 100%
- routed bucket coverage：12 / 12 = 100%

Batched 阶段：

- batched scene：144 / 1132 = 12.72%
- batched style window：109 / 135 = 80.74%
- batched chapter：238 / 270 = 88.15%
- batched axis coverage：10 / 10 = 100%
- batched bucket coverage：12 / 12 = 100%

当前 batched 覆盖偏低的 axis：

- `body_modification`：109 / 750 = 14.53%
- `resource_pressure`：110 / 704 = 15.62%
- `education_filter`：174 / 999 = 17.42%
- `institutional_absurdity`：193 / 993 = 19.44%
- `dark_humor`：187 / 901 = 20.75%

当前 batched 覆盖偏低的 bucket：

- `body_assetization`：80 / 620 = 12.90%
- `commercialized_conflict`：70 / 432 = 16.20%
- `exam_screening`：124 / 731 = 16.96%
- `institutional_pipeline`：172 / 941 = 18.28%
- `family_survival`：69 / 349 = 19.77%

## 6. 本阶段收益

- 现在可以明确看到 legacy 24/24 sample 的压缩程度，而不是只看到最终 style bible。
- 现在可以明确看到某个 axis / bucket / chapter 在 sampled、routed、batched 各阶段的覆盖变化。
- 现在已经把 “全量输入 -> semantic routing -> batch planning” 作为正式中间层接入，为 Phase 2 的 bucket memo map-reduce 做好了输入接口。
- `style_bible_final.json` 未改 schema，现有 eval/judge/gold-set 合同不需要同步重做。

## 7. 已知遗留与 Phase 2 前的边界

本阶段刻意未做：

- 还没有 `bucket_memos/*.json`
- final reducer 还没有改成“吃 memos 再 reduce”
- 还没有 grounding schema / claim-evidence map
- 还没有 anti-pattern prompt / eval v2 / judge v2

当前明确边界：

- final synthesis 仍然直接消费 `style_bible_source_bundle.json`
- `batch_plan.json` 目前只作为 Phase 1 中间产物和可观测性产物，还没有进入 final reducer
- 本次真实冒烟验证的是 Phase 0/1 前置产物链路；没有把外部 LLM synthesis 作为本阶段验收前提

## 8. 结论

Phase 0 与 Phase 1 已完成落地，且已在真实数据上验证中间产物可生成、覆盖率可观测、router/batch planner 可独立运行。

下一阶段可以直接进入：

- Phase 2：Bucket Memo Map-Reduce
- 目标：让 final reducer 从 `bucket_memos/*.json` 生成 final，而不是继续直接吃 24/24 legacy sample
