# 历史股票池、行情和现金退市

`stock-import-membership-spans` 将证券在某个股票池中的日期区间展开为 `stock-screen` 与 `stock-backtest` 可用的交易日快照。输入列为 `ticker,cik,start,end,start_confidence`。`start` 含当日，`end` 为首个不再纳入的日期，仍在样本内的证券留空。CSV 保留每条证券记录的 CIK、加入日期和来源置信度。跨期复用同一代码却属于不同发行人的区间须先拆成可追溯的证券标识。

2020 至 2023 年样本使用固定版本的 `index-constituents` 作为标普 500 历史成员的研究记录。它综合公开成分变化、历史快照和未解释的变化，输出用于研究的按日股票池。在 `local_data/sp500_pit_research/` 保存原始版本、四组策略的预先固定区间和输入哈希。[历史成分数据版本及说明](https://github.com/dfjmax/index-constituents/tree/bb542830336cd9bff6145256854dd2013ff820ac)

`fetch_kibot_daily.py` 从公开客座会话取日线，用明确的 `symbol,provider_symbol` 映射表缓存原始回复，并报告每个证券的时间范围。复权价用于回测，`--unadjusted` 用于收盘价和现金对价必须采用同一原始每股单位的场景。核对历史代码的发行人和股票类别，例如 `BRK-B` 对应 Kibot 的 `BRK.B`。代码重用可能返回格式正确但发行人不同的报价，本次从覆盖率审计输入隔离了 `PARA` 的错误报价。[Kibot 日线字段与请求](https://www.kibot.com/api/history-request.html) · [Kibot 证券目录](https://www.kibot.com/reference/data-sources.html)

## 现金退市

`stock-import-cash-exits` 读取有来源的 `symbol,last_trade_date,cash_date,cash_per_share,source_ref`，获取同一证券最后交易日的未复权收盘，再计算 `现金对价 ÷ 未复权收盘 − 1`。回测把该比例用在复权账本的最后交易日持仓上。复权和未复权报价均须止于最后交易日，金额来源和输入最后价保留在输出。`cash_date` 表示可用现金入账日，再由回测在随后的首个估值点入账。

真实案例以 SEC 披露的 54.20 美元每股 Twitter 对价和 95 美元每股动视暴雪对价验收。用交易所公告确定交易停止日期，模拟在最后交易日后的第三个美股交易日收到现金。具体券商到账日由测算场景另行提供。[NYSE 提交的 Twitter 停牌及对价记录](https://www.sec.gov/Archives/edgar/data/876661/000087666122000890/ruleprovisionnotice.htm) · [Nasdaq 动视暴雪合并公告](https://nasdaqtrader.com/TraderNews.aspx?id=ECA2023-588) · [动视暴雪 SEC 文件](https://www.sec.gov/Archives/edgar/data/718877/000110465923108985/tm2328253d1_8k.htm)

```powershell
$sample = 'local_data\sp500_pit_research'
.\.venv\Scripts\python.exe -m atlaspm.cli stock-import-membership-spans --spans "$sample\dfjmax_periods.csv" --benchmark-prices "$sample\spy.csv" --start 2020-01-02 --end 2023-12-29 --output "$sample\membership.csv"
.\.venv\Scripts\python.exe -m atlaspm.cli stock-history-audit --prices "$sample\prices_identity_screened.csv" --benchmark-prices "$sample\spy.csv" --universe-history "$sample\membership.csv" --start 2022-02-01 --end 2023-12-29 --rebalance-days 21 --output "$sample\audit_identity_screened.json"
.\.venv\Scripts\python.exe -m atlaspm.cli stock-import-cash-exits --events "$sample\cash_exit_events.csv" --raw-prices "$sample\cash_exit_raw_prices.csv" --adjusted-prices "$sample\prices.csv" --output "$sample\delistings_verified.csv"
```

两条导入命令要求新的输出文件名。历史成分只是候选范围，实际入选还依赖当天的报价和收盘前可用的输入。股票被纳入或者移出指数，与证券停止交易或公司并购生效可能是不同的时间。处理持仓时以公告和实际报价为依据。

## 验收与覆盖率

- 2020–2023 年共生成 1,006 个交易日、587 个历史成分证券代码的快照，每日 502 至 507 个成员。2022 年 2 月至 2023 年底的 23 个预定调仓日应有 11,577 条当日收盘价，隔离错配后目前找到 11,552 条。`stock-history-audit` 为 `REVIEW_REQUIRED`。其余 25 条包括错配的 `PARA` 和三处代码更名连接，不能用别的证券行情补齐。
- 在 5 股受控篮子中，`stock-screen` 在第一日列出两只后来退市的股票，`stock-backtest` 使用实际收盘与显式退市输入完成 13 次调仓。TWTR 的 Kibot 未复权末日收盘为 53.78 美元，ATVI 为 94.44 美元。策略、等权对照和 `--quant` 组合各发生两笔模拟现金退市，逐笔持仓账值与入账比例已对上。该篮子专用于核对退市处理。
- `research_plan.json` 预先固定训练、验证和测试日期。广泛股票池的基本面数据以及 `stock-compare` 和 Quant 样本外结果仍待补齐。`manifest.json` 保存行情来源、缺价概况、退市验证结果与文件哈希。
