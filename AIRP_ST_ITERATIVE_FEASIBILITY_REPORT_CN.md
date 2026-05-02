# 基于大节点迭代的 AIRP / SillyTavern 资产工程可行性报告

## 1. 结论摘要

结论是：**可行，而且应该采用“大节点版本化迭代”作为正式路线。**

原因不是抽象意义上的“可以试试”，而是当前仓库已经具备了这个路线最关键的三个前提：

1. 已经存在可复用的**节点版语义版本目录结构**，每个主节点都能独立落地产物。
2. `build-style-bible` 与 `evaluate-style-bible` 已经落地，说明“节点内生成 + 节点内验收”的最小闭环已经出现。
3. SillyTavern 生态本身已经具备世界书、RAG、提示词装配、变量状态、事件脚本和自定义 UI 的承载能力，适合做“运行时投影层”。

因此，最推荐的方向不是等全书所有离线资产都完全结束再一次性交付，而是把每个**已确认的大节点**当成一个可发布、可评估、可导出、可回归的语义版本单元，持续迭代。

这条路线的核心判断是：

- **离线权威层**：Canon / Style Bible / 导出资产 / 评估结果
- **SillyTavern 运行时层**：世界书、Data Bank、提示词模板、变量卡、FSM 状态投影、Slash/JS 交互
- **节点发布单元**：`node_id`

也就是说，SillyTavern 不应成为权威真相源，而应成为节点资产的消费端。

---

## 2. 当前仓库状态快照（2026-04-08）

基于当前仓库实测，现状已经足以支撑“先做节点 1-2 的评估闭环 + ST 资产试点”。

### 2.1 抽取进度

- `scenes` 总量：`3983`
- `facts` 已完成：`3109`
- `facts` 当前覆盖率：约 `78.1%`
- `style window` 已完成：`420`

其中：

- 节点 1 `main_01_kunxu_l1_ch0001_0270`：`1133 / 1133`，fact 完整
- 节点 2 `main_02_kunxu_l2_civil_ch0271_0510`：`972 / 972`，fact 完整
- 节点 3 `main_03_kunxu_l2_artificer_league_ch0511_0654`：`557 / 559`，几乎完整
- 节点 4 `main_04_post_league_ch0655_0738`：`447 / 572`
- 节点 5 `main_05_sect_arc_ch0739_0841`：`0 / 747`

### 2.2 已落地的节点版能力

当前仓库已经存在节点版流水线脚本：

- `scripts/run_story_node_pipeline_formal_cn_gpt54_stable.ps1`

该脚本已能顺序执行：

1. 节点版 `build-canon`
2. 节点版 `build-style-bible`
3. 节点版 `evaluate-style-bible`

当前节点版产物已经落地到：

- `data/semantic_versions_formal_cn_gpt54_stable/main_01_kunxu_l1_ch0001_0270/`
- `data/semantic_versions_formal_cn_gpt54_stable/main_02_kunxu_l2_civil_ch0271_0510/`

### 2.3 当前 Style Bible 评估现状

节点 1：

- `overall_score = 97.04`
- 全部检查项 `pass`

节点 2：

- `overall_score = 95.34`
- 总体仍 `pass`
- 但 `supporting_evidence`、`routing_hints`、`worldbook_binding` 出现 `warn`

这说明当前规则版 evaluator 已经不是“摆设”，它已经能指出真正和下游导出相关的问题，尤其是：

- 证据 `source_ref` 不规范
- 路由提示过于主题化，触发条件不够具体
- `rag_worthy / worldbook_worthy` 仍偏抽象，不够原子化

### 2.4 当前最关键的缺口

蓝图中明确列出的三个缺口仍然存在：

- `judge-style-bible`
- `compare-style-runs`
- `regress-style-quality`

也就是：

- C. Gold set / 基准集
- D. 自动裁判模型
- E. 量化指标与回归

这意味着当前已经有“生成 + 规则验收”，但还没有“稳定比较 + 历史回归 + 对抗漂移”的工程能力。

---

## 3. 为什么“大节点迭代”非常适合这个项目

## 3.1 它天然符合当前目录和数据结构

你现在的目录结构已经不是“全书一次性结果”思路，而是：

- `story_nodes_confirmed.json` 定义主节点边界
- `semantic_versions_formal_cn_gpt54_stable/<node_id>/...` 存放节点版资产

这本质上就是一个**语义版本系统**。既然目录和脚本都已经按节点切分，继续把它产品化为“大节点版本发布”是顺势而为，而不是另起炉灶。

## 3.2 它比“全书完工后统一导出”更适合评估

如果一次性拿全书做 Style Bible、世界书、RAG 和 FSM：

- 人工审阅负担太大
- 问题定位成本太高
- 回归测试难以稳定复现
- 每次 prompt / schema 调整都会影响整书

按主节点迭代则能把评估单元控制在一个稳定范围内：

- 同一节点的语义风格更稳定
- Gold set 更容易人工定义
- judge 更容易学会“这个节点应该长什么样”
- 运行时资产更容易做分期上线

## 3.3 它比“按章节”更适合 ST 资产

章节太细，会导致：

- 世界书条目碎片化
- 路由逻辑过拟合到单章
- FSM 状态跳转过于频繁
- 角色卡文风预设不断抖动

大节点则刚好适合作为：

- 世界状态阶段
- 角色行为阶段
- 风格预设阶段
- RAG 检索域
- 剧情状态机的大状态

也就是说，**章节适合抽取，主节点适合发布。**

## 3.4 它允许“前两节点先上线体验闭环”

当前节点 1 和节点 2 已经 fact 完整，而且节点版 Style Bible 与 evaluator 都有实物产出。  
这意味着你完全没必要等节点 3-5 全部补完后再去做 ST 集成试验。

最合理的策略是：

- 用节点 1 做第一版 ST 资产闭环
- 用节点 2 做第一次“跨节点升级”
- 用节点 3-5 在 fact 完整后继续扩展

这样能尽早发现真正影响体验的问题，而不是把大量工作压到最后。

---

## 4. 对最终目标的工程拆分建议

你的最终目标不是只做一张角色卡，而是做一整套可在 SillyTavern 中持续迭代的资产系统。建议把目标拆成五个发布面：

1. **角色卡核心**
   - 人设、开场、基础关系、行为边界

2. **世界书 / Lorebook**
   - 机构、规则、门槛、术语、地域、势力、常识

3. **RAG / Data Bank**
   - 长设定、节点事实、关系链、制度细节、事件证据

4. **剧情状态机 / 路由规则**
   - 大节点状态
   - 分支触发条件
   - 禁止越级推进的规则

5. **文风预设 / Prompt 组件**
   - 叙事距离
   - 对话风格
   - 黑色幽默机制
   - 压力轴线
   - 负向约束

其中最重要的边界是：

- **Canon / Style Bible / Gold set / 评估报告** 放在离线工程侧
- **世界书 / Data Bank / Prompt 模板 / MVU 状态展示 / Slash 脚本** 放在 ST 运行时侧

不要把 ST 里的变量、模板或世界书内容当成权威源，而应把它们当成**离线资产的投影**。

---

## 5. SillyTavern 侧的可行性判断

结合外部资料，ST 侧的基础承载能力是足够的，甚至比当前离线评估层更成熟。

### 5.1 官方能力已经能承载世界书与 RAG

从 SillyTavern 官方文档看：

- `World Info / Lorebook` 可以作为动态注入的知识层，甚至在导出角色时嵌入角色卡。
- `Data Bank` 已经能提供基于附件的检索增强，但角色级附件不会随角色卡一起导出。
- `Prompt Manager / Context Template` 已经支持自定义提示词装配。

这直接导出两个结论：

1. **世界书适合做“可随角色卡发布”的节点资产**
2. **RAG 更适合做“独立导入包”，不要假设它能自动跟角色卡走**

### 5.2 社区插件生态适合做运行时投影层

你给出的几条参考路线刚好构成一个合理分工：

- **ST-Prompt-Template**：适合做提示词模板化、变量插槽、节点级风格片段装配
- **Tavern Helper / JS-Slash-Runner 文档生态**：适合做世界书 CRUD、变量 CRUD、事件触发、脚本桥接
- **MVU / 变量卡路线**：适合做状态可视化和阶段 UI 投影

这意味着你的“后端有限状态机剧情节点判断 + ST 前端变量卡”思路并不违和，反而很契合现有生态。

### 5.3 但不适合把权威 FSM 逻辑直接塞进模板层

这里要特别克制。

ST-Prompt-Template、EJS、MVU 和外部脚本都很强，但它们更适合：

- 读取状态
- 渲染状态
- 投影状态
- 执行受控动作

它们不适合直接承担：

- 权威剧情推进判断
- Canon 级真相判定
- 复杂多条件状态迁移的唯一来源

最稳妥的设计是：

- **FSM 规则本体**：离线 JSON / 规则文件 / 后端服务
- **ST 插件脚本**：只负责调用、缓存、回写和展示

换句话说，模板层负责“怎么用”，而不是“什么是真的”。

### 5.4 需要注意第三方脚本风险

JS-Slash-Runner 文档明确提醒了第三方脚本风险。  
因此如果后续真要把这一套做成长期项目，建议：

- 尽量使用**自有脚本仓库**
- 给脚本做**版本号**
- 固定导入 URL 或本地分发方式
- 避免把关键逻辑依赖在不可控的远程脚本上

这不是功能可行性问题，而是长期维护和安全边界问题。

---

## 6. C / D / E 三块的建议落地方式

## 6.1 C. Gold set / 基准集

### 目标

把“好的节点版 Style Bible 应该长什么样”固定成一个可复用的评价基准，而不是每次靠人工临场感觉。

### 建议不要做成什么

不要一开始就试图做“整本书唯一标准答案”。  
Style Bible 不是数学题，真正可行的 gold set 应该是**约束集**，而不是唯一文本。

### 建议做成什么

建议按 **case-based gold set** 来做，每条 case 只定义“必须命中 / 禁止犯错 / 证据要求 / 下游用途”。

建议目录：

```text
data/eval/style_gold_set/v1/
  index.json
  cases/
    main_01_*.json
    main_02_*.json
```

每个 case 建议包含：

- `case_id`
- `node_id`
- `scope_type`：`node` / `window` / `scene_bundle`
- `source_refs`
- `required_axes`
- `required_mechanisms`
- `forbidden_patterns`
- `required_downstream_surfaces`
- `evidence_expectations`
- `human_notes`

### Gold set v0 的最佳起点

不要从全书开始，直接从**节点 1 + 节点 2** 开始。

建议最小规模：

- 节点 1：`12-18` 个 case
- 节点 2：`12-18` 个 case

优先覆盖：

- 资源 / 债务压力
- 教育筛选 / 阶层门槛
- 身体异化 / 改造成本
- 制度荒诞 / 流程口吻
- 黑色幽默 / 冷面吐槽
- 路由提示与世界书绑定

这样做的好处是：

- 数据已经完整
- 评估范围稳定
- 能很快为 judge 和 regression 提供样本

## 6.2 D. 自动裁判模型（`judge-style-bible`）

### 目标

让模型不只是“看好不好”，而是沿着固定 rubric 去判断：

- 结论是否空泛
- 是否机制化
- 是否有证据支撑
- 是否遗漏关键轴线
- 是否适合导出到世界书 / RAG / 路由 / prompt preset

### 输入建议

`judge-style-bible` 输入不应只有 candidate，还应至少包括：

- `style_bible_final.json`
- `style_bible_source_bundle.json`
- `gold_set case` 或 `gold rubric`
- `rules_config`
- `run_manifest.json`

### 输出建议

```text
judge_report.json
judge_report.md
judge_rows.jsonl
```

`judge_rows.jsonl` 可以按 criterion 粒度落盘，便于之后做 compare 和 regress。

### 推荐裁判维度

- `evidence_faithfulness`
- `mechanism_specificity`
- `axis_coverage`
- `routing_executability`
- `worldbook_exportability`
- `rag_atomicity`
- `prompt_preset_usability`
- `anti_genericity`

### 关键实现原则

1. **裁判必须看到 source bundle，而不只看 style bible**
   - 否则会把“写得像样”误判成“真实可靠”

2. **裁判输出必须绑定字段路径和 source_ref**
   - 否则无法用于修正

3. **裁判最好做 criterion 级别打分，而不是只给总分**
   - 否则无法回归

4. **裁判不要直接取代规则版 evaluator**
   - 规则版负责硬门槛
   - judge 负责语义质量

## 6.3 E. 量化指标、对比和回归

这部分建议拆成两个命令，而不是一个大命令。

### 1. `compare-style-runs`

用途：比较两个候选运行结果谁更好。

输入：

- `run_a`
- `run_b`
- `node_id`
- `gold_set`
- `judge rubric`

输出：

- 每项指标的增减
- pairwise judge 胜负
- 差异摘要
- 风险提示

适合场景：

- 换模型
- 改 prompt
- 改 sampling
- 改 style bundle 裁剪策略

### 2. `regress-style-quality`

用途：判断一个新候选是否相对基线退化。

输入：

- `baseline run registry`
- `candidate run`
- `threshold config`

输出：

- `pass / warn / fail`
- 退化项列表
- 历史分数变化
- 是否允许进入下游导出

适合场景：

- prompt 改版后的 CI
- judge prompt 改版后的复核
- 规则 config 改版后的稳定性检测

### 推荐量化指标

建议保留当前规则版 evaluator 的现有指标，并向下游可用性继续推进。

建议至少固定以下指标：

- `schema_validity_score`
- `section_completeness_score`
- `required_axis_coverage_score`
- `supporting_evidence_score`
- `actionability_score`
- `routing_hint_usefulness_score`
- `worldbook_binding_usefulness_score`
- `generic_language_penalty`
- `judge_mechanism_specificity_score`
- `judge_downstream_usability_score`
- `judge_evidence_faithfulness_score`
- `pairwise_win_rate`
- `regression_severity_index`

### 回归闸门建议

建议把回归闸门做成两层：

1. **硬门槛**
   - schema 不可坏
   - evidence 不能大面积失效
   - 路由和 worldbook 不能退到不可用

2. **软门槛**
   - 总分不能低于基线若干阈值
   - 某些维度允许小幅波动

这样可以避免：

- 一点措辞变化就误判回归
- 真实退化却因为总分够高被放过

---

## 7. 在做 compare / regress 之前，必须先补的元数据契约

当前仓库已经有一个会影响后续 compare/regress 的隐患：  
不同节点产出的 `style_bible_final.json` 里，`style_id` 目前并不天然唯一。

这会导致：

- 跨节点记录混淆
- 历史比较不可靠
- baseline registry 难以索引

因此在正式做 compare/regress 前，建议先冻结一份 `run_manifest.json` 契约。

至少要包含：

- `run_id`
- `node_id`
- `scope`
- `model_name`
- `prompt_version`
- `prompt_hash`
- `rules_version`
- `style_bible_schema_version`
- `source_bundle_hash`
- `built_at`
- `git_commit`

并建议把 `style_id` 也改成带节点和版本信息的形式，例如：

```text
style_bible_main_01_kunxu_l1_ch0001_0270_v1
```

如果不先做这一步，`compare-style-runs` 和 `regress-style-quality` 会在工程上很难落地。

---

## 8. 面向 ST 的资产编排建议

建议把每个节点的发布资产做成统一目录：

```text
semantic_versions/<node_id>/
  canon/
  style_bible/
  style_bible_eval/
  exports/
    worldbook/
    rag/
    prompt_presets/
    route_rules/
    mvu_state/
```

### 8.1 世界书

建议世界书条目只放：

- 机构
- 地点
- 制度规则
- 门槛
- 角色常识
- 术语

不要把整段 Style Bible 直接塞进世界书。

### 8.2 RAG / Data Bank

建议 RAG 侧主要放：

- 长事实
- 事件链
- 关系演化
- 复杂制度说明
- 不适合关键词触发的长文背景

并把它作为**独立导入包**，不要假设和角色卡天然同生命周期。

### 8.3 Prompt Preset / Style Preset

建议从 Style Bible 编译出可装配片段，而不是直接把整本 style bible 贴进系统提示词。

至少拆成：

- `narrative_rules`
- `dialogue_rules`
- `humor_rules`
- `pressure_axes`
- `negative_rules`
- `routing_hint_fragments`

这部分更适合由 ST-Prompt-Template 消费。

### 8.4 FSM / 路由规则

建议用结构化 JSON，而不是散落在 prompt 里的自然语言。

最少包含：

- `current_node_id`
- `allowed_next_nodes`
- `trigger_conditions`
- `hard_block_conditions`
- `state_updates`
- `variable_projection`

这部分更适合由外部脚本或中间件消费，再回写到 ST 变量系统。

### 8.5 MVU / 变量卡

MVU 更适合做：

- 当前大节点
- 当前关系态
- 债务 / 资源 / 声望 / 风险等状态展示
- 世界阶段 UI

不适合做：

- 权威剧情判断
- 唯一状态机规则源

最佳定位是“展示层 + 操作入口层”。

---

## 9. 风险与对应缓解

| 风险 | 严重度 | 说明 | 缓解 |
|---|---|---|---|
| later nodes fact 未完成 | 高 | 节点 3-5 还不能形成全节点评估闭环 | 先以节点 1-2 建立完整范式 |
| style_id / run_id 不唯一 | 高 | compare / regress 会混淆 run | 先冻结 run manifest 与命名规范 |
| judge 过度主观 | 中 | 不同 judge prompt 可能漂移 | 规则版 + judge + 人工抽检三层并行 |
| gold set 过度写成“标准答案” | 中 | 容易压制多样性 | 改做约束集而非唯一文本 |
| worldbook 条目过于抽象 | 中 | 下游检索与触发无效 | 强制做原子化与实体/规则分类 |
| 把 FSM 权威逻辑塞进模板层 | 高 | 很难维护，也难回归 | FSM 规则保持离线结构化，模板层只投影 |
| 第三方脚本依赖失控 | 中 | 远程脚本更新会破坏运行 | 使用自有版本化脚本和固定导入策略 |

---

## 10. 推荐里程碑

## M0：冻结评估元数据契约

目标：

- 固定 `run_manifest.json`
- 固定 `style_id` / `run_id` 命名
- 固定 compare/regress 的输入输出目录

产出：

- 评估目录规范
- 命名规范
- CLI I/O 约定

## M1：节点 1-2 的 Gold set v0

目标：

- 先做 24-36 个 case
- 聚焦节点 1、2
- 覆盖 6 条核心轴线 + 3 个下游面

产出：

- `style_gold_set/v1`
- `gold rubric`

## M2：`judge-style-bible`

目标：

- 对单个 candidate 做 criterion 级评估
- 输出可审阅报告
- 可定位到字段和证据

产出：

- `judge_report.json`
- `judge_rows.jsonl`

## M3：`compare-style-runs` + `regress-style-quality`

目标：

- 允许模型切换、prompt 改版后做自动比较
- 允许建立 baseline 并做退化门禁

产出：

- run diff 报告
- regression gate 报告

## M4：节点 1 的 ST 资产试点

目标：

- 导出第一套节点版世界书
- 导出第一套 prompt preset 片段
- 导出第一版 route rules / FSM projection
- 做一版 MVU 状态板

产出：

- 节点 1 的 ST 可导入资产包

## M5：节点 2 作为第一次“版本升级”

目标：

- 验证从节点 1 升级到节点 2 时
  - 世界书如何扩容
  - 变量如何迁移
  - prompt preset 如何切换
  - FSM 如何更新

如果节点 2 能顺畅升级，说明“大节点持续迭代”这条路线真正成立。

---

## 11. 建议的验收标准

如果要判断这条路线是否真的跑通，建议至少满足以下条件：

### 评估层

- 节点 1 和节点 2 都有可复跑的 Gold set
- `judge-style-bible` 能稳定输出 criterion 级报告
- `compare-style-runs` 能指出 prompt / model 改版的有效差异
- `regress-style-quality` 能作为下游导出前的质量门

### 资产层

- 至少有一个节点能稳定导出世界书、RAG 包、prompt preset 和 route rules
- 这些资产能在 ST 中成功导入并使用

### 运行时层

- MVU / 变量卡能正确投影节点状态
- 路由/FSM 不依赖模型自由发挥，而有结构化约束
- 风格预设能在节点升级时切换，而不是每轮聊天漂移

---

## 12. 最终建议

建议正式采用以下路线：

1. **把项目定义为“基于主节点发布的长期迭代工程”**
2. **立即用节点 1 和节点 2 补齐 C / D / E**
3. **不要等待全书 fact 全完才开始 ST 闭环**
4. **把 ST 定位为消费层，不是权威层**
5. **把世界书、RAG、FSM、Prompt Preset 做成同一节点版本下的并列资产**

一句话概括：

**这个想法不只是可行，而且与当前仓库已经形成的“节点版 Canon / Style Bible / Eval”结构高度一致；真正的关键不在于能不能做，而在于是否先把 Gold set、judge、regress 和资产契约固定下来。**
