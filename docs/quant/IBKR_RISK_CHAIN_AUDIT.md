# AtlasPM v0.4.14 — Offline Risk Evidence Chain Audit

**What it does:** cross-check five existing, strictly private local diagnostics from v0.4.9–v0.4.13 and report arithmetic inconsistencies, changed files and hypothetical risk-alert counts.

**What it does not do:** independently authenticate IBKR data, prove financing principal, certify Treasury collateral, establish an order-bound broker What-If or allow live transactions. Every report is user-controlled/unsigned and may be stale.

## Windows PowerShell

If you already ran the previous five synthetic tests, their local output files can be used directly:

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_risk_chain_audit*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-risk-audit --monitor local_data\margin_monitor_fixture.json --financing local_data\ibkr_financing_check.json --stress local_data\ibkr_margin_stress.json --multiasset local_data\ibkr_multiasset_budget.json --reverse local_data\ibkr_reverse_stress.json --output local_data\ibkr_risk_audit.json
```

An optional *second* audit can compare the five current files with the first audit's SHA-256 digest manifest:

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-risk-audit --monitor local_data\margin_monitor_fixture.json --financing local_data\ibkr_financing_check.json --stress local_data\ibkr_margin_stress.json --multiasset local_data\ibkr_multiasset_budget.json --reverse local_data\ibkr_reverse_stress.json --compare local_data\ibkr_risk_audit.json --output local_data\ibkr_risk_audit_second.json
```

`--compare` must point to an existing private v0.4.14 audit under `local_data/`, and the second output must be a **different file**. Never overwrite audit input files. Do not paste, upload, print or commit any genuine broker JSON; only share the redacted CLI output.

## Audit semantics

The command:

- Enforces the correct report method and expected non-executable/provenance flags for all five stages, matching full private account IDs across the reports.
- Verifies monitor/financing/stress baseline NLV, ELV, initial and maintenance margin and available/excess-liquidity fields.
- Checks financing principal consistency across financing, stress, multiasset and reverse stages; **this is consistency of supplied claims, never independent loan verification**.
- Recomputes v0.4.11 market-shock losses, incremental simple interest, hypothetical NLV, equity, margin and excess-liquidity arithmetic.
- Recomputes v0.4.12 weighted scenario shock P&L, incremental interest, parametric one-day 99% normal VaR using declared sleeve exposures/volatility/correlations, and hypothetical equity/margin fields.
- Recomputes v0.4.13 reverse shock sensitivities, simple interest, mathematical NLV/excess at maximum decline, and first crossing fractions and trigger codes.
- Saves SHA-256 hashes of canonically serialized reports. When `--compare` is supplied it notes differences since the original saved manifest; this is **not** a cryptographic proof of when/by whom the original file was created. The audit cannot detect coordinated editing of reports and the baseline audit, and cannot certify any original brokerage source.

### Possible statuses

`CHAIN_INCONSISTENT` means report status, account, expected non-trading permissions, financial arithmetic, or optional baseline digest checks failed.

`HYPOTHETICAL_ALERTS_PRESENT` means all supported checks agree but one or more preceding assumed scenarios or the monitor are cautionary. This is **expected** with the synthetic fixtures, whose severe stress cases are intentionally risky.

`HYPOTHETICAL_CLEAR_NOT_VERIFIED` means there were no detected model-level issues or hypothetical alert states; **this is never an approval for trading or a claim of actual brokerage safety**.

No report can set `ready_for_institutional_risk_gate` or `live_execution_enabled` to true.

## Important audit boundaries

These files contain user-supplied figures and synthetic risk-sleeve assumptions, not signed IBKR broker snapshots. Purely internal consistency checks can be passed by incorrectly constructed but internally consistent data. Some scenario status codes are assessed as stage summaries; the audit does not reproduce real-world market conditions, lending rates, borrow eligibility or forced-liquidation decisions. Timestamp freshness cannot be reauthenticated from previously saved reports.

No network requests are made. Full source hashes and private metadata remain in ignored `local_data/`. Standard output includes only the masked account suffix, counts, audit state and a closed vocabulary of drift codes.

For stage methodology, see [v0.4.13 reverse stress](IBKR_REVERSE_STRESS.md), [v0.4.12 multiasset risk budget](IBKR_MULTI_ASSET_BUDGET.md), [v0.4.11 static stress](IBKR_MARGIN_STRESS.md), and [v0.4.10 margin monitor](IBKR_MARGIN_MONITOR.md).
