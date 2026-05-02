# Hybrid RAG 首轮施工清单

日期：2026-04-19

适用仓库：`D:\card\novel_pipeline`

目标：按照当前确定的优先级，先落地最值得采纳、且对现有主链风险最低的能力。

---

## 1. 本轮施工原则

1. 不重写现有 `Style Bible v2` 主链。
2. 不把外部框架整套接进来替代现有代码。
3. 先做“资产导出层”和“离线评估层”的增强。
4. 先补可观测性、可导出性、可评估性，再推进运行时检索层。

---

## 2. 首轮优先级

### P0：本轮必须完成

- [x] 输出一份明确的施工清单
- [ ] 新增 `World Graph -> GraphRAG BYOG` 导出层
- [ ] 新增 `export-world-graph-graphrag` CLI
- [ ] 新增 `Style Bible` 的 `Ragas-ready` 离线评估 runner
- [ ] 新增 `evaluate-style-bible-ragas` CLI
- [ ] 为以上两项补测试
- [ ] 更新蓝图文档
- [ ] 更新 `README_CN.md`

### P1：紧随其后

- [ ] 为 `World Graph` 增加更强的 `community reports`
- [ ] 明确 `Global / Local / Hybrid` world query contract
- [ ] 为 `Style-RAG` / `World Graph` 增加统一 retrieval manifest
- [ ] 明确 Qdrant 接入点

### P2：第二阶段

- [ ] 抽象 `Local Reduce / Densify / Judge` 的 Python Signature
- [ ] 建 `prompt contract bench`
- [ ] 建 `StyleRetriever / WorldGraphRetriever / HybridRetriever`
- [ ] 评估是否需要 LangGraph 化控制平面

---

## 3. 本轮代码落点

### 3.1 GraphRAG BYOG 导出

计划新增：

- `src/novel_pipeline_stable/world_graph_graphrag_export.py`
- `tests/test_world_graph_graphrag_export.py`

目标产物：

- `graphrag_entities.jsonl`
- `graphrag_relationships.jsonl`
- `graphrag_text_units.jsonl`
- `graphrag_community_reports.jsonl`
- `graphrag_manifest.json`

设计原则：

1. 不改动 `build-world-graph` 现有输出合同。
2. 以现有 `world_graph_*` 离线资产为输入。
3. 先做稳定的 JSONL/manifest 导出，不强绑新的外部依赖。

### 3.2 Ragas-ready 离线评估

计划新增：

- `src/novel_pipeline_stable/style_bible_ragas_eval.py`
- `tests/test_style_bible_ragas_eval.py`

目标产物：

- `ragas_rows.jsonl`
- `ragas_dataset.json`
- `ragas_report.json`
- `ragas_report.md`

设计原则：

1. 先做 `Ragas-ready` runner，不把它直接接成 hard gate。
2. 聚焦：
   - `routing_hints`
   - `worldbook_worthy`
   - `rag_worthy`
3. 优先给出：
   - faithfulness proxy
   - relevance proxy
   - grounding ratio

---

## 4. 文档更新要求

### 蓝图文档

需要补充：

1. 首轮施工项已经从“研究阶段”进入“实现阶段”
2. `GraphRAG BYOG` 是 `World Graph Build` 的下一步，而不是重做 canon
3. `Ragas-ready` runner 是 judge/eval 的增强层，而不是替代层

### README_CN

需要补充：

1. 新增命令：
   - `export-world-graph-graphrag`
   - `evaluate-style-bible-ragas`
2. 新增文档索引：
   - 本施工清单
   - 开源项目调研报告
3. 明确当前 Hybrid RAG 的首轮落地状态

---

## 5. 验收标准

### GraphRAG BYOG 导出验收

1. 能从现有 `build-world-graph` 输出目录继续导出
2. 导出文件数与 manifest 一致
3. 不破坏现有 `world_graph_builder` 测试

### Ragas-ready runner 验收

1. 能读取 `style_bible_final.json` 与 `style_bible_source_bundle.json`
2. 能为 `routing_hints / worldbook_worthy / rag_worthy` 生成逐条评估行
3. 能输出 JSON 和 Markdown 报告

### 文档验收

1. 蓝图文档反映最新落地状态
2. `README_CN.md` 能直接看到新的命令和施工入口

---

## 6. 本轮完成后的下一步

1. 为 `World Graph` 增加更强的 `community summarization`
2. 为 `Style-RAG` / `World Graph` 建统一 retrieval contract
3. 再决定是否推进 Qdrant 与运行时 Hybrid Retriever

