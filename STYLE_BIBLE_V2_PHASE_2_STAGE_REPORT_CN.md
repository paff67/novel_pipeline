# Style Bible V2 Phase 2 阶段改造报告

更新时间：2026-04-09

## 1. 本阶段目标

本阶段继续在 Phase 0 + Phase 1 的基础上，完成 Bucket Memo Map-Reduce 改造：

- 新增 bucket memo 合成阶段，不再让最终 style bible 直接吃旧版 24/24 sample
- 新增 reducer 阶段，由 bucket memos 汇总生成 `style_bible_final.json`
- 对 `evidence_refs` 做 prompt 红线 + 代码级净化双保险
- 将 bucket synthesis 从串行调用改为受控并发执行
- 选取 3 个代表 bucket 做灰度测试，并产出阶段报告

## 2. 已完成改造

### 2.1 Prompt 层

新增：

- `D:\card\novel_pipeline\prompts\style_bible_bucket_synthesis.md`
- `D:\card\novel_pipeline\prompts\style_bible_reduce.md`

其中 `style_bible_bucket_synthesis.md` 已显式写死以下红线：

- 只允许输出 JSON
- `evidence_refs` 绝对禁止自然语言证据
- `evidence_refs` 只能原封不动复制输入 XML 中的 `ref` 属性值
- 如果当前 batch 没有强机制，必须输出 `rule_candidates: []`
- 明确压制空泛赞美、关键词堆砌、单 scene 细节上升、广告/运营话术误判为核心风格等施工清单中的高危陷阱

`style_bible_reduce.md` 已明确要求：

- reducer 只吃 bucket memos 和全局 summaries
- 不回退为“直接对 raw sample 再总结一次”
- `evidence_map.evidence_refs` 只能来自 bucket memo 里的合法 ref
- `final.supporting_evidence.source_ref` 必须来自 reducer 选中的 evidence refs

### 2.2 Schema / 合同层

修改：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_contracts.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_eval_contract.py`

新增了 Phase 2 所需模型：

- `StyleBibleBucketRuleCandidate`
- `StyleBibleBucketBatchMemo`
- `StyleBibleBucketMemo`
- `StyleBibleReduceTraceEntry`
- `StyleBibleReducerOutput`

同时扩展了 `StyleBibleSamplingReport`：

- 新增 `memoed_refs`
- 新增 `reduced_refs`

并扩展 run manifest，允许记录：

- `bucket_memo_dir`
- `reduce_trace_file`
- reducer prompt 信息

### 2.3 Builder / Reducer 主链路

新增：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_bucket_builder.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`

改造：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_builder.py`

当前主链路已经改成：

1. Phase 0/1 先生成 `style_bible_source_bundle.json`、`style_bible_routed_index.json`、`batch_plan.json`、`sampling_report.json`
2. Phase 2 bucket builder 基于 `batch_plan.json` 生成 `bucket_prompt_bundles/*.xml`
3. 并发合成 `bucket_memos/*.json`
4. reducer 从 `bucket_memos/*.json` 生成 `style_bible_final.json`
5. 同时输出 `style_bible_reduce_trace.json`
6. 若走完整 `build-style-bible`，会回写 `sampling_report.json` 的 memoed/reduced 覆盖

### 2.4 并发策略

`style_bible_bucket_builder.py` 明确没有使用暴力串行 `for batch in batches: call_llm()`。

实现为：

- `ThreadPoolExecutor`
- 并发数默认 `6`
- 代码级 clamp 到 `4-7`
- 灰测实际使用 `4`

同时为避免共享 client 的可变状态产生串扰，采用了：

- 每个 batch 独立 `request_key`
- 每个 batch 独立 `_bucket_requests/<batch_id>` artifacts 目录

### 2.5 CLI 能力

修改：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\cli.py`

新增命令：

- `build-style-bible-bucket-memos`
- `reduce-style-bible`

并扩展原 `build-style-bible` 的输出打印，使其可显示：

- bucket memo 目录
- reduce trace 文件

## 3. 产物清单

本阶段新增/接通的关键产物：

- `bucket_prompt_bundles/*.xml`
- `bucket_memos/*.json`
- `style_bible_reduce_trace.json`
- `style_bible_final.json`（来源已切换为 memos -> reducer）

## 4. 灰度测试

### 4.1 选取 bucket

本次灰测选取了 3 个代表 bucket：

- `resource_pressure`
- `institutional_pipeline`
- `dark_humor`

选择理由：

- `resource_pressure`：验证资源压力 / 债务 / 生存账本机制
- `institutional_pipeline`：验证制度流程语气、量化筛选、接口推进
- `dark_humor`：验证冷面黑色幽默是否被提炼成机制，而非空泛审美词

### 4.2 灰测执行结果

编译检查：

- `compileall` 通过

bucket memo 灰测输出目录：

- `D:\card\novel_pipeline\data\smoke\style_bible_phase2_gray`

memo 阶段结果：

- bucket memo 数：3
- batch memo 数：18
- memoed refs：54
- `evidence_refs` 机器校验违规数：0

各 bucket 汇总：

- `resource_pressure`：6 个 batch，6 个非空 batch，12 条聚合候选，18 个 allowed refs
- `institutional_pipeline`：6 个 batch，6 个非空 batch，12 条聚合候选，18 个 allowed refs
- `dark_humor`：6 个 batch，6 个非空 batch，12 条聚合候选，19 个 allowed refs

reducer 阶段结果：

- 产出 `style_bible_final.json`
- 产出 `style_bible_reduce_trace.json`
- `supporting_evidence`：17 条
- `evidence_map`：17 条
- reducer trace ref pool：43
- reduced refs：36
- `final.supporting_evidence.source_ref` 非法引用数：0

### 4.3 灰测结论

本次灰测说明：

- bucket synthesis prompt 红线已真正落地，`evidence_refs` 未出现自然语言污染
- 拒绝策略已可用，本次 18 个 batch 虽然都产出了候选，但输出已经被限制在机制句和 ref 列表内
- reducer 已经能从 memos 生成 partial final，并保持 final ref 与 trace ref pool 一致
- `dark_humor` bucket 没有退化成“很荒诞/很好笑”式空泛表达，而是提炼出了流程化去神圣化、制度口吻反差、算账式幽默等机制

## 5. 本阶段涉及文件

新增：

- `D:\card\novel_pipeline\prompts\style_bible_bucket_synthesis.md`
- `D:\card\novel_pipeline\prompts\style_bible_reduce.md`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_bucket_builder.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`
- `D:\card\novel_pipeline\STYLE_BIBLE_V2_PHASE_2_STAGE_REPORT_CN.md`

修改：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\models.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_contracts.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_router.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_builder.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_eval_contract.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\cli.py`

## 6. 已知边界

- 本轮灰测走的是新增的 `build-style-bible-bucket-memos` + `reduce-style-bible` 局部闭环，未执行完整 12 bucket 的正式 `build-style-bible` 生产跑批
- `style_bible_final.json` 仍保持 v1 schema，不含正式的 claim-evidence map；Phase 2 额外输出了 `style_bible_reduce_trace.json` 作为审计痕迹
- Grounding Schema / `style_bible_reasoning.json` / claim-evidence 强制映射仍属于下一阶段
- Anti-pattern registry、eval/judge v2、regression v2 仍未进入本次改造范围

## 7. 阶段结论

Phase 2 已完成可落地实现，并通过 3 个代表 bucket 的灰度测试：

- bucket memo 合成链路已接通
- `evidence_refs` 红线已通过 prompt + 代码净化双重封口
- builder 并发策略已改成 4-7 的受控并发
- final reducer 已切换为从 memos 生成 final
- 后续可继续进入 Grounding Schema 与 Eval/Judge v2 阶段
