# AtlasPM v0.4.16 — Local Risk Dashboard (Offline HTML)

**A real browser-openable risk review interface, built from existing AtlasPM reports; not a server, brokerage connector or trading terminal.**

This version introduces `ibkr-dashboard`, a single Windows PowerShell command that reads eight *private existing* diagnostic/audit files from `local_data/`, recalculates v0.4.14 cross-stage integrity and cross-checks v0.4.15 audit differences, and writes one **self-contained, redacted** HTML page. It requires no npm, React, web server or new Python packages.

## Windows PowerShell

You already completed v0.4.10–v0.4.15 synthetic tests and should have all files.

```powershell
cd $HOME\Desktop\AtlasPM
git pull origin main
.\.venv\Scripts\python.exe -m pip install -e .

# Validate v0.4.16's new functionality.
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ibkr_dashboard*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# One-command dashboard build. Everything remains under local_data/.
.\.venv\Scripts\python.exe -m atlaspm.cli ibkr-dashboard --folder local_data --output local_data\atlaspm_dashboard.html

# Open the offline dashboard in your default browser.
Start-Process .\local_data\atlaspm_dashboard.html
```

**You must re-run the dashboard command after changing any input JSON**: the HTML is a static snapshot, not an auto-refreshing monitor. The browser only opens a local file; no server is started, no public URL is created, and no broker data is sent to any site.

If you previously only ran the first v0.4.14 audit, run the documented v0.4.15 repeated audit + audit diff first; the dashboard requires all eight files.

## Data expected, using exact filenames

The command expects these files in the project's Git-ignored `local_data/` folder:

```text
margin_monitor_fixture.json
ibkr_financing_check.json
ibkr_margin_stress.json
ibkr_multiasset_budget.json
ibkr_reverse_stress.json
ibkr_risk_audit.json
ibkr_risk_audit_second.json
ibkr_risk_audit_diff.json
```

You can set `--output` to a different `.html` path **within the same ignored local_data/** but `--folder` must resolve to the project's actual `local_data` directory. The CLI rejects a public output or attempts to write a non-HTML file.

## Dashboard contents

The dark, responsive HTML page includes:

- A headline local diagnostic state: `LOCAL_SIMULATED_ALERTS_PRESENT` (normal for the synthetic fixtures), `LOCAL_REPORTS_CONSISTENT_UNVERIFIED`, or `LOCAL_REVIEW_REQUIRED` for stale/conflicting saved inputs.
- Overview cards for local arithmetic integrity count, total hypothetical scenario warnings, number of changed local source reports, and permanently disabled execution.
- Five expandable stage cards showing **only fixed, allowlisted state labels and numbered scenario outcomes**: monitor, financing, static stress, correlation budget and reverse stress.
- An offline, CSS-only chart of hypothetical alert counts for three risk categories.
- Summary of v0.4.14 integrity findings and v0.4.15 saved diff review status.
- Persistent warning: none of these reports prove financing debt, eligible collateral, broker source authenticity, buying power, order-bound What-If or IBKR liquidation limits.

Unlike a live dashboard, this is not a continuously updating browser application. It contains **no JavaScript, external CSS, image/font URLs, CDNs, API calls, network access, hidden brokerage tokens or login flows**. An embedded Content-Security-Policy explicitly disallows scripts and remote connections. Details expand natively with HTML `<details>` without JavaScript.

## Privacy and data-integrity limits

- The generator uses raw input reports **in memory** only to recompute a local five-stage audit. It does not embed them into the HTML.
- Page generation maps statuses through hardcoded, finite allowlists. It never prints full account IDs, security names, dollar amounts, loan principal, user-provided scenario labels, source hashes or private JSON paths.
- All HTML exports remain on your local disk; they still disclose your **masked account suffix and risk status**, so don't share them publicly.
- If the newly recomputed five-stage audit disagrees with the saved latest audit, or if the saved audit diff disagrees with re-evaluated audit comparison, the screen shows `LOCAL_REVIEW_REQUIRED`. It **does not** silently present a green risk approval.
- Audit SHA-256 hashes only capture unsigned local file contents, not original broker provenance. Anyone able to modify both source reports and audit outputs can forge internally consistent histories.
- Changes to previous scenario inputs do not refresh the generated page; rerun each stage and the saved audits before rebuilding it.
- All versions keep `IBKR_financing_debt_separately_verified=false`, `ready_for_institutional_risk_gate=false`, and `live_execution_enabled=false`.

**Do not upload your real `local_data/` JSON reports or dashboard HTML** to GitHub, screenshots, public websites, emails, or chat. You can safely share the redacted PowerShell console summary for troubleshooting.

Related: [v0.4.14 risk-chain audit](IBKR_RISK_CHAIN_AUDIT.md) and [v0.4.15 audit-diff workflow](IBKR_RISK_AUDIT_DIFF.md).
