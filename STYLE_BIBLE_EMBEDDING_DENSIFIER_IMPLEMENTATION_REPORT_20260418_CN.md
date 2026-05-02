# Style Bible Embedding Densifier 落地报告（2026-04-18）

## 1. 本次落地目标

本次按“渐进式接入 embedding”的采纳方案，把 embedding 只接入到 **section completeness 收口阶段最有收益、风险最低的一段**：

- `section_targets + path_targets` 控制面
- `section densifier` prompt 装配
- reducer 内的缺槽识别、语义召回、候选去重与保增量过滤

目标不是重做 router / evaluator / 全量 RAG，而是先解决：

1. `worldbook_binding.routing_hints / rag_worthy / worldbook_worthy` 在 section completeness 阶段容易“数量不够、内容不厚”的问题。
2. Densifier 补行时容易产出“字面改写旧规则”的问题。
3. 在不破坏当前稳定性的前提下，为后续 embedding 增强预埋统一调用与配置入口。

---

## 2. 已完成的代码改动

### 2.1 控制面与配置

新增或扩展了以下文件：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_section_targets.py`
- `D:\card\novel_pipeline\config\style_bible_section_targets.toml`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\config.py`
- `D:\card\novel_pipeline\config\formal_cn_gpt54_stable.toml`

完成内容：

- 引入 `SectionSlotSpec` 与 `SectionPathTarget`，把 densifier 目标从“只看最小条数”升级为“最小条数 + 槽位 coverage”。
- 在 `style_bible_section_targets.toml` 中新增 `[densify]` 配置段。
- 为以下 3 个高价值路径配置了 `path_targets`：
  - `worldbook_binding.routing_hints`
  - `worldbook_binding.rag_worthy`
  - `worldbook_binding.worldbook_worthy`
- 每个 `path_target` 都包含：
  - `target_count`
  - `max_new_rows`
  - `retrieval_top_k`
  - `bucket_allowlist`
  - `dedupe_threshold`
  - `slot_match_threshold`
  - `slot_specs`
- 在项目配置中新增 `EmbeddingConfig`，并支持：
  - `embedding.enabled`
  - `embedding.model`
  - `embedding.env_profile`
  - `embedding.max_batch_size`
  - `embedding.retrieval_top_k`
  - `embedding.dedupe_threshold`
  - `embedding.slot_match_threshold`
- `load_project_config()` 现在会单独解析 embedding 网关，并支持 `embedding.env_profile or model.env_profile` 的回退逻辑。

### 2.2 Embedding 基建

新增文件：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\embedding_client.py`

完成内容：

- 新增 `StableOpenAICompatibleEmbeddingClient`
- 支持 OpenAI-compatible embedding 网关调用
- 支持批量 embedding
- 支持多网关轮转 fallback
- 支持本地 cache
- 支持 request metrics 落盘

当前设计原则：

- embedding 初始化失败时，densifier 不会拖垮 reducer 主流程
- reducer 会记录 `embedding_unavailable` 状态并安全 no-op

### 2.3 Prompt 装配与 Densifier Prompt

新增或扩展文件：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_prompt_assembler.py`
- `D:\card\novel_pipeline\prompts\style_bible_section_densify.md`

完成内容：

- 新增 `assemble_section_densify_prompt(...)`
- 新增以下 payload 规范化逻辑：
  - slot specs
  - path target
  - densify bundle
  - retrieved reasoning entries
- 新增 `style_bible_section_densify.md`，把 densifier 的硬约束写死：
  - 只允许补当前 `target_path`
  - 只允许填 `missing_slots`
  - 只允许用检索回来的 `evidence_refs`
  - 禁止改写已有 row
  - 禁止输出泛主题总结

### 2.4 Reducer 集成

核心文件：

- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py`

完成内容：

- 在 reducer 中加入 densifier 相关辅助逻辑：
  - `_select_section_densify_requests`
  - `_compute_missing_slots`
  - `_retrieve_reasoning_entries_for_slots`
  - `_build_section_densify_bundle`
  - `_filter_section_densify_candidates`
  - `_run_section_densify_pass`
  - `_run_section_densify_passes`
  - `_apply_section_densify_metrics`
- 当前 reducer 执行顺序变为：
  1. local reduce
  2. repair pass
  3. pre-densify global merge
  4. section densify
  5. final global merge
- densifier 接入的 embedding 能力只用于：
  - 槽位缺失判定
  - 针对缺槽的 reasoning 检索
  - 候选规则与已有规则的柔性语义去重
  - 候选规则与缺槽的语义匹配过滤
- densifier 产物会写入独立目录：
  - `_section_densify/<path>/pass_xx/`
- `request_metrics` / `usage_metadata` / `reduce_trace` 已纳入 densifier 统计。

---

## 3. 本次新增测试

更新了以下测试文件：

- `D:\card\novel_pipeline\tests\test_style_bible_eval_profiles.py`
- `D:\card\novel_pipeline\tests\test_style_bible_v2_schema_contracts.py`
- `D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py`

新增覆盖点：

1. `section_targets` 能正确加载 densify `path_targets` 与 `slot_specs`
2. `assemble_section_densify_prompt(...)` 能正确装配：
   - `path_target`
   - `missing_slots`
   - `retrieved_reasoning_entries`
   - `runtime_identifiers`
3. `_filter_section_densify_candidates(...)` 能识别并丢弃语义上与已有规则重复的候选
4. hierarchical reducer 在 densify 集成下：
   - 已有 1 条路由规则
   - densifier 返回 1 条重复候选 + 1 条真实增量候选
   - 最终只保留 1 条新规则
   - 不会把 `routing_hints` 再吹胖

---

## 4. 实际执行的检查与测试

### 4.1 语法检查

执行：

```powershell
python -m py_compile `
  D:\card\novel_pipeline\src\novel_pipeline_stable\config.py `
  D:\card\novel_pipeline\src\novel_pipeline_stable\embedding_client.py `
  D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_prompt_assembler.py `
  D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_section_targets.py `
  D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_reducer.py `
  D:\card\novel_pipeline\tests\test_style_bible_eval_profiles.py `
  D:\card\novel_pipeline\tests\test_style_bible_v2_schema_contracts.py `
  D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py
```

结果：

- 通过，无语法错误

### 4.2 定向测试

执行：

```powershell
python -m unittest `
  D:\card\novel_pipeline\tests\test_style_bible_eval_profiles.py `
  D:\card\novel_pipeline\tests\test_style_bible_v2_schema_contracts.py `
  D:\card\novel_pipeline\tests\test_style_bible_hierarchical_reducer.py
```

结果：

- `Ran 31 tests in 1.156s`
- `OK`

### 4.3 全量测试回归

执行：

```powershell
python -m unittest discover -s D:\card\novel_pipeline\tests
```

结果：

- `Ran 62 tests in 1.495s`
- `OK`

### 4.4 Embedding 配置烟测

执行内容：

- 加载 `formal_cn_gpt54_stable.toml`
- 验证 embedding 配置是否被读取
- 验证 embedding 网关是否被解析
- 不输出任何密钥

结果：

```text
{
  'embedding_enabled': True,
  'embedding_model': 'text-embedding-3-small',
  'embedding_gateway_count': 3,
  'primary_embedding_base_url_configured': True
}
```

### 4.5 Embedding Client 初始化烟测

执行内容：

- 用正式配置初始化 `StableOpenAICompatibleEmbeddingClient`
- 不发起真实 embedding 请求
- 仅确认 client 可实例化

结果：

```text
{
  'model': 'text-embedding-3-small',
  'gateway_count': 3,
  'cache_dir_exists': True
}
```

---

## 5. 当前效果与边界

### 5.1 已经得到的收益

- embedding 已经从“架构建议”变成了 reducer 内可运行的真实控制链路
- densifier 不再只按条数补行，而是开始按 `coverage_slots` 补“缺的能力”
- 对重复候选已经有了语义级护栏，不会只靠字面 normalize
- embedding 失败不会拖死主 reducer，稳定性边界清晰

### 5.2 当前仍然刻意没有做的事情

这次没有接入以下能力，属于后续阶段：

- `style_bible_router.py` 的 hybrid routing 打分
- `style_bible_judge.py` / evaluator 的 embedding 幻觉校验
- reducer 之外的全局语义聚类 batching
- 向量库持久化写入
- RAG runtime 检索链路
- GraphRAG / Hybrid RAG 的实体图谱部分

### 5.3 当前接入范围的结论

当前 embedding 接入范围是 **合理且克制的**：

- 足够接近你现在的最高优先级 `section_completeness`
- 能直接服务 `routing_hints / rag_worthy / worldbook_worthy`
- 既能提升“补得更厚”，也能防止“补出来只是改写”
- 没有把 router / evaluator / online retrieval 一次性拖进来，风险可控

---

## 6. 下一步建议

建议按这个顺序继续推进：

1. 先用这版 reducer 跑一轮真实 `mini3 evaluate`，观察：
   - `section_completeness`
   - `worldbook_binding.*` 的最小条数达标率
   - `useful_routing_hint_ratio`
2. 如果 `routing_hints` 的增益明显，再把同样的 embedding 护栏扩到：
   - `rag_worthy`
   - `worldbook_worthy`
3. 等 section completeness 稳住后，再评估是否进入：
   - evaluator 幻觉校验池
   - Hybrid RAG 的向量检索落库

---

## 7. 本轮结论

本轮已经完成的是：

- control plane 落地
- embedding 网关配置接入
- reducer densifier 接入
- 语义去重与缺槽召回
- 单元测试与全量回归
- embedding 配置与 client 初始化烟测

当前代码状态适合进入下一步真实数据评估，而不是继续停留在方案讨论阶段。
