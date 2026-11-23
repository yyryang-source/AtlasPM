# AtlasPM 功能清单

AtlasPM 是一个本地运行的量化研究与组合风险工具。它能准备行情和财报数据、做选股和组合回测、研究期权与国债对冲、核对账户风险并生成待审核的调仓草案。

本清单以 `feature/rebalance-workbench` 的代码基线 `6ae82a2` 为准。按用途共 **8 类功能**，包括 **55 个 `atlaspm` CLI 子命令**，以及共用同一后台的调仓审核页面与独立命令。辅助数据脚本另列。新增需求、实测缺口和开发进度见 [TODO.md](TODO.md)。

| 功能域 | 当前主要用途 | 主 CLI 命令数 |
| --- | --- | ---: |
| 行情与研究数据 | 下载美股行情、维护历史股票池、导入财报、检查覆盖率 | 10 |
| 股票筛选与 AI 排名 | 技术与基本面选股、LightGBM 排名、月末候选清单 | 9 |
| 组合优化、回测与风险预算 | 计算权重、比较净值与基准、模拟成本和风险限制 | 11 |
| 机器学习组合模型 | 比较风险模型、训练 Atlas 自研模型、分析历史稳健性 | 4 |
| 期权、国债与多资产对冲 | 回放交割与现金流、比较保护性看跌期权和股债组合 | 5 |
| 账本、订单计划与成交回放 | 账本估值、草案预检查、离线模拟成交和对账 | 6 |
| IBKR 只读接入与风控报告 | 读取账户快照、诊断保证金和融资、输出风险看板 | 10 |
| 调仓审核台 | 把数据检查、选股、仓位、草案及人工审核串成一页 | 独立入口 |

仓库根目录运行 `.\.venv\Scripts\python.exe -m atlaspm.cli --help` 可查看全部子命令。在具体命令后加 `--help` 可查看输入文件、参数和输出位置。依赖和样本从 [README 快速开始](../README.md#windows-powershell-quick-start) 获取，数据字段见 [DATA.md](DATA.md)。

## 1. 行情与研究数据

| 命令 | 当前能力 | 说明 |
| --- | --- | --- |
| `download-prices` | 按指定标的和日期区间下载复权日线 CSV。 | [历史数据流程](../README.md#historical-data-workflow-v02) |
| `stock-universe` | 用 Nasdaq Trader 上市目录生成当前美国交易所上市候选池，可导入本地目录文件。 | [美股股票池](selection/US_MARKET_SCREENER.md) |
| `stock-sync-market` | 按股票池分批下载复权行情，记录失败和覆盖率，同一日期区间可复用逐股缓存，也可离线用缓存重建。 | [美股行情扩充](selection/US_MARKET_DATA_EXPANSION.md) |
| `stock-market-audit` | 按 NYSE 日历检查当前股票池的决策日收盘价、历史长度、缺口和覆盖率。 | [全市场行情审计](selection/US_MARKET_DATA_EXPANSION.md) |
| `stock-history-audit` | 核对各历史调仓日能用哪些股票池快照、是否缺决策日收盘价。 | [历史成分与退出](selection/HISTORICAL_MEMBERSHIP_AND_EXITS.md) |
| `stock-split-reconcile` | 审计有证据的反向拆股，可把确认的修正写入单独的研究行情文件。 | [公司行动与报价核对](selection/CORPORATE_ACTION_PRICE_RECONCILIATION.md) |
| `stock-import-membership-spans` | 将成分加入、退出区间展开为各基准交易日可用的股票池。 | [历史成分与退出](selection/HISTORICAL_MEMBERSHIP_AND_EXITS.md) |
| `stock-import-cash-exits` | 用可核对的现金退出对价、未复权和复权行情计算终止收益，供回测模拟退市结算。 | [历史成分与退出](selection/HISTORICAL_MEMBERSHIP_AND_EXITS.md) |
| `stock-import-sec-factors` | 用本地 SEC Company Facts、证券映射和行情生成按申报公开时间计算的财务因子。 | [SEC 财报与样本外研究](selection/SEC_FUNDAMENTALS_AND_HOLDOUT.md) |
| `stock-import-fundamentals` | 将年度申报表和带日期的估值转换为选股器可读的基本面快照。 | [选股回测](selection/STOCK_WALKFORWARD.md) |

当前上市目录覆盖当前可发现的证券。历史回测可使用另行提供的历史成份、退市事件和行情。团队已留存 2020–2023 年历史标普 500 研究样本及其来源、覆盖和缺口说明。 [历史样本说明](selection/SEC_FUNDAMENTALS_AND_HOLDOUT.md)

## 2. 股票筛选与 AI 排名

| 命令 | 当前能力 | 说明 |
| --- | --- | --- |
| `stock-screen` | 按指定日用动量、均线、波动、回撤及可选基本面因子打分，输出排序与排除原因。支持历史股票池、上升趋势、最低市值和行业数量限制等参数。 | [多因子选股器](selection/STOCK_SCREENER.md) |
| `stock-ai-train` | 按时间切分训练集、验证集和测试集，训练第一版 LightGBM 模型，标签基于未来相对 SPY 的收益。 | [AI 选股模型 V1](selection/AI_STOCK_RANKER.md) |
| `stock-ai-rank` | 加载本地训练的第一版模型，对指定日股票输出候选排序。 | [AI 选股模型 V1](selection/AI_STOCK_RANKER.md) |
| `stock-ai-train-v2` | 用 LightGBM `rank_xendcg` 训练按横截面相对表现排序的模型，并报告验证指标。 | [AI 选股模型 V2](selection/AI_STOCK_RANKER_V2.md) |
| `stock-ai-rank-v2` | 用已训练的 V2 模型输出序数排名和分数并列诊断。 | [AI 选股模型 V2](selection/AI_STOCK_RANKER_V2.md) |
| `stock-ai-diagnose-momentum` | 回看 12–1 月动量 Top N 的异常收益、异常价格和股票样本，无需重新训练。 | [动量异常诊断](selection/V2_MOMENTUM_FORENSICS.md) |
| `stock-ai-monthly` | 按 NYSE 月末最后交易日生成候选和研究报告，检查行情覆盖、模型新鲜度、并列分数等条件。 | [月末 AI 选股](selection/MONTHLY_ALPHA_SELECTION.md) |
| `stock-alpha-gate` | 用月末报告生成排名百分位候选名单，检查覆盖率、模型时效和并列边界。 | [研究候选资格](selection/ALPHA_ELIGIBILITY_GATE.md) |
| `stock-alpha-check` | 检查指定日、股票和加仓或减仓等假设操作是否符合候选名单规则。结果供研究筛选。 | [研究候选资格](selection/ALPHA_ELIGIBILITY_GATE.md) |

## 3. 组合优化、回测与风险预算

| 命令 | 当前能力 | 说明 |
| --- | --- | --- |
| `analyze` | 从账户 JSON 计算净值、杠杆、借款成本、假设维持保证金和市场下跌压力。 | [组合分析快速开始](../README.md#windows-powershell-quick-start) |
| `allocate` | 根据年化波动和单项权重上限生成逆波动率配置。 | [组合优化概览](../README.md#what-works-today) |
| `optimize` | 根据输入的预期收益与协方差做约束均值方差优化，支持风险厌恶、权重上限和假设融资成本。 | [组合优化概览](../README.md#what-works-today) |
| `estimate` | 用对齐日期的复权价格估算年化收益、波动与协方差。 | [历史数据流程](../README.md#historical-data-workflow-v02) |
| `backtest` | 仅用当时已知历史滚动调仓，模拟手续费、借贷成本、现金或融资组合及可选的触线后次收盘去杠杆。 | [组合回测](quant/BACKTEST.md) |
| `audit` | 比较两份已存的现金和融资回测报告，整理收益、回撤和压力风险。 | [回测审计](quant/AUDIT.md) |
| `benchmark-spy` | 对齐精确交易日和独立 SPY 买入持有基准，计算相对表现与风险。 | [SPY 基准](quant/BENCHMARK.md) |
| `risk-check` | 检查一笔假设配置的杠杆、集中度、波动、压力损失、保证金缓冲和融资成本。 | [风险预算](quant/RISK_BUDGET.md) |
| `backtest-risk` | 动态按风险预算调整现金和风险仓位，可用历史预期损失 ES 约束尾部风险。 | [动态风险预算](quant/ADAPTIVE_RISK.md) · [尾部风险](quant/TAIL_RISK.md) |
| `stock-backtest` | 用多因子排名做只做多的滚动回测，对照等权和 SPY，计入费用与滑点，可读取历史股票池和现金退市事件。加 `--quant` 可对同一入选名单做考虑相关性、单股/行业集中度、成本和换手的优化。 | [选股滚动回测](selection/STOCK_WALKFORWARD.md) |
| `stock-compare` | 在固定独立测试窗口和共同可用股票池，对照池内等权、技术因子、技术加基本面和 LightGBM 四组。 | [真数据样本外四法对比](selection/SEC_FUNDAMENTALS_AND_HOLDOUT.md) |

股票策略四法对照和 Quant 敏感性测试使用了 2022–2023 年真实历史样本。样本区间、数据覆盖、成本和约束参数随报告保存，便于复跑。 [实验设置和结果](selection/SEC_FUNDAMENTALS_AND_HOLDOUT.md)

## 4. 机器学习组合模型

| 命令 | 当前能力 | 说明 |
| --- | --- | --- |
| `ml-rebalance-backtest` | 训练 Ridge 或 LightGBM 波动预测模型，在历史模拟中用预测与硬风险约束调整仓位。 | [ML 动态调仓](quant/ML_DYNAMIC_REBALANCE.md) |
| `ml-model-tournament` | 用已经过去且标签已完整的预测比较 Ridge、LightGBM 和历史波动基线，可选冠军模型或模型集成。 | [模型对照实验](quant/ML_MODEL_TOURNAMENT.md) |
| `atlas-native-backtest` | 训练并回测 Atlas 自研模型的双头、RiskNet 风险单头或 FusionNet 多尺度风险架构。共享历史数据与调仓限制，FusionNet 可比较逆风险、下行风险和相关性收缩配置。 | [原生模型](quant/ATLAS_NATIVE_MODEL.md) · [RiskNet](quant/ATLAS_RISKNET_V2.md) · [FusionNet](quant/ATLAS_FUSIONNET_V1.md) |
| `atlas-native-audit` | 读取 RiskNet 与双头模型已有回测结果，做配对历史对照、区块自助法和压力区间检查。 | [稳健性审计](quant/ATLAS_ROBUSTNESS_AUDIT.md) |

RegimeNet 与交易执行约束中的不交易区间属于原生模型研究路径。模型用于本地历史模拟，结果的验证范围由各报告记录。 [原生模型研究](quant/ATLAS_NATIVE_MODEL.md) · [风险模型限制](quant/ATLAS_RISKNET_V2.md)

## 5. 期权、国债与多资产对冲

| 命令 | 当前能力 | 说明 |
| --- | --- | --- |
| `option-import-lean-quotes` | 把 LEAN 分钟期权报价 ZIP 转换为回放使用的标准报价 CSV。 | [期权行情导入](quant/OPTION_HISTORY_REPLAY.md) |
| `option-history-replay` | 输入合约、历史买卖报价、结算值和计划，模拟长仓开平仓、权利金、价差、手续费及现金或实物交割。 | [历史期权回放](quant/OPTION_HISTORY_REPLAY.md) |
| `protective-put-backtest` | 用历史期权链选择并滚动持有保护性看跌期权，对照同等持股的净值、成本与最大回撤。 | [保护性看跌期权回测](quant/PROTECTIVE_PUT_BACKTEST.md) |
| `treasury-replay` | 回放已持有美国固定票息国债或零息国库券的净价、应计利息、票息、本金、DV01 和利率冲击。 | [国债持有回放](quant/TREASURY_REPLAY.md) |
| `multiasset-hedge-backtest` | 在相同期初资本与日期下比较纯股票、股债和股债加看跌期权，报告净值、回撤、股票暴露、保护成本和利率风险。 | [多资产对冲回测](quant/MULTIASSET_HEDGE_BACKTEST.md) |

已有 SPY 历史期权报价、SPXW 实际结算值与美国国债历史估值的研究样本。2022 年下半年的联合样本已用于三组组合对照。 [期权样本](quant/OPTION_HISTORY_REPLAY.md) · [联合样本](quant/MULTIASSET_HEDGE_BACKTEST.md)

## 6. 账本、订单计划与成交回放

| 命令 | 当前能力 | 说明 |
| --- | --- | --- |
| `institutional-check` | 基于输入的账户状态、报价、策略限额和模拟券商预览检查订单意图的前置条件。 | [组合风控架构](quant/INSTITUTIONAL_ARCHITECTURE.md) |
| `ledger-report` | 用现金、股票、美国国债、期权和债务计算本地多资产账本，核对所提供的净值和估值。 | [多资产账本](quant/INSTITUTIONAL_LEDGER.md) |
| `plan-equity` | 给已持有的股票生成单笔增减仓提案，计算交易后的现金、敞口和风险预检查。 | [股票增减仓提案](quant/EQUITY_PLANNER.md) |
| `plan-basket` | 联合计算股票、美国国债、保护性看跌期权及融资债务的多腿组合提案。 | [多资产篮子](quant/MULTI_ASSET_BATCH.md) |
| `oms-replay` | 回放合成订单事件，覆盖部分成交、撤单、拒单等状态，维护现金和持仓，逐次成交检查敞口并生成本地 SHA-256 事件链。 | [离线订单回放](quant/PAPER_OMS.md) · [成交后核对](quant/OMS_RISK_AUDIT.md) |
| `broker-reconcile` | 导入券商导出快照，与本地账本核对净值、资金、债务、持仓及抵押品差异。 | [离线券商对账](quant/BROKER_RECONCILIATION.md) |

## 7. IBKR 只读接入与风控报告

| 命令 | 当前能力 | 说明 |
| --- | --- | --- |
| `ibkr-readonly` | 经用户认证的本地 Client Portal Gateway 读取账户、USD 账本与持仓原始数据，支持离线样例。 | [IBKR 只读接入](quant/IBKR_READONLY.md) |
| `ibkr-map-check` | 诊断 IBKR conid、证券及持仓数量单位的映射。 | [IBKR 合约映射](quant/IBKR_MAPPING.md) |
| `ibkr-financing-check` | 把导出的融资或保证金记录与只读快照、合约映射做离线核对。 | [融资与保证金核对](quant/IBKR_FINANCING.md) |
| `ibkr-margin-monitor` | 读取本地 Gateway 的当前保证金摘要并核对同会话 USD 账本、时间戳和缓冲阈值，也可运行离线样例。 | [只读保证金监测](quant/IBKR_MARGIN_MONITOR.md) |
| `ibkr-stress` | 对给定下跌和融资成本情景模拟净值及保证金压力。 | [保证金压力测试](quant/IBKR_MARGIN_STRESS.md) |
| `ibkr-multiasset-budget` | 模拟多资产共同下跌、相关性与融资风险预算。 | [多资产风险预算](quant/IBKR_MULTI_ASSET_BUDGET.md) |
| `ibkr-reverse-stress` | 反推假设跌幅下触及保证金或流动性缓冲阈值的位置。 | [反向压力测试](quant/IBKR_REVERSE_STRESS.md) |
| `ibkr-risk-audit` | 核对已有私有风险诊断报告之间的计算、输入关联和完整性。 | [风险证据链审计](quant/IBKR_RISK_CHAIN_AUDIT.md) |
| `ibkr-audit-diff` | 比较两份本地风险审计快照，识别警告、假设与结论变化。 | [风险审计差异](quant/IBKR_RISK_AUDIT_DIFF.md) |
| `ibkr-dashboard` | 用已有风险报告生成脱敏的本地静态 HTML 看板。 | [IBKR HTML 风险看板](quant/IBKR_DASHBOARD.md) |

直连入口限于本地已认证的 Gateway 只读访问，适合采集和监控。现有快照、离线风险报告与真正的 IBKR Paper 账户下单属于不同的验收阶段。 [只读接入范围](quant/IBKR_READONLY.md) · [待验收任务](TODO.md#7-收盘调仓与审核)

## 8. 调仓审核台

调仓审核台是第 8 类功能，复用第 2、3 类的选股和仓位计算。它为专用 USD 现金账户的美股整数多头提供一条本地审核流程。

1. 按 NYSE 日历判断决策日是否收盘，检查股票池决策日收盘价、连续 253 个交易日复权历史和未复权参考价。
2. 用等权或 Quant 优化计算目标权重和目标股数。
3. 导入账户持仓、现金和未成交挂单，计算持仓差额及订单草案。
4. 检查现金与费用缓冲、挂单冲突、单股集中度、换手与下跌压力，拦截不合格草案。
5. 在浏览器对比持仓市值、查看草案及风控原因，记录人工审核，按状态筛选历史并导出 CSV 或 JSON。SQLite 保存运行、输入哈希及审核记录。

| 入口 | 用法 |
| --- | --- |
| 浏览器 | 通过 Streamlit 运行 `src/atlaspm/rebalance_dashboard.py`，默认打开 `http://127.0.0.1:8501`。 |
| 独立后端命令 | `.\.venv\Scripts\python.exe -m atlaspm.rebalance_workbench --demo`，也可指定日期和四份输入文件。 |

已用合成账户验收两种仓位算法、拦截和审核记录，用 2023-12-29 四只股票的真实历史行情与模拟账户复验草案。下一阶段包括收盘定时更新、IBKR 账户直连与 What-If、次日复核、Paper 下单及成交对账。 [启动和输入格式](quant/REBALANCE_WORKBENCH.md) · [待开发清单](TODO.md#7-收盘调仓与审核)

## 辅助脚本

| 脚本 | 当前用途 |
| --- | --- |
| [`Invoke-USMarketDataExpansion.ps1`](../scripts/selection/Invoke-USMarketDataExpansion.ps1) | 手动串接上市目录、批量行情缓存和全市场覆盖审计。 |
| [`Invoke-MonthlyStockResearch.ps1`](../scripts/selection/Invoke-MonthlyStockResearch.ps1) | Windows 月末股票研究入口，根目录 `scripts/` 下另有同名兼容入口。 |
| [`fetch_kibot_daily.py`](../scripts/selection/fetch_kibot_daily.py) | 按代码映射下载和缓存 Kibot 日线，生成复权或未复权样本文件。 |
| [`prepare_spy_put_sample.py`](../scripts/quant/prepare_spy_put_sample.py) | 将 SPY 历史期权压缩包整理成保护性看跌期权回测输入。 |
| [`fetch_fedinvest_quotes.py`](../scripts/quant/fetch_fedinvest_quotes.py) | 抓取指定国债和日期的 TreasuryDirect 历史估值，保留原始报价文件。 |
