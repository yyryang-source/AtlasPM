# Security policy

Do not commit API tokens, broker credentials, account identifiers, personally identifiable information, bank details, or actual private portfolios.

The `.gitignore` excludes common environment and credential files, but Git ignore rules are **not** a replacement for secret scanning.

If a credential is exposed, revoke/rotate it at its provider and follow GitHub's documented process for purging sensitive history. Do not submit leaked credentials in public issues.

AtlasPM v0.1 does not connect to any brokerage and cannot submit orders. Proposed broker features must be designed for least privilege, a paper-trading default and explicit risk controls.

For security concerns, contact the maintainer privately rather than posting secrets in a public issue.
