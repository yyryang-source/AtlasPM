"""Offline tests for the unified reader; fixtures confer no data approval."""
from contextlib import closing
from decimal import Decimal
from datetime import date
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

    def test_all_price_and_nonnegative_fields_share_numeric_rules(self):
        for field in ("open", "high", "low", "close", "adjusted_close",
                      "volume", "volume_shares", "bid", "ask", "bid_size", "ask_size"):
            for value in (1, 1.25, Decimal("1.234567890123456789")):
                with self.subTest(field=field, value=value):
                    unified._validate_field(field, value)
            for value in (-1, -1.25, Decimal("-0.01"), True, "1",
                          float("nan"), float("inf"), Decimal("NaN"),
                          Decimal("sNaN"), Decimal("-Infinity")):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(unified.DataQualityError):
                        unified._validate_field(field, value)
        for field in ("open", "high", "low", "close", "adjusted_close"):
            for value in (0, 0., Decimal("0.00"), None):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(unified.DataQualityError):
                        unified._validate_field(field, value)
        for field in ("volume", "volume_shares", "bid", "ask", "bid_size", "ask_size"):
            for value in (0, 0., Decimal("0.00"), None):
                with self.subTest(field=field, value=value):
                    unified._validate_field(field, value)

    @unittest.skipUnless(find_spec("duckdb"), "lake extra required")
    def test_raw_minute_read_only_rth_and_quality(self):
        import duckdb
        path = hf_stock5m.raw_path("2020-01")
        path.parent.mkdir(parents=True)
        quoted = str(path).replace("\\", "/").replace("'", "''")
        def write(rows):
            if path.exists():
                path.unlink()
            with closing(duckdb.connect()) as con:
                con.execute(f"""COPY (SELECT * FROM (VALUES {rows})
                    AS t(ticker,timestamp,open,high,low,close,volume))
                    TO '{quoted}' (FORMAT PARQUET)""")
        good = "(' a ', TIMESTAMPTZ '2020-01-02 14:30:00+00',10,11,9,10,0)"
        pre = "('A', TIMESTAMPTZ '2020-01-02 14:29:00+00',10,11,9,10,1)"
        write(good + "," + pre)
        before = path.read_bytes()
        batch, = list(self.query("equity_1m", columns=("close", "volume_shares"), symbols=("A",)))
        self.assertEqual(batch.rows[0], ("A", date(2020, 1, 2), 1577975400000, 10, 0))
        self.assertFalse(batch.approved_for_model_training)
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(unified.DataUnavailable):
            list(self.query("equity_1m", end="2020-02-01"))
        for bad in (good + "," + good,
                    good.replace("14:30:00", "14:30:01"),
                    good.replace("10,11,9,10,0", "10,9,9,10,0"),
                    good.replace("2020-01-02", "2020-02-03")):
            with self.subTest(bad=bad):
                write(bad)
                with self.assertRaises(unified.DataQualityError):
                    list(self.query("equity_1m", columns=("close",)))

    @unittest.skipUnless(find_spec("duckdb"), "lake extra required")
    def test_sec_candidate_index_cutoff_and_coverage(self):
        from atlaspm.data import hf_stock5m_filings_v52 as filings
        path = unified.private_path(filings.BASE / "index.duckdb")
        with self.assertRaises(unified.DataUnavailable):
            list(self.query("sec_filings", symbols=("123",), as_of="2020-02-01"))
        self.assertFalse(path.exists())
        with closing(filings._db()) as con:
            con.execute("INSERT INTO partitions VALUES ('2020-Q1', ?, 'fixture', 2, 0)", ["a"*64])
            con.execute("""INSERT INTO filings VALUES
                ('2020-Q1','0000000123','Issuer','8-K','2020-01-02','https://example.org/1'),
                ('2020-Q1','0000000123','Issuer','8-K','2020-01-03','https://example.org/2')""")
        args = dict(symbols=("123",), as_of="2020-01-03", end="2020-01-02")
        batch, = list(self.query("sec_filings", **args))
        self.assertEqual(batch.rows[0][0:2], ("0000000123", date(2020, 1, 2)))
        self.assertEqual(batch.rows[0][-2], "a"*64)
        self.assertFalse(batch.point_in_time_verified)
        with self.assertRaises(unified.AdmissionDenied):
            list(self.query("sec_filings", symbols=("123",), as_of="2020-01-31"))
        with self.assertRaises(unified.DataUnavailable):
            list(self.query("sec_filings", symbols=("123",), end="2020-04-01", as_of="2020-04-02"))
        with self.assertRaises(ValueError):
            list(self.query("sec_filings", symbols=("AAPL",), **{"end":"2020-01-02","as_of":"2020-01-03"}))

    def test_security_review_missing_ledger_and_no_approval(self):
        from atlaspm.data import hf_stock5m_security_master_v53 as master
        with self.assertRaises(unified.DataUnavailable):
            unified.review_security(as_of="2020-01-03")
        path = unified.private_path(master.BASE / "ledger.sqlite3")
        self.assertFalse(path.exists())
        with closing(master._db()):
            pass
        before = path.read_bytes()
        report = unified.review_security(as_of="2020-01-03")
        self.assertEqual(report["members"], [])
        self.assertEqual(report["actions"], [])
        self.assertFalse(report["approved_for_model_training"])
        self.assertFalse(report["approved_for_backtest"])
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(unified.AdmissionDenied):
            unified.review_security(as_of="2020-01-03", trade_date="2020-01-04")

    def test_fundamentals_provenance_cutoff_and_duplicates(self):
        from atlaspm.data.stock_fundamentals_import import _PROVENANCE_COLUMNS
        from atlaspm.strategies.equity.stock_screener import FUNDAMENTAL_COLUMNS
        path = Path("local_data/fundamentals.csv")
        header = ",".join(FUNDAMENTAL_COLUMNS + _PROVENANCE_COLUMNS)
        record = ("2020-01-03,A,Tech,100,0.1,0.1,0.2,0.03,10,"
                  "2020-01-02T21:00:00+00:00,2020-01-02,2019-12-31,filing,valuation")
        path.write_text(header + "\n" + record + "\n", encoding="utf-8")
        args = dict(start="2020-01-01", end="2020-01-03", as_of="2020-01-04",
                    purpose="exploratory")
        batch, = list(unified.iter_fundamentals(path, **args))
        self.assertEqual(batch.rows[0][3], 100.)
        self.assertFalse(batch.approved_for_model_training)
        with self.assertRaises(unified.AdmissionDenied):
            list(unified.iter_fundamentals(path, **{**args, "as_of":"2020-01-03"}))
        path.write_text(header + "\n" + record + "\n" + record + "\n", encoding="utf-8")
        with self.assertRaises(unified.DataQualityError):
            list(unified.iter_fundamentals(path, **args))

    def test_partition_manifest_changes_and_missing(self):
        path = hf_stock5m.output_path("2020-01")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"first")
        args = dict(start="2020-01-01", end="2020-01-31")
        first = unified.parquet_manifest("equity_5m", **args)
        path.write_bytes(b"other")
        second = unified.parquet_manifest("equity_5m", **args)
        self.assertNotEqual(first["partitions"][0]["sha256"], second["partitions"][0]["sha256"])
        self.assertFalse(second["approved_for_model_training"])
        with self.assertRaises(unified.DataUnavailable):
            unified.parquet_manifest("equity_5m", **{**args, "end":"2020-02-01"})

    def test_archive_plan_includes_unadvertised_months_and_no_writes(self):
        path = hf_stock5m.raw_path("2026-03")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture")
        report = unified.archive_plan("2026-03", "2026-04")
        first, second = report["months"]
        self.assertTrue(first["raw_present"])
        self.assertFalse(first["bars_present"])
        self.assertFalse(second["within_advertised_archive"])
        self.assertFalse(report["approved_for_model_training"])
        self.assertFalse(hf_stock5m.output_path("2026-03").exists())
        with self.assertRaises(unified.DataUnavailable):
            unified.audit_archive("2026-03", "2026-04")
        self.assertEqual(unified.audit_archive("2026-03", "2026-03")["mode"], "dry_run")

    def test_broker_capture_is_local_and_fixture_requires_explicit_opt_in(self):
        import json
        from unittest.mock import patch
        path = Path("local_data/capture.json")
        mapping = Path("local_data/mapping.json")
        path.write_text(json.dumps({"source": "synthetic_ibkr_fixture",
                                    "live_execution_enabled": False}), encoding="utf-8")
        mapping.write_text("{}", encoding="utf-8")
        with self.assertRaises(unified.AdmissionDenied):
            unified.review_broker_capture(path, mapping)
        with patch("atlaspm.execution.ibkr_mapping.diagnose_ibkr_contract_mapping",
                   return_value={"mapping_status": "BLOCK_MAPPING"}) as diagnose:
            result = unified.review_broker_capture(path, mapping, allow_fixture=True)
        diagnose.assert_called_once()
        self.assertTrue(result["synthetic_fixture"])
        self.assertFalse(result["live_execution_enabled"])
        self.assertFalse(result["approved_for_model_training"])
        with self.assertRaises(ValueError):
            unified.review_broker_capture(Path("outside.json"), mapping)
