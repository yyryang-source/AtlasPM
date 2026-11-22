# AtlasPM architecture (v0.1)

## Principles

1. **Local-first**: data and execution remain on the user's machine.
2. **Explicit account separation**: cash and margin accounts do not share maintenance buffers.
3. **Explainability**: calculations are auditable, and assumptions are in output.
4. **No silent orders**: broker integrations will be paper-only by default; future live orders require explicit authorization and pre-trade risk checks.
5. **Secrets never in Git**: use environment variables or a local secret store, never config tracked in source control.

## Modules

- `models.py`: validated long-only account and position objects.
- `portfolio.py`: ledger-based equity/leverage, borrowing estimates, scenario stress.
- `historical.py`: optional Yahoo Finance downloader and strict local CSV estimator (252 trading days per year, shared-date simple daily returns, no forward-fill).
- `mean_variance.py`: deterministic projected-gradient mean-variance scenario optimizer; estimates/covariance are explicit inputs and assumed financing is not a broker margin check.
- `optimizer.py`: deterministic inverse-volatility baseline with max-weight constraints.
- `cli.py`: reproducible JSON-in/JSON-out command-line interface.
- `examples/`: fictional sample input.
- `tests/`: numerical and input-validation regression tests.

## Definitions

Equity / net liquidation value = cash + market value of longs - borrowed amount.

Gross exposure = long market value (cash excluded).

Gross exposure leverage = gross exposure / equity.

Illustrative maintenance required = sum(position value * assumed maintenance fraction).

Maintenance excess = equity - illustrative maintenance required.

A hypothetical uniform decline `d` makes stressed holdings `(1 - d) * holdings`, leaving cash/debt unchanged. The output reports an illustrative maintenance shortfall, not a broker-confirmed margin-call condition.

Annualized borrowing estimate = debt * stated rate, excluding compounding, tiering, fees, changes in borrowing balance, and other financing terms.

## Explicit nonfeatures in v0.1

No live broker data, no broker orders, no training datasets, no forecasting model, no covariance optimizer, no options/shorts, no tax lots, no transaction cost model, no production risk engine, and no web UI. The CLI only analyzes data supplied by the user.
