# AtlasPM v0.4.12 — Multi-Asset Correlation Stress & Financing Risk Budgets

**Offline, synthetic, read-only, research-only.** This upgrade consumes a private v0.4.11 `ibkr-stress` report and a separate *assumed* three-sleeve portfolio/correlation manifest. It does not read new brokerage data, validate loan principal, estimate actual IBKR Portfolio Margin, forecast liquidation, place broker orders, or train an AI model.

## Windows PowerShell — starting from a saved v0.4.11 report

The `ibkr_margin_stress.json` input is the private result created during v0.4.11. Use the completed synthetic report; don't type a JSON object at the PowerShell command prompt.

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .

.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_multiasset_budget*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-multiasset-budget --stress local_data\ibkr_margin_stress.json --assumptions examples\ibkr_multiasset_budget_synthetic.json --output local_data\ibkr_multiasset_budget.json
```

If you deleted the local v0.4.11 report, first rerun the complete synthetic fixture steps in [v0.4.11](IBKR_MARGIN_STRESS.md). **Do not upload private IBKR JSON** to GitHub, issues, PRs or ChatGPT. Only share the redacted CLI output. All detailed files must remain in Git-ignored `local_data/`.

The example has three entirely fictional *risk sleeves*, all equities, adding to the earlier **$60,000 purely assumed shockable amount**. They are **not** three known IBKR holdings (the older synthetic Gateway fixture only contained one equity conid plus a bond). This separation is intentional: v0.4.12 does **not** claim to map the real positions, securities haircuts, cash, shorts, FX, futures or options.

## Explicit input assumptions

The JSON input declares:

- `method: hypothetical_multiasset_risk_budget`;
- `assets`: one to 12 unique long-only research sleeves, `id`, `type` (`equity` or `treasury`), positive or zero `market_value_usd` and `annual_volatility`. The sleeve values **must sum** to the upstream v0.4.11 `assumed_shockable_position_value_usd` within 1 cent;
- `base_correlation`: the square, symmetric, diagonal-1, positive-semidefinite correlation matrix in **exact asset order**; matrices with invalid or contradictory correlations are rejected;
- `risk_limits`: `max_one_day_99pct_var_fraction_of_nlv`, `max_gross_exposure_to_nlv`, `min_excess_liquidity_to_nlv`, and `max_assumed_annual_interest_to_nlv`;
- `scenarios`: one to 20 named hypothetical scenarios with exactly one `asset_returns` entry per sleeve, `maintenance_multiplier` (1 to 5), integer `horizon_days` (0 to 365), optional `apr_additive`, `volatility_multiplier` and `correlation_override`.

For better privacy with custom scenarios, save your personal assumptions to `local_data/` rather than the public `examples/` directory. The example file is completely fabricated and safe to commit.

## Calculations and important distinctions

**Deterministic stress P&L:**

```text
market_PnL = sum(assumed_asset_market_value_i * assumed_asset_return_i)
new_financing_interest = claimed_loan_principal * (assumed_APR + APR_add) * days / 365

scenario_NLV = starting_NLV + market_PnL - new_financing_interest
scenario_ELV = starting_equity_with_loan + market_PnL - new_financing_interest
scenario_maintenance = starting_maintenance * maintenance_multiplier
scenario_excess = scenario_ELV - scenario_maintenance
```

The existing financed balance is **not subtracted again** from starting NLV/ELV. Already-accrued interest is **not** re-expensed. The APR is a user assumption, not IBKR's actual tiered rate.

**Correlation-sensitive statistical risk (separate from fixed-price shock):**

```text
annual_variance_USD² = sum_i sum_j(V_i * V_j * vol_i * vol_j * correlation_ij)
1-day_99pct_delta_normal_VaR_USD = 2.326347874 * sqrt(annual_variance_USD² / 252) * vol_multiplier
VaR_ratio = VaR_USD / starting_NLV
```

- This normal-model VaR is a **single-day statistical approximation**, not a stress-test P&L and not a validated empirical tail loss; it assumes 252 trading days, zero return drift, assumed annual volatilities, stable exposures and user-supplied PSD correlations.
- Increasing cross-asset correlations may raise statistical portfolio VaR but does **not** retroactively change the P&L of a deterministic scenario whose per-asset returns are already specified.
- `gross_exposure_to_nlv` uses only the explicitly shockable sleeves, **not** the complete broker portfolio. Don't treat it as a verified total-gross limit.
- Scenario risk state can be `HYPOTHETICAL_WITHIN_LIMITS`, `HYPOTHETICAL_LIMIT_BREACH`, or `HYPOTHETICAL_DEFICIT`. Reasons identify statistical VaR, total assumed shockable leverage, interest-cost budget, cash availability, liquidity buffers, or maintenance deficit.
- Even an apparently healthy hypothetical state cannot pass a real broker risk gate.

## Privacy and trust boundaries

The upstream report is a **user-controlled, unsigned** local diagnostic. v0.4.12 validates schema, baseline arithmetic, the permanent false trading/provenance flags, and the assumed total shockable value, but cannot independently reauthenticate or timestamp the original account capture. The console masks the account ID and prints only scenario ordinals, states, generic reason codes and number of fictional sleeves, never amounts, asset IDs or private scenario names.

**Always false:**

```json
{
  "IBKR_financing_debt_separately_verified": false,
  "broker_treasury_collateral_verified": false,
  "IBKR_order_bound_whatif_verified": false,
  "ready_for_institutional_risk_gate": false,
  "live_execution_enabled": false
}
```

This prototype excludes empirical covariance estimation, price-history provenance, tail dependence, changing position weights, actual broker maintenance/haircut rules and correlation instability. Those require separately validated market data and a future research workflow. No AI training, brokerage actions, or financing transactions occur here.
