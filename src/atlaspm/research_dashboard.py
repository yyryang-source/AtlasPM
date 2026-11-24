"""Local Streamlit entry point for studies, archived results, and rebalance review."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from math import sin
from pathlib import Path

import pandas_market_calendars as calendars
import streamlit as st

from atlaspm import rebalance_dashboard, research_jobs, research_reports


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "local_data"
STATUS = {"queued": "排队", "running": "运行中", "completed": "已完成", "failed": "失败"}
ACTIVE = {"queued", "running"}
COMMAND_LABELS = {"stock-screen": "股票筛选", "stock-backtest": "股票回测"}
SYNTHETIC = "合成样例"
UPLOAD = "导入 CSV"
PRESETS = {
    "美股历史四股样本": (
        {"prices": "stock_research_public_sample_equities.csv",
         "benchmark-prices": "stock_research_public_sample_prices.csv"},
        date(2016, 1, 4), date(2020, 12, 31), 2,
        "AAPL、IBM、MSFT、ORCL，基准为 SPY。该样本适合验证研究流程。",
    ),
    "历史标普 500 样本": (
        {"prices": "sp500_pit_research/prices_continuous_v3.csv",
         "benchmark-prices": "sp500_pit_research/spy.csv",
         "universe-history": "sp500_pit_research/membership_fundamental_eligible_2022_2023.csv",
         "fundamentals": "sp500_pit_research/sec_fundamentals_v3.csv",
         "delistings": "sp500_pit_research/holdout_delistings_10bps.csv"},
        date(2022, 2, 1), date(2023, 12, 29), 20,
        "使用历史成份记录和决策日可用的 SEC 因子。股票池限于有可用基本面数据的样本。",
    ),
}
ARCHIVES = {
    "screen": {
        "四股历史样本 · 2020-12-31": "stock_research_public_sample_screen.json",
    },
    "backtest": {
        "四股样本 · 同股等权与 Quant": "stock_research_public_sample_quant.json",
        "历史标普 500 · 同股等权与 Quant": "sp500_pit_research/quant_holdout_base.json",
        "历史标普 500 · 四策略对照": "sp500_pit_research/stock_compare_2022_2023.json",
    },
    "options": {
        "SPY 保护性看跌期权 · 2022 下半年": "spy_put_2022h2/report.json",
        "SPXW 到期现金结算 · 2022-09-16": "spxw_expiry_20220916/report.json",
        "SPY 分钟级期权成交回放 · 2023-08-03": "lean_spy_option_20230803/report.json",
    },
    "treasury": {"美国两年期国债 · 91282CAC5": "treasury_91282cac5/report.json"},
    "hedge": {"股债与保护性看跌期权 · 2022 下半年": "multiasset_2022h2/report.json"},
}


def archive_reports(kind: str) -> dict[str, Path]:
    return {label: DATA / path for label, path in ARCHIVES[kind].items() if (DATA / path).is_file()}


def view_archive(kind: str) -> None:
    saved = archive_reports(kind)
    choices = [*saved, "导入报告 JSON"]
    label = st.selectbox("报告来源", choices, key=f"{kind}_archive")
    if label in saved:
        st.caption("本地研究报告")
        research_reports.display(saved[label], saved[label].name, kind, f"{kind}_archive_download")
    else:
        upload = st.file_uploader("选择 AtlasPM 报告", type=["json"], key=f"{kind}_archive_upload")
        if upload is not None:
            research_reports.display(upload.getvalue(), upload.name, kind, f"{kind}_archive_download")
        elif not saved:
            st.info("本机尚无此类报告。可以导入现有命令生成的 JSON。")


def synthetic_prices() -> dict[str, bytes]:
    series = {"prices": io.StringIO(), "benchmark-prices": io.StringIO()}
    writers = {name: csv.writer(buffer) for name, buffer in series.items()}
    for writer in writers.values():
        writer.writerow(["date", "symbol", "adjusted_close"])
    sessions = calendars.get_calendar("NYSE").valid_days("2022-01-03", "2024-12-31")
    for i, stamp in enumerate(sessions):
        day = stamp.date().isoformat()
        for j, symbol in enumerate(("FAKE-A", "FAKE-B", "FAKE-C", "FAKE-D")):
            level = 65 + j * 13 + i * (.04 + j * .025) + 7 * sin(i / (32 + 4 * j) + j)
            writers["prices"].writerow([day, symbol, f"{level:.4f}"])
        writers["benchmark-prices"].writerow([day, "SPY", f"{350 + i * .13 + 19 * sin(i / 48):.4f}"])
    return {name: buffer.getvalue().encode("utf-8") for name, buffer in series.items()}


def time_label(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")


@st.fragment(run_every=2)
def monitor(job_ids: tuple[str, ...]) -> None:
    states = [research_jobs.read_job(ROOT, job_id) for job_id in job_ids]
    if any(job["status"] not in ACTIVE for job in states):
        st.rerun()
    st.caption(" · ".join(f"{job['id'][:8]} {STATUS[job['status']]}" for job in states))


def view_jobs(*, command: str | None = None) -> None:
    records = {job["id"]: job for job in research_jobs.list_jobs(ROOT, command)}
    if not records:
        st.info("暂无运行记录。可从股票筛选或回测页面提交任务。")
        return
    active = tuple(job["id"] for job in records.values() if job["status"] in ACTIVE)
    if active:
        monitor(active)
    group = command or "all"
    key = f"selected_job_{group}"
    submitted = st.session_state.pop(f"submitted_{group}", None)
    if submitted in records:
        st.session_state[key] = submitted
    job_id = st.selectbox(
        "运行记录", list(records), key=key,
        format_func=lambda id_: (
            f"{time_label(records[id_]['created_at'])} · {COMMAND_LABELS[records[id_]['command']]} · "
            f"{records[id_]['label']} · {id_[:8]}"
        ),
    )
    job = records[job_id]
    st.caption(f"任务状态 · {STATUS[job['status']]}")
    if job["status"] == "failed":
        st.error(job.get("error", "任务失败"))
    elif job["status"] == "completed":
        kind = "backtest" if job["command"] == "stock-backtest" else "screen"
        research_reports.display(
            research_jobs.job_file(ROOT, job_id, "report.json"),
            f"{job['command']}-{job_id[:8]}.json", kind, f"job_download_{group}",
        )
    with st.expander("参数、输入版本与日志", expanded=job["status"] == "failed"):
        st.json({"参数": job["options"], "输入版本": job["inputs"]}, expanded=1)
        log = research_jobs.job_file(ROOT, job_id, "run.log")
        if log.is_file():
            with log.open("rb") as handle:
                handle.seek(max(0, log.stat().st_size - 12_000))
                tail = handle.read().decode("utf-8", errors="replace").strip()
            if tail:
                st.code(tail, language="text")


def run_form(command: str) -> None:
    backtest = command == "stock-backtest"
    available = {
        label: preset for label, preset in PRESETS.items()
        if all((DATA / file).is_file() for file in preset[0].values())
    }
    dataset = st.selectbox("股票池与数据", [*available, SYNTHETIC, UPLOAD], key=f"dataset_{command}")
    files, start, end, top, description = available.get(
        dataset, ({}, date(2023, 1, 3), date(2024, 12, 31), 2, ""),
    )
    if dataset in available:
        st.caption(description)
    elif dataset == SYNTHETIC:
        st.caption("虚构股票与基准序列，用于演练选股和回测。")
    dates = {"min_value": date(1900, 1, 1), "max_value": date.today()}
    with st.form(f"research_{command}_{dataset}", border=True):
        inputs: dict[str, Path | bytes] = {}
        required: tuple[str, ...] = ()
        if dataset == UPLOAD:
            fields = {
                "prices": "复权日线 CSV", "universe-history": "历史股票池 CSV（可选）",
                "fundamentals": "按公布日记录的财报因子 CSV（可选）",
            }
            required = ("prices",)
            if backtest:
                fields |= {"benchmark-prices": "SPY 基准日线 CSV", "delistings": "现金退市记录 CSV（可选）"}
                required += ("benchmark-prices",)
            uploads = [(flag, st.file_uploader(label, type=["csv"])) for flag, label in fields.items()]
        elif dataset in available:
            inputs = {flag: DATA / file for flag, file in files.items() if flag in research_jobs.COMMANDS[command][0]}
        options: dict = {}
        if backtest:
            left, right, count = st.columns(3)
            begin = left.date_input("起始日", start, **dates)
            finish = right.date_input("截止日", end, **dates)
            options.update(start=begin.isoformat(), end=finish.isoformat())
            cost, slip, period = st.columns(3)
            options.update({
                "fee-bps": cost.number_input("手续费（基点）", 0.0, 1000.0, 5.0, .5),
                "slippage-bps": slip.number_input("滑点（基点）", 0.0, 1000.0, 5.0, .5),
                "rebalance-days": period.number_input("调仓间隔（交易日）", 1, 252, 21),
                "quant": True,
            })
            with st.expander("Quant 仓位限制"):
                weight, turnover = st.columns(2)
                options["quant-max-weight"] = weight.number_input("单股上限", .01, 1., .15 if top == 20 else .55, .01)
                options["quant-turnover-limit"] = turnover.number_input("换手上限", 0., 2., .8 if top == 20 else 2., .1)
        else:
            day, count = st.columns(2)
            options["as-of"] = day.date_input("决策日", end, **dates).isoformat()
            options["require-decision-close"] = True
        options["top"] = count.number_input("入选数量", 1, 100, top)
        if "fundamentals" in inputs:
            options["max-fundamental-age-days"] = 455
        submitted = st.form_submit_button("运行等权与 Quant 回测" if backtest else "运行选股", type="primary")
    if not submitted:
        return
    if dataset == UPLOAD:
        inputs = {flag: item.getvalue() for flag, item in uploads if item is not None}
    elif dataset == SYNTHETIC:
        inputs = {flag: value for flag, value in synthetic_prices().items() if flag in research_jobs.COMMANDS[command][0]}
    if any(flag not in inputs for flag in required):
        st.error("请上传复权日线及本次运行所需的基准文件。")
        return
    if backtest and begin >= finish:
        st.error("回测起始日应早于截止日。")
        return
    job_id = research_jobs.queue_job(ROOT, command, dataset, inputs, options)
    st.session_state[f"submitted_{command}"] = job_id
    st.toast(f"{COMMAND_LABELS[command]}已登记 · {job_id[:8]}")


def stocks(command: str, kind: str, title: str, description: str) -> None:
    st.title(title)
    st.caption(description)
    execute, archive = st.tabs(["运行研究", "已有报告"])
    with execute:
        run_form(command)
        st.subheader("运行结果")
        view_jobs(command=command)
    with archive:
        view_archive(kind)


def open_report(title: str, description: str, kind: str) -> None:
    st.title(title)
    st.caption(description)
    view_archive(kind)


def overview(pages: dict) -> None:
    st.title("AtlasPM 量化研究工作台")
    st.caption("美股选股、组合回测、对冲报告与调仓审核")
    jobs = research_jobs.list_jobs(ROOT)
    for col, name, value in zip(
        st.columns(3), ("本地历史报告", "研究运行", "进行中"),
        (sum(len(archive_reports(kind)) for kind in ARCHIVES), len(jobs),
         sum(job["status"] in ACTIVE for job in jobs)), strict=True,
    ):
        with col, st.container(border=True):
            st.metric(name, value)
    st.subheader("股票研究")
    for col, key, detail in zip(st.columns(3), ("screen", "backtest", "rebalance"), (
        "按股票池和日期计算排名，查看股票被排除的原因。",
        "对同一批入选股票比较等权与 Quant 仓位的净值和回撤。",
        "核对目标仓位、未成交挂单、草案和逐项风控。",
    ), strict=True):
        with col, st.container(border=True):
            st.page_link(pages[key])
            st.caption(detail)
    st.subheader("期权、国债与对冲报告")
    for col, kind, detail in zip(st.columns(3), ("options", "treasury", "hedge"), (
        "权利金、成交、到期行权和交割。",
        "净价、票息、本金和利率敏感度。",
        "同等期初资本下的股票、股债和股债加期权。",
    ), strict=True):
        with col, st.container(border=True):
            st.page_link(pages[kind])
            st.caption(detail)
    if jobs:
        st.subheader("最近运行")
        st.dataframe([
            {"时间": time_label(job["created_at"]), "任务": COMMAND_LABELS[job["command"]],
             "样本": job["label"], "状态": STATUS[job["status"]]}
            for job in jobs[:5]
        ], hide_index=True)
    st.page_link(pages["jobs"])


def all_jobs() -> None:
    st.title("研究任务")
    st.caption("运行参数、输入哈希、日志和报告保存在 local_data/runs/。")
    view_jobs()


def api_management() -> None:
    from atlaspm.monitoring.api_management import inventory, public_smoke, set_enabled
    st.title("API 管理中心")
    st.caption("仅本地进程状态；未验证的订阅、费用、权限和连接不会显示为已通过。")
    rows=inventory()
    st.dataframe(rows,hide_index=True)
    provider=st.selectbox("供应商（仅当前工作台进程）",[row["provider"] for row in rows])
    left,right=st.columns(2)
    if left.button("启用研究接口"):
        set_enabled(provider,True)
        st.rerun()
    if right.button("禁用研究接口"):
        set_enabled(provider,False)
        st.rerun()
    st.info("通过本地环境变量或操作系统凭证启动器配置密钥。此页面不接收或显示密钥。")
    if st.button("测试 Treasury 公开接口"):
        try:
            report=public_smoke()
        except (ValueError,OSError):
            st.error("公开接口检查失败；请检查本地连接及启用配置。")
        else:
            st.json(report)


def main() -> None:
    st.set_page_config(page_title="AtlasPM 量化研究工作台", layout="wide", initial_sidebar_state="expanded")
    pages = {
        "home": st.Page(lambda: overview(pages), title="工作台总览", default=True),
        "screen": st.Page(lambda: stocks("stock-screen", "screen", "股票筛选", "按决策日查看因子排名与排除原因。"),
                          title="股票筛选", url_path="screen"),
        "backtest": st.Page(lambda: stocks("stock-backtest", "backtest", "回测与 Quant", "用同一入选名单对比等权与 Quant，并保留基准。"),
                            title="回测与 Quant", url_path="backtest"),
        "options": st.Page(lambda: open_report("期权研究", "期权历史成交、到期和保护成本。", "options"),
                           title="期权研究", url_path="options"),
        "treasury": st.Page(lambda: open_report("国债研究", "美国国债历史净价和现金流回放。", "treasury"),
                            title="国债研究", url_path="treasury"),
        "hedge": st.Page(lambda: open_report("多资产对冲", "用相同期初资本比较三组组合。", "hedge"),
                         title="多资产对冲", url_path="hedge"),
        "rebalance": st.Page(rebalance_dashboard.render, title="调仓审核", url_path="rebalance"),
        "jobs": st.Page(all_jobs, title="研究任务", url_path="jobs"),
        "api": st.Page(api_management, title="API 管理中心", url_path="api"),
    }
    page = st.navigation({
        "工作台": [pages["home"]],
        "研究": [pages[key] for key in ("screen", "backtest", "options", "treasury", "hedge")],
        "本地运行": [pages["rebalance"], pages["jobs"], pages["api"]],
    })
    page.run()


if __name__ == "__main__":
    main()
