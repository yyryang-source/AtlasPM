"""Local research-only market database for equities, options and Treasuries.

No network, API keys, orders, live permission, or inferred prices. Data stays
in gitignored local_data/. SQLite is not an OPRA-wide minute-history data lake.
"""
from __future__ import annotations
import csv
from datetime import date, datetime, timezone
import glob
import math
from pathlib import Path
import re
import sqlite3

DB_PATH = Path('local_data/atlaspm_market.sqlite3')
OPTION_RE = re.compile(r'^O:([A-Z]{1,6})(\d{6})([CP])(\d{8})$')
SYMBOL_RE = re.compile(r'^[A-Z][A-Z0-9.^-]{0,24}$')
DDL = """
CREATE TABLE IF NOT EXISTS equity_daily (
 symbol TEXT NOT NULL, trade_date TEXT NOT NULL, adjusted_close REAL NOT NULL CHECK(adjusted_close > 0),
 source TEXT NOT NULL, PRIMARY KEY(symbol,trade_date)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_equity_date ON equity_daily(trade_date);
CREATE TABLE IF NOT EXISTS option_contracts (
 symbol TEXT PRIMARY KEY, underlying TEXT NOT NULL, expiration TEXT NOT NULL,
 option_type TEXT NOT NULL CHECK(option_type IN ('call','put')),
 strike REAL NOT NULL CHECK(strike > 0), reference_asof TEXT, source TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_contract_under_exp ON option_contracts(underlying,expiration);
CREATE TABLE IF NOT EXISTS option_bars (
 symbol TEXT NOT NULL REFERENCES option_contracts(symbol), timeframe TEXT NOT NULL CHECK(timeframe IN ('1d','5m','1m')),
 timestamp_ms INTEGER NOT NULL, date_et TEXT NOT NULL,
 open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
 volume REAL NOT NULL CHECK(volume >= 0), vwap REAL, trade_count INTEGER,
 sma5 REAL, sma10 REAL, sma20 REAL, ema5 REAL, ema10 REAL, ema20 REAL, volume_ma20 REAL,
 consecutive_bars INTEGER, source TEXT NOT NULL,
 PRIMARY KEY(symbol,timeframe,timestamp_ms)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_option_bars_date ON option_bars(date_et,timeframe);
CREATE TABLE IF NOT EXISTS bonds (
 cusip TEXT PRIMARY KEY, bond_kind TEXT NOT NULL, issuer TEXT NOT NULL,
 security_type TEXT, coupon_rate_pct REAL, issue_date TEXT, maturity_date TEXT, source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS treasury_auctions (
 cusip TEXT NOT NULL REFERENCES bonds(cusip), auction_date TEXT NOT NULL, security_type TEXT NOT NULL,
 security_term TEXT, coupon_rate_pct REAL, high_yield_pct REAL, offering_amount REAL, source TEXT NOT NULL,
 PRIMARY KEY(cusip,auction_date,security_type)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS option_quotes (
 symbol TEXT NOT NULL REFERENCES option_contracts(symbol), timestamp_ms INTEGER NOT NULL,
 bid REAL, ask REAL, bid_size REAL, ask_size REAL, source TEXT NOT NULL,
 PRIMARY KEY(symbol,timestamp_ms), CHECK(bid IS NULL OR bid >= 0),
 CHECK(ask IS NULL OR ask >= 0)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS option_greeks (
 symbol TEXT NOT NULL REFERENCES option_contracts(symbol), observed_at_ms INTEGER NOT NULL,
 underlying_price REAL, iv REAL, delta REAL, gamma REAL, theta REAL, vega REAL, rho REAL,
 source TEXT NOT NULL, PRIMARY KEY(symbol,observed_at_ms)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS bond_quotes (
 cusip TEXT NOT NULL REFERENCES bonds(cusip), quote_date TEXT NOT NULL,
 bid_price REAL, ask_price REAL, yield_to_maturity_pct REAL, source TEXT NOT NULL,
 PRIMARY KEY(cusip,quote_date)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS import_log (
 id INTEGER PRIMARY KEY, dataset TEXT NOT NULL, input_file TEXT NOT NULL,
 rows_processed INTEGER NOT NULL, rows_skipped INTEGER NOT NULL,
 imported_utc TEXT NOT NULL, source TEXT NOT NULL);
"""
SQL = {
 'stocks': """INSERT INTO equity_daily VALUES (?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET
 adjusted_close=excluded.adjusted_close,source=excluded.source""",
 'contracts': """INSERT INTO option_contracts VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
 underlying=excluded.underlying,expiration=excluded.expiration,option_type=excluded.option_type,
 strike=excluded.strike,reference_asof=COALESCE(excluded.reference_asof,option_contracts.reference_asof),source=excluded.source""",
 'bars': """INSERT INTO option_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
 ON CONFLICT(symbol,timeframe,timestamp_ms) DO UPDATE SET
 date_et=excluded.date_et,open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
 volume=excluded.volume,vwap=excluded.vwap,trade_count=excluded.trade_count,
 sma5=excluded.sma5,sma10=excluded.sma10,sma20=excluded.sma20,
 ema5=excluded.ema5,ema10=excluded.ema10,ema20=excluded.ema20,
 volume_ma20=excluded.volume_ma20,consecutive_bars=excluded.consecutive_bars,source=excluded.source""",
 'bonds': """INSERT INTO bonds VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(cusip) DO UPDATE SET
 security_type=excluded.security_type,coupon_rate_pct=COALESCE(excluded.coupon_rate_pct,bonds.coupon_rate_pct),
 issue_date=COALESCE(excluded.issue_date,bonds.issue_date),maturity_date=COALESCE(excluded.maturity_date,bonds.maturity_date),source=excluded.source""",
 'auctions': """INSERT INTO treasury_auctions VALUES (?,?,?,?,?,?,?,?)
 ON CONFLICT(cusip,auction_date,security_type) DO UPDATE SET
 security_term=excluded.security_term,coupon_rate_pct=excluded.coupon_rate_pct,
 high_yield_pct=excluded.high_yield_pct,offering_amount=excluded.offering_amount,source=excluded.source""",
}

def private_path(path: Path, exists: bool = False) -> Path:
    dest = Path(path).expanduser().resolve()
    if not dest.is_relative_to((Path.cwd() / 'local_data').resolve()):
        raise ValueError('Database and CSV inputs must stay under gitignored local_data/')
    if exists and not dest.is_file():
        raise ValueError(f'Input CSV not found: {dest}')
    return dest

def connect(path: Path):
    db = private_path(path)
    if db.suffix != '.sqlite3':
        raise ValueError('Database filename must end in .sqlite3')
    db.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(db, timeout=60)
    cx.execute('PRAGMA foreign_keys=ON')
    cx.execute('PRAGMA journal_mode=WAL')
    cx.execute('PRAGMA busy_timeout=60000')
    version = cx.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1):
        cx.close()
        raise ValueError(f'Unsupported warehouse schema version {version}')
    cx.executescript(DDL)
    cx.execute('PRAGMA user_version=1')
    cx.commit()
    return cx

def dt(value, name, optional=False):
    s = str(value or '').strip()
    if not s and optional:
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError as exc:
        raise ValueError(f'Invalid {name}: {s!r}') from exc

def num(value, name, optional=False, minimum=None, strict=False):
    s = str(value if value is not None else '').strip()
    if optional and s.lower() in ('', 'n/a', 'null', 'none'):
        return None
    try:
        n = float(s)
    except ValueError as exc:
        raise ValueError(f'Invalid {name}: {s!r}') from exc
    if not math.isfinite(n) or (minimum is not None and (n <= minimum if strict else n < minimum)):
        raise ValueError(f'Invalid {name}: {s!r}')
    return n

def integer(value, name, optional=False):
    s = str(value if value is not None else '').strip()
    if optional and not s:
        return None
    try:
        n = int(s)
    except ValueError as exc:
        raise ValueError(f'Invalid {name}: {s!r}') from exc
    if n < 0:
        raise ValueError(f'Invalid {name}: {s!r}')
    return n

def option_id(value):
    symbol = str(value or '').strip().upper()
    match = OPTION_RE.fullmatch(symbol)
    if not match:
        raise ValueError(f'Invalid option symbol: {symbol!r}')
    underlying, yymmdd, kind, strike = match.groups()
    expiry = datetime.strptime(yymmdd, '%y%m%d').date().isoformat()
    return symbol, underlying, expiry, ('call' if kind == 'C' else 'put'), int(strike) / 1000

def map_stock(row, source, asof):
    symbol = str(row.get('symbol') or '').strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise ValueError(f'Invalid stock symbol: {symbol!r}')
    return (symbol, dt(row.get('date'), 'date'),
            num(row.get('adjusted_close'), 'adjusted_close', minimum=0, strict=True), source)

def map_contract(row, source, asof):
    sym, under, expiry, kind, strike = option_id(row.get('ticker') or row.get('symbol'))
    u = str(row.get('underlying_ticker') or under).upper()
    e = dt(row.get('expiration_date') or expiry, 'expiration_date')
    k = str(row.get('contract_type') or kind).lower()
    s = num(row.get('strike_price') or strike, 'strike_price', minimum=0, strict=True)
    if (u, e, k) != (under, expiry, kind) or abs(s - strike) > 1e-6:
        raise ValueError(f'Conflicting option contract metadata: {sym}')
    return (sym, under, expiry, kind, strike, asof, source)

def map_bar(row, source, asof):
    symbol = option_id(row.get('symbol'))[0]
    bar = row.get('bar')
    if bar not in ('1m','5m','1d'):
        raise ValueError(f'Invalid bar timeframe: {bar!r}')
    ms = integer(row.get('timestamp_ms'), 'timestamp_ms')
    if ms < 946684800000:
        raise ValueError('Timestamp must be UTC Unix milliseconds since year 2000')
    d = dt(row.get('date_et'), 'date_et')
    o, h, l, c = [num(row.get(f), f, minimum=0, strict=True)
                  for f in ('open','high','low','close')]
    if h < max(o,c,l) or l > min(o,c) or l > h:
        raise ValueError(f'Invalid OHLC bounds for {symbol} at {ms}')
    volume = num(row.get('volume_contracts'), 'volume_contracts', minimum=0)
    vwap = num(row.get('vwap'), 'vwap', optional=True, minimum=0, strict=True)
    trades = integer(row.get('trade_count'), 'trade_count', optional=True)
    ma = [num(row.get(f), f, optional=True, minimum=0, strict=(f != 'volume_ma20'))
          for f in ('sma5','sma10','sma20','ema5','ema10','ema20','volume_ma20')]
    consecutive = integer(row.get('consecutive_bars'), 'consecutive_bars', optional=True)
    return (symbol,bar,ms,d,o,h,l,c,volume,vwap,trades,*ma,consecutive,source)

def map_treasury(row, source, asof):
    cusip = str(row.get('cusip') or '').strip().upper()
    if not cusip:
        return None
    if not re.fullmatch('[A-Z0-9]{9}',cusip):
        raise ValueError(f'Invalid Treasury CUSIP: {cusip!r}')
    typ = row.get('security_type') or 'Unknown'
    coupon = num(row.get('interest_rate'),'interest_rate',optional=True,minimum=0)
    issue = dt(row.get('issue_date'),'issue_date',optional=True)
    maturity = dt(row.get('maturity_date'),'maturity_date',optional=True)
    auction = dt(row.get('auction_date'),'auction_date',optional=True)
    bond = (cusip,'treasury','US Treasury',typ,coupon,issue,maturity,source)
    lot = (cusip,auction,typ,row.get('security_term') or None,coupon,
           num(row.get('high_yield'),'high_yield',optional=True),
           num(row.get('offering_amt'),'offering_amt',optional=True,minimum=0),source) if auction else None
    return bond, lot

def import_csv(cx, dataset, filename, source, asof):
    path = private_path(filename, exists=True)
    mapping = {'stocks':map_stock,'contracts':map_contract,'bars':map_bar,'treasury':map_treasury}
    processed = skipped = 0
    items, extra = [], []
    def flush():
        if items:
            cx.executemany(SQL['bonds' if dataset == 'treasury' else dataset], items)
            if extra:
                cx.executemany(SQL['auctions'], extra)
            items.clear()
            extra.clear()
    try:
        with path.open(newline='',encoding='utf-8-sig') as f, cx:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError('Missing CSV header')
            for lineno, row in enumerate(reader,start=2):
                try:
                    mapped = mapping[dataset](row,source,asof)
                except (ValueError,TypeError) as exc:
                    raise ValueError(f'{path.name}:{lineno}: {exc}') from exc
                if mapped is None:
                    skipped += 1
                    continue
                if dataset == 'treasury':
                    item, auction = mapped
                    items.append(item)
                    if auction:
                        extra.append(auction)
                else:
                    items.append(mapped)
                processed += 1
                if len(items) >= 2000:
                    flush()
            flush()
            cx.execute('INSERT INTO import_log(dataset,input_file,rows_processed,rows_skipped,imported_utc,source) VALUES (?,?,?,?,?,?)',
                       (dataset,str(path.relative_to(Path.cwd().resolve())),processed,skipped,
                        datetime.now(timezone.utc).isoformat(),source))
    except sqlite3.IntegrityError as exc:
        raise ValueError(f'{path.name}: referential integrity error; import option contracts before bars: {exc}') from exc
    return {'file':str(path.relative_to(Path.cwd().resolve())),
            'rows_processed':processed,'rows_skipped':skipped}

def stats(cx):
    tables = ('equity_daily','option_contracts','option_bars','option_quotes','option_greeks','bonds','treasury_auctions','bond_quotes','import_log')
    result = {table:cx.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in tables}
    result['equity_symbols'] = cx.execute('SELECT COUNT(DISTINCT symbol) FROM equity_daily').fetchone()[0]
    result['option_underlyings'] = cx.execute('SELECT COUNT(DISTINCT underlying) FROM option_contracts').fetchone()[0]
    result['option_bars_by_timeframe'] = dict(cx.execute('SELECT timeframe,COUNT(*) FROM option_bars GROUP BY timeframe'))
    result['equity_dates'] = cx.execute('SELECT MIN(trade_date),MAX(trade_date) FROM equity_daily').fetchone()
    result['option_dates'] = cx.execute('SELECT MIN(date_et),MAX(date_et) FROM option_bars').fetchone()
    return {'schema_version':1,'counts':result,'research_only':True,'live_execution_enabled':False,
            'point_in_time_validated':False}

def register_parser(sub):
    p = sub.add_parser('market-db',help='Local US equity/option/Treasury research warehouse')
    p.add_argument('--db',type=Path,default=DB_PATH)
    actions = p.add_subparsers(dest='db_action',required=True)
    actions.add_parser('init')
    actions.add_parser('status')
    t = actions.add_parser('fetch-treasury', help='Download official US Treasury auction CSV pages')
    t.add_argument('--max-pages', type=int, default=0, help='0=all pages, otherwise limit download')
    t.add_argument('--refresh', action='store_true')
    t = actions.add_parser('fetch-option-contracts', help='Download Massive options contract reference CSV')
    t.add_argument('--underlyings', nargs='+', required=True)
    t.add_argument('--as-of', required=True)
    t.add_argument('--expiry', default=None)
    t.add_argument('--max-contracts', type=int, default=20)
    t.add_argument('--rpm', type=int, default=5)
    t = actions.add_parser('fetch-option-bars', help='Download selected contract daily/5m/1m aggregates')
    t.add_argument('--contracts', type=Path, default=Path('local_data/options/massive/selected_contracts.csv'))
    t.add_argument('--start', required=True)
    t.add_argument('--end', required=True)
    t.add_argument('--timeframes', nargs='+', default=['1d','5m'])
    t.add_argument('--max-contracts', type=int, default=10)
    t.add_argument('--rpm', type=int, default=5)
    t.add_argument('--refresh', action='store_true')
    q=actions.add_parser('stock-5m-probe',help='Probe Massive Stocks historical 5-minute bars')
    q.add_argument('--symbol',default='AAPL')
    q.add_argument('--date',default='2026-11-20')
    q=actions.add_parser('stock-5m-backfill',help='Download full-market 1m files and aggregate to 5m Parquet')
    q.add_argument('--start',default='2000-01-01')
    q.add_argument('--end',default='2026-11-23')
    q.add_argument('--max-days',type=int,default=0)
    q.add_argument('--session',choices=['rth','all'],default='rth')
    q.add_argument('--profile',default='massive')
    q.add_argument('--dry-run',action='store_true')
    q.add_argument('--remove-raw',action='store_true')
    q.add_argument('--refresh',action='store_true')
    actions.add_parser('stock-5m-status')
    h=actions.add_parser('hf-5m-backfill',help='HF minute OHLCV to local 5m research lake')
    h.add_argument('--start-month',default='2026-03')
    h.add_argument('--end-month',default='2026-03')
    h.add_argument('--max-months',type=int,default=1)
    h.add_argument('--session',choices=['rth','all'],default='rth')
    h.add_argument('--dry-run',action='store_true')
    h.add_argument('--refresh',action='store_true')
    h.add_argument('--remove-raw',action='store_true')
    h.add_argument('--acknowledge-rights',action='store_true')
    h.add_argument('--input',type=Path,default=None)
    actions.add_parser('hf-5m-status')
    h=actions.add_parser('hf-5m-reconcile',help='Compare local 5m daily close with independent reference')
    h.add_argument('--month',required=True)
    h.add_argument('--symbols',nargs='+',default=None)
    h.add_argument('--reference-csv',type=Path,default=None)
    h.add_argument('--fetch-yahoo',action='store_true')
    h.add_argument('--close-tolerance-pct',type=float,default=1.0)
    h.add_argument('--volume-tolerance-pct',type=float,default=25.0)
    h.add_argument('--no-save',action='store_true')
    h=actions.add_parser('hf-5m-audit',help='Audit local HF 5m bars against NYSE calendar and major symbols')
    h.add_argument('--month',required=True)
    h.add_argument('--session',choices=['rth','all'],default='rth')
    h.add_argument('--no-save',action='store_true')
    h=actions.add_parser('hf-5m-audit-batch',help='Offline resumable full-history HF 5m quality audit')
    h.add_argument('--start-month',default='2000-01')
    h.add_argument('--end-month',default='2026-03')
    h.add_argument('--max-months',type=int,default=0)
    h.add_argument('--session',choices=['rth','all'],default='rth')
    h.add_argument('--dry-run',action='store_true')
    h.add_argument('--no-resume',action='store_true')
    h=actions.add_parser('hf-5m-session-v4',help='Offline calendar diagnostics and research projection plan')
    h.add_argument('--start-month',default='2000-01')
    h.add_argument('--end-month',default='2026-03')
    h.add_argument('--all-months',action='store_true')
    h.add_argument('--dry-run',action='store_true')
    h=actions.add_parser('hf-5m-certify-v5',help='Offline evidence review and historical identity coverage')
    h.add_argument('--start-month',default='2000-01')
    h.add_argument('--end-month',default='2026-03')
    h.add_argument('--identity-csv',type=Path,default=None)
    h.add_argument('--actions-csv',type=Path,default=None)
    h.add_argument('--scan',action='store_true')
    h=actions.add_parser('hf-5m-evidence-v51',help='Plan or explicitly collect public evidence candidates')
    h.add_argument('--start-quarter',default='2000-Q1')
    h.add_argument('--end-quarter',default='2026-Q1')
    h.add_argument('--listing-dates',nargs='+',default=None)
    h.add_argument('--no-current',action='store_true')
    h.add_argument('--fetch',action='store_true')
    h.add_argument('--refresh',action='store_true')
    h.add_argument('--max-requests',type=int,default=0)
    # Offline, source-isolated daily factor mart; no new top-level CLI family.
    f = actions.add_parser('factor-catalog', help='List documented equity factors')
    for name in ('factor-status', 'factor-build', 'factor-audit', 'factor-query', 'factor-batch'):
        f = actions.add_parser(name)
        f.add_argument('--source', choices=['hf', 'sqlite'], default='hf')
        f.add_argument('--session', choices=['rth'], default='rth')
        if name in ('factor-build', 'factor-audit'):
            f.add_argument('--month', required=True)
        if name in ('factor-build', 'factor-batch'):
            f.add_argument('--sqlite-db', type=Path, default=DB_PATH)
            f.add_argument('--refresh', action='store_true')
            f.add_argument('--dry-run', action='store_true')
        if name == 'factor-batch':
            f.add_argument('--start-month', required=True)
            f.add_argument('--end-month', required=True)
            f.add_argument('--max-months', type=int, default=0)
        if name == 'factor-query':
            f.add_argument('--symbol', required=True)
            f.add_argument('--start', default=None)
            f.add_argument('--end', default=None)
            f.add_argument('--limit', type=int, default=20)
        if name != 'factor-status':
            f.add_argument('--memory', default='4GB')
            f.add_argument('--threads', type=int, default=4)
    e = actions.add_parser('factor-evaluate',
        help='Exploratory Rank IC/ICIR/quintile diagnostics; never a training gate')
    e.add_argument('--start', required=True)
    e.add_argument('--end', required=True)
    e.add_argument('--source', choices=['hf', 'sqlite'], default='hf')
    e.add_argument('--session', choices=['rth'], default='rth')
    e.add_argument('--factors', nargs='+', default=None)
    e.add_argument('--horizons', type=int, nargs='+', default=[5])
    e.add_argument('--holdout-start', default=None)
    e.add_argument('--min-symbols', type=int, default=30)
    e.add_argument('--min-dates', type=int, default=8)
    e.add_argument('--min-bars', type=int, default=60)
    e.add_argument('--min-minute-fraction', type=float, default=0.85)
    e.add_argument('--cost-bps-per-leg-roundtrip', type=float, default=20.0)
    e.add_argument('--memory', default='4GB')
    e.add_argument('--threads', type=int, default=4)
    e.add_argument('--exploratory', action='store_true')
    e.add_argument('--no-save', action='store_true')
    v = actions.add_parser('factor-v3-analyze',
        help='V3 descriptive factor normalization, correlation, neutralization and composite IC')
    v.add_argument('--start', required=True)
    v.add_argument('--end', required=True)
    v.add_argument('--source', choices=['hf', 'sqlite'], default='hf')
    v.add_argument('--session', choices=['rth'], default='rth')
    v.add_argument('--factors', nargs='+', default=None)
    v.add_argument('--horizon', type=int, choices=[1, 5, 21, 63], default=1)
    v.add_argument('--holdout-start', default=None)
    v.add_argument('--weighting', choices=['equal', 'ic_train'], default='equal')
    v.add_argument('--neutralize-industry', action='store_true')
    v.add_argument('--industry-csv', type=Path, default=None)
    v.add_argument('--min-group-size', type=int, default=5)
    v.add_argument('--winsor-tail', type=float, default=0.01)
    v.add_argument('--redundancy-threshold', type=float, default=0.85)
    v.add_argument('--min-symbols', type=int, default=30)
    v.add_argument('--min-dates', type=int, default=8)
    v.add_argument('--min-bars', type=int, default=60)
    v.add_argument('--min-minute-fraction', type=float, default=0.85)
    v.add_argument('--memory', default='4GB')
    v.add_argument('--threads', type=int, default=4)
    v.add_argument('--exploratory', action='store_true')
    v.add_argument('--no-save', action='store_true')
    v.add_argument('--save-scores', action='store_true')
    for action in ('import-stocks','import-option-contracts','import-option-bars','import-treasury'):
        q = actions.add_parser(action)
        q.add_argument('--input',type=Path,action='append',default=[])
        q.add_argument('--glob',dest='glob_pattern',default=None)
        q.add_argument('--source',default=None)
        if action == 'import-option-contracts':
            q.add_argument('--as-of',default=None)

def run(args):
    if args.db_action == 'factor-v3-analyze':
        from atlaspm.data import factor_v3
        return factor_v3.analyze(
            start=args.start, end=args.end, source=args.source, session=args.session,
            factors=args.factors, horizon=args.horizon, holdout_start=args.holdout_start,
            weighting=args.weighting, neutralize_industry=args.neutralize_industry,
            industry_csv=args.industry_csv, min_group_size=args.min_group_size,
            winsor_tail=args.winsor_tail,
            redundancy_threshold=args.redundancy_threshold,
            min_symbols=args.min_symbols, min_dates=args.min_dates,
            min_bars=args.min_bars, min_minute_fraction=args.min_minute_fraction,
            memory=args.memory, threads=args.threads, exploratory=args.exploratory,
            save=not args.no_save, save_scores=args.save_scores)
    if args.db_action == 'factor-evaluate':
        from atlaspm.data import factor_eval
        return factor_eval.evaluate(
            start=args.start, end=args.end, source=args.source, session=args.session,
            factors=args.factors, horizons=args.horizons, holdout_start=args.holdout_start,
            min_symbols=args.min_symbols, min_dates=args.min_dates,
            min_bars=args.min_bars, min_minute_fraction=args.min_minute_fraction,
            cost_bps_per_leg_roundtrip=args.cost_bps_per_leg_roundtrip,
            memory=args.memory, threads=args.threads,
            exploratory=args.exploratory, save=not args.no_save)
    if args.db_action.startswith('factor-'):
        from atlaspm.data import factor_db
        action = args.db_action
        if action == 'factor-catalog':
            return factor_db.catalog()
        common = {'source': args.source, 'session': args.session}
        if action == 'factor-status':
            return factor_db.status(**common)
        common.update(memory=args.memory, threads=args.threads)
        if action == 'factor-audit':
            return factor_db.audit(args.month, **common)
        if action == 'factor-query':
            return factor_db.query(args.symbol, start=args.start, end=args.end,
                                   limit=args.limit, **common)
        common.update(sqlite_db=args.sqlite_db, dry_run=args.dry_run,
                      refresh=args.refresh)
        if action == 'factor-build':
            return factor_db.build(args.month, **common)
        return factor_db.batch(args.start_month, args.end_month,
                               max_months=args.max_months, **common)
    if args.db_action == 'hf-5m-reconcile':
        from atlaspm.data import hf_stock5m_reconcile
        return hf_stock5m_reconcile.reconcile(
            args.month,symbols=args.symbols,
            reference_csv=args.reference_csv,fetch_yahoo=args.fetch_yahoo,
            close_tolerance_pct=args.close_tolerance_pct,
            volume_tolerance_pct=args.volume_tolerance_pct,
            save=not args.no_save)
    if args.db_action == 'hf-5m-audit':
        from atlaspm.data import hf_stock5m_audit
        return hf_stock5m_audit.audit(args.month,session=args.session,save=not args.no_save)
    if args.db_action == 'hf-5m-audit-batch':
        from atlaspm.data import hf_stock5m_batch
        return hf_stock5m_batch.run(
            args.start_month, args.end_month, session=args.session,
            max_months=args.max_months, resume=not args.no_resume,
            dry_run=args.dry_run)
    if args.db_action == 'hf-5m-session-v4':
        from atlaspm.data import hf_stock5m_session_v4
        return hf_stock5m_session_v4.run(
            args.start_month,args.end_month,all_months=args.all_months,
            dry_run=args.dry_run)
    if args.db_action == 'hf-5m-certify-v5':
        from atlaspm.data import hf_stock5m_certification_v5
        return hf_stock5m_certification_v5.run(
            args.start_month,args.end_month,identity_csv=args.identity_csv,
            actions_csv=args.actions_csv,scan=args.scan)
    if args.db_action == 'hf-5m-evidence-v51':
        from atlaspm.data import hf_stock5m_evidence_v51
        return hf_stock5m_evidence_v51.run(
            args.start_quarter,args.end_quarter,listing_dates=args.listing_dates,
            current=not args.no_current,fetch=args.fetch,refresh=args.refresh,
            max_requests=args.max_requests)
    if args.db_action.startswith('hf-5m-'):
        from atlaspm.data import hf_stock5m
        if args.db_action == 'hf-5m-status':
            return hf_stock5m.status()
        return hf_stock5m.backfill(args.start_month,args.end_month,
            max_months=args.max_months,session=args.session,dry_run=args.dry_run,
            refresh=args.refresh,remove_raw=args.remove_raw,
            acknowledge_rights=args.acknowledge_rights,input_file=args.input)
    if args.db_action.startswith('stock-5m-'):
        from atlaspm.data import stock5m_flatfiles
        if args.db_action == 'stock-5m-probe':
            return stock5m_flatfiles.probe(args.symbol,args.date)
        if args.db_action == 'stock-5m-status':
            return stock5m_flatfiles.status()
        return stock5m_flatfiles.backfill(
            args.start,args.end,max_days=args.max_days,session=args.session,
            profile=args.profile,dry_run=args.dry_run,
            remove_raw=args.remove_raw,refresh=args.refresh)
    if args.db_action.startswith('fetch-'):
        from atlaspm.data import market_fetch
        if args.db_action == 'fetch-treasury':
            return market_fetch.treasury(max_pages=args.max_pages, refresh=args.refresh)
        if args.db_action == 'fetch-option-contracts':
            return market_fetch.contracts(underlyings=args.underlyings,as_of=args.as_of,
                                          expiry=args.expiry,max_contracts=args.max_contracts,rpm=args.rpm)
        return market_fetch.bars(contracts_file=args.contracts,start=args.start,end=args.end,
                                 timeframes=args.timeframes,max_contracts=args.max_contracts,
                                 rpm=args.rpm,refresh=args.refresh)
    cx = connect(args.db)
    try:
        if args.db_action == 'init':
            return {'schema_version':1,'database':str(private_path(args.db)),
                    'research_only':True,'live_execution_enabled':False}
        if args.db_action == 'status':
            return stats(cx)
        files = list(args.input)
        if args.glob_pattern:
            files.extend(Path(p) for p in sorted(glob.glob(args.glob_pattern)))
        files = list(dict.fromkeys(files))
        if not files:
            raise ValueError('Provide --input FILE or --glob "local_data/.../*.csv"')
        if args.db_action == 'import-option-contracts' and args.as_of:
            dt(args.as_of,'as-of')
        dataset = {'import-stocks':'stocks','import-option-contracts':'contracts',
                   'import-option-bars':'bars','import-treasury':'treasury'}[args.db_action]
        source = args.source or {'stocks':'yfinance adjusted close','contracts':'Massive reference',
                                 'bars':'Massive aggregate trades','treasury':'US Treasury FiscalData'}[dataset]
        if len(source)>120 or not source.strip():
            raise ValueError('Invalid source label')
        reports = [import_csv(cx,dataset,path,source,getattr(args,'as_of',None)) for path in files]
        return {'database':str(private_path(args.db)),'dataset':dataset,'files':reports,
                'rows_processed':sum(x['rows_processed'] for x in reports),
                'research_only':True,'live_execution_enabled':False}
    finally:
        cx.close()
