# 历史期权回放

`option-history-replay` 用历史买卖报价回放指定的美国期权长仓交易，记录权利金、买卖价差、手续费、到期和交割。报告写入 `local_data/`。合约条款和到期结算值由数据源提供。现金结算指数期权和标准实物交割美股期权的条款示例见 [OCC 指数期权](https://www.theocc.com/clearance-and-settlement/clearing/index-options)和 [OCC 股票期权](https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications)。

## 输入文件

| 文件 | CSV 表头 |
|---|---|
| `contracts.csv` | `contract_id,underlying,right,strike,last_trade_at,expiration_at,multiplier,exercise_style,settlement_type,exercise_threshold_usd` |
| `quotes.csv` | `contract_id,quote_at,bid,ask,bid_size,ask_size` |
| `settlements.csv` | `contract_id,reference_at,available_at,cash_at,settlement_value` |

- 所有时间戳都带 UTC 偏移。报价时间表示该报价可被使用的时点，价格按报价单位填写，买卖盘大小按合约张数填写。
- 合约方向用 `call` 或 `put`，行权方式用 `american` 或 `european`，交割用 `cash` 或 `physical`。`exercise_threshold_usd` 是每张合约的行权门槛。执行价、报价和结算参考值按乘数转换成美元金额。实物交割模型适用于可交付整数股的单一标的合约。
- `reference_at` 是结算参考值对应的时点，`available_at` 是其公布时间，`cash_at` 是交割时间。持有到期的合约必须有匹配的结算记录。若全部在到期前平仓，结算文件只需表头。

交易计划 `plan.json` 示例。卖出时须提供资金可用的 `cash_at`，买入时即时扣留资金。时间点和合约代码应与输入数据一致。

```json
{
  "initial_cash_usd": 1000,
  "initial_shares": {"SPY": 100},
  "fee_per_contract_usd": 1,
  "max_quote_age_minutes": 1,
  "orders": [
    {"at": "2023-08-03T13:31:00Z", "contract_id": "SPY-20230901-P-430", "side": "buy", "contracts": 1},
    {"at": "2023-08-03T14:01:00Z", "contract_id": "SPY-20230901-P-430", "side": "sell", "contracts": 1,
     "cash_at": "2023-08-04T13:30:00Z"}
  ],
  "valuation_times": ["2023-08-03T13:31:00Z", "2023-08-03T14:01:00Z", "2023-08-04T13:30:00Z"]
}
```

## Windows PowerShell

若已有规范化的报价 CSV，直接运行第二条命令。导入器按标的、行权方式、方向、执行价和到期日匹配合约，直到每根分钟线结束才发布其收盘买卖报价。导入时请选择尚未存在的 CSV 路径。[LEAN 文件格式](https://github.com/QuantConnect/Lean/blob/master/Data/option/readme.md) · [LEAN 报价线时间](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/time-modeling/periods)

```powershell
.\.venv\Scripts\python.exe -m atlaspm.cli option-import-lean-quotes --zip local_data\spy_option_quotes.zip --contracts local_data\contracts.csv --output local_data\quotes.csv
.\.venv\Scripts\python.exe -m atlaspm.cli option-history-replay --contracts local_data\contracts.csv --quotes local_data\quotes.csv --settlements local_data\settlements.csv --plan local_data\plan.json --output local_data\option_history_replay.json
```

买入用卖价，卖出用买价，成交受当时可见的报价及可见数量约束。报告按指定时间列出可用与待交割现金、股票数量、期权仓位和可用买价估值。价差成本以报价中点为参照，已体现在成交价中。模型按到期处理行权，完整策略研究还需组合标的估值、滚动换仓和真实结算数据。

## 样本验收

用 LEAN 的 2023 年 8 月 3 日 SPY 分钟数据导入了同一张 430 行权价看跌期权的 405 条报价。按固定时间模拟买入一张，权利金 227 美元，随后模拟卖出收入 215 美元。两次手续费各 1 美元，卖出款于计划里的下一交易日转为可用现金，现金从 1,000 美元变为 986 美元，持股始终是 100 股。数据版本及文件哈希在本地 `local_data/lean_spy_option_20230803/manifest.json`。单日样本用于验收导入、成交和现金对账。多日组合结果见[保护性看跌期权回测](PROTECTIVE_PUT_BACKTEST.md)。[样本 ZIP](https://github.com/QuantConnect/Lean/blob/32a302a9a0fde64c0435798c0c4cb2c2309873ed/Data/option/usa/minute/spy/20230803_quote_american.zip)

## 真实 SPXW 到期参考值验收

使用 HistoricalData.net 的 2022 年 9 月 15 日日终报价，模拟以 18.30 点买入一张行权价为 3,900 点的 `SPXW220916P03900000`。SPXW 为欧式现金结算，下午到期系列根据成分股收盘价格计算参考值。S&P Dow Jones Indices 经 FRED 发布的 2022 年 9 月 16 日收盘值为 3,873.33 点，与历史样本当日标的收盘一致。[HistoricalData.net 期权样本](https://historicaldata.net/options.html) · [Cboe SPXW 合约条款](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/) · [S&P 500 官方收盘序列](https://fred.stlouisfed.org/series/SP500)

权利金为 1,830 美元，模拟手续费为 1 美元。按 `(3,900 - 3,873.33) × 100` 算得应收行权款 2,667 美元。到期后，在参考值可用前，应收仍为零。参考值可用后记入应收，下一个营业日变为可用资金。初始现金 10,000 美元，最终现金 10,836 美元。实际输入使用日终报价和官方收盘，报价快照、参考值可用和入账的日内具体时间为回放设定。来源、输入哈希和报告存于本地 `local_data/spxw_expiry_20220916/`。[OCC 现金结算规则](https://www.theocc.com/clearance-and-settlement/clearing/index-options)
