# Security policy

Do not commit API tokens, broker credentials, account identifiers, personally identifiable information, bank details, or actual private portfolios.

The `.gitignore` excludes common environment and credential files, but Git ignore rules are **not** a replacement for secret scanning.

If a credential is exposed, revoke/rotate it at its provider and follow GitHub's documented process for purging sensitive history. Do not submit leaked credentials in public issues.

AtlasPM supports explicitly human-confirmed order submission to Interactive Brokers (IBKR) Paper Trading accounts only. Live-account order submission remains disabled. Paper order execution requires explicit operator authorization, verified Paper account identity, and pre-trade risk controls. Brokerage integrations must follow the principle of least privilege.

For security concerns, contact the maintainer privately rather than posting secrets in a public issue.
