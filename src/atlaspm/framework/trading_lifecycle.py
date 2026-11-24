"""Seven-stage trading lifecycle: offline review and integration contracts.

This module records declared evidence, not verified approvals. It deliberately
has no broker client, scheduling, order submission or model promotion API.
Existing domain implementations are mapped, never invoked by the registry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json


class Step(str, Enum):
    MANDATE = "mandate"
    INPUTS = "inputs"
    QUALITY = "data_quality"
    RESEARCH = "research"
    UNIVERSE = "universe"
    VALIDATION = "validation"
    PLAN = "trade_plan"
    COST = "cost"
    ALLOCATION = "allocation"
    RISK = "risk"
    PREFLIGHT = "preflight"
    ORDER_REVIEW = "order_review"
    FILL_REVIEW = "fill_review"
    POST_FILL = "post_fill_risk_reconcile"
    MONITOR = "position_monitor"
    ADJUST = "adjustment_plan"
    EXIT = "exit_plan"
    FLAT = "flat_reconciliation"
    JOURNAL = "journal"
    ATTRIBUTION = "attribution"
    MODEL_REVIEW = "model_review"


@dataclass(frozen=True, slots=True)
class Phase:
    number: int
    label: str
    steps: tuple[Step, ...]
    existing_modules: tuple[str, ...]
    open_interfaces: tuple[str, ...]


PHASES = (
    Phase(1, "目标与约束", (Step.MANDATE,),
          ("atlaspm.engine.models", "atlaspm.risk.risk_budget"),
          ("versioned mandate: objectives, risk budget, account type, allowed universe",)),
    Phase(2, "数据与研究输入",
          (Step.INPUTS, Step.QUALITY, Step.RESEARCH),
          ("atlaspm.data.us_market_quality", "atlaspm.data.factor_db",
           "atlaspm.data.stock_fundamentals_import", "atlaspm.research.ai_stock_ranker_v2",
           "atlaspm.research.training_engine"),
          ("volume, news, SEC filings, unusual options flow, technicals, analyst reports, fundamentals, macro and industry",
           "point-in-time availability, provenance, freshness, corporate actions",
           "multi-model fusion, calibrated uncertainty, expected return and risk")),
    Phase(3, "策略与计划", (Step.UNIVERSE, Step.VALIDATION, Step.PLAN, Step.COST),
          ("atlaspm.strategies.equity.stock_screener", "atlaspm.research.stock_walkforward",
           "atlaspm.strategies.equity.alpha_eligibility", "atlaspm.engine.replay"),
          ("liquidity, market cap, halts and delisting eligibility",
           "independent holdout, stress and cost validation",
           "entry thesis, target, stop, holding period, spread, slippage, financing, fees and taxes")),
    Phase(4, "组合构建与风控审批", (Step.ALLOCATION, Step.RISK, Step.PREFLIGHT),
          ("atlaspm.engine.unified", "atlaspm.risk.risk_budget",
           "atlaspm.execution.institutional_gate", "atlaspm.ibkr_whatif"),
          ("cash, leverage, sizing, hedge and rebalance constraints",
           "concentration, drawdown, margin, liquidity, circuit breakers",
           "session, quote deviation, halt, order size and account checks")),
    Phase(5, "执行与成交", (Step.ORDER_REVIEW, Step.FILL_REVIEW, Step.POST_FILL),
          ("atlaspm.execution.paper_oms", "atlaspm.ibkr_paper_orders",
           "atlaspm.paper_order_journal", "atlaspm.execution.broker_reconcile"),
          ("order slicing, limit/market policy, cancellation and idempotency",
           "partial fills, rejections, timeouts and unknown-order reconciliation",
           "actual fills, quantities and refreshed portfolio risk")),
    Phase(6, "持仓管理与退出", (Step.MONITOR, Step.ADJUST, Step.EXIT, Step.FLAT),
          ("atlaspm.risk.ibkr_margin_monitor", "atlaspm.close_drafts",
           "atlaspm.execution.institutional_rebalance"),
          ("continuous stops, signal changes, earnings and exposure drift",
           "add/reduce/rebalance, risk-reducing exit route independent of entry-alpha approval",
           "profit/stop/time/thesis exit rules and verified flat position")),
    Phase(7, "记录、复盘与迭代", (Step.JOURNAL, Step.ATTRIBUTION, Step.MODEL_REVIEW),
          ("atlaspm.monitoring.audit", "atlaspm.monitoring.performance",
           "atlaspm.research.training_experiment"),
          ("signal snapshot, orders, tax and compliance records",
           "return/risk/cost/slippage attribution",
           "model drift, versioning, retraining, rollback and independent revalidation")),
)

# Feedback and retry routes are explicit; an exit must re-enter risk/preflight.
NEXT = {
    Step.MANDATE: (Step.INPUTS,),
    Step.INPUTS: (Step.QUALITY,),
    Step.QUALITY: (Step.RESEARCH,),
    Step.RESEARCH: (Step.UNIVERSE,),
    Step.UNIVERSE: (Step.VALIDATION,),
    Step.VALIDATION: (Step.PLAN,),
    Step.PLAN: (Step.COST,),
    Step.COST: (Step.ALLOCATION,),
    Step.ALLOCATION: (Step.RISK,),
    Step.RISK: (Step.PREFLIGHT,),
    Step.PREFLIGHT: (Step.ORDER_REVIEW,),
    Step.ORDER_REVIEW: (Step.FILL_REVIEW,),
    Step.FILL_REVIEW: (Step.POST_FILL,),
    Step.POST_FILL: (Step.MONITOR, Step.FLAT),
    Step.MONITOR: (Step.MONITOR, Step.ADJUST, Step.EXIT),
    Step.ADJUST: (Step.COST,),
    Step.EXIT: (Step.COST,),
    Step.FLAT: (Step.JOURNAL,),
    Step.JOURNAL: (Step.ATTRIBUTION,),
    Step.ATTRIBUTION: (Step.MODEL_REVIEW,),
    Step.MODEL_REVIEW: (),
}

CONTROLS = (
    "emergency_stop", "portfolio_circuit_breaker", "human_takeover",
    "exception_alerts", "simulation_first", "independent_release_gate",
    "no_automatic_broker_orders", "no_live_execution",
)


@dataclass(frozen=True, slots=True)
class Receipt:
    step: Step
    evidence_ref: str
    outcome: str
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.step, Step):
            raise ValueError("invalid step")
        if self.outcome not in ("reviewed", "blocked"):
            raise ValueError("invalid review outcome")
        if not isinstance(self.evidence_ref, str) or not self.evidence_ref.strip():
            raise ValueError("nonempty evidence reference required")
        if not isinstance(self.note, str):
            raise ValueError("note must be text")
        if self.outcome == "blocked" and not self.note.strip():
            raise ValueError("blocked review requires a reason")


@dataclass(frozen=True, slots=True)
class LifecycleReview:
    """In-memory replay of declared review receipts, never trade authorization.

    Blocked reviews are terminal. A new review must use fresh evidence.
    An operator must independently verify any evidence before operational use.
    """
    review_id: str
    receipts: tuple[Receipt, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.review_id, str) or not self.review_id.strip():
            raise ValueError("review ID required")
        if not isinstance(self.receipts, tuple):
            raise ValueError("receipts must be immutable")
        allowed = (Step.MANDATE,)
        seen_refs: set[str] = set()
        for receipt in self.receipts:
            if not isinstance(receipt, Receipt) or receipt.step not in allowed:
                raise ValueError("out-of-order or terminal review")
            ref = receipt.evidence_ref.strip()
            if ref in seen_refs:
                raise ValueError("duplicate evidence reference")
            seen_refs.add(ref)
            allowed = NEXT[receipt.step] if receipt.outcome == "reviewed" else ()

    @property
    def next_steps(self) -> tuple[Step, ...]:
        if not self.receipts:
            return (Step.MANDATE,)
        last = self.receipts[-1]
        return NEXT[last.step] if last.outcome == "reviewed" else ()

    @property
    def status(self) -> str:
        if not self.receipts:
            return "not_started"
        if self.receipts[-1].outcome == "blocked":
            return "blocked"
        if self.receipts[-1].step == Step.MODEL_REVIEW:
            return "review_complete"
        return "in_review"

    def record(self, receipt: Receipt) -> LifecycleReview:
        return LifecycleReview(self.review_id, (*self.receipts, receipt))

    def report(self) -> dict:
        return {
            "review_id": self.review_id, "status": self.status,
            "next_steps": [step.value for step in self.next_steps],
            "receipts": [asdict(receipt) for receipt in self.receipts],
            "evidence_independently_verified": False,
            "trade_authorized": False, "live_execution_enabled": False,
            "automatic_broker_orders_enabled": False,
        }


def architecture_report() -> dict:
    steps = [step for phase in PHASES for step in phase.steps]
    if len(steps) != len(set(steps)) or set(steps) != set(Step) or set(NEXT) != set(Step):
        raise RuntimeError("invalid lifecycle registry")
    if any(target not in NEXT for targets in NEXT.values() for target in targets):
        raise RuntimeError("invalid lifecycle edge")
    return {
        "schema": "atlaspm_trading_lifecycle_v1",
        "architecture_only": True,
        "phases": [asdict(phase) for phase in PHASES],
        "transitions": {step.value: [target.value for target in targets]
                        for step, targets in NEXT.items()},
        "controls_required": list(CONTROLS),
        "feedback": "new mandate and independent revalidation; no auto-promotion",
        "trade_authorized": False, "live_execution_enabled": False,
        "automatic_broker_orders_enabled": False,
    }


def main() -> int:
    print(json.dumps(architecture_report(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
