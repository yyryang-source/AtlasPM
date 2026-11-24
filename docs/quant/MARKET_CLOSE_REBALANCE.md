# 收盘行情与调仓草案

这条流程使用 NYSE 交易日历与收盘缓冲，更新指定股票池的行情，保存审计版本，再把通过的版本交给调仓后台。复权价用于选股，行情源的决策日 `Close` 用于估算股数。准备数据和生成草案都可以从命令行执行。

## 行情更新与审计

在仓库根目录打开 PowerShell。把例子里的股票换成团队确认的策略股票池。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[market,calendar,portfolio]"
.\.venv\Scripts\python.exe -m atlaspm.market_close --symbols AAPL IBM MSFT ORCL --schedule month-end
```

命令获取最近一个过了收盘缓冲的 NYSE 交易日，默认缓冲为 20 分钟。末尾的 `--watch` 按默认 900 秒轮询。Windows 也可用 PowerShell 注册当前用户登录时启动的任务。选择唯一任务名，`-StartNow` 从当前登录启动。

```powershell
.\scripts\selection\Register-MarketCloseWatcher.ps1 -TaskName 'AtlasPM Market Close' -Symbols AAPL,IBM,MSFT,ORCL -Schedule month-end -StartNow
```

注册脚本可用 `-DefinitionOnly` 检查任务定义，并用 `-Directory` 指定仓库 `local_data` 下的输出目录。任务忽略重复实例，异常退出后最多重启三次。当前机器返回 Task Scheduler 注册权限不足（`0x80070005`），验收时已直接运行生成的任务动作，并确认它能写出真实四股行情审计。实际注册需使用有权限的 Windows 会话，也可以在当前用户会话内直接运行 `--watch`。 [Windows 任务计划设置](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset)

当前用户的登录启动也可使用系统启动目录。下面的命令在本地数据目录生成一个使用同一参数的后台启动快捷方式。确认股票池后，将生成的 `.lnk` 放入 `Win+R` 打开的 `shell:startup`，Windows 会在当前用户下次登录时启动行情观察。此方式与计划任务任选其一。 [Windows 当前用户登录启动](https://support.microsoft.com/en-gb/windows/experience/startup-boot/configure-startup-applications-in-windows)

```powershell
.\scripts\selection\Register-MarketCloseWatcher.ps1 -TaskName 'AtlasPM Market Close' -Symbols AAPL,IBM,MSFT,ORCL -Schedule month-end -ExportStartupShortcut
```

日常收盘可用 `daily`，月末采用实际最后一个 NYSE 交易日。程序按交易所处理节假日、提前收盘和夏令时。默认每个决策日读取最新 Nasdaq Trader 上市目录，也可用 `--manifest` 指定已有目录。没有显式选股池时可运行全市场研究审计，结果记录实际覆盖范围。策略名单外的持仓可用 `--additional-quotes` 下载决策日参考价。

行情写入 `local_data/market_close/quotes.sqlite3`。首次为各股票下载所需历史，后续通常只请求最近 5 个已知交易日及新数据。重叠观察发生变动会重取这一段历史，避免旧复权基准和新基准拼接。通过单股检查的观察可在下一次复用，遇限流时停止本次请求。每次收盘审计报告保存在按决策日分区的目录中，轮询日志保存在 `watch.log`。

指定策略股票池要求同日收盘价及连续 253 个交易日可用。通过的尝试保存股票池、复权行情、参考价与 SHA-256。缺价、覆盖不足、更新失败、非调仓日或文件变化时，后台入口会拒绝生成调仓草案。Yahoo 日线用于本地研究和价格估算，提交前的价格应以独立的券商报价和风险检查为准。 [日线接口说明](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html)

## 生成草案

审核台的「收盘行情监控」默认读取 `local_data/market_close/watch.log`。如启动时指定了 `--directory` 或 `-Directory`，在页面切换到同一目录。页面显示最近记录时间、行情所属交易日、股票池收盘价覆盖和拦截原因，可下载对应审计文件。账户草案生成后，可从交接结果打开同一次调仓记录。日志在结果变化或发生错误时追加，记录时间也可能早于当前轮询时间。

把行情命令输出的 `audit` 完整路径填入下面命令。也可使用符合 [账户快照格式](REBALANCE_WORKBENCH.md#导入格式) 的本地 JSON。

```powershell
.\.venv\Scripts\python.exe -m atlaspm.rebalance_workbench --market-audit $auditPath --account local_data\rebalance\account.json --allocation quant
```

IBKR 只读入口使用已认证的本机 Client Portal Gateway 和受信 TLS。适用账户是专用 USD 现金账户，所有持仓与工作中的限价订单均为美股整数股。命令读取账本、持仓和当前订单，两次订单快照需要一致。可用资金取现金和资金字段的保守下界，未成交挂单只使用剩余股数。提供 `--market-audit` 时，审计通过后读取账户并生成草案。 [IBKR 只读安装](IBKR_READONLY.md) · [IBKR 未成交订单字段](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/order-monitoring/live-orders)

```powershell
.\.venv\Scripts\python.exe -m atlaspm.ibkr_equity_snapshot --account $accountId --dedicated-cash-equities --allow-local-gateway --market-audit $auditPath --allocation quant
```

Paper 账户只读验收可将 `--account $accountId` 换成 `--selected-paper-account`，直接使用浏览器登录后选中的 Paper 账户。省略 `--market-audit` 时只采集账户快照，控制台显示持仓与挂单数量及总现金字段是否可用。

如需在收盘审计通过后自动生成待审核草案，可给 `atlaspm.market_close` 添加 `--draft-account $accountId --dedicated-cash-equities --allocation quant`。Windows 注册脚本对应参数是 `-DraftAccount $accountId -DedicatedCashEquities -Allocation quant`，本地受信证书可用 `-GatewayCaFile` 指定。流程只在调仓日和收盘后四小时内读账户。池外持仓或挂单需要补参考价时，补齐后再次读取账户。新出现的持仓或挂单若仍缺参考价，本次先拦截。同一审计、账户和完整策略参数复用已有草案，采集窗口结束后仍可查看。

审计与账户输入版本记录在调仓报告，可在本地审核台查看。当前券商路径已使用合成 Gateway 响应验证账户类型、时间戳、部分成交、现金和合约映射。

## IBKR Paper 的 What-If 预览

在审核台记录人工审核后，使用审核台报告中的运行 ID。现有持仓及挂单里的合约 ID 自动读取，新标的按股票代码查询唯一美国股票合约。预览会用 Gateway 的合约详情核对标的、币种及证券类型。有歧义或无法查询时，可追加 `--contracts` 并提供代码到券商合约号的 JSON，例如 `{"AAPL": 265598}`。 [IBKR 按代码查询](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/contract/security-stocks-by-symbol) · [IBKR 合约详情](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/contract/contract-information-by-contract-id)

```powershell
.\.venv\Scripts\python.exe -m atlaspm.ibkr_whatif --run $runId --account $paperAccountId --allow-paper-preview --output local_data\rebalance\whatif.json
```

这个预览验证 Paper 会话、实时双边报价、与已审草案相关的持仓和挂单变化，并逐笔保存精确限价单、佣金及初始与维持保证金估计。现金校验计入买入挂单预留和估计佣金。券商警告、报价偏离、手续费超出资金或持仓变化会拦截。 [IBKR What-If](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/orders/preview-order-what-if-order)

## 次日复核与 Paper 生命周期

下一个 NYSE 正常交易时段，运行次日复核。它保留上一交易日核准的目标权重，重新读取 Paper 账户和所有相关股的实时双边报价，重新计算股数、买卖限价、费用、换手、现金、集中度、总敞口与下跌压力。它用逐笔 What-If 的佣金和保证金增量复核现金及风险，原草案和次日结果都会保留。到审核台记录新草案的人工审核，复制其新 ID。

审核台的「策略参数与输入版本」列出次日实际使用的 IBKR 合约号。若使用了 `--contracts` 文件，提交前复核也传入同一文件。合约发生变化时需重新审核。

在页面操作时，从「运行记录」选择已审核的收盘草案，在对应 NYSE 次日交易时段打开「IBKR Paper 次日复核」。使用浏览器登录后选中的 Paper 账户，可选填仓库 `local_data` 下的 PEM 证书路径并上传合约映射。点击运行后会保存并打开新的复核记录。

逐笔 What-If 结束后会重新读取 Paper 账户并确认报价仍在 30 秒有效期内、NYSE 正常时段仍开放。预览前后持仓、挂单、现金或合约发生变化时，本次草案拦截，报告保留复核结果。

```powershell
.\.venv\Scripts\python.exe -m atlaspm.ibkr_next_day --run $closeRunId --account $paperAccountId --allow-paper-preview
```

Paper 提交通过独立命令显式确认，要求使用已审核的次日运行 ID。提交前再次执行完整复核。实时买卖盘在已审核限价的 0.5% 内时保留原限价，仍会按当前市值计算股数和风控。仓位、挂单、资金或票据变更，以及券商费用或保证金增量上升时，命令保存一份新结果供审核。逐单发送前先保存客户订单号，遇回执不明、超时或券商警告时停止后续订单，把当前结果交给对账。每个账户和交易日只接受一个已登记批次。 [IBKR 下单与回执](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/orders/place-order)

```powershell
.\.venv\Scripts\python.exe -m atlaspm.ibkr_paper_orders submit --run $nextDayRunId --confirm-run $nextDayRunId --account $paperAccountId
.\.venv\Scripts\python.exe -m atlaspm.ibkr_paper_orders reconcile --run $nextDayRunId --account $paperAccountId --watch
.\.venv\Scripts\python.exe -m atlaspm.ibkr_paper_orders cancel --run $nextDayRunId --account $paperAccountId --client-id $clientOrderId
```

页面可从已登记 Paper 批次的「同步成交与对账」执行单次只读查询。它使用 Gateway 当前选中的 Paper 账户，保存订单和成交状态、现金和持仓差额，并提供对账 JSON 下载。需要持续跟踪时，使用命令行的 `reconcile --watch`。

每个 Gateway 客户端会按券商速率限制间隔发起请求，挂单、成交和账户列表分别控制查询间隔。`reconcile --watch` 在首次或后续查询暂时无法连接时会继续轮询，重新登录后可接着核对同一批订单。 [IBKR 请求速率限制](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/#pacing-limitations)

订单从列表消失、且已有券商单号时，对账会尝试单笔状态查询。缓存若提供终态，需核对账户、合约、股数、下单时间及两次观测，以确认属于当前批次。券商无法提供该旧单状态时保持待核实，可继续核对成交。撤单需要在本次查询中观察到仍在工作的订单。 [IBKR 单笔订单状态](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/order-monitoring/order-status)

对账用客户订单号关联券商回执，以成交编号去重，核对成交方向、价格、数量、佣金与当前现金和持仓。撤单请求仍在等待终态时，后续挂单观察只补入成交信息。跨交易日尚未查清的提交或撤单回执会阻止同一账户发送新批次。支持追加 `--output local_data\rebalance\paper_reconciliation.json` 保存核对结果。报价、单号、成交和撤单状态从本地 SQLite 日志读给审核台。Paper 路径已用合成 Gateway 响应验证，实际登录、Brokerage API 返回字段和行情权限仍需要账户实测。 [IBKR 订单](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/order-monitoring/live-orders) · [成交](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/order-monitoring/trades) · [撤单](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/orders/cancel-order) · [待验收任务](../TODO.md#7-收盘调仓与审核)
