"""Resume completed folds and optional sequence-model epochs; research only."""
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


class EpochStore:
    """Transactional latest-epoch state scoped to an existing fold-run identity."""
    def __init__(self,fold_store,fold,model):
        self.store=fold_store
        self.fold=fold
        self.model=model
        with closing(self.store.connect()) as con,con:
            con.execute("""CREATE TABLE IF NOT EXISTS sequence_epochs (
                run_id TEXT NOT NULL, fold INTEGER NOT NULL, model TEXT NOT NULL,
                epoch INTEGER NOT NULL, state BLOB NOT NULL, sha256 TEXT NOT NULL,
                PRIMARY KEY(run_id,fold,model)) WITHOUT ROWID""")

    def load(self):
        import hashlib
        import io
        import torch
        with closing(self.store.connect()) as con:
            row=con.execute("""SELECT epoch,state,sha256 FROM sequence_epochs
                WHERE run_id=? AND fold=? AND model=?""",
                (self.store.run_id,self.fold,self.model)).fetchone()
        if row is None:
            return None
        epoch,blob,sha=row
        if hashlib.sha256(blob).hexdigest()!=sha:
            raise ValueError("EPOCH_CHECKPOINT_INTEGRITY_FAILURE")
        state=torch.load(io.BytesIO(blob),map_location="cpu",weights_only=True)
        if state.get("completed_epochs")!=epoch:
            raise ValueError("EPOCH_CHECKPOINT_MISMATCH")
        return state

    def save(self,state):
        import hashlib
        import io
        import torch
        epoch=state["completed_epochs"]
        if type(epoch) is not int or epoch<1:
            raise ValueError("INVALID_EPOCH_CHECKPOINT")
        buffer=io.BytesIO()
        torch.save(state,buffer)
        blob=buffer.getvalue()
        with closing(self.store.connect()) as con,con:
            con.execute("BEGIN IMMEDIATE")
            old=con.execute("""SELECT epoch FROM sequence_epochs
                WHERE run_id=? AND fold=? AND model=?""",
                (self.store.run_id,self.fold,self.model)).fetchone()
            if epoch!=(1 if old is None else old[0]+1):
                raise ValueError("EPOCH_CHECKPOINT_CONCURRENT_UPDATE")
            con.execute("""INSERT INTO sequence_epochs VALUES (?,?,?,?,?,?)
                ON CONFLICT(run_id,fold,model) DO UPDATE SET epoch=excluded.epoch,
                state=excluded.state,sha256=excluded.sha256""",
                (self.store.run_id,self.fold,self.model,epoch,blob,hashlib.sha256(blob).hexdigest()))
