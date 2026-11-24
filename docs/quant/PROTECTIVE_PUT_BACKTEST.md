# 保护性看跌期权回测

`protective-put-backtest` 用相同初始现金和持股比较两条净值曲线。一条只持股，另一条定期买入同一标的的看跌期权。期权付费、成交和现金入账复用历史回放引擎。该方法参考了月度滚动、持有价外看跌期权的公开基准，实际规则见下文。[Cboe PPUT 指数方法](https://cdn.cboe.com/api/global/us_indices/governance/PPUT_Methodology.pdf) · [期权回放输入](OPTION_HISTORY_REPLAY.md)

## 决策和成交

- 第一日计算信号，随后每隔指定交易日数选合约。用当时可见的双边报价，找行权价在标的价格指定比例以下、足以覆盖全部股数、能持有到计划平仓日的实物交割看跌期权。优先选离目标到期天数最近的到期日，同到期日优先选离目标执行价最近的行权价。
- 下一交易日用可用的实际买卖报价模拟成交。换仓卖出用买价、买入用卖价。当天卖出款计入应收，按输入的时间转为可用，买入需要已有资金。若缺报价、可见数量或资金，回测报错，保留该次实验可追溯性。
- 最后一个交易日卖出剩余期权。合约均在到期前平仓，保证两组持股数量始终相同。报告包含保费进出、手续费、相对报价中点的价差成本、成交明细、覆盖股数和每日净值。价差成本已反映在成交价中，两组持股分红均在除息日计入净值，留在独立账项，现金收益率设为零。价格使用未复权收盘价，回测区间内持股数须无拆分调整。

股票估值输入 `stock_marks.csv` 的表头为 `observed_at,close,sale_cash_at,dividend_per_share`。按交易日排序并提供每个交易日的输入，由数据源确定有效的报价时间和卖出款可用时间。`dividend_per_share` 可省略。合约、报价和结算 CSV 格式与期权回放相同。到期前全部平仓的试验可提供只有表头的结算 CSV。

## 复跑 2022 年下半年样本

从 HistoricalData.net 的公开样本页下载期权 ZIP，保存为 `local_data/options_sample_2022H2.zip`。准备脚本逐日校验发布方的文件哈希，与 NYSE 日历对齐，筛选常规 SPY 看跌合约，并记录价差有效的报价覆盖率。样本的报价字段只有日期，脚本以 NYSE 收盘后 15 分钟作为期权日终快照时间。下一个 NYSE 开盘作为卖出款可用时间。运行时使用 `--eod-snapshot` 标记时间假设。[HistoricalData.net 期权样本](https://historicaldata.net/options.html) · [State Street SPY 分红](https://www.ssga.com/au/en_gb/institutional/etfs/state-street-spdr-sp-500-etf-spy)

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[calendar]'
.\.venv\Scripts\python.exe scripts\quant\prepare_spy_put_sample.py local_data\options_sample_2022H2.zip local_data\spy_put_2022h2
.\.venv\Scripts\python.exe -m atlaspm.cli protective-put-backtest --contracts local_data\spy_put_2022h2\contracts.csv --quotes local_data\spy_put_2022h2\quotes.csv --settlements local_data\spy_put_2022h2\settlements.csv --stock-marks local_data\spy_put_2022h2\stock_marks.csv --underlying SPY --shares 100 --cash-usd 5000 --eod-snapshot --output local_data\spy_put_2022h2\report.json
```

准备脚本要求新的输出目录。样本覆盖 2022 年 7 月 1 日至 12 月 30 日的 127 个交易日，保留了 272,047 条可用双边报价。固定使用 21 个交易日换仓、5% 价外及目标 45 天到期的设置，产生 12 笔模拟成交。支付权利金 3,255 美元，卖出收回 2,691 美元，手续费 12 美元，对冲累计使期末净值少 576 美元。

| 组合 | 期末净值 | 区间净收益 | 最大回撤 |
|---|---:|---:|---:|
| 持股加保护性看跌期权 | $43,004.78 | -0.28% | -12.92% |
| 相同持股和初始现金 | $43,580.78 | +1.06% | -14.91% |

结果衡量该样本在日终快照成交假设下的表现。样本无逐笔报价时间，期权成交和入账均由模型模拟，且该策略在到期前平仓。原始及规范化行情保存在 `local_data/`，复跑所需数据自公开样本页取得。[HistoricalData.net 期权样本](https://historicaldata.net/options.html)
