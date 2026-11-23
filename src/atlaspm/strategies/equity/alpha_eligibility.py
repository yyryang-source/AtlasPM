"""AtlasPM alpha eligibility gate: a STRICT candidate universe for monthly quant research.

The AI model decides the candidate universe by *relative percentile and rank*,
not by an uncalibrated LightGBM ranking score. This module can screen whether
a hypothetical new/increased position's ticker belongs to that universe.
It NEVER approves an actual trade, sets weights, issues broker instructions,
or overrides portfolio risk exits.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
from math import ceil, isfinite
from pathlib import Path
import re
from typing import Any

from atlaspm.monthly_stock_selection import SCHEMA as MONTHLY_SCHEMA

GATE_SCHEMA = "atlaspm_monthly_alpha_eligibility_v1"
STOCK_ACTIONS = frozenset({"OPEN", "INCREASE", "HOLD", "REDUCE", "CLOSE"})
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_MONTH_PATTERN = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_monthly(report: dict[str, Any]) -> None:
    if report.get("schema") != MONTHLY_SCHEMA:
        raise ValueError("Unsupported monthly stock report schema")
    if report.get("research_only") is not True:
        raise ValueError("Monthly report must be research-only")
    if report.get("live_execution_enabled") is not False:
        raise ValueError("Cannot generate a research gate from a live-execution report")
    if report.get("automated_handoff_approved") is not False:
        raise ValueError("Unexpected automated portfolio handoff in monthly report")
    if report.get("order_instructions") != []:
        raise ValueError("Monthly report unexpectedly contains order instructions")
    if not _MONTH_PATTERN.fullmatch(str(report.get("month", ""))):
        raise ValueError("Monthly report month invalid")
    if not _MONTH_PATTERN.fullmatch(str(report.get("next_calendar_month", ""))):
        raise ValueError("Next month invalid")
    as_of = date.fromisoformat(report["as_of_nyse_session"])
    if as_of.strftime("%Y-%m") != report["month"]:
        raise ValueError("Month and completed NYSE session mismatch")
    if report["next_calendar_month"] != (
        f"{as_of.year + (as_of.month == 12):04d}-{(as_of.month % 12) + 1:02d}"
    ):
        raise ValueError("Next calendar month inconsistent with research period")
    sha = report.get("model", {}).get("sha256", "")
    if not isinstance(sha, str) or not _SHA256_PATTERN.fullmatch(sha):
        raise ValueError("Missing valid SHA256 model identifier")
    if report.get("model", {}).get("score_semantics") != "ordinal_relative_rank_utility_not_return":
        raise ValueError("Cannot interpret this model's scores as ordinal ranks")
    if report.get("report_status") not in ("REVIEW_REQUIRED", "READY_FOR_RESEARCH_REVIEW"):
        raise ValueError("Unknown monthly research review status")
    for x in ("top_20", "top_50"):
        if not isinstance(report.get(x, {}).get("candidates"), list):
            raise ValueError("Missing stock candidates in monthly report")
    if report["top_50"].get("requested") != 50:
        raise ValueError("Top50 must be actual top-50 ranking")
    if report["top_20"].get("requested") != 20:
        raise ValueError("Top20 must be actual top-20 ranking")
    if report["top_50"].get("returned") != len(report["top_50"]["candidates"]):
        raise ValueError("Top50 returned count mismatches candidates")
    if report["top_20"].get("returned") != len(report["top_20"]["candidates"]):
        raise ValueError("Top20 returned count mismatches candidates")


def build_alpha_eligibility(
    monthly_report: dict[str, Any],
    *,
    monthly_report_sha256: str,
    top_percent: float = 10.0,
    max_ranked_names: int = 50,
    minimum_scored_names: int = 500,
    minimum_listing_coverage_pct: float = 90.0,
    maximum_model_age_days: int = 180,
) -> dict[str, Any]:
    """Define a research candidate allowlist; fail-closed on poor data.

    Example: 5000 scored stocks, top_percent=10, cap=50 -> top 50.
    Example: 38 scored stocks -> ZERO eligible by default: insufficient scale
    and October 2026 V2 training is stale. No backdoor flag to bypass these
    safeguards in automated workflows; relaxed research thresholds still
    cannot turn this output into trade execution authorization.
    """
    _validate_monthly(monthly_report)
    if isinstance(top_percent, bool) or not isinstance(top_percent, (int, float)) or not (
        isfinite(top_percent) and 0 < top_percent <= 100
    ):
        raise ValueError("top-percent must be a finite number in (0, 100]")
    _positive_integer(max_ranked_names, "max-ranked-names")
    if max_ranked_names > 50:
        raise ValueError("max-ranked-names cannot exceed the published Top50")
    _positive_integer(minimum_scored_names, "minimum-scored-names")
    if (isinstance(minimum_listing_coverage_pct, bool)
        or not isinstance(minimum_listing_coverage_pct, (int, float))
        or not isfinite(minimum_listing_coverage_pct)
        or not 0 <= minimum_listing_coverage_pct <= 100):
        raise ValueError("minimum-listing-coverage-pct must be 0..100")
    if (isinstance(maximum_model_age_days, bool)
        or not isinstance(maximum_model_age_days, int)
        or maximum_model_age_days < 0):
        raise ValueError("maximum-model-age-days must be nonnegative")
    if not _SHA256_PATTERN.fullmatch(str(monthly_report_sha256)):
        raise ValueError("Invalid monthly report SHA256")

    data = monthly_report["data_quality"]
    model = monthly_report["model"]
    scored = int(data["scored_at_month_end"])
    model_age = int(model["training_age_days"])
    coverage = data["listed_market_coverage_pct"]
    issues: list[str] = []
    if monthly_report["report_status"] != "READY_FOR_RESEARCH_REVIEW":
        issues.append("UPSTREAM_MONTHLY_REVIEW_REQUIRED")
        issues.extend(f"UPSTREAM: {reason}" for reason in monthly_report.get("review_reasons", []))
    if scored < minimum_scored_names:
        issues.append(f"INSUFFICIENT_SCORED_NAMES: {scored} < {minimum_scored_names}")
    if coverage is None:
        issues.append("UNVERIFIED_MARKET_SCOPE: listing manifest not provided")
    elif not isfinite(float(coverage)) or float(coverage) < minimum_listing_coverage_pct:
        issues.append(
            f"INSUFFICIENT_LISTING_COVERAGE: {coverage}% < {minimum_listing_coverage_pct}%"
        )
    if model_age < 0 or model_age > maximum_model_age_days:
        issues.append(f"STALE_MODEL: trained {model_age} days before this month end")
    if scored <= 0:
        issues.append("NO_VALID_SCORED_STOCKS")

    desired = min(
        max_ranked_names,
        max(1, ceil(scored * top_percent / 100.0)) if scored > 0 else 0,
    )
    all_candidates = monthly_report["top_50"]["candidates"]
    if len(all_candidates) < desired:
        issues.append(f"INCOMPLETE_TOP_RANKING: have {len(all_candidates)}, need {desired}")

    included: list[dict[str, Any]] = []
    # Refuse partial tied groups, rather than arbitrarily choosing the
    # alphabetically-first security from equally-scored candidates.
    if desired and len(all_candidates) >= desired:
        edge = all_candidates[desired - 1]
        edge_rank = _positive_integer(edge.get("rank"), "edge rank")
        edge_tie = _positive_integer(edge.get("tie_count"), "edge tie count")
        if edge_rank + edge_tie - 1 > desired:
            issues.append("AMBIGUOUS_THRESHOLD_BOUNDARY: cutoff intersects a tied rank group")
        if not issues:
            seen: set[str] = set()
            previous_score = float("inf")
            previous_rank = 0
            for row in all_candidates[:desired]:
                name = row.get("symbol")
                if not isinstance(name, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,12}", name):
                    raise ValueError("Invalid stock ticker in ranking report")
                if name in seen:
                    raise ValueError("Duplicate candidate ticker")
                seen.add(name)
                raw_score = float(row["ranking_score"])
                rank = _positive_integer(row.get("rank"), "rank")
                if not isfinite(raw_score) or raw_score > previous_score or rank < previous_rank:
                    raise ValueError("Invalid ranking score order")
                previous_score, previous_rank = raw_score, rank
                included.append({
                    "symbol": name,
                    "rank": rank,
                    "ranking_score": raw_score,
                    "rank_percentile": round(
                        100 * (1 - (rank - 1) / scored), 4
                    ),
                    "model_score_is_return_forecast": False,
                })

    # Never claim that this research output alone authorizes a broker order.
    status = "RESEARCH_CANDIDATES_AVAILABLE" if not issues else "BLOCKED_DATA_OR_MODEL"
    if issues:
        included = []

    return {
        "schema": GATE_SCHEMA,
        "month": monthly_report["month"],
        "valid_for_month": monthly_report["next_calendar_month"],
        "as_of_nyse_session": monthly_report["as_of_nyse_session"],
        "monthly_report_sha256": monthly_report_sha256,
        "model_sha256": model["sha256"],
        "eligibility_rule": {
            "top_percent_of_scored": float(top_percent),
            "max_ranked_names": max_ranked_names,
            "minimum_scored_names": minimum_scored_names,
            "minimum_listing_coverage_pct": float(minimum_listing_coverage_pct),
            "maximum_model_age_days": maximum_model_age_days,
            "requested_maximum_eligible": desired,
        },
        "source_data_quality": data,
        "source_report_status": monthly_report["report_status"],
        "research_eligibility_status": status,
        "blocked_reasons": issues,
        "eligible_count": len(included),
        "new_or_increase_research_candidates": included,
        "eligible_symbols": [row["symbol"] for row in included],
        "new_and_increase_require_allowlist_membership": True,
        "reduce_and_close_remain_outside_alpha_filter": True,
        "research_only": True,
        "automated_handoff_approved": False,
        "live_execution_enabled": False,
        "order_instructions": [],
        "note": (
            "RESEARCH CANDIDATE UNIVERSE ONLY: passing this gate is not "
            "authorization to trade. Existing quant strategy and risk controls "
            "must independently validate all actions."
        ),
    }


def research_action_check(
    gate: dict[str, Any],
    *,
    symbol: str,
    action: str,
    decision_date: str,
) -> dict[str, Any]:
    """Hypothetical quant-facing check; non-broker decision data ONLY.

    New/increased exposure must be in a fresh, passing alpha candidate set.
    Closing/reducing exposure is NOT restricted by the alpha screen:
    protective liquidation must not become impossible when alpha falls.
    """
    if gate.get("schema") != GATE_SCHEMA:
        raise ValueError("Unsupported alpha eligibility gate schema")
    if gate.get("live_execution_enabled") is not False or gate.get("research_only") is not True:
        raise ValueError("Only research-only gates can be checked")
    if gate.get("automated_handoff_approved") is not False:
        raise ValueError("A research gate may not authorize portfolio handoff")
    if gate.get("order_instructions") != []:
        raise ValueError("A research gate may not include order instructions")
    action = action.strip().upper()
    symbol = symbol.strip().upper()
    if action not in STOCK_ACTIONS:
        raise ValueError(f"Unknown action {action}; expected OPEN/INCREASE/HOLD/REDUCE/CLOSE")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,12}", symbol):
        raise ValueError("Invalid symbol")
    when = date.fromisoformat(decision_date)
    valid_for_month = when.strftime("%Y-%m") == gate.get("valid_for_month")
    # Validate identity and counts, but no claim of cryptographic authenticity:
    # untrusted edited files must not become authorization for trade execution.
    names = gate.get("eligible_symbols", [])
    if not isinstance(names, list) or len(names) != len(set(names)):
        raise ValueError("Duplicate or invalid candidate universe")
    if gate.get("eligible_count") != len(names):
        raise ValueError("Candidate count mismatches allowlist")
    if gate.get("research_eligibility_status") != "RESEARCH_CANDIDATES_AVAILABLE":
        names = []
    if gate.get("blocked_reasons"):
        names = []

    if action in ("REDUCE", "CLOSE"):
        result = "NOT_RESTRICTED_BY_ALPHA_GATE"
        rationale = "Risk-controlled exposure reductions or closures cannot be blocked by stock-selection rankings"
        allowed_for_alpha_research = True
    elif action == "HOLD":
        result = "EXISTING_POSITION_NO_ALPHA_ENTRY_CHECK"
        rationale = "Holding is not a new exposure; separate risk checks still apply"
        allowed_for_alpha_research = True
    elif not valid_for_month:
        result = "DENY_OUTDATED_MONTHLY_ALPHA"
        rationale = "Alpha selection only applies to the month immediately following its decision month"
        allowed_for_alpha_research = False
    elif symbol not in names:
        result = "DENY_NOT_IN_ELIGIBLE_UNIVERSE"
        rationale = "New or increased exposure requires membership in a passing monthly research allowlist"
        allowed_for_alpha_research = False
    else:
        result = "ELIGIBLE_FOR_SEPARATE_QUANT_REVIEW"
        rationale = "Passes ALPHA UNIVERSE ONLY; not position sizing, broker/trade or risk approval"
        allowed_for_alpha_research = True
    return {
        "symbol": symbol,
        "action": action,
        "decision_date": decision_date,
        "gate_month": gate["valid_for_month"],
        "alpha_research_candidate": allowed_for_alpha_research,
        "result": result,
        "rationale": rationale,
        "actual_trade_approval": False,
        "live_execution_enabled": False,
    }


def build_from_file(monthly_report_path: Path, **kwargs: Any) -> dict[str, Any]:
    payload = monthly_report_path.read_bytes()
    report = json.loads(payload)
    if not isinstance(report, dict):
        raise ValueError("Monthly report must be a JSON object")
    return build_alpha_eligibility(
        report, monthly_report_sha256=hashlib.sha256(payload).hexdigest(), **kwargs
    )


def write_eligibility_json(
    gate: dict[str, Any], output_path: Path, *, overwrite: bool = False
) -> str:
    """Write dated private research artifact; never silently overwrite."""
    if gate.get("schema") != GATE_SCHEMA or gate.get("live_execution_enabled") is not False:
        raise ValueError("Invalid alpha eligibility report")
    if output_path.exists() and not overwrite:
        raise ValueError("Eligibility report exists; use --overwrite to replace research snapshot")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_suffix(output_path.suffix + ".part")
    try:
        temp.write_text(
            json.dumps(gate, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temp.replace(output_path)
    finally:
        temp.unlink(missing_ok=True)
    return str(output_path)
