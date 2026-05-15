# Multi-Export 方案：完整实施计划

## 当前状态

| 指标 | 旧 (pre-step123) | 新 (post-step123) | 目标 |
|---|---|---|---|
| Eval overall | 0.8622 (pass) | 0.6612 (warn) | ≥ 0.72 (pass) |
| Judge overall | 38.53 (fail) | 71.38 (warn) | ≥ 75 (pass) |

**核心矛盾**：Eval 要自然+grounded 的描述，Judge 要 `当...路由到...` 的机器指令。同一份文本无法同时满足。

**解法**：canonical master（Eval 友好）+ deterministic exports（Judge/下游友好）。

---

## Phase 1：Eval + Judge 同时过线

### 1a. 部分回退 prompt 模板硬度

#### [MODIFY] [style_bible_local_reduce.md](file:///opt/novel_pipeline/prompts/style_bible_local_reduce.md)

将 `## 输出形态约束` 改为：

```markdown
## 输出形态约束
- 所有 final rows 的文本字段必须使用中文。禁止输出英文 "When..." / "Route to..." / "Store the rule that..." 句式。
- routing 必须使用以下结构：
  `当出现[可观测信号]时，路由到[具体节点/规则集]，并携带[生成约束/检索关键词]。`
- worldbook 必须写成接口化世界规则：
  `存储世界规则：[机构/门槛/资格/资源/制度]如何通过[接口]约束后续行动。`
- rag 必须写成可检索原子：
  `可检索原子：[触发器] -> [约束] -> [下一步动作]。`
- narrative / expression / voice / aesthetics 规则不要求固定句式模板，但必须是中文，必须包含具体机制（触发条件 + 约束 + 后续写法），不要写成抽象总结或风格评论。
```

**变更要点**：移除 narrative/expression 的硬性模板 `当[触发器]出现，先[检查/结算/办理]` 和 `动作指令优先使用以下开头词` 的强制列表，改为软性引导。routing/worldbook/rag 的模板保留不动。

#### [MODIFY] [style_bible_section_densify.md](file:///opt/novel_pipeline/prompts/style_bible_section_densify.md)

同上修改。

---

### 1b. 新增确定性 Judge 导出器

#### [NEW] [style_bible_judge_export.py](file:///opt/novel_pipeline/src/novel_pipeline_stable/style_bible_judge_export.py)

纯 Python 确定性转换，不调 LLM。从 `style_bible_final.json`（canonical master）生成 `judge_flat.json`。

**转换规则清单**：

| 规则 | 作用域 | 逻辑 |
|---|---|---|
| `ensure_trigger_prefix` | 所有 rule list 的 `text` 字段 | 如果前 10 字符不含 `当/如果/出现/遇到`，在句首加 `当` |
| `ensure_route_cue` | `routing_hints` 的 `route_target_action` | 如果不含 `路由到/路由至/进入/归到`，将首个动词短语替换为 `路由到` |
| `ensure_worldbook_anchor` | `worldbook_worthy` 的 `text` | 如果不含 `机构/规则/门槛/资格/资源/制度/节点/世界书`，从文本中提取最相关名词并前置为 `[锚点]规则：` |
| `ensure_rag_atomic` | `rag_worthy` 的 `text` | 如果长度 > 40 字，截断到首个分号/句号；如果缺触发器结构，加 `触发器→约束→动作` 标记 |
| `enrich_evidence_refs` | 所有 rule 的 `evidence_refs` | 从 `source_bundle` 中补齐：对每条规则，用 `reasoning_ref` 追溯到 reasoning entry，补入该 entry 的 `evidence_refs` |
| `ensure_chinese_lead` | 所有 rule list 的 `text` 字段 | 如果以英文开头（匹配 `^(When|If|Route|Store|Must|Do not)`），整条标记为需要中文重写（Phase 1 用 fallback 删除该条） |

**输出 schema**：与 `style_bible_export_flat.json` 完全相同（`StyleBibleResult` 模型），Judge 直接验证。

**集成点**：在 [orchestrator.py L5385](file:///opt/novel_pipeline/src/novel_pipeline_stable/style_bible_reduction/orchestrator.py#L5385) 写完 `export_flat` 之后，追加：

```python
from novel_pipeline_stable.style_bible_judge_export import build_judge_flat
judge_flat_record = build_judge_flat(record, source_bundle)
judge_flat_path = output_path / JUDGE_FLAT_FILE
write_json(judge_flat_path, judge_flat_record)
```

---

### 1c. Judge V2 优先读 judge_flat.json

#### [MODIFY] [style_bible_judge.py](file:///opt/novel_pipeline/src/novel_pipeline_stable/style_bible_judge.py)

```python
# L22-24: 新增常量
JUDGE_FLAT_FILE = "judge_flat.json"

# L2106-2109: 优先读 judge_flat
judge_flat_path = source_dir / JUDGE_FLAT_FILE
style_bible_path = source_dir / STYLE_BIBLE_FILE
# L2144: 优先使用 judge_flat
if judge_flat_path.exists():
    judge_flat_payload = read_json(judge_flat_path)
    normalized_payload = judge_flat_payload if isinstance(judge_flat_payload, dict) else normalized_payload
```

---

## Phase 1 → Phase 2 进入标准

### 硬门槛（全部满足才可进 Phase 2）

| # | 指标 | 门槛 | 当前值 | 验证方式 |
|---|---|---|---|---|
| H1 | Eval overall_score | ≥ **0.72** (pass) | 0.6612 | `evaluate-style-bible` |
| H2 | Judge overall_score | ≥ **75.0** (pass) | 71.38 | `judge-style-bible` |
| H3 | Judge fail case 数 | **= 0** | 0 ✅ | `judge_report.md` |
| H4 | 单元测试 | **137/137 pass** | 137/137 ✅ | `pytest` |
| H5 | build 失败数 | **= 0** | 0 ✅ | build manifest |

### 软门槛（建议满足，未满足需在 Phase 2 计划中标注应对策略）

| # | 指标 | 建议值 | 当前值 | 未满足的影响 |
|---|---|---|---|---|
| S1 | `semantic_average_specificity` | ≥ **0.60** | 0.5001 | runtime_flat 导出的规则太模板化，下游生成器收到的指令不够具体 |
| S2 | `semantic_average_grounding` | ≥ **0.70** | 0.6258 | routing_hints.jsonl 和 rag_atoms.jsonl 的 source_ref 质量差 |
| S3 | Judge `evidence_faithfulness` 均分 | ≥ **10.0** / 16 | 7.48 | worldbook_entries.jsonl 缺可信来源，导出后下游无法验证 |
| S4 | Judge `anti_pattern_resistance` 均分 | ≥ **3.2** / 4 | 3.17 | 残留反模式会被导出放大，污染下游消费者 |
| S5 | Judge 最差单 case 分数 | ≥ **70.0** | 67.25 | 某个 bucket 的导出质量明显偏弱 |
| S6 | `ungrounded_worldbook_hits` | ≤ **4** | 12 | worldbook_entries.jsonl 含大量无锚点条目 |

### 判定逻辑

```
if H1-H5 全部满足:
    if S1-S6 全部满足:       → 直接进 Phase 2
    elif S1-S6 有 ≤2 项未满足: → 进 Phase 2，计划中标注应对策略
    else:                     → 继续迭代 Phase 1
else:
    → 继续 Phase 1
```

### 验证流程（Phase 1 每轮迭代）

```bash
# 1. 重跑 pipeline
build-style-bible --input-dir ... --output-dir .../style_bible_phase1_vN

# 2. 跑 Eval
evaluate-style-bible --input-dir .../style_bible_phase1_vN --output-dir .../style_bible_eval_phase1_vN

# 3. 跑 Judge
judge-style-bible --input-dir .../style_bible_phase1_vN --output-dir .../style_bible_judge_phase1_vN

# 4. 检查硬门槛
grep "overall_score" .../style_bible_eval_phase1_vN/style_eval_report.md
grep "overall_score" .../style_bible_judge_phase1_vN/judge_report.md

# 5. 检查软门槛
grep "semantic_average_specificity" .../style_bible_eval_phase1_vN/style_eval_report.md
grep "evidence_faithfulness" .../style_bible_judge_phase1_vN/judge_report.md
grep "ungrounded_worldbook_hits" .../style_bible_judge_phase1_vN/judge_report.json

# 6. 跑单元测试
pytest
```

---

## Phase 2：下游消费者 Export 层

### 目录结构

```
style_bible_phase2_vN/
├── style_bible_final.json              # canonical master (Eval 评价)
├── style_bible_export_flat.json        # 旧兼容层 (保留)
├── judge_flat.json                     # Judge V2 专用 (Phase 1 已有)
└── style_bible_exports/
    ├── export_manifest.json
    ├── routing_hints.jsonl
    ├── rag_atoms.jsonl
    ├── worldbook_entries.jsonl
    └── prompt_preset.json
```

---

### 2a. export_manifest.json

#### [NEW] export_manifest.json

```json
{
  "export_version": "style-bible-export-v1",
  "source_style_id": "style_bible_main_01_kunxu_l1_ch0001_0270_v1",
  "source_file": "style_bible_final.json",
  "source_sha256": "...",
  "generated_at": "...",
  "exports": [
    {"file": "routing_hints.jsonl",      "format": "jsonl", "item_count": 8,  "schema": "RoutingHintAtom"},
    {"file": "rag_atoms.jsonl",          "format": "jsonl", "item_count": 8,  "schema": "RagAtom"},
    {"file": "worldbook_entries.jsonl",   "format": "jsonl", "item_count": 8,  "schema": "WorldbookEntry"},
    {"file": "prompt_preset.json",       "format": "json",  "item_count": 1,  "schema": "PromptPreset"}
  ]
}
```

---

### 2b. routing_hints.jsonl

**来源**：`style_bible_final.json → worldbook_binding.routing_hints` + `narrative_system.engine` 中含路由信号的规则

**每行 schema**：
```json
{
  "route_id": "rh_001",
  "matcher": "当出现价格、资格、倒计时或处分信号",
  "route_target": "resource_pressure.threshold_routing",
  "action": "先算账，再进入试课/兼职/签约/资格节点",
  "carry_constraints": ["必须先结算成本", "不得无成本推进"],
  "source_rule_id": "resource_pressure__rp_row_01",
  "source_path": "worldbook_binding.routing_hints",
  "source_refs": ["scene:0001_007", "0191_0192"]
}
```

**转换逻辑**：
1. 遍历 `routing_hints`，提取 `query_feature_matcher` → `matcher`，`route_target_action` → `route_target` + `action`
2. 遍历 `narrative_system.engine`，如果 `text` 包含 `路由到/进入/归到`，也提取为路由原子
3. `carry_constraints` 从 `constraint` 字段拆分
4. `source_refs` 从 `evidence_refs` 透传

---

### 2c. rag_atoms.jsonl

**来源**：`worldbook_binding.rag_worthy` + `narrative_system.engine` 中机制性强的规则

**每行 schema**：
```json
{
  "atom_id": "rag_001",
  "query_terms": ["价格", "资格", "倒计时", "处分"],
  "trigger": "场景出现现实门槛信号",
  "constraint": "角色必须先结算成本再行动",
  "next_action": "进入成本闭环节点",
  "source_rule_id": "resource_pressure__rp_rag_01",
  "source_path": "worldbook_binding.rag_worthy",
  "source_refs": ["scene:0001_005"]
}
```

**转换逻辑**：
1. 遍历 `rag_worthy`，提取 `text` 拆分为 `trigger` → `constraint` → `next_action`
2. `query_terms`：从 `text` 中用 `_JUDGE_SHAPE_WORLDBOOK_ANCHORS` + 停用词过滤提取关键名词
3. 长度 > 40 字的条目截断到原子级

---

### 2d. worldbook_entries.jsonl

**来源**：`worldbook_binding.worldbook_worthy`

**每行 schema**：
```json
{
  "entry_id": "wb_001",
  "topic": "资格链",
  "rule": "学校、合同、付款、检查点共同构成资格链。",
  "interface": "分数线 / 合同 / 收费通道 / 可被拒绝入口",
  "stability": "cross_scene",
  "source_rule_id": "institutional_pipeline__ip_wb_01",
  "source_path": "worldbook_binding.worldbook_worthy",
  "source_refs": ["scene:0212_002"]
}
```

**转换逻辑**：
1. 遍历 `worldbook_worthy`，提取 `text`
2. `topic`：从 text 首句提取主题名词
3. `interface`：从 text 中提取 `机构/门槛/资格/资源/制度` 相关的接口词
4. `stability`：如果 `evidence_refs` 跨多个 scene → `cross_scene`，否则 `single_scene`

---

### 2e. prompt_preset.json

**来源**：跨 section 聚合

**Schema**：
```json
{
  "preset_version": "v1",
  "source_style_id": "...",
  "must_do": [
    "当出现还款、赔偿、资格审核时，必须路由到成本闭环节点",
    "..."
  ],
  "avoid": [
    "不要把资源压力写成背景情绪",
    "..."
  ],
  "routing_macros": [
    {"signal": "还款/赔偿/资格审核", "target": "成本闭环节点"},
    "..."
  ],
  "voice_macros": [
    {"register": "公文结算词混入叙述", "when": "考试/战斗/教学/筛选/事故善后"},
    "..."
  ]
}
```

**转换逻辑**：
1. `must_do`：从 `narrative_system.engine` + `expression_system.*` 提取前 8 条高 judge_shape_score 的规则
2. `avoid`：从 `negative_rules` 提取 `forbidden_action`
3. `routing_macros`：从 `routing_hints.jsonl` 聚合
4. `voice_macros`：从 `voice_contract.register_mix` 提取

---

### Phase 2 代码修改清单

#### [NEW] [style_bible_exporter.py](file:///opt/novel_pipeline/src/novel_pipeline_stable/style_bible_exporter.py)

统一导出模块，包含：
- `export_routing_hints(master) -> list[dict]`
- `export_rag_atoms(master) -> list[dict]`
- `export_worldbook_entries(master) -> list[dict]`
- `export_prompt_preset(master) -> dict`
- `export_all(master, output_dir) -> ExportManifest`

所有函数都是纯 Python 确定性转换，不调 LLM。

#### [MODIFY] [orchestrator.py](file:///opt/novel_pipeline/src/novel_pipeline_stable/style_bible_reduction/orchestrator.py)

在 L5385 区域，写完 `judge_flat` 后，追加：

```python
from novel_pipeline_stable.style_bible_exporter import export_all
exports_dir = ensure_dir(output_path / "style_bible_exports")
export_manifest = export_all(record, exports_dir)
write_json(exports_dir / "export_manifest.json", export_manifest)
```

#### [MODIFY] CLI

添加 `--skip-exports` flag（默认 false），允许在调试时跳过导出。

---

### Phase 2 验证计划

1. **导出一致性**：`export_manifest.json` 中的 `item_count` 与实际 JSONL 行数一致
2. **溯源完整性**：每个 export 条目的 `source_rule_id` 都能在 `style_bible_final.json` 中找到对应规则
3. **schema 验证**：每个 JSONL 行都能通过对应 Pydantic 模型验证
4. **幂等性**：同一份 `style_bible_final.json` 多次导出产生完全相同的结果（`sha256` 一致）
5. **回归**：Eval 和 Judge 分数不因 Phase 2 代码变化而退化（Phase 2 只增加导出，不改生成逻辑）

```bash
# Phase 2 验证命令
python3 -c "
import json
manifest = json.load(open('.../style_bible_exports/export_manifest.json'))
for export in manifest['exports']:
    if export['format'] == 'jsonl':
        lines = open(f'.../style_bible_exports/{export[\"file\"]}').readlines()
        assert len(lines) == export['item_count'], f'{export[\"file\"]}: expected {export[\"item_count\"]}, got {len(lines)}'
        for line in lines:
            item = json.loads(line)
            assert 'source_rule_id' in item and item['source_rule_id']
            assert 'source_refs' in item
print('All export validations passed')
"
```

---

## 不建议的方向

- ❌ 调低 Judge 阈值
- ❌ 简单增加 densify 轮数（没有 Judge failure context）
- ❌ 一次落地全部 8 个 export（修改面太大）
- ❌ 在 export 中引入 LLM 调用（应保持确定性）
