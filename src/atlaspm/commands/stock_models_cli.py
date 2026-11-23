"""stock_models_cli: offline AtlasPM command handlers; no live orders.

Focused CLI adapter, not the underlying quantitative strategy/model.
"""

from __future__ import annotations

import json
from pathlib import Path
from atlaspm.execution.ibkr_gateway import require_private_output
from atlaspm.research.ai_stock_ranker import train_ai_stock_ranker, rank_with_ai
from atlaspm.research.ai_stock_ranker_v2 import train_stock_learning_to_rank, rank_stocks_v2
from atlaspm.research.v2_momentum_diagnostics import diagnose_v2_momentum_extremes

COMMANDS = frozenset(["stock-ai-train-v2","stock-ai-diagnose-momentum","stock-ai-rank-v2","stock-ai-train","stock-ai-rank"])


def run(args) -> dict:
    if args.command == "stock-ai-train-v2":
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
            "model_research_review": result["model_research_review"],
            "live_execution_enabled": False,
        }

    elif args.command == "stock-ai-diagnose-momentum":
        destination = require_private_output(args.output)
        if destination.suffix.lower() != ".json":
            raise ValueError("Momentum diagnostics output must be private JSON")
        inputs = {
            args.prices.expanduser().resolve(),
            args.benchmark_prices.expanduser().resolve(),
        }
        if destination in inputs:
            raise ValueError("Diagnostics output must not overwrite source CSV files")
        result = diagnose_v2_momentum_extremes(
            args.prices, args.benchmark_prices,
            start=args.start, train_end=args.train_end,
            validation_end=args.validation_end, test_end=args.test_end,
            benchmark_symbol=args.benchmark_symbol,
            horizon_sessions=args.horizon_sessions,
            sample_every_sessions=args.sample_every_sessions,
            top=args.top,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        report = {
            "status": result["status"],
            "validation": {
                key: value for key, value in result["validation"].items()
                if key != "cohorts"
            },
            "test": {
                key: value for key, value in result["test"].items()
                if key != "cohorts"
            },
            "written": str(destination.relative_to(Path.cwd().resolve())),
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
    else:
        raise ValueError("Unsupported stock_models_cli command")
    return report


def register_stock_ai_train_v2(sub) -> None:
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


def register_stock_ai_diagnose_momentum(sub) -> None:
    diagnostic = sub.add_parser(
        "stock-ai-diagnose-momentum",
        help="Investigate per-date extreme 12-1 momentum Top N failures; no model fitting",
    )
    diagnostic.add_argument("--prices", type=Path, required=True)
    diagnostic.add_argument("--benchmark-prices", type=Path, required=True)
    diagnostic.add_argument("--start", default="2025-01-02")
    diagnostic.add_argument("--train-end", default="2025-12-31")
    diagnostic.add_argument("--validation-end", default="2026-05-29")
    diagnostic.add_argument("--test-end", default="2026-11-20")
    diagnostic.add_argument("--benchmark-symbol", default="SPY")
    diagnostic.add_argument("--horizon-sessions", type=int, default=21)
    diagnostic.add_argument("--sample-every-sessions", type=int, default=21)
    diagnostic.add_argument("--top", type=int, default=10)
    diagnostic.add_argument("--output", type=Path,
                            default=Path("local_data/v2_momentum_diagnostics.json"))


def register_stock_ai_rank_v2(sub) -> None:
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


def register_stock_ai_train(sub) -> None:
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


def register_stock_ai_rank(sub) -> None:
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

