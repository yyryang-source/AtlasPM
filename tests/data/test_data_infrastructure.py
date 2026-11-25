"""Data infrastructure reads and strict provider-response regression tests."""
from contextlib import closing
from http.client import IncompleteRead
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from atlaspm.data import api_gateway as api, infrastructure as infra, market_db, unified


class InfrastructureTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        old=Path.cwd()
        os.chdir(tmp.name)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir,old)
        with closing(market_db.connect(market_db.DB_PATH)) as con:
            con.execute("INSERT INTO option_contracts VALUES (?,?,?,?,?,?,?)",
                        ("O:ABC200117C00010000","ABC","2020-01-17","call",10.0,None,"fixture"))
            con.execute("INSERT INTO bonds VALUES (?,?,?,?,?,?,?,?)",
                        ("123456789","treasury","US Treasury","Note",2.0,"2020-01-01","2025-01-01","fixture"))
            con.commit()

    def test_reference_lookup_read_only_and_missing(self):
        old=market_db.DB_PATH.read_bytes()
        out=unified.read_reference("option_contracts",identifiers=["O:ABC200117C00010000","missing"])
        self.assertEqual(out["records"][0]["strike"],10.)
        self.assertIsNone(out["records"][0]["reference_asof"])
        self.assertEqual(out["missing_identifiers"],["missing"])
        self.assertFalse(out["all_requested_identifiers_present"])
        bond=unified.read_reference("bond_terms",identifiers=["123456789"])
        self.assertEqual(bond["records"][0]["coupon_rate_pct"],2.)
        self.assertTrue(bond["all_requested_identifiers_present"])
        self.assertFalse(bond["historical_versions_verified"])
        self.assertFalse(bond["approved_for_model_training"])
        self.assertEqual(old,market_db.DB_PATH.read_bytes())

    def test_reference_guards(self):
        for kw in ({"purpose":"training"},{"as_of":"2020-01-01"}):
            with self.assertRaises(unified.AdmissionDenied):
                unified.read_reference("bond_terms",identifiers=["123456789"],**kw)
        for ids in ([],["a","a"],"abc",["a"]*129):
            with self.assertRaises(ValueError):
                unified.read_reference("bond_terms",identifiers=ids)
        self.assertEqual(unified.read_reference("bond_terms",identifiers=["' OR 1=1 --"])["records"],[])
        with self.assertRaises(unified.DataUnavailable):
            unified.read_reference("bond_terms",identifiers=["x"],database=Path("local_data/absent.sqlite3"))
        self.assertFalse(Path("local_data/absent.sqlite3").exists())

    def test_reference_invalid_terms_fail_closed(self):
        with closing(market_db.connect(market_db.DB_PATH)) as con:
            con.execute("UPDATE bonds SET maturity_date='2019-01-01'")
            con.execute("UPDATE option_contracts SET expiration='not-a-date'")
            con.commit()
        for dataset,identifier in (("bond_terms","123456789"),
                                  ("option_contracts","O:ABC200117C00010000")):
            with self.assertRaises(unified.DataQualityError):
                unified.read_reference(dataset,identifiers=[identifier])

    def test_inventory_schema_and_missing_no_network(self):
        g=api.Gateway(opener=lambda *a,**kw:self.fail("network"))
        with patch.dict(os.environ,{"FRED_API_KEY":"not-disclosed"}):
            result=infra.inspect(gateway=g)
        self.assertEqual(result["network_requests"],0)
        rows={r["dataset"]:r for r in result["datasets"]}
        self.assertEqual(rows["bond_terms"]["status"],"TABLE_SCHEMA_PRESENT")
        self.assertEqual(rows["option_contracts"]["status"],"TABLE_SCHEMA_PRESENT")
        self.assertEqual(rows["equity_5m"]["status"],"PARTITION_SCOPE_REQUIRES_EXPLICIT_READ")
        self.assertFalse(rows["equity_daily"]["coverage_verified"])
        self.assertNotIn("not-disclosed",json.dumps(result))
        missing=infra.inspect(database=Path("local_data/no.sqlite3"),gateway=g)
        self.assertEqual(missing["database_status"],"DATABASE_MISSING")
        self.assertFalse(Path("local_data/no.sqlite3").exists())

    def test_cli_integration_and_configuration_diagnostics(self):
        from atlaspm.cli import build_parser
        parser=build_parser()
        args=parser.parse_args(["market-db","reference-lookup","--dataset","bond_terms",
                                "--identifier","123456789"])
        self.assertTrue(market_db.run(args)["all_requested_identifiers_present"])
        with patch.dict(os.environ,{"XPRESSAPI_USERNAME":"user","XPRESSAPI_PASSWORD":"secret",
                                    "XPRESSAPI_TOKEN":"","ATLASPM_XPRESSAPI_MAX_REQUESTS":"",
                                    "ATLASPM_XPRESSAPI_RIGHTS":"fixture",
                                    "ATLASPM_ENABLE_XPRESSAPI":"1",
                                    "ATLASPM_ALLOW_EXTERNAL_AI":"0"}):
            out=market_db.run(parser.parse_args(["market-db","infrastructure-status"]))
        rows={r["provider"]:r for r in out["providers"]}
        x=rows["xpressapi"]["manual_attempt_preconditions"]
        self.assertIn("BEARER_TOKEN_REQUIRED_FOR_DATA_GET",x["blockers"])
        self.assertIn("REQUEST_BUDGET_INVALID_OR_MISSING",x["blockers"])
        self.assertFalse(x["request_authorized"])
        self.assertIn("EXPLICIT_EXTERNAL_AI_OPT_IN_REQUIRED",
                      rows["openai"]["manual_attempt_preconditions"]["blockers"])

    def test_duplicate_reference_identifier_is_rejected(self):
        p=Path("local_data/duplicate.sqlite3")
        with closing(sqlite3.connect(p)) as con:
            con.execute("CREATE TABLE bonds AS SELECT * FROM (SELECT '123456789' AS cusip, 'treasury' AS bond_kind, 'US Treasury' AS issuer, 'Note' AS security_type, 2.0 AS coupon_rate_pct, '2020-01-01' AS issue_date, '2025-01-01' AS maturity_date, 'fixture' AS source)")
            con.execute("INSERT INTO bonds SELECT * FROM bonds")
            con.commit()
        with self.assertRaisesRegex(unified.DataQualityError,"Ambiguous"):
            unified.read_reference("bond_terms",identifiers=["123456789"],database=p)

    def test_inventory_partial_schema(self):
        p=Path("local_data/partial.sqlite3")
        with closing(sqlite3.connect(p)) as con:
            con.execute("CREATE TABLE bonds(cusip TEXT)")
        rows={r["dataset"]:r for r in infra.inspect(database=p)["datasets"]}
        self.assertEqual(rows["bond_terms"]["status"],"TABLE_SCHEMA_MISMATCH")
        self.assertEqual(rows["option_contracts"]["status"],"TABLE_MISSING")


class StrictResponseTests(unittest.TestCase):
    def test_nested_duplicate_and_overflow_json(self):
        for body in (b'{"outer":{"x":1,"x":2}}',b'{"x":1e999}',b'{"x":-1e999}'):
            g=api.Gateway(opener=lambda *a,**kw:BytesIO(body))
            with self.assertRaises(api.GatewayError):
                g.fetch("treasury","https://api.fiscaldata.treasury.gov/test")
            self.assertEqual(g.health()["treasury"]["last_status"],"INVALID_RESPONSE")
        self.assertEqual(api._strict_json(b'{"x":1.25}'),{"x":1.25})

    def test_incomplete_transport_is_sanitized(self):
        def broken(*args,**kwargs):
            raise IncompleteRead(b"private-partial")
        g=api.Gateway(opener=broken,attempts=1)
        with self.assertRaisesRegex(api.GatewayError,"NETWORK_ERROR") as err:
            g.fetch("treasury","https://api.fiscaldata.treasury.gov/test")
        self.assertNotIn("private-partial",str(err.exception))
        self.assertEqual(g.health()["treasury"]["failed_attempts"],1)
