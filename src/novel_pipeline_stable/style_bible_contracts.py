from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SAMPLING_REPORT_FILE = "sampling_report.json"
COVERAGE_REPORT_FILE = "style_bible_coverage_report.json"
ROUTED_INDEX_FILE = "style_bible_routed_index.json"
BATCH_PLAN_FILE = "batch_plan.json"
PLANNER_DEBUG_REPORT_FILE = "planner_debug_report.json"
BUCKET_MEMO_DIR = "bucket_memos"
REDUCE_TRACE_FILE = "style_bible_reduce_trace.json"
REASONING_FILE = "style_bible_reasoning.json"
EXPORT_FLAT_FILE = "style_bible_export_flat.json"
JUDGE_FLAT_FILE = "judge_flat.json"

STYLE_BIBLE_SAMPLING_REPORT_VERSION = "style-bible-sampling-report-v2"
STYLE_BIBLE_ROUTED_INDEX_VERSION = "style-bible-routed-index-v2"
STYLE_BIBLE_BATCH_PLAN_VERSION = "style-bible-batch-plan-v3"
STYLE_BIBLE_BUCKET_MEMO_VERSION = "style-bible-bucket-memo-v2"

SAMPLING_MODE_DEBUG_SMALL = "debug_small"
SAMPLING_MODE_SCOPE_STRATIFIED = "scope_stratified_sample_v2"
SAMPLING_MODE_FULL_CORPUS = "full_corpus_routed_v1"
ROUTING_MODE_SIGNAL_FUSION_V2 = "signal_fusion_router_v2"
BATCHING_MODE_BUCKET_AFFINITY_V3 = "bucket_affinity_planner_v3"
ORPHANAGE_BUCKET_ID = "orphanage"
ORPHANAGE_BUCKET_ROUTING_THRESHOLD = 0.18


@dataclass(frozen=True, slots=True)
class StyleBibleAxisDefinition:
    axis_id: str
    label: str
    description: str
    keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StyleBibleBucketDefinition:
    bucket_id: str
    label: str
    description: str
    primary_axes: tuple[str, ...]
    keywords: tuple[str, ...]


PRIORITY_AXES: tuple[StyleBibleAxisDefinition, ...] = (
    StyleBibleAxisDefinition(
        axis_id="resource_pressure",
        label="resource pressure / debt pressure",
        description="Money, debt, cash-flow, and survival costs dominate the action logic.",
        keywords=("债务", "贷款", "现金流", "手术费", "没钱", "穷", "补习费", "借贷", "成本", "收益", "余额", "花钱"),
    ),
    StyleBibleAxisDefinition(
        axis_id="education_filter",
        label="education filter / ranking gate",
        description="Schooling, rankings, exams, and qualification gates sort people into tiers.",
        keywords=("学校", "排名", "分数", "面试", "示范班", "升学", "教育", "门槛", "考试", "资格", "班级"),
    ),
    StyleBibleAxisDefinition(
        axis_id="body_modification",
        label="body modification / body as cost",
        description="Bodies are modified, traded, or optimized as part of the system.",
        keywords=("身体", "改造", "绝育", "变性", "器官", "肉身", "植入", "手术", "法骸", "灵根", "肌肉", "骨"),
    ),
    StyleBibleAxisDefinition(
        axis_id="institutional_absurdity",
        label="institutional absurdity / procedural voice",
        description="Absurd rules are delivered through calm administrative or procedural language.",
        keywords=("制度", "规则", "流程", "通知", "标准", "政策", "条件", "合同", "审批", "管理", "规定"),
    ),
    StyleBibleAxisDefinition(
        axis_id="dark_humor",
        label="dark humor / deadpan satire",
        description="Deadpan delivery and tonal mismatch create black comedy.",
        keywords=("黑色幽默", "冷面", "吐槽", "讽刺", "荒诞", "反差", "笑点", "冷笑", "一本正经"),
    ),
    StyleBibleAxisDefinition(
        axis_id="family_labor",
        label="family labor / household exchange",
        description="Household labor and family sacrifice subsidize the protagonist's survival.",
        keywords=("家庭", "母亲", "加班", "转账", "资源交换", "兼职", "体面", "凑钱", "还债", "家里"),
    ),
    StyleBibleAxisDefinition(
        axis_id="labor_logic",
        label="labor logic",
        description="Cultivation and advancement behave like work, shifts, or piece-rate labor.",
        keywords=("劳动", "打工", "工时", "时薪", "绩效", "加班", "赚钱", "接活", "补课", "工作", "兼职"),
    ),
    StyleBibleAxisDefinition(
        axis_id="identity_shame",
        label="identity shame / status exposure",
        description="Humiliation, status loss, and social exposure shape behavior.",
        keywords=("羞耻", "丢脸", "体面", "地位", "外地人", "掉价", "贬低", "看穿", "排名低", "穷学生"),
    ),
    StyleBibleAxisDefinition(
        axis_id="production_commonwealth",
        label="production commonwealth / mutual production",
        description="Mutual aid, co-production, and shared labor form fragile alliances.",
        keywords=("共同训练", "共享资源", "互相带活", "生产共同体", "一起赚钱", "互助", "协作", "搭伙", "分工", "共用"),
    ),
    StyleBibleAxisDefinition(
        axis_id="asset_repricing",
        label="asset repricing / predatory repricing",
        description="People, contracts, and opportunities are repeatedly re-priced.",
        keywords=("估值", "围猎", "招揽", "打压", "重新定价", "赔偿", "补偿", "报价", "压价", "抬价"),
    ),
)


PRIORITY_BUCKETS: tuple[StyleBibleBucketDefinition, ...] = (
    StyleBibleBucketDefinition(
        bucket_id="resource_pressure",
        label="resource pressure",
        description="Debt, cash, and survival-cost pressure on the protagonist.",
        primary_axes=("resource_pressure", "family_labor"),
        keywords=("债务", "贷款", "现金流", "余额", "花钱", "手术费", "补习费", "借贷"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="exam_screening",
        label="exam screening",
        description="Ranking, examination, and schooling as institutional screening.",
        primary_axes=("education_filter", "institutional_absurdity", "identity_shame"),
        keywords=("面试", "考试", "示范班", "排名", "升学", "筛选", "考场", "门槛"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="body_assetization",
        label="body assetization",
        description="The body treated as a replaceable, billable, or improvable asset.",
        primary_axes=("body_modification", "resource_pressure", "asset_repricing"),
        keywords=("身体", "器官", "手术", "绝育", "改造", "植入", "法骸", "灵根"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="institutional_pipeline",
        label="institutional pipeline",
        description="Administrative process language that normalizes cruelty.",
        primary_axes=("institutional_absurdity", "education_filter", "labor_logic"),
        keywords=("制度", "流程", "标准", "通知", "政策", "审批", "管理", "规定"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="dark_humor",
        label="dark humor",
        description="Deadpan satire and tonal mismatch.",
        primary_axes=("dark_humor", "institutional_absurdity"),
        keywords=("黑色幽默", "吐槽", "冷面", "反差", "荒诞", "讽刺", "笑点"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="family_survival",
        label="family survival",
        description="Household sacrifice and family labor keeping the system running.",
        primary_axes=("family_labor", "resource_pressure"),
        keywords=("母亲", "家庭", "转账", "凑钱", "加班", "兼职", "家里", "还债"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="gray_labor",
        label="gray labor",
        description="Side jobs, underground labor, and piece-work survival tactics.",
        primary_axes=("labor_logic", "resource_pressure", "production_commonwealth"),
        keywords=("打工", "兼职", "接活", "补课", "工时", "时薪", "赚钱", "工作"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="identity_shame",
        label="identity shame",
        description="Status panic, humiliation, and exposure.",
        primary_axes=("identity_shame", "education_filter"),
        keywords=("体面", "羞耻", "丢脸", "看不起", "外地", "贬低", "排名低"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="collective_production",
        label="collective production",
        description="Shared labor, collaborative training, and mutual production.",
        primary_axes=("production_commonwealth", "labor_logic"),
        keywords=("互助", "协作", "共享资源", "搭伙", "一起赚钱", "共同训练", "分工"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="asset_repricing",
        label="asset repricing",
        description="Contracts, compensation, and predatory re-valuation.",
        primary_axes=("asset_repricing", "resource_pressure", "institutional_absurdity"),
        keywords=("估值", "赔偿", "补偿", "报价", "压价", "抬价", "重新定价", "围猎"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="contract_sales",
        label="contract / sales",
        description="Contracts, sales language, sponsorship, and platformized persuasion.",
        primary_axes=("asset_repricing", "institutional_absurdity", "resource_pressure"),
        keywords=("合同", "协议", "签约", "条款", "销售", "广告", "推广", "套餐", "授权"),
    ),
    StyleBibleBucketDefinition(
        bucket_id="commercialized_conflict",
        label="commercialized conflict",
        description="Competition or combat framed through cost, sponsorship, and market logic.",
        primary_axes=("resource_pressure", "asset_repricing", "body_modification"),
        keywords=("比赛", "战斗", "赞助", "广告", "市场", "赔偿", "直播", "商业"),
    ),
)

ORPHANAGE_BUCKET = StyleBibleBucketDefinition(
    bucket_id=ORPHANAGE_BUCKET_ID,
    label="orphanage",
    description="Low-confidence recovery bucket for items that fail to reach the semantic routing floor.",
    primary_axes=(),
    keywords=(),
)


def infer_sampling_mode(
    *,
    max_style_windows: int,
    max_scene_samples: int,
    available_style_windows: int | None = None,
    available_scene_samples: int | None = None,
) -> str:
    style_available = int(available_style_windows or 0)
    scene_available = int(available_scene_samples or 0)
    style_is_full = max_style_windows <= 0 or (style_available > 0 and max_style_windows >= style_available)
    scene_is_full = max_scene_samples <= 0 or (scene_available > 0 and max_scene_samples >= scene_available)
    if style_is_full and scene_is_full:
        return SAMPLING_MODE_FULL_CORPUS
    if max_style_windows <= 24 and max_scene_samples <= 24:
        return SAMPLING_MODE_DEBUG_SMALL
    return SAMPLING_MODE_SCOPE_STRATIFIED


def axis_catalog_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": axis.axis_id,
            "label": axis.label,
            "description": axis.description,
            "keywords": list(axis.keywords),
            "primary_axes": [],
        }
        for axis in PRIORITY_AXES
    ]


def bucket_catalog_payload(*, include_orphan_bucket: bool = False) -> list[dict[str, Any]]:
    payload = [
        {
            "id": bucket.bucket_id,
            "label": bucket.label,
            "description": bucket.description,
            "keywords": list(bucket.keywords),
            "primary_axes": list(bucket.primary_axes),
        }
        for bucket in PRIORITY_BUCKETS
    ]
    if include_orphan_bucket:
        payload.append(
            {
                "id": ORPHANAGE_BUCKET.bucket_id,
                "label": ORPHANAGE_BUCKET.label,
                "description": ORPHANAGE_BUCKET.description,
                "keywords": list(ORPHANAGE_BUCKET.keywords),
                "primary_axes": list(ORPHANAGE_BUCKET.primary_axes),
            }
        )
    return payload
