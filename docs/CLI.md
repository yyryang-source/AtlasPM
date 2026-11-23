# AtlasPM CLI layout

The public commands are stable: run `atlaspm <command>` or `python -m atlaspm.cli <command>` from Windows PowerShell.

```text
src/atlaspm/
  cli.py                  # stable error handler, JSON output, interruption semantics
  commands/
    quant.py              # portfolio analysis, backtests and AI risk-model training
    selection.py          # stock screening, model training and monthly alpha research
    broker.py             # IBKR read-only, margin audit and synthetic paper OMS
```

There are **45 existing command names**; this refactor only relocates argument registration and handler bodies. CLI option spellings, output schemas, default paths, error codes (including interrupted `stock-sync-market` exit 130), and fail-closed brokerage permissions remain unchanged.

`atlaspm.cli.download_yahoo_adjusted_prices` remains a compatibility injection point for offline tests. All other command handlers use canonical `atlaspm.quant.*` and `atlaspm.selection.*` modules. The historical top-level Python module aliases remain for external compatibility until a deliberate versioned migration.

## Windows sanity check

```powershell
cd C:\Users\Kevin\Desktop\AtlasPM
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli --help
.\.venv\Scripts\python.exe -m atlaspm.cli stock-ai-train-v2 --help
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-margin-monitor --help
```

Full 5,830-stock datasets and credentials stay local under `local_data/`, not in the Git repository. No live trading is implemented.
