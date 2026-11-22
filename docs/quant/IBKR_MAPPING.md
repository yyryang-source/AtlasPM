# AtlasPM v0.4.8 — IBKR Security-Master Mapping Diagnostics

**Research-only, offline, no order connectivity.** Converts neither private IBKR Gateway capture nor a user-supplied contract mapping into verified buying power, live broker margin, permission to trade, or trusted collateral.

## Windows PowerShell — fully synthetic mode

~~~powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_mapping*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-readonly --fixture examples\ibkr_gateway_synthetic.json --account DU1234567 --output local_data\ibkr_fixture_capture.json

.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-map-check --capture local_data\ibkr_fixture_capture.json --mapping examples\ibkr_mapping_synthetic.json --output local_data\ibkr_mapping_check.json
~~~

Fixture account, conids, CUSIP-like ID, shares, quotes and amounts are all **invented**. These commands make no network requests and modify no real portfolio.

Expected console-only status for the synthetic mapping: IDENTITIES_MAPPED_REVIEW_REQUIRED, 2/2 contracts mapped, no mapping blockers, readiness for institutional RMS false, real execution false. Full account details are written only to Git-ignored local_data. The CLI masks account IDs and never prints conids, holdings or dollar balances.

## Why explicit mapping is necessary

IBKR's portfolio2 positions endpoint identifies each security by a numeric conid and a broker asset class. AtlasPM research identifies a long equity by its ticker, Treasury by its CUSIP, and an option by a complete signed contract identity. An IBKR BOND class can indicate many kinds of bonds; it does **NOT** establish that the bond is a U.S. Treasury, nor the face-value unit convention. A matching public fixture does not validate genuine CUSIPs.

A mapping manifest must declare for every conid:
- The corresponding IBKR assetClass STK, BOND or OPT.
- An exact AtlasPM identifier, asset type, manually_reviewed boolean and units_confirmed boolean.
- Equity quantity units SHARES, UST quantity units FACE_USD, or options CONTRACTS.
- A Treasury requires explicit issuer_confirmed true and a nine-character CUSIP-format identifier. This is a *claim requiring outside validation*, not cryptographic CUSIP validation.
- An option needs right, underlying, strike, expiration date and contract multiplier, as well as signed contract position. Static manifest metadata is **not** independent broker verification of those terms.

If a conid is missing or duplicated, one conid is mapped to more than one internal instrument, asset classes conflict, a marked security value conflicts with another value field, a quote/value is missing, currencies mismatch, or contract unit/issuer review is incomplete, the result is BLOCK_MAPPING.

## Cash, debt, bond value and interest

IBKR's USD settledcash is a signed cash field. AtlasPM MUST NOT interpret its negative value as a verified margin debit: unsettled obligations, other currency segments, financing interest and broker-specific account structure may require a different accounting bridge. The code reports, but never converts, the unexplained difference between IBKR netliquidationvalue and settledcash plus position market values.

IBKR's /portfolio/{accountId}/ledger documentation describes the field USD.interest as a **margin interest rate**. It is not an independently provided accrued financing interest liability and must NOT be placed into the internal accrued_financing_interest field. Both debt and actual accrued interest remain unverified.

The position market value may be returned under mktValue or marketValue, depending on the source shape. If both exist they must match within one cent. For Treasury securities, face quantity and dirty market value are distinct fields; the diagnostic does not compute a haircut, repo capacity or approved broker loan value. No extra collateral is generated.

## Data freshness and security

The capture must be timezone-aware, not future-dated, and no older than the mapping manifest's maximum age; 600 seconds in the synthetic example. The selected account, USD base and position currencies must agree. Captures with additional unmodeled currency blocks are blocked.

Real capture files contain account numbers, personal balances, market values and positions. Never upload actual capture or mapping manifests to public GitHub or ChatGPT. Store them only in local_data, excluded from Git; the command rejects any output outside that folder.

The mapping result is deliberately NOT a normalized account that can be used by institutional-check, plan-basket or oms-replay. It lacks authenticated margin loan data, valid security-master provenance, broker haircut, real option Greeks/expiry events, current initial and maintenance margin, funding interest accrued and broker What-If evidence.

## Related official references

- https://ibkrcampus.com/docs/web-api/v1/endpoints/portfolio/positions-new
- https://ibkrcampus.com/docs/web-api/v1/endpoints/portfolio/portfolio-ledger
- https://ibkrcampus.com/docs/web-api/trading/portfolio-and-positions/querying-equity-and-margin

## Next stage

A separate account reconciliation adapter should query authenticated broker summary and margin endpoints, normalize FX segments, prove security contract IDs, validate Treasury bond face-unit and accrued values, distinguish signed cash from financing principal, and keep genuine broker statements private. Only after robust account/NLV reconciliation and independent What-If preview should a separately engineered paper OMS consider submitting to an actual broker paper account. **Live execution remains disabled.**
