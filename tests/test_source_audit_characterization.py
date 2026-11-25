"""Characterization of the source audit's report and exit-independent checks."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "maintenance" / "audit_source.py"
_SPEC = importlib.util.spec_from_file_location("atlaspm_source_audit", _SCRIPT)
audit_source = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit_source)


class SourceAuditTests(unittest.TestCase):
    def test_inventory_and_alias_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "src/atlaspm/__init__.py": "",
                "src/atlaspm/engine/core.py": "def f():\n    return 1\n",
                "src/atlaspm/old.py": '_modules[__name__] = _import_module("atlaspm.engine.core")\n',
                "src/atlaspm/older.py": '_modules[__name__] = _import_module("atlaspm.old")\n',
                "tests/test_example.py": "",
                "scripts/example.py": "",
            }
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            report = audit_source.audit(root)
            self.assertEqual(report, {
                "source_modules": 4,
                "test_files": 1,
                "domain_counts": {"compatibility_shims": 2, "engine": 1,
                                  "maintenance_scripts": 1, "package_root": 1},
                "alias_count": 2,
                "multi_hop_aliases": [{"alias": "atlaspm.older",
                                       "target": "atlaspm.engine.core", "depth": 2}],
                "duplicate_function_candidates": [],
                "errors": [],
                "note": "Duplicate bodies and compatibility paths require review; no automatic deletion.",
            })

    def test_duplicate_bodies_and_docstrings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "src" / "atlaspm"
            folder.mkdir(parents=True)
            body = "".join(f"    v{i} = {i} + {i}\n" for i in range(12))
            (folder / "a.py").write_text('def a():\n    """first"""\n' + body, encoding="utf-8")
            (folder / "b.py").write_text('async def b():\n    """second"""\n' + body, encoding="utf-8")
            report = audit_source.audit(root)
            self.assertEqual(report["duplicate_function_candidates"], [
                ["src/atlaspm/a.py:1:a", "src/atlaspm/b.py:1:b"]
            ])
            self.assertEqual(report["errors"], [])

    def test_invalid_source_and_missing_alias_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "src" / "atlaspm"
            folder.mkdir(parents=True)
            (folder / "broken.py").write_text("def invalid(\n", encoding="utf-8")
            (folder / "old.py").write_text(
                '_modules[__name__] = _import_module("atlaspm.missing")\n', encoding="utf-8")
            report = audit_source.audit(root)
            self.assertEqual(report["source_modules"], 1)
            self.assertTrue(report["errors"][0].startswith("src/atlaspm/broken.py:"))
            self.assertEqual(report["errors"][1], "missing alias target: atlaspm.old -> atlaspm.missing")


if __name__ == "__main__":
    unittest.main()
