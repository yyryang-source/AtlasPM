"""Command-line entrypoint for reproducible Windows/local experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from atlaspm.optimizer import inverse_volatility_weights
from atlaspm.historical import download_yahoo_adjusted_prices, estimate_from_prices
from atlaspm.mean_variance import optimize_mean_variance
from atlaspm.portfolio import analyze_accounts, parse_accounts
from atlaspm.backtest import backtest_walk_forward
from atlaspm.audit import audit_backtests
from atlaspm.benchmark import compare_spy
from atlaspm.risk_budget import check_risk_budget
from atlaspm.risk_backtest import backtest_risk_budget
from atlaspm.institutional_gate import assess_institutional_intent
from atlaspm.institutional_ledger import build_institutional_ledger
from atlaspm.institutional_rebalance import plan_equity_trade
from atlaspm.institutional_batch import propose_multi_asset_batch
from atlaspm.paper_oms import replay_paper_orders
from atlaspm.broker_reconcile import reconcile_imported_broker_snapshot
from atlaspm.ibkr_gateway import (
    LocalGatewayTransport, FixtureGatewayTransport,
    collect_ibkr_readonly_account, redacted_console_summary,
    require_private_output,
)
from atlaspm.ibkr_mapping import diagnose_ibkr_contract_mapping, mapping_redacted_summary
from atlaspm.ibkr_financing import reconcile_ibkr_financing_margin, financing_redacted_summary
from atlaspm.ibkr_margin_monitor import collect_ibkr_margin_monitor, redacted_margin_console
from atlaspm.ibkr_margin_stress import run_margin_stress, redacted_stress_console
from atlaspm.ibkr_multiasset_budget import analyze_multiasset_risk_budget, redacted_multiasset_summary
from atlaspm.ibkr_reverse_stress import reverse_margin_stress, redacted_reverse_summary
from atlaspm.ibkr_risk_chain_audit import audit_ibkr_risk_chain, redacted_audit_summary
from atlaspm.ibkr_risk_audit_diff import compare_ibkr_risk_audits, redacted_audit_diff_summary
from atlaspm.ibkr_dashboard import build_dashboard_view, render_dashboard_html, redacted_dashboard_console
from atlaspm.ml_dynamic_rebalance import backtest_ml_dynamic_rebalance, redacted_ml_console
from atlaspm.ml_model_tournament import backtest_model_tournament, redacted_tournament_console
from atlaspm.atlas_regimenet import backtest_atlas_native, redacted_atlas_native_console
from atlaspm.atlas_robustness_audit import audit_atlas_risknet, redacted_audit_console
from atlaspm.stock_screener import screen_stocks
from atlaspm.stock_walkforward import backtest_stock_screener
from atlaspm.us_market_universe import build_us_listed_universe
from atlaspm.us_market_prices import sync_us_market_prices
from atlaspm.selection.us_market_quality import audit_current_us_stock_prices
from atlaspm.ai_stock_ranker import train_ai_stock_ranker, rank_with_ai
from atlaspm.ai_stock_ranker_v2 import train_stock_learning_to_rank, rank_stocks_v2
from atlaspm.monthly_stock_selection import monthly_candidates, write_monthly_research
from atlaspm.alpha_eligibility import build_from_file, research_action_check, write_eligibility_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlaspm", description="AtlasPM: local portfolio research toolkit"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Risk report from JSON input")
    analyze.add_argument("--file", type=Path, required=True)
    analyze.add_argument(
        "--declines", type=float, nargs="+", default=[0.10, 0.20, 0.30],
        help="Uniform decline fractions (e.g. 0.10 means down 10%%)",
    )
    analyze.add_argument(
        "--apr", type=float, default=0.065, help="Annual margin borrowing rate"
    )

    allocate = sub.add_parser(
        "allocate", help="Inverse-volatility capped allocation baseline"
    )
    allocate.add_argument(
        "--vol", nargs="+", required=True,
        help="Annualized volatility items such as QQQ=0.25 NVDA=0.50",
    )
    allocate.add_argument("--max-weight", type=float, default=0.60)
    optim = sub.add_parser("optimize", help="Constrained mean-variance scenario")
    optim.add_argument("--file", type=Path, required=True)
    optim.add_argument("--risk-aversion", type=float, default=4.0)
    optim.add_argument("--max-weight", type=float, default=0.60)
    optim.add_argument("--leverage", type=float, default=1.0)
    optim.add_argument("--apr", type=float, default=0.065)
    prices = sub.add_parser("download-prices", help="Download adjusted daily prices to a local CSV")
    prices.add_argument("--symbols", nargs="+", required=True)
    prices.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    prices.add_argument("--end", required=True, help="YYYY-MM-DD, exclusive")
    prices.add_argument("--output", type=Path, default=Path("local_data/prices.csv"))
    prices.add_argument("--overwrite", action="store_true")

    estimate = sub.add_parser("estimate", help="Estimate annual returns and covariance from local CSV")
    estimate.add_argument("--file", type=Path, required=True)
    estimate.add_argument("--output", type=Path, default=Path("local_data/estimates.json"))
    estimate.add_argument("--min-observations", type=int, default=60)

    backtest = sub.add_parser("backtest", help="Walk-forward, past-only portfolio simulation")
    backtest.add_argument("--file", type=Path, required=True)
    backtest.add_argument("--output", type=Path, default=Path("local_data/backtest.json"))
    backtest.add_argument("--lookback-days", type=int, default=756)
    backtest.add_argument("--rebalance-days", type=int, default=63)
    backtest.add_argument("--risk-aversion", type=float, default=4.0)
    backtest.add_argument("--max-weight", type=float, default=0.60)
    backtest.add_argument("--leverage", type=float, default=1.0)
    backtest.add_argument("--apr", type=float, default=0.065)
    backtest.add_argument("--fee-bps", type=float, default=5.0)
    backtest.add_argument("--maintenance", type=float, default=0.30)
    backtest.add_argument("--margin-policy", choices=["none", "next_close_deleverage_to_1x"], default="none")

    audit = sub.add_parser("audit", help="Compare saved cash and margin backtests")
    audit.add_argument("--cash", type=Path, required=True)
    audit.add_argument("--margin", type=Path, required=True)
    audit.add_argument("--output", type=Path, default=Path("local_data/risk_audit.json"))

    benchmark = sub.add_parser("benchmark-spy", help="Exact-date independent SPY comparison")
    benchmark.add_argument("--cash", type=Path, required=True)
    benchmark.add_argument("--margin", type=Path, required=True)
    benchmark.add_argument("--spy", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, default=Path("local_data/spy_comparison.json"))

    risk = sub.add_parser("risk-check", help="Read-only pre-trade research risk budget")
    risk.add_argument("--file", type=Path, required=True, help="Historical estimate JSON")
    risk.add_argument("--leverage", type=float, default=2.0)
    risk.add_argument("--risk-aversion", type=float, default=4.0)
    risk.add_argument("--max-weight", type=float, default=0.60, help="Optimizer concentration cap")
    risk.add_argument("--max-leverage", type=float, default=2.0)
    risk.add_argument("--max-name-weight", type=float, default=0.60, help="Risk budget concentration cap")
    risk.add_argument("--max-volatility", type=float, default=0.30)
    risk.add_argument("--stress-decline", type=float, default=0.30)
    risk.add_argument("--max-stress-loss", type=float, default=0.40)
    risk.add_argument("--maintenance", type=float, default=0.30)
    risk.add_argument("--min-buffer", type=float, default=0.05)
    risk.add_argument("--apr", type=float, default=0.065)
    risk.add_argument("--max-financing-cost", type=float, default=0.08)
    risk.add_argument("--output", type=Path, default=Path("local_data/risk_budget.json"))

    managed = sub.add_parser("backtest-risk", help="Past-only dynamic risk budgets with a cash sleeve")
    managed.add_argument("--file", type=Path, required=True)
    managed.add_argument("--output", type=Path, default=Path("local_data/risk_managed_backtest.json"))
    managed.add_argument("--lookback-days", type=int, default=756)
    managed.add_argument("--rebalance-days", type=int, default=63)
    managed.add_argument("--risk-aversion", type=float, default=4.0)
    managed.add_argument("--max-weight", type=float, default=0.60)
    managed.add_argument("--max-leverage", type=float, default=2.0)
    managed.add_argument("--max-volatility", type=float, default=0.30)
    managed.add_argument("--stress-decline", type=float, default=0.30)
    managed.add_argument("--max-stress-loss", type=float, default=0.40)
    managed.add_argument("--maintenance", type=float, default=0.30)
    managed.add_argument("--min-buffer", type=float, default=0.05)
    managed.add_argument("--apr", type=float, default=0.065)
    managed.add_argument("--max-financing-cost", type=float, default=0.08)
    managed.add_argument("--fee-bps", type=float, default=5.0)
    managed.add_argument("--max-daily-tail-loss", type=float, default=None,
                         help="Optional maximum historical one-day ES as fraction of equity")
    managed.add_argument("--tail-fraction", type=float, default=0.05,
                         help="Worst historical tail share, >0 and <=0.25")

    institutional = sub.add_parser(
        "institutional-check",
        help="Institutional-style fail-closed paper order intent preflight",
    )
    institutional.add_argument("--file", type=Path, required=True)
    institutional.add_argument(
        "--output", type=Path, default=Path("local_data/institutional_preflight.json")
    )

    ledger = sub.add_parser(
        "ledger-report", help="Read-only cash, stock, Treasury and option accounting"
    )
    ledger.add_argument("--file", type=Path, required=True)
    ledger.add_argument(
        "--output", type=Path, default=Path("local_data/institutional_ledger_report.json")
    )

    planner = sub.add_parser(
        "plan-equity",
        help="Read-only BUY/SELL equity proposal against ledger and RMS",
    )
    planner.add_argument("--ledger", type=Path, required=True)
    planner.add_argument("--plan", type=Path, required=True)
    planner.add_argument(
        "--output", type=Path, default=Path("local_data/equity_trade_plan.json")
    )

    batch = sub.add_parser(
        "plan-basket",
        help="Read-only combined equities, UST, protective put and margin debt plan",
    )
    batch.add_argument("--ledger", type=Path, required=True)
    batch.add_argument("--plan", type=Path, required=True)
    batch.add_argument(
        "--output", type=Path, default=Path("local_data/institutional_batch_plan.json")
    )

    paper = sub.add_parser(
        "oms-replay",
        help="Offline paper OMS event replay for an approved synthetic multi-asset basket",
    )
    paper.add_argument("--ledger", type=Path, required=True)
    paper.add_argument("--plan", type=Path, required=True)
    paper.add_argument("--events", type=Path, required=True)
    paper.add_argument(
        "--output", type=Path, default=Path("local_data/paper_oms_replay.json")
    )

    reconcile = sub.add_parser(
        "broker-reconcile",
        help="Offline, read-only broker-export vs local ledger reconciliation",
    )
    reconcile.add_argument("--ledger", type=Path, required=True)
    reconcile.add_argument("--snapshot", type=Path, required=True)
    reconcile.add_argument(
        "--output", type=Path,
        default=Path("local_data/broker_reconciliation.json"),
    )

    ibkr = sub.add_parser(
        "ibkr-readonly",
        help="Explicit-opt-in local IBKR Client Portal Gateway GET-only account capture",
    )
    ibkr.add_argument("--account", required=True)
    ibkr.add_argument("--fixture", type=Path, default=None)
    ibkr.add_argument("--allow-local-gateway", action="store_true")
    ibkr.add_argument("--url", default="https://localhost:5000/v1/api")
    ibkr.add_argument("--ca-file", type=Path, default=None)
    ibkr.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_private_raw.json")
    )

    mapper = sub.add_parser(
        "ibkr-map-check",
        help="Offline, non-executable conid identity and unit mapping diagnostics",
    )
    mapper.add_argument("--capture", type=Path, required=True)
    mapper.add_argument("--mapping", type=Path, required=True)
    mapper.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_mapping_check.json")
    )

    financing = sub.add_parser(
        "ibkr-financing-check",
        help="Offline IBKR financing principal and current margin summary cross-check",
    )
    financing.add_argument("--capture", type=Path, required=True)
    financing.add_argument("--mapping", type=Path, required=True)
    financing.add_argument("--statement", type=Path, required=True)
    financing.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_financing_check.json")
    )

    monitor = sub.add_parser(
        "ibkr-margin-monitor",
        help="GET-only local Gateway portfolio summary monitor (or synthetic fixture)",
    )
    monitor.add_argument("--account", required=True)
    monitor.add_argument("--fixture", type=Path, default=None)
    monitor.add_argument("--allow-local-gateway", action="store_true")
    monitor.add_argument("--url", default="https://localhost:5000/v1/api")
    monitor.add_argument("--ca-file", type=Path, default=None)
    monitor.add_argument("--max-age-seconds", type=int, default=600)
    monitor.add_argument("--min-buffer-ratio", type=float, default=0.15)
    monitor.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_margin_monitor.json")
    )

    stress = sub.add_parser(
        "ibkr-stress",
        help="Offline hypothetical margin shock and financing-cost scenarios; no execution",
    )
    stress.add_argument("--monitor", type=Path, required=True)
    stress.add_argument("--financing", type=Path, required=True)
    stress.add_argument("--scenarios", type=Path, required=True)
    stress.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_margin_stress.json")
    )

    multiasset = sub.add_parser(
        "ibkr-multiasset-budget",
        help="Offline hypothetical correlation stress and financing risk-budget check",
    )
    multiasset.add_argument("--stress", type=Path, required=True)
    multiasset.add_argument("--assumptions", type=Path, required=True)
    multiasset.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_multiasset_budget.json")
    )

    reverse = sub.add_parser(
        "ibkr-reverse-stress",
        help="Offline reverse stress: assumed market decline needed to hit margin and buffer limits",
    )
    reverse.add_argument("--budget", type=Path, required=True)
    reverse.add_argument("--assumptions", type=Path, required=True)
    reverse.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_reverse_stress.json")
    )

    chain = sub.add_parser(
        "ibkr-risk-audit",
        help="Audit five private offline IBKR risk diagnostics; no broker proof or execution",
    )
    chain.add_argument("--monitor", type=Path, required=True)
    chain.add_argument("--financing", type=Path, required=True)
    chain.add_argument("--stress", type=Path, required=True)
    chain.add_argument("--multiasset", type=Path, required=True)
    chain.add_argument("--reverse", type=Path, required=True)
    chain.add_argument("--compare", type=Path, default=None)
    chain.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_risk_audit.json")
    )

    audit_diff = sub.add_parser(
        "ibkr-audit-diff",
        help="Compare two private unsigned risk-audit files; no broker provenance claim",
    )
    audit_diff.add_argument("--previous", type=Path, required=True)
    audit_diff.add_argument("--current", type=Path, required=True)
    audit_diff.add_argument(
        "--output", type=Path, default=Path("local_data/ibkr_risk_audit_diff.json")
    )

    dashboard = sub.add_parser(
        "ibkr-dashboard",
        help="Generate local offline redacted HTML dashboard from existing private reports",
    )
    dashboard.add_argument(
        "--folder", type=Path, default=Path("local_data"),
        help="Private local_data folder containing v0.4.10–v0.4.15 reports",
    )
    dashboard.add_argument(
        "--output", type=Path, default=Path("local_data/atlaspm_dashboard.html"),
    )

    ml = sub.add_parser(
        "ml-rebalance-backtest",
        help="Train supervised volatility risk models and simulate autonomous cash-only rebalances",
    )
    ml.add_argument("--file", type=Path, required=True)
    ml.add_argument("--model", choices=["ridge", "lightgbm"], default="ridge",
                    help="Trained supervised risk model; LightGBM requires optional [ml] extra")
    ml.add_argument("--output", type=Path, default=Path("local_data/ml_rebalance_backtest.json"))
    ml.add_argument("--feature-days", type=int, default=20)
    ml.add_argument("--forecast-days", type=int, default=5)
    ml.add_argument("--min-train", type=int, default=60)
    ml.add_argument("--rebalance-days", type=int, default=10)
    ml.add_argument("--target-volatility", type=float, default=0.18)
    ml.add_argument("--max-weight", type=float, default=0.70)
    ml.add_argument("--max-exposure", type=float, default=1.0)
    ml.add_argument("--stress-decline", type=float, default=0.30)
    ml.add_argument("--max-stress-loss", type=float, default=0.25)
    ml.add_argument("--max-daily-tail-loss", type=float, default=0.04)
    ml.add_argument("--drawdown-trigger", type=float, default=0.10)
    ml.add_argument("--drawdown-exposure-cap", type=float, default=0.25)
    ml.add_argument("--fee-bps", type=float, default=5.0)
    ml.add_argument("--slippage-bps", type=float, default=5.0)

    tournament = sub.add_parser(
        "ml-model-tournament",
        help="Time-safe Ridge/LightGBM/trailing-vol model competition and offline automatic rebalancing",
    )
    tournament.add_argument("--file", type=Path, required=True)
    tournament.add_argument("--output", type=Path, default=Path("local_data/model_tournament.json"))
    tournament.add_argument("--mode", choices=["champion", "ensemble"], default="champion")
    tournament.add_argument("--feature-days", type=int, default=20)
    tournament.add_argument("--forecast-days", type=int, default=5)
    tournament.add_argument("--min-train", type=int, default=60)
    tournament.add_argument("--rebalance-days", type=int, default=10)
    tournament.add_argument("--validation-window", type=int, default=36)
    tournament.add_argument("--min-validated-rebalances", type=int, default=6)
    tournament.add_argument("--switch-margin", type=float, default=0.02)
    tournament.add_argument("--target-volatility", type=float, default=0.18)
    tournament.add_argument("--max-weight", type=float, default=0.70)
    tournament.add_argument("--max-exposure", type=float, default=1.0)
    tournament.add_argument("--stress-decline", type=float, default=0.30)
    tournament.add_argument("--max-stress-loss", type=float, default=0.25)
    tournament.add_argument("--max-daily-tail-loss", type=float, default=0.04)
    tournament.add_argument("--drawdown-trigger", type=float, default=0.10)
    tournament.add_argument("--drawdown-exposure-cap", type=float, default=0.25)
    tournament.add_argument("--fee-bps", type=float, default=5.0)
    tournament.add_argument("--slippage-bps", type=float, default=5.0)

    native = sub.add_parser(
        "atlas-native-backtest",
        help="Train AtlasPM's OWN gated three-expert dual-head risk/return network and simulate auto rebalances (no broker orders)",
    )
    native.add_argument("--file", type=Path, required=True)
    native.add_argument("--output", type=Path, default=Path("local_data/atlas_native_backtest.json"))
    native.add_argument("--feature-days", type=int, default=20)
    native.add_argument("--forecast-days", type=int, default=5)
    native.add_argument("--min-train", type=int, default=756)
    native.add_argument("--train-window", type=int, default=504)
    native.add_argument("--sample-stride", type=int, default=2)
    native.add_argument("--epochs", type=int, default=40)
    native.add_argument("--architecture", choices=["dual", "risknet", "fusion"], default="dual",
                        help="dual trains risk+return; risknet learns total risk; fusion learns multi-scale total and downside risk")
    native.add_argument("--fusion-allocator-mode",
                        choices=["inverse_risk", "downside", "shrinkage"],
                        default="shrinkage",
                        help="In fusion, isolate plain risk, downside and correlation-aware allocation")
    native.add_argument("--return-head-strength", type=float, default=1.0,
                        help="0 disables return forecasts in allocation; 1 is v0.6.0 setting")
    native.add_argument("--no-trade-band", type=float, default=0.0,
                        help="L1 account-weight gap below which trading may be skipped only if all risk caps still pass")
    native.add_argument("--rebalance-days", type=int, default=21)
    native.add_argument("--target-volatility", type=float, default=0.18)
    native.add_argument("--max-weight", type=float, default=0.70)
    native.add_argument("--max-exposure", type=float, default=1.0)
    native.add_argument("--stress-decline", type=float, default=0.30)
    native.add_argument("--max-stress-loss", type=float, default=0.25)
    native.add_argument("--max-daily-tail-loss", type=float, default=0.04)
    native.add_argument("--drawdown-trigger", type=float, default=0.10)
    native.add_argument("--drawdown-exposure-cap", type=float, default=0.25)
    native.add_argument("--fee-bps", type=float, default=5.0)
    native.add_argument("--slippage-bps", type=float, default=5.0)

    audit = sub.add_parser(
        "atlas-native-audit",
        help="RiskNet-vs-dual-vs-naive paired historical robustness audit (no training or orders)",
    )
    audit.add_argument("--risknet-report", type=Path, required=True)
    audit.add_argument("--dual-report", type=Path, required=True)
    audit.add_argument("--output", type=Path, default=Path("local_data/atlas_risknet_audit.json"))
    audit.add_argument("--block-days", type=int, default=21)
    audit.add_argument("--bootstrap-reps", type=int, default=500)

    alpha_gate = sub.add_parser(
        "stock-alpha-gate",
        help="Build rank-percentile allowlist for monthly quant research, never order permission",
    )
    alpha_gate.add_argument("--monthly-report", type=Path, required=True)
    alpha_gate.add_argument("--top-percent", type=float, default=10.0)
    alpha_gate.add_argument("--max-ranked-names", type=int, default=50)
    alpha_gate.add_argument("--minimum-scored-names", type=int, default=500)
    alpha_gate.add_argument("--minimum-listing-coverage-pct", type=float, default=90)
    alpha_gate.add_argument("--maximum-model-age-days", type=int, default=180)
    alpha_gate.add_argument("--output", type=Path, required=True)
    alpha_gate.add_argument("--overwrite", action="store_true")

    alpha_check = sub.add_parser(
        "stock-alpha-check",
        help="Check hypothetical NEW/INCREASE or REDUCE/CLOSE against alpha universe",
    )
    alpha_check.add_argument("--gate", type=Path, required=True)
    alpha_check.add_argument("--symbol", required=True)
    alpha_check.add_argument("--action", required=True)
    alpha_check.add_argument("--decision-date", required=True)

    monthly = sub.add_parser(
        "stock-ai-monthly",
        help="Produce calendar-correct NYSE month-end pure AI stock research; no orders",
    )
    monthly.add_argument("--prices", type=Path, required=True)
    monthly.add_argument("--benchmark-prices", type=Path, required=True)
    monthly.add_argument("--model", type=Path, required=True)
    monthly.add_argument("--month", required=True, help="Calendar month YYYY-MM")
    monthly.add_argument("--universe", type=Path, default=None,
                         help="Optional listing manifest for real full-market coverage audit")
    monthly.add_argument("--benchmark-symbol", default="SPY")
    monthly.add_argument("--min-scored", type=int, default=20)
    monthly.add_argument("--min-market-coverage-pct", type=float, default=90)
    monthly.add_argument("--max-training-age-days", type=int, default=365)
    monthly.add_argument("--output-dir", type=Path,
                         default=Path("local_data/monthly_stock_research"))
    monthly.add_argument("--overwrite", action="store_true",
                         help="Explicitly replace previous month's research snapshot")

    ai_train_v2 = sub.add_parser(
        "stock-ai-train-v2",
        help="Train a pure stock learning-to-rank LightGBM rank_xendcg model",
    )
    ai_train_v2.add_argument("--prices", type=Path, required=True)
    ai_train_v2.add_argument("--benchmark-prices", type=Path, required=True)
    ai_train_v2.add_argument("--start", default="2000-01-01")
    ai_train_v2.add_argument("--train-end", default="2018-12-31")
    ai_train_v2.add_argument("--validation-end", default="2022-12-30")
    ai_train_v2.add_argument("--test-end", default="2026-11-20")
    ai_train_v2.add_argument("--benchmark-symbol", default="SPY")
    ai_train_v2.add_argument("--horizon-sessions", type=int, default=21)
    ai_train_v2.add_argument("--sample-every-sessions", type=int, default=21)
    ai_train_v2.add_argument("--num-boost-round", type=int, default=300)
    ai_train_v2.add_argument("--top", type=int, default=10)
    ai_train_v2.add_argument("--model-output", type=Path,
                             default=Path("local_data/atlas_stock_ranker_v2.txt"))
    ai_train_v2.add_argument("--report", type=Path,
                             default=Path("local_data/atlas_stock_ranker_v2_report.json"))

    ai_rank_v2 = sub.add_parser(
        "stock-ai-rank-v2",
        help="Rank stocks with an ordinal learning-to-rank model, no orders",
    )
    ai_rank_v2.add_argument("--prices", type=Path, required=True)
    ai_rank_v2.add_argument("--benchmark-prices", type=Path, required=True)
    ai_rank_v2.add_argument("--model", type=Path, required=True)
    ai_rank_v2.add_argument("--as-of", required=True)
    ai_rank_v2.add_argument("--top", type=int, default=20)
    ai_rank_v2.add_argument("--benchmark-symbol", default="SPY")
    ai_rank_v2.add_argument("--output", type=Path,
                            default=Path("local_data/atlas_stock_ranker_v2_rankings.json"))

    ai_train = sub.add_parser(
        "stock-ai-train",
        help="Train a price-based LightGBM alpha ranker on purged chronological market splits",
    )
    ai_train.add_argument("--prices", type=Path, required=True)
    ai_train.add_argument("--benchmark-prices", type=Path, required=True)
    ai_train.add_argument("--start", default="2000-01-01")
    ai_train.add_argument("--train-end", default="2018-12-31")
    ai_train.add_argument("--validation-end", default="2022-12-30")
    ai_train.add_argument("--test-end", default="2026-11-20")
    ai_train.add_argument("--benchmark-symbol", default="SPY")
    ai_train.add_argument("--sample-every-sessions", type=int, default=21)
    ai_train.add_argument("--horizon-sessions", type=int, default=21)
    ai_train.add_argument("--num-boost-round", type=int, default=180)
    ai_train.add_argument("--min-train-rows", type=int, default=300)
    ai_train.add_argument("--top", type=int, default=10)
    ai_train.add_argument("--model-output", type=Path,
                          default=Path("local_data/atlas_ai_stock_lgbm.txt"))
    ai_train.add_argument("--report", type=Path,
                          default=Path("local_data/atlas_ai_stock_train.json"))

    ai_rank = sub.add_parser(
        "stock-ai-rank",
        help="Rank stocks with a locally trained LightGBM alpha ranker; no trades",
    )
    ai_rank.add_argument("--prices", type=Path, required=True)
    ai_rank.add_argument("--benchmark-prices", type=Path, required=True)
    ai_rank.add_argument("--model", type=Path, required=True)
    ai_rank.add_argument("--benchmark-symbol", default="SPY")
    ai_rank.add_argument("--as-of", required=True)
    ai_rank.add_argument("--top", type=int, default=20)
    ai_rank.add_argument("--output", type=Path,
                         default=Path("local_data/atlas_ai_stock_ranking.json"))

    market_universe = sub.add_parser(
        "stock-universe",
        help="Discover current all-US-exchange-listed equity candidates from Nasdaq Trader",
    )
    market_universe.add_argument(
        "--output", type=Path, default=Path("local_data/us_equity_universe.csv")
    )
    market_universe.add_argument(
        "--report", type=Path, default=Path("local_data/us_equity_universe_report.json")
    )
    market_universe.add_argument("--nasdaq-file", type=Path, default=None)
    market_universe.add_argument("--other-file", type=Path, default=None)

    market_audit = sub.add_parser(
        "stock-market-audit",
        help="Audit whole-US-stock quote CSV against current listings and real NYSE sessions",
    )
    market_audit.add_argument("--universe", type=Path, required=True)
    market_audit.add_argument("--prices", type=Path, required=True)
    market_audit.add_argument("--as-of", required=True, help="Actual NYSE session YYYY-MM-DD")
    market_audit.add_argument("--min-history-sessions", type=int, default=253)
    market_audit.add_argument("--recent-sessions", type=int, default=63)
    market_audit.add_argument("--min-recent-coverage-pct", type=float, default=85)
    market_audit.add_argument("--min-month-end-coverage-pct", type=float, default=90)
    market_audit.add_argument("--min-eligible-coverage-pct", type=float, default=75)
    market_audit.add_argument("--min-eligible-names", type=int, default=500)
    market_audit.add_argument("--max-manifest-age-days", type=int, default=7)
    market_audit.add_argument("--output", type=Path,
                              default=Path("local_data/us_market_data_audit.json"))

    market_sync = sub.add_parser(
        "stock-sync-market",
        help="Resume-safe quote download for every symbol in a market universe",
    )
    market_sync.add_argument("--universe", type=Path, required=True)
    market_sync.add_argument("--start", default="2024-01-01")
    market_sync.add_argument("--end", default="2026-11-21")
    market_sync.add_argument(
        "--cache-dir", type=Path, default=Path("local_data/us_equity_cache")
    )
    market_sync.add_argument(
        "--output", type=Path, default=Path("local_data/us_equity_prices.csv")
    )
    market_sync.add_argument(
        "--report", type=Path, default=Path("local_data/us_equity_coverage.json")
    )
    market_sync.add_argument(
        "--max-symbols", type=int, default=0,
        help="0=all uncovered symbols; positive integer=quick trial batch",
    )
    market_sync.add_argument("--workers", type=int, default=2)
    market_sync.add_argument("--attempts", type=int, default=2)

    stock_backtest = sub.add_parser(
        "stock-backtest",
        help="Long-only 2000-2026 walk-forward stock ranking vs adjusted SPY, no orders",
    )
    stock_backtest.add_argument("--prices", type=Path, required=True)
    stock_backtest.add_argument("--benchmark-prices", type=Path, required=True)
    stock_backtest.add_argument("--benchmark-symbol", default="SPY")
    stock_backtest.add_argument("--start", default="2000-01-01")
    stock_backtest.add_argument("--end", default="2026-11-20")
    stock_backtest.add_argument("--top", type=int, default=10)
    stock_backtest.add_argument("--rebalance-days", type=int, default=21)
    stock_backtest.add_argument("--fee-bps", type=float, default=5.0)
    stock_backtest.add_argument("--slippage-bps", type=float, default=5.0)
    stock_backtest.add_argument("--max-volatility", type=float, default=0.80)
    stock_backtest.add_argument("--max-drawdown", type=float, default=0.65)
    stock_backtest.add_argument("--require-uptrend", action="store_true")
    stock_backtest.add_argument(
        "--output", type=Path,
        default=Path("local_data/stock_walkforward_2000_2026.json"),
    )

    stock = sub.add_parser(
        "stock-screen",
        help="Offline point-in-time-capped multi-factor stock ranking; no orders",
    )
    stock.add_argument("--prices", type=Path, required=True,
                       help="Long CSV: date,symbol,adjusted_close")
    stock.add_argument("--as-of", required=True, help="Evaluation date YYYY-MM-DD")
    stock.add_argument("--fundamentals", type=Path, default=None,
                       help="Optional dated fundamental snapshot CSV")
    stock.add_argument("--top", type=int, default=10)
    stock.add_argument("--max-price-age-days", type=int, default=10)
    stock.add_argument("--max-fundamental-age-days", type=int, default=200)
    stock.add_argument("--max-volatility", type=float, default=0.80)
    stock.add_argument("--max-drawdown", type=float, default=0.65)
    stock.add_argument("--min-market-cap", type=float, default=0.0,
                       help="Minimum market_cap_usd; requires --fundamentals")
    stock.add_argument("--require-uptrend", action="store_true")
    stock.add_argument("--max-per-sector", type=int, default=None,
                       help="Max picks per sector; requires --fundamentals")
    stock.add_argument("--output", type=Path,
                       default=Path("local_data/stock_screen.json"))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "stock-alpha-gate":
            source = require_private_output(args.monthly_report)
            destination = require_private_output(args.output)
            if source.suffix.lower() != ".json" or destination.suffix.lower() != ".json":
                raise ValueError("Alpha eligibility input/output must be .json under local_data")
            if source == destination:
                raise ValueError("Alpha gate cannot overwrite monthly input report")
            candidate_gate = build_from_file(
                source, top_percent=args.top_percent,
                max_ranked_names=args.max_ranked_names,
                minimum_scored_names=args.minimum_scored_names,
                minimum_listing_coverage_pct=args.minimum_listing_coverage_pct,
                maximum_model_age_days=args.maximum_model_age_days,
            )
            written = write_eligibility_json(
                candidate_gate, destination, overwrite=args.overwrite
            )
            report = {
                "month": candidate_gate["month"],
                "valid_for_month": candidate_gate["valid_for_month"],
                "eligibility_status": candidate_gate["research_eligibility_status"],
                "eligible_count": candidate_gate["eligible_count"],
                "blocked_reasons": candidate_gate["blocked_reasons"],
                "eligible_symbols": candidate_gate["eligible_symbols"],
                "written": written,
                "live_execution_enabled": False,
                "automated_handoff_approved": False,
            }
        elif args.command == "stock-alpha-check":
            source = require_private_output(args.gate)
            if source.suffix.lower() != ".json":
                raise ValueError("Alpha gate must be private .json")
            candidate_gate = json.loads(source.read_text(encoding="utf-8"))
            report = research_action_check(
                candidate_gate, symbol=args.symbol, action=args.action,
                decision_date=args.decision_date,
            )
        elif args.command == "stock-ai-monthly":
            output_probe = require_private_output(args.output_dir / "_output_probe.json")
            model_path = require_private_output(args.model)
            output_dir = output_probe.parent
            source_paths = {
                args.prices.expanduser().resolve(),
                args.benchmark_prices.expanduser().resolve(),
                model_path,
                model_path.with_suffix(model_path.suffix + ".meta.json"),
            }
            if args.universe is not None:
                source_paths.add(args.universe.expanduser().resolve())
            if any(p == output_dir or p.is_relative_to(output_dir)
                   for p in source_paths):
                raise ValueError("Monthly output directory must not contain input/model files")
            result = monthly_candidates(
                args.prices, args.benchmark_prices, model_path,
                month=args.month, benchmark_symbol=args.benchmark_symbol,
                universe_path=args.universe, min_scored=args.min_scored,
                min_market_coverage_pct=args.min_market_coverage_pct,
                max_training_age_days=args.max_training_age_days,
            )
            artifacts = write_monthly_research(
                result, output_dir, overwrite=args.overwrite
            )
            report = {
                "month": result["month"],
                "as_of_nyse_session": result["as_of_nyse_session"],
                "report_status": result["report_status"],
                "review_reasons": result["review_reasons"],
                "data_quality": result["data_quality"],
                "top20_returned": result["top_20"]["returned"],
                "top50_returned": result["top_50"]["returned"],
                "top20_ambiguous": result["top_20"]["boundary_tied"],
                "top50_ambiguous": result["top_50"]["boundary_tied"],
                "artifacts": artifacts,
                "research_only": True,
                "live_execution_enabled": False,
            }
        elif args.command == "stock-ai-train-v2":
            destination = require_private_output(args.model_output)
            report_path = require_private_output(args.report)
            if destination.suffix.lower() != ".txt" or report_path.suffix.lower() != ".json":
                raise ValueError("V2 ranker model must be private .txt and report private .json")
            metadata = destination.with_suffix(destination.suffix + ".meta.json")
            inputs = {args.prices.expanduser().resolve(), args.benchmark_prices.expanduser().resolve()}
            if len({destination, metadata, report_path}) != 3 or any(
                item in inputs for item in (destination, metadata, report_path)
            ):
                raise ValueError("Ranker outputs must not overwrite model metadata or input data")
            result = train_stock_learning_to_rank(
                args.prices, args.benchmark_prices, destination,
                start=args.start, train_end=args.train_end,
                validation_end=args.validation_end, test_end=args.test_end,
                benchmark_symbol=args.benchmark_symbol,
                horizon_sessions=args.horizon_sessions,
                sample_every_sessions=args.sample_every_sessions,
                num_boost_round=args.num_boost_round, top=args.top,
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = {
                "status": result["status"],
                "model": str(destination.relative_to(Path.cwd().resolve())),
                "report": str(report_path.relative_to(Path.cwd().resolve())),
                "split_counts": result["split_counts"],
                "group_counts": result["group_counts"],
                "validation": result["validation"],
                "test": result["test"],
                "live_execution_enabled": False,
            }
        elif args.command == "stock-ai-rank-v2":
            destination = require_private_output(args.output)
            model_path = require_private_output(args.model)
            if destination.suffix.lower() != ".json":
                raise ValueError("Ranker V2 output must be private .json")
            if destination in {
                args.prices.expanduser().resolve(), args.benchmark_prices.expanduser().resolve(),
                model_path, model_path.with_suffix(model_path.suffix + ".meta.json"),
            }:
                raise ValueError("Ranker output cannot overwrite input data")
            result = rank_stocks_v2(
                args.prices, args.benchmark_prices, model_path,
                as_of=args.as_of, top=args.top, benchmark_symbol=args.benchmark_symbol,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = {
                "as_of": result["as_of"],
                "scored_count": result["scored_count"],
                "selected": result["selected"],
                "ranking_diagnostics": result["ranking_diagnostics"],
                "model_diagnostics": result["model_diagnostics"],
                "written": str(destination.relative_to(Path.cwd().resolve())),
                "live_execution_enabled": False,
            }
        elif args.command == "stock-ai-train":
            model_path = require_private_output(args.model_output)
            report_path = require_private_output(args.report)
            if model_path.suffix.lower() != ".txt" or report_path.suffix.lower() != ".json":
                raise ValueError("AI model needs private local_data .txt and report .json")
            inputs = {args.prices.expanduser().resolve(),
                      args.benchmark_prices.expanduser().resolve()}
            metadata_path = model_path.with_suffix(model_path.suffix + ".meta.json")
            if (len({model_path, metadata_path, report_path}) != 3
                    or any(p in inputs for p in (model_path, metadata_path, report_path))):
                raise ValueError("Model and report must be distinct, cannot overwrite input data")
            result = train_ai_stock_ranker(
                args.prices, args.benchmark_prices, model_path,
                start=args.start, train_end=args.train_end,
                validation_end=args.validation_end, test_end=args.test_end,
                benchmark_symbol=args.benchmark_symbol,
                sample_every_sessions=args.sample_every_sessions,
                horizon_sessions=args.horizon_sessions,
                num_boost_round=args.num_boost_round,
                min_train_rows=args.min_train_rows,
                top=args.top,
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = {
                "status": result["status"],
                "model": str(model_path.relative_to(Path.cwd().resolve())),
                "report": str(report_path.relative_to(Path.cwd().resolve())),
                "split_counts": result["split_counts"],
                "purged_rows": result["purged_rows"],
                "validation": result["validation"],
                "test": result["test"],
                "live_execution_enabled": False,
            }
        elif args.command == "stock-ai-rank":
            destination = require_private_output(args.output)
            model_path = require_private_output(args.model)
            if destination.suffix.lower() != ".json":
                raise ValueError("AI rank output must be private local_data .json")
            inputs = {args.prices.expanduser().resolve(),
                      args.benchmark_prices.expanduser().resolve(),
                      model_path,
                      model_path.with_suffix(model_path.suffix + ".meta.json")}
            if destination in inputs:
                raise ValueError("AI rank output cannot overwrite inputs or model")
            result = rank_with_ai(
                args.prices, args.benchmark_prices, model_path,
                as_of=args.as_of, top=args.top, benchmark_symbol=args.benchmark_symbol,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = {
                "as_of": result["as_of"],
                "universe_count": result["universe_count"],
                "scored_count": result["scored_count"],
                "excluded_count": result["excluded_count"],
                "selected": result["selected"],
                "ranking_diagnostics": result["ranking_diagnostics"],
                "model_diagnostics": result["model_diagnostics"],
                "written": str(destination.relative_to(Path.cwd().resolve())),
                "live_execution_enabled": False,
            }
        elif args.command == "stock-universe":
            destination = require_private_output(args.output)
            report_path = require_private_output(args.report)
            if destination.suffix.lower() != ".csv" or report_path.suffix.lower() != ".json":
                raise ValueError("Universe needs .csv output and .json report under local_data")
            if destination == report_path:
                raise ValueError("Universe CSV and report paths must differ")
            sources = {
                p.expanduser().resolve() for p in (args.nasdaq_file, args.other_file) if p is not None
            }
            if destination in sources or report_path in sources:
                raise ValueError("Refusing to overwrite universe source file")
            report = build_us_listed_universe(
                destination, nasdaq_file=args.nasdaq_file, other_file=args.other_file,
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            report = {
                "listing_count": report["listing_count"],
                "unverified_issue_count": report["unverified_issue_count"],
                "snapshot_utc": report["snapshot_utc"],
                "written": str(destination.relative_to(Path.cwd().resolve())),
                "report": str(report_path.relative_to(Path.cwd().resolve())),
                "live_execution_enabled": False,
                "warning": "Current listings only, NOT historical all stocks.",
            }
        elif args.command == "stock-market-audit":
            destination = require_private_output(args.output)
            source_paths = {
                args.universe.expanduser().resolve(),
                args.prices.expanduser().resolve(),
            }
            if destination.suffix.lower() != ".json":
                raise ValueError("Market audit output must be a private .json")
            if destination in source_paths:
                raise ValueError("Market audit may not overwrite source price/universe files")
            details = audit_current_us_stock_prices(
                universe_path=args.universe,
                prices_path=args.prices,
                as_of=args.as_of,
                min_history_sessions=args.min_history_sessions,
                recent_sessions=args.recent_sessions,
                min_recent_coverage_pct=args.min_recent_coverage_pct,
                min_month_end_coverage_pct=args.min_month_end_coverage_pct,
                min_eligible_coverage_pct=args.min_eligible_coverage_pct,
                min_eligible_names=args.min_eligible_names,
                max_manifest_age_days=args.max_manifest_age_days,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(details, indent=2, ensure_ascii=False, sort_keys=True,
                           allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = {
                "market_data_status": details["market_data_status"],
                "listed_stock_universe_count": details["listed_stock_universe_count"],
                "stocks_with_exact_decision_close": details["stocks_with_exact_decision_close"],
                "decision_close_coverage_pct": details["decision_close_coverage_pct"],
                "eligible_history_and_freshness": details["eligible_history_and_freshness"],
                "review_reasons": details["review_reasons"],
                "written": str(destination.relative_to(Path.cwd().resolve())),
                "research_only": True,
                "live_execution_enabled": False,
            }
        elif args.command == "stock-sync-market":
            destination = require_private_output(args.output)
            report_path = require_private_output(args.report)
            cache_probe = require_private_output(args.cache_dir / "_atlaspm_probe.json")
            cache_dir = cache_probe.parent
            if destination.suffix.lower() != ".csv" or report_path.suffix.lower() != ".json":
                raise ValueError("Quote output needs .csv and report .json under local_data")
            if len({destination, report_path}) != 2:
                raise ValueError("Quote output and report must differ")
            universe_file = args.universe.expanduser().resolve()
            if destination == universe_file or report_path == universe_file:
                raise ValueError("Refusing to overwrite stock universe manifest")
            if destination.is_relative_to(cache_dir) or report_path.is_relative_to(cache_dir):
                raise ValueError("Quote output/report must not be inside ticker cache")
            details = sync_us_market_prices(
                args.universe, cache_dir, destination,
                start=args.start, end=args.end,
                max_symbols=args.max_symbols, workers=args.workers,
                attempts=args.attempts,
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(details, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = {
                "listing_candidates": details["listing_candidates"],
                "covered": details["cached_or_downloaded_symbols"],
                "missing": details["missing_symbols"],
                "coverage_pct": details["coverage_pct"],
                "new_successes": details["new_download_successes"],
                "failed_this_run": details["failed_this_run"],
                "price_rows": details["price_rows"],
                "full_market_coverage": details["full_market_coverage"],
                "written": str(destination.relative_to(Path.cwd().resolve())),
                "report": str(report_path.relative_to(Path.cwd().resolve())),
                "live_execution_enabled": False,
            }
        elif args.command == "stock-backtest":
            destination = require_private_output(args.output)
            if destination.suffix.lower() != ".json":
                raise ValueError("Stock backtest output must be private local_data .json")
            sources = {args.prices.expanduser().resolve(),
                       args.benchmark_prices.expanduser().resolve()}
            if destination in sources:
                raise ValueError("Stock backtest cannot overwrite its inputs")
            result = backtest_stock_screener(
                args.prices, args.benchmark_prices,
                start=args.start, end=args.end,
                benchmark_symbol=args.benchmark_symbol,
                top=args.top, rebalance_days=args.rebalance_days,
                fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
                max_volatility=args.max_volatility,
                max_drawdown=args.max_drawdown,
                require_uptrend=args.require_uptrend,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": "local_data/" + destination.relative_to(
                    (Path.cwd().resolve() / "local_data").resolve()
                ).as_posix(),
                "requested_window": result["requested_window"],
                "actual_window": result["actual_window"],
                "universe_count": result["universe_count_excluding_benchmark"],
                "decision_count": result["decision_count"],
                "executed_rebalances": result["executed_rebalances"],
                "portfolio": result["portfolio"],
                "benchmark": result["benchmark"],
                "equal_weight_universe": result["equal_weight_universe"],
                "relative": result["relative"],
                "live_execution_enabled": False,
                "data_caution": "Surviving ticker universe; NOT survivorship-safe or certified alpha.",
            }
        elif args.command == "stock-screen":
            destination = require_private_output(args.output)
            if destination.suffix.lower() != ".json":
                raise ValueError("Stock screen output must be a private local_data .json file")
            inputs = {args.prices.expanduser().resolve()}
            if args.fundamentals:
                inputs.add(args.fundamentals.expanduser().resolve())
            if destination in inputs:
                raise ValueError("Refusing to overwrite stock screener input")
            result = screen_stocks(
                args.prices,
                args.as_of,
                fundamentals_path=args.fundamentals,
                top=args.top,
                max_price_age_days=args.max_price_age_days,
                max_fundamental_age_days=args.max_fundamental_age_days,
                max_annual_volatility=args.max_volatility,
                max_drawdown=args.max_drawdown,
                minimum_market_cap_usd=args.min_market_cap,
                require_uptrend=args.require_uptrend,
                max_per_sector=args.max_per_sector,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": "local_data/" + destination.relative_to(
                    (Path.cwd().resolve() / "local_data").resolve()
                ).as_posix(),
                "as_of": result["as_of"],
                "mode": result["mode"],
                "universe_count": result["universe_count"],
                "eligible_count": result["eligible_count"],
                "excluded_count": len(result["excluded"]),
                "selected": [
                    {"rank": row["rank"], "symbol": row["symbol"], "score": row["score"]}
                    for row in result["selected"]
                ],
                "live_execution_enabled": False,
            }
        elif args.command == "atlas-native-audit":
            inputs = [
                require_private_output(args.risknet_report),
                require_private_output(args.dual_report),
            ]
            destination = require_private_output(args.output)
            if any(p.suffix.lower() != ".json" for p in [*inputs, destination]):
                raise ValueError("Audit inputs and output must be local_data .json files")
            if inputs[0] == inputs[1] or destination in inputs:
                raise ValueError("Native audit needs distinct reports and cannot overwrite input")
            report_full = audit_atlas_risknet(
                inputs[0], inputs[1],
                block_days=args.block_days,
                iterations=args.bootstrap_reps,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(report_full, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = redacted_audit_console(report_full, args.output)
        elif args.command == "atlas-native-backtest":
            destination = require_private_output(args.output)
            if destination.suffix.lower() != ".json":
                raise ValueError("Native Atlas model output must be private .json")
            if destination == args.file.expanduser().resolve():
                raise ValueError("Cannot overwrite Atlas native training input")
            try:
                result = backtest_atlas_native(
                    args.file,
                    feature_days=args.feature_days,
                    forecast_days=args.forecast_days,
                    minimum_training_samples=args.min_train,
                    training_window=args.train_window,
                    sample_stride=args.sample_stride,
                    training_epochs=args.epochs,
                    architecture=args.architecture,
                    fusion_allocator_mode=args.fusion_allocator_mode,
                    return_head_strength=args.return_head_strength,
                    no_trade_band=args.no_trade_band,
                    rebalance_days=args.rebalance_days,
                    target_annual_volatility=args.target_volatility,
                    maximum_name_weight=args.max_weight,
                    maximum_gross_exposure=args.max_exposure,
                    uniform_stress_decline=args.stress_decline,
                    maximum_uniform_stress_loss=args.max_stress_loss,
                    maximum_daily_tail_loss=args.max_daily_tail_loss,
                    account_drawdown_trigger=args.drawdown_trigger,
                    drawdown_exposure_cap=args.drawdown_exposure_cap,
                    fee_bps=args.fee_bps,
                    slippage_bps=args.slippage_bps,
                )
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                raise ValueError(
                    "Native Atlas model training/backtest failed validation; inspect private historical inputs"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False)+"\n",
                encoding="utf-8",
            )
            report = redacted_atlas_native_console(result, str(args.output))
        elif args.command == "ml-model-tournament":
            destination = require_private_output(args.output)
            if destination.suffix.lower() != ".json":
                raise ValueError("Model tournament output must be a private .json file")
            if destination == args.file.expanduser().resolve():
                raise ValueError("Model tournament cannot overwrite training input")
            try:
                result = backtest_model_tournament(
                    args.file,
                    mode=args.mode,
                    feature_days=args.feature_days,
                    forecast_days=args.forecast_days,
                    minimum_training_samples=args.min_train,
                    rebalance_days=args.rebalance_days,
                    validation_window=args.validation_window,
                    minimum_validated_rebalances=args.min_validated_rebalances,
                    switch_margin=args.switch_margin,
                    target_annual_volatility=args.target_volatility,
                    maximum_name_weight=args.max_weight,
                    maximum_gross_exposure=args.max_exposure,
                    uniform_stress_decline=args.stress_decline,
                    maximum_uniform_stress_loss=args.max_stress_loss,
                    maximum_daily_tail_loss=args.max_daily_tail_loss,
                    account_drawdown_trigger=args.drawdown_trigger,
                    drawdown_exposure_cap=args.drawdown_exposure_cap,
                    fee_bps=args.fee_bps,
                    slippage_bps=args.slippage_bps,
                )
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                raise ValueError(
                    "Offline model tournament failed validation; inspect private data and optional [ml] dependency"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = redacted_tournament_console(result, str(args.output))
        elif args.command == "ml-rebalance-backtest":
            destination = require_private_output(args.output)
            if destination.suffix.lower() != ".json":
                raise ValueError("ML backtest output must be private .json under local_data/")
            if destination == args.file.expanduser().resolve():
                raise ValueError("Refusing to overwrite historical training input")
            try:
                result = backtest_ml_dynamic_rebalance(
                    args.file,
                    model_family=args.model,
                    feature_days=args.feature_days,
                    forecast_days=args.forecast_days,
                    minimum_training_samples=args.min_train,
                    rebalance_days=args.rebalance_days,
                    target_annual_volatility=args.target_volatility,
                    maximum_name_weight=args.max_weight,
                    maximum_gross_exposure=args.max_exposure,
                    uniform_stress_decline=args.stress_decline,
                    maximum_uniform_stress_loss=args.max_stress_loss,
                    maximum_daily_tail_loss=args.max_daily_tail_loss,
                    account_drawdown_trigger=args.drawdown_trigger,
                    drawdown_exposure_cap=args.drawdown_exposure_cap,
                    fee_bps=args.fee_bps,
                    slippage_bps=args.slippage_bps,
                )
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                raise ValueError(
                    "Offline ML training/rebalancing failed validation; inspect private inputs"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = redacted_ml_console(result, str(args.output))
        elif args.command == "ibkr-dashboard":
            destination = require_private_output(args.output)
            if destination.suffix.lower() != ".html":
                raise ValueError("Local dashboard output must be an .html file")
            base = args.folder.expanduser().resolve()
            project_local_data = (Path.cwd().resolve() / "local_data").resolve()
            if base != project_local_data:
                raise ValueError("Dashboard inputs must come from the project local_data/ folder")
            names = {
                "monitor": "margin_monitor_fixture.json",
                "financing": "ibkr_financing_check.json",
                "stress": "ibkr_margin_stress.json",
                "multiasset": "ibkr_multiasset_budget.json",
                "reverse": "ibkr_reverse_stress.json",
                "previous_audit": "ibkr_risk_audit.json",
                "latest_audit": "ibkr_risk_audit_second.json",
                "audit_diff": "ibkr_risk_audit_diff.json",
            }
            sources = {
                name: require_private_output(base / filename)
                for name, filename in names.items()
            }
            if destination in set(sources.values()):
                raise ValueError("Refusing to overwrite a private dashboard input")
            try:
                loaded = {
                    name: json.loads(path.read_text(encoding="utf-8"))
                    for name, path in sources.items()
                }
                reports = {
                    name: loaded[name]
                    for name in ("monitor", "financing", "stress", "multiasset", "reverse")
                }
                view = build_dashboard_view(
                    reports, loaded["previous_audit"], loaded["latest_audit"],
                    loaded["audit_diff"],
                )
                page = render_dashboard_html(view)
            except (OSError, ValueError, TypeError, KeyError):
                raise ValueError(
                    "Local dashboard inputs failed validation; inspect private files"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(page, encoding="utf-8")
            report = redacted_dashboard_console(view, str(args.output))
        elif args.command == "ibkr-audit-diff":
            destination = require_private_output(args.output)
            previous_path = require_private_output(args.previous)
            current_path = require_private_output(args.current)
            if destination in {previous_path, current_path}:
                raise ValueError("Refusing to overwrite an audit comparison input")
            try:
                previous = json.loads(previous_path.read_text(encoding="utf-8"))
                current = json.loads(current_path.read_text(encoding="utf-8"))
                result = compare_ibkr_risk_audits(previous, current)
            except (ValueError, TypeError, KeyError, OSError):
                raise ValueError(
                    "Private audit comparison failed validation; inspect local files only"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = redacted_audit_diff_summary(result, str(args.output))
        elif args.command == "ibkr-risk-audit":
            destination = require_private_output(args.output)
            source_paths = {
                "monitor": require_private_output(args.monitor),
                "financing": require_private_output(args.financing),
                "stress": require_private_output(args.stress),
                "multiasset": require_private_output(args.multiasset),
                "reverse": require_private_output(args.reverse),
            }
            baseline_path = require_private_output(args.compare) if args.compare else None
            if destination in set(source_paths.values()) or destination == baseline_path:
                raise ValueError("Refusing to overwrite a private risk audit input")
            try:
                reports = {
                    name: json.loads(path.read_text(encoding="utf-8"))
                    for name, path in source_paths.items()
                }
                previous = (
                    json.loads(baseline_path.read_text(encoding="utf-8"))
                    if baseline_path else None
                )
                result = audit_ibkr_risk_chain(reports, baseline_audit=previous)
            except (OSError, ValueError, KeyError, TypeError):
                raise ValueError(
                    "Local audit reports failed validation; inspect private files only"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            report = redacted_audit_summary(result, str(args.output))
        elif args.command == "ibkr-reverse-stress":
            destination = require_private_output(args.output)
            upstream_path = require_private_output(args.budget)
            try:
                upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
                assumptions = json.loads(args.assumptions.read_text(encoding="utf-8"))
                result = reverse_margin_stress(upstream, assumptions)
            except (OSError, ValueError, KeyError, TypeError):
                raise ValueError(
                    "Local reverse stress inputs are invalid; review only private files"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report = redacted_reverse_summary(result, str(args.output))
        elif args.command == "ibkr-multiasset-budget":
            destination = require_private_output(args.output)
            prior_path = require_private_output(args.stress)
            try:
                prior = json.loads(prior_path.read_text(encoding="utf-8"))
                inputs = json.loads(args.assumptions.read_text(encoding="utf-8"))
                result = analyze_multiasset_risk_budget(prior, inputs)
            except (ValueError, TypeError, KeyError, OSError):
                raise ValueError(
                    "Local multiasset risk inputs failed validation; inspect private files"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report = redacted_multiasset_summary(result, str(args.output))
        elif args.command == "ibkr-stress":
            destination = require_private_output(args.output)
            # Historical monitor/finance reports contain account IDs and balances.
            monitor_source = require_private_output(args.monitor)
            financing_source = require_private_output(args.financing)
            try:
                monitor_payload = json.loads(monitor_source.read_text(encoding="utf-8"))
                finance_payload = json.loads(financing_source.read_text(encoding="utf-8"))
                assumptions = json.loads(args.scenarios.read_text(encoding="utf-8"))
                result = run_margin_stress(monitor_payload, finance_payload, assumptions)
            except (ValueError, TypeError, KeyError, OSError):
                raise ValueError(
                    "Local margin stress inputs failed validation; inspect only your private files"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = redacted_stress_console(result, str(args.output))
        elif args.command == "ibkr-margin-monitor":
            destination = require_private_output(args.output)
            if (args.fixture is None) == (not args.allow_local_gateway):
                raise ValueError(
                    "Choose exactly one: --fixture OR --allow-local-gateway"
                )
            try:
                if args.fixture is not None:
                    fixture_data = json.loads(args.fixture.read_text(encoding="utf-8"))
                    transport = FixtureGatewayTransport(fixture_data)
                    fixture_mode = True
                else:
                    transport = LocalGatewayTransport(
                        args.url, ca_file=str(args.ca_file) if args.ca_file else None
                    )
                    fixture_mode = False
                diagnostic = collect_ibkr_margin_monitor(
                    transport, account_id=args.account, fixture_mode=fixture_mode,
                    max_field_age_seconds=args.max_age_seconds,
                    min_buffer_ratio=args.min_buffer_ratio,
                )
            except (OSError, ValueError, TypeError, KeyError):
                raise ValueError(
                    "IBKR margin monitor failed validation or transport; inspect local setup and data"
                ) from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = redacted_margin_console(diagnostic, str(args.output))
        elif args.command == "ibkr-financing-check":
            destination = require_private_output(args.output)
            try:
                capture_payload = json.loads(args.capture.read_text(encoding="utf-8"))
                mapping_payload = json.loads(args.mapping.read_text(encoding="utf-8"))
                margin_payload = json.loads(args.statement.read_text(encoding="utf-8"))
                diagnostic = reconcile_ibkr_financing_margin(
                    capture_payload, mapping_payload, margin_payload
                )
            except (ValueError, KeyError, TypeError) as exc:
                # Never include raw identifiers, amounts or malformed input in stderr.
                raise ValueError("Private financing data failed validation; inspect local inputs") from None
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = financing_redacted_summary(diagnostic, str(args.output))
        elif args.command == "ibkr-map-check":
            destination = require_private_output(args.output)
            capture_payload = json.loads(args.capture.read_text(encoding="utf-8"))
            mapping_payload = json.loads(args.mapping.read_text(encoding="utf-8"))
            diagnostic = diagnose_ibkr_contract_mapping(
                capture_payload, mapping_payload
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = mapping_redacted_summary(diagnostic, str(args.output))
        elif args.command == "ibkr-readonly":
            if (args.fixture is None) == (not args.allow_local_gateway):
                raise ValueError(
                    "Choose exactly one: --fixture for no-network test OR --allow-local-gateway"
                )
            if args.fixture is not None:
                fixture_data = json.loads(args.fixture.read_text(encoding="utf-8"))
                transport = FixtureGatewayTransport(fixture_data)
                fixture_mode = True
            else:
                transport = LocalGatewayTransport(
                    args.url,
                    ca_file=str(args.ca_file) if args.ca_file else None,
                )
                fixture_mode = False
            destination = require_private_output(args.output)
            captured = collect_ibkr_readonly_account(
                transport, account_id=args.account,
                fixture_mode=fixture_mode,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(captured, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = redacted_console_summary(captured, args.output)
        elif args.command == "broker-reconcile":
            ledger_payload = json.loads(args.ledger.read_text(encoding="utf-8"))
            broker_payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
            report = reconcile_imported_broker_snapshot(
                ledger_payload, broker_payload
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "account_id": report["account_id"],
                "decision": report["decision"],
                "matched_position_count": report["matched_position_count"],
                "missing_in_import": report["missing_in_import"],
                "unexpected_in_import": report["unexpected_in_import"],
                "local_net_liquidation": report["local_net_liquidation"],
                "imported_net_liquidation": report["imported_net_liquidation"],
                "imported_balance_sheet_reconciliation_gap": report[
                    "imported_balance_sheet_reconciliation_gap"
                ],
                "snapshot_age_seconds": report["snapshot_age_seconds"],
                "block_reasons": report["block_reasons"],
                "differences": report["differences"],
                "source_authentication_verified": report["source_authentication_verified"],
                "broker_order_margin_preview_verified": report["broker_order_margin_preview_verified"],
                "live_execution_enabled": report["live_execution_enabled"],
                "warnings": report["warnings"],
            }
        elif args.command == "oms-replay":
            base_payload = json.loads(args.ledger.read_text(encoding="utf-8"))
            plan_payload = json.loads(args.plan.read_text(encoding="utf-8"))
            event_payload = json.loads(args.events.read_text(encoding="utf-8"))
            report = replay_paper_orders(base_payload, plan_payload, event_payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "batch_id": report["batch_id"],
                "account_id": report["account_id"],
                "decision": report["decision"],
                "live_execution_enabled": report["live_execution_enabled"],
                "duplicate_events_ignored": report["duplicate_events_ignored"],
                "unresolved_orders": report["unresolved_orders"],
                "post_fill_risk_breaches": report["post_fill_risk_breaches"],
                "post_fill_risk_snapshot_count": len(report["post_fill_risk_snapshots"]),
                "audit_root_sha256": report["audit_root_sha256"],
                "audit_hash_chain_self_consistent": report["audit_hash_chain_self_consistent"],
                "broker_margin_rechecked_after_fills": report["broker_margin_rechecked_after_fills"],
                "orders": report["orders"],
                "account_replay": {
                    key: value for key, value in report["account_replay"].items()
                    if key != "positions"
                },
                "warnings": report["warnings"],
            }
        elif args.command == "plan-basket":
            base_payload = json.loads(args.ledger.read_text(encoding="utf-8"))
            batch_payload = json.loads(args.plan.read_text(encoding="utf-8"))
            report = propose_multi_asset_batch(base_payload, batch_payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "decision": report["decision"],
                "batch_id": report["batch_id"],
                "account_id": report["account_id"],
                "before": report["before"],
                "pro_forma_unexecuted": {
                    key: value for key, value in report["pro_forma_unexecuted"].items()
                    if key != "positions"
                },
                "leg_risk_verdicts": report["leg_risk_verdicts"],
                "block_reasons": report["block_reasons"],
                "live_execution_enabled": report["live_execution_enabled"],
                "warnings": report["warnings"],
            }
        elif args.command == "plan-equity":
            ledger_payload = json.loads(args.ledger.read_text(encoding="utf-8"))
            plan_payload = json.loads(args.plan.read_text(encoding="utf-8"))
            report = plan_equity_trade(ledger_payload, plan_payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report = {
                "written": str(args.output),
                "decision": report["decision"],
                "risk_state": report["rms"]["risk_state"],
                "client_order_id": report["client_order_id"],
                "account_id": report["account_id"],
                "action": report["action"],
                "symbol": report["symbol"],
                "shares": report["shares"],
                "trade_notional": report["trade_notional"],
                "estimated_fee": report["estimated_fee"],
                "pro_forma_after_unexecuted_trade": report["pro_forma_after_unexecuted_trade"],
                "eligible_for_paper_intent": report["eligible_for_paper_intent"],
                "live_execution_enabled": report["live_execution_enabled"],
                "block_reasons": report["block_reasons"],
                "warnings": report["warnings"],
            }
        elif args.command == "ledger-report":
            payload = json.loads(args.file.read_text(encoding="utf-8"))
            report = build_institutional_ledger(payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "valuation_at": report["valuation_at"],
                "consolidated": report["consolidated"],
                "accounts": [
                    {
                        "account_id": account["account_id"],
                        "account_type": account["account_type"],
                        "net_liquidation_calculated": account["net_liquidation_calculated"],
                        "broker_reported_net_liquidation": account["broker_reported_net_liquidation"],
                        "reconciliation_difference": account["reconciliation_difference"],
                        "account_ledger_ready_for_risk_review": account["account_ledger_ready_for_risk_review"],
                        "alerts": account["alerts"],
                    }
                    for account in report["accounts"]
                ],
                "warnings": report["warnings"],
            }
        elif args.command == "institutional-check":
            payload = json.loads(args.file.read_text(encoding="utf-8"))
            report = assess_institutional_intent(payload)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report["written"] = str(args.output)
        elif args.command == "analyze":
            payload = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Input file must contain an object")
            report = analyze_accounts(
                parse_accounts(payload),
                declines=args.declines,
                annual_margin_rate=args.apr,
            )
        elif args.command == "download-prices":
            report = download_yahoo_adjusted_prices(
                args.symbols, args.start, args.end, args.output, args.overwrite
            )
        elif args.command == "estimate":
            report = estimate_from_prices(args.file, min_observations=args.min_observations)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report = {
                "written": str(args.output),
                "metadata": report["metadata"],
                "next_step": f"atlaspm optimize --file {args.output}",
            }
        elif args.command == "backtest-risk":
            report = backtest_risk_budget(
                args.file,
                lookback_days=args.lookback_days,
                rebalance_days=args.rebalance_days,
                risk_aversion=args.risk_aversion,
                max_weight=args.max_weight,
                max_leverage=args.max_leverage,
                max_annual_volatility=args.max_volatility,
                stress_decline=args.stress_decline,
                max_stress_loss=args.max_stress_loss,
                maintenance_fraction=args.maintenance,
                minimum_maintenance_buffer=args.min_buffer,
                annual_financing_rate=args.apr,
                max_annual_financing_cost=args.max_financing_cost,
                fee_bps=args.fee_bps,
                max_daily_tail_loss=args.max_daily_tail_loss,
                tail_fraction=args.tail_fraction,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "method": report["method"],
                "data": report["data"],
                "strategy": report["strategy"],
                "benchmark": report["benchmark"],
                "warnings": report["warnings"],
            }
        elif args.command == "risk-check":
            assumptions = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(assumptions, dict):
                raise ValueError("Risk input must be a JSON object")
            candidate = optimize_mean_variance(
                assumptions["expected_returns"],
                assumptions["covariance"],
                leverage=args.leverage,
                risk_aversion=args.risk_aversion,
                max_weight=args.max_weight,
                annual_financing_rate=args.apr,
            )
            report = check_risk_budget(
                candidate["weights"], assumptions["covariance"],
                proposed_leverage=args.leverage,
                max_allowed_leverage=args.max_leverage,
                max_name_weight=args.max_name_weight,
                max_annual_volatility=args.max_volatility,
                stress_decline=args.stress_decline,
                max_stress_loss_fraction=args.max_stress_loss,
                maintenance_fraction=args.maintenance,
                min_maintenance_excess_fraction=args.min_buffer,
                annual_financing_rate=args.apr,
                max_annual_financing_cost_fraction=args.max_financing_cost,
            )
            report["input_metadata"] = assumptions.get("metadata")
            report["candidate_optimizer"] = {
                "method": candidate["method"],
                "risk_aversion": candidate["risk_aversion"],
                "expected_net_return": candidate["expected_net_return"],
                "is_trained_ai": False,
                "warning": "Returns and covariance were estimated from history, not future forecasts."
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            report["written"] = str(args.output)
        elif args.command == "benchmark-spy":
            report = compare_spy(args.cash, args.margin, args.spy)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {key: value for key, value in report.items() if key != "spy_curve"}
            diagnostics = report.get("active_risk")
            if isinstance(diagnostics, dict):
                report["active_risk"] = {
                    key: value for key, value in diagnostics.items()
                    if key != "calendar_years"
                }
                report["active_risk"]["annual_breakdown_saved_to_file"] = True
            report["written"] = str(args.output)
        elif args.command == "audit":
            report = audit_backtests(args.cash, args.margin)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "date_range": report["date_range"],
                "observations": report["observations"],
                "cash": {
                    key: value for key, value in report["cash"].items()
                    if key not in {"calendar_year_returns"}
                },
                "margin": {
                    key: value for key, value in report["margin"].items()
                    if key not in {"calendar_year_returns"}
                },
                "benchmark": report["benchmark"],
                "conclusion": report["conclusion"],
            }
        elif args.command == "backtest":
            report = backtest_walk_forward(
                args.file,
                lookback_days=args.lookback_days,
                rebalance_days=args.rebalance_days,
                risk_aversion=args.risk_aversion,
                max_weight=args.max_weight,
                leverage=args.leverage,
                annual_financing_rate=args.apr,
                fee_bps=args.fee_bps,
                maintenance_fraction=args.maintenance,
                margin_policy=args.margin_policy,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            report = {
                "written": str(args.output),
                "data": report["data"],
                "parameters": report["parameters"],
                "strategy": report["strategy"],
                "benchmark": report["benchmark"],
                "warnings": report["warnings"],
            }
        elif args.command == "optimize":
            assumptions = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(assumptions, dict):
                raise ValueError("Input file must be an object")
            report = optimize_mean_variance(
                assumptions["expected_returns"],
                assumptions["covariance"],
                risk_aversion=args.risk_aversion,
                max_weight=args.max_weight,
                leverage=args.leverage,
                annual_financing_rate=args.apr,
            )
        else:
            vols: dict[str, float] = {}
            for item in args.vol:
                symbol, sep, raw = item.partition("=")
                if not sep or not symbol.strip() or symbol in vols:
                    raise ValueError(f"Invalid or duplicate --vol entry: {item}")
                vols[symbol.strip()] = float(raw)
            report = {
                "method": "inverse_volatility_capped_baseline",
                "weights": inverse_volatility_weights(vols, args.max_weight),
                "disclaimer": "Heuristic baseline, not a trained AI or a trade signal.",
            }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"AtlasPM error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
