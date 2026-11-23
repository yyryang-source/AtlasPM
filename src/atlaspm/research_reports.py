"""Browser views of reports emitted by the existing AtlasPM research commands."""

from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st


COLORS = ["#08786C", "#2C5DAC", "#89642C", "#6C5399", "#AE515A"]
BACKTEST_SERIES = {
    "equity_index": "入选股票等权", "quant_equity_index": "同股 Quant", "universe_index": "股票池等权",
    "spy_index": "基准指数", "equal_weight": "共同股票池等权", "technical": "技术因子",
    "fundamental": "技术加基本面", "lightgbm": "LightGBM", "benchmark": "基准指数",
}
HEDGE_SERIES = {
    "stock_only": "股票", "stock_treasury": "股票 + 国债", "stock_treasury_put": "股票 + 国债 + 看跌期权",
}


def table(rows: list[dict], names: dict[str, str]) -> None:
    data = pd.DataFrame(rows)
    columns = [key for key in names if key in data]
    if not data.empty and columns:
        st.dataframe(data[columns].rename(columns=names), hide_index=True)


def curves(
    rows: list[dict], labels: dict[str, str], title: str, *,
    date_key: str = "date", drawdown: bool = False, ratio: bool = False,
) -> None:
    frame = pd.DataFrame(rows)
    keys = [key for key in labels if key in frame]
    if frame.empty or not keys or date_key not in frame:
        st.info("报告中没有可绘制的时间序列。")
        return
    frame = frame.sort_values(date_key)
    values = frame[keys].apply(pd.to_numeric, errors="coerce")
    if drawdown:
        values = values / values.cummax().clip(lower=1.0) - 1
    frame = values.rename(columns=labels).assign(日期=frame[date_key].astype(str).str[:10])
    points = frame.melt(id_vars="日期", var_name="序列", value_name="数值").dropna(subset=["数值"])
    axis_format = ".1%" if drawdown or ratio else ",.2f"
    chart = alt.Chart(alt.InlineData(values=points.to_dict("records"))).mark_line(strokeWidth=2).encode(
        x=alt.X("日期:T", title=None, scale=alt.Scale(type="utc")),
        y=alt.Y("数值:Q", title=title, scale=alt.Scale(zero=False), axis=alt.Axis(format=axis_format)),
        color=alt.Color("序列:N", scale=alt.Scale(domain=[labels[key] for key in keys], range=COLORS[:len(keys)]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("日期:T", format="%Y-%m-%d"), "序列:N", alt.Tooltip("数值:Q", format=axis_format)],
    ).properties(height=290, autosize=alt.AutoSizeParams(type="fit-x", contains="padding"))
    st.altair_chart(chart.interactive(), use_container_width=True)


def performance(items: dict[str, dict]) -> None:
    def percent(item: dict, key: str) -> str:
        return f"{item[key]:.2%}" if isinstance(item.get(key), (int, float)) else "—"

    st.dataframe([
        {"组合": label, "累计收益": percent(stats, "total_return"),
         "最大回撤": percent(stats, "max_drawdown"),
         "年化波动": percent(stats, "annual_volatility")}
        for label, stats in items.items()
    ], hide_index=True)


def screen(report: dict) -> None:
    ranked, selected, excluded = report["ranked"], report["selected"], report["excluded"]
    cols = st.columns(4)
    for col, label, value in zip(cols, ("决策日", "股票池", "符合筛选", "最终入选"),
                                 (report["as_of"], report["universe_count"], report["eligible_count"], len(selected)),
                                 strict=True):
        col.metric(label, value)
    picked = {row["symbol"] for row in selected}
    ranking, rejection = st.tabs(["排名", "排除原因"])
    with ranking:
        if ranked:
            table([row | {"picked": row["symbol"] in picked} for row in ranked], {
                "rank": "排名", "symbol": "股票", "score": "得分", "picked": "入选",
                "sector": "行业", "last_price_date": "最新行情", "fundamentals_as_of": "基本面可用日",
            })
        else:
            st.info("该日期没有满足选股规则的股票。")
    with rejection:
        if excluded:
            table(excluded, {"symbol": "股票", "reason": "排除原因"})
        else:
            st.caption("该次运行无排除项。")


def backtest(report: dict) -> None:
    if "strategies" in report:
        groups = {BACKTEST_SERIES[key]: value for key, value in report["strategies"].items()}
    else:
        groups = {
            BACKTEST_SERIES[key]: report[field] for key, field in
            (("equity_index", "portfolio"), ("universe_index", "equal_weight_universe")) if field in report
        }
        if "quant" in report:
            groups[BACKTEST_SERIES["quant_equity_index"]] = report["quant"]["portfolio"]
    groups[BACKTEST_SERIES["benchmark"]] = report["benchmark"]
    performance(groups)
    nav, dd, events = st.tabs(["净值", "回撤", "调仓记录"])
    with nav:
        curves(report["equity_curve"], BACKTEST_SERIES, "初始净值 = 1")
    with dd:
        curves(report["equity_curve"], BACKTEST_SERIES, "距历史峰值", drawdown=True)
    with events:
        table(report["decisions"], {
            "date": "决策日", "execution_date": "执行日", "eligible_count": "符合筛选",
            "universe_member_count": "股票池", "universe_snapshot_as_of": "股票池日期", "selected": "入选",
        })


def options(report: dict) -> None:
    replay = report.get("option_replay", report)
    for col, label, key in zip(st.columns(3), ("买入权利金", "卖出权利金", "交易费用"),
                               ("premium_paid_usd", "premium_sold_usd", "execution_fees_usd"), strict=True):
        col.metric(label, f"${replay[key]:,.2f}")
    if "hedged" in report:
        performance({"保护性看跌期权": report["hedged"], "持股对照": report["unhedged"]})
        curves(report["curve"], {"hedged_nav_usd": "有保护", "unhedged_nav_usd": "持股对照"}, "净值（USD）", date_key="at")
    trades, expiration, delivery = st.tabs(["历史成交", "到期行权", "结算现金流"])
    with trades:
        table(replay["fills"], {"at": "成交时间", "contract_id": "合约", "side": "方向",
                               "contracts": "张数", "fill_price": "成交价", "premium_usd": "权利金 USD", "fee_usd": "费用 USD"})
    with expiration:
        if replay["expirations"]:
            table(replay["expirations"], {"at": "到期", "contract_id": "合约", "exercised": "行权",
                                         "reference_value": "结算参考值", "intrinsic_usd_per_contract": "每张内在价值 USD"})
        else:
            st.caption("该报告无到期记录。")
    with delivery:
        if replay["deliveries"]:
            table(replay["deliveries"], {"at": "结算时间", "contract_id": "合约", "cash_usd": "到账现金 USD",
                                        "stock": "交割股票", "shares": "交割股数"})
        else:
            st.caption("该报告无交割记录。")


def treasury(report: dict) -> None:
    st.caption(f"CUSIP {report['cusip']} · 利率冲击 {report['rate_shock_bps']} 基点")
    vals = report["valuations"]
    price, risk, cash = st.tabs(["国债净价与估值", "利率风险", "票息与本金"])
    with price:
        curves(vals, {"clean_close_per_100": "历史净价"}, "每 100 USD 面值净价")
        table(vals, {"date": "估值日", "quote_date": "报价日", "clean_close_per_100": "净价",
                     "accrued_interest_usd": "应计利息 USD", "nav_usd": "净值 USD"})
    with risk:
        table(vals, {"date": "估值日", "dv01_usd": "DV01（USD / 基点）",
                     "rate_shock_pnl_usd": "冲击损益 USD", "rate_shock_nav_usd": "冲击后净值 USD"})
    with cash:
        table(report["cashflows"], {"scheduled_on": "合约日期", "paid_on": "到账日",
                                   "coupon_usd": "票息 USD", "principal_usd": "本金 USD"})


def hedge(report: dict) -> None:
    st.caption(f"共同期初资本 ${report['settings']['initial_capital_usd']:,.2f}")
    performance({HEDGE_SERIES[key]: value for key, value in report["performance"].items()})
    curves(report["curve"], {f"{key}_nav_usd": label for key, label in HEDGE_SERIES.items()}, "净值（USD）")
    cost = report["option_costs"]["net_cost_after_sale_usd"]
    with st.expander("保护成本与压力区间"):
        st.metric("期权净成本", f"${cost:,.2f}")
        stress = report.get("stress_window")
        if stress:
            st.caption(f"{stress['start']} 至 {stress['end']}")
            performance({HEDGE_SERIES[key]: value for key, value in stress["performance"].items()})
        table(report["curve"], {"date": "日期", "protected_shares": "保护股数", "treasury_dv01_usd": "国债 DV01",
                               "treasury_rate_shock_pnl_usd": "利率冲击损益 USD"})


def display(source: Path | bytes, name: str, kind: str, key: str) -> None:
    render, methods = {
        "screen": (screen, {"offline_cross_sectional_factor_percentile_v1"}),
        "backtest": (backtest, {"offline_stock_factor_walkforward_next_close_v1", "matched_stock_selection_comparison_v1"}),
        "options": (options, {"historical_long_option_replay", "prior_session_signal_next_eod_snapshot",
                              "prior_session_signal_next_snapshot"}),
        "treasury": (treasury, {"nominal_treasury_settled_holding_replay"}),
        "hedge": (hedge, {"matched_capital_stock_treasury_put"}),
    }[kind]
    try:
        data = source.read_bytes() if isinstance(source, Path) else source
        report = json.loads(data.decode("utf-8-sig"))
        if not isinstance(report, dict) or report.get("method") not in methods:
            raise ValueError("请选择本页对应的 AtlasPM JSON 报告。")
        render(report)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        st.error(f"报告无法读取或字段不完整。{exc}")
        return
    st.download_button("下载原始报告", data, file_name=name, mime="application/json", key=key)
