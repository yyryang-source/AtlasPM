# AtlasPM Tests

- [Stock-selection tests](selection/) cover market universe/quotes, AI ranking, monthly reports and Alpha Eligibility Gate.
- [Quantitative/risk tests](quant/) cover portfolio optimization, model training, backtests, broker read-only and synthetic OMS.
- Shared root tests cover the CLI interface.

Run the entire suite from the repository root: `python -m unittest discover -s tests -v`. Run a focused test anywhere recursively: `python -m unittest discover -s tests -p "test_ai_stock_ranker_v2.py" -v`.
