"""Regression checks for retained module-object compatibility shims (no market data)."""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "atlaspm"
SHIM_PATTERN = re.compile(r'_modules\[__name__\] = _import_module\("(atlaspm\.[^"]+)"\)')


def _shims() -> dict[str, str]:
    result = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found = SHIM_PATTERN.search(text)
        if found:
            name = "atlaspm." + ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
            result[name] = found.group(1)
    return result


class DeprecatedShimTests(unittest.TestCase):
    def test_all_shims_target_final_modules_and_warn_with_correct_stacklevel(self):
        targets = _shims()
        self.assertEqual(len(targets), 98)
        for old, final in targets.items():
            with self.subTest(shim=old):
                self.assertNotIn(final, targets)
                filename = PACKAGE / Path(*old.split(".")[1:]).with_suffix(".py")
                text = filename.read_text(encoding="utf-8")
                self.assertIn("from warnings import warn as _warn", text)
                self.assertIn("DeprecationWarning, stacklevel=2", text)
                self.assertIn(old + " is deprecated; use " + final, text)
                ast.parse(text, filename=str(filename))

    def test_deprecated_imports_warn_and_preserve_module_identity(self):
        # Fresh process: imports in unrelated tests must not suppress warnings.
        targets = _shims()
        code = (
            "import importlib, warnings\n"
            "targets = " + repr(targets) + "\n"
            "for old, new in targets.items():\n"
            "    with warnings.catch_warnings(record=True) as seen:\n"
            "        warnings.simplefilter('always', DeprecationWarning)\n"
            "        actual = importlib.import_module(old)\n"
            "    assert actual is importlib.import_module(new), old\n"
            "    assert any(item.category is DeprecationWarning "
            "and old in str(item.message) and new in str(item.message) "
            "for item in seen), old\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)

    def test_regular_test_imports_no_longer_use_shims(self):
        targets = _shims()
        problems = []
        for path in sorted((ROOT / "tests").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
                if isinstance(node, ast.ImportFrom) and node.level == 0:
                    if node.module in targets:
                        problems.append((str(path), node.lineno, node.module))
                    elif node.module == "atlaspm":
                        problems.extend(
                            (str(path), node.lineno, "atlaspm." + name.name)
                            for name in node.names if "atlaspm." + name.name in targets
                        )
                elif isinstance(node, ast.Import):
                    problems.extend(
                        (str(path), node.lineno, entry.name)
                        for entry in node.names if entry.name in targets
                    )
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
