# AtlasPM — 完整交易生命周期 V1

This is the seven-phase integration framework for the supplied trading workflow.
It complements the existing eight-stage multi-asset research registry
([QUANT_WORKFLOW.md](QUANT_WORKFLOW.md)); no legacy API or strategy is replaced.

## Lifecycle

```mermaid
flowchart TD
    subgraph A["1 目标与约束"]
        mandate["收益目标 / 风险预算 / 账户 / 股票池约束"]
    end
    subgraph B["2 数据与研究输入"]
        inputs["成交量 / 新闻 / SEC / 期权异常流 / 技术面 / 投行 / 基本面 / 宏观行业"]
        quality["时点 / 缺失 / 复权 / 延迟 / 来源检查"]
        research["AI 多模型融合 / 置信度 / 预期收益 / 风险"]
        inputs --> quality --> research
    end
    subgraph C["3 策略与计划"]
        universe["流动性 / 市值 / 停牌 / 退市筛选"]
        validation["回测 / 样本外 / 压力测试"]
        plan["理由 / 目标价 / 止损 / 持有期"]
        cost["佣金 / 点差 / 滑点 / 融资 / 税费"]
        universe --> validation --> plan --> cost
    end
    subgraph D["4 组合构建与风控审批"]
        allocation["仓位 / 杠杆 / 现金 / 再平衡"]
        risk["集中度 / 回撤 / 保证金 / 流动性 / 熔断"]
        preflight["时段 / 价格偏离 / 停牌 / 订单规模"]
        allocation --> risk --> preflight
    end
    subgraph E["5 执行与成交"]
        orders["订单审查 / 拆单 / 撤单"]
        fills["成交审查 / 部分成交 / 拒单 / 超时"]
        postfill["成交后风险重算与对账"]
        orders --> fills --> postfill
    end
    subgraph F["6 持仓管理与退出"]
        monitor["持仓监控 / 信号 / 财报 / 风险漂移"]
        adjust["加仓 / 减仓 / 再平衡计划"]
        exit["止盈 / 止损 / 时间 / 逻辑失效"]
        flat["平仓或零成交确认 / 无未决订单"]
        monitor --> monitor
        monitor --> adjust
        monitor --> exit
    end
    subgraph G["7 记录、复盘与迭代"]
        journal["日志 / 信号快照 / 合规"]
        attribution["收益 / 风险 / 成本 / 滑点归因"]
        models["模型漂移 / 版本 / 重训 / 回滚"]
        journal --> attribution --> models
    end
    mandate --> inputs
    research --> universe
    cost --> allocation
    preflight --> orders
    postfill --> monitor
    postfill --> flat
    adjust --> cost
    exit --> cost
    flat --> journal
    models -.->|新周期与独立验证| mandate
    guard["全流程保护：紧急停止 / 熔断 / 人工接管 / 告警 / 模拟优先"]
    guard -.-> quality
    guard -.-> risk
    guard -.-> orders
    guard -.-> monitor
```

## Canonical domain boundaries

| Phase | Reuse existing code | Remaining integration work |
| --- | --- | --- |
| 1 Mandate | engine.models, risk.risk_budget | Versioned mandate schema, account policy |
| 2 Inputs | data, research | News, analyst research and unusual options flow adapters; point-in-time provenance; calibrated multi-model fusion |
| 3 Plan | strategies.equity, research, engine.replay | Costed trade-plan contract, independent validation evidence |
| 4 Allocation and risk | engine.unified, risk, execution.institutional_gate | Unified handoff, current broker snapshot and preflight approval |
| 5 Execution | execution.paper_oms, existing IBKR Paper modules | Authenticated Paper acceptance, partial fill, unknown order and retry reconciliation |
| 6 Positions and exits | risk.ibkr_margin_monitor, close_drafts, execution.institutional_rebalance | Continuous position monitor, independent exit policies and flat confirmation |
| 7 Review | monitoring, research.training_experiment | Attribution, immutable model registry handoff and independent promotion |

All names are under atlaspm. Existing code references are integration
candidates, **not** proof that a provider is connected or a stage is production-ready.

## Offline review API

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m atlaspm.framework.trading_lifecycle
& $py -m unittest discover -s tests -p test_trading_lifecycle.py -v
```

```python
from atlaspm.framework.trading_lifecycle import LifecycleReview, Receipt, Step

review = LifecycleReview("synthetic-example")
review = review.record(Receipt(Step.MANDATE, "fixture-mandate", "reviewed"))
review = review.record(Receipt(Step.INPUTS, "fixture-inputs", "reviewed"))
review = review.record(Receipt(Step.QUALITY, "fixture-quality", "blocked", "stale quotes"))
assert review.status == "blocked"
assert not review.report()["trade_authorized"]
```

The 21 review steps and permitted transitions live in
`src/atlaspm/framework/trading_lifecycle.py`. Receipts are immutable and
require a distinct evidence reference. They are **declared** evidence only;
no actual evidence content, timestamps, signatures, fills or positions are
verified. The caller must use synthetic or independently verified local records.
A block terminates this review, not the management of real positions.

## Safety and implementation boundaries

- This is an offline review state machine, not an order executor, risk engine,
  scheduler, persistent OMS, or model deployment service. Its declared control
  list is a requirement inventory, not implemented emergency-stop functionality.
- Every adjustment/exit review revisits costs, allocation, risk and preflight.
  Before an operational adapter exists, distinguish increase-risk from
  reduce-only actions; increasing risk requires current independent alpha
  eligibility and exits must never depend on entry-alpha eligibility.
- Real emergency reduction and cancellation must have a separate independently
  validated operational route; a blocked review must never strand risk.
- Flat reconciliation requires actual broker position and outstanding-order
  verification before operational completion; the offline API does not perform it.
- Existing manually confirmed IBKR Paper behavior is untouched. There is no
  auto-submit, live permission, automatic model promotion or live scale-up.
- Implement actual controls and provider adapters in canonical domains, not
  duplicate algorithms under framework/. Add independent acceptance tests before
  wiring any operational handoff. Keep all private inputs and artifacts local.
