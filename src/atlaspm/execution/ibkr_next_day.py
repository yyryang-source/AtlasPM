"""Check refreshed Paper positions, quotes and broker costs for a reviewed close signal."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from atlaspm.execution.ibkr_equity_snapshot import collect_cash_equity_snapshot
from atlaspm.execution.ibkr_whatif import PreviewTransport, _require_paper, _ticket, broker_estimate, contract_scope, live_quotes
from atlaspm.execution.ibkr_gateway import DEFAULT_GATEWAY_URL
from atlaspm.rebalance_workbench import RunStore, _json, next_session_window, rerisk_targets


ACCOUNT_FIELDS = ("positions", "open_orders", "contracts", "settled_cash", "available_cash", "total_cash")


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _verify_original(original: dict, account_id: str, observed: datetime) -> None:
    if (original["status"] != "reviewed" or original.get("parent_id")
            or not original["input_sha256"].get("market_audit")
            or original.get("account_id_sha256") != hashlib.sha256(account_id.encode()).hexdigest()):
        raise ValueError("The reviewed, market-audited close draft must belong to this account")
    session, opens, closes = next_session_window(date.fromisoformat(original["as_of"]))
    if observed.tzinfo is None or not opens <= observed < closes:
        raise ValueError(f"Run next-day checks during the regular NYSE session {session}")


def _risk_at_broker_cost(report: dict, estimates: list[dict]) -> None:
    def check(name: str) -> dict:
        return next(item["detail"] for item in report["checks"] if item["name"] == name)

    fees = sum((Decimal(str(row["commission"])) for row in estimates), Decimal(0))
    buys = sum((Decimal(str(row["ticket"]["price"])) * row["ticket"]["quantity"]
                for row in estimates if row["ticket"]["side"] == "BUY"), Decimal(0))
    initial_delta, maintenance_delta = (
        sum((max(Decimal(0), Decimal(str(item[field]["change"]))) for item in estimates), Decimal(0))
        for field in ("initial_margin", "maintenance_margin")
    )
    available = Decimal(str(check("现金与费用缓冲")["可用现金下界"]))
    requirements = max(buys + fees, initial_delta + fees, maintenance_delta + fees)
    report["checks"].append({
        "name": "券商费用与资金", "passed": available >= requirements,
        "detail": {
            "可用现金下界": float(available), "拟买入限价金额": float(buys),
            "预估佣金": float(fees), "初始保证金正增量之和": float(initial_delta),
            "维持保证金正增量之和": float(maintenance_delta), "资金需求上界": float(requirements),
        },
    })
    equity = Decimal(str(check("账户净值参考"))) - fees
    gross = sum((Decimal(str(row["close"])) * (
        row["held"] + row["pending_buy"] + max(0, row["delta"])
    ) for row in report["positions"]), Decimal(0))
    largest = max((Decimal(str(row["close"])) * (
        row["held"] + row["pending_buy"] + max(0, row["delta"])
    ) for row in report["positions"]), default=Decimal(0))
    policy = report["policy"]
    stressed = gross * Decimal(str(policy["shock"]))
    passed = (equity > 0 and gross <= equity
              and largest <= equity * Decimal(str(policy["max_weight"]))
              and stressed <= equity * Decimal(str(policy["max_stress_loss"])))
    report["checks"].append({
        "name": "计入券商佣金后的组合风险", "passed": passed,
        "detail": {"费用后净值参考": float(equity), "最大总敞口": float(gross),
                   "最大单股敞口": float(largest), "压力损失": float(stressed)},
    })


def _confirm_preview_inputs(transport, report: dict, snapshot: dict, *, now: datetime | None) -> None:
    try:
        latest = collect_cash_equity_snapshot(
            transport, account_id=snapshot["account_id"], dedicated=True, now=now,
        )
        captured = datetime.fromisoformat(latest["captured_at"])
        finished = now or datetime.now(timezone.utc)
        if captured.tzinfo is None or not 0 <= (finished - captured).total_seconds() <= 60:
            raise ValueError("券商预览后的账户快照已过期或时间无效")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["checks"].append({"name": "券商预览后账户复核", "passed": False, "detail": str(exc)})
        return
    changes = [field for field in ACCOUNT_FIELDS if latest.get(field) != snapshot.get(field)]
    if latest.get("account_id") != snapshot["account_id"] or latest.get("is_paper") is not True:
        changes.append("Paper 账户身份")
    report["account_confirmed_at"] = latest["captured_at"]
    report["input_sha256"]["next_day_account_confirmation"] = _sha(latest)
    report["checks"].append({"name": "券商预览后账户复核", "passed": not changes,
                             "detail": changes or "持仓、挂单及现金与预览输入一致"})

    report["observed_at"] = finished.isoformat()
    _, _, close = next_session_window(date.fromisoformat(report["as_of"]))
    stale = sorted(symbol for symbol, quote in report["next_day_quotes"].items()
                   if finished.timestamp() - quote["updated_epoch"] > 30)
    report["checks"].append({"name": "券商预览后报价与交易时段", "passed": finished < close and not stale,
                             "detail": {"仍在交易时段": finished < close, "报价过期": stale}})


def next_day_review(
    transport, original: dict, account_id: str, mapping: dict[str, int],
    *, now: datetime | None = None, reviewed_limits: dict[tuple[str, str], Decimal] | None = None,
) -> dict:
    _verify_original(original, account_id, now or datetime.now(timezone.utc))
    _require_paper(transport, account_id)
    snapshot = collect_cash_equity_snapshot(transport, account_id=account_id, dedicated=True, now=now)
    symbols = set(original["target_weights"]) | snapshot["positions"].keys() | {
        order["symbol"] for order in snapshot["open_orders"]
    }
    contracts = contract_scope(transport, snapshot, mapping, symbols)
    quotes = live_quotes(transport, contracts, now=now)
    observed = now or datetime.now(timezone.utc)
    report = rerisk_targets(original, snapshot, quotes, now=observed, reviewed_limits=reviewed_limits)
    report["policy"] = original["policy"]
    report["input_sha256"] = original["input_sha256"] | {
        "next_day_account": _sha(snapshot), "next_day_quotes": _sha(quotes), "next_day_contracts": _sha(contracts),
    }
    report["observed_at"] = observed.isoformat()
    report["next_day_snapshot"] = {key: value for key, value in snapshot.items() if key != "account_id"}
    report["next_day_quotes"] = quotes
    report["next_day_contracts"] = contracts
    report["broker_estimates"] = []
    if report["status"] == "review":
        # Client IDs are stable for the same proposed order in this
        # rebalance. Changing side, size or price produces another ID.
        for row in report["draft"]:
            ticket = _ticket(original["id"], row, contracts[row["symbol"]], Decimal(str(row["limit_price"])))
            try:
                estimate = broker_estimate(transport, account_id, ticket)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report["checks"].append({"name": "券商 What-If", "passed": False,
                                         "detail": f"{row['symbol']}: {exc}"})
                continue
            report["broker_estimates"].append({"symbol": row["symbol"], "ticket": ticket} | estimate)
        report["checks"].append({
            "name": "券商预览覆盖", "passed": len(report["broker_estimates"]) == len(report["draft"]),
            "detail": {"已预览": len(report["broker_estimates"]), "应预览": len(report["draft"])},
        })
        _risk_at_broker_cost(report, report["broker_estimates"])
        if report["draft"] and all(item["passed"] for item in report["checks"]):
            _confirm_preview_inputs(transport, report, snapshot, now=now)
    old = {(row["symbol"], row["side"]): row for row in original["draft"]}
    new = {(row["symbol"], row["side"]): row for row in report["draft"]}
    changes = []
    for key in sorted(old.keys() | new.keys()):
        prior, refreshed = old.get(key, {}), new.get(key, {})
        prior_price = prior.get("limit_price", prior.get("reference_close"))
        if (prior.get("shares"), prior_price) != (refreshed.get("shares"), refreshed.get("limit_price")):
            changes.append({"symbol": key[0], "side": key[1], "previous_shares": prior.get("shares"),
                            "new_shares": refreshed.get("shares"), "previous_price": prior_price,
                            "new_limit_price": refreshed.get("limit_price")})
    report["draft_changes"] = changes
    if any(not item["passed"] for item in report["checks"]):
        report.update(status="blocked", draft=[])
    report["id"] = _sha(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--contracts", type=Path, help="Optional JSON mapping of symbols to verified conids")
    parser.add_argument("--url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--ca-file")
    parser.add_argument("--allow-paper-preview", action="store_true", required=True)
    args = parser.parse_args()
    try:
        store = RunStore(Path("local_data/rebalance/workbench.sqlite3"))
        original = store.get(args.run)
        if original is None:
            raise ValueError("Original run not found")
        mapping = json.loads(args.contracts.read_text(encoding="utf-8")) if args.contracts else {}
        if not isinstance(mapping, dict):
            raise ValueError("Contract mapping must be a JSON object")
        transport = PreviewTransport(args.url, ca_file=args.ca_file)
        report = next_day_review(transport, original, args.account, mapping)
        run_id = store.record_next_day(report)
        print(_json({"id": run_id, "status": report["status"], "execution_session": report["execution_session"],
                     "changes": len(report["draft_changes"]), "orders": len(report["draft"])}))
        return 2 if report["status"] == "blocked" else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(_json({"status": "blocked", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
