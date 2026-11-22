# Risk and operational safety

This repository contains **research tools**, not financial advice or a broker-certified margin model.

- Account-specific margin requirements are hypothetical inputs and must not be mistaken for a broker's actual real-time house or regulatory requirements.
- The model assumes only long positions, uniform stress shocks, unchanged cash and debt, instantaneous repricing, and constant maintenance percentages.
- Broker maintenance requirements can increase suddenly; brokers may liquidate positions before or without a traditional margin call.
- The simple borrowing calculation ignores daily accrual, changing rates, spread tiers, and fees.
- No live-trade execution exists in v0.1.
- Do not add a credential to Git, README, tests, examples, or issue comments.
- Do not use this engine to determine maximum safe leverage.
- Future broker support must use least-privilege credentials, paper trading first, explicit opt-in for live mode, idempotency protections, hard order caps, a kill switch, reconciliation, and an audit log.
