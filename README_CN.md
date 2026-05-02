# Novel Pipeline

基于 OpenAI 兼容 API 的中文小说结构化分析流水线，功能涵盖：

- 章节导入与暂存
- 场景拆分
- 事实设定提取（角色、地点、阵营、事件、世界观设定）
- 风格指纹提取（叙事引擎、幽默机制、人物塑造手法）
- Canon（设定集）构建与导出
- Style Bible（二阶段风格总手册）综合
- Style Bible 评估（Eval + Judge）
- 静态 HTML 审阅面板生成

流水线专为中文网络小说设计，将**事实设定提取**与**叙事风格学习**分为两条独立管线。

## 当前主线状态（2026-04-18）

- 当前正式主线是 `novel_pipeline_stable`，正式配置为 `config/formal_cn_gpt54_stable.toml`
- 当前正式续跑入口脚本是 `scripts/launch_fact_style_full_formal_cn_gpt54_stable.ps1`
- 当前节点版语义版本入口脚本是 `scripts/run_story_node_pipeline_formal_cn_gpt54_stable.ps1`
- 当前正式全量输入为：
  - `D:\card\novel_pipeline\data\experimental\chapters_full_0001_0841_ready_20260330`
  - `D:\card\novel_pipeline\data\experimental\scenes_full_0001_0841_ready_20260330`
- 当前正式输出复用既有目录并依赖 `--resume`：
  - `D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable`
  - `D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable`
- 当前 watchdog 会同时监控 `fact` 与 `style`，并在两者都完成后触发 **全量 Canon**
- 当前节点版流水线会只对“已确认且 fact 已完整覆盖”的主节点放行，并输出到 `D:\card\novel_pipeline\data\semantic_versions_formal_cn_gpt54_stable`
- 当前最新主节点清单位于：
  - `D:\card\novel_pipeline\data\experimental\story_nodes_user_confirmed_main_20260331\story_nodes_confirmed.json`
  - `D:\card\novel_pipeline\data\experimental\story_nodes_user_confirmed_main_20260331\story_nodes_summary.md`
- 当前检索路线已经更新为 `Hybrid RAG`：
  - 文风与叙事法则：继续以 `Style Bible v2` 为主，并计划接入 Embedding 增强
  - 世界观设定与实体网络：后续从 `facts/canon` 侧剥离，重构为 `GraphRAG`
- 当前最新 `mini3` 重跑结果表明：
  - `mini profile` 下 `section_completeness` 已通过
  - 当前共同阻塞项主要是 `routing_hints`

> 标记约定：下文凡标注 **[已过时]** 的内容，仅保留作历史参考，不再代表当前正式主线。

VPS 部署与同步边界请优先参考 `VPS_SYNC_GUIDE.md`。

## 当前首轮 Hybrid RAG 施工状态（2026-04-19）

本仓库已经从“方案确定”进入“首轮实施”阶段，当前新增了两条正式落地点：

1. `World Graph -> GraphRAG BYOG` 导出层
   - 命令：`novel-pipeline export-world-graph-graphrag`
2. `Style Bible` 的主语义评估闸门
   - 命令：`novel-pipeline evaluate-style-bible`
3. `Style + World` 检索契约导出层
   - 命令：`novel-pipeline build-hybrid-rag-contract`
4. `HybridRetriever` 离线探针
   - 命令：`novel-pipeline probe-hybrid-retriever`

本轮施工清单和对应调研文档位于：

1. `HYBRID_RAG_IMPLEMENTATION_CHECKLIST_20260419_CN.md`
2. `OPEN_SOURCE_HYBRID_RAG_RESEARCH_REPORT_20260419_CN.md`

## 0. 源数据清洗

在进入 `novel_pipeline` 之前，建议先用根目录的清洗脚本生成干净章节：

```powershell
python D:\card\split_novel.py `
  "D:\card\没钱修什么仙？(1-500章).txt" `
  -o "D:\card\cleaned_chapters"
```

当前源清洗层已经覆盖这些问题：

- 重复章节标题与空白伪章节
- 章首重复标题
- 广告/站点提示/域名推广
- 章末作者活动、月票抽奖、求票说明
- 部分源文本损坏章节的手动修复覆盖

手动修复覆盖文件位于：

- `D:\card\chapter_repairs`

其中：

- `drop_titles.txt` 用于删除已知损坏标题
- 同名章节 `.txt` 文件用于回填缺失正文

## 0.1 全库异常扫描

项目内置了章节异常扫描器，可对 `cleaned_chapters` 做结构体检：

```powershell
novel-pipeline scan-chapter-anomalies `
  --input-dir "D:\card\cleaned_chapters" `
  --output-file "D:\card\novel_pipeline\data\reports\chapter_anomalies.json"
```

当前最新扫描结果：

- 空文件：0
- 仅标题无正文：0
- 重复标题：0
- 重复正文：0
- 残留作者活动正文：0

仍保留 1 个已知编号断档：

- `第71章 上架活动福利不要错过`

这是一个纯活动章，已在清洗时主动剔除，所以 `chapter_0070.txt` 后面直接是 `第72章`，属于**已知例外，不是剧情缺章**。

## 1. 环境搭建

```powershell
cd D:\card\novel_pipeline
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

在 `.env` 中填写以下配置：

- `OPENAI_COMPAT_API_KEY` — API 密钥
- `OPENAI_COMPAT_BASE_URL` — API 端点地址

如果需要在同一个 `.env` 中保留多套兼容网关，可按“注释块 + 重复键”的方式组织：

```env
#Gemini方案网关
OPENAI_COMPAT_API_KEY=...
OPENAI_COMPAT_BASE_URL=...

#gpt方案网关
OPENAI_COMPAT_API_KEY=...
OPENAI_COMPAT_BASE_URL=...
```

随后在对应 TOML 配置里通过：

```toml
[models]
env_profile = "gemini"
```

或：

```toml
[models]
env_profile = "gpt"
```

选择要使用的注释块。

兼容端点示例：

- `http://127.0.0.1:4000/v1`（本地代理）
- `https://your-proxy.example.com/v1`（云端网关）

## 2. 项目结构

```text
novel_pipeline/
  config/                 # 运行配置（TOML 格式）
    project.example.toml  # 默认配置模板
    trial_2_5pro.toml     # 试运行配置（Gemini 2.5 Pro）
  data/                   # 数据目录
    chapters/             # 已清洗的章节文件
    scenes/               # 场景拆分结果（JSON）
    reports/              # 异常扫描与可疑词扫描报告
    extracted/
      facts/              # 事实提取结果（JSON）
      style/              # 风格提取结果（JSON）
    canon/                # Canon 构建输出（JSONL + JSON）
    style_bible/          # 二阶段风格总手册输出
    review/               # 审阅面板（HTML + JSON）
  prompts/                # 提取 Prompt 模板
    fact_extraction.md    # 事实提取 System Prompt
    style_extraction.md   # 风格提取 System Prompt
    style_bible_synthesis.md # Style Bible 综合 Prompt
  scripts/                # 正式运行、监控与辅助脚本
    launch_fact_style_full_formal_cn_gpt54_stable.ps1
    run_fact_formal_cn_gpt54_stable.ps1
    run_style_formal_cn_gpt54_stable.ps1
    run_fact_watchdog_formal_cn_gpt54_stable.ps1
    run_story_node_pipeline_formal_cn_gpt54_stable.ps1
  src/
    novel_pipeline/       # Python 源码
    novel_pipeline_stable/ # 当前正式主线
```

补充文档：

- `ITERATION_LOG_CN.md` — 本项目从源数据清洗到蒸馏管线的迭代记录
- `MODEL_COMPARISON_AND_STYLE_PROMPT_ANALYSIS_CN.md` — `gpt-5.4` 与 Gemini 样本质量对比，以及 `文风蒸馏器.md` 的项目适配分析
- `FULL_PIPELINE_TO_RP_BLUEPRINT_CN.md` — 从当前蒸馏阶段到 RP / Hybrid RAG / 世界书 / 中间件的完整规划
- `HYBRID_RAG_FEASIBILITY_AND_IMPLEMENTATION_ROADMAP_CN.md` — 当前正式采用的 Hybrid RAG 可行性报告与后续技术路线
- `data\experimental\story_nodes_user_confirmed_main_20260331\story_nodes_summary.md` — 当前最新版主节点清单

> **[已过时]** 旧文档曾引用 `CHANGELOG_STABILITY_CN.md`，但当前仓库中不存在该文件。

## 3. Prompt 管理

提取 Prompt 位于以下路径，可直接编辑：

- `prompts/fact_extraction.md` — 控制事实提取的行为（输出哪些实体、事件、关系等）
- `prompts/style_extraction.md` — 控制风格分析的维度（叙事引擎、幽默机制等）
- `prompts/style_bible_synthesis.md` — 二阶段风格总手册 Prompt，用于把 facts/style/canon 汇总成 RP / Style-RAG / 风格路由可用的项目专用风格配置

配置项 `paths.prompt_dir` 决定使用哪个 Prompt 目录。

## 4. API 调用模式

项目通过 OpenAI Python SDK 调用兼容端点。结构化输出模式由配置控制：

| 模式 | 适用场景 |
|---|---|
| `response_format = "json_schema"` | 网关支持严格 JSON Schema 约束时使用 |
| `response_format = "json_object"` | 网关仅支持通用 JSON 模式时使用 |

请求频率也可在配置中调整：

```toml
max_requests_per_minute = 2.0
```

> **[已过时]** 旧主线曾以 `2 RPM` 作为保守默认值。

当前正式主线 `config/formal_cn_gpt54_stable.toml` 已将：

```toml
max_requests_per_minute = 0.0
```

同时正式运行脚本还会额外设置：

- `NOVEL_PIPELINE_MAX_RPM=0`

也就是说，当前正式主线不再依赖固定 RPM 限流，而是依赖多网关切换、超时/退避、失败回退与 `--resume` 持续推进。

## 5. 试运行配置

首次试运行建议使用：

- `config/trial_2_5pro.toml`

该配置使用 `gemini-2.5-pro` 模型，并采用较小的风格窗口以加快验证速度。

当前正式全量运行的推荐配置：

> **[已过时]** 本文早前将 `config/formal_cn_2_5pro.toml` 视为正式全量推荐配置；这已经不再是当前主线。

当前正式主线推荐配置：

- `config/formal_cn_gpt54_stable.toml`

当前新增了一条经过验证的稳定化 `gpt-5.4` 路线：

- `config/formal_cn_gpt54_stable.toml`

这条路线通过独立的 `novel_pipeline_stable` 客户端运行，包含：

- 流式读取
- 更保守的重试与退避
- JSON 修复
- 请求度量落盘
- `facts` 两段式抽取（长场景或失败回退时自动启用）
- 多网关会话级失效切换
- 正式脚本中的双线程分流：
  - `fact` 默认优先 `gateway index = 3`
  - `style` 默认优先 `gateway index = 1`

推荐使用方式：

- **当前正式主线 / 全量续跑**：`config/formal_cn_gpt54_stable.toml`
- **历史对照、旧实验或回退参考**：`config/formal_cn_2_5pro.toml`、`config/formal_cn_gpt54.toml`

`novel_pipeline` 中基于 Gemini 的路径仍保留作历史老项目与对照实验，但不是当前正式主线。

## 6. 推荐工作流

### 第一步：导入章节文件

```powershell
novel-pipeline stage-chapters `
  --input-dir "D:\card\cleaned_chapters" `
  --output-dir "D:\card\novel_pipeline\data\chapters" `
  --clear
```

### 第二步：拆分场景

```powershell
novel-pipeline split-scenes `
  --config "D:\card\novel_pipeline\config\trial_2_5pro.toml" `
  --input-dir "D:\card\novel_pipeline\data\chapters" `
  --output-dir "D:\card\novel_pipeline\data\scenes"
```

### 第三步：小批量试提取

建议首轮提取范围：
- 事实：前 30 个场景
- 风格：前 5 个窗口

```powershell
novel-pipeline extract-facts `
  --config "D:\card\novel_pipeline\config\trial_2_5pro.toml" `
  --input-dir "D:\card\novel_pipeline\data\scenes" `
  --output-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --limit 30 `
  --resume
```

```powershell
novel-pipeline extract-style `
  --config "D:\card\novel_pipeline\config\trial_2_5pro.toml" `
  --input-dir "D:\card\novel_pipeline\data\chapters" `
  --output-dir "D:\card\novel_pipeline\data\extracted\style" `
  --limit 5 `
  --resume
```

### 第三步补充：稳定版 GPT-5.4 试跑

如果要验证稳定版 `gpt-5.4` 兼容链路，可直接运行：

```powershell
powershell -File "D:\card\novel_pipeline\scripts\run_trial_gpt54_stable_phase1.ps1"
```

这会依次执行：

- `probe-gateway`
- 小批量 `extract-facts`

实验输出位于：

- `D:\card\novel_pipeline\data\experimental\probe_gpt54_stable_phase1`
- `D:\card\novel_pipeline\data\experimental\facts_gpt54_stable_phase1*`

### 第三步补充：稳定版 GPT-5.4 正式蒸馏

当前正式续跑入口：

```powershell
powershell -File "D:\card\novel_pipeline\scripts\launch_fact_style_full_formal_cn_gpt54_stable.ps1"
```

它会同时完成以下事情：

- 用全量 ready 目录拉起 `fact` 与 `style`
- 两个线程都复用正式输出目录并启用 `--resume`
- `fact` / `style` 分别使用不同的优先网关
- 自动拉起 watchdog
- 自动写入 `formal_cn_gpt54_stable_live_processes.json`

当前正式全量输入目录：

- `D:\card\novel_pipeline\data\experimental\scenes_full_0001_0841_ready_20260330`
- `D:\card\novel_pipeline\data\experimental\chapters_full_0001_0841_ready_20260330`

对应后台脚本：

```powershell
powershell -File "D:\card\novel_pipeline\scripts\run_fact_formal_cn_gpt54_stable.ps1"
powershell -File "D:\card\novel_pipeline\scripts\run_style_formal_cn_gpt54_stable.ps1"
```

对应输出目录：

- `D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable`
- `D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable`
- `D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable`
- `D:\card\novel_pipeline\data\review_formal_cn_gpt54_stable`

运行记录：

- `D:\card\novel_pipeline\data\reports\formal_cn_gpt54_stable_live_processes.json`
- `D:\card\novel_pipeline\data\reports\fact_watchdog_formal_cn_gpt54_stable_status.json`
- `D:\card\novel_pipeline\data\reports\fact_watchdog_formal_cn_gpt54_stable_events.jsonl`
- `D:\card\novel_pipeline\data\logs\facts_full_resume_*.log`
- `D:\card\novel_pipeline\data\logs\style_full_resume_*.log`
- `D:\card\novel_pipeline\data\logs\fact_watchdog_*.log`

> **[已过时]** `scripts\launch_formal_cn_gpt54_stable.ps1` 与 `scripts\run_formal_cn_gpt54_stable.ps1` 仍保留作历史串行脚本参考，但不再是当前正式续跑的推荐入口。

### 第四步：构建 Canon（设定集）

```powershell
novel-pipeline build-canon `
  --facts-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --style-dir "D:\card\novel_pipeline\data\extracted\style" `
  --output-dir "D:\card\novel_pipeline\data\canon"
```

如果你准备把“换地图 / 大节点”做成语义版本，建议先生成候选节点，不要直接运行节点版 Canon：

```powershell
novel-pipeline detect-story-nodes `
  --chapters-dir "D:\card\novel_pipeline\data\chapters" `
  --facts-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --output-dir "D:\card\novel_pipeline\data\story_nodes_candidates"
```

这一步会输出：

- `story_node_candidates.json`：机器可读的候选节点清单
- `story_node_candidates.md`：便于人工审阅的候选理由与证据
- `story_nodes_confirmed.json`：待你确认的清单模板

当前最新版主节点清单已经单独固化到：

- `D:\card\novel_pipeline\data\experimental\story_nodes_user_confirmed_main_20260331\story_nodes_confirmed.json`

当前已确认的主节点为：

| 主节点 | 范围 | 说明 |
|---|---|---|
| 1 | `0001-0270` | 一层阶段 |
| 2 | `0271-0510` | 二层土木阶段 |
| 3 | `0511-0654` | 二层炼器与十大联赛阶段 |
| 4 | `0655-0738` | 后十大联赛博弈阶段 |
| 5 | `0739-0841` | 宗门阶段（`0739-0759` 为过渡，`0760+` 为正式进入宗门） |

> **[已过时]** `D:\card\novel_pipeline\data\experimental\story_nodes_gpt54_stable_20260330_v5\story_nodes_confirmed.json` 只是一份旧候选模板，覆盖范围也只到 `0489`，不再代表当前主节点边界。

只有当你把目标节点明确标记为 `selected=true` 且 `status=confirmed` 之后，节点版 Canon 构建才会放行：

```powershell
novel-pipeline build-canon `
  --facts-dir "D:\card\novel_pipeline\data\extracted\facts_formal_cn_gpt54_stable" `
  --style-dir "D:\card\novel_pipeline\data\extracted\style_formal_cn_gpt54_stable" `
  --output-dir "D:\card\novel_pipeline\data\canon_node_scope" `
  --story-nodes "D:\card\novel_pipeline\data\experimental\story_nodes_user_confirmed_main_20260331\story_nodes_confirmed.json" `
  --node-id "main_03_kunxu_l2_artificer_league_ch0511_0654"
```

> 当前 watchdog 自动触发的仍然是**全量 Canon**，不会自动把这份主节点 manifest 注入 `build-canon`。节点版 Canon 目前仍需手动指定 `--story-nodes` 与 `--node-id`。

如果你已经确认主节点清单，并希望直接按节点串起来跑 `build-canon -> build-style-bible -> evaluate-style-bible`，可以直接运行：

```powershell
powershell -File "D:\card\novel_pipeline\scripts\run_story_node_pipeline_formal_cn_gpt54_stable.ps1"
```

这条脚本当前会：

- 只读取 `story_nodes_confirmed.json` 中 `selected=true` 且 `status=confirmed` 的节点
- 只放行 fact 已完整覆盖的节点
- 先构建**节点版 Canon**
- 再基于该节点版 Canon 自动收缩 `build-style-bible` 的事实/风格输入范围
- 随后自动执行 `evaluate-style-bible`

如果只想跑单个节点，可先设置：

```powershell
$env:NOVEL_PIPELINE_NODE_ID = "main_01_kunxu_l1_ch0001_0270"
powershell -File "D:\card\novel_pipeline\scripts\run_story_node_pipeline_formal_cn_gpt54_stable.ps1"
```

### 第五步：生成审阅面板

```powershell
novel-pipeline build-review-panel `
  --facts-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --style-dir "D:\card\novel_pipeline\data\extracted\style" `
  --output-dir "D:\card\novel_pipeline\data\review"
```

然后在浏览器中打开：

- `D:\card\novel_pipeline\data\review\review_panel.html`

### 第六步：合成 Style Bible

`Style Bible` 的定位不是 style window 集合，而是基于 `facts + style + canon` 的二阶段综合结果。

截至当前版本，`Style Bible` 的正式职责是：

1. 风格与叙事规则母体
2. Style-RAG 上游资产层
3. 风格侧 routing hints 生成层

它不再被视为未来世界设定主检索层。

推荐直接使用当前唯一公开 CLI 入口 `novel-pipeline`：

```powershell
novel-pipeline build-style-bible `
  --config "D:\card\novel_pipeline\config\formal_cn_gpt54_stable.toml" `
  --facts-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --style-dir "D:\card\novel_pipeline\data\extracted\style" `
  --canon-dir "D:\card\novel_pipeline\data\canon" `
  --output-dir "D:\card\novel_pipeline\data\style_bible" `
  --resume
```

如果只是先做预演，可以调小采样参数：

```powershell
novel-pipeline build-style-bible `
  --config "D:\card\novel_pipeline\config\formal_cn_gpt54_stable.toml" `
  --facts-dir "D:\card\novel_pipeline\data\extracted\facts" `
  --style-dir "D:\card\novel_pipeline\data\extracted\style" `
  --canon-dir "D:\card\novel_pipeline\data\canon" `
  --output-dir "D:\card\novel_pipeline\data\style_bible_preview" `
  --max-style-windows 12 `
  --max-scene-samples 12 `
  --max-plot-nodes 12 `
  --max-chapter-summaries 12 `
  --resume
```

如果 `--canon-dir` 指向的是**节点版 Canon**，并且其中带有 `story_node_scope.json`，当前版本的 `build-style-bible` 会自动：

- 继承该节点范围
- 只读取该节点范围内的 facts / style rows
- 在输出目录中同步写入 `story_node_scope.json`

也就是说，节点版 `build-style-bible` 现在已经可以通过节点版 Canon 间接放行，而不需要你手动再拆一份节点版 facts/style 目录。

### 第七步：评估 Style Bible

`evaluate-style-bible` 当前是离线质量闸门，主要检查 schema 完整性、关键轴线覆盖、`supporting_evidence` 与 `source_ref` 质量、规则动作化程度、`routing_hints` 可执行性、`worldbook_binding` 可导出性，以及泛化赞美语句告警。

```powershell
novel-pipeline evaluate-style-bible `
  --input-dir "D:\card\novel_pipeline\data\style_bible" `
  --output-dir "D:\card\novel_pipeline\data\style_bible_eval" `
  --resume
```

### 第七点五步：运行 Gold Set 回归 Judge

`judge-style-bible` 只保留为显式 gold-set 回归工具，不再承担主生产闸门职责。

```powershell
novel-pipeline judge-style-bible `
  --input-dir "D:\card\novel_pipeline\data\style_bible" `
  --output-dir "D:\card\novel_pipeline\data\style_bible_judge" `
  --gold-set-index "D:\card\novel_pipeline\data\eval\style_gold_set\v2\index.json" `
  --judge-config "D:\card\novel_pipeline\config\style_bible_judge_rules.toml" `
  --resume
```

### 第七点六步：导出 GraphRAG BYOG 资产

当 `build-world-graph` 已经生成离线世界图后，可以继续导出首轮 GraphRAG BYOG 对接资产：

```powershell
novel-pipeline export-world-graph-graphrag `
  --world-graph-dir "D:\card\novel_pipeline\data\world_graph" `
  --output-dir "D:\card\novel_pipeline\data\world_graph_graphrag"
```

如果你希望把 `Style Bible` 和 `World Graph` 的职责边界固化成后续运行时可消费的契约，可继续生成离线 contract：

```powershell
novel-pipeline build-hybrid-rag-contract `
  --style-bible-dir "D:\card\novel_pipeline\data\style_bible" `
  --world-graph-dir "D:\card\novel_pipeline\data\world_graph" `
  --output-dir "D:\card\novel_pipeline\data\hybrid_rag_contract"
```

如果你希望在真正接入运行时中间件前，先用一个 query 验证 `Style Lane + World Lane` 的混合召回是否合理，可继续跑离线 probe：

```powershell
novel-pipeline probe-hybrid-retriever `
  --style-bible-dir "D:\card\novel_pipeline\data\style_bible" `
  --world-graph-dir "D:\card\novel_pipeline\data\world_graph" `
  --output-dir "D:\card\novel_pipeline\data\hybrid_probe" `
  --query "审批通知出现时要怎么写外门资格规则"
```

## 7. 输出文件说明

### Canon 构建输出

| 文件 | 内容 |
|---|---|
| `entities.jsonl` | 去重后的全部实体（角色、地点、阵营、物品等） |
| `facts.jsonl` | 原子化的世界设定事实 |
| `events.jsonl` | 具体事件及参与者 |
| `chapter_summaries.jsonl` | 按章节聚合的场景摘要 |
| `style_bible.json` | 风格窗口的完整分析结果（中间产物，不是最终总手册） |
| `style_index.json` | 风格指纹频次统计 |
| `plot_node_index.json` | 基于 facts/style 汇总出的草稿剧情节点索引 |
| `canon_index.json` | 构建概览（各类数据的计数） |
| `story_node_scope.json` | 仅在节点版 Canon 中出现，记录已确认节点的范围、标签与来源 manifest |

### 故事节点候选输出

| 文件 | 内容 |
|---|---|
| `story_node_candidates.json` | 候选节点列表，含建议范围、标签、置信度、理由与证据 |
| `story_node_candidates.md` | 人工审阅版报告，便于快速确认哪些候选值得保留 |
| `story_nodes_confirmed.json` | 人工确认模板；只有节点被标记为 `selected=true` 且 `status=confirmed` 后，节点版构建才会放行 |

补充说明：

- 当前主线另有一份**用户确认版主节点清单**：
  - `D:\card\novel_pipeline\data\experimental\story_nodes_user_confirmed_main_20260331\story_nodes_confirmed.json`
- 旧候选模板：
  - `D:\card\novel_pipeline\data\experimental\story_nodes_gpt54_stable_20260330_v5\story_nodes_confirmed.json`
  - **[已过时]** 不再代表当前正式主节点边界

### Style Bible 输出

| 文件 | 内容 |
|---|---|
| `style_bible_source_bundle.json` | 喂给综合 prompt 的压缩输入包，包含 style/facts/canon 的代表性证据 |
| `style_bible_final.json` | 二阶段综合后的正式风格总手册，也是后续 Style-RAG 的母体资产 |
| `story_node_scope.json` | 仅在节点版 Style Bible 中出现，记录该手册继承的主节点范围 |
| `manifest.json` | 本次构建的模型、采样和请求度量记录 |
| `failures.json` | 构建失败记录 |

### Style Bible 评估输出

| 文件 | 内容 |
|---|---|
| `style_eval_report.json` | 离线规则评估结果，含总分、分项检查和建议 |
| `style_eval_report.md` | 便于人工查看的 Markdown 评估报告 |
| `manifest.json` | 本次评估的状态、规则配置和分数记录 |
| `failures.json` | 评估失败记录 |

### 审阅面板输出

| 文件 | 内容 |
|---|---|
| `review_data.json` | 合并后的审阅数据 |
| `review_panel.html` | 自包含的静态审阅页面（离线可用） |

## 8. 审阅要点

使用审阅面板重点检查：

- 实体提取是否稳定（同一角色是否被统一识别）
- 事实是否包含可溯源的证据文本
- 事件描述是否过于模糊或范围过大
- 风格指纹是否真正捕捉到了小说独特的叙事手法
- 模型是否因样本量不足而过度泛化

## 9. 补充说明

- **[已过时]** 旧文档曾将 `gemini-2.5-pro` 视为推荐默认模型；当前正式主线已切换到稳定版 `gpt-5.4`。
- `novel_pipeline` 中的 Gemini 路径现阶段主要用于历史对照、旧实验和回退参考。
- 当前若更在意正式续跑稳定性与结果一致性，应优先使用稳定版 `gpt-5.4` 路线。
- 使用 `--limit` 和 `--start-at` 可只处理部分数据。
- 若遇到模型拒绝或网络错误，加 `--resume` 重新运行即可跳过**同一输出目录中已完成的文件**。
- 若你先修复了 `D:\card\cleaned_chapters`，记得重新同步 `data/chapters` 并重建 `data/scenes`。

## 10. 实时监控（Web）

现在流水线会在运行输出目录内自动写入以下监控文件：
- `run_status.json`：当前阶段、总数、已处理数、成功/失败/跳过计数、最近一条状态
- `run_log.jsonl`：逐条运行日志，可用于实时查看最近处理了哪些 chapter / scene / window
- `failures.json`：facts / style 提取阶段的失败样本清单
- `request_metrics.jsonl`：稳定版请求度量、网关切换与耗时记录

当前正式脚本还会额外写入：

- `D:\card\novel_pipeline\data\reports\formal_cn_gpt54_stable_live_processes.json`
- `D:\card\novel_pipeline\data\reports\fact_watchdog_formal_cn_gpt54_stable_status.json`
- `D:\card\novel_pipeline\data\reports\fact_watchdog_formal_cn_gpt54_stable_events.jsonl`

启动本地监控页：

```powershell
novel-pipeline serve-monitor `
  --data-root "D:\card\novel_pipeline\data" `
  --host 127.0.0.1 `
  --port 8765
```

然后在浏览器中打开：
- `http://127.0.0.1:8765`

如果你希望手机或同局域网其他设备访问，可以改成：

```powershell
novel-pipeline serve-monitor `
  --data-root "D:\card\novel_pipeline\data" `
  --host 0.0.0.0 `
  --port 8765
```

监控页会自动扫描 `data/` 下所有包含 `run_status.json` 的输出目录，并展示：
- 当前阶段（split-scenes / extract-facts / extract-style / build-canon / build-style-bible / evaluate-style-bible / build-review-panel）
- 实时进度条与 processed / success / failed / skipped 计数
- 最近日志
- 失败预览

watchdog 额外负责：

- 监控 `fact` / `style` 两个正式线程是否仍在运行
- 检测 20 分钟无进展、失败数增长、进程退出
- 在 `fact` 与 `style` 都完成后自动触发 **全量 Canon**

> 注意：当前 watchdog 触发的 Canon 仍然是不带 `--story-nodes` / `--node-id` 的全量构建；节点版 Canon 尚未自动接入。

补充说明：

- 节点版 `build-canon -> build-style-bible -> evaluate-style-bible` 已经可以通过 `scripts/run_story_node_pipeline_formal_cn_gpt54_stable.ps1` 手动拉起
- 监控页会自动扫到 `data/semantic_versions_formal_cn_gpt54_stable` 下的节点版运行状态
- RP 资产 / 世界书 / 角色卡导出仍未在 stable CLI 中正式实现

不需要额外参数，只要使用新版 CLI 运行相关命令，就会自动生成这些监控文件。
## 11. Phase 2 架构状态（2026-04-22）

当前 `novel_pipeline_stable` 已完成 Style Bible Phase 2 的主干重构，正式术语与实现对齐如下：

- `typed rule family`：`models.py` 现在只保留 `StyleBibleRuleBase`、`NarrativeRuleItem`、`WorldbookFactItem`、`RoutingHintItem`、`NegativeRuleItem`、`ScalarRuleItem`
- `Schema as Code`：`style_bible_prompt_assembler.py` 直接基于 Pydantic `model_json_schema()` 生成响应合同
- `prompt 状态机分层`：`local reduce`、`repair-only`、`densify` 的合同与说明已物理分层
- `中文语义锚点`：`style_bible_section_targets.toml` / `style_bible_section_targets.py` 以 `cue + canonical_description` 作为主语义锚点
- `去 keyword 化`：评估与 reducer 主路径不再依赖 keyword 列表或 shadow sidecar，语义评分直接接管主闸门
- `物理拆分`：旧 `style_bible_reducer.py` / `client.py` / `openai_client.py` 已拆为 `style_bible_reduction/` 与 `api_clients/`
- `评估主闸门`：`evaluate-style-bible` 直接输出语义评分；`judge-style-bible` 仅保留作 gold-set 回归

### Style Bible 输出补充

Phase 2 完成后，Style Bible 正式链除 `style_bible_final.json` 外，还会稳定产出：

- `style_bible_reasoning.json`
- `style_bible_reduce_trace.json`
- `_section_densify\...\section_densify_trace.json`
- `_section_densify\...\semantic_dedupe_drop_pairs.json`

### Eval / Judge 输出补充

- `style_eval_report.json` 现在以语义评分结果作为主报告主体
- `judge_report.json` 保留 gold-set 回归输出
- `judge_rows.jsonl` 仍保持原有回归主合同

### Full Live 主验收链

Phase 2 的正式验收链从 `extract-style` 开始，默认复用既有正式 facts 输出：

1. `extract-style`
2. `build-canon`
3. `build-style-bible`
4. `evaluate-style-bible`
5. `judge-style-bible`

其中 `build-world-graph`、`build-hybrid-rag-contract`、`probe-hybrid-retriever` 不属于这次主 gating 链。
