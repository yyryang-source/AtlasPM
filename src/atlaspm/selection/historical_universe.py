"""Select from complete, dated snapshots of a supplied stock universe."""

from __future__ import annotations

import csv
from bisect import bisect_right
from datetime import date
from pathlib import Path


DEFAULT_MAX_UNIVERSE_AGE_DAYS = 35


class HistoricalUniverse:
    def __init__(self, path: Path, max_age_days: int = DEFAULT_MAX_UNIVERSE_AGE_DAYS):
        if type(max_age_days) is not int or max_age_days < 0:
            raise ValueError("max-universe-age-days must be a non-negative integer")
        self._max_age_days = max_age_days
        snapshots: dict[date, set[str]] = {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not {"as_of_date", "symbol"}.issubset(reader.fieldnames):
                raise ValueError("Historical universe CSV needs: as_of_date,symbol")
            for number, row in enumerate(reader, 2):
                raw_date = (row["as_of_date"] or "").strip()
                try:
                    observed = date.fromisoformat(raw_date)
                except ValueError as exc:
                    raise ValueError(f"Invalid historical universe date at row {number}") from exc
                symbol = (row["symbol"] or "").strip().upper()
                if observed.isoformat() != raw_date or not symbol:
                    raise ValueError(f"Historical universe row {number} needs YYYY-MM-DD and a symbol")
                members = snapshots.setdefault(observed, set())
                if symbol in members:
                    raise ValueError(f"Duplicate historical universe member {symbol} on {observed}")
                members.add(symbol)
        if not snapshots:
            raise ValueError("Historical universe CSV has no snapshots")
        self._snapshots = snapshots
        self._dates = sorted(snapshots)

    def prices_at(
        self,
        prices: dict[str, dict[date, float]],
        decision: date,
        *,
        benchmark_symbol: str | None = None,
    ) -> tuple[date, dict[str, dict[date, float]]]:
        index = bisect_right(self._dates, decision)
        if not index:
            raise ValueError(f"No historical universe snapshot available by {decision}")
        snapshot = self._dates[index - 1]
        if (decision - snapshot).days > self._max_age_days:
            raise ValueError(f"Historical universe snapshot {snapshot} is stale for {decision}")

        members = sorted(self._snapshots[snapshot] - {benchmark_symbol})
        if not members:
            raise ValueError(f"No non-benchmark historical universe members on {decision}")
        missing = [symbol for symbol in members if decision not in prices.get(symbol, {})]
        if missing:
            raise ValueError(
                f"MISSING_UNIVERSE_DECISION_CLOSE: {len(missing)} member(s) on {decision}; "
                f"first missing: {', '.join(missing[:5])}"
            )
        return snapshot, {symbol: prices[symbol] for symbol in members}
