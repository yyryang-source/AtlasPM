# 股票、国债和保护性看跌期权联合回测

`multiasset-hedge-backtest` 用同一组股票交易日和同一笔初始资金比较三个组合。

| 组合 | 初始头寸 |
|---|---|
| 股票 | 保留最低现金准备金，其余按首日估值价和成本购买整数股 |
| 股票 + 国债 | 指定股票股数和国债面值，以首日净价加应计利息确定债券投入，其余留作现金 |
| 股票 + 国债 + 看跌期权 | 与股债组的初始持仓和现金完全一致，此后用既定现金预算滚动买入覆盖全部股数的看跌期权 |

所有组合从同一初始资金计算收益和回撤。股票和国债按各自的首日估值价建仓，并按相同规则扣除各资产的开仓成本。股票和国债按期末市值估值。期权根据上一个交易日可见的报价和标的价格选合约，下一交易日按买卖报价和可见数量成交，期末平仓。股票和国债的第一日收入归原持有人，之后的股票分红按除息日计入报告，债券票息和本金按付款日进入现金账。两类收入留在原来的账项，既定期权预算只使用初始分配和已结算的期权卖出款。[保护性看跌规则](PROTECTIVE_PUT_BACKTEST.md) · [国债回放规则](TREASURY_REPLAY.md)

报告逐日保存三个组合净值、股票市值占比、期权覆盖股数、保护成本、国债现金流、DV01 和利率冲击损益。股票市值占比用于衡量持股暴露，覆盖股数记录期权对应的标的股数。指定 `--stress-window START END` 时，额外按压力区间首日的收盘净值计算该区间收益和最大回撤。股市开市但债市休市的日期须列在 `--treasury-closed`，回测沿用最近的债券净价并记录来源日期。其他交易日缺少有效国债净价会停止回测。

## 复跑 2022 年下半年样本

股票和期权输入来自 [SPY 样本准备步骤](PROTECTIVE_PUT_BACKTEST.md)。国债使用 CUSIP `91282CEP2`，原始起息日为 2022 年 5 月 15 日、发行日为 5 月 16 日，票息率为 `0.02875`，面值增量为 100 美元，到期日为 2032 年 5 月 15 日。每年 5 月 15 日和 11 月 15 日付息，首期为 2022 年 11 月 15 日，周末付款移至下一营业日。按[国债输入格式](TREASURY_REPLAY.md)生成 `local_data/multiasset_2022h2/contracts.csv` 与 `payments.csv`。本次压力区间为 2022 年 9 月 1–30 日，覆盖 9 月 21 日美联储加息决定。[财政部拍卖结果](https://www.treasurydirect.gov/instit/annceresult/press/preanre/2022/R_20220511_2.pdf) · [美联储会议声明](https://www.federalreserve.gov/newsevents/pressreleases/monetary20220921a.htm)

```powershell
$stockMarks = 'local_data\spy_put_2022h2\stock_marks.csv'
$bond = 'local_data\multiasset_2022h2'
$bondHolidays = @('2022-10-10', '2022-11-11')
$priceDates = Import-Csv $stockMarks | ForEach-Object { $_.observed_at.Substring(0, 10) } | Where-Object { $_ -notin $bondHolidays }
.\.venv\Scripts\python.exe scripts\quant\fetch_fedinvest_quotes.py --contracts "$bond\contracts.csv" --cusip 91282CEP2 --raw-dir "$bond\source_prices" --output "$bond\quotes.csv" --dates @priceDates
.\.venv\Scripts\python.exe -m atlaspm.cli multiasset-hedge-backtest --contracts local_data\spy_put_2022h2\contracts.csv --quotes local_data\spy_put_2022h2\quotes.csv --settlements local_data\spy_put_2022h2\settlements.csv --stock-marks $stockMarks --treasury-contracts "$bond\contracts.csv" --treasury-payments "$bond\payments.csv" --treasury-quotes "$bond\quotes.csv" --underlying SPY --shares 100 --treasury-cusip 91282CEP2 --treasury-face-usd 15000 --initial-capital-usd 60000 --minimum-cash-usd 5000 --treasury-closed @bondHolidays --stress-window 2022-09-01 2022-09-30 --eod-snapshot --output "$bond\report.json"
```

节假日沿用上一债券交易日的净价，日期来自 SIFMA 公布的休市建议。SPY 期权只有日终快照，成交与资金入账时点使用前述样本假设。现金收益率为零，10 年期国债价格用 TreasuryDirect 的估值净价，开仓成交成本采用模型的基点设置。[TreasuryDirect 历史价格](https://www.treasurydirect.gov/GA-FI/FedInvest/selectSecurityPriceDate) · [SIFMA 10 月 10 日休市](https://www.sifma.org/news/press-releases/sifma-fixed-income-market-close-recommendations-in-the-u-s-the-u-k-and-japan-for-u-s-columbus-day-and-japan-health-and-sports-day-holidays) · [SIFMA 11 月 11 日休市](https://www.sifma.org/news/press-releases/sifma-fixed-income-market-close-recommendation-in-the-u-s-the-u-k-and-japan-for-the-u-s-veterans-day-holiday-4)

## 样本结果

125 个国债有效估值日和 2 个明确休市日对齐到 127 个 SPY 交易日。三组的初始资金均为 60,000 美元。股票组持有 144 股，股债两组各持有 100 股和面值 15,000 美元的同一只国债，开仓成本均按各交易资产市值的 5 个基点估计。

| 组合 | 全期净收益 | 全期最大回撤 | 9 月净收益 | 9 月最大回撤 |
|---|---:|---:|---:|---:|
| 股票 | +1.05% | −15.39% | −8.72% | −11.70% |
| 股票 + 国债 | −0.86% | −12.91% | −7.20% | −9.18% |
| 股票 + 国债 + 看跌期权 | −1.82% | −11.55% | −5.13% | −6.59% |

股债两组在 11 月 15 日各收到约 215.63 美元票息。开仓时国债 DV01 为约 13.01 美元，模型中利率上移 100 个基点的国债估值损益约为 −1,241.26 美元。期权买入权利金 3,255 美元，卖出收回 2,691 美元，手续费 12 美元，股债组加期权后期末净值少 576 美元。原始价格、合约、逐日资产估值和哈希都保存在本地 `local_data/multiasset_2022h2/`。[HistoricalData.net 期权样本](https://historicaldata.net/options.html)
