"""SQLite online snapshot, sealed copy and recovery boundaries."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import sqlite3
import unittest

from atlaspm.data import market_db,sqlite_recovery as recovery


class SQLiteRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        prior=Path.cwd()
        os.chdir(temp.name)
        self.addCleanup(os.chdir,prior)
        self.con=market_db.connect(market_db.DB_PATH)
        self.addCleanup(self.con.close)
        self.con.execute("PRAGMA wal_autocheckpoint=0")
        self.con.execute("INSERT INTO equity_daily VALUES (?,?,?,?)",
                         ("AAA","2020-01-02",100.,"fixture"))
        self.con.commit()
        self.opts={"retention_reviewed":True,"max_bytes":16*1024**2,"timeout_seconds":30}

    def test_committed_wal_snapshot_and_isolated_restore(self):
        self.assertTrue(Path(str(market_db.DB_PATH)+"-wal").exists())
        a=recovery.backup("first",**self.opts)
        self.con.execute("UPDATE equity_daily SET adjusted_close=200")
        self.con.commit()
        b=recovery.restore_isolated("first","rehearsal",**self.opts)
        self.assertEqual(a["sha256"],b["sha256"])
        self.assertEqual(recovery.verify("rehearsal",**self.opts)["sha256"],a["sha256"])
        with closing(sqlite3.connect(Path(b["folder"])/"market.sqlite3")) as con:
            self.assertEqual(con.execute("SELECT adjusted_close FROM equity_daily").fetchone()[0],100)
        self.assertEqual(self.con.execute("SELECT adjusted_close FROM equity_daily").fetchone()[0],200)
        self.assertFalse(b["active_database_replaced"])

    def test_explicit_rights_and_budgets_before_copy(self):
        with self.assertRaisesRegex(ValueError,"RETENTION"):
            recovery.backup("denied")
        with self.assertRaisesRegex(ValueError,"BUDGET"):
            recovery.backup("oversize",**{**self.opts,"max_bytes":1})
        self.assertFalse(recovery.ROOT.exists())

    def test_tampering_and_no_overwrite(self):
        a=recovery.backup("first",**self.opts)
        with self.assertRaises(FileExistsError):
            recovery.backup("first",**self.opts)
        path=Path(a["folder"])/"market.sqlite3"
        with path.open("ab") as handle:
            handle.write(b"x")
        with self.assertRaisesRegex(ValueError,"HASH_MISMATCH"):
            recovery.restore_isolated("first","bad",**self.opts)
        self.assertFalse((recovery.ROOT/"bad").exists())

    def test_unsealed_sidecar_rejected(self):
        a=recovery.backup("first",**self.opts)
        (Path(a["folder"])/"market.sqlite3-wal").touch()
        with self.assertRaisesRegex(ValueError,"SIDECAR"):
            recovery.verify("first",**self.opts)

    def test_invalid_names_and_insufficient_space(self):
        for name in ("../x","CON","a/b"):
            with self.assertRaisesRegex(ValueError,"RECOVERY_ID"):
                recovery.backup(name,**self.opts)
        with patch.object(recovery.shutil,"disk_usage",return_value=type("Disk",(),{"free":0})()):
            with self.assertRaisesRegex(ValueError,"DISK_SPACE"):
                recovery.backup("space",**self.opts)
        self.assertFalse((recovery.ROOT/"space").exists())

    def test_incomplete_backup_never_counts_as_verified(self):
        with patch.object(recovery,"_check",side_effect=ValueError("INTEGRITY_CHECK")):
            with self.assertRaisesRegex(ValueError,"INTEGRITY_CHECK"):
                recovery.backup("partial",**self.opts)
        self.assertFalse((recovery.ROOT/"partial"/"manifest.json").exists())
        with self.assertRaises(FileNotFoundError):
            recovery.verify("partial",**self.opts)


    def test_registry_backup_restore_checks_record_contracts(self):
        from atlaspm.research.training_registry import register,FLAGS,load
        from atlaspm.research.training_artifacts import ModelArtifacts,SCHEMA
        p=Path("local_data/research.sqlite3")
        run_id=register(p,{"fixture":True},{**FLAGS,"dataset_sha256":"a"*64})
        store=ModelArtifacts(p)
        artifact=store.save({**FLAGS,"schema":SCHEMA,"model":"historical_mean","weights":[]})
        a=recovery.backup("registry",registry_path=p,**self.opts)
        b=recovery.restore_isolated("registry","registry-copy",**self.opts)
        copy=Path(b["folder"])/"market.sqlite3"
        self.assertEqual(a["scope"],"research_registry")
        self.assertEqual(load(copy,run_id)["run_id"],run_id)
        self.assertEqual(ModelArtifacts(copy).load(artifact)["model"],"historical_mean")
        self.assertFalse(b["live_execution_enabled"])

    def test_registry_corruption_unknown_tables_block_sealing(self):
        from atlaspm.research.training_registry import register,FLAGS
        p=Path("local_data/research.sqlite3")
        register(p,{}, {**FLAGS,"dataset_sha256":"a"*64})
        with closing(sqlite3.connect(p)) as con,con:
            con.execute("UPDATE research_runs SET report_json='{}'")
        with self.assertRaises(ValueError):
            recovery.backup("bad-registry",registry_path=p,**self.opts)
        self.assertFalse((recovery.ROOT/"bad-registry"/"manifest.json").exists())
        with closing(sqlite3.connect(p)) as con,con:
            con.execute("CREATE TABLE account_secret(value TEXT)")
        with self.assertRaisesRegex(ValueError,"REGISTRY_SCHEMA"):
            recovery.backup("unknown",registry_path=p,**self.opts)
