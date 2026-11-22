# AtlasPM v0.4.15 — Offline Risk Audit Comparison

**Research-only / no live execution.** `ibkr-audit-diff` compares two private v0.4.14 `ibkr-risk-audit` JSON reports for the same local account. It checks stored SHA-256 canonical-source fingerprints and changes in hypothetical risk alert counts. It does **not** attest to which audit came first, authenticate the broker, establish genuine portfolio improvement, or enable trading.

## Windows PowerShell: compare baseline against a repeated snapshot

You already generated `local_data\\ibkr_risk_audit.json` when running v0.4.14. Create a *second* audit using the five existing synthetic files:

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .

.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_risk_audit_diff*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# Repeat the audit with the original v0.4.14 report as a comparison baseline.
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-risk-audit --monitor local_data\margin_monitor_fixture.json --financing local_data\ibkr_financing_check.json --stress local_data\ibkr_margin_stress.json --multiasset local_data\ibkr_multiasset_budget.json --reverse local_data\ibkr_reverse_stress.json --compare local_data\ibkr_risk_audit.json --output local_data\ibkr_risk_audit_second.json

# Analyze differences between the two saved audit outputs.
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-audit-diff --previous local_data\ibkr_risk_audit.json --current local_data\ibkr_risk_audit_second.json --output local_data\ibkr_risk_audit_diff.json
```

The expected synthetic result is `NO_SOURCE_CHANGES_DETECTED_UNVERIFIED`, an empty `changed_source_stages` array, and zero risk-count deltas, **provided none of the original stage report files were altered**. A different result does not mean the software is broken: first inspect which private files or assumptions changed.

### Comparison states

- `NO_SOURCE_CHANGES_DETECTED_UNVERIFIED` — the five stored canonical JSON content digests match, and summary counts/statuses agree. **Not evidence that the broker data were correct or recent.**
- `REVIEW_REQUIRED_CHANGED_EVIDENCE` — one or more report fingerprints changed. Inspect the changed stage(s) before interpreting any improvement or deterioration.
- `REVIEW_REQUIRED_AUDIT_CONFLICT` — either audit recorded a chain inconsistency, or alert counts/statuses changed *without* a change in the underlying recorded source hashes. This is a fail-closed review state.

The comparison also reports only broad change classes such as `HYPOTHETICAL_ALERT_COUNTS_HIGHER`, `HYPOTHETICAL_ALERT_COUNTS_LOWER_UNVERIFIED`, `MIXED_HYPOTHETICAL_ALERT_CHANGE`, or `HYPOTHETICAL_ALERT_COUNTS_UNCHANGED`. A reduced alert count is neither an actual risk improvement nor a reason to add leverage.

## Security and trust

Both inputs and the output **must remain under** Git-ignored `local_data/`. The CLI refuses to overwrite either input. Stdout masks account ID and omits source hashes, loan sizes, positions and scenario labels. The private output retains the old and new source fingerprints for local research.

All audits are unsigned **user-controlled** JSON: a coordinated modification to both saved reports may go undetected. The program does not independently check broker timestamps, obtain IBKR statements, sign documents, protect the local disk against attackers, or provide authentication/authorization.

All result flags remain false:

```json
{
  "broker_source_independently_authenticated": false,
  "IBKR_financing_debt_separately_verified": false,
  "broker_treasury_collateral_verified": false,
  "IBKR_order_bound_whatif_verified": false,
  "ready_for_institutional_risk_gate": false,
  "live_execution_enabled": false
}
```

This is a useful *local review and change-detection workflow*, not a real-world margin risk attestation. Review [v0.4.14 original evidence-chain audit](IBKR_RISK_CHAIN_AUDIT.md) for source limitations.
