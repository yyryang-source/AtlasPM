# AtlasPM CLI: stable commands, organized by domain

There are **58 top-level CLI commands**, available through `atlaspm <command>` or `python -m atlaspm.cli <command>`. The selection, quant and broker routers register 56 commands. The main entry point also registers `market-db` and `portfolio-engine`.

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
