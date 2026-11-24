"""Disk-backed chronological prediction archive for synthetic research."""
from __future__ import annotations
from contextlib import closing
import sqlite3
from atlaspm.research.training_registry import local_path
from atlaspm.engine.contracts import finite


class PredictionArchive:
    def __init__(self,path):
        self.path=local_path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        # Refuse overwrite; an incomplete run remains explicitly incomplete.
        with closing(sqlite3.connect(self.path,timeout=30)) as con,con:
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
            if con.execute("SELECT status FROM prediction_state").fetchone()!=("INCOMPLETE",):
                raise ValueError("PREDICTION_ARCHIVE_FINALIZED")
            con.executemany("INSERT INTO forecasts VALUES (?,?,?,?,?,?)",values())

    def rows(self,model):
        with closing(sqlite3.connect(self.path,timeout=30)) as con:
            for day,sid,end,actual,prediction in con.execute(
                "SELECT date,security_id,label_end_date,actual,prediction FROM forecasts WHERE model=? ORDER BY date,security_id",(model,)):
                yield dict(date=day,security_id=sid,label_end_date=end,actual=actual,prediction=prediction)

    def finalize(self):
        with closing(sqlite3.connect(self.path,timeout=30)) as con,con:
            cur=con.execute("UPDATE prediction_state SET status='COMPLETE' WHERE status='INCOMPLETE'")
            if cur.rowcount!=1:
                raise ValueError("PREDICTION_ARCHIVE_FINALIZED")
