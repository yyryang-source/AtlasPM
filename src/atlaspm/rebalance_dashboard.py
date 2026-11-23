"""Streamlit entry point for the local equities review workbench."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from atlaspm.rebalance_workbench import Policy, RunStore, demo_inputs, latest_completed_session


STATUSES = {"blocked": "已拦截", "review": "待审核", "reviewed": "已审核"}
NEW_YORK = ZoneInfo("America/New_York")
POSITION_COLUMNS = {
    "symbol": "代码", "held": "已持有", "pending_buy": "待买入", "pending_sell": "待卖出",
    "effective": "计入挂单后", "target": "目标持仓", "delta": "拟调整", "close": "收盘价",
}
DRAFT_COLUMNS = {
    "symbol": "代码", "side": "方向", "shares": "股数", "reference_close": "参考收盘价",
    "notional": "估算金额", "fee_buffer": "费用缓冲",
}


def eastern_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(NEW_YORK).strftime("%Y-%m-%d %H:%M %Z")


def money_column(label: str):
    return st.column_config.NumberColumn(label, format="$ %.2f")


def render_positions(positions: list[dict]) -> None:
    st.subheader("持仓差额")
    notionals = pd.DataFrame([
        {"代码": row["symbol"], "已持有": row["held"] * row["close"],
         "目标持仓": row["target"] * row["close"]}
        for row in positions
    ])
    notionals["规模"] = notionals[["已持有", "目标持仓"]].max(axis=1)
    displayed = notionals.nlargest(12, "规模")
    data = displayed.melt(id_vars="代码", value_vars=["已持有", "目标持仓"],
                          var_name="仓位", value_name="市值")
    chart = alt.Chart(data).mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("市值:Q", title="市值（USD）", axis=alt.Axis(format="$,.0f", tickCount=5)),
        y=alt.Y("代码:N", sort=displayed["代码"].tolist(), title=None),
        yOffset="仓位:N",
        color=alt.Color("仓位:N", scale=alt.Scale(
            domain=["已持有", "目标持仓"], range=["#8194B4", "#08786C"],
        ), legend=alt.Legend(title=None, orient="top")),
        tooltip=["代码:N", "仓位:N", alt.Tooltip("市值:Q", format="$,.2f")],
    ).properties(
        height=max(190, len(displayed) * 36),
        autosize=alt.AutoSizeParams(type="fit-x", contains="padding"),
    )
    if len(positions) > len(displayed):
        st.caption("图中显示市值最大的 12 只股票。完整持仓见下表。")
    st.altair_chart(chart, use_container_width=True)
    st.dataframe(
        positions, hide_index=True,
        column_config=POSITION_COLUMNS | {"close": money_column("收盘价")},
        column_order=list(POSITION_COLUMNS),
    )


def render_risk(checks: list[dict]) -> None:
    passed = sum(item["passed"] for item in checks)
    st.caption(f"通过 {passed} 项 · 拦截 {len(checks) - passed} 项")
    for check in checks:
        badge = "通过" if check["passed"] else "拦截"
        with st.expander(f"{badge} · {check['name']}", expanded=not check["passed"]):
            detail = check["detail"]
            if isinstance(detail, dict):
                rows = []
                for name, value in detail.items():
                    if isinstance(value, list):
                        display = "、".join(value) or "无"
                    elif isinstance(value, (float, int)) and check["name"] in {
                        "单股集中度", "草稿换手率", "股票下跌压力测试",
                    }:
                        display = f"{value:.2%}"
                    elif isinstance(value, (float, int)) and check["name"] == "现金与费用缓冲":
                        display = f"${value:,.2f}"
                    else:
                        display = str(value)
                    rows.append({"检查项": name, "结果": display})
                st.table(pd.DataFrame(rows).set_index("检查项"))
            elif isinstance(detail, list):
                st.text("、".join(detail) or "无")
            elif check["name"] == "账户净值参考":
                st.text(f"${detail:,.2f}")
            elif check["name"] == "收盘时间":
                st.text(eastern_time(detail))
            else:
                st.text(detail)


def render_run(store: RunStore, report: dict) -> None:
    status = report["status"]
    with st.container(border=True):
        metrics = st.columns(4)
        metrics[0].metric("决策日（美东）", report["as_of"])
        metrics[1].metric("入选股票", len(report.get("candidates", [])))
        metrics[2].metric("订单草案", len(report["draft"]))
        metrics[3].metric("审核状态", STATUSES[status])
        strategy = "Quant 优化" if report["policy"]["allocation"] == "quant" else "等权"
        if report["mode"] == "demo":
            st.caption(f"样例演练 · {strategy} · 合成行情和模拟现金账户")
        else:
            st.caption(f"快照导入 · {strategy}")
            st.text(f"股票池来源  {report.get('scope_source') or '待校验'}")
            if report.get("snapshot_at"):
                st.caption(f"账户快照采集于 {eastern_time(report['snapshot_at'])}")

    if status == "blocked":
        failures = "、".join(check["name"] for check in report["checks"] if not check["passed"])
        st.warning(f"草案已拦截。请在「行情与风控」查看 {failures}。")

    holdings, risk, selection = st.tabs(["持仓与草案", "行情与风控", "选股依据"])
    with holdings:
        if report.get("positions"):
            render_positions(report["positions"])
        if report["draft"]:
            st.subheader("订单草案")
            st.caption("金额按未复权收盘价估算。记录审核仅保存本地备注。")
            draft = pd.DataFrame(report["draft"], columns=list(DRAFT_COLUMNS))
            subtotals = draft.groupby("side")["notional"].sum()
            buy, sell = st.columns(2)
            buy.metric("买入估算金额", f"${subtotals.get('BUY', 0):,.2f}")
            sell.metric("卖出估算金额", f"${subtotals.get('SELL', 0):,.2f}")
            columns = DRAFT_COLUMNS | {
                key: money_column(DRAFT_COLUMNS[key]) for key in
                ("reference_close", "notional", "fee_buffer")
            }
            st.dataframe(
                draft.assign(side=draft["side"].map({"BUY": "买入", "SELL": "卖出"})),
                hide_index=True, column_config=columns,
            )
            st.download_button(
                "下载草案 CSV", draft.rename(columns=DRAFT_COLUMNS).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"rebalance-draft-{report['id'][:10]}.csv", mime="text/csv",
            )
        elif status != "blocked":
            st.info("当前持仓与挂单已满足目标组合。")
        if status == "review":
            with st.form("review_" + report["id"]):
                note = st.text_input("审核备注", max_chars=1000)
                if st.form_submit_button("记录审核"):
                    try:
                        store.acknowledge(report["id"], note)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
        if report["reviewed"]:
            st.success(f"审核记录已保存 · {eastern_time(report['reviewed'])}")
            st.text(report["review_note"])
    with risk:
        render_risk(report["checks"])
        with st.expander("策略参数与输入版本"):
            st.json({"策略参数": report["policy"], "输入 SHA-256": report["input_sha256"]})
    with selection:
        if report.get("candidates"):
            weights = report.get("target_weights", {})
            st.dataframe(
                [{"代码": row["symbol"], "选股得分": round(row["score"], 2),
                  "目标权重": f"{weights[row['symbol']]:.2%}" if row["symbol"] in weights else "—"}
                 for row in report["candidates"]],
                hide_index=True,
            )
        if report.get("excluded"):
            st.subheader("排除记录")
            st.dataframe(report["excluded"], hide_index=True,
                         column_config={"symbol": "代码", "reason": "原因"})
        if not report.get("candidates") and not report.get("excluded"):
            st.info("行情通过检查后，这里会显示选股结果。")


def main() -> None:
    st.set_page_config(page_title="AtlasPM 调仓审核台", layout="wide", initial_sidebar_state="expanded")
    root = Path(__file__).resolve().parents[2]
    store = RunStore(root / "local_data" / "rebalance" / "workbench.sqlite3")

    st.sidebar.title("AtlasPM")
    st.sidebar.subheader("运行设置", divider="green")
    source = st.sidebar.radio("输入来源", ["样例演练", "导入本地快照"])
    demo = source == "样例演练"
    with st.sidebar.form("rebalance"):
        if demo:
            st.caption("使用固定交易日的合成行情，演示未成交挂单如何影响草案。")
        else:
            latest = latest_completed_session()
            day = st.date_input("美东决策日", value=latest, max_value=latest, key="session_date")
            uploads = (
                st.file_uploader("股票池快照", type=["json"]),
                st.file_uploader("复权历史行情", type=["csv"]),
                st.file_uploader("未复权收盘价", type=["csv"]),
                st.file_uploader("现金账户快照", type=["json"]),
            )
        allocation = st.selectbox("目标仓位计算", ["等权", "Quant 优化"])
        top = st.number_input("选股数量", min_value=1, max_value=100, value=4)
        submitted = st.form_submit_button("运行调仓演练", type="primary", use_container_width=True)
        if submitted:
            if not demo and not all(uploads):
                st.error("请上传四份输入文件。")
            else:
                inputs = demo_inputs() if demo else (day, *(item.getvalue() for item in uploads))
                with st.spinner("正在校验行情、持仓和风险"):
                    run = store.run(
                        *inputs, Policy(top=top, allocation="equal" if allocation == "等权" else "quant"),
                        demo=demo,
                    )
                    st.session_state["status_filter"] = "全部"
                    st.session_state["active_run"] = run
    st.sidebar.caption("运行记录保存在此电脑的 SQLite 数据库。")

    st.title("调仓审核台", anchor=False)
    st.caption("美股现金账户 · 收盘行情、目标仓位与订单草案")
    runs = {row["id"]: row for row in store.list()}
    if not runs:
        st.info("在左侧点击「运行调仓演练」，查看一笔包含未成交挂单的调仓。")
        return
    filters, history = st.columns([1, 3], vertical_alignment="bottom")
    status_filter = filters.selectbox("审核状态筛选", ["全部", *STATUSES.values()], key="status_filter")
    matching = [key for key, row in runs.items()
                if status_filter in ("全部", STATUSES[row["status"]])]
    if not matching:
        st.info("所选状态暂无记录。")
        return
    if st.session_state.get("active_run") not in matching:
        st.session_state["active_run"] = matching[0]
    run = history.selectbox(
        "运行记录", matching, key="active_run",
        format_func=lambda key: f"{eastern_time(runs[key]['created'])} · {STATUSES[runs[key]['status']]} · {key[:10]}",
    )
    report = store.get(run)
    if report is not None:
        render_run(store, report)
        with st.expander("查看运行报告"):
            st.download_button("下载 JSON 报告", json.dumps(report, ensure_ascii=False, indent=2),
                               file_name=f"rebalance-{run[:10]}.json", mime="application/json")


if __name__ == "__main__":
    main()
