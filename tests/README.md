# AtlasPM regression suites

Run all tests: `python -m unittest discover -s tests -v`.

| Directory | Responsibility |
| --- | --- |
| data/ | Market data, Parquet lake, factors and quality |
| research/ | AI training, memory modes, models and checkpoints |
| interfaces/ | CLI, compatibility and workflow integration |
| selection/ | Stock screening and alpha eligibility |
| quant/ | Portfolio/risk models, financing and paper OMS |
| root replay/portfolio tests | Unified engine and replay; retained at root to preserve shared fixtures on the existing replay branch |

Run focused suites with root discovery so shared fixtures resolve:
```powershell
python -m unittest discover -s tests -p "test_training*.py" -v
python -m unittest discover -s tests -p "test_factor*.py" -v
python -m unittest discover -s tests -p "test_historical_replay*.py" -v
```
Never use private broker captures or production price history as CI fixtures.
