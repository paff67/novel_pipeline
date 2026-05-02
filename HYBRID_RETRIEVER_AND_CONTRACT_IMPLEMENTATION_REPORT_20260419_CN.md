# Hybrid Retriever / Contract 实施报告（2026-04-19）

## 1. 本轮新增目标

在已经落地的：

1. `build-world-graph`
2. `export-world-graph-graphrag`
3. `evaluate-style-bible-ragas`

基础上，继续补齐 Hybrid RAG 的首轮“运行时前置骨架”，重点不是直接上服务，而是先把：

1. `Style Lane` 与 `World Lane` 的职责边界固化
2. 离线混合召回探针跑通
3. 世界图 community summary 再抬高一层

做成稳定、可测试、可文档化的中间层。

## 2. 代码新增与修改

### 2.1 新增模块

1. `src/novel_pipeline_stable/hybrid_rag_contract.py`
   - 新增 `build_hybrid_rag_contract(...)`
   - 输出 `hybrid_rag_contract.json`
   - 输出 `hybrid_rag_contract.md`
   - 将 `Style Lane / World Lane / Hybrid Policy` 的职责边界固化成可消费契约

2. `src/novel_pipeline_stable/hybrid_retriever.py`
   - 新增 `StyleRetriever`
   - 新增 `WorldGraphRetriever`
   - 新增 `HybridRetriever`
   - 新增 `run_hybrid_retrieval_probe(...)`
   - 输出 `hybrid_retrieval_probe.json`
   - 输出 `hybrid_retrieval_probe.md`

### 2.2 CLI 新增命令

1. `novel-pipeline build-hybrid-rag-contract`
2. `novel-pipeline probe-hybrid-retriever`

### 2.3 世界图增强

修改 `src/novel_pipeline_stable/world_graph_builder.py`：

1. 现有 `chapter_scope community` 之外，新增更高层的 scope community
2. 若存在 `story_node_scope.json`，生成 `story_node_scope` community
3. 若不存在节点范围，则生成 `global_scope` community
4. manifest 新增 `community_type_counts`

修改 `src/novel_pipeline_stable/world_graph_graphrag_export.py`：

1. 为 community reports 增加 `level`
2. `chapter_scope -> level 1`
3. `story_node_scope -> level 2`
4. `global_scope -> level 3`
5. 导出 `chapter_ids`

## 3. 这轮落地后的架构变化

### 3.1 新增的离线闭环

```text
Style Bible
  -> evaluate-style-bible
  -> judge-style-bible
  -> evaluate-style-bible-ragas

World Graph
  -> build-world-graph
  -> export-world-graph-graphrag

Hybrid Retrieval Scaffold
  -> build-hybrid-rag-contract
  -> probe-hybrid-retriever
```

### 3.2 Embedding 在这层中的角色

当前 `HybridRetriever` 的实现是“两阶段”：

1. 先做 lexical / semantic baseline 召回
2. 若传入 `--config` 且 embedding 已启用，则只对 shortlist 做 batch embedding rerank

这样做的目的：

1. 不强依赖 embedding 才能工作
2. 避免一上来就对全量节点/规则做重型索引
3. 与现有 `StableOpenAICompatibleEmbeddingClient` 的批量请求与缓存能力兼容

## 4. 测试

新增测试：

1. `tests/test_hybrid_rag_contract.py`
2. `tests/test_hybrid_retriever.py`

更新测试：

1. `tests/test_world_graph_builder.py`
2. `tests/test_world_graph_graphrag_export.py`

本轮验证命令：

```powershell
$env:PYTHONPATH='src'
python -m unittest `
  tests.test_hybrid_rag_contract `
  tests.test_hybrid_retriever `
  tests.test_world_graph_builder `
  tests.test_world_graph_graphrag_export
```

结果：`5/5` 通过。

## 5. 当前仍未完成的部分

这轮完成的是“运行时前置骨架”，不是最终服务化版本。仍未完成的核心部分有：

1. `Style-RAG` 正式导出层
2. 生产级 `GraphRAG` 查询层
3. `HybridRetriever` 的服务化接入
4. FastAPI / middleware 层
5. Qdrant 或其他向量库的正式接入

## 6. 建议的下一步

建议按以下顺序继续推进：

1. 先把 `Style-RAG` 正式导出层补出来
2. 再把 `HybridRetriever` 从“离线 probe”推进到“可复用 runtime component”
3. 然后再决定 Qdrant 是先接 Style lane、World lane，还是两边同时接
