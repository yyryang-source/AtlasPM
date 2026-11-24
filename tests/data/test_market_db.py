"""Synthetic offline tests: idempotency, rollback, option MAs and Treasury auctions."""
from __future__ import annotations
import csv
import os
from pathlib import Path
import tempfile
import unittest
from atlaspm.data import market_db

class MarketDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        os.chdir(self.temp.name)
        Path('local_data').mkdir()
        self.cx = market_db.connect(market_db.DB_PATH)
    def tearDown(self):
        self.cx.close()
        os.chdir(self.old)
        self.temp.cleanup()
    def write(self,name,columns,rows):
        p = Path('local_data') / name
        p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
        return p
    def test_equity_idempotent(self):
        path=self.write('prices.csv',['date','symbol','adjusted_close'],[
          {'date':'2026-11-20','symbol':'AAPL','adjusted_close':'234.5'},
          {'date':'2026-11-20','symbol':'MSFT','adjusted_close':'555.5'}])
        for _ in range(2):
            self.assertEqual(2,market_db.import_csv(self.cx,'stocks',path,'Yahoo',None)['rows_processed'])
        self.assertEqual(2,market_db.stats(self.cx)['counts']['equity_daily'])
    def test_bad_row_rolls_back(self):
        path=self.write('bad.csv',['date','symbol','adjusted_close'],[
          {'date':'2026-11-20','symbol':'AAPL','adjusted_close':'234.5'},
          {'date':'2026-11-20','symbol':'MSFT','adjusted_close':'NaN'}])
        with self.assertRaisesRegex(ValueError,'bad.csv:3'):
            market_db.import_csv(self.cx,'stocks',path,'Yahoo',None)
        self.assertEqual(0,market_db.stats(self.cx)['counts']['equity_daily'])
    def test_option_bars_and_ma(self):
        sym='O:SPY261218C00650000'
        ref=self.write('options/selected_contracts.csv',
          ['ticker','underlying_ticker','expiration_date','contract_type','strike_price'],[
          {'ticker':sym,'underlying_ticker':'SPY','expiration_date':'2026-12-18',
           'contract_type':'call','strike_price':'650'}])
        market_db.import_csv(self.cx,'contracts',ref,'Massive','2026-11-16')
        bar=self.write('options/5m/SPY.csv',
          ['symbol','bar','timestamp_ms','date_et','open','high','low','close','volume_contracts','sma5','ema5'],[
          {'symbol':sym,'bar':'5m','timestamp_ms':'1794843000000','date_et':'2026-11-16',
           'open':'3','high':'4','low':'2','close':'3.5','volume_contracts':'24',
           'sma5':'3.1','ema5':'3.2'}])
        for _ in range(2):
            market_db.import_csv(self.cx,'bars',bar,'Massive',None)
        self.assertEqual(1,market_db.stats(self.cx)['counts']['option_bars'])
        self.assertEqual((3.1,3.2),self.cx.execute('SELECT sma5,ema5 FROM option_bars').fetchone())
    def test_option_bars_require_contract(self):
        path=self.write('options/1d/no_contract.csv',
          ['symbol','bar','timestamp_ms','date_et','open','high','low','close','volume_contracts'],[
          {'symbol':'O:QQQ261218P00600000','bar':'1d','timestamp_ms':'1794873600000',
           'date_et':'2026-11-16','open':'4','high':'5','low':'2','close':'4','volume_contracts':'10'}])
        with self.assertRaisesRegex(ValueError,'import option contracts before bars'):
            market_db.import_csv(self.cx,'bars',path,'Massive',None)
        self.assertEqual(0,market_db.stats(self.cx)['counts']['option_bars'])
    def test_treasury(self):
        path=self.write('bonds/auctions.csv',
          ['cusip','security_type','security_term','interest_rate','auction_date',
           'issue_date','maturity_date','high_yield','offering_amt'],[
          {'cusip':'91282CZZ1','security_type':'Note','security_term':'10-Year',
           'interest_rate':'4.125','auction_date':'2026-11-15','issue_date':'2026-11-17',
           'maturity_date':'2036-11-17','high_yield':'4.3','offering_amt':'42000000000'},
          {'cusip':'','security_type':'Note'}])
        report=market_db.import_csv(self.cx,'treasury',path,'FiscalData',None)
        self.assertEqual((1,1),(report['rows_processed'],report['rows_skipped']))
        self.assertEqual(1,market_db.stats(self.cx)['counts']['treasury_auctions'])
    def test_private_path(self):
        with self.assertRaisesRegex(ValueError,'local_data'):
            market_db.private_path(Path('secrets.csv'))

if __name__=='__main__':
    unittest.main()
