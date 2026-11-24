"""Auditable, non-executable specification of AtlasPM's quantitative workflow.

The registry maps a professional multi-asset research lifecycle onto modules
that actually exist today. It is NOT a trade engine, broker API, strategy
approval, future-return predictor or cross-asset position allocator.

Status words describe *software capabilities*, not alpha profitability.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


SCHEMA = "atlaspm_standard_quant_workflow_v1"
CAPABILITIES = frozenset({"research", "partial_research", "synthetic_only", "planned"})


@dataclass(frozen=True, slots=True)
class WorkflowStage:
    code: str
    label: str
    objective: str
    capability: str
    depends_on: tuple[str, ...] = ()
    existing_modules: tuple[str, ...] = ()
    deliverables: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssetSleeve:
    code: str
    label: str
    capability: str
    research_methods: tuple[str, ...]
    existing_modules: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


STAGES: tuple[WorkflowStage, ...] = (
    WorkflowStage(
        "data", "01 数据获取与治理",
        "严格时点数据、完整性检查、公司行动及交易日历",
        "partial_research",
        existing_modules=(
            "atlaspm.data.us_market_universe",
            "atlaspm.data.us_market_quality",
            "atlaspm.data.factor_db",
            "atlaspm.data.factor_eval",
            "atlaspm.data.factor_v3",
            "atlaspm.data.corporate_action_reconciliation",
            "atlaspm.data.historical_universe",
            "atlaspm.data.stock_fundamentals_import",
            "atlaspm.execution.market_close",
        ),
        deliverables=("data_quality_report", "point_in_time_universe"),
        limitations=("完整历史退市与 IPO 时点股票池尚未验证", "价格源调整可能存在追溯修订"),
    ),
    WorkflowStage(
        "alpha", "02 因子研究与 Alpha 信号",
        "生成因子、训练排名模型、与简明基线比较",
        "research", ("data",),
        existing_modules=(
            "atlaspm.strategies.equity.consensus_20_screener",
            "atlaspm.research.ai_stock_ranker_v2",
            "atlaspm.research.training_engine",
        ),
        deliverables=("factor_diagnostics", "dated_research_signals"),
        limitations=("现有美股大池 V2 测试尚未证明有效 Alpha",),
    ),
    WorkflowStage(
        "validation", "03 回测、样本外验证与准入",
        "历史回测、Walk-Forward、费用、稳健性与独立审查",
        "partial_research", ("alpha",),
        existing_modules=(
            "atlaspm.research.stock_walkforward",
            "atlaspm.research.stock_comparison",
            "atlaspm.engine.replay",
            "atlaspm.research.training_experiment",
            "atlaspm.research.v2_momentum_diagnostics",
            "atlaspm.strategies.equity.alpha_eligibility",
            "atlaspm.research.atlas_robustness_audit",
        ),
        deliverables=("walk_forward_evidence", "research_eligibility"),
        limitations=("已反复查看的历史不等于独立测试集", "研究候选资格不授权交易"),
    ),
    WorkflowStage(
        "sleeves", "04 股票 / 期权 / 债券策略层",
        "按资产类别独立研究信号，再合并成可审查候选池",
        "partial_research", ("validation",),
        existing_modules=(
            "atlaspm.strategies.equity.monthly_stock_selection",
            "atlaspm.strategies.equity.alpha_eligibility",
            "atlaspm.strategies.options.protective_put",
            "atlaspm.strategies.bonds.treasury_replay",
        ),
        deliverables=("equity_candidates", "optional_derivative_and_bond_research"),
        limitations=("期权、债券独立 Alpha/选券引擎仍待开发",),
    ),
    WorkflowStage(
        "portfolio", "05 组合优化 + 风险预算 + 对冲",
        "联合考虑收益风险、相关性、成本、仓位与对冲敞口",
        "partial_research", ("sleeves",),
        existing_modules=(
            "atlaspm.engine.mean_variance",
            "atlaspm.engine.unified",
            "atlaspm.engine.stock_allocation",
            "atlaspm.hedging.multiasset_hedge",
            "atlaspm.risk.risk_budget",
            "atlaspm.risk.ibkr_multiasset_budget",
            "atlaspm.risk.atlas_risknet",
            "atlaspm.risk.atlas_fusionnet",
        ),
        deliverables=("hypothetical_weights", "risk_exposure_report", "hedge_research"),
        limitations=("尚未实现跨三资产策略的自动优化/对冲闭环",),
    ),
    WorkflowStage(
        "pretrade", "06 交易前风险与合规检查",
        "校验价格、保证金、头寸、集中度、流动性与操作权限",
        "partial_research", ("portfolio",),
        existing_modules=(
            "atlaspm.execution.institutional_gate",
            "atlaspm.execution.institutional_batch",
            "atlaspm.rebalance_workbench",
            "atlaspm.execution.ibkr_whatif",
        ),
        deliverables=("offline_pretrade_review", "paper_what_if_review"),
        limitations=("IBKR Paper 联调待验收", "预检不授予实盘权限"),
    ),
    WorkflowStage(
        "execution", "07 订单执行、成交回报与对账",
        "研究订单生命周期、部分成交、取消、异常及账本一致性",
        "partial_research", ("pretrade",),
        existing_modules=(
            "atlaspm.execution.paper_oms",
            "atlaspm.execution.broker_reconcile",
            "atlaspm.execution.ibkr_paper_orders",
            "atlaspm.execution.paper_order_journal",
        ),
        deliverables=("synthetic_oms_journal", "paper_order_journal", "reconciliation_diagnostics"),
        limitations=("IBKR Paper 执行和对账待券商联调验收，仅支持人工确认发单",),
    ),
    WorkflowStage(
        "monitor", "08 监控、绩效归因与复盘",
        "监控组合和风险，分析成本、基准、因子及模型漂移",
        "partial_research", ("execution",),
        existing_modules=(
            "atlaspm.monitoring.performance",
            "atlaspm.monitoring.ibkr_dashboard",
        ),
        deliverables=("offline_performance_report", "model_review_queue"),
        limitations=("目前不是实时生产监控或独立归因平台",),
    ),
)

ASSET_SLEEVES: tuple[AssetSleeve, ...] = (
    AssetSleeve(
        "equity", "股票策略", "partial_research",
        ("多因子", "LightGBM 排序", "动量基线", "月度候选池"),
        existing_modules=(
            "atlaspm.research.ai_stock_ranker_v2",
            "atlaspm.strategies.equity.monthly_stock_selection",
            "atlaspm.strategies.equity.stock_screener",
        ),
        limitations=("样本外 Alpha 尚未验证",),
    ),
    AssetSleeve(
        "options", "期权策略", "partial_research",
        ("历史报价回放", "保护性看跌期权"),
        existing_modules=("atlaspm.strategies.options.option_replay", "atlaspm.strategies.options.protective_put"),
        limitations=("波动率曲面、Greek 风险和交易联调待开发",),
    ),
    AssetSleeve(
        "bonds", "债券策略", "partial_research",
        ("美国国债历史报价", "现金流与久期风险回放"),
        existing_modules=("atlaspm.strategies.bonds.treasury_replay",),
        limitations=("信用债选券、完整期限结构和交易联调待开发",),
    ),
)

# A continuous control plane: risk checks are not just a post-portfolio step.
CROSS_CUTTING_CONTROLS: tuple[str, ...] = (
    "market_and_factor_exposure",
    "credit_and_counterparty",
    "liquidity_and_capacity",
    "portfolio_concentration_and_leverage",
    "operational_and_data_integrity",
    "compliance_and_permissions",
)

# Explicit research -> operational gates; none represents broker authorization.
REQUIRED_GATES: tuple[str, ...] = (
    "point_in_time_data_and_actions_verified",
    "independent_out_of_sample_alpha_validated",
    "costed_replay_and_portfolio_risk_approved",
    "pretrade_broker_provenance_and_margin_verified",
    "paper_orders_and_reconciliation_verified",
)


def validate_workflow(
    stages: Iterable[WorkflowStage] = STAGES,
    sleeves: Iterable[AssetSleeve] = ASSET_SLEEVES,
) -> None:
    """Require a unique, ordered and acyclic contract; never execute stages."""
    steps = tuple(stages)
    assets = tuple(sleeves)
    ids = [stage.code for stage in steps]
    if not steps or len(ids) != len(set(ids)):
        raise ValueError("Workflow stages must have distinct nonempty IDs")
    if not all(isinstance(item, WorkflowStage) and item.code for item in steps):
        raise ValueError("Invalid workflow stage definition")
    seen: set[str] = set()
    for stage in steps:
        if stage.capability not in CAPABILITIES:
            raise ValueError(f"Unsupported capability for {stage.code}")
        if not stage.label or not stage.objective:
            raise ValueError(f"Incomplete stage metadata: {stage.code}")
        if stage.code in stage.depends_on:
            raise ValueError(f"Self-dependency in {stage.code}")
        if len(stage.depends_on) != len(set(stage.depends_on)):
            raise ValueError(f"Duplicate dependency for {stage.code}")
        for dependency in stage.depends_on:
            if dependency not in seen:
                raise ValueError(
                    f"Unresolved, cyclic or out-of-order dependency: {stage.code} -> {dependency}"
                )
        seen.add(stage.code)
    assets_seen: set[str] = set()
    for asset in assets:
        if not isinstance(asset, AssetSleeve) or not asset.code or asset.code in assets_seen:
            raise ValueError("Duplicate or invalid asset sleeve")
        if asset.capability not in CAPABILITIES or not asset.label or not asset.research_methods:
            raise ValueError(f"Invalid asset sleeve: {asset.code}")
        assets_seen.add(asset.code)
    if not {"equity", "options", "bonds"}.issubset(assets_seen):
        raise ValueError("Multi-asset research requires equity/options/bonds slots")


def workflow_report() -> dict[str, object]:
    """Machine-readable architectural inventory, not a runnable strategy."""
    validate_workflow()
    return {
        "schema": SCHEMA,
        "architecture_only": True,
        "research_only": True,
        "live_execution_enabled": False,
        "automatic_broker_orders_enabled": False,
        "automatic_stock_to_portfolio_handoff_enabled": False,
        "hedging_engine_implemented": False,
        "stock_alpha_validated_for_execution": False,
        "stages": [asdict(stage) for stage in STAGES],
        "asset_sleeves": [asdict(asset) for asset in ASSET_SLEEVES],
        "continuous_risk_controls": list(CROSS_CUTTING_CONTROLS),
        "independent_approval_gates_required": list(REQUIRED_GATES),
        "feedback_loop": {"from": "monitor", "to": "alpha", "type": "research_review_only"},
        "warnings": [
            "Architecture inventory only: nothing executes, allocates or authorizes orders.",
            "Options, Treasuries and multi-asset hedges have historical replay with limited coverage.",
            "IBKR Paper integration still requires authenticated validation.",
            "Risk management spans research, portfolio, execution and monitoring.",
            "Hedges must be jointly optimized with exposures and costs, not appended blindly.",
        ],
    }


def mermaid_flowchart() -> str:
    """A GitHub-renderable diagram aligned with the registry's stage IDs."""
    validate_workflow()
    labels = {stage.code: stage.label for stage in STAGES}
    lines = ["flowchart LR"]
    for code, label in labels.items():
        lines.append(f'    {code}["{label}"]')
    for stage in STAGES:
        if stage.code == "sleeves":
            continue
        for dependency in stage.depends_on:
            if dependency != "sleeves":
                lines.append(f"    {dependency} --> {stage.code}")
    lines += [
        '    equity(("股票研究"))',
        '    options(("期权历史回放"))',
        '    bonds(("国债历史回放"))',
        "    validation --> sleeves",
        "    sleeves --> equity",
        "    sleeves --> options",
        "    sleeves --> bonds",
        "    equity --> portfolio",
        "    options --> portfolio",
        "    bonds --> portfolio",
        '    controls[["贯穿全流程：风险 / 数据 / 合规控制"]]',
        "    controls -.-> validation",
        "    controls -.-> portfolio",
        "    controls -.-> pretrade",
        "    controls -.-> execution",
        "    controls -.-> monitor",
        "    monitor -.->|复盘与重新验证| alpha",
    ]
    return "\n".join(lines)
