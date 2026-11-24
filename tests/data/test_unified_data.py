"""Offline tests for the unified reader; fixtures confer no data approval."""
from contextlib import closing
from decimal import Decimal
from importlib.util import find_spec
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atlaspm.data import hf_stock5m, market_db, unified


class UnifiedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cwd = Path.cwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, cwd)
        with closing(market_db.connect(market_db.DB_PATH)) as con:
            con.executemany("INSERT INTO equity_daily VALUES (?,?,?,?)", [
                ("A", "2020-01-02", 10., "fixture"),
                ("A", "2020-01-03", 11., "fixture"),
                ("B", "2020-01-02", 12., "fixture"),
            ])
            con.commit()

    def query(self, dataset="equity_daily", **kwargs):
        args = dict(start="2020-01-01", end="2020-01-31",
                    purpose="exploratory", batch_size=1)
        args.update(kwargs)
        return unified.iter_batches(dataset, **args)

    def test_sqlite_projection_filter_bounds_and_flags(self):
        batches = list(self.query(symbols=("A",), columns=("adjusted_close",)))
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0].columns, ("symbol", "trade_date", "adjusted_close", "source"))
        self.assertEqual(batches[0].rows, (("A", "2020-01-02", 10., "fixture"),))
        for batch in batches:
            self.assertFalse(batch.point_in_time_verified)
            self.assertFalse(batch.approved_for_model_training)
            self.assertFalse(batch.approved_for_backtest)
            self.assertFalse(batch.live_execution_enabled)
        self.assertEqual(list(self.query(symbols=("A' OR 1=1 --",))), [])
        self.assertEqual(len(list(self.query(start="2020-01-03"))), 1)

    def test_reject_permissions_and_invalid_requests(self):
        for args in (dict(purpose="training"), dict(purpose="backtest"),
                     dict(as_of="2020-01-03T21:00:00Z")):
            with self.assertRaises(unified.AdmissionDenied):
                list(self.query(**args))
        for args in (dict(columns=("close; DROP TABLE equity_daily",)),
                     dict(batch_size=True), dict(batch_size=0),
                     dict(start="2010-01-01"), dict(start="20200101"),
                     dict(database=Path("outside.sqlite3"))):
            with self.assertRaises(ValueError):
                list(self.query(**args))
        for name in unified.UNCONNECTED:
            with self.assertRaises(unified.DataUnavailable):
                list(self.query(name))

    def test_missing_data_does_not_create_db(self):
        missing = Path("local_data/missing.sqlite3")
        with self.assertRaises(unified.DataUnavailable):
            list(self.query(database=missing))
        self.assertFalse(missing.exists())
        with self.assertRaises(unified.DataUnavailable):
            list(self.query("equity_5m"))

    def test_read_only_and_early_close(self):
        before = market_db.DB_PATH.read_bytes()
        with closing(self.query()) as batches:
            self.assertEqual(len(next(batches).rows), 1)
        self.assertEqual(before, market_db.DB_PATH.read_bytes())
        self.assertEqual(len(list(self.query())), 3)

    def test_bad_selected_value_fails_closed(self):
        with closing(market_db.connect(market_db.DB_PATH)) as con:
            con.execute("UPDATE equity_daily SET source='' WHERE symbol='A'")
            con.commit()
        with self.assertRaises(unified.DataQualityError):
            list(self.query())

    @unittest.skipUnless(find_spec("duckdb"), "lake extra required")
    def test_parquet_projection_duplicate_and_missing_partition(self):
        import duckdb
        path = hf_stock5m.output_path("2020-01")
        path.parent.mkdir(parents=True)
        quoted = str(path).replace("\\", "/").replace("'", "''")
        with closing(duckdb.connect()) as con:
            con.execute(f"""COPY (SELECT 'A' AS symbol, DATE '2020-01-02' AS trade_date_et,
                1577975400000::BIGINT AS timestamp_ms, 10.0 AS close)
                TO '{quoted}' (FORMAT PARQUET)""")
        batch, = list(self.query("equity_5m", columns=("close",)))
        self.assertEqual(batch.rows[0][-1], Decimal('10.0'))
        self.assertIsInstance(batch.rows[0][-1], Decimal)
        with self.assertRaises(unified.DataUnavailable):
            list(self.query("equity_5m", columns=("close",), end="2020-02-01"))
        path.unlink()
        with closing(duckdb.connect()) as con:
            con.execute(f"""COPY (SELECT 'A' AS symbol, DATE '2020-01-02' AS trade_date_et,
                1577975400000::BIGINT AS timestamp_ms, 10.0 AS close FROM range(2))
                TO '{quoted}' (FORMAT PARQUET)""")
        with self.assertRaises(unified.DataQualityError):
            list(self.query("equity_5m", columns=("close",)))

    def test_numeric_validation_preserves_decimal_precision(self):
        for value in (1, 1.25, Decimal("0.0000000000000000001"), Decimal("1e1000")):
            with self.subTest(value=value):
                self.assertTrue(unified._finite_numeric(value))
        for value in (True, "10", None, float("nan"), float("inf"),
                      Decimal("NaN"), Decimal("sNaN"), Decimal("-Infinity")):
            with self.subTest(value=value):
                self.assertFalse(unified._finite_numeric(value))

    @unittest.skipUnless(find_spec("duckdb"), "lake extra required")
    def test_parquet_decimal_and_invalid_prices(self):
        import duckdb
        path = hf_stock5m.output_path("2020-01")
        path.parent.mkdir(parents=True)
        quoted = str(path).replace("\\", "/").replace("'", "''")
        for expression, valid in (
            ("CAST(12.345678901234567890 AS DECIMAL(38,18))", True),
            ("CAST(1.25 AS DOUBLE)", True),
            ("CAST(1 AS BIGINT)", True),
            ("CAST(0 AS DECIMAL(10,2))", False),
            ("CAST(-1 AS DECIMAL(10,2))", False),
            ("CAST('NaN' AS DOUBLE)", False),
            ("CAST('Infinity' AS DOUBLE)", False),
            ("NULL::DECIMAL(10,2)", False),
            ("TRUE", False),
            ("'10'", False),
        ):
            with self.subTest(expression=expression):
                if path.exists():
                    path.unlink()
                with closing(duckdb.connect()) as con:
                    con.execute(f"""COPY (SELECT 'A' AS symbol,
                        DATE '2020-01-02' AS trade_date_et,
                        1577975400000::BIGINT AS timestamp_ms, {expression} AS close,
                        0.0::DECIMAL(10,2) AS volume_shares)
                        TO '{quoted}' (FORMAT PARQUET)""")
                if valid:
                    batch, = list(self.query("equity_5m", columns=("close", "volume_shares")))
                    self.assertGreater(batch.rows[0][-2], 0)
                    self.assertEqual(batch.rows[0][-1], Decimal("0.00"))
                    if "DECIMAL(38,18)" in expression:
                        self.assertEqual(batch.rows[0][-2], Decimal("12.345678901234567890"))
                else:
                    with self.assertRaises(unified.DataQualityError):
                        list(self.query("equity_5m", columns=("close",)))
