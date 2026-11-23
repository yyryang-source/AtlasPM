"""quant_models_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.execution.ibkr_gateway import require_private_output
from atlaspm.research.ml_dynamic_rebalance import backtest_ml_dynamic_rebalance, redacted_ml_console
from atlaspm.research.ml_model_tournament import backtest_model_tournament, redacted_tournament_console
from atlaspm.research.atlas_regimenet import backtest_atlas_native, redacted_atlas_native_console
from atlaspm.research.atlas_robustness_audit import audit_atlas_risknet, redacted_audit_console

COMMANDS = frozenset(["ml-rebalance-backtest","ml-model-tournament","atlas-native-backtest","atlas-native-audit"])


def run(args) -> dict:
    if args.command == "atlas-native-audit":
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
    else:
        raise ValueError("Unsupported quant_models_cli command")
    return report


def register_ml_rebalance_backtest(sub) -> None:
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


def register_ml_model_tournament(sub) -> None:
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


def register_atlas_native_backtest(sub) -> None:
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


def register_atlas_native_audit(sub) -> None:
    audit = sub.add_parser(
        "atlas-native-audit",
        help="RiskNet-vs-dual-vs-naive paired historical robustness audit (no training or orders)",
    )
    audit.add_argument("--risknet-report", type=Path, required=True)
    audit.add_argument("--dual-report", type=Path, required=True)
    audit.add_argument("--output", type=Path, default=Path("local_data/atlas_risknet_audit.json"))
    audit.add_argument("--block-days", type=int, default=21)
    audit.add_argument("--bootstrap-reps", type=int, default=500)

