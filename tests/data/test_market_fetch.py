"""Offline mocked provider tests; no paid API calls in GitHub Actions."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from atlaspm.data import market_fetch, market_db

class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.prev=Path.cwd()
        os.chdir(self.tmp.name)
        Path("local_data").mkdir()
    def tearDown(self):
        os.chdir(self.prev)
        self.tmp.cleanup()
    def test_treasury_mapping_and_cache(self):
        payload={"data":[{"cusip":"91282CZZ1","security_type":"Note",
                  "comp_auction_close_date":"2026-11-19","int_rate":"4.125",
                  "issue_date":"2026-11-20","maturity_date":"2036-11-20",
                  "high_yield":"4.3","offering_amt":"4200000"}]}
        with patch.object(market_fetch,"get_json",return_value=payload) as get:
            first=market_fetch.treasury(max_pages=1)
            second=market_fetch.treasury(max_pages=1)
        self.assertEqual(1,get.call_count)
        self.assertEqual(1,first["fetched_pages"])
        self.assertEqual(1,second["cached_pages"])
        cx=market_db.connect(market_db.DB_PATH)
        path=Path("local_data/bonds/treasury/fiscaldata_v2/auctions_0001.csv")
        market_db.import_csv(cx,"treasury",path,"FiscalData",None)
        self.assertEqual(1,market_db.stats(cx)["counts"]["treasury_auctions"])
        cx.close()
    def test_contracts_and_5m_ma(self):
        contract={"ticker":"O:SPY261218C00650000","underlying_ticker":"SPY",
          "expiration_date":"2026-12-18","contract_type":"call","strike_price":650}
        origin=1794843000000
        def get(url,key=None):
            if "/reference/" in url:
                return {"results":[contract]}
            return {"results":[{"t":origin+i*300000,"o":2+i,"h":3+i,"l":1+i,
                    "c":2.5+i,"v":20,"n":2} for i in range(6)]}
        with patch.dict(os.environ,{"MASSIVE_API_KEY":"TEST_KEY"}), \
             patch.object(market_fetch,"get_json",side_effect=get):
            c=market_fetch.contracts(underlyings=["SPY"],as_of="2026-11-16",max_contracts=1)
            b=market_fetch.bars(contracts_file=Path(c["file"]),start="2026-11-16",
                                end="2026-11-16",timeframes=["5m"],max_contracts=1)
        self.assertEqual(1,b["csv_files_downloaded"])
        csvs=list(Path("local_data/options/massive/5m").glob("*.csv"))
        with csvs[0].open(newline="",encoding="utf8") as f:
            data=list(csv.DictReader(f))
        self.assertEqual("",data[0]["sma5"])
        self.assertNotEqual("",data[4]["sma5"])
        cx=market_db.connect(market_db.DB_PATH)
        market_db.import_csv(cx,"contracts",Path(c["file"]),"Massive",None)
        market_db.import_csv(cx,"bars",csvs[0],"Massive",None)
        self.assertEqual(6,market_db.stats(cx)["counts"]["option_bars"])
        cx.close()
    def test_key_required(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,"MASSIVE_API_KEY"):
                market_fetch.contracts(underlyings=["SPY"],as_of="2026-11-20")
    def test_private_paths(self):
        with self.assertRaisesRegex(ValueError,"local_data"):
            market_fetch.save_csv(Path("bad.csv"),("a",),[{"a":1}])

if __name__=="__main__":
    unittest.main()
