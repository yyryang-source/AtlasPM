# AtlasPM 收盘调仓审核台

收盘后行情增量更新、审计拦截和 IBKR 只读入口见 [收盘行情与调仓草案](MARKET_CLOSE_REBALANCE.md)。

本地工作台用 Python 运行选股、仓位计算和草案风控，用 Streamlit 展示过程，用 SQLite 保存已完成的运行及其审核备注。第一版覆盖专用 USD 现金账户的美股整数多头仓位。样例使用虚构行情与账户，导入模式使用本地文件。草案可供人工审核，页面没有券商发送接口。

## 启动

在 AtlasPM 仓库根目录打开 PowerShell。已有 `.venv` 时直接安装可选依赖并运行。新检出可先执行 `python -m venv .venv`。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[workbench]"
.\.venv\Scripts\python.exe -m streamlit run .\src\atlaspm\rebalance_dashboard.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

在浏览器打开 `http://127.0.0.1:8501`。首次用「样例演练」，选择等权或 Quant 后点击「运行调仓演练」。「持仓与草案」展示持仓市值对比、挂单剩余股数、目标差额与草案。「行情与风控」展示每项检查结果。「选股依据」展示入选排名、目标权重与排除原因。

也可以只跑 Python 后端。它与页面共用数据库，退出码 `2` 表示拦截。

```powershell
.\.venv\Scripts\python.exe -m atlaspm.rebalance_workbench --demo --allocation quant
```

调仓记录保存在 `local_data/rebalance/workbench.sqlite3`。同一输入哈希和参数复用同一运行编号。可按审核状态查找运行记录，审核备注只写一次。页面支持下载草案 CSV 和完整 JSON 报告。

## 导入格式

四份文件应对应同一个美东交易日，页面默认最近一个已收盘 NYSE 交易日。下表说明需要的字段，日期使用 ISO 格式。

| 文件 | 内容 |
| --- | --- |
| 股票池 JSON | `as_of`、`symbols` 数组和 `source`。这是策略负责筛选的股票池，使用当天可用的成份记录。 |
| 复权历史 CSV | `date,symbol,adjusted_close`。股票池内每只股票需要最近连续 253 个 NYSE 交易日。 |
| 未复权收盘 CSV | `date,symbol,close`。须包含决策日股票池、实际持仓和有效挂单涉及的所有股票，用于计算股数、名义金额和风险。 |
| 账户快照 JSON | 见下文。按决策日收盘后四小时内采集的时间填写 `captured_at`，注明时区。 |

下面用虚构账户展示输入结构。账户必须为本策略专用，`positions` 含已成交后实际持有的股数，`open_orders` 含全部仍在生效的限价挂单，其中 `remaining` 是尚未成交的股数。股票池内未入选的仓位以及快照中的股票池外仓位会以目标零股参与计算。佣金估计仅按本地费用缓冲处理。

```json
{
  "account_id": "DEMO-CASH",
  "account_type": "cash",
  "currency": "USD",
  "captured_at": "2024-12-31T21:20:00Z",
  "positions_and_orders_complete": true,
  "managed_equities_only": true,
  "settled_cash": 26000,
  "available_cash": 25000,
  "positions": {"ALFA": 16, "ECHO": 12},
  "open_orders": [
    {"id": "DEMO-1", "symbol": "ECHO", "side": "BUY", "remaining": 5, "limit_price": 160}
  ]
}
```

使用 `--as-of YYYY-MM-DD --universe ... --adjusted ... --references ... --account ...` 在 CLI 导入。有外部文件准备流程时可以把 `--as-of` 换为 `--latest-close`，后台按交易日历确定最近一个已收盘的交易日。数据审计依照提供的股票池进行。快照来源和完整性由提供文件的人负责确认。

## 草案风控

复用 AtlasPM 技术因子选股器和 Quant 优化器。默认选四只、目标投资比例最多 60%、单股上限 30%，成本预留 10 个基点。草案股数按「目标股数 − 实际持仓 − 未成交买单股数 + 未成交卖单股数」计算。有效挂单占用买入资金或卖出库存，卖单预期收入不会提高本次可用现金。集中度和压力检查按买单先成交、卖单尚未成交的情景计算。任一检查拦截时只保存诊断和已计算的依据。

行情下载、IBKR 账户归一化和 What-If、次日复核、Paper 成交及对账的进度见 [TODO](../TODO.md)。
