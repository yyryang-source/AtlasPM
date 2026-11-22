# Adaptive risk-budget walk-forward research backtest (v0.3.0)

This mode extends AtlasPM's **existing, immutable baseline simulation**. It uses past-only sample covariance and a user-defined set of risk budgets **at each scheduled rebalance** to scale the risky asset sleeve up or down. Gross exposure can be **below 1×**, with the remainder in cash earning zero interest, or **above 1×** with hypothetical borrowed funds.

It is not trained AI, a verified investment signal or an IBKR liquidation/margin engine.

## PowerShell quick start

After cloning, downloading the same four assets in `local_data\prices.csv`, and checking tests:

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli backtest-risk --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.60 --max-leverage 2 --max-volatility 0.30 --stress-decline 0.30 --max-stress-loss 0.40 --maintenance 0.30 --min-buffer 0.05 --apr 0.065 --max-financing-cost 0.08 --fee-bps 5 --output local_data\backtest_risk_managed.json
```

For a comparison with independent SPY prices already downloaded into `local_data\spy_prices.csv`, use:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli benchmark-spy --cash local_data\backtest_cash.json --margin local_data\backtest_risk_managed.json --spy local_data\spy_prices.csv --output local_data\spy_comparison_risk_managed.json
```

**Read this carefully:** the comparison command's `margin` field means the second strategy case, not that every trading day used borrowed funds. The adaptive strategy may be partially in cash.

## Exactly when decisions are made

For each scheduled rebalance, at session `t-1` close:

1. Use **only** daily return observations from the prior `lookback-days` trading sessions, ending at `t-1`.
2. Compute the annualized sample covariance and mean returns on that rolling training window.
3. Optimize long-only weights of the risky sleeve, subject to `--max-weight`. The composition is selected using the **requested maximum leverage**, prior to risk scaling.
4. Calculate the largest allowed total gross exposure `g` that meets the specified estimated volatility, uniform stress-loss, maintenance-cushion, APR expense, and maximum leverage assumptions. If `g < 1`, maintain a cash sleeve of `1-g`; if `g > 1`, borrow `g-1` times equity.
5. Execute the simulated rebalance **at the next session `t` close** using ideal adjusted-close prices and a proportional turnover fee.
6. Hold through `t+1`, when the first out-of-sample return is realized. Trades happen every `--rebalance-days` trading days, not daily.

Cash/loan/holdings are maintained in separate ledgers and reconciled to portfolio equity. Cash earns no interest, while borrowed debt accrues constant hypothetical APR / 252 each trading day. Between rebalances, holdings drift and leverage can move beyond the selected rebalancing target.

## Risk budget math

Let `g` be risky securities exposure / account equity, `s` the market decline, `v` estimated annual volatility of the fully invested risky sleeve, `m` maintenance rate, and `b` required buffer.

- Annual volatility assumption: `g × v`; limit `<= --max-volatility`.
- Uniform stress loss: `g × s`; limit `<= --max-stress-loss`.
- Cash account `g <= 1`: stress equity `1 - g × s`, with **zero maintenance requirement**. Buffer must be at least `b`.
- Margin account `g > 1`: stressed equity `1 - g × s`; maintenance `m × g × (1-s)`. The difference must be at least `b`.
- Borrowing cost budget: `max(g - 1, 0) × APR <= --max-financing-cost`.

The optimizer reports each selected exposure, allocation, cash percentage, and binding risk inputs in the full `rebalances` JSON. The summary includes minimum/maximum/average selected gross exposure, number of rebalances with cash, modeled borrowing expenses, max drawdown and maintenance violations.

**Example:** with a 30% uniform market crash and maximum account loss of 40%, exposure cannot exceed about 1.33× even if the user requests 2×; other budgets may reduce that further. This is a scenario bound, **not guaranteed crash protection**.

## Important limitations

The 2003–2026 historical period was already inspected during project development, so even if individual trading decisions are strictly past-only, the **strategy settings and chosen stocks can still be optimized with hindsight**. This is not a blind out-of-sample claim of Alpha.

Historical sample volatility is not a future-risk guarantee. Real crashes are unequal across stocks, jumps and liquidity matter, and real broker maintenance levels may change intraday. This simulation does not force liquidations or rebalance continuously when an unscheduled breach occurs. Finance rates are constant, the provider's adjusted closes may be revised, market-on-close fills are idealized, and no tax, bid-ask, market-impact or regulatory limitations are modeled.

If your goal is to quantify incremental economic value, validate on time periods and stock universes *not used to choose the strategy*, compare with cash-funded SPY and matching-risk benchmarks, use more realistic cost histories, and only then consider controlled paper trading.
