# Optional margin liquidation stress scenario (not a broker simulation)

AtlasPM models maintenance fraction as a user assumption, not the actual real-time requirements of Interactive Brokers or any other brokerage.

## Why it exists

The initial 2003–2026 2× research backtest flagged four days below a hypothetical constant 30% maintenance margin. It continued trading without any forced sales. That is an optimistic simplifying assumption.

The optional `--margin-policy next_close_deleverage_to_1x` mode tests **one explicit hypothetical response**, not an actual broker margin call:

1. At a session close, compare equity with assumed maintenance fraction × current long holdings.
2. If below, record a trigger and **queue** a portfolio-wide proportional reduction for the **following session's close**. The portfolio remains exposed during that session.
3. At that next close, sell down to **1× unlevered exposure**, repay remaining margin debt and apply per-notional trading costs.
4. From then on, the hypothetical account **does not re-borrow** even at scheduled rebalances. This deliberately conservative assumption makes one event affect the entire remaining backtest.
5. Record the trigger and execution dates, debt before/after, and trading fees. If equity is exhausted before execution, the run ends with an error: it does not pretend to settle a bankrupt account.

A breach observed on the execution date is still counted as a pre-liquidation breach, even if post-sale maintenance excess becomes positive.

## Reproducible Windows commands

After `git pull origin main`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli backtest --file local_data\prices.csv --lookback-days 756 --rebalance-days 63 --risk-aversion 4 --max-weight 0.6 --leverage 2 --apr 0.065 --fee-bps 5 --maintenance 0.30 --margin-policy next_close_deleverage_to_1x --output local_data\backtest_margin_forced.json

.\.venv\Scripts\python.exe -m atlaspm.cli audit --cash local_data\backtest_cash.json --margin local_data\backtest_margin_forced.json --output local_data\risk_audit_forced.json
```

To reproduce the earlier idealized no-liquidation margin scenario, omit `--margin-policy` (default `none`).

## What this does **not** do

- It does **not** apply security-specific house maintenance, Portfolio Margin, stress-based risk, intraday enforcement, trading halts, overnight gap liquidation, or real margin loan schedules.
- It does **not** model broker choice of which shares to sell, haircut, spread, market impact, taxes, or human order approval.
- No trade execution or broker connection is implemented.
- 30% maintenance is illustrative only; broker requirements can be higher and change.
- Past simulated results remain biased by hindsight asset selection, adjustment conventions and financing assumptions.

The resulting comparison is a **stress diagnostic**. It is not a promise of safety or a track record that could have been realized.
