"""Focused integration tests for local data preparation orchestration."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from atlaspm.research import data_admission as admission


def _row(month,available=True,factors=False):
    return {"month":month,"within_advertised_archive":True,"raw_present":available,
            "bars_present":False,"factor_present":factors}


class DataPreparationTests(unittest.TestCase):
    def test_inventory_spans_do_not_bridge_missing_months(self):
        rows=[_row("2000-01"),_row("2000-02",False),_row("2000-03",True,True),
              _row("2000-04",True,True)]
        with patch.object(admission,"_archive_inventory",return_value=rows):
            report=admission.real_data_inventory()
        self.assertEqual(report["source_spans"],[
            {"start_month":"2000-01","end_month":"2000-01","months":1},
            {"start_month":"2000-03","end_month":"2000-04","months":2}])
        self.assertEqual(report["factor_month_count"],2)
        self.assertFalse(report["approved_for_model_training"])

    def test_missing_source_stops_before_any_writes(self):
        rows=[_row("2000-01",False)]
        with patch.object(admission,"_archive_inventory",return_value=rows), \
             patch("atlaspm.research.real_dataset_v6._calendar",return_value=[
                 (f"day-{i}",0,0) for i in range(180)]), \
             patch("atlaspm.data.unified_pipeline.maintain") as maintain:
            result=admission.prepare_real_research("2000-01","2000-07",execute=True)
        self.assertIn("MISSING_LOCAL_SOURCE_MONTHS",result["blockers"])
        self.assertFalse(result["maintenance_started"])
        maintain.assert_not_called()

    def test_actual_pipeline_uses_existing_builders_and_never_admits(self):
        before=Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                rows=[_row("2000-01")]
                counts={str(h):{"fold_count":2,"folds_meeting_sample_minimum":1,
                                "labeled_feature_complete_rows":200,
                                "folds_meeting_closing_path_sample_minimum":0} for h in (1,5,21)}
                checked={"status":"STRUCTURAL_PREFLIGHT_COMPLETE_TRAINING_BLOCKED",
                         "dataset_id":"a"*64,"lineage_id":"b"*64,
                         "label_paths_reconciled":True,"feature_source_reconciled":True,
                         "observed_rows":300,"feature_complete_rows":200,"calendar_sessions":180,
                         "sessions_without_observations":0,"rows_missing_session_closing_bar":2,
                         "horizons":counts,"blockers":["DATA_RIGHTS_UNVERIFIED"]}
                with patch.object(admission,"_archive_inventory",return_value=rows), \
                     patch("atlaspm.research.real_dataset_v6._calendar",return_value=[(f"day-{i}",0,0) for i in range(180)]), \
                     patch("atlaspm.research.real_dataset_v6._folds",return_value=[{},{}]), \
                     patch("atlaspm.data.unified_pipeline.maintain",return_value={
                         "months":[],"all_requested_months_processed":True}) as maintain, \
                     patch.object(admission,"build_research_dataset",return_value={
                         "status":"RESEARCH_DATASET_READY_TRAINING_BLOCKED","dataset_id":"a"*64,
                         "lineage_id":"b"*64,"blockers":[]}) as build, \
                     patch.object(admission,"inspect_research_dataset",return_value=checked) as inspect:
                    plan=admission.prepare_real_research("2000-01","2000-07")
                    self.assertFalse(plan["maintenance_started"])
                    maintain.assert_not_called()
                    result=admission.prepare_real_research("2000-01","2000-07",execute=True)
                    maintain.assert_called_once()
                    build.assert_called_once()
                    inspect.assert_called_once()
                    self.assertEqual(result["status"],"RESEARCH_DATASET_PREPARED_TRAINING_BLOCKED")
                    self.assertFalse(result["all_folds_meet_sample_minimum"])
                    self.assertIn("INSUFFICIENT_FOLD_SAMPLE_COUNTS:21",result["blockers"])
                    self.assertFalse(result["all_folds_meet_closing_path_minimum"])
                    self.assertIn("INSUFFICIENT_CLOSING_PATH_FOLD_SAMPLES:21",result["blockers"])
                    self.assertFalse(result["approved_for_model_training"])
                    self.assertFalse(result["model_registry_admission"])
                    self.assertFalse(Path("local_data/training/v6/.prepare.lock").exists())
            finally:
                os.chdir(before)
