# Backtest risk audit

The audit reads two **saved local** AtlasPM walk-forward backtest JSON files. It does not request live broker data and does not upload account information.

## Windows PowerShell

From the AtlasPM directory, after running both the 1× and 2× backtests:

```powershell
git pull origin main
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m atlaspm.cli audit --cash local_data\backtest_cash.json --margin local_data\backtest_margin_2x.json --output local_data\risk_audit.json
```

The audit includes any optional modeled forced-deleveraging events and prints a concise risk comparison and saves `local_data\risk_audit.json` with full calendar-year returns. That folder is excluded by `.gitignore`.

## Outputs

- Shared date range, matched symbols and length of each equity curve.
- Cash and margin annualized compounded return, volatility, drawdown, and hypothetical zero-risk-free-rate Sharpe.
- Return difference versus the *same-universe equal-weight buy-and-hold* benchmark, not a market index.
- Exact **simulated** dates on which the simplified maintenance-margin condition was violated.
- Worst five calendar years in the simulation and annual close-to-close calendar returns.
- Explicit warning flags for drawdown beyond 50%, benchmark underperformance, and maintenance breaches. **These flags are research diagnostics, not automatic investment approval.**

The first reported calendar year may be incomplete. Calendar-year figures use the closing equity at the previous year-end as denominator, and an initial equity index of 1 for the first available year.

## Risk restrictions

The audit rejects unequal date grids, different symbols, mismatched backtest settings, incorrect case labels (cash must use 1× leverage; the margin scenario must use more than 1×), inconsistent maintenance counts, or a mismatch between the final equity curve and the reported ending equity.

**A run with maintenance breaches is not an investable 2× track record**: the simulator does not model the broker's actual liquidation actions (even if an optional synthetic deleveraging rule is enabled) or changing house-margin rules. Even runs without a simulated breach are not validated for live trading.

This audit does not solve survivorship bias, vendor-adjustment issues, future information in symbol selection, changing financing rates, taxes, price impact, or execution timing uncertainty. It is a tool for finding problems before moving to more realistic broker simulation and independent benchmark testing.
