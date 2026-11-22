# AtlasPM v0.4.11 — Offline Financing Cost & Static Margin Stress

**All outputs are hypothetical and non-executable.** No accounts, market data, statements, broker authentication, or live orders are required for the synthetic test. This module reuses v0.4.9 and v0.4.10 diagnostics as local, private *claims*; it cannot authenticate the saved JSON or establish IBKR margin approval.

## PowerShell: completely synthetic end-to-end pipeline

Run from the repository in PowerShell after the release is merged:

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_margin_stress*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# (1) Fresh synthetic margin monitor
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-margin-monitor --fixture examples\ibkr_gateway_synthetic.json --account DU1234567 --output local_data\margin_monitor_fixture.json

# (2) Fresh synthetic raw Gateway capture
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-readonly --fixture examples\ibkr_gateway_synthetic.json --account DU1234567 --output local_data\ibkr_fixture_capture.json

# (3) Offline financing-claims cross-check
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-financing-check --capture local_data\ibkr_fixture_capture.json --mapping examples\ibkr_mapping_synthetic.json --statement examples\ibkr_financing_synthetic.json --output local_data\ibkr_financing_check.json

# (4) Offline static portfolio stress: NEVER connects to IBKR
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-stress --monitor local_data\margin_monitor_fixture.json --financing local_data\ibkr_financing_check.json --scenarios examples\ibkr_margin_stress_synthetic.json --output local_data\ibkr_margin_stress.json
```

The summary prints only masked account suffixes, numbered scenarios and risk codes. **Never paste or commit** real reports in `local_data/`; that directory is ignored by Git and is mandatory for private inputs and reports. The synthetic fixture account/positions/principal are invented.

Expected terminal status is `HYPOTHETICAL_STRESS_ONLY`. Results include `ILLUSTRATIVE_ABOVE_FLOOR`, `ILLUSTRATIVE_WATCH`, or `ILLUSTRATIVE_DEFICIT` on each scenario; these describe only the provided assumptions. None is permission to buy, borrow, trade or liquidate.

## Explicit financial assumptions (not from broker)

The scenarios JSON defines:

- `method: hypothetical_static_margin_stress`;
- `shockable_position_value_usd`: portion of the portfolio assumed to suffer a proportional price loss. This must be explicitly declared; **not inferred** from broker holdings or verified eligible collateral;
- `assumed_annual_financing_apr`: a user-assumed simple annual financing rate, not the current IBKR margin schedule, tier or guaranteed future rate;
- `minimum_excess_to_nlv_ratio`: user-defined hypothetical warning cushion;
- Each entry has a private `name`, `shock_fraction` from 0 to 1, `maintenance_multiplier` from 1 to 5, and `horizon_days` from 0 to 365.

Financial arithmetic (USD, illustrative, rounded only when displayed):

```text
market_loss = shockable_position_value * shock_fraction
new_simple_financing_interest = loan_principal_claim * assumed_APR * days / 365

hypothetical_NLV = starting_NLV - market_loss - new_simple_financing_interest
hypothetical_equity_with_loan = starting_equity_with_loan - market_loss - new_simple_financing_interest

hypothetical_maintenance = starting_maintenance * maintenance_multiplier
hypothetical_initial = starting_initial * maintenance_multiplier

hypothetical_excess = hypothetical_equity_with_loan - hypothetical_maintenance
hypothetical_available = hypothetical_equity_with_loan - hypothetical_initial
```

The financing claim is NOT subtracted as a fresh debt from a starting NLV that already incorporates account liabilities. Previously accrued interest is tracked as a separate claimed amount and is NOT subtracted again. The only modeled incremental interest is the explicitly assumed future cost for the chosen time horizon.

A `PROJECTED_MAINTENANCE_DEFICIT` flag means **the static mathematical assumption** produced nonpositive excess; IBKR may demand different securities-specific or intraday margin requirements. The model omits volatility-based Portfolio Margin, options Greeks, FX conversion, order fees, bid-ask slippage, tax effects, price gaps, collateral haircut transitions, loan rate tiers, settlement and broker liquidation policies.

## Integrity and trust conditions

This function refuses reports with missing or conflicting accounts, unsupported mixed synthetic/real sources, upstream data blocked, conflicting NLV or margin numbers, conflicting loan principal claims, malformed scenario inputs, or upstream attempts to advertise execution readiness. The two source reports are **not signed or newly reauthenticated** when a saved file is opened: freshness of prior observations is not independently proven. Rerun the read-only steps for current synthetic diagnostics; any real usage requires additional broker-source assurance.

The resulting private report permanently sets:

```json
{
  "IBKR_financing_debt_separately_verified": false,
  "broker_treasury_collateral_verified": false,
  "IBKR_order_bound_whatif_verified": false,
  "ready_for_institutional_risk_gate": false,
  "live_execution_enabled": false
}
```

**Not a broker simulation, margin forecast, investment recommendation, risk gate approval, or liquidation engine.**

Related limitations: [IBKR margin monitor](IBKR_MARGIN_MONITOR.md), [financing claim checks](IBKR_FINANCING.md), [hypothetical liquidation backtest](MARGIN_SIM.md).
