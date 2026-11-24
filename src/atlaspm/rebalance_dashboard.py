"""Streamlit entry point for the local equities review workbench."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

from atlaspm.ibkr_equity_snapshot import selected_paper_account
from atlaspm.ibkr_next_day import next_day_review
from atlaspm.ibkr_paper_orders import reconcile
from atlaspm.ibkr_whatif import PreviewTransport
from atlaspm.paper_order_journal import PaperJournal
from atlaspm.execution.ibkr_gateway import DEFAULT_GATEWAY_URL, GatewayHTTPError, LocalGatewayTransport, require_private_output
from atlaspm.rebalance_workbench import Policy, RunStore, demo_inputs, latest_completed_session, next_session_window


STATUSES = {"blocked": "已拦截", "review": "待审核", "reviewed": "已审核"}
HANDOFF_STATUSES = {
    "market_blocked": "行情质检拦截", "not_scheduled": "当前交易日不调仓",
    "outside_account_capture_window": "当前时间超出账户采集窗口",
    "account_marks_blocked": "持仓或挂单缺少同日参考价", "draft_blocked": "草案被风控拦截",
    "blocked": "账户交接失败", "review_available": "草案已生成",
}
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


def read_close_event(log: Path) -> tuple[str, dict]:
    with log.open("rb") as stream:
        size = stream.seek(0, 2)
        start = max(0, size - 65536)
        stream.seek(start)
        lines = stream.read(65536).splitlines()
    if start:
        lines = lines[1:]
    for raw in reversed(lines):
        stamp, _, message = raw.decode("utf-8", errors="replace").partition(" ")
        try:
            if datetime.fromisoformat(stamp).tzinfo is None:
                continue
        except ValueError:
            continue
        if message.startswith("Market close preparation failed: "):
            return stamp, {"status": "error", "error": message}
        try:
            event = json.loads(message)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("status") in {"blocked", "ready"}:
            return stamp, event
    raise ValueError("监控日志暂无完整记录。")


def render_market_audit(log: Path, event: dict) -> None:
    try:
        day = date.fromisoformat(event["as_of"])
        audit_path = Path(event["audit"]).resolve()
        if audit_path.name != "audit.json" or not audit_path.is_relative_to(log.parent / day.isoformat()):
            raise ValueError("审计文件不在对应的交易日目录内。")
        contents = audit_path.read_bytes()
        audit = json.loads(contents)
        if (audit["as_of"], audit["status"]) != (day.isoformat(), event["status"]):
            raise ValueError("审计文件与日志记录不一致。")
        market = audit["market_audit"]
        exact = market["stocks_with_exact_decision_close"]
        universe = market["listed_stock_universe_count"]
    except (OSError, ValueError, KeyError, TypeError):
        st.warning("对应的行情审计文件暂不可用。")
        return
    scope = "配置股票池" if market.get("data_scope") == "configured_strategy_symbols" else "上市清单"
    st.caption(f"{scope}收盘价覆盖 · {exact} / {universe} 只")
    st.download_button("下载行情审计 JSON", contents, file_name=f"market-close-{day}.json", mime="application/json")


def render_close_monitor(root: Path, runs: dict[str, dict]) -> None:
    with st.expander("收盘行情监控", expanded=True):
        directory, refresh = st.columns([3, 1], vertical_alignment="bottom")
        folder = directory.text_input("监控输出目录（相对于仓库）", "local_data/market_close")
        refresh.button("刷新行情状态")
        try:
            log = require_private_output(root / folder / "watch.log", project_root=root)
        except ValueError:
            st.warning("请选择仓库 local_data 下的监控输出目录。")
            return
        try:
            stamp, event = read_close_event(log)
            saved_at = eastern_time(stamp)
        except FileNotFoundError:
            st.info("该目录暂无收盘监控日志。")
            return
        except (OSError, ValueError) as exc:
            st.warning(f"无法读取收盘监控日志。{exc}")
            return
        st.caption(f"最近记录于 {saved_at}")
        if event["status"] == "error":
            st.error(event["error"])
            return

        day, gate, calls = st.columns(3)
        day.metric("行情交易日（美东）", event.get("as_of", "—"))
        gate.metric("行情质检", "通过" if event["status"] == "ready" else "拦截")
        calls.metric("该次审计的下载请求数", event.get("download_calls", "—"))
        if event.get("reasons"):
            st.warning("\n\n".join(event["reasons"]))
        render_market_audit(log, event)

        draft = event.get("draft")
        if not draft:
            st.caption("此记录只包含行情审计。")
            return
        handoff = draft.get("status", "")
        st.write(f"账户交接 · {HANDOFF_STATUSES.get(handoff, handoff)}")
        if draft.get("error"):
            st.warning(draft["error"])
        if draft.get("missing_symbols"):
            st.warning("缺少参考价的股票：" + "、".join(draft["missing_symbols"]))
        run_id = draft.get("run_id")
        if run_id in runs and st.button("查看此调仓记录"):
            st.session_state["status_filter"] = "全部"
            st.session_state["active_run"] = run_id


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
                        "总股票敞口", "单股集中度", "草稿换手率", "股票下跌压力测试",
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


def gateway_fields(root: Path) -> tuple[str, str]:
    certificate = Path("local_data/ibkr_gateway_validation/localhost.pem")
    url = st.text_input("Gateway API 地址", value=DEFAULT_GATEWAY_URL)
    ca = st.text_input("本机证书 PEM 路径（可选）",
                       value=certificate.as_posix() if (root / certificate).is_file() else "")
    return url.strip(), ca.strip()


def connect_paper_gateway(
    root: Path, url: str, ca: str, transport_type: type[LocalGatewayTransport],
) -> tuple[LocalGatewayTransport, str]:
    ca_file = require_private_output(root / ca, project_root=root) if ca else None
    transport = transport_type(url, ca_file=str(ca_file) if ca_file else None)
    return transport, selected_paper_account(transport.get("/iserver/accounts"))


def show_paper_error(error: Exception) -> None:
    st.error("请先在本机 Gateway 使用 Paper 账户登录。"
             if isinstance(error, GatewayHTTPError) and error.status == 401 else str(error))


def render_paper_batch(root: Path, journal: PaperJournal, batch: dict) -> None:
    labels = {
        "not_sent": "未发送", "sending": "等待回执", "working": "挂单中", "partial": "部分成交",
        "filled": "已成交", "cancel_requested": "撤单已请求", "cancel_unknown": "撤单待核实",
        "cancelled": "已撤单", "expired": "已过期", "rejected": "已拒单",
        "unknown": "回执待核实", "confirmation_required": "券商警告待处理",
    }
    st.subheader("Paper 订单与对账")
    orders = batch["orders"]
    st.dataframe([
        {"代码": row["symbol"], "客户订单号": row["client_id"], "券商单号": row["order_id"],
         "订单状态": labels.get(row["state"], row["state"]), "券商已成交股数": row["broker_filled"]}
        for row in orders
    ], hide_index=True)
    if batch["fills"]:
        with st.expander("券商成交明细"):
            st.dataframe(batch["fills"], hide_index=True, column_config={
                "execution_id": "成交编号", "client_id": "客户订单号", "symbol": "代码",
                "side": "方向", "shares": "股数", "price": "成交价", "commission": "佣金",
            })
    reconciliation = batch["reconciliation"]
    if reconciliation:
        status = {"pending": "待对账", "matched": "资金与持仓一致", "mismatch": "对账差异"}[reconciliation["status"]]
        st.caption(f"{status} · {eastern_time(reconciliation['observed_at'])}")
        st.write({"现金差额": reconciliation["cash_difference"], "持仓差额": reconciliation["position_differences"]})
        st.download_button("下载对账 JSON", json.dumps(reconciliation, ensure_ascii=False, indent=2),
                           file_name=f"paper-reconciliation-{batch['id'][:10]}.json", mime="application/json")
        if reconciliation["status"] == "matched":
            return
    with st.expander("同步成交与对账"):
        with st.form("paper_reconcile_" + batch["id"]):
            url, ca = gateway_fields(root)
            refresh = st.form_submit_button("从券商同步并对账")
        if not refresh:
            return
        try:
            with st.spinner("正在查询券商订单、成交、持仓和资金"):
                transport, account = connect_paper_gateway(root, url, ca, LocalGatewayTransport)
                reconcile(transport, journal, batch["id"], account)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            show_paper_error(exc)
        else:
            st.rerun()


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
            source = "IBKR 只读快照" if report.get("account_source") == "local_ibkr_cash_equity_snapshot" else "快照导入"
            st.caption(f"{source} · {strategy}")
            st.text(f"股票池来源  {report.get('scope_source') or '待校验'}")
            if report.get("snapshot_at"):
                st.caption(f"账户快照采集于 {eastern_time(report['snapshot_at'])}")

    if status == "blocked":
        failures = "、".join(check["name"] for check in report["checks"] if not check["passed"])
        st.warning(f"草案已拦截。请在「行情与风控」查看 {failures}。")

    if report.get("execution_session"):
        st.caption(f"次日复核 · NYSE {report['execution_session']} · 原草案 {report['parent_id'][:10]}")
        if report.get("account_confirmed_at"):
            st.caption(f"券商预览后账户采集于 {eastern_time(report['account_confirmed_at'])}")
    holdings, risk, selection = st.tabs(["持仓与草案", "行情与风控", "选股依据"])
    with holdings:
        if report.get("positions"):
            render_positions(report["positions"])
        if report["draft"]:
            st.subheader("订单草案")
            if report.get("execution_session"):
                st.caption("金额按次日实时买卖报价和草案限价估算。")
                names = DRAFT_COLUMNS | {"reference_close": "报价中点", "limit_price": "限价"}
            else:
                st.caption("金额按未复权收盘价估算。")
                names = DRAFT_COLUMNS
            draft = pd.DataFrame(report["draft"], columns=list(names))
            subtotals = draft.groupby("side")["notional"].sum()
            buy, sell = st.columns(2)
            buy.metric("买入估算金额", f"${subtotals.get('BUY', 0):,.2f}")
            sell.metric("卖出估算金额", f"${subtotals.get('SELL', 0):,.2f}")
            columns = names | {
                key: money_column(names[key]) for key in
                ("reference_close", "notional", "fee_buffer", "limit_price") if key in names
            }
            st.dataframe(
                draft.assign(side=draft["side"].map({"BUY": "买入", "SELL": "卖出"})),
                hide_index=True, column_config=columns,
            )
            st.download_button(
                "下载草案 CSV", draft.rename(columns=names).to_csv(index=False).encode("utf-8-sig"),
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
        if report.get("draft_changes"):
            st.subheader("次日草案变动")
            st.dataframe(report["draft_changes"], hide_index=True, column_config={
                "symbol": "代码", "side": "方向", "previous_shares": "原数量", "new_shares": "新数量",
                "previous_price": money_column("原参考价"), "new_limit_price": money_column("新限价"),
            })
        if report.get("broker_estimates"):
            st.subheader("Paper 券商预览")
            st.dataframe([
                {"代码": row["symbol"], "限价": row["ticket"]["price"], "佣金": row["commission"],
                 "初始保证金变动": row["initial_margin"]["change"], "维持保证金变动": row["maintenance_margin"]["change"]}
                for row in report["broker_estimates"]
            ], hide_index=True)
        with st.expander("策略参数与输入版本"):
            inputs = {"策略参数": report["policy"], "输入 SHA-256": report["input_sha256"]}
            if "next_day_contracts" in report:
                inputs["IBKR 合约编号"] = report["next_day_contracts"]
            st.json(inputs)
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


def render_next_day_panel(root: Path, store: RunStore, report: dict) -> None:
    if (report["status"] != "reviewed" or report.get("parent_id")
            or not report["input_sha256"].get("market_audit") or not report.get("account_id_sha256")):
        return
    day, opens, closes = next_session_window(date.fromisoformat(report["as_of"]))
    market_open = opens <= datetime.now(timezone.utc) < closes
    with st.expander("IBKR Paper 次日复核", expanded=market_open):
        duration = f"{eastern_time(opens.isoformat())} 至 {eastern_time(closes.isoformat())}"
        st.caption(f"NYSE 次日交易时段 · {duration}")
        if not market_open:
            st.info(f"本次草案的复核时段为 {day}，请在对应时段运行。")
            return
        with st.form("next_day_review_" + report["id"]):
            url, ca = gateway_fields(root)
            uploaded = st.file_uploader("股票合约映射 JSON（可选）", type=["json"])
            run = st.form_submit_button("运行 Paper 次日复核", type="primary")
        if not run:
            return
        try:
            mapping = json.loads(uploaded.getvalue()) if uploaded is not None else {}
            if not isinstance(mapping, dict):
                raise ValueError("合约映射应为股票代码到券商合约号的 JSON 对象。")
            with st.spinner("正在刷新账户和行情，并向券商请求 What-If 预览"):
                transport, account = connect_paper_gateway(root, url, ca, PreviewTransport)
                refreshed = next_day_review(transport, report, account, mapping)
                run_id = store.record_next_day(refreshed)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            show_paper_error(exc)
        else:
            st.session_state["open_next_day_run"] = run_id
            st.rerun()


def render() -> None:
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
    render_close_monitor(root, runs)
    next_day_run = st.session_state.pop("open_next_day_run", None)
    if next_day_run in runs:
        st.session_state["status_filter"] = "全部"
        st.session_state["active_run"] = next_day_run
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
        render_next_day_panel(root, store, report)
        if report.get("execution_session"):
            journal = PaperJournal(store.path)
            batch = journal.get(report["id"])
            if batch is not None:
                render_paper_batch(root, journal, batch)
        with st.expander("查看运行报告"):
            st.download_button("下载 JSON 报告", json.dumps(report, ensure_ascii=False, indent=2),
                               file_name=f"rebalance-{run[:10]}.json", mime="application/json")


def main() -> None:
    st.set_page_config(page_title="AtlasPM 调仓审核台", layout="wide", initial_sidebar_state="expanded")
    render()


if __name__ == "__main__":
    main()
