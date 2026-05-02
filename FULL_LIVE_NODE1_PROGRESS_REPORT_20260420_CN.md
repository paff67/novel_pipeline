# Full Live Node1 运行进度报告（进行中）

日期：2026-04-20  
状态：进行中，尚未完成 full live 全量收尾  
范围：`main_01_kunxu_l1_ch0001_0270`

## 1. 运行目标

本轮目标不是 mini mock，而是从真实 `extract-style` 链路起步，对正式数据集执行一次 full live 验证，并完整记录日志，重点观察：

1. section completeness control plane 是否真正起效
2. story-node sampling cap 是否约束住 router / batching
3. local reduce / repair / densify 在真实模型下的稳定性与长尾耗时

## 2. 运行根目录与关键日志

主运行根目录：

- `data/live_runs/full_live_from_extract_style_20260419_231949`

关键日志：

1. `logs/01_extract_style_rerun.log`
   - `extract-style --resume`
   - 结果：`420/420 skipped`
2. `logs/02_build_canon.log`
   - 首次 `build-canon` 失败，原因是 metadata json 被误当成 scene/style 产物
3. `logs/02_build_canon_compat_rerun.log`
   - 加入 compat 后 `build-canon` 成功
4. `logs/05_node1_caps_smoke_fix01.log`
   - 第一次修复后 smoke，验证采样控制面是否真正收敛
5. `logs/06_node1_caps_smoke_fix01_resume.log`
   - 在 `_reasoning_ref / compact reasoning` 兼容修复后，对同一输出根做 `--resume`

当前有效 smoke 目录：

- `semantic_versions_node1_caps_smoke_fix01/main_01_kunxu_l1_ch0001_0270`

## 3. 修复前后的关键对比

### 3.1 采样前后 scope

来自 `style_bible/sampled_input_scope.json`：

| 维度 | 原始数量 | 过滤后数量 |
| --- | ---: | ---: |
| scene | 1132 | 24 |
| style_window | 135 | 24 |
| chapter | 270 | 66 |
| plot_node | 270 | 85 |
| entity | 2611 | 679 |

解释：

1. `scene/style` 已被严格压到 `24/24`
2. `chapter/plot/entity` 不是简单切到 `24/24/20`，而是按 sampled scene/style 实际涉及到的 chapter scope 做了受控扩展，用来保证 support refs 不断层

### 3.2 batching 爆量修复结果

对比旧 smoke 与修复后 smoke：

| 指标 | 修复前 | 修复后 |
| --- | ---: | ---: |
| 总 batch 数 | 264 | 23 |
| unbatched item | 未关注 | 0 |

修复前 bucket `selected_item_count`（典型爆量）：

- `exam_screening`: 961
- `body_assetization`: 629
- `dark_humor`: 597
- `family_survival`: 599
- `resource_pressure`: 438
- `commercialized_conflict`: 509

修复后 bucket `selected_item_count`：

- `exam_screening`: 33
- `dark_humor`: 26
- `body_assetization`: 23
- `commercialized_conflict`: 22
- `institutional_pipeline`: 19
- `identity_shame`: 18
- `resource_pressure`: 13
- `family_survival`: 12

修复后 `batch_plan.json` 的 per-bucket batch 数：

- `dark_humor`: 4
- `exam_screening`: 3
- `institutional_pipeline`: 3
- `body_assetization`: 2
- `commercialized_conflict`: 2
- `gray_labor`: 2
- `identity_shame`: 2
- `asset_repricing`: 1
- `collective_production`: 1
- `contract_sales`: 1
- `family_survival`: 1
- `resource_pressure`: 1

结论：

phase01 的 story-node cap 已经从“只写在 source bundle 里”变成“真正约束住 routed inputs 与 batch plan”。

## 4. Local Reduce 真实运行状态

### 4.1 已确认通过的 bucket

截至报告撰写时，以下 bucket 已写出 `local_reduce_summary.json`：

- `asset_repricing`
- `body_assetization`
- `collective_production`
- `commercialized_conflict`
- `contract_sales`
- `dark_humor`
- `exam_screening`
- `family_survival`
- `gray_labor`
- `identity_shame`
- `institutional_pipeline`
- `resource_pressure`

### 4.2 真实 repair 长尾

从 `_local_reduce` 痕迹可见：

1. `dark_humor`
   - base attempt 数：3
   - 问题：模型把 `_reasoning_ref` 写成 evidence/window ref，并把 `reasoning.entries` 写成 compact shape
   - 现已通过兼容修复解决
2. `resource_pressure`
   - base attempt 数：2
   - 已成功
3. `identity_shame`
   - 出现 `pass_01` repair
   - repair pass 已成功落盘
4. `family_survival`
   - 当前仍可见 `pass_01` repair 的最新 raw response 更新
   - 说明当前长尾主要集中在 targeted repair 阶段

## 5. 当前 live 停在哪

截至 `2026-04-20 04:10 +08:00`：

1. `build-canon` 已完成
2. `build-style-bible` 的 phase01（source bundle / routed index / batch plan）已完成
3. 全部 base local reduce 已生成 summary
4. 当前仍未看到以下最终产物：
   - 根目录 `style_bible.json`
   - 根目录 reasoning/export flat
   - `_section_densify/*`
   - `style_bible_eval/*`

因此可以判断：

- 本次 node1 真实 live 尚未完成
- 当前长尾位于 reducer 后段，优先怀疑 section repair / densify / global merge 的收尾，而不是 phase01 再次爆量

## 6. 本轮 runtime 关键诊断结论

### 已经确认解决的问题

1. 采样 cap 丢失到 router / batching 的控制面 bug
2. `_reasoning_ref` 被误写成 evidence ref 导致 local reduce 卡死的 runtime 问题
3. `reasoning.entries` 使用 compact shape 导致 Pydantic 校验失败的问题

### 仍在观察的问题

1. section repair pass 真实耗时偏长
2. `run_status.json` 对 reducer 内部阶段的进度揭示不够细
3. 当前 live 未产出最终 `style_bible.json`，说明后段仍需继续观察

## 7. 建议的下一步

1. 保留当前进程继续跑完 node1，不建议清空目录重来，因为现阶段已经跨过最贵的 batch memo / local reduce 主体。
2. node1 完成后，优先整理：
   - 最终 `style_bible.json`
   - `reasoning.json`
   - `export_flat.json`
   - `_section_densify/*`
   - `style_bible_eval/*`
3. 在 node1 完整跑通前，不建议直接放大全量 5 节点 full live，因为当前瓶颈已经转移到 reducer 后段长尾，不再是 phase01。

## 8. 本报告对应的实现修复文档

配套代码修复说明见：

- `STYLE_BIBLE_SAMPLING_SCOPE_AND_LOCAL_REDUCE_RUNTIME_FIX_REPORT_20260420_CN.md`
