"""Disk-backed chronological prediction archive for synthetic research."""
from __future__ import annotations
from contextlib import closing
from datetime import date
from itertools import zip_longest
import sqlite3
from atlaspm.research.training_registry import local_path
from atlaspm.engine.contracts import finite


class PredictionArchive:
    def __init__(self,path):
        self.path=local_path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        # Reserve the filename exclusively before opening SQLite. Never attach
        # prediction tables to an unrelated existing database.
        try:
            with self.path.open('xb'):
                pass
        except FileExistsError as exc:
            raise sqlite3.OperationalError('PREDICTION_ARCHIVE_ALREADY_EXISTS') from exc
        # An incomplete run remains explicitly incomplete.
        with closing(sqlite3.connect(self.path,timeout=30)) as con,con:
            if con.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()!="wal":
                raise ValueError("PREDICTION_ARCHIVE_REQUIRES_WAL")
            con.execute("BEGIN IMMEDIATE")
            con.execute("CREATE TABLE prediction_state (status TEXT NOT NULL)")
            con.execute("INSERT INTO prediction_state VALUES ('INCOMPLETE')")
            con.execute("""CREATE TABLE forecasts (
                model TEXT NOT NULL, date TEXT NOT NULL, security_id TEXT NOT NULL,
                label_end_date TEXT NOT NULL, actual REAL, prediction REAL NOT NULL,
                PRIMARY KEY(model,date,security_id)) WITHOUT ROWID""")

    def append(self,model,rows):
        def values():
            for r in rows:
                yield (model,r["date"],r["security_id"],r["label_end_date"],
                       None if r["actual"] is None else finite(r["actual"],"actual"),
                       finite(r["prediction"],"prediction"))
        with closing(sqlite3.connect(self.path,timeout=30)) as con,con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT status FROM prediction_state").fetchone()!=("INCOMPLETE",):
                raise ValueError("PREDICTION_ARCHIVE_FINALIZED")
            con.executemany("INSERT INTO forecasts VALUES (?,?,?,?,?,?)",values())

    def rows(self,model,*,page_size=4096):
        """Read immutable model rows in keyset pages, without pinning a WAL snapshot.

        Call after all writes to the requested model are complete. Other models
        can be appended by the same research run while this iterator is open.
        """
        if type(page_size) is not int or not 1<=page_size<=65536:
            raise ValueError("INVALID_PREDICTION_PAGE_SIZE")
        with closing(sqlite3.connect(self.path,timeout=30)) as con:
            upper=con.execute(
                "SELECT date,security_id FROM forecasts WHERE model=? ORDER BY date DESC,security_id DESC LIMIT 1",
                (model,)).fetchone()
            if upper is None:
                return
            last=None
            while True:
                if last is None:
                    page=con.execute("""SELECT date,security_id,label_end_date,actual,prediction
                        FROM forecasts WHERE model=? AND (date,security_id)<=(?,?)
                        ORDER BY date,security_id LIMIT ?""",(model,*upper,page_size)).fetchall()
                else:
                    page=con.execute("""SELECT date,security_id,label_end_date,actual,prediction
                        FROM forecasts WHERE model=? AND (date,security_id)>(?,?)
                        AND (date,security_id)<=(?,?) ORDER BY date,security_id LIMIT ?""",
                        (model,*last,*upper,page_size)).fetchall()
                if not page:
                    break
                last=page[-1][:2]
                # fetchall ends the SQLite statement before yielding to callers.
                for day,sid,end,actual,prediction in page:
                    yield dict(date=day,security_id=sid,label_end_date=end,actual=actual,prediction=prediction)
                if last==upper:
                    break

    def finalize(self):
        with closing(sqlite3.connect(self.path,timeout=30)) as con,con:
            cur=con.execute("UPDATE prediction_state SET status='COMPLETE' WHERE status='INCOMPLETE'")
            if cur.rowcount!=1:
                raise ValueError("PREDICTION_ARCHIVE_FINALIZED")


def aligned_forecasts(streams, names, *, mismatch, order, label_mismatch, maturity):
    """Single validation contract for chronological aligned model streams."""
    previous=None
    for rows in zip_longest(*(iter(streams[name]) for name in names)):
        if any(row is None for row in rows):
            raise ValueError(mismatch)
        first=rows[0]
        key=(first["date"],first["security_id"])
        label=(first["actual"],first["label_end_date"])
        if previous is not None and key<=previous:
            raise ValueError(order)
        previous=key
        try:
            valid_dates=date.fromisoformat(label[1])>date.fromisoformat(key[0])
        except (TypeError,ValueError) as exc:
            raise ValueError(maturity) from exc
        if not valid_dates:
            raise ValueError(maturity)
        if label[0] is not None:
            finite(label[0],"actual")
        for row in rows:
            if (row["date"],row["security_id"])!=key:
                raise ValueError(mismatch)
            if (row["actual"],row["label_end_date"])!=label:
                raise ValueError(label_mismatch)
            finite(row["prediction"],"prediction")
        yield rows
