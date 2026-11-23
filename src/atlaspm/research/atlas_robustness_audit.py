"""Independent local research diagnostics for AtlasPM native model backtests.

This reads immutable precomputed historical reports, checks that comparisons
are paired on identical dates and risk/cost rules, and computes multi-period
diagnostics and moving-block resampling. It DOES NOT retrain, pick a champion,
optimize parameters, certify a final holdout or execute orders.
"""

from __future__ import annotations

import json
import random
from datetime import date
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean
from typing import Any

TRADING_DAYS_PER_YEAR = 252
CRISIS_WINDOWS = (
    ("dotcom_2000_2002", "2000-03-10", "2002-10-09"),
    ("global_financial_crisis", "2007-10-01", "2009-03-31"),
    ("covid_crash", "2020-02-19", "2020-03-23"),
    ("stock_bond_2022", "2022-01-03", "2022-12-30"),
    ("recent_2021_2026", "2021-01-01", "2026-11-20"),
)

METRIC_KEYS = (
    "target_annual_volatility", "maximum_name_weight",
    "maximum_gross_exposure", "uniform_stress_decline",
    "maximum_uniform_stress_loss", "maximum_daily_tail_loss",
    "account_drawdown_trigger", "drawdown_exposure_cap",
    "fee_bps", "slippage_bps", "no_trade_band_l1_account_weight",
)
DATA_KEYS = (
    "symbols", "first_price_date", "last_price_date",
    "first_execution_date", "held_out_trading_sessions",
    "minimum_training_samples", "actual_train_window_dates",
    "sample_stride", "training_epochs", "feature_days",
    "forecast_days", "rebalance_days",
)


def _number(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v):
        raise ValueError(f"Invalid nonfinite metric {name}")
    return float(v)


def _load(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".json":
        raise ValueError("Only native JSON research reports are supported")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Missing or invalid local native research JSON") from exc
    if not isinstance(obj, dict):
        raise ValueError("Native research report must be a JSON object")
    if (
        obj.get("status") != "OFFLINE_TRAINED_RESEARCH_ONLY"
        or obj.get("live_execution_enabled") is not False
        or obj.get("model_validated_for_live_trading") is not False
        or obj.get("ready_for_institutional_risk_gate") is not False
        or obj.get("IBKR_financing_debt_separately_verified") is not False
    ):
        raise ValueError("Risk audit requires offline research-only reports")
    return obj


def _validated_curves(
    report: dict[str, Any], strategy: str,
) -> tuple[list[str], list[float], list[float]]:
    rows = report["equity_curves"][strategy]
    if not isinstance(rows, list) or len(rows) < 60:
        raise ValueError("At least 60 paired trading sessions required")
    dates: list[str] = []
    daily: list[float] = []
    indexes: list[float] = []
    prev = 1.0
    for row in rows:
        stamp = row["date"]
        if not isinstance(stamp, str) or date.fromisoformat(stamp).isoformat() != stamp:
            raise ValueError("Malformed historic trading session date")
        if dates and stamp <= dates[-1]:
            raise ValueError("Historical dates are duplicated or out of order")
        equity = _number(row["equity_index"], "equity_index")
        if equity <= 0:
            raise ValueError("Nonpositive simulated equity")
        cash = _number(row["cash_equity_index_units"], "cash")
        gross = _number(row["gross_holdings_equity_index_units"], "holdings")
        if cash < -1e-9 or gross < -1e-9 or gross > equity+1e-8:
            raise ValueError("Risk audit refuses leveraged/negative cash ledger")
        if abs(equity-cash-gross) > 1e-8*max(equity,1):
            raise ValueError("Historical holdings and cash do not reconcile")
        dates.append(stamp)
        daily.append(equity/prev-1.0)
        indexes.append(equity)
        prev = equity
    if len(dates) != report["data"]["held_out_trading_sessions"]:
        raise ValueError("Curve session count inconsistent with model output")
    expected_return = _number(report["performance"][strategy]["total_return"],"total_return")
    if abs(indexes[-1]-1-expected_return) > 1e-7:
        raise ValueError("Recorded total return disagrees with equity curve")
    return dates,daily,indexes


def _validate_pair(
    risk: dict[str, Any], dual: dict[str, Any],
) -> dict[str, tuple[list[str], list[float], list[float]]]:
    if risk.get("architecture_choice") != "risknet":
        raise ValueError("First report must contain the true RiskNet v2 architecture")
    if risk.get("return_prediction_head_trained") is not False:
        raise ValueError("RiskNet report still contains a trained return head")
    if dual.get("architecture_choice") not in (None, "dual"):
        raise ValueError("Second report must contain dual-head return-off control")
    if dual["policy"].get("return_head_strength") != 0:
        raise ValueError("Dual control must disable return forecasts in allocation")
    if risk["policy"].get("return_head_strength") != 0:
        raise ValueError("RiskNet risk-only allocation must not have return signal")
    for key in DATA_KEYS:
        if risk["data"].get(key) != dual["data"].get(key):
            raise ValueError(f"Reports have different historical experiment settings: {key}")
    for key in METRIC_KEYS:
        if risk["policy"].get(key) != dual["policy"].get(key):
            raise ValueError(f"Reports have different execution/risk controls: {key}")
    series = {
        "risknet": _validated_curves(risk,"atlas_native"),
        "dual_return_off": _validated_curves(dual,"atlas_native"),
        "traditional": _validated_curves(risk,"naive_trailing_vol_risk"),
        "traditional_dual_report": _validated_curves(
            dual,"naive_trailing_vol_risk"
        ),
    }
    shared = series["risknet"][0]
    for key,v in series.items():
        if v[0] != shared:
            raise ValueError(f"Unpaired strategy trading calendar: {key}")
    for a,b in zip(
        series["traditional"][2],series["traditional_dual_report"][2]
    ):
        if abs(a-b) > 1e-9*max(1.0,abs(a),abs(b)):
            raise ValueError("Traditional benchmark changed between experiments")
    if risk["data"]["first_execution_date"] > shared[0]:
        raise ValueError("First execution is after the first simulated return")
    return {k:v for k,v in series.items() if k != "traditional_dual_report"}


def _stats(returns: list[float]) -> dict[str, Any]:
    if not returns:
        return {"sessions":0,"total_return":None,"annualized_volatility":None,
                "zero_rf_sharpe":None,"max_drawdown":None}
    current=1.0
    peak=1.0
    worst=0.0
    for r in returns:
        current*=1+r
        if current <= 0:
            raise ValueError("Resampled equity nonpositive")
        peak=max(peak,current)
        worst=min(worst,current/peak-1)
    mean=fmean(returns)
    variance=fmean((r-mean)**2 for r in returns)
    ann_vol=sqrt(max(variance,0)*TRADING_DAYS_PER_YEAR)
    return {
        "sessions":len(returns),
        "total_return":current-1,
        "annualized_volatility":ann_vol,
        "zero_rf_sharpe":mean*TRADING_DAYS_PER_YEAR/ann_vol if ann_vol>1e-12 else None,
        "max_drawdown":worst,
    }


def _quantile(sorted_values: list[float], fraction: float) -> float:
    k=fraction*(len(sorted_values)-1)
    low=int(k)
    high=min(len(sorted_values)-1,low+1)
    weight=k-low
    return sorted_values[low]*(1-weight)+sorted_values[high]*weight


def _paired_bootstrap(
    a: list[float], b: list[float], *,
    block_days: int, iterations: int, seed: int,
) -> dict[str, Any]:
    n=len(a)
    if len(b)!=n or n<60:
        raise ValueError("Block resampling requires at least 60 paired daily outcomes")
    if isinstance(block_days,bool) or not isinstance(block_days,int) or not 5<=block_days<=252:
        raise ValueError("Block length must be 5 to 252 sessions")
    if isinstance(iterations,bool) or not isinstance(iterations,int) or not 100<=iterations<=5000:
        raise ValueError("Bootstrap repetitions must be 100 to 5000")
    rng=random.Random(seed)
    return_spreads=[]
    sharpe_spreads=[]
    for _ in range(iterations):
        indexes=[]
        while len(indexes)<n:
            start=rng.randrange(n)
            indexes.extend((start+j)%n for j in range(block_days))
        indexes=indexes[:n]
        ra=[a[i] for i in indexes]
        rb=[b[i] for i in indexes]
        return_spreads.append(
            TRADING_DAYS_PER_YEAR*fmean(x-y for x,y in zip(ra,rb))
        )
        sa=_stats(ra)["zero_rf_sharpe"]
        sb=_stats(rb)["zero_rf_sharpe"]
        if sa is not None and sb is not None:
            sharpe_spreads.append(sa-sb)
    return_spreads.sort()
    sharpe_spreads.sort()
    first=TRADING_DAYS_PER_YEAR*fmean(x-y for x,y in zip(a,b))
    a_sharpe=_stats(a)["zero_rf_sharpe"]
    b_sharpe=_stats(b)["zero_rf_sharpe"]
    return {
        "block_trading_sessions":block_days,
        "resamples":iterations,
        "seed":seed,
        "observed_annualized_arithmetic_daily_return_spread":first,
        "bootstrap_95pct_interval_annualized_arithmetic_spread":[
            _quantile(return_spreads,.025),_quantile(return_spreads,.975)
        ],
        "observed_zero_rf_sharpe_difference": (
            a_sharpe-b_sharpe if a_sharpe is not None and b_sharpe is not None
            else None
        ),
        "bootstrap_95pct_interval_sharpe_difference":(
            [_quantile(sharpe_spreads,.025),_quantile(sharpe_spreads,.975)]
            if sharpe_spreads else None
        ),
        "warning": (
            "EXPLORATORY paired circular moving-block bootstrap only. "
            "This interval assumes approximate block exchangeability and "
            "ignores parameter-selection overfitting and changing market regimes. "
            "It is NOT a valid untouched final test or trading certification."
        ),
    }


def audit_atlas_risknet(
    risknet_report_path: Path, dual_report_path: Path,
    *,
    block_days: int=21,
    iterations: int=500,
) -> dict[str, Any]:
    """Audits identical 2000-2026 research curves; no model retraining."""
    risk=_load(risknet_report_path)
    dual=_load(dual_report_path)
    series=_validate_pair(risk,dual)
    dates=series["risknet"][0]
    diagnostics={}
    for key,(_,daily,_) in series.items():
        yearly={}
        for year in sorted({d[:4] for d in dates}):
            subset=[r for d,r in zip(dates,daily) if d.startswith(year)]
            yearly[year]=_stats(subset)
        crises={}
        for label,start,end in CRISIS_WINDOWS:
            subset=[r for d,r in zip(dates,daily) if start<=d<=end]
            crises[label]={
                **_stats(subset),
                "calendar_start":start,"calendar_end":end,
                "has_at_least_10_observations":len(subset)>=10,
            }
        diagnostics[key]={
            "whole_period":_stats(daily),
            "calendar_years":yearly,
            "predefined_market_windows":crises,
        }
    return {
        "status":"OFFLINE_RESEARCH_AUDIT_NOT_LIVE_VALIDATED",
        "method":"atlaspm_paired_native_robustness_audit",
        "asset_count":len(risk["data"]["symbols"]),
        "paired_trading_sessions":len(dates),
        "first_held_session":dates[0],
        "last_held_session":dates[-1],
        "matched_data_and_risk_policy":True,
        "matched_traditional_benchmark_between_native_reports":True,
        "model_families":{
            "risknet":risk["model_family"],
            "dual_return_off":dual["model_family"],
            "traditional":"naive_trailing_vol_risk",
        },
        "diagnostics":diagnostics,
        "paired_uncertainty":{
            "risknet_minus_traditional":_paired_bootstrap(
                series["risknet"][1],series["traditional"][1],
                block_days=block_days, iterations=iterations,seed=37622
            ),
            "risknet_minus_dual":_paired_bootstrap(
                series["risknet"][1],series["dual_return_off"][1],
                block_days=block_days,iterations=iterations,seed=37623
            ),
        },
        "in_sample_model_selection_caveat": (
            "All 2000-2026 history was already inspected during model "
            "development. Year-by-year and bootstrap diagnostics are "
            "post-selection exploratory; NONE constitutes fresh independent "
            "out-of-sample evidence or statistical proof of alpha."
        ),
        "no_order_execution":True,
        "live_execution_enabled":False,
        "model_validated_for_live_trading":False,
        "ready_for_institutional_risk_gate":False,
        "IBKR_financing_debt_separately_verified":False,
    }


def redacted_audit_console(report: dict[str, Any], output: Path) -> dict[str, Any]:
    private=(Path.cwd()/"local_data").resolve()
    target=output.expanduser().resolve()
    try:
        name=target.relative_to(private)
    except ValueError:
        raise ValueError("Audit report must be written inside local_data/") from None
    return {
        "written":str(Path("local_data")/name),
        "status":report["status"],
        "method":report["method"],
        "paired_trading_sessions":report["paired_trading_sessions"],
        "matched_data_and_risk_policy":True,
        "matched_traditional_benchmark_between_native_reports":True,
        "market_windows_checked":len(CRISIS_WINDOWS),
        "live_execution_enabled":False,
        "model_validated_for_live_trading":False,
        "warnings":[
            "The 2000-2026 history is not a fresh final holdout.",
            "Block-bootstrap intervals reflect historical noise, not model-selection bias or real brokerage trading conditions.",
            "Private annual and crisis-window statistics are saved in ignored local_data only.",
        ],
    }
