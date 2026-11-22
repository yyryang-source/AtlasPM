# Walk-forward research backtesting (prototype)

This backtester evaluates whether the **rules would have worked on unseen future sessions**, rather than fitting weights once on the entire 2000–2026 period and reading that result as historical performance. It does not train an AI model, connect to a broker, or establish that a strategy is investable.

## Windows PowerShell quick start

From the AtlasPM repository root after `git pull origin main`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli backtest --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.6 --leverage 1 --fee-bps 5 --output local_data\backtest_cash.json
```

To test a **hypothetical** 2× financed account, run a separate scenario (only after the 1× test passes):

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli backtest --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.6 --leverage 2 --apr 0.065 --fee-bps 5 --maintenance 0.30 --output local_data\backtest_margin_2x.json
```

Parameters: `--lookback-days 756` means approximately three years of past daily returns; `--rebalance-days 63` means approximately quarterly; `--max-weight 0.6` restricts each holding to 60% of **gross invested securities**; `--fee-bps 5` means 5 basis points per dollar traded (buys and sells); `--apr` is a constant hypothetical annual borrow rate; `--maintenance 0.3` is an illustrative maintenance fraction, **not** a real broker requirement. `--leverage` is gross risky-asset exposure relative to account equity.

The CLI prints a compact summary and saves the full daily equity curve and rebalance decisions to the JSON output path. `local_data/` is Git-ignored to prevent accidental disclosure.

## Strict time ordering

At the close of session `t-1`, the system produces a signal using strictly known information:
1. Read only the previous `lookback_days` daily-return observations; the final observation ends at `t-1`.
2. Estimate annualized arithmetic returns and sample covariance.
3. Optimize long-only asset weights subject to the max-weight constraint.
4. On the **next session `t`**, execute at a *hypothetical* adjusted close (the signal was already available since `t-1`), with notional-proportional fees.
5. Hold that portfolio through session `t+1`. The first held-out return is the movement from `t` to `t+1`.

This one-session execution delay avoids the unrealistic assumption that a strategy can observe the same closing price and retroactively fill its resulting order at that exact close. Actual market-on-close order execution, spreads and price impact are **still not modeled**.

At the next rebalance, previous asset quantities drift with returns, so there is **no hidden daily rebalancing**. Each rebalance resets the borrowed amount to the requested target gross leverage. Annual borrowing APR is accrued to debt each trading day (`APR / 252`), including between rebalances. There is no cash interest. Maintenance-related forced selling is OFF by default; an optional next-close full-deleveraging stress mode is documented in [MARGIN_SIM.md](MARGIN_SIM.md). It is not an actual broker liquidation model.

## Reported performance

- Ending equity index and total return, starting from equity index 1.0.
- Trading-day annualized compounded return, daily standard-deviation annualized volatility, and zero-risk-free-rate Sharpe-like statistic.
- Maximum drawdown observed on closing-day equity, with starting equity index 1.0 included as the initial high-water mark.
- Number of rebalances, total modeled trading costs, total modeled financing interest, and hypothetical maintenance-breach days.
- Explicit pre-signal training dates and next-session dates for each rebalance.
- Comparison: **equal-weight buy-and-hold of the exact same assets** over the same held-out period, with no borrowing, fees, or rebalancing. This is **not SPY or the S&P 500**; its zero-cost treatment is favorable to the benchmark.

Costs and interest are expressed in **equity-index units**, not USD. Multiply by hypothetical starting capital to obtain USD under linear scaling assumptions.

## Limitations

The downloaded Yahoo-adjusted series can be revised, and the selected stocks were chosen with hindsight; **this is not point-in-time data**. Survivorship and asset-selection bias remain. Dividends/splits are represented only through provider adjustment conventions, not independent corporate-action verification. Trades are assumed possible at the signal close, with no spread, slippage, price impact, taxes, market restrictions or funding friction.

A fixed APR ignores historical interest rates and tiered margin schedules. Maintenance fraction is user-specified and identical across all assets; real broker house requirements are security-specific and can change unexpectedly. By default, the simulator **does not liquidate** even when maintenance is breached. With the optional stress policy, it sells proportionally at the *next* close and then stays unlevered. If simulated equity is exhausted it stops with an error, rather than modeling negative equity or broker recovery.

**Never use these results as a live-trading approval or a claim of achievable returns.** Next work: point-in-time universe, realistic rate curves, out-of-sample parameter selection, independent SPY/QQQ benchmark, slippage and corporate-action audit.
