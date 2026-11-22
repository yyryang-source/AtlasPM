# IBKR Client Portal Gateway — GET-only read adapter (v0.4.7)

**Scope: local Windows account capture, NOT order execution.** This module supports raw read-only IBKR portfolio information when the user personally starts and authenticates the Client Portal Gateway. Portfolio financing debit, true Treasury collateral, option identifiers, and margin requirements are NOT yet normalized to the institutional risk gate.

## Zero-network validation first

~~~powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_gateway*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-readonly --fixture examples\ibkr_gateway_synthetic.json --account DU1234567 --output local_data\ibkr_fixture_capture.json
~~~

The account ID DU1234567 is fake. The fixture run makes no network requests.

## Optional local private read when personally authenticated

Only if you actually have an IBKR account:

1. Download the official Client Portal Gateway, which requires a local Java runtime. Follow the IBKR official lesson below.
2. Extract the gateway ZIP. In the gateway directory on Windows run:
   ~~~powershell
   .\bin\run.bat root\conf.yaml
   ~~~
   Leave the gateway running and authenticate yourself in a browser at https://localhost:5000. Never supply login details, cookies or two-factor codes to this repository or ChatGPT.
3. TLS note: The gateway's default self-signed certificate may be untrusted or expired. AtlasPM deliberately does NOT support skipping TLS verification. Configure a valid localhost certificate and trust its issuing certificate in Windows, or provide the trusted PEM certificate/issuer through the optional --ca-file option.
4. From a separate AtlasPM PowerShell window, use your own account ID, never a sample ID:
   ~~~powershell
   .\.venv\Scripts\python.exe -m atlaspm.cli ibkr-readonly --allow-local-gateway --account YOUR_IBKR_ACCOUNT_ID --url https://localhost:5000/v1/api --ca-file C:\path\to\your\trusted-local-ca.pem --output local_data\ibkr_private_raw.json
   ~~~
   If the gateway already uses a certificate trusted by Python, you may omit --ca-file.

The command fails closed if the gateway is not authenticated, the account is not explicitly selected from the portfolio account list, the TLS certificate cannot be verified, or the returned account data is malformed or unsupported. No insecure-SSL flag exists.

## GET-only endpoints

- /iserver/auth/status — connected and authenticated session check
- /portfolio/accounts — obtain and select an account, before other portfolio endpoints
- /portfolio/{accountId}/ledger — raw USD account ledger including settledcash and netliquidationvalue
- /portfolio2/{accountId}/positions — recent account positions identified by IBKR contract IDs

References:
- https://ibkrcampus.com/docs/web-api/trading/portfolio-and-positions/querying-your-accounts
- https://ibkrcampus.com/docs/web-api/v1/endpoints/portfolio/portfolio-ledger
- https://ibkrcampus.com/docs/web-api/v1/endpoints/portfolio/positions-new
- https://ibkrcampus.com/campus/trading-lessons/launching-and-authenticating-the-gateway/
- https://ibkrcampus.com/docs/web-api/authentication/cpgw/client-portal-gateway-faq

## Privacy, output and trust boundary

- Full raw output includes account identifiers, holdings, balances and possibly gateway session metadata. It is **PRIVATE**. Never upload the full JSON to GitHub, ChatGPT or public issues. Save only in local_data, excluded by Git.
- Console output hides the complete account ID and all portfolio dollar amounts. It displays only class counts, authenticated status and data limitations.
- This is a local user-authenticated Gateway HTTPS response, NOT proof of approved margin, regulatory borrowing eligibility, a security haircut, option trading permission or a broker order preview.
- Margin borrowing, unsettled receivables, bond dirty price, and options contracts require a careful separate mapping to AtlasPM's accounting schema. Do not blindly map signed IBKR cash to net margin debt.
- Only a single USD-base account with identifiable USD position currency is supported at first. No FX translation or cross-account collateral use is assumed.
- No real or broker-paper orders are submitted. IBKR's local browser login may require daily reauthentication. No automatic login or secrets are stored.
- The source is restricted to GET requests to loopback HTTPS, with no redirects and no environment proxies; it uses standard TLS validation and response time/size limits.

## Remaining work

Normalize conid vs ticker/CUSIP/OCC identities, verify actual brokerage margin inputs, handle multi-currency/settlement differences, reconcile transactions and account financing, add safe transient data snapshots and fully authenticated What-If checks. The existing Paper OMS remains offline and the live trading code path does not exist.
