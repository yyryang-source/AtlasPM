"""Synthetic factor mart regression tests; never use live or vendor price data."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from atlaspm.data import factor_db, market_db

try:
    import duckdb
except ImportError:
    duckdb = None


class FactorContractTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        os.chdir(self.workspace.name)
        Path('local_data').mkdir()

    def tearDown(self):
        os.chdir(self.old)
        self.workspace.cleanup()

    def test_catalog_and_empty_status(self):
        self.assertEqual(12, len(factor_db.catalog()['factors']))
        self.assertEqual(0, factor_db.status()['months'])
        self.assertEqual([], factor_db.query('AAPL')['rows'])
        self.assertFalse(factor_db.catalog()['approved_for_model_training'])

    def test_dry_run_has_no_download_or_disk_write(self):
        report = factor_db.build('2026-03', dry_run=True)
        self.assertFalse(report['source_exists'])
        self.assertFalse(report['requires_network'])
        self.assertFalse(Path('local_data/factors').exists())

    def test_invalid_symbols_and_months(self):
        with self.assertRaises(ValueError):
            factor_db.query('AAPL;DROP')
        with self.assertRaises(ValueError):
            factor_db.build('2026-13')


@unittest.skipUnless(duckdb, 'pip install -e ".[lake]" for factor integration tests')
class FactorDuckDBTests(FactorContractTests):
    def _month_trading_days(self, month: str):
        y, m = map(int, month.split('-'))
        day = date(y, m, 1)
        while day.month == m:
            if day.weekday() < 5:
                yield day
            day += timedelta(days=1)

    def _write_month(self, month: str, duplicate: bool = False):
        path = factor_db.hf_output_path(month)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for idx, day in enumerate(self._month_trading_days(month)):
            for symbol, base in (('AAPL', 100.0), ('MSFT', 200.0)):
                clock = datetime(day.year, day.month, day.day, 15, 55,
                                 tzinfo=ZoneInfo('America/New_York'))
                stamp = int(clock.timestamp() * 1000)
                close = base + idx
                row = (symbol, day.isoformat(), stamp, close - 0.5, close + 0.5,
                       close - 1, close, 1000.0, None, 1, 1)
                rows.append(row)
        if duplicate:
            rows.append(rows[0])
        cx = duckdb.connect()
        try:
            cx.execute('''CREATE TABLE bars (
                symbol VARCHAR, trade_date_et DATE, timestamp_ms BIGINT,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                volume_shares DOUBLE, transactions BIGINT,
                source_minute_bars BIGINT, unique_source_minutes BIGINT)''')
            cx.executemany('INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?)', rows)
            cx.execute(f"COPY bars TO '{path.as_posix()}' (FORMAT PARQUET)")
        finally:
            cx.close()
        return path

    def test_hf_materialization_query_idempotency_and_audit(self):
        self._write_month('2026-01')
        self._write_month('2026-02')
        factor_db.build('2026-01', memory='256MB', threads=1)
        result = factor_db.build('2026-02', memory='256MB', threads=1)
        self.assertEqual('built', result['state'])
        self.assertEqual(2, result['daily_input_months'])
        self.assertGreater(result['non_null_factor_values'], 0)
        self.assertEqual('existing', factor_db.build('2026-02', memory='256MB', threads=1)['state'])
        rows = factor_db.query('AAPL', start='2026-02-01', end='2026-02-28',
                               memory='256MB', threads=1)['rows']
        self.assertTrue(rows)
        self.assertIsNotNone(rows[0]['mom_21d'])
        self.assertAlmostEqual(1, rows[0]['volume_ratio_20d'], places=9)
        self.assertTrue(factor_db.audit('2026-02', memory='256MB', threads=1)['valid'])
        self.assertEqual(2, factor_db.status()['months'])
        self.assertFalse(result['approved_for_model_training'])

    def test_factor_audit_rejects_null_close_but_allows_missing_warmup(self):
        self._write_month("2026-01")
        factor_db.build("2026-01", memory="256MB", threads=1)
        path = factor_db._path("2026-01", "hf", "rth", "factors")
        self.assertTrue(factor_db.audit("2026-01", memory="256MB", threads=1)["valid"])
        temp = path.with_suffix(".new.parquet")
        cx = duckdb.connect()
        try:
            cx.execute(f"""COPY (SELECT * REPLACE (NULL::DOUBLE AS close)
                FROM {factor_db._files_sql([path])}) TO {factor_db._quoted(temp)} (FORMAT PARQUET)""")
        finally:
            cx.close()
        os.replace(temp, path)
        report = factor_db.audit("2026-01", memory="256MB", threads=1)
        self.assertFalse(report["valid"])
        self.assertEqual(report["invalid_rows"], report["rows"])

    def test_duplicate_hf_rejected_without_output(self):
        self._write_month('2026-02', duplicate=True)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            factor_db.build('2026-02', memory='256MB', threads=1)
        self.assertFalse(factor_db._path('2026-02', 'hf', 'rth', 'factors').exists())

    def test_sqlite_adjusted_close_is_source_isolated(self):
        cx = market_db.connect(market_db.DB_PATH)
        try:
            rows = [('AAPL', day.isoformat(), 100.0 + n, 'synthetic')
                    for n, day in enumerate(self._month_trading_days('2026-02'))]
            cx.executemany('INSERT INTO equity_daily VALUES (?,?,?,?)', rows)
            cx.commit()
        finally:
            cx.close()
        report = factor_db.build('2026-02', source='sqlite', memory='256MB', threads=1)
        self.assertEqual('built', report['state'])
        aapl = factor_db.query('AAPL', source='sqlite', limit=1,
                                memory='256MB', threads=1)['rows'][0]
        self.assertIsNone(aapl['volume_ratio_20d'])
        self.assertIsNone(aapl['intraday_return'])
        self.assertIsNotNone(aapl['mom_5d'])
        self.assertEqual(0, factor_db.status(source='hf')['months'])
        self.assertEqual(1, factor_db.status(source='sqlite')['months'])
        plan = factor_db.batch('2026-01', '2026-03', source='sqlite', dry_run=True)
        self.assertEqual(['2026-02'], plan['months_with_source'])
        batch = factor_db.batch('2026-01', '2026-03', source='sqlite',
                                memory='256MB', threads=1)
        self.assertEqual(1, batch['skipped_existing'])
        self.assertEqual(1, batch['months_processed'])
        # Actual month-price mutation invalidates the cached daily projection.
        cx = market_db.connect(market_db.DB_PATH)
        try:
            cx.execute("UPDATE equity_daily SET adjusted_close=321.0 "
                       "WHERE symbol='AAPL' AND trade_date='2026-02-27'")
            cx.commit()
        finally:
            cx.close()
        updated = factor_db.build('2026-02', source='sqlite',
                                  memory='256MB', threads=1)
        self.assertEqual('built', updated['state'])




if __name__ == '__main__':
    unittest.main()
