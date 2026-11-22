# Risk-budget pre-trade research checks (v0.2.5)

AtlasPM can now test a **hypothetical candidate allocation** against explicit concentration, leverage, portfolio volatility, uniform market-loss, illustrative maintenance-margin and borrowing-cost limits. This is a **read-only research tool**. It has **no broker API**, cannot place trades, and does **not** validate safe borrowing capacity.

## Windows PowerShell

From `AtlasPM` after `git pull origin main`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli risk-check --file local_data\estimates.json --leverage 2 --risk-aversion 4 --max-weight 0.6 --max-name-weight 0.6 --max-leverage 2 --max-volatility 0.30 --stress-decline 0.30 --max-stress-loss 0.40 --maintenance 0.30 --min-buffer 0.05 --apr 0.065 --max-financing-cost 0.08 --output local_data\risk_budget_2x.json
```

The command first optimizes the weights using the input expected returns and covariance with the chosen risk aversion and leverage. It then evaluates the **proposed** leveraged allocation against all requested budgets, prints pass/fail details, and writes the full result to `local_data/` (Git-ignored).

To compare a cash-only candidate, change `--leverage 2` to `--leverage 1` and use a separate output path.

## Check definitions

- **Name weight:** maximum single-stock allocation in the long-only risky-asset portfolio (not net portfolio equity).
- **Gross leverage:** total invested risky assets ÷ initial equity, with no cash buffer or short positions.
- **Estimated annualized volatility:** gross leverage × volatility inferred from the input covariance matrix. Historical covariance may not forecast future variance.
- **Uniform stress:** assume each stock immediately falls by the chosen fraction; stressed equity ÷ initial equity equals `1 - leverage × decline`.
- **Maintenance cushion:** for financed cases, stressed equity minus `maintenance × stressed securities`, expressed relative to initial equity. Cash-only cases do **not** require a maintenance deposit.
- **Annual borrowing cost:** `(leverage - 1) × APR`, relative to initial equity. This excludes compounding, rate tiers and fees.

The tool reports a hypothetical leverage upper bound for each budget. For example, with a **30% broad market loss**, a **40% maximum stressed equity loss** permits no more than `0.40 / 0.30 ≈ 1.33×` gross leverage, *even before* applying other constraints.

If a concentration limit is breached, simply reducing leverage does not fix the concentration. If even fully invested 1× equity breaches the requested volatility or stress-loss limits, the report explicitly states that no candidate in the engine's supported **fully invested 1×–4× range** passes all checks. A separate cash-buffer optimizer is future work.

## Limitations and interpretation

The 2000–2026 `estimates.json` is an **in-sample historical estimate**. It contains future knowledge relative to most historical backtest dates. The pre-trade command should therefore **not** be used as historical proof of predictive performance or traded recommendations. The analytical leverage bound does **not** mean a broker will lend that amount; real maintenance and portfolio margin are dynamic, security-specific and can be intraday.

Current checks are **static** and do not automatically rebalance or modify the walk-forward backtest. Future work should integrate past-only covariance budgets directly into the rebalance process, include cash allocations below 1×, and validate risk limits out of sample.

Do not upload local brokerage account data, API credentials, or financial transaction details to a public repository.
