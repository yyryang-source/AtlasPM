# AtlasPM v0.5.3 — Past-only Multi-Model Risk Tournament

**Purpose:** automatically select a *recently validated risk-forecast model*, or blend models, at each historical portfolio rebalance. This is **not** an oracle that picks whichever model has the best final 2000–2026 returns. The approach is a strict rolling *prequential* (predict, then validate once the forecast horizon has ended) research experiment.

Models in the tournament:

- **Ridge**: historical expanding-window supervised forward RMS risk regression, no pretrained checkpoints
- **LightGBM**: optional native open-source boosted trees, fit from the same historical features/fully observed labels, not the official Microsoft Qlib pipeline
- **Trailing volatility**: untrained 20-day annualized return RMS, a genuine fallback competitor

All candidates predict the *magnitude of risk*, **not** market direction, expected return, outperformance, profitability or IBKR buying power.

## Windows training commands

Update from GitHub and install the optional real LightGBM package:

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e ".[ml]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ml_model_tournament*.py" -v
```

Using your existing **local, downloaded research CSV** `local_data/market_2000_2026.csv` containing SPY, QQQ, VFITX:

```powershell
# Champion: automatically select the most recently validated candidate.
.\.venv\Scripts\python.exe -m atlaspm.cli ml-model-tournament --file local_data\market_2000_2026.csv --mode champion --feature-days 20 --forecast-days 5 --min-train 756 --rebalance-days 21 --validation-window 36 --min-validated-rebalances 6 --switch-margin 0.02 --max-exposure 1 --output local_data\tournament_champion_2000_2026.json

# Ensemble: dynamically combine predictions using inverse rolling MAE weights.
.\.venv\Scripts\python.exe -m atlaspm.cli ml-model-tournament --file local_data\market_2000_2026.csv --mode ensemble --feature-days 20 --forecast-days 5 --min-train 756 --rebalance-days 21 --validation-window 36 --min-validated-rebalances 6 --switch-margin 0.02 --max-exposure 1 --output local_data\tournament_ensemble_2000_2026.json
```

Full output includes actual predictions, candidate MAEs, selected model weights, portfolio and cash allocations, trade costs, and five full equity curves under Git-ignored `local_data/`. Console output is limited to fixed model identifiers and aggregate counts, not account names, holdings or full user-profile paths.

Compare the simulated strategies:

```powershell
$champ = Get-Content .\local_data\tournament_champion_2000_2026.json -Raw | ConvertFrom-Json
$ensemble = Get-Content .\local_data\tournament_ensemble_2000_2026.json -Raw | ConvertFrom-Json

foreach ($r in @($champ, $ensemble)) {
    Write-Output ("MODE: " + $r.selection_policy.mode)
    $r.model_selection | Format-List
    $r.forecast_validation | Format-List
    $r.performance | Format-List
}
```

## How real-time historical selection is audited (strictly no future peeking)

Each rebalance has a historical price-index signal `s` (the prior session's close). A candidate predicts risk for the next `H` daily returns `[s, s+H)`. **Its target is only eligible for validation after the close at index `s+H`.** At current signal `t`, the tournament uses only predictions emitted at earlier decisions whose label end satisfies `s+H <= t`. Future realized outcomes are NOT saved in selection history ahead of maturity.

Every historical decision:

1. Retrieve recent **fully matured** out-of-sample predictions, limited to the latest `--validation-window` prior rebalance dates (default 36). No samples from the future.
2. If fewer than `--min-validated-rebalances` (default 6) have matured, **use trailing volatility** only; don't crown an AI with no verified track record.
3. Compute per-model MAE against their **future observed risk**, now known at this decision, across matching assets/dates.
4. **Champion** mode: prefer the lower-MAE ML model only if it beats the trailing-vol baseline by at least `--switch-margin` fraction (default 2%). Hysteresis reduces unnecessary switches between closely scored ML models. If no ML model clearly wins, choose traditional trailing volatility.
5. **Ensemble** mode: if no candidate ML model clearly beats the baseline, again choose trailing-vol only; otherwise blend all three forecasts with positive inverse-squared validation-MAE weights, normalized to sum 1.
6. Convert the chosen annual risk forecasts into portfolio weights and cash. **Every candidate and the selected strategy** must pass the SAME per-asset maximum weight, predicted-volatility cap, historical tail-loss cap, uniform stress-loss cap, drawdown guard and 0–1x total risky exposure hard limit.
7. Simulate trade at the following session close, pay fixed fee+slippage per traded notional, then earn subsequent close-to-close returns.

Past-only labels are used to train Ridge/LightGBM models; their model refits at each decision never use a future trading return. This prevents **direct lookahead**. The MAE selection remains a noisy research heuristic, not an econometrically tested superiority rule, and repeated selection over the same historical series can overfit even without direct price leakage.

### Five strategies reported side-by-side

`performance` and `equity_curves` both contain:

- `selected`: champion or ensemble, automatically reweighted
- `ridge_only`: continuously fitted Ridge, otherwise identical constraints and costs
- `lightgbm_only`: continuously fitted LightGBM, otherwise identical constraints and costs
- `naive_trailing_vol_risk`: untrained historical RMS, otherwise identical constraints and costs
- `equal_weight_buy_and_hold`: fully invested equally weighted original basket (buy once), identical initial fees/slippage assumptions

The buy/hold benchmark has meaningfully different risk exposure; **do not** claim winner status from lower drawdown alone. The tournament selection is by *forecast MAE*, **not** trailing Sharpe, return, drawdown or final equity. All five are research comparators, not investable risk-adjusted total-return strategies.

## What this DOES NOT do

- No Alpha158, full Qlib implementation, institutional proprietary weights, trained options strategy or factor investment alpha.
- No real authenticated IBKR positions, institutional Portfolio Margin, financing, direct bond yields, cash yield, execution simulation at actual quoted prices, or live/paper orders.
- No independently untouched final time split yet. The full 2000–2026 historical timeline is a series of repeated expanding fits and locally evaluated validation windows. **Do not optimize parameters repeatedly on the last five years and then still call that interval a blind final test.**
- No statistical certainty that any chosen winner is robust through new regimes; overlapping 5-day risk outcomes across nearby predictions are dependent, and market data itself can be revised.
- No guarantee that a lower risk-prediction MAE yields a better portfolio Sharpe, lower drawdown or higher net returns.
- No hyperparameter search on the final data. `validation_window=36`, `minimum_validated_rebalances=6`, `switch_margin=.02` are explicitly research defaults, not optimized trading parameters.

### Safe next steps

Use an explicit, separately locked **2000–2015 fit / 2016–2020 model-development / 2021–2026 final-holdout** protocol that does not look at 2021–2026 results until the design is frozen. Validate data provenance, trading calendars and adjusted mutual fund ETF marks. Add cash/Treasury yields and realistic spread, trading cost, fillability and after-tax implications. Only after independent research validation consider permissioned paper order simulation with an entirely separate risk/authorization process.

This project does not perform background or autonomous execution. The word "automatic" applies solely to the historical simulated target-weight decisions within one user-run CLI command.
