# AtlasPM 量化研究工作台

工作台把股票筛选、同股等权与 Quant 回测、已有期权和国债报告、多资产对冲及调仓审核放在同一个本地浏览器界面。

## 启动

在仓库根目录用 PowerShell 运行。没有虚拟环境时先执行 `python -m venv .venv`。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[workbench]"
.\.venv\Scripts\python.exe -m streamlit run .\src\atlaspm\research_dashboard.py --server.headless true
```

在浏览器打开 `http://127.0.0.1:8501`。如需另开端口，在启动命令末尾加 `--server.port 8504`。单独打开调仓审核的命令及输入格式见 [调仓审核台](REBALANCE_WORKBENCH.md)。

## 页面

| 页面 | 操作 |
| --- | --- |
| 工作台总览 | 查看本地报告和最近的任务，打开各研究页。 |
| 股票筛选 | 选股票池和决策日，运行因子选股，查看排名、入选和排除原因。时间戳与历史股票池按 CLI 规则校验。 |
| 回测与 Quant | 选日期区间、调仓周期、费用、滑点和 Quant 限额。同一入选名单生成等权和 Quant 两条净值，另列股票池等权和 SPY 对照。 |
| 期权研究 | 打开保护性看跌期权与历史回放报告，查看权利金、成交、到期与交割。 |
| 国债研究 | 查看历史净价、应计利息、DV01、利率冲击损益和票息本金入账。 |
| 多资产对冲 | 比较相同期初资本下的股票、股债、股债加保护性看跌期权，查看保护成本和压力区间。 |
| 调仓审核 | 导入账户快照或运行样例，生成并审核美股现金账户的调仓草案。 |
| 研究任务 | 查看运行状态、输入版本、参数与日志，下载已生成的原始报告。 |

## 数据和运行记录

- `local_data/` 中存在相应历史数据时，股票页提供四股与历史标普 500 样本。新检出的仓库可用合成样例或导入自己的 CSV。复权日线和 SPY 日线列为 `date,symbol,adjusted_close`，也可导入历史股票池、按公布日记录的财报因子及回测所需退市记录。参见[选股回测格式](../selection/STOCK_WALKFORWARD.md)。
- 已有报告可以从预设位置打开，也可以上传由 AtlasPM CLI 生成的 JSON。期权、国债与对冲页面使用这一流程，报告生成命令见 [期权回放](OPTION_HISTORY_REPLAY.md)、[国债回放](TREASURY_REPLAY.md)和[多资产对冲](MULTIASSET_HEDGE_BACKTEST.md)。
- 页面提交的选股和回测在后台进程调用现有 CLI。每次运行在 `local_data/runs/<编号>/` 保存 CSV 输入副本、`job.json` 参数及输入哈希、`run.log` 和成功时的 `report.json`。切换页面不影响已经登记的任务，关闭页面后也可从「研究任务」回看。运行失败时可查看退出码和日志。
