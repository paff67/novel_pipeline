# Style Gold Set Guide v1

本指南定义当前项目中的 gold set 应该怎么做。

## 1. 基本原则

gold set 不应做成“唯一标准答案文本”，而应做成**约束集**。

也就是说，我们不要求候选 Style Bible 必须长得和参考文本一模一样，而要求它满足：

- 必须命中的轴线
- 必须出现的机制
- 不允许出现的误判
- 下游必须可用的导出面
- 最低证据要求

## 2. 当前最推荐的范围

先只做：

- 节点 1 `main_01_kunxu_l1_ch0001_0270`
- 节点 2 `main_02_kunxu_l2_civil_ch0271_0510`

原因：

- facts 已完整
- 节点版 Style Bible 已存在
- evaluator 已有真实反馈
- 适合先做 v0 基线

## 3. case 应该如何选

不要平均抽样，而是优先选“最能暴露风格机制”的 case。

优先覆盖六条主轴：

1. 资源 / 债务压力
2. 教育门槛 / 阶层筛选
3. 身体异化 / 改造成本
4. 制度荒诞 / 流程口吻
5. 黑色幽默 / 冷面吐槽
6. 家庭劳动 / 资源交换

同时要求每个节点至少覆盖三个下游面：

- `rag_worthy`
- `worldbook_worthy`
- `routing_hints`

## 4. 推荐 case 粒度

建议优先使用两种粒度：

1. `scene_bundle`
   - 2 到 5 个强相关 scene
   - 适合标注“机制是如何形成的”

2. `style_window`
   - 1 个 style window + 对应 scene refs
   - 适合标注“应如何被 Style Bible 综合”

不建议当前阶段直接做：

- 全节点唯一大 case
- 逐章节 case

## 5. 每个 case 至少要写什么

每个 case 至少要明确：

- `source_refs`
- `required_axes`
- `required_mechanisms`
- `forbidden_patterns`
- `required_downstream_surfaces`
- `evidence_expectations`

其中最关键的是两部分：

### 5.1 `required_mechanisms`

这里写的不是主题词，而是机制。

好的写法：

- “资源危机不是背景板，而是推动角色接活、借贷、训练和参赛的直接驱动”
- “制度荒诞通过通知、流程、标准口吻与极端身体成本的错位形成黑色幽默”

不好的写法：

- “有社会批判”
- “很荒诞”
- “很黑色幽默”

### 5.2 `forbidden_patterns`

用于防止 judge 和 compare 把明显错误也算成“差不多对”。

典型 forbidden：

- 把主题词当机制词
- 只写抽象赞美
- 缺失路由触发条件
- worldbook 条目只有抽象判断，没有实体/规则/机构
- 证据不落到具体 `scene:` 或 `window_id`

这里的 style window 引用请直接写原始 `window_id`，例如 `0001_0002`，不要写文件名 `style_window_0001_0002`。

### 5.3 不要把 `must_include_any` 写成“答案串”

`must_include_any` 的作用是给 judge 一个**机制锚点**，不是逼候选逐字背答案。

建议：

- 单个 mechanism 的 `must_include_any` 优先控制在 2 到 4 个锚点词，最多 5 个。
- 优先写“制度动作 / 资源类型 / 角色反应 / 关系方式”这类能稳定复现的锚点。
- scene_bundle 需要 scene 级精度时，可以用 `required_source_ref_prefixes = ["scene:"]` 明确要求 judge 看 scene 证据。

尽量避免直接写成：

- 数字阶梯，例如 `10% / 30% / 60% / 90%`
- 长引号台词或单场景问句
- 一整串赔偿清单原文
- 只有该 scene 才出现一次的吐槽句

只有当数字、机构名、术语本身就是稳定世界规则的一部分时，才保留为锚点。

好的写法：

- `现金赔偿`
- `租用权`
- `资源包`
- `围观定价`
- `归属谈判`

不好的写法：

- `50万`
- `100万`
- `150万`
- `不会被哪家公司对付吧`
- `根据价格决定回复`

## 6. 当前最合理的工作量

v0 不要贪大。

建议：

- 节点 1：12 到 16 个 case
- 节点 2：12 到 16 个 case

总量控制在 24 到 32 个 case 之间。

这样足够支撑：

- judge rubric 校准
- compare baseline
- regress gate 试运行

## 7. 标注顺序建议

建议按这个顺序做：

1. 先定 `source_refs`
2. 再写 `required_axes`
3. 再写 `required_mechanisms`
4. 再写 `required_downstream_surfaces`
5. 最后补 `forbidden_patterns` 和 `human_notes`

不要一上来就写长段参考答案。

## 8. 通过标准

一个 case 写完后，至少应该满足：

- 别的标注人能看懂你在约束什么
- judge 模型能据此做 criterion 级判断
- compare / regress 能从这个 case 中拿到可量化信号

如果只适合“人类读后大概知道意思”，那它还不是合格 gold set。

## 9. 快速判断清单

一个 gold set case 写完后，可以快速问自己六个问题：

1. 别的标注人只看这个 case，能不能独立知道“必须命中什么”？
2. judge 模型能不能对每条 `required_mechanisms` 做接近是/否的判断？
3. 这个 case 有没有明确禁止最常见的“看上去像对，其实没抓住机制”的错误写法？
4. 它有没有逼候选输出可被 `rag_worthy` / `worldbook_worthy` / `routing_hints` 直接消费的内容？
5. 它有没有绑定真实 `scene:<scene_id>` 或 `window_id`，而不是只凭印象下判断？
6. 如果换一个更差的 Style Bible 来跑 compare / regress，这个 case 能不能稳定拉开差距？

六个问题里只要有两项答不上来，这个 case 通常就还不够硬。
