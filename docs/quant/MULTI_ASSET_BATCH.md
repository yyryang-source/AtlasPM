# AtlasPM Multi-Asset Batch Planner (v0.4.3)

**Engineering status: synthetic, read-only, no live brokerage connectivity.**

This phase links the institutional ledger, proposed stock reductions/additions, Treasury holdings, long protective puts and financing repayments into a **single account-level, combined pro-forma and deterministic RMS review**. It does NOT submit paper orders or real orders.

## Windows PowerShell

~~~powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli plan-basket --ledger examples\institutional_batch_ledger.json --plan examples\institutional_batch_plan.json --output local_data\institutional_batch_report.json
~~~

The example is fully fictional and uses a **single, reconciled margin account**:
- Sell 10 QQQ shares, recording $2,999 net **unsettled sale receivable** after a $1 fee. These receivables cannot finance this basket before settlement.
- Buy an additional $10,000 face of an **already-owned** Treasury using a hypothetical 97.90 dirty price, costing $9,792 with a $2 fee. Dirty price **already includes accrued interest**. The original broker-reported collateral credit of $90,000 does not increase merely because the bond holding increased.
- Buy one long QQQ $280 strike protective put, 100-share multiplier, at hypothetical $5 premium per share plus $2 fee; premium and fees use $502 settled cash. A long put can have intrinsic payoff under some crashes, but **no such benefit is credited to pass the risk gate** in this version.
- Repay $2,000 margin principal using **settled** cash, not unsettled sale proceeds or hypothetical new borrowing.

The starting synthetic margin account has $16,000 settled cash, $50,000 debt and $123,950 marked net liquidation. The planned outcome at unchanged marks is $3,706 settled cash, $3,499 unsettled receivables, $48,000 debt and $123,945 net liquidation, a $5 modeled cost difference. This is not a real trade or an independently priced quote.

## Controls

The basket planner restricts itself to five leg actions: BUY_EQUITY and SELL_EQUITY (existing long positions only), BUY_TREASURY (expand an existing identified Treasury position), BUY_PUT (new long put only with a held underlying stock), and REPAY_MARGIN. Maximum 20 actions in one proposal.

For every leg it validates instrument identity, amount, price limits versus time-stamped account marks, option quote age, premium and contract multiplier. New puts cannot notionally cover more shares than the remaining long underlying exposure.

The planner computes combined marked portfolio values and a separate independent wealth-change identity based on fees and limit-vs-mark differences. It reports the reconciliation difference; any nonzero material difference **blocks** the whole proposal. The risk gate conservatively **ignores put gains** when computing stressed equity and does not assume an increase in Treasury collateral availability.

Each leg is evaluated by the deterministic institutional gate against the *same combined pro-forma leverage and stress*. Any leg veto vetoes the basket. The supplied **mock** basket preview must match its account ID, batch ID, *exact ordered leg contents*, modeled margin debt and modeled after-trade NLV. Reusing a preview from different legs, or a stale or rejected preview, blocks the proposal.

The preview in this local JSON file is not broker-authenticated. Real IBKR services may not support a single cross-asset combined What-If endpoint; each leg and the resulting account state would have to be checked through authenticated, current brokerage interfaces. The synthetic field does **not** authorize actual execution or margin borrowing.

## Settlement, funding and cash controls

- A sale credits unsettled receivables, not settled cash, and cannot be used to finance the same basket.
- Purchases and loan repayment consume cash that is available after setting aside existing unsettled payables. In a cash account, insufficient settled cash blocks the basket.
- On a margin account, borrowing requires an explicit flag, current user-supplied preview with resulting debt, and normal RMS limits. Repayment plus reborrowing to pay the same debt is blocked.
- A Treasury purchased with brokerage margin is an asset, but its face amount is **not** an independent new credit facility. Collateral report amounts never enter cash or account equity.
- Accounts remain isolated; the cash or margin of a different account cannot be silently pledged, transferred or netted.

## Limitations

No actual transaction journal, T+1 settlement engine, coupon dates, Treasury market data, security master, option chain, Greeks, full hedge payoff scenario engine, margin-tier financing charges, hedge rolls, fill processing, buy-side custody, or live broker adapter is implemented here. Orders and account balances are **not changed**.

The simulation also cannot model whether a multi-leg order executes atomically: a broker may fill some legs while others fail. The production design must include per-leg state, timeouts, duplicate protection, partial-fill accounting, cancellation and reconciliation. Because real margin can change intraday, simulated ALLOW_PAPER_INTENT is **not** permission for live execution.

The safest next phase is a separate event-sourced paper OMS and authenticated broker read-only snapshot adapter, followed by stand-alone Treasury cashflow and realistic option hedging scenario engines. Keep credentials and actual personal brokerage data out of public GitHub.
