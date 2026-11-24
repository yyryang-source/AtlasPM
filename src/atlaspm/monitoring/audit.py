"""Audit saved research backtests without uploading the user's local data."""

from __future__ import annotations

from atlaspm.engine.contracts import finite_number

import json
from datetime import date
from math import isclose
from pathlib import Path
from typing import Any


def _number(value: object, label: str) -> float:
    return finite_number(value, label,
    invalid_message=f"{label} must be a finite number",
    nonfinite_message=f"{label} must be finite")

def _load(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("equity_curve"), list):
        raise ValueError(f"Not an AtlasPM backtest JSON file: {path}")
    if not raw["equity_curve"]:
        raise ValueError(f"Empty equity curve: {path}")
    for name in ("data", "parameters", "strategy", "benchmark"):
        if not isinstance(raw.get(name), dict):
            raise ValueError(f"Missing {name} in {path}")
    dates: list[str] = []
    for point in raw["equity_curve"]:
        if not isinstance(point, dict):
            raise ValueError("Invalid equity-curve point")
        day = point.get("date")
        if not isinstance(day, str):
            raise ValueError("Missing date in equity curve")
        date.fromisoformat(day)
        dates.append(day)
        for field in ("equity_index", "benchmark_equity_index", "drawdown"):
            _number(point.get(field), field)
    if sorted(set(dates)) != dates:
        raise ValueError(f"Equity curve has duplicate or unsorted dates in {path}")
    if len(dates) != raw["data"].get("backtest_daily_observations"):
        raise ValueError("Equity curve does not match daily observation count")
    ending = _number(raw["strategy"].get("ending_equity_index"), "ending_equity_index")
    if not isclose(ending, float(raw["equity_curve"][-1]["equity_index"]), abs_tol=1e-8, rel_tol=1e-8):
        raise ValueError("Ending equity index disagrees with equity curve")
    breaches = sum(bool(p.get("below_assumed_maintenance", False)) for p in raw["equity_curve"])
    if breaches != raw["strategy"].get("below_assumed_maintenance_days"):
        raise ValueError("Reported maintenance breaches disagree with equity curve")
    events = raw.get("forced_deleveraging_events", [])
    if not isinstance(events, list):
        raise ValueError("Malformed forced_deleveraging_events")
    event_count = raw["strategy"].get("forced_deleveraging_event_count", len(events))
    if event_count != len(events):
        raise ValueError("Forced deleveraging count does not match event log")
    return raw


def _calendar_year_returns(
    curve: list[dict[str, Any]], field: str
) -> list[dict[str, Any]]:
    """Year-end to year-end returns; first year may be partial."""
    closes: dict[int, float] = {}
    starts: dict[int, str] = {}
    for point in curve:
        day = point["date"]
        year = date.fromisoformat(day).year
        closes[year] = _number(point[field], field)
        starts.setdefault(year, day)
    previous = 1.0
    result: list[dict[str, Any]] = []
    for year, year_end in sorted(closes.items()):
        result.append({
            "year": year,
            "first_observed_date": starts[year],
            "year_end_equity_index": year_end,
            "return": year_end / previous - 1 if previous > 0 else None,
            "partial_start_year": year == min(closes),
        })
        previous = year_end
    return result


def _view(source: dict[str, Any]) -> dict[str, Any]:
    strategy = source["strategy"]
    benchmark = source["benchmark"]
    curve = source["equity_curve"]
    violated_dates = [
        p["date"] for p in curve if p.get("below_assumed_maintenance", False)
    ]
    yearly = _calendar_year_returns(curve, "equity_index")
    worst = sorted(
        (r for r in yearly if r["return"] is not None),
        key=lambda r: r["return"]
    )[:5]
    strategy_cagr = _number(strategy.get("annualized_compounded_return"), "CAGR")
    bench_cagr = _number(benchmark.get("annualized_compounded_return"), "benchmark CAGR")
    drawdown = _number(strategy.get("max_drawdown"), "max_drawdown")
    return {
        "parameters": {
            "target_leverage": source["parameters"].get("target_leverage"),
            "lookback_trading_days": source["parameters"].get("lookback_trading_days"),
            "rebalance_every_trading_days": source["parameters"].get("rebalance_every_trading_days"),
            "margin_policy": source["parameters"].get("margin_policy", "none"),
        },
        "annualized_compounded_return": strategy_cagr,
        "benchmark_compounded_return": bench_cagr,
        "cagr_excess_vs_equal_weight": strategy_cagr - bench_cagr,
        "annualized_volatility": _number(strategy.get("annualized_volatility"), "volatility"),
        "maximum_drawdown": drawdown,
        "sharpe_zero_risk_free_assumption": strategy.get("sharpe_zero_risk_free_assumption"),
        "ending_equity_index": _number(strategy.get("ending_equity_index"), "ending_equity_index"),
        "maintenance_breach_dates": violated_dates,
        "maintenance_breach_days": len(violated_dates),
        "forced_deleveraging_events": source.get("forced_deleveraging_events", []),
        "forced_deleveraging_event_count": len(source.get("forced_deleveraging_events", [])),
        "worst_five_calendar_years": worst,
        "calendar_year_returns": yearly,
        "research_flags": {
            "margin_breach_detected": bool(violated_dates),
            "modeled_forced_deleveraging": bool(source.get("forced_deleveraging_events", [])),
            "drawdown_exceeds_50_percent": drawdown <= -0.50,
            "underperformed_equal_weight_cagr": strategy_cagr < bench_cagr,
            "live_trading_validated": False,
        },
    }


def audit_backtests(cash_path: Path, margin_path: Path) -> dict[str, Any]:
    cash = _load(cash_path)
    margin = _load(margin_path)
    for key in ("symbols", "first_backtest_return_date", "last_backtest_return_date"):
        if cash["data"].get(key) != margin["data"].get(key):
            raise ValueError(f"Backtests must match on {key}")
    cash_dates = [p["date"] for p in cash["equity_curve"]]
    margin_dates = [p["date"] for p in margin["equity_curve"]]
    if cash_dates != margin_dates:
        raise ValueError("Backtest equity curves must have identical date grids")
    if cash["parameters"].get("target_leverage") != 1:
        raise ValueError("Cash comparison must use target leverage 1.0")
    if _number(margin["parameters"].get("target_leverage"), "margin leverage") <= 1:
        raise ValueError("Margin comparison requires leverage > 1")
    for key in ("lookback_trading_days", "rebalance_every_trading_days", "risk_aversion", "max_weight", "trading_cost_basis_points_each_side"):
        if cash["parameters"].get(key) != margin["parameters"].get(key):
            raise ValueError(f"Cash and margin experiment settings differ: {key}")
    return {
        "title": "AtlasPM historical walk-forward risk audit",
        "comparison_universe": cash["data"].get("symbols"),
        "date_range": [cash_dates[0], cash_dates[-1]],
        "observations": len(cash_dates),
        "cash": _view(cash),
        "margin": _view(margin),
        "benchmark": {
            "type": cash["benchmark"].get("type"),
            "annualized_compounded_return": cash["benchmark"].get("annualized_compounded_return"),
            "max_drawdown": cash["benchmark"].get("max_drawdown"),
            "warning": "Benchmark is selected with hindsight and is not an independent market index.",
        },
        "conclusion": {
            "research_only": True,
            "broker_execution_suitability_established": False,
            "warning": (
                "Maintenance breaches are not subject to real broker liquidation; "
                "leveraged performance is not a realizable brokerage track record. "
                "Historical asset selection, prices and funding assumptions require independent validation."
            ),
        },
    }
