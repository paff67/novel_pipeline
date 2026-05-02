# Style Bible V2 编码清理与全仓扫描报告

## 1. 本次处理范围

- 仓库主目录: `D:\card\novel_pipeline`
- 重点链路: `src/novel_pipeline_stable`
- 重点产物: 中文 Markdown 报告、阶段报告、评测报告、Judge 报告、Regression 报告
- 外部测试报告核验目录: `C:\sbtests`

## 2. 已完成的编码治理改动

### 2.1 统一读取兼容层

文件: `D:\card\novel_pipeline\src\novel_pipeline_stable\io_utils.py`

已完成:

- `read_text()` 改为 `encoding="utf-8-sig"`
- `read_json()` 改为 `encoding="utf-8-sig"`
- `read_jsonl()` 改为 `encoding="utf-8-sig"`
- 新增 `write_markdown()`，专门以 `utf-8-sig` 原子写出 Markdown

这一步的意义是:

- 兼容带 BOM 和不带 BOM 的 UTF-8 文件
- 避免 Windows 下 Markdown 报告再次出现显示层假乱码

### 2.2 Markdown 报告写出链路收口

以下文件的 Markdown 输出已统一切换到 `write_markdown()`:

- `D:\card\novel_pipeline\src\novel_pipeline_stable\story_nodes.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_compare.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_evaluator.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_judge.py`
- `D:\card\novel_pipeline\src\novel_pipeline_stable\style_bible_regression.py`

这意味着主链路里最关键的中文报告产物，已经有了统一编码出口。

### 2.3 已修复并核验的中文报告

以下报告已重写或补齐 BOM，并确认可以用 `utf-8-sig` 稳定读出:

- `D:\card\novel_pipeline\STYLE_BIBLE_V2_PROJECT_REPORT_CN.md`
- `C:\sbtests\20260410T223536_planner_matrix_debug_cn_gpt54_stable\planner_matrix_debug_report_cn.md`
- `C:\sbtests\20260410T203235_controlled_live_batch_cache_probe_formal_cn_gpt54_stable\live_subset_report_cn.md`
- `C:\sbtests\20260410T221049_warmup_parallel_repair_usage_live_probe_formal_cn_gpt54_stable\live_patch_validation_report_cn.md`

## 3. 核验结果

### 3.1 报告可读性核验

已确认上述 4 份关键中文报告:

- 文件头包含 UTF-8 BOM
- 用 `Path.read_text(encoding="utf-8-sig")` 可正常读出中文
- 用 `Get-Content -Encoding utf8` 可正常预览中文

### 3.2 源码层高置信扫描结果

为了避免“扫描脚本自己把中文打坏”的误报，本次扫描使用了 ASCII 转义形式的模式串，只检测高置信损坏特征:

- `U+FFFD` 替代字符
- 典型 UTF-8 被错按 Latin-1 / CP1252 解读后形成的 mojibake 串
- BOM 被错误落入正文的串形

扫描结论:

- `D:\card\novel_pipeline` 全仓高置信编码污染命中数: `0`
- `src/`、`config/`、`prompts/`、根目录 Markdown / TOML 的高置信命中数: `0`

这说明:

- `style_bible_prompt_assembler.py` 没有发现真实源码级乱码
- `style_bible_contracts.py` 没有发现真实源码级乱码
- `style_bible_router.py` 没有发现真实源码级乱码
- 当前仓库主链路源码层，未再发现新的真实编码污染

### 3.3 为什么之前会看起来“像乱码”

根因主要有两类:

1. Windows / PowerShell 默认显示编码不稳定，UTF-8 中文可能被显示成乱码
2. 在 PowerShell here-string 中直接夹带中文，再 pipe 给 Python 时，中文本身可能先被打成 `?`

所以此前不少“源码乱码”现象，实际是显示层或命令注入层问题，而不是文件内容真的坏了。

## 4. 扫描到的剩余异常

### 4.1 这些异常不属于“源码编码损坏”

虽然高置信编码污染为 0，但仓库的数据目录里仍存在较多“问号型文本异常”。它们更接近:

- 原始语料抓取残留
- 上游文本清洗不完整
- 剧情内本来就存在的占位符

而不是 UTF-8 文件本身被写坏。

### 4.2 当前识别出的两类主要数据异常

第一类: `本书首发...????` 一类源站残留或脏 URL

- 典型位置: `data/chapters/` 与 `data/scenes/`
- 代表文件:
  - `D:\card\novel_pipeline\data\chapters\chapter_0040.txt`
  - `D:\card\novel_pipeline\data\chapters\chapter_0182.txt`
  - `D:\card\novel_pipeline\data\scenes\scene_0040_001.json`
  - `D:\card\novel_pipeline\data\scenes\scene_0182_001.json`

第二类: `“???”` 一类剧情内占位符或抽取后遗留标记

- 典型位置: `data/canon_formal_cn_gpt54_stable/`、`data/review_formal_cn_gpt54_stable/`
- 代表文件:
  - `D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable\chapter_summaries.jsonl`
  - `D:\card\novel_pipeline\data\canon_formal_cn_gpt54_stable\entities.jsonl`
  - `D:\card\novel_pipeline\data\review_formal_cn_gpt54_stable\review_data.json`
  - `D:\card\novel_pipeline\data\review_formal_cn_gpt54_stable\review_panel.html`

### 4.3 目录级分布

按 `???` 形态扫描，当前命中的文件主要集中在:

- `data/experimental`: `97` 个文件
- `data/chapters`: `10` 个文件
- `data/scenes`: `10` 个文件
- `data/reports`: `8` 个文件
- `data/canon_formal_cn_gpt54_stable`: `5` 个文件
- `data/semantic_versions_formal_cn_gpt54_stable`: `5` 个文件
- `data/extracted`: `3` 个文件
- `data/review_formal_cn_gpt54_stable`: `2` 个文件
- `data/smoke`: `2` 个文件

这里要特别注意:

- `data/reports`、`data/smoke`、`data/canon_*` 中出现的 `“???”`，很多是从上游剧情内容复制过来的衍生污染
- 不能把这类内容和“代码文件编码坏掉”混为一谈

## 5. 结论

本轮编码治理已经把真正影响主链路可维护性的部分收住了:

- 源码层未发现新的高置信编码污染
- 报告写出链路已统一补上 `utf-8-sig` / BOM 兼容
- 之前最明显的中文 Markdown 乱码报告已经修复

当前剩下的问题，核心不再是“代码文件编码坏了”，而是“数据层文本本身有脏内容”。

## 6. 后续建议

1. 把“编码治理”和“语料清洗”分成两个独立问题管理，不要再混扫。
2. 后续所有中文 Markdown 报告继续统一走 `write_markdown()`。
3. 后续扫描脚本只用 ASCII 转义模式，不要在 PowerShell here-string 里直接写中文模式串。
4. 如果要继续清理数据层，建议新开一个专项，专门清洗:
   - `本书首发...????`
   - 源站残留 URL / 水印
   - 可明确判定为非剧情内容的问号串
5. 对 `“???”` 这一类占位符，先区分“剧情真实设定”与“抽取污染”，再决定是否清洗。
