"""Resume completed training folds, not optimizer steps or live models."""
from __future__ import annotations
from contextlib import closing
import sqlite3
import json
from atlaspm.research.training_registry import canonical,digest,local_path


class FoldStore:
    def __init__(self,path,identity):
        self.path=local_path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.run_id=digest(identity)
        with closing(self.connect()) as con,con:
            con.execute("""CREATE TABLE IF NOT EXISTS fold_checkpoints (
                run_id TEXT NOT NULL, fold INTEGER NOT NULL, content TEXT NOT NULL,
                sha256 TEXT NOT NULL, PRIMARY KEY(run_id,fold))""")

    def connect(self):
        return sqlite3.connect(self.path,timeout=30)

    def load(self,index):
        with closing(self.connect()) as con:
            row=con.execute("SELECT content,sha256 FROM fold_checkpoints WHERE run_id=? AND fold=?",
                            (self.run_id,index)).fetchone()
        if row is None:
            return None
        data=json.loads(row[0])
        if digest(data)!=row[1]:
            raise ValueError("CHECKPOINT_INTEGRITY_FAILURE")
        return data

    def save(self,index,data):
        with closing(self.connect()) as con,con:
            con.execute("INSERT OR IGNORE INTO fold_checkpoints VALUES (?,?,?,?)",
                        (self.run_id,index,canonical(data),digest(data)))
        if digest(self.load(index))!=digest(data):
            raise ValueError("CHECKPOINT_CONFLICT")
