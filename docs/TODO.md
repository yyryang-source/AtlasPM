# AtlasPM 开发清单

按选股器、Quant、期权、债券、对冲的顺序推进。每项通过对应测试或回测后勾选。用合成数据验收的项目会注明验证范围，真实数据验收单独记录。

## 1. 选股器

- [x] `stock-screen` 输出指定日的候选排名和排除原因。使用历史股票池时采用决策日已知的快照。
- [x] `stock-backtest` 支持滚动调仓、统一成本、等权和 SPY 基线。合成数据覆盖成分变动、缺价和按公布日期取基本面。
- [x] `stock-history-audit` 列出各调仓日不可用的快照和缺少收盘价的股票。合成数据已验证汇总覆盖率及审计与回测日程一致。
- [x] `stock-backtest --delistings` 处理有记录的单笔现金退市结算。合成数据已验证延迟回款、归零、未成交资金和等权基线对账。
- [x] `stock-import-fundamentals` 生成按披露时间可用的年度财报快照，保留财报和市值来源。已用苹果 2020 年 10-K 验证收盘后披露的生效日期。
- [x] 用四只股票和 SPY 的真实日线跑通单日排名及 60 次历史调仓，本地数据、报告和哈希记录在 `local_data/stock_research_public_sample_manifest.json`。样本为现存股票，验证范围是流程。
- [x] 导入 2020–2023 年按日开放标普 500 历史成员记录，生成 1,006 个日期的研究股票池并保留来源置信度。
- [x] `stock-import-cash-exits` 接入 TWTR 和 ATVI 的实际每股现金对价。在 5 股受控案例中用真实行情完成 `stock-screen`、`stock-backtest` 和两笔模拟退市入账。
- [ ] 整理真实历史成分、行情、退市退出数据和按公布日期保存的财报，记录数据来源与覆盖率。
- [x] `stock-compare` 对齐等权、技术因子、技术加基本面、LightGBM 的调仓日、可投资范围和成本。合成数据已验证四组指标、训练与测试隔离，以及输入和模型哈希。
- [ ] 用真实历史数据固定训练、验证和最终测试期，保留配置和数据版本，完成样本外收益、回撤和换手对比。

已有入口见[选股回测说明](selection/STOCK_WALKFORWARD.md)。第一版使用 Python 和本地文件。

历史股票池、现金退出和实际覆盖率见[历史股票池验收](selection/HISTORICAL_MEMBERSHIP_AND_EXITS.md)。

财报可从 [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) 获取。历史上市状态和退市回报分别核对 [Alpha Vantage 上市状态文档](https://www.alphavantage.co/documentation/#listing-status)和 [CRSP 退市回报定义](https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-data-descriptions-guide-crspaccess/)，再确认团队数据的可用年份与覆盖率。

## 2. Quant 组合优化

- [x] `stock-backtest --quant` 将选股排名接到目标仓位计算，保留同一批入选股的等权分仓作为对照。
- [x] 纳入相关性、单股和行业集中度、换手上限及交易成本。合成数据已验证约束、权重、现金和成交成本对账。
- [x] 用同一批候选和同一时间窗口比较优化前后的净收益、回撤和换手，报告限制触发和跳过原因。四股真实样本的报告在 `local_data/stock_research_public_sample_quant.json`。
- [ ] 在独立测试期的历史股票池上复验 Quant，检查成本和参数敏感性。

## 3. 期权

- [x] 接入带时间戳的期权合约和买卖报价，校验标的、执行价、到期日和合约乘数。用 LEAN 的 SPY 单日样本验收导入与交易回放。
- [x] 补齐长仓期权的权利金、买卖价差、到期与结算模拟。真实报价验证买卖与现金对账，合成数据验证到期、结算与持仓变化。
- [x] 整理 SPY 的多日历史期权链和买卖报价，记录来源及覆盖率。2022 年下半年样本覆盖 127 个交易日，按日终快照模拟成交。
- [x] 用真实历史期权链回测保护性看跌期权，报告保费、成本和同等持股的回撤对比。样本在到期前平仓。
- [x] 接入可核对的真实到期结算参考值。用 2022 年 9 月 16 日到期的 SPXW 看跌期权验证现金结算，正价差全额乘以 100，入账前列应收。

命令、输入格式和样本记录见[历史期权回放](quant/OPTION_HISTORY_REPLAY.md)和[保护性看跌期权回测](quant/PROTECTIVE_PUT_BACKTEST.md)。

## 4. 债券

- [x] 接入国债合约资料和历史报价，校验面值、票息、付息日和到期日。用财政部拍卖资料与 TreasuryDirect 的 `91282CAC5` 估值净价验收。
- [x] `treasury-replay` 记录票息、应计利息、到期本金及利率冲击。真实条款和 5 个估值报价验收 7 个日期的净值对账，包括周日到期与周一入账。
- [x] 运行股票和债券联合回测，报告债券现金流与利率风险暴露。2022 年下半年使用 125 个 TreasuryDirect 国债估值日，另两个股市开市日按公示的债市休市日期沿用净价。

见[国债持有回放](quant/TREASURY_REPLAY.md)。

## 5. 对冲

- [x] `multiasset-hedge-backtest` 在同一回测框架内比较股票、股债组合和股债加保护性看跌期权的组合。
- [x] 记录股票市值暴露、覆盖股数、国债 DV01 和利率冲击、期权保护成本和最大回撤。逐日核对三组净值和每笔期权成交。
- [x] 在相同日期、60,000 美元起始资金和一致的成本规则下，用 2022 年下半年 SPY、10 年期国债和 SPY 日终期权报价回测，并报告 2022 年 9 月压力区间的结果。

见[多资产对冲回测](quant/MULTIASSET_HEDGE_BACKTEST.md)。

## 存储和界面

- [x] 本地 CLI 和 HTML 风险看板可用于读取研究结果和查看风控报告。
- [ ] 历史数据查询、版本管理或多人共享出现明确需求时，评估数据库选型。
- [ ] 团队需要在浏览器操作参数和对照实验结果时，评估前端接入。
