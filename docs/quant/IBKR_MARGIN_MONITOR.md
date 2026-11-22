# AtlasPM v0.4.10 — IBKR Read-Only Margin Monitor

**Research / operator review only. No trading endpoints and no paper/live order placement.** This feature obtains an account's GET-only Client Portal portfolio summary, compares timestamps and securities-segment margin figures with a same-session read-only USD ledger, and emits warning codes. Margin checks never confirm actual finance principal and never grant trading permissions.

## Windows PowerShell: synthetic / zero network

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_margin_monitor*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-margin-monitor --fixture examples\ibkr_gateway_synthetic.json --account DU1234567 --output local_data\margin_monitor_fixture.json
```

All account IDs, positions, balances, timestamps, and securities in this fixture are fabricated. The string `SYNTHETIC_NOW` is accepted **only** for synthetic responses so the checked timestamps can be fresh when the test runs. The command does not send a network request and writes its detailed result only to Git-ignored `local_data/`.

Expected synthetic redacted console result: `monitor_state: NORMAL`, no integrity blockers or alerts, `broker_observed_summary_fields: false`, `IBKR_financing_debt_separately_verified: false`, `ready_for_institutional_risk_gate: false`, `live_execution_enabled: false`.

## Optional real IBKR Gateway read (no credential collection)

Only for an account that you control, start and authenticate the **official** IBKR Client Portal Gateway yourself, following [the read-only setup guide](IBKR_READONLY.md). This requires a functioning local Gateway with a certificate trusted by your Python client. AtlasPM deliberately does NOT support disabling TLS certificate verification, scraping login credentials or storing cookie tokens.

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-margin-monitor --allow-local-gateway --account YOUR_OWN_IBKR_ACCOUNT_ID --url https://localhost:5000/v1/api --ca-file C:\path\to\trusted-local-ca.pem --min-buffer-ratio 0.15 --max-age-seconds 600 --output local_data\margin_monitor_private.json
```

Omit `--ca-file` **only** if Windows/Python already trust the Gateway's certificate. Do not paste private JSON, account identifiers, funds amounts, auth cookies, or certificates into ChatGPT, GitHub Issues or PRs. Only share the redacted command output. No real broker access was executed or tested during development.

## Official GET endpoints used

The single read call sequence is:

1. `/iserver/auth/status` — confirm authenticated and connected browser session
2. `/portfolio/accounts` — confirm the exact accessible USD-base account
3. `/portfolio/{accountId}/ledger` — read signed USD cash and reported NLV
4. `/portfolio2/{accountId}/positions` — collect unnormalized contract IDs and positions
5. `/portfolio/{accountId}/summary` — collect securities margin fields for this stage

The portfolio summary uses **lowercase** keys such as `netliquidation`, `netliquidation-s`, `equitywithloanvalue-s`, `initmarginreq-s`, `maintmarginreq-s`, `availablefunds-s` and `excessliquidity-s`. `-s` means securities, while `-c` means commodities. Account summary includes `{amount, isNull, severity, timestamp, value, currency}` objects, where `timestamp` is *epoch milliseconds*. The API's numeric `currency` representation (e.g. `1.1`) must **not** be interpreted as an FX rate or a self-evident USD ISO currency code.

## Monitor validation and priority

- Missing/null/nonfinite/abnormal-severity fields are rejected.
- A valid accountcode must match the selected account.
- A fresh snapshot requires numeric Unix-millisecond timestamps for **all** used margin fields, at most 120 s spread, and no greater than 600 s old (configurable up to 3600). Timestamps may not be in the future beyond five seconds.
- The retrieved account, base currency, and ledger must align. Extra FX ledger currencies and nonzero commodities NLV are unsupported and block use.
- `netliquidation-s` must agree within $1 with both the portfolio-total `netliquidation` (under the single securities-segment assumption) and the captured USD ledger NLV.
- Current securities margin checks use `availablefunds-s ≈ equitywithloanvalue-s - initmarginreq-s` and `excessliquidity-s ≈ equitywithloanvalue-s - maintmarginreq-s`. Rounding tolerance is $1.
- `BLOCKED_DATA`: missing identity/synchronization, stale/mixed-currency or broken accounting bridge.
- `CRITICAL`: current or look-ahead excess liquidity nonpositive, **only when data pass checks**.
- `WATCH`: negative available funds or buffer below your chosen minimum (default 15%), **only when data pass checks**.
- `NORMAL`: no detected alert in the supported fields; it is not a claim of broker approval or robust safety.

All private numeric metrics are saved locally; stdout only includes masked account hint, monitor state and reason codes. A `BLOCKED_DATA` state takes priority because inconsistent numbers cannot support a meaningful liquidation warning.

## Excluded (must not be inferred)

No broker-authenticated loan principal reconciliation, accrued financing interest validation, per-security/UST haircut, FX conversion, margin account product permission, order-specific What-If, overnight/house margin change forecast, or liquidation timing prediction is present. Negative settled cash is **not** verified loan debt; Gateway `USD.interest` is a reported interest rate, not accrued debt. Securities net liquidation should not be double-debited for loan principal.

Even if `monitor_state == NORMAL`, the outputs always include:

```json
{
  "IBKR_financing_debt_separately_verified": false,
  "broker_treasury_collateral_verified": false,
  "IBKR_order_bound_whatif_verified": false,
  "ready_for_institutional_risk_gate": false,
  "live_execution_enabled": false
}
```

## Broker documentation

- [Portfolio summary: GET /portfolio/{accountId}/summary](https://www.interactivebrokers.com/docs/web-api/api-reference/trading/trading-portfolio/get-portfolio-summary)
- [Account values: GET /iserver/account/:accountId/summary](https://www.interactivebrokers.com/docs/web-api/api-reference/trading/trading-accounts/get-account-summary)
- [IBKR explanation of current excess liquidity](https://www.interactivebrokers.com/support/faq/content/23298933)
- [Read-only Gateway setup](IBKR_READONLY.md)

**Next implementation:** signed/observed balance evidence, additional cash/FX accounting, authenticated order-independent loan principal verification, and collateral haircut reporting (still read-only) before permitting connection to any RMS/trading interface.
