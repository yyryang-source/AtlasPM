import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch
from datetime import date

from research.test_training_autonomous import local_workspace
from atlaspm.research.training_admission import _coverage


@unittest.skipUnless(find_spec("duckdb"),"lake extra required")
class CoverageTests(unittest.TestCase):
    def test_monthly_calendar_and_anomalies_are_diagnostic(self):
        import duckdb
        with local_workspace():
            path=Path("local_data/factors.parquet")
            path.parent.mkdir(exist_ok=True)
            con=duckdb.connect(":memory:")
            try:
                con.execute("CREATE TABLE f(trade_date DATE,volume_shares DOUBLE,ret_1d DOUBLE)")
                con.execute("INSERT INTO f VALUES ('2020-01-02',10,.6),('2020-01-04',-1,0)")
                con.execute("COPY f TO 'local_data/factors.parquet' (FORMAT PARQUET)")
            finally:
                con.close()
            with patch("atlaspm.data.hf_stock5m_audit._calendar",
                       return_value=[(date(2020,1,2),0,1),(date(2020,1,3),0,1)]):
                result=_coverage(path,"2020-01",memory="512MB",threads=1)
            self.assertEqual(result["missing_sessions"],["2020-01-03"])
            self.assertEqual(result["unexpected_sessions"],["2020-01-04"])
            self.assertEqual(result["invalid_volume_rows"],1)
            self.assertEqual(result["extreme_return_rows"],1)
            self.assertFalse(result["security_level_coverage_verified"])
