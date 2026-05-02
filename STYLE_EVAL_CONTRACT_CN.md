# Style Eval Contract v1

本文件用于冻结 M0 阶段的评估元数据契约，目标是为后续三项能力提供稳定输入：

- `judge-style-bible`
- `compare-style-runs`
- `regress-style-quality`

## 1. 当前约定

从本版本开始，节点版 `build-style-bible` 会在输出目录写入：

- `style_bible_final.json`
- `style_bible_source_bundle.json`
- `run_manifest.json`

节点版 `evaluate-style-bible` 会在输出目录写入：

- `style_eval_report.json`
- `style_eval_report.md`
- `evaluation_manifest.json`

## 2. `style_id` 约定

`style_id` 不再依赖模型自由生成，而由代码统一生成。

推荐格式：

```text
style_bible_<node_or_scope>_v1
```

示例：

```text
style_bible_main_01_kunxu_l1_ch0001_0270_v1
style_bible_main_02_kunxu_l2_civil_ch0271_0510_v1
```

这样做的目的：

- 保证跨节点唯一
- 保证 compare / regress 能稳定索引
- 避免模型把 `style_id` 写成泛化值

## 3. `run_id` 约定

`run_id` 表示一次具体生成运行，和 `style_id` 的关系是：

- `style_id`：资产身份
- `run_id`：某次候选构建

推荐格式：

```text
<style_id>__<model_slug>__<source_bundle_hash8>__<timestamp>
```

## 4. `run_manifest.json` 核心字段

最重要的字段如下：

- `manifest_version`
- `stage`
- `run_id`
- `style_id`
- `style_bible_schema_version`
- `scope`
- `scope_type`
- `node_id`
- `start_chapter`
- `end_chapter`
- `model_name`
- `prompt_name`
- `prompt_path`
- `prompt_hash`
- `config_path`
- `config_hash`
- `built_at`
- `git_commit`
- `input_dirs`
- `output_files`
- `hashes`
- `corpus_stats`
- `sampling`
- `request_metrics`
- `usage_metadata`
- `story_node_scope`

## 5. `evaluation_manifest.json` 核心字段

- `manifest_version`
- `stage`
- `evaluation_id`
- `run_id`
- `style_id`
- `style_bible_schema_version`
- `scope`
- `scope_type`
- `node_id`
- `model_name`
- `prompt_hash`
- `config_hash`
- `rules_path`
- `rules_hash`
- `evaluated_at`
- `status`
- `overall_score`
- `max_score`
- `pass_score`
- `warn_score`
- `quality_gate_passed`
- `check_counts`
- `report_hash`
- `source_run_manifest_file`

## 6. 为什么要先冻结这些字段

因为后续 compare / regress 至少要稳定回答四个问题：

1. 这两个候选是不是同一节点、同一资产类型？
2. 它们分别用的是什么模型、什么 prompt、什么 config？
3. 它们的输入 source bundle 是否一致？
4. 当前评估分数对应的 rules 版本是什么？

如果这些元数据不稳定，后面的 judge、pairwise compare、历史回归都会失真。

## 7. M0 之后的依赖关系

- `judge-style-bible` 读取 `run_manifest.json`
- `compare-style-runs` 比较两个 `run_manifest.json + judge/eval` 组合
- `regress-style-quality` 用 `run_id` 与 baseline registry 建立映射

因此，M0 不是附属工作，而是后续 C / D / E 的前置基础。
