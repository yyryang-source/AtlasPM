# AtlasPM v0.4.9 — IBKR Financing and Margin Cross-Check

**Read-only, offline, and fail-closed.** v0.4.9 accepts a v0.4.7 Gateway capture, a v0.4.8 reviewed conid mapping, and separately supplied account-summary/financing fields. It compares field arithmetic and account/timestamp consistency. It does **not** authenticate manual margin inputs, prove a broker margin loan, authorize use of U.S. Treasury collateral, provide an order-specific What-If, or submit any order.

## Windows PowerShell — complete synthetic test

Run each line as a PowerShell command. Do **not** paste a JSON object directly at a PowerShell prompt.

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_financing*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-readonly --fixture examples\ibkr_gateway_synthetic.json --account DU1234567 --output local_data\ibkr_fixture_capture.json

.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-financing-check --capture local_data\ibkr_fixture_capture.json --mapping examples\ibkr_mapping_synthetic.json --statement examples\ibkr_financing_synthetic.json --output local_data\ibkr_financing_check.json
```

Expected (fictional) redacted console state: `FIELDS_CONSISTENT_REVIEW_REQUIRED`, `check_issues: []`, `IBKR_financing_debt_separately_verified: false`, `IBKR_margin_requirements_independently_verified: false`, `ready_for_institutional_risk_gate: false`, and `live_execution_enabled: false`. The full monetary report stays in Git-ignored `local_data/`. Nothing in the fixtures is evidence of a real IBKR account.

## Manually supplied margin fields

The `--statement` file is a **local, private** JSON object with:

- `source`: `manually_transcribed_broker_summary` or `synthetic_margin_fixture`.
- `account_id`, `currency: USD`, `segment: SECURITIES`, `basis: CURRENT`.
- `as_of_utc`: actual timezone-aware UTC timestamp, plus positive `max_age_seconds` (up to 3600) and `max_capture_skew_seconds` (up to 600).
- `min_excess_liquidity_to_nlv`: operator-supplied risk-review threshold, 0 to 1, *not* broker margin approval.
- `margin_summary` containing numeric `NetLiquidation`, `EquityWithLoanValue`, `InitMarginReq`, `MaintMarginReq`, `AvailableFunds`, `ExcessLiquidity` as current same-segment values.
- `financing` containing **separately copied claims**: `loan_principal_from_statement_usd`, `loan_principal_from_activity_usd`, `accrued_interest_from_statement_usd`.

The synthetic-only `as_of_utc: SAME_AS_SYNTHETIC_CAPTURE` token works **only** when both files self-identify as fixtures. It is not permitted for a non-synthetic Gateway capture. Actual accounts need genuinely observed timestamps and real distinct financing records. The source string is only a label—never an authenticity certificate.

Use matching account, settlement currency, and **same-time securities-segment** readings. We deliberately do not treat a field from a commodities segment or the look-ahead period as if it were current securities equity. Values that are missing, infinite, strings, negative where impossible, or stale fail closed.

## Checks

1. Re-executes the full conid mapping against the **current** raw capture; a stale or incomplete mapping blocks this stage.
2. Checks account, currency, securities segment, time basis, age and capture-to-margin timestamp skew.
3. Verifies IBKR current-value identities with a **USD 1 observational tolerance**: `AvailableFunds ≈ EquityWithLoanValue - InitMarginReq`; `ExcessLiquidity ≈ EquityWithLoanValue - MaintMarginReq`. Both definitions are broker-published for securities accounts; IBKR may apply additional account rules.
4. Compares manually supplied `NetLiquidation` with the capture's reported USD net liquidation and compares two **claimed** financing principal figures to one cent. This comparison alone never verifies a loan.
5. Vetoes negative available funds, nonpositive excess liquidity, buffer below declared policy threshold and cash-account borrowing claims.

**Crucial accounting rule:** The Gateway `USD.settledcash` is *signed cash*, not verified financing principal. Do not convert negative cash into debt or subtract the supplied loan from NLV again. The Gateway `USD.interest` field is an interest **rate**, not accrued debt. The two loan principal fields are user-provided evidence claims that need separate broker-source validation.

The full diagnostic contains sensitive dollar amounts; the console only exposes the masked account suffix, status and reason codes. Output is restricted to `local_data/`. Do not paste or commit real capture, mapping, or statement files.

## Remaining gate conditions (not delivered in v0.4.9)

- Independently authenticated IBKR margin/account summary and financing ledger adapter;
- Settlement/FX/cash-segment reconciliation and verified collateral/loan values;
- Broker-sourced securities-level haircuts and current/overnight margin model;
- Signed order-bound What-If preview, partial-fill re-risk, human review and paper OMS connection.

Until each separately audited prerequisite is completed, **institutional risk gate readiness and live execution remain false**, even when every diagnostic field is internally consistent.

Official IBKR references:
- https://www.interactivebrokers.com/docs/tws-api/doc/accounts/summary
- https://www.interactivebrokers.com/support/faq/content/available-funds-273033990
- https://www.interactivebrokers.com/support/faq/content/difference-between-excess-liquidity-available-28216468
