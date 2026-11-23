# AtlasPM CLI: stable commands, organized by domain

There are **45 existing CLI commands**, accessed unchanged through `atlaspm <command>` or `python -m atlaspm.cli <command>`. The public `selection_cli.py`, `quant_cli.py`, `broker_cli.py` files are now small routing facades; all argument parsing and handler code is grouped by functionality.

```text
src/atlaspm/
  cli.py                         # stable JSON/errors, Ctrl+C handling and dispatch
  commands/
    selection_cli.py             # public stock command router
      stock_data_cli.py          # US equity universe, prices and audits
      stock_models_cli.py        # LightGBM V1/V2 models and diagnostics
      stock_rules_cli.py         # 20-signal consensus, screens, backtests
      stock_monthly_cli.py       # monthly candidates and eligibility gate
    quant_cli.py                 # public quant command router
      portfolio_research_cli.py  # allocation, prices, estimation, optimization
      quant_backtesting_cli.py   # backtests, benchmarks, risk checks
      quant_models_cli.py        # native/ML research and robustness audits
    broker_cli.py                # public read-only/synthetic broker router
      broker_accounts_cli.py     # read-only IBKR account and mapping
      broker_risk_cli.py         # margins, stress and risk budgets
      broker_audits_cli.py       # audits and local HTML report
      broker_paper_cli.py        # synthetic ledgers, baskets and paper OMS
```

All 45 command registrations retain their original order, option names and defaults. The specialized command modules import algorithms from `data/`, `research/`, `strategies/equity/`, `risk/`, `execution/`, `monitoring/`, or remaining core `quant/` modules. No new model training, backtest algorithm or order submission has been added by this refactor.

`atlaspm.cli.download_yahoo_adjusted_prices` is preserved as a dependency-injection point for existing tests. All broker execution permissions remain disabled.

## Windows validation

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli --help
.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-train-v2 --help
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-margin-monitor --help
```

The local `local_data/` folder holds uncommitted private market/broker files; none are imported automatically or sent to any service by the CLI routing layer.
