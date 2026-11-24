# 国债持有回放

`treasury-replay` 读取美国固定票息国债或零息国库券的条款、付息与兑付日期、历史净价，重建持有期间的净值、应计利息、票息入账和本金兑付。用同一组未来现金流报告 DV01 和利率冲击后的市值。输入头寸在第一条估值前已持有，`--cash-usd` 是该头寸以外的期初现金余额。[美国财政部拍卖资料](https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/) · [TreasuryDirect 定价说明](https://www.treasurydirect.gov/marketable-securities/understanding-pricing/)

| 文件 | CSV 表头 |
|---|---|
| `contracts.csv` | `cusip,kind,dated_on,issued_on,maturity,annual_coupon_rate,face_increment_usd` |
| `payments.csv` | `cusip,scheduled_on,paid_on` |
| `quotes.csv` | `cusip,observed_on,clean_close_per_100` |

- `kind` 使用 `bill`、`note` 或 `bond`。票息率使用小数，0.125% 填 `0.00125`。固定票息品种使用原始发行日期和完整的常规半年付息表，国库券只填本金到期行。输入覆盖单一美元面值的不含赎回权、通胀或浮息条款的品种。
- `scheduled_on` 是合约日期，`paid_on` 是实际入账日。到期或付息日在联储休业日时，现金在下一营业日支付。应计利息按本计息期已过日数占实际期长的比例计算。票息到期即停止累积该期应计，付款尚未到账时列为应收，避免与脏价重复记账。[31 CFR §356.30 和附录 B](https://treasurydirect.gov/files/laws-and-regulations/auction-regulations-uoc/31-cfr-part-356.pdf)
- 估值日仅用当日或此前最近的净价。使用旧净价时，报告写明报价日期并检查价龄上限。压力模型按实际天数除以 365，用一个连续复利平坦利率拟合当天的脏价，再平移该利率重估未到期现金流。它给出指定冲击的损益和 1 个基点的价格敏感度，显示的利率是模型参数。已到期应收和现金不随该利率冲击重估。

## TreasuryDirect 样本

先将 Treasury Fiscal Data 的原始拍卖结果映射到 `contracts.csv`，把百分比票息除以 100，核对券种、首付息日和到期日。用公告和实际营业日填入完整 `payments.csv`。下列脚本从 TreasuryDirect 的公开历史价格页面取指定日期的 CSV，核对 CUSIP、票息及到期日后，使用 `END OF DAY` 作为估值净价。零值或缺价的日期返回在下载摘要中。该估值价用于历史持有回放。[TreasuryDirect 历史价格](https://www.treasurydirect.gov/GA-FI/FedInvest/selectSecurityPriceDate)

```powershell
.\.venv\Scripts\python.exe scripts\quant\fetch_fedinvest_quotes.py --contracts local_data\treasury_91282cac5\contracts.csv --cusip 91282CAC5 --dates 2022-01-28 2022-01-31 2022-07-01 2022-07-15 2022-07-29 --raw-dir local_data\treasury_91282cac5\source_prices --output local_data\treasury_91282cac5\quotes.csv
.\.venv\Scripts\python.exe -m atlaspm.cli treasury-replay --contracts local_data\treasury_91282cac5\contracts.csv --payments local_data\treasury_91282cac5\payments.csv --quotes local_data\treasury_91282cac5\quotes.csv --cusip 91282CAC5 --face-usd 10000 --cash-usd 250 --dates 2022-01-28 2022-01-31 2022-07-01 2022-07-15 2022-07-29 2022-07-31 2022-08-01 --output local_data\treasury_91282cac5\report.json
```

验收使用两年期、0.125% 票息的 `91282CAC5`，面值 10,000 美元。5 个真实交易日的净价用于 7 个估值点。2022 年 1 月 31 日票息入账 6.25 美元，7 月 31 日到期形成 10,006.25 美元应收，8 月 1 日转成可用现金。全程票息 12.50 美元，加上期初现金和本金得到期末现金 10,262.50 美元。7 月 29 日独立复算的应计利息约为 6.18 美元，平坦利率上移 100 个基点对当日净值影响约为 −0.82 美元。原始来源和报告哈希写入本地 `local_data/treasury_91282cac5/manifest.json`。[该券拍卖记录](https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query?filter=cusip%3Aeq%3A91282CAC5)
