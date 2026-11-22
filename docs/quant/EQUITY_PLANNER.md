# Read-only equity add/reduce planner (v0.4.2)

AtlasPM connects the **multi-asset cash/margin ledger** to the deterministic institutional risk gate for an auditable *proposed* BUY or SELL of an **already-held long stock or ETF**. All input is local JSON. No orders, contracts, broker APIs, trading permissions or accounts are modified.

## Run on Windows PowerShell

~~~powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli plan-equity --ledger examples\institutional_ledger.json --plan examples\institutional_equity_plan.json --output local_data\institutional_equity_plan_report.json
~~~

The examples are **entirely synthetic** and contain an invented bank snapshot and What-If preview. The displayed result may be ALLOW_PAPER_INTENT only as a validation of a fictional input contract: **it is never a real broker order authorization**.

## Planner design

1. Read a USD-only, independently reconciled account from the previous ledger interface. Do not permit cross-account collateral netting.
2. Validate proposed order account, side, identifier, share quantity, limit price, estimated fees and an explicit client order ID.
3. Only operate on an existing *long* share position. New symbols, fractional shares, shorts, securities transfers, options execution and Treasury execution are not supported yet.
4. Reject a limit price outside policy's maximum basis-point deviation from the ledger's mark and stale security quotes.
5. Construct a **hypothetical cash/debt/equity calculation** using the proposed limit price, existing instrument mark and estimated fees:
   - BUY: reserve unsettled payables, use remaining settled cash, and only project new borrowing if margin account plus explicit allow_margin_borrowing flag; the independent risk gate still requires a matching broker What-If.
   - SELL: reduce shares, credit net hypothetical cash proceeds, do not implicitly repay margin debt (requires a separate future ledger event).
   - Net liquidation at current marks changes only for modeled fees and any deviation between execution limit and position mark. No claims about actual fills are made.
6. Refuse understated pro-forma exposure or loss metrics: compute a conservative minimum gross marked exposure and a uniform equity/Treasury stress lower bound. These simple figures do not capture option delta or joint tail-risk jumps.
7. A supplied broker snapshot must match the same account ID, ledger net liquidation and debt. A supplied What-If preview must be bound to the **exact** client ID, account ID, symbol, side, shares, price and projected financing debt. Otherwise the proposed intent is blocked.
8. Call the existing institutional risk gate with reconciled account, observed stress limits, a fresh broker snapshot and that bound What-If preview. Any ledger or RMS blocker makes the final decision BLOCK. CRITICAL/HALT requires operator review, not automated risk-reducing submissions.
9. Store the full validation and hypothetical post-trade state locally; terminal printing stays concise. **No post-trade state is written back to the account ledger**, and no broker order state exists.

## Synthetic example arithmetic

- Pre-trade fictional cash account NLV: $45,525, settled cash $25,000, unsettled payable $100, XYZ 100 shares at $200.
- Hypothetical BUY 10 XYZ shares at a $200 limit, with an assumed $2 fee.
- Pro-forma XYZ 110 shares, settled cash $22,998, borrowing $0, and marked NLV $45,523.
- The $90,000 reported Treasury collateral in the *other margin account* cannot be spent, treated as cash, or silently pledged to this cash account.

The plan's "broker_preview", timestamps, margin amounts and risk figures are fictional. Real broker previews must be retrieved through a separately authenticated read-only adapter and tied to the same trade; simply copying this JSON is never evidence of an executable preview.

## Limitations

This is not yet a complete rebalancer, order router or transaction journal. The full institution-level implementation must still handle: multiple simultaneous legs; target allocation sizing; best execution, lot size and liquidity; dividend/split and unsettled-cash dynamics; financing tiers; margin interest; Treasury coupons, haircuts and actual collateral eligibility; option chain and Greeks; order idempotency, partial fills, cancellation, state reconciliation, alerts and kill switches.

A permission to model a paper intent is **not** confirmation of investment suitability, available broker margin, or real execution rights. Leave live trading disabled. Never store actual account identifiers, authentication tokens, current broker snapshots, or investor personal data in a public GitHub repository.
