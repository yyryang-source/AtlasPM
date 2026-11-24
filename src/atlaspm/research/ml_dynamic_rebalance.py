"""v0.5.2 supervised ML dynamic rebalancing RESEARCH engine (offline only).

Trains expanding-window ridge or optionally native LightGBM *volatility
forecast models* for risky assets; predictions change portfolio weights and
cash allocation. These supervised models are not alpha forecasts. Research
only: no real/paper broker order endpoint, no authenticated risk approval.

WARNING: execution-at-next-close after prior-close signals still ignores
market impact and the inability to guarantee a closing auction fill.
"""

from __future__ import annotations

from math import ceil, exp, isfinite, log, sqrt
from pathlib import Path
from statistics import fmean
from typing import Any

from atlaspm.engine.backtest import _metrics
from atlaspm.data.historical import TRADING_DAYS_PER_YEAR, load_adjusted_prices
from atlaspm.engine.optimizer import inverse_volatility_weights
from atlaspm.risk.risk_backtest import _trade_to_risk_budget

FEATURE_NAMES = (
    "last_daily_return", "mean_last_5", "rms_last_5",
    "rms_past_window", "downside_rms_past_window",
)
RIDGE_REGULARIZATION = 0.10
SIGMA_FLOOR = 0.005
SIGMA_CEILING = 4.0


def _real(value: Any, name: str, low: float | None = None,
          high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be finite numeric data")
    result = float(value)
    if low is not None and result < low:
        raise ValueError(f"{name} below supported minimum")
    if high is not None and result > high:
        raise ValueError(f"{name} above supported maximum")
    return result


def _integer(value: Any, name: str, low: int, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low or (
        high is not None and value > high
    ):
        raise ValueError(f"{name} must be an integer within supported range")
    return value


def _rms(series: list[float]) -> float:
    if not series:
        raise ValueError("Cannot estimate risk from an empty observation window")
    return sqrt(sum(x * x for x in series) / len(series) * TRADING_DAYS_PER_YEAR)


def _features(asset_returns: list[float], at_price_index: int, window: int) -> list[float]:
    """Features observed by close at_price_index; never see later returns."""
    if at_price_index < window or at_price_index > len(asset_returns):
        raise ValueError("Feature point outside past-only price history")
    past = asset_returns[at_price_index - window:at_price_index]
    short = past[-5:]
    downside = [min(0.0, x) for x in past]
    return [
        past[-1],
        fmean(short),
        sqrt(sum(x*x for x in short) / len(short)),
        sqrt(sum(x*x for x in past) / len(past)),
        sqrt(sum(x*x for x in downside) / len(downside)),
    ]


def _solve_positive_ridge(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Deterministic 5x5 pivoted Gaussian solve on positive definite normal equations."""
    n = len(rhs)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda i: abs(aug[i][col]))
        if abs(aug[pivot][col]) < 1e-13:
            raise ValueError("ML ridge covariance matrix cannot be solved")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        divisor = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= divisor
        for i in range(n):
            if i != col:
                factor = aug[i][col]
                for j in range(col, n + 1):
                    aug[i][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def _train_asset_model(
    asset_returns: list[float],
    signal_index: int,
    lookback: int,
    horizon: int,
    minimum_samples: int,
) -> dict[str, Any]:
    """Training label for sample i uses returns i..i+horizon-1.

    Latest eligible i is signal_index-horizon, so its label is fully known
    at signal_index close. No future bar enters either the fit or features.
    """
    end = signal_index - horizon
    training_points = list(range(lookback, end + 1))
    if len(training_points) < minimum_samples:
        raise ValueError("Insufficient fully observed historic ML training labels")
    x = [_features(asset_returns, i, lookback) for i in training_points]
    y = [
        log(max(SIGMA_FLOOR, _rms(asset_returns[i:i+horizon])))
        for i in training_points
    ]
    size = len(FEATURE_NAMES)
    mean_x = [fmean(row[j] for row in x) for j in range(size)]
    std_x = [
        max(1e-8, sqrt(fmean((row[j]-mean_x[j])**2 for row in x)))
        for j in range(size)
    ]
    z = [[(row[j]-mean_x[j])/std_x[j] for j in range(size)] for row in x]
    mean_y = fmean(y)
    normal = [
        [
            fmean(row[i]*row[j] for row in z)
            + (RIDGE_REGULARIZATION if i == j else 0.0)
            for j in range(size)
        ]
        for i in range(size)
    ]
    rhs = [fmean(row[i] * (target - mean_y) for row, target in zip(z, y))
           for i in range(size)]
    coefs = _solve_positive_ridge(normal, rhs)
    if any(not isfinite(c) for c in coefs):
        raise ValueError("Invalid learned ML weights")
    return {
        "model": "trained_regularized_log_future_rms_ridge",
        "training_samples": len(training_points),
        "training_first_signal_index": training_points[0],
        "training_last_signal_index": training_points[-1],
        "label_fully_observed_through_price_index": end + horizon,
        "target": "log_next_horizon_annualized_root_mean_square_daily_return",
        "feature_names": list(FEATURE_NAMES),
        "feature_means": mean_x,
        "feature_stds": std_x,
        "coefficients": coefs,
        "intercept_log_volatility": mean_y,
        "ridge_regularization": RIDGE_REGULARIZATION,
    }


def _forecast(model: dict[str, Any], row: list[float]) -> float:
    z = [(row[i]-model["feature_means"][i]) / model["feature_stds"][i]
         for i in range(len(row))]
    prediction = model["intercept_log_volatility"] + sum(
        a*b for a,b in zip(model["coefficients"], z)
    )
    if not isfinite(prediction):
        raise ValueError("Non-finite ML volatility forecast")
    return min(SIGMA_CEILING, max(SIGMA_FLOOR, exp(max(-20.0, min(5.0, prediction)))))


def _historical_tail_loss(
    returns: list[list[float]],
    weights: list[float],
    fraction: float = 0.05,
) -> float:
    losses = sorted(
        (max(0.0, -sum(w*r for w, r in zip(weights, row))) for row in returns),
        reverse=True,
    )
    if not losses:
        raise ValueError("No training returns for historical tail-loss cap")
    return fmean(losses[:max(1, ceil(len(losses)*fraction))])


def _select_allocation(
    symbols: list[str],
    forecasts: list[float],
    trailing_daily_returns: list[list[float]],
    *,
    maximum_name_weight: float,
    target_annual_volatility: float,
    maximum_gross_exposure: float,
    uniform_stress_decline: float,
    maximum_uniform_stress_loss: float,
    maximum_daily_tail_loss: float,
    account_drawdown: float,
    account_drawdown_trigger: float,
    drawdown_exposure_cap: float,
    sleeve_weights: dict[str, float] | None = None,
    additional_exposure_cap: float = 1.0,
) -> dict[str, Any]:
    # Custom in-house predictors may choose the risky-sleeve composition,
    # but they cannot bypass the same independent risk/exposure guardrails.
    if sleeve_weights is None:
        weights_by_name = inverse_volatility_weights(
            dict(zip(symbols, forecasts)), max_weight=maximum_name_weight,
        )
    else:
        if set(sleeve_weights) != set(symbols):
            raise ValueError("Native sleeve must match all risk forecast symbols")
        weights_by_name = {s: _real(
            sleeve_weights[s], f"sleeve_weight_{s}", 0, maximum_name_weight
        ) for s in symbols}
        if abs(sum(weights_by_name.values())-1.0) > 1e-8:
            raise ValueError("Native sleeve weights must sum to 1")
    additional_exposure_cap = _real(
        additional_exposure_cap, "additional_exposure_cap", 0, 1
    )
    weights = [weights_by_name[s] for s in symbols]
    # Weighted standalone vol is a conservative proxy that does not rely on
    # nonexistent real-time/correlated portfolio covariance estimates.
    conservative_annual_risk = sum(w * v for w, v in zip(weights, forecasts))
    observed_es = _historical_tail_loss(trailing_daily_returns, weights)
    caps = {
        "maximum_cash_account_exposure": maximum_gross_exposure,
        "additional_model_exposure_cap": additional_exposure_cap,
        "forecast_volatility_cap": target_annual_volatility / max(
            SIGMA_FLOOR, conservative_annual_risk
        ),
        "hypothetical_market_stress_cap": (
            maximum_uniform_stress_loss / uniform_stress_decline
            if uniform_stress_decline > 0 else maximum_gross_exposure
        ),
        "past_observed_daily_es_cap": (
            maximum_daily_tail_loss / observed_es
            if observed_es > 0 else maximum_gross_exposure
        ),
    }
    if account_drawdown <= -account_drawdown_trigger:
        caps["current_equity_drawdown_guard"] = drawdown_exposure_cap
    gross = max(0.0, min(caps.values()))
    if gross > 1 + 1e-12:
        raise ValueError("AI cash-account exposure may never exceed 100%")
    return {
        "risky_sleeve_weights": weights_by_name,
        "risky_gross": gross,
        "cash_weight": max(0.0, 1-gross),
        "account_weight_targets": {s: gross*w for s,w in weights_by_name.items()},
        "conservative_forecast_annual_risk": conservative_annual_risk * gross,
        "observed_historical_daily_es_before_scaling": observed_es,
        "binding_caps": sorted(k for k, v in caps.items() if abs(v-gross) < 1e-9),
        "risk_guards": {k: (min(1.0, v) if isfinite(v) else None) for k,v in caps.items()},
    }


def backtest_ml_dynamic_rebalance(
    path: Path,
    *,
    feature_days: int = 20,
    forecast_days: int = 5,
    minimum_training_samples: int = 60,
    rebalance_days: int = 10,
    target_annual_volatility: float = 0.18,
    maximum_name_weight: float = 0.70,
    maximum_gross_exposure: float = 1.0,
    uniform_stress_decline: float = 0.30,
    maximum_uniform_stress_loss: float = 0.25,
    maximum_daily_tail_loss: float = 0.04,
    account_drawdown_trigger: float = 0.10,
    drawdown_exposure_cap: float = 0.25,
    fee_bps: float = 5.0,
    slippage_bps: float = 5.0,
    model_family: str = "ridge",
) -> dict[str, Any]:
    """Walk-forward supervised vol models + simulated rebalances.

    signal at t-1 close => execute at t close => earn t->t+1.
    Trains using only labels ending by the signal close. Optimizer controls
    *risk*, not predicted return. One full session execution lag prevents
    close-price leakage; simulated prices still omit market impact.
    """
    feature_days = _integer(feature_days, "feature_days", 5, 252)
    forecast_days = _integer(forecast_days, "forecast_days", 2, 63)
    minimum_training_samples = _integer(
        minimum_training_samples, "minimum_training_samples", 20, 5000
    )
    rebalance_days = _integer(rebalance_days, "rebalance_days", 1, 252)
    target_annual_volatility = _real(
        target_annual_volatility, "target_annual_volatility", 0.001, 2,
    )
    maximum_name_weight = _real(maximum_name_weight, "maximum_name_weight", .05, 1)
    maximum_gross_exposure = _real(
        maximum_gross_exposure, "maximum_gross_exposure", 0, 1
    )
    uniform_stress_decline = _real(
        uniform_stress_decline, "uniform_stress_decline", 0, 1
    )
    maximum_uniform_stress_loss = _real(
        maximum_uniform_stress_loss, "maximum_uniform_stress_loss", 0, 1
    )
    maximum_daily_tail_loss = _real(
        maximum_daily_tail_loss, "maximum_daily_tail_loss", 0, 1
    )
    account_drawdown_trigger = _real(
        account_drawdown_trigger, "account_drawdown_trigger", 0.001, 1,
    )
    drawdown_exposure_cap = _real(
        drawdown_exposure_cap, "drawdown_exposure_cap", 0, 1
    )
    fee_bps = _real(fee_bps, "fee_bps", 0, 1000)
    slippage_bps = _real(slippage_bps, "slippage_bps", 0, 1000)
    if model_family not in {"ridge", "lightgbm"}:
        raise ValueError("Supported supervised risk models: ridge or lightgbm")
    if model_family == "lightgbm":
        # Do not import the optional dependency for default Ridge use.
        from atlaspm.research.ml_lightgbm import (
            _dependency, train_lightgbm_risk, predict_lightgbm_risk,
        )
        _dependency()

    prices = load_adjusted_prices(path)
    symbols = sorted(prices)
    if not 2 <= len(symbols) <= 12:
        raise ValueError("Train on 2 to 12 risky symbols for capped allocations")
    if len(symbols) * maximum_name_weight < 1 - 1e-12:
        raise ValueError("Maximum name weight infeasible for number of assets")
    dates = sorted(set.intersection(*(set(prices[s]) for s in symbols)))
    first_exec_index = feature_days + forecast_days + minimum_training_samples
    if len(dates) <= first_exec_index + 2:
        raise ValueError("Not enough aligned prices for ML train and held-out simulation")
    price_rows = [[prices[s][d] for s in symbols] for d in dates]
    returns = [
        [price_rows[t+1][j]/price_rows[t][j]-1 for j in range(len(symbols))]
        for t in range(len(price_rows)-1)
    ]
    by_asset = [
        [row[j] for row in returns] for j in range(len(symbols))
    ]
    fee_fraction = (fee_bps + slippage_bps) / 10000.0

    def empty_state() -> dict[str, Any]:
        return {"holdings":[0.0]*len(symbols), "cash":1.0, "equity":1.0,
                "peak":1.0, "returns":[], "curve":[], "total_cost":0.0,
                "turnover":0.0, "rebalance_count":0}
    states = {
        "ai":empty_state(),
        "naive_trailing_vol_risk":empty_state(),
        "equal_weight_buy_and_hold":empty_state(),
    }
    rebalances: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    last_trained_models: dict[str, Any] = {}

    for t in range(first_exec_index, len(dates)-1):
        signal = t - 1
        initial_equities = {k: state["equity"] for k,state in states.items()}
        if (t - first_exec_index) % rebalance_days == 0:
            trained: dict[str, Any] = {}
            ai_sigma: list[float] = []
            naive_sigma: list[float] = []
            for j, symbol in enumerate(symbols):
                if model_family == "lightgbm":
                    model = train_lightgbm_risk(
                        by_asset[j], signal, feature_days,
                        forecast_days, minimum_training_samples,
                    )
                else:
                    model = _train_asset_model(
                        by_asset[j], signal, feature_days,
                        forecast_days, minimum_training_samples,
                    )
                trained[symbol] = model
                features = _features(by_asset[j], signal, feature_days)
                ai_sigma.append(
                    predict_lightgbm_risk(model, features)
                    if model_family == "lightgbm" else _forecast(model, features)
                )
                naive_sigma.append(_rms(by_asset[j][signal-feature_days:signal]))
            last_trained_models = trained
            trailing = returns[signal-feature_days:signal]
            selected: dict[str, Any] = {}
            for label, sigma in (
                ("ai", ai_sigma), ("naive_trailing_vol_risk", naive_sigma),
            ):
                state = states[label]
                drawdown = state["equity"]/state["peak"] - 1
                allocation = _select_allocation(
                    symbols, sigma, trailing,
                    maximum_name_weight=maximum_name_weight,
                    target_annual_volatility=target_annual_volatility,
                    maximum_gross_exposure=maximum_gross_exposure,
                    uniform_stress_decline=uniform_stress_decline,
                    maximum_uniform_stress_loss=maximum_uniform_stress_loss,
                    maximum_daily_tail_loss=maximum_daily_tail_loss,
                    account_drawdown=drawdown,
                    account_drawdown_trigger=account_drawdown_trigger,
                    drawdown_exposure_cap=drawdown_exposure_cap,
                )
                h, cash, loan, costs, turnover = _trade_to_risk_budget(
                    state["holdings"], state["equity"],
                    [allocation["risky_sleeve_weights"][s] for s in symbols],
                    allocation["risky_gross"], fee_fraction,
                )
                if loan != 0:
                    raise ValueError("Unexpected lending in cash-only AI engine")
                state["holdings"],state["cash"]=h,cash
                state["equity"] -= costs
                state["total_cost"] += costs
                state["turnover"] += turnover
                state["rebalance_count"] += 1
                selected[label] = allocation
            if t == first_exec_index:
                bench = states["equal_weight_buy_and_hold"]
                h,cash,loan,cost,turnover = _trade_to_risk_budget(
                    bench["holdings"],bench["equity"],[1/len(symbols)]*len(symbols),
                    1.0,fee_fraction,
                )
                if loan != 0:
                    raise ValueError("Unexpected lending for buy-hold benchmark")
                bench["holdings"],bench["cash"]=h,cash
                bench["equity"] -= cost
                bench["total_cost"] += cost
                bench["turnover"] += turnover
                bench["rebalance_count"] += 1
            for j,symbol in enumerate(symbols):
                if signal + forecast_days <= len(returns):
                    observed = _rms(by_asset[j][signal:signal+forecast_days])
                    predictions.append({
                        "signal_date":dates[signal].isoformat(),
                        "asset":symbol, "prediction_annual_rms":ai_sigma[j],
                        "baseline_prediction_annual_rms":naive_sigma[j],
                        "future_realized_annual_rms":observed,
                    })
            training_last_label_date = dates[signal].isoformat()
            rebalances.append({
                "signal_date":dates[signal].isoformat(),
                "execution_date":dates[t].isoformat(),
                "first_held_return_date":dates[t+1].isoformat(),
                "training_last_label_date":training_last_label_date,
                "most_recent_training_feature_date":dates[
                    signal-forecast_days
                ].isoformat(),
                "training_sample_count_per_asset":trained[symbols[0]]["training_samples"],
                "model": (
                    "trained_lightgbm_log_forward_rms_risk"
                    if model_family == "lightgbm"
                    else "trained_regularized_log_future_rms_ridge"
                ),
                "ai_forecast_annual_rms":dict(zip(symbols, ai_sigma)),
                "naive_forecast_annual_rms":dict(zip(symbols, naive_sigma)),
                "ai_allocation":selected["ai"],
                "naive_allocation":selected["naive_trailing_vol_risk"],
                "execution_cost_bps_assumption":fee_bps+slippage_bps,
                "is_real_or_paper_broker_order":False,
            })
        moves = returns[t]
        for name,state in states.items():
            state["holdings"] = [
                h*(1+r) for h,r in zip(state["holdings"],moves)
            ]
            state["equity"]=sum(state["holdings"])+state["cash"]
            if state["equity"] <= 0 or not isfinite(state["equity"]):
                raise ValueError("Simulated cash portfolio equity depleted")
            if abs(sum(state["holdings"])+state["cash"]-state["equity"])>1e-10:
                raise ValueError("Simulated cash and holdings ledger fails to reconcile")
            state["peak"]=max(state["peak"],state["equity"])
            state["returns"].append(state["equity"]/initial_equities[name]-1)
            state["curve"].append({
                "date":dates[t+1].isoformat(),
                "equity_index":state["equity"],
                "drawdown":state["equity"]/state["peak"]-1,
                "cash_equity_index_units":state["cash"],
                "gross_holdings_equity_index_units":sum(state["holdings"]),
            })

    stats: dict[str, Any] = {}
    for name,state in states.items():
        stats[name] = {
            **_metrics(state["returns"], state["curve"], state["equity"]),
            "rebalance_count":state["rebalance_count"],
            "total_cost_equity_index_units":state["total_cost"],
            "cumulative_traded_notional_over_equity":state["turnover"],
        }
    forecast_count=len(predictions)
    mae_ml=(sum(abs(p["prediction_annual_rms"]-p["future_realized_annual_rms"])
                for p in predictions)/forecast_count) if forecast_count else None
    mae_naive=(sum(abs(p["baseline_prediction_annual_rms"]-p["future_realized_annual_rms"])
                for p in predictions)/forecast_count) if forecast_count else None
    return {
        "method":"atlaspm_trained_ml_dynamic_rebalance_walkforward",
        "status":"OFFLINE_TRAINED_RESEARCH_ONLY",
        "is_trained_machine_learning_model":True,
        "model_family":(
            "supervised_lightgbm_gbdt_log_forward_rms_risk"
            if model_family == "lightgbm"
            else "supervised_ridge_regression_log_forward_rms_risk"
        ),
        "selected_model_family": model_family,
        "is_official_qlib_pipeline": False,
        "is_pretrained_institutional_model": False,
        "trading_mode":"local_historical_cash_account_simulation",
        "data":{
            "symbols":symbols, "shared_price_dates":len(dates),
            "first_price_date":dates[0].isoformat(),
            "last_price_date":dates[-1].isoformat(),
            "first_execution_date":dates[first_exec_index].isoformat(),
            "held_out_trading_sessions":len(states["ai"]["returns"]),
            "feature_days":feature_days, "forecast_days":forecast_days,
            "minimum_training_samples":minimum_training_samples,
            "rebalance_days":rebalance_days,
            "input_price_file":str(path),
            "data_provider_independently_verified":False,
        },
        "policy":{
            "target_annual_volatility":target_annual_volatility,
            "maximum_name_weight":maximum_name_weight,
            "maximum_gross_exposure":maximum_gross_exposure,
            "uniform_stress_decline":uniform_stress_decline,
            "maximum_uniform_stress_loss":maximum_uniform_stress_loss,
            "maximum_daily_tail_loss":maximum_daily_tail_loss,
            "account_drawdown_trigger":account_drawdown_trigger,
            "drawdown_exposure_cap":drawdown_exposure_cap,
            "fee_bps":fee_bps,"slippage_bps":slippage_bps,
        },
        "performance":stats,
        "forecast_validation":{
            "out_of_sample_asset_forecast_count":forecast_count,
            "ml_mean_absolute_error_annual_vol":mae_ml,
            "naive_mean_absolute_error_annual_vol":mae_naive,
            "ml_outperforms_naive_on_observed_oos":(
                mae_ml < mae_naive if forecast_count else False
            ),
            "warning":"All predictions evaluated out-of-sample relative to fit times; overlapping forecast labels are correlated and not independent tests.",
        },
        "final_trained_model_snapshot":last_trained_models,
        "rebalances":rebalances,
        "equity_curves":{name:state["curve"] for name,state in states.items()},
        "model_validated_for_live_trading":False,
        "IBKR_financing_debt_separately_verified":False,
        "ready_for_institutional_risk_gate":False,
        "live_execution_enabled":False,
        "warnings":[
            "The supervised model predicts realized future risk, NOT asset returns, alpha, market direction or guaranteed profit.",
            "LightGBM optional training uses an open-source model family also benchmarked by Microsoft Qlib; it is NOT an official Qlib Alpha158 run or a proprietary pretrained institutional model.",
            "All fitted labels are fully observed by each signal date. The execution is one close after the signal, so no forecast sees the held returns.",
            "This uses unverified adjusted-close history; survivorship bias, vendor revisions, splits, historical availability, real bid-ask spreads and market impact are not authenticated.",
            "No cash yield; no direct U.S. Treasury cashflows or actual IBKR Portfolio Margin rules. Gross holdings are always at most 1x account equity.",
            "Slippage and fees are simplified assumed bps per traded notional; no financing or actual brokerage orders are involved.",
            "Model performance can be inferior to simple trailing-volatility or buy-and-hold benchmarks; one synthetic backtest cannot establish deployability.",
        ],
    }


def redacted_ml_console(result: dict[str, Any], output: str) -> dict[str, Any]:
    # Absolute paths reveal Windows profile names, and JSON-escaped backslashes
    # cannot safely be removed with a raw string replacement. Normalize first.
    # The CLI already requires a destination under local_data/, but keep the
    # summary function fail-closed if used independently in future code.
    private_root = (Path.cwd() / "local_data").resolve()
    target = Path(output).expanduser().resolve()
    try:
        relative_output = target.relative_to(private_root)
    except ValueError:
        raise ValueError("ML summary output must be in local_data/") from None
    forecast = result["forecast_validation"]
    return {
        "written": str(Path("local_data") / relative_output),
        "method":result["method"],
        "status":result["status"],
        "model_family":result["model_family"],
        "asset_count":len(result["data"]["symbols"]),
        "held_out_trading_sessions":result["data"]["held_out_trading_sessions"],
        "rebalances":len(result["rebalances"]),
        "training_samples_last_fit":(
            result["rebalances"][-1]["training_sample_count_per_asset"]
            if result["rebalances"] else 0
        ),
        "out_of_sample_forecast_count":forecast["out_of_sample_asset_forecast_count"],
        "ml_outperforms_naive_on_observed_oos":forecast[
            "ml_outperforms_naive_on_observed_oos"
        ],
        "model_validated_for_live_trading":False,
        "IBKR_financing_debt_separately_verified":False,
        "ready_for_institutional_risk_gate":False,
        "live_execution_enabled":False,
        "warnings":[
            "Full predictions, learned coefficients, asset names and performance stay in private local_data.",
            "Offline cash-account rebalance simulation only. No broker-paper or live orders.",
        ],
    }
