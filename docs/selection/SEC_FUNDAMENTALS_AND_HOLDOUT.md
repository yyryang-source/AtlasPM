# SEC 财报与固定样本外验证

`stock-import-sec-factors` 将 SEC Company Facts 年报和历史未复权行情转成 `stock-screen`、`stock-backtest` 与 `stock-compare` 能读取的因子。报告以 CIK、申报编号和申报日记录来源。同一条快照的金额必须取自同一次申报，申报日之后首个基准交易日收盘才可使用。修订重列的数值随其自己的申报日生效。快照市值按申报内最近的流通在外普通股数与可用日未复权收盘计算，是基于已披露股数的估计。[SEC Company Facts](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

进入因子表需要可比的完整年度收入、权益、利润、债务、现金流、资本开支和折旧摊销。回测按相同约定使用 ROE、收入增速、债务权益比、自由现金流收益率和 EV/EBITDA。缺少可比数据、映射到同一 CIK 的多个交易股类和不适用的财务比率会在导入时记录。余额项使用同一申报的已标记金额，应以原报告复核缺失或公司自定义标签。

## 数据与模型设置

- 2020–2023 年使用历史标普 500 成分的研究记录，保留成分来源置信度。用公告核对更名、并购和交易停止时间，将同一证券的代码变更连接到连续行情。Paramount 用固定版本 FINSABER 档案中的上市期间行情，与更名前的重叠报价核对。L Brands 使用分拆后持续交易代码的供应商复权行情，分拆发生在最终测试期之前。[原始历史成员记录](https://github.com/dfjmax/index-constituents/tree/bb542830336cd9bff6145256854dd2013ff820ac) · [FINSABER 历史行情](https://huggingface.co/datasets/finsaber-team/FINSABER-reproduce/tree/e156a6a5a36fce2eab49c3431eb0194ea85252b4/data/price) · [L Brands 公告](https://investors.bbwinc.com/news-releases/news-release-details/bath-body-works-inc-completes-separation-victorias-secret/)
- TWTR、ATVI、CERN 和 NLSN 的现金对价用公告确认。IHS Markit 按每股换取 0.2838 股 SPGI 模拟在并购生效日收盘卖出接收股票，扣同一策略费率，按当时的 T+2 规则在 2022 年 3 月 2 日入账。回放在应收结算前使用末日持仓账值。原始交易停止日期和对价可复核，收款日是账务模型输入。[IHS Markit 交易条款](https://www.sec.gov/Archives/edgar/data/64040/000119312522056007/d321269d8k.htm) · [Cerner 公告](https://www.sec.gov/Archives/edgar/data/804753/000119312522169841/d223229d8k.htm) · [Nielsen 公告](https://www.sec.gov/Archives/edgar/data/1492633/000119312522260583/d407513d8k.htm) · [SEC 交割规则](https://www.sec.gov/rules-regulations/2023/02/34-96930)
- 训练决策区间从 2020 年 1 月 2 日到 2021 年 3 月 31 日，此后的验证区间截至 2021 年 12 月 31 日。最终测试期固定为 2022 年 2 月 1 日至 2023 年 12 月 29 日。训练与验证用于拟合和选择迭代次数，采用完成标签且不跨分界的整组样本。测试每 21 个交易日调仓，信号在决策日收盘后生成，下一交易日收盘成交，手续费和滑点各 5 个基点。四组使用相同的当日因子可用股票池，三种排名方法均用 Top 20，等权对照纳入同一池内所有合格股票。

## 复跑命令

`securities_sec_v3.csv` 含 `symbol,cik,sector`。每份 SEC 缓存存为 `sec_facts/CIK##########.json.gz`。未复权长表列为 `date,symbol,close`，用于估计市值。下面的文件位于本地 `local_data/sp500_pit_research/`，`holdout_manifest.json` 记录输入与结果的哈希。

```powershell
$p = 'local_data\sp500_pit_research'
.\.venv\Scripts\python.exe -m atlaspm.cli stock-import-sec-factors --securities "$p\securities_sec_v3.csv" --facts-directory "$p\sec_facts" --raw-prices "$p\raw_prices_continuous_v3.csv" --benchmark-prices "$p\spy.csv" --start 2019-01-02 --end 2023-12-29 --output "$p\sec_fundamentals_v3.csv"
.\.venv\Scripts\python.exe -m atlaspm.cli stock-compare --prices "$p\prices_continuous_v3.csv" --benchmark-prices "$p\spy.csv" --universe-history "$p\membership_continuous_v3.csv" --fundamentals "$p\sec_fundamentals_v3.csv" --delistings "$p\holdout_delistings_10bps.csv" --train-start 2020-01-02 --train-end 2021-03-31 --validation-end 2021-12-31 --test-start 2022-02-01 --test-end 2023-12-29 --top 20 --rebalance-days 21 --fee-bps 5 --slippage-bps 5 --max-fundamental-age-days 455 --num-boost-round 300 --output "$p\stock_compare_2022_2023.json"
.\.venv\Scripts\python.exe -m atlaspm.cli stock-backtest --prices "$p\prices_continuous_v3.csv" --benchmark-prices "$p\spy.csv" --universe-history "$p\membership_fundamental_eligible_2022_2023.csv" --fundamentals "$p\sec_fundamentals_v3.csv" --delistings "$p\holdout_delistings_10bps.csv" --start 2022-02-01 --end 2023-12-29 --top 20 --rebalance-days 21 --fee-bps 5 --slippage-bps 5 --max-fundamental-age-days 455 --quant --quant-max-weight 0.15 --quant-sector-cap 0.35 --quant-turnover-limit 0.8 --quant-risk-aversion 2 --output "$p\quant_holdout_base.json"
```

重新导入财报时须使用新的输出文件名。Quant 的日期快照取自 `stock-compare` 当日的 `selected.equal_weight`，代表四组共同候选。Quant 与它自己的等权分仓在每个调仓日使用相同的 Top 20。独立比较入口和 Quant 入口的并列规则不同，报告保留了各自下单名单。

## 实际验证结果

拟合与测试区间的 47 个调仓日收盘价审计覆盖 23,697 条应有报价，均有匹配。564 家发行人的 SEC 缓存产生 265 个股票代码的 1,004 条年报指标快照。最终 23 次调仓中，每次有 160 至 183 只能同时算出价格和财报指标的股票。LightGBM 实际使用训练期 1,610 行、验证期 661 行，最佳迭代次数为 30。模型保留训练、验证各自最后一笔标签日期和模型哈希。按整组剔除了训练期 4 次和验证期 4 次缺完整标签的调仓组，另有 2 次调仓组因为标签跨边界或超出验证区间而剔除。

| 2022-02-01 至 2023-12-29 | 净收益 | 最大回撤 | 累计换手 |
|---|---:|---:|---:|
| 符合共同数据要求的股票全部等权 | +7.02% | −19.26% | 2.78 倍 |
| 技术因子 Top 20 | −4.49% | −20.35% | 14.80 倍 |
| 技术 + 基本面 Top 20 | −9.24% | −20.94% | 14.82 倍 |
| LightGBM Top 20 | −6.36% | −27.76% | 20.37 倍 |

Quant 子实验对同样的入选名单比仓位。基准参数为 63 个交易日风险估计、单股最高 15%、行业最高 35%、每次流动权益换手上限 80%、风险厌恶系数 2。基准费率下，Quant 组合净收益为 −8.39%，同一批入选股的等权分仓为 −9.22%。最大回撤从 −20.93% 缩小至 −18.86%，累计换手从 14.75 倍降至 12.94 倍。23 次均求解成功，其中单股上限触发 16 次、行业上限 12 次、换手上限 6 次。

| 双组采用的单边成本 | 风险厌恶系数 | 等权分仓净收益 | Quant 净收益 |
|---|---:|---:|---:|
| 0 基点 | 2 | −7.87% | −9.28% |
| 10 基点 | 2 | −9.22% | −8.39% |
| 20 基点 | 2 | −10.55% | −9.18% |
| 10 基点 | 1 | −9.22% | −6.82% |
| 10 基点 | 4 | −9.22% | −10.01% |

零成本与风险厌恶系数为 4 时，Quant 净收益低于同股等权。成本是其求解输入，改变成本会改变仓位和换手。四法对照只有一次基本面 Top 20 分界并列，独立四法报告剔除了跨分界的并列组，而 Quant 子实验按 `stock-backtest` 的顺序从同一候选池给两个分仓组合下达相同的选股名单。样本的结论适用于本数据来源、因子资格与历史区间。
