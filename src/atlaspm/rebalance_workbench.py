"""Local after-close equity rehearsal. Produces review records, never orders."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from atlaspm.historical import load_adjusted_prices
from atlaspm.quant.stock_allocation import (
    StockAllocationSettings,
    optimize_stock_allocation,
    prepare_stock_allocation,
)
from atlaspm.selection.stock_screener import screen_stocks_from_prices


@dataclass(frozen=True)
class Policy:
    top: int = 4
    allocation: str = "equal"
    invested_fraction: float = .60
    max_weight: float = .30
    turnover_limit: float = .75
    shock: float = .20
    max_stress_loss: float = .15
    fee_buffer_bps: int = 10

    def __post_init__(self) -> None:
        if type(self.top) is not int or not 1 <= self.top <= 100:
            raise ValueError("选股数量应为 1 至 100")
        if self.allocation not in {"equal", "quant"}:
            raise ValueError("未知仓位计算方式")
        if any(type(value) not in {float, int} or not 0 < value <= 1 for value in (
            self.invested_fraction, self.max_weight, self.shock, self.max_stress_loss
        )):
            raise ValueError("仓位、集中度和压力参数应大于 0 且不超过 1")
        if (type(self.turnover_limit) not in {float, int} or not 0 < self.turnover_limit <= 2
                or type(self.fee_buffer_bps) is not int or not 0 <= self.fee_buffer_bps <= 1000):
            raise ValueError("换手率或费用缓冲超出范围")


@lru_cache(maxsize=16)
def exchange_window(day: date) -> tuple[tuple[date, ...], datetime]:
    import pandas_market_calendars as calendars

    schedule = calendars.get_calendar("NYSE").schedule(day - timedelta(days=410), day)
    if schedule.empty or schedule.index[-1].date() != day:
        raise ValueError(f"{day} 不是 NYSE 交易日")
    return tuple(stamp.date() for stamp in schedule.index), (
        schedule.iloc[-1]["market_close"].to_pydatetime()
    )


def latest_completed_session(now: datetime | None = None) -> date:
    import pandas_market_calendars as calendars

    now = now or datetime.now(timezone.utc)
    calendar = calendars.get_calendar("NYSE")
    local_day = now.astimezone(calendar.tz).date()
    schedule = calendar.schedule(local_day - timedelta(days=14), local_day)
    completed = schedule[schedule["market_close"] <= now]
    return completed.index[-1].date()


def _object(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} 应为 JSON 对象")
    return value


def _symbol(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", value):
        raise ValueError(f"无效股票代码 {value!r}")
    return value


def _amount(value: object, name: str, *, positive: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(f"{name} 缺少有效金额") from exc
    if not amount.is_finite() or not 0 <= amount <= 10**12 or (positive and not amount):
        raise ValueError(f"{name} 金额超出范围")
    return amount


def _shares(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 10**9:
        raise ValueError(f"{name} 应为非负的整数股数")
    return value


def _read_json(data: bytes) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 字段重复 {key}")
            result[key] = value
        return result

    return _object(json.loads(data, object_pairs_hook=unique_object), "输入")


def _reference_prices(data: bytes, day: date) -> dict[str, Decimal]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    if not {"date", "symbol", "close"}.issubset(reader.fieldnames or []):
        raise ValueError("订单参考价 CSV 需要 date,symbol,close（未复权收盘价）")
    prices = {}
    for row in reader:
        if date.fromisoformat(row["date"]) != day:
            continue
        symbol = _symbol(row["symbol"])
        if symbol in prices:
            raise ValueError(f"{symbol} 同日参考价重复")
        prices[symbol] = _amount(row["close"], f"{symbol} 收盘价", positive=True)
    return prices


def _account(
    raw: dict, *, closed_at: datetime, observed_at: datetime
) -> tuple[Decimal, Decimal, dict[str, int], list[dict]]:
    if raw["currency"] != "USD" or raw["account_type"] != "cash":
        raise ValueError("当前只支持 USD 现金账户的美股整数多头仓位")
    if raw.get("positions_and_orders_complete") is not True:
        raise ValueError("账户快照必须包含全部持仓及未完成订单")
    if raw.get("managed_equities_only") is not True:
        raise ValueError("账户快照必须确认所有持仓均为本策略管理的美股")
    if not isinstance(raw.get("account_id"), str) or not raw["account_id"].strip():
        raise ValueError("账户快照缺少账户标识")
    captured_at = datetime.fromisoformat(raw["captured_at"])
    if captured_at.tzinfo is None or not closed_at <= captured_at <= observed_at:
        raise ValueError("账户快照应在决策日收盘后采集，并且不得晚于运行时间")
    if captured_at - closed_at > timedelta(hours=4):
        raise ValueError("账户快照与决策日收盘相隔超过 4 小时")
    settled = _amount(raw["settled_cash"], "已结算现金")
    available = _amount(raw["available_cash"], "账户可用现金")
    positions = {
        _symbol(sym): _shares(qty, f"{sym} 持仓")
        for sym, qty in _object(raw["positions"], "持仓").items()
    }
    orders = raw["open_orders"]
    if not isinstance(orders, list):
        raise ValueError("open_orders 应为数组")
    ids = set()
    for value in orders:
        order = _object(value, "挂单")
        order_id = order["id"]
        if not isinstance(order_id, str) or not order_id or order_id in ids:
            raise ValueError("挂单编号缺失或重复")
        ids.add(order_id)
        _symbol(order["symbol"])
        if order["side"] not in {"BUY", "SELL"} or not _shares(order["remaining"], "挂单剩余股数"):
            raise ValueError("挂单需提供买卖方向和未成交股数")
        _amount(order["limit_price"], "挂单限价", positive=True)
    return settled, available, positions, orders


def _plan(
    day: date, scope: bytes, adjusted: bytes, references: bytes, snapshot: bytes,
    policy: Policy, *, demo: bool,
) -> dict:
    result: dict[str, Any] = {
        "status": "blocked", "mode": "demo" if demo else "import",
        "as_of": day.isoformat(), "checks": [], "draft": [],
        "scope_source": None, "account_source": None,
    }

    def check(name: str, passed: bool, detail: object) -> bool:
        result["checks"].append({"name": name, "passed": passed, "detail": detail})
        return passed

    try:
        sessions, closed_at = exchange_window(day)
        observed_at = datetime.now(timezone.utc)
        if not check("收盘时间", observed_at >= closed_at, closed_at.isoformat()):
            return result
        universe = _read_json(scope)
        if universe["as_of"] != day.isoformat():
            raise ValueError("股票池快照日期与决策日不一致")
        names = universe["symbols"]
        if not isinstance(names, list) or not names:
            raise ValueError("股票池需要非空的 symbols 数组")
        symbols = {_symbol(sym) for sym in names}
        if len(names) != len(symbols):
            raise ValueError("股票池包含重复股票代码")
        result["scope_source"] = universe["source"]
        if not isinstance(universe["source"], str) or not universe["source"].strip():
            raise ValueError("股票池缺少来源")
        with TemporaryDirectory() as directory:
            prices_path = Path(directory) / "adjusted.csv"
            prices_path.write_bytes(adjusted)
            history = load_adjusted_prices(prices_path)
        refs = _reference_prices(references, day)
        trailing = set(sessions[-253:])
        missing_close = sorted(sym for sym in symbols if day not in history.get(sym, {}))
        missing_history = sorted(sym for sym in symbols if not trailing <= history.get(sym, {}).keys())
        missing_ref = sorted(symbols - refs.keys())
        data_ready = check("行情覆盖", not (missing_close or missing_history or missing_ref), {
            "股票池数量": len(symbols), "缺决策日复权收盘价": missing_close,
            "缺最近 253 个交易日行情": missing_history, "缺未复权收盘价": missing_ref,
        })
        if not data_ready:
            return result
        history = {sym: {d: history[sym][d] for d in trailing} for sym in symbols}
        screen = screen_stocks_from_prices(history, day.isoformat(), top=policy.top, require_decision_close=True)
        picks = screen["selected"]
        result["candidates"] = picks
        result["excluded"] = screen["excluded"]
        if not check("选股", bool(picks), f"入选 {len(picks)} 只"):
            return result
        account = _read_json(snapshot)
        cash, reported_available, holdings, pending = _account(
            account, closed_at=closed_at, observed_at=observed_at,
        )
        all_symbols = symbols | holdings.keys() | {order["symbol"] for order in pending}
        missing_account_marks = sorted(all_symbols - refs.keys())
        if not check("账户参考价", not missing_account_marks, missing_account_marks):
            return result
        result["account_source"] = "synthetic_fixture" if demo else "user_imported_snapshot"
        result["snapshot_at"] = account["captured_at"]
        effective = defaultdict(int, holdings)
        buys: dict[str, int] = defaultdict(int)
        sells: dict[str, int] = defaultdict(int)
        fee_rate = Decimal(policy.fee_buffer_bps) / 10000
        reserve = Decimal(0)
        for order in pending:
            sym, qty = order["symbol"], order["remaining"]
            if order["side"] == "BUY":
                buys[sym] += qty
                effective[sym] += qty
                reserve += qty * Decimal(str(order["limit_price"])) * (1 + fee_rate)
            else:
                sells[sym] += qty
                effective[sym] -= qty
        oversold = sorted(sym for sym, quantity in sells.items() if quantity > holdings.get(sym, 0))
        check("卖出挂单库存", not oversold, oversold)
        cash_after_reserves = min(cash - reserve, reported_available)
        equity = cash + sum(qty * refs[sym] for sym, qty in holdings.items())
        if not check("账户净值参考", equity > 0, float(equity)):
            return result

        if policy.allocation == "quant":
            # The optimizer allocates a sleeve. Convert account weights into
            # sleeve weights and scale optimized targets back to account weights.
            settings = StockAllocationSettings(
                max_weight=min(1., policy.max_weight / policy.invested_fraction),
                turnover_limit=min(2., policy.turnover_limit / policy.invested_fraction),
            )
            sleeve = equity * Decimal(str(policy.invested_fraction))
            current = {sym: float(qty * refs[sym] / sleeve) for sym, qty in effective.items() if qty > 0}
            signal = prepare_stock_allocation(picks, history, list(sessions[-64:]), settings)
            if signal is None:
                raise ValueError("Quant 协方差缺少连续行情")
            weights, reason = optimize_stock_allocation(
                signal, current, set(signal.symbols), settings,
                cost_rate=float(fee_rate), holding_sessions=21,
            )
            if not check("Quant 求解", weights is not None and any(weights.values()), reason):
                return result
            target_weights = {
                sym: min(policy.max_weight, max(0., weight * policy.invested_fraction))
                for sym, weight in weights.items()
            }
        else:
            weight = min(policy.max_weight, policy.invested_fraction / len(picks))
            target_weights = {row["symbol"]: weight for row in picks}
        result["target_weights"] = target_weights
        rows, draft, conflicts, targets = [], [], [], {}
        for sym in sorted(target_weights.keys() | holdings.keys() | effective.keys()):
            mark = refs[sym]
            target = int(equity * Decimal(str(target_weights.get(sym, 0.))) / mark)
            delta = target - effective[sym]
            targets[sym] = target
            rows.append({
                "symbol": sym, "held": holdings.get(sym, 0), "pending_buy": buys[sym],
                "pending_sell": sells[sym], "effective": effective[sym],
                "target": target, "delta": delta, "close": float(mark),
            })
            if (delta > 0 and sells[sym]) or (delta < 0 and buys[sym]) or (buys[sym] and sells[sym]):
                conflicts.append(sym)
            if delta:
                notional = abs(delta) * mark
                draft.append({
                    "symbol": sym, "side": "BUY" if delta > 0 else "SELL",
                    "shares": abs(delta), "reference_close": float(mark),
                    "notional": float(notional), "fee_buffer": float(notional * fee_rate),
                })
        result["positions"] = rows
        if not check("持仓目标", any(targets.values()), "目标组合需至少持有一只入选股"):
            return result
        # Check worst direction separately. Pending sales cannot fund buys
        # or supply shares to a draft sell, and buys may fill before sells.
        buy_cost = sum(max(row["delta"], 0) * refs[row["symbol"]] * (1 + fee_rate) for row in rows)
        sell_fee = sum(max(-row["delta"], 0) * refs[row["symbol"]] * fee_rate for row in rows)
        worst_values = [
            (row["held"] + row["pending_buy"] + max(row["delta"], 0)) * refs[row["symbol"]]
            for row in rows
        ]
        stress = sum(worst_values) / equity * Decimal(str(policy.shock))
        max_position = max(worst_values, default=Decimal(0)) / equity
        turnover = sum(abs(row["delta"]) * refs[row["symbol"]] for row in rows) / equity
        check("挂单方向冲突", not conflicts, conflicts)
        check("现金与费用缓冲", buy_cost + sell_fee <= cash_after_reserves, {
            "挂单预留": float(reserve), "可用现金下界": float(cash_after_reserves),
            "新单买入及费用缓冲": float(buy_cost + sell_fee),
        })
        check("单股集中度", max_position <= Decimal(str(policy.max_weight)), {
            "最坏情形": float(max_position), "上限": policy.max_weight,
        })
        check("草稿换手率", turnover <= Decimal(str(policy.turnover_limit)), {
            "换手率": float(turnover), "上限": policy.turnover_limit,
        })
        check("股票下跌压力测试", stress <= Decimal(str(policy.max_stress_loss)), {
            "冲击": policy.shock, "净值损失": float(stress), "损失上限": policy.max_stress_loss,
        })
        if all(item["passed"] for item in result["checks"]):
            result["status"] = "review"
            result["draft"] = draft
        return result
    except (ValueError, TypeError, KeyError, ArithmeticError, OSError, csv.Error) as exc:
        check("输入或计算错误", False, str(exc))
        return result


def demo_inputs() -> tuple[date, bytes, bytes, bytes, bytes]:
    """Deterministic fictional equities and a partially filled order."""
    day = date(2024, 12, 31)
    sessions, close = exchange_window(day)
    symbols = ["ALFA", "BRAV", "CHAR", "DELT", "ECHO"]
    adjusted = io.StringIO()
    raw = io.StringIO()
    writer = csv.writer(adjusted)
    writer.writerow(["date", "symbol", "adjusted_close"])
    marks = csv.writer(raw)
    marks.writerow(["date", "symbol", "close"])
    for index, sym in enumerate(symbols):
        for n, session in enumerate(sessions[-253:]):
            value = 65 + index * 8 + n * (.08 + index * .03) + ((n + index) % 7 - 3) * .13
            writer.writerow([session, sym, f"{value:.4f}"])
        marks.writerow([day, sym, f"{value + (index + 1) * 2:.2f}"])
    scope = {"as_of": day.isoformat(), "symbols": symbols, "source": "synthetic_fixture"}
    account = {
        "account_id": "DEMO-CASH", "account_type": "cash", "currency": "USD",
        "captured_at": (close + timedelta(minutes=20)).isoformat(),
        "positions_and_orders_complete": True, "managed_equities_only": True,
        "settled_cash": 26000, "available_cash": 25000,
        "positions": {"ALFA": 16, "ECHO": 12},
        "open_orders": [{"id": "DEMO-1", "symbol": "ECHO", "side": "BUY", "remaining": 5, "limit_price": 160}],
    }
    return day, _json(scope).encode(), adjusted.getvalue().encode(), raw.getvalue().encode(), _json(account).encode()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class RunStore:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def _init(self, db: sqlite3.Connection) -> None:
        db.execute(
            """CREATE TABLE IF NOT EXISTS rebalance_runs (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, as_of TEXT NOT NULL,
                status TEXT NOT NULL, report TEXT NOT NULL,
                reviewed TEXT, review_note TEXT
            )"""
        )

    def run(self, day: date, scope: bytes, adjusted: bytes, references: bytes, snapshot: bytes,
            policy: Policy = Policy(), *, demo: bool = False) -> str:
        params = {"policy": asdict(policy), "mode": "demo" if demo else "import", "day": day.isoformat()}
        hashes = {
            name: hashlib.sha256(value).hexdigest()
            for name, value in zip(("universe", "adjusted", "references", "account"),
                                   (scope, adjusted, references, snapshot), strict=True)
        }
        key = hashlib.sha256(_json({"inputs": hashes, "params": params, "schema": 1}).encode()).hexdigest()
        with closing(self._connect()) as db, db:
            self._init(db)
            saved = db.execute("SELECT status FROM rebalance_runs WHERE id = ?", (key,)).fetchone()
            if saved and saved["status"] != "blocked":
                return key
            report = _plan(day, scope, adjusted, references, snapshot, policy, demo=demo)
            report.update(input_sha256=hashes, policy=params["policy"], id=key)
            db.execute(
                """INSERT INTO rebalance_runs (id, created, as_of, status, report) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status, report=excluded.report
                   WHERE rebalance_runs.status = 'blocked'""",
                (key, datetime.now(timezone.utc).isoformat(), day.isoformat(), report["status"], _json(report)),
            )
        return key

    def list(self) -> list[dict]:
        with closing(self._connect()) as db, db:
            self._init(db)
            return [dict(row) for row in db.execute(
                """SELECT id, as_of, status, created, reviewed FROM rebalance_runs
                   ORDER BY created DESC LIMIT 100"""
            )]

    def get(self, key: str) -> dict | None:
        with closing(self._connect()) as db, db:
            self._init(db)
            row = db.execute("SELECT * FROM rebalance_runs WHERE id = ?", (key,)).fetchone()
            if row is None:
                return None
            result = json.loads(row["report"])
            return result | {field: row[field] for field in ("status", "created", "reviewed", "review_note")}

    def acknowledge(self, key: str, note: str) -> bool:
        note = note.strip()
        if not note or len(note) > 1000:
            raise ValueError("请填写 1 至 1000 字的审核备注")
        with closing(self._connect()) as db, db:
            self._init(db)
            return db.execute(
                """UPDATE rebalance_runs SET status='reviewed', reviewed=?, review_note=?
                   WHERE id=? AND status='review'""",
                (datetime.now(timezone.utc).isoformat(), note, key),
            ).rowcount == 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("local_data/rebalance/workbench.sqlite3"))
    parser.add_argument("--demo", action="store_true")
    sessions = parser.add_mutually_exclusive_group()
    sessions.add_argument("--as-of", type=date.fromisoformat)
    sessions.add_argument("--latest-close", action="store_true")
    for name in ("universe", "adjusted", "references", "account"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--allocation", choices=["equal", "quant"], default="equal")
    args = parser.parse_args()
    files = (args.universe, args.adjusted, args.references, args.account)
    if args.demo:
        if args.as_of or args.latest_close or any(files):
            parser.error("--demo cannot be combined with input files or a decision date")
        inputs = demo_inputs()
    elif (args.as_of or args.latest_close) and all(files):
        day = args.as_of or latest_completed_session()
        inputs = (day, *(path.read_bytes() for path in files))
    else:
        parser.error("Specify --demo or choose --as-of / --latest-close with all four input files")
    store = RunStore(args.db)
    key = store.run(*inputs, Policy(allocation=args.allocation), demo=args.demo)
    status = store.get(key)["status"]
    print(_json({"id": key, "status": status}))
    return 2 if status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
