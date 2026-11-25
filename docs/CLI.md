# AtlasPM CLI: stable commands, organized by domain

There are **62 top-level CLI commands**, available through `atlaspm <command>` or `python -m atlaspm.cli <command>`. The selection, quant and broker routers register 57 commands. The main entry point also registers `market-db`, `portfolio-engine`, `autonomous-alpha`, `architecture` and `lifecycle-review`.

```text
src/atlaspm/
  cli.py                         # stable JSON/errors, Ctrl+C handling and dispatch
  commands/
    selection_cli.py             # public stock command router
      stock_data_cli.py          # US equity universe, prices and audits
      stock_history_cli.py       # Historical membership, exits, filings and comparisons
      stock_models_cli.py        # LightGBM V1/V2 models and diagnostics
      stock_rules_cli.py         # 20-signal consensus, screens, backtests
      stock_monthly_cli.py       # monthly candidates and eligibility gate
    quant_cli.py                 # public quant command router
      portfolio_research_cli.py  # allocation, prices, estimation, optimization
      quant_backtesting_cli.py   # backtests, benchmarks, risk checks
      quant_models_cli.py        # native/ML research and robustness audits
      multiasset_research_cli.py # Option, Treasury and hedge replay
    broker_cli.py                # public read-only/synthetic broker router
      broker_accounts_cli.py     # read-only IBKR account and mapping
      broker_risk_cli.py         # margins, stress and risk budgets
      broker_audits_cli.py       # audits and local HTML report
      broker_paper_cli.py        # synthetic ledgers, baskets and paper OMS
```

The command handlers delegate to the corresponding domain module. The full command inventory and standalone entries are listed in [FUNCTIONS.md](FUNCTIONS.md).

`atlaspm.cli.download_yahoo_adjusted_prices` remains a dependency-injection point for existing tests. Paper submission uses the separate operator-approved workflow described in the [rebalance guide](quant/MARKET_CLOSE_REBALANCE.md).

## Windows validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli --help
.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-train-v2 --help
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-margin-monitor --help
```

The local `local_data/` folder holds uncommitted private market/broker files; none are imported automatically or sent to any service by the CLI routing layer.


## Autonomous Alpha research (stage 4, incremental)

See [Autonomous Alpha Engine](AUTONOMOUS_ALPHA_ENGINE.md) for implemented synthetic research, safety boundaries, CLI and remaining validation. Run `python -m atlaspm.cli autonomous-alpha --file local_data/alpha_request.json`. Production admission and live execution remain disabled.

## Offline foundation and declared lifecycle journal

~~~powershell
& $py -m atlaspm.cli architecture
& $py -m atlaspm.cli lifecycle-review create --review-id review-001
& $py -m atlaspm.cli lifecycle-review inspect --review-id review-001
# Append only with the current revision_sha256 returned by create/inspect:
& $py -m atlaspm.cli lifecycle-review append --review-id review-001 --step mandate --evidence-ref local-review-001 --outcome reviewed --expected-revision SHA256_FROM_INSPECT
~~~

The journal stores only declared review metadata below local_data/runs/lifecycle_reviews.
Use a fresh review ID after a terminal block. No order, source verification,
training permission or real execution is provided by this command.
