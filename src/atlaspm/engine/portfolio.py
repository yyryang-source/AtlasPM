"""Portfolio risk calculations and sample data parsing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from atlaspm.models import PortfolioAccount, Position, nonnegative_number


def parse_accounts(payload: Mapping[str, object]) -> list[PortfolioAccount]:
    raw_accounts = payload.get("accounts")
    if not isinstance(raw_accounts, list) or not raw_accounts:
        raise ValueError("'accounts' must be a nonempty list")

    result: list[PortfolioAccount] = []
    seen_names: set[str] = set()
    for raw in raw_accounts:
        if not isinstance(raw, dict):
            raise ValueError("Each account must be an object")
        raw_positions = raw.get("positions")
        if not isinstance(raw_positions, list):
            raise ValueError("Each account requires a 'positions' list")
        positions: list[Position] = []
        for item in raw_positions:
            if not isinstance(item, dict):
                raise ValueError("Each position must be an object")
            positions.append(
                Position(
                    symbol=item["symbol"],
                    market_value=item["market_value"],
                    maintenance_requirement=item.get("maintenance_requirement", 0.30),
                )
            )
        account = PortfolioAccount(
            name=raw["name"],
            account_type=raw["account_type"],
            cash=raw["cash"],
            debt=raw["debt"],
            positions=tuple(positions),
        )
        if account.name in seen_names:
            raise ValueError(f"Duplicate account name: {account.name}")
        seen_names.add(account.name)
        result.append(account)
    return result


def analyze_accounts(
    accounts: Sequence[PortfolioAccount],
    declines: Sequence[float] = (0.10, 0.20, 0.30),
    annual_margin_rate: float = 0.065,
) -> dict[str, object]:
    if not accounts:
        raise ValueError("At least one account is required")
    apr = nonnegative_number(annual_margin_rate, "annual_margin_rate")
    decline_values = [
        nonnegative_number(value, "decline") for value in declines
    ]
    if any(value > 1 for value in decline_values):
        raise ValueError("declines must be between 0 and 1")

    reports = []
    for account in accounts:
        reports.append(
            {
                "name": account.name,
                "account_type": account.account_type,
                "cash": account.cash,
                "gross_market_value": account.market_value,
                "borrowed": account.debt,
                "equity": account.equity,
                "gross_leverage": account.gross_leverage,
                "maintenance_required": account.maintenance_required,
                "maintenance_excess": account.maintenance_excess,
                "below_maintenance": account.maintenance_excess < 0,
                "annual_borrowing_cost_estimate": account.debt * apr,
                "stress_tests": [account.stress(value) for value in decline_values],
            }
        )

    total_equity = sum(account.equity for account in accounts)
    total_market_value = sum(account.market_value for account in accounts)
    total_cash = sum(account.cash for account in accounts)
    total_debt = sum(account.debt for account in accounts)

    return {
        "disclaimer": (
            "Hypothetical, simplified analysis only. Broker margin rules "
            "and liquidation thresholds differ. No trade instructions."
        ),
        "annual_margin_rate_assumption": apr,
        "summary": {
            "total_equity": total_equity,
            "total_cash": total_cash,
            "gross_market_value": total_market_value,
            "borrowed": total_debt,
            "gross_leverage": total_market_value / total_equity,
            "annual_borrowing_cost_estimate": total_debt * apr,
            "any_account_below_maintenance": any(
                account.maintenance_excess < 0 for account in accounts
            ),
        },
        "accounts": reports,
    }
