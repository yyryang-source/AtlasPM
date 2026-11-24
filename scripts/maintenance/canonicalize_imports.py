"""Stage 2: preview rewrites of internal imports through AtlasPM compatibility shims.

Default is strictly read-only. Source files are changed ONLY with --apply and
--expect-plan-hash matching a previously reviewed, unchanged dry-run plan.
Never touches compatibility shim files, tests, local_data, or brokerage code paths
beyond ordinary import statements. Ambiguous import forms require manual review.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from audit_source import alias_target, module_name

SCHEMA = "atlaspm_internal_canonical_import_preview_v1"


def shim_map(src: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    modules: set[str] = set()
    aliases: dict[str, str] = {}
    for file in sorted((src / "atlaspm").rglob("*.py")):
        name = module_name(file, src)
        modules.add(name)
        target = alias_target(ast.parse(file.read_text(encoding="utf-8"), filename=str(file)))
        if target:
            aliases[name] = target
    final: dict[str, str] = {}
    chains: list[dict[str, Any]] = []
    for old, target in sorted(aliases.items()):
        seen = {old}
        hops = 1
        while target in aliases:
            if target in seen:
                raise ValueError(f"COMPATIBILITY_ALIAS_CYCLE: {old}")
            seen.add(target)
            target = aliases[target]
            hops += 1
        if target not in modules or target in aliases:
            raise ValueError(f"COMPATIBILITY_ALIAS_TARGET_MISSING: {old} -> {target}")
        if not target.startswith("atlaspm."):
            raise ValueError(f"COMPATIBILITY_ALIAS_OUTSIDE_ATLASPM: {old}")
        final[old] = target
        if hops > 1:
            chains.append({"alias": old, "final_module": target, "hops": hops})
    return final, chains


def _span(text: str, node: ast.AST) -> tuple[int, int]:
    # Python AST columns use UTF-8 byte offsets; never index str by bytes.
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    def at(line_no: int, column_bytes: int) -> int:
        line = lines[line_no - 1]
        prefix = line.encode("utf-8")[:column_bytes].decode("utf-8")
        return offsets[line_no - 1] + len(prefix)

    return at(node.lineno, node.col_offset), at(node.end_lineno, node.end_col_offset)


def _plan_file(path: Path, src: Path, final: dict[str, str]) -> tuple[list[dict], list[dict]]:
    name = module_name(path, src)
    if name in final:
        return [], []  # Never edit a compatibility shim in stage 2.
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    edits: list[dict] = []
    manual: list[dict] = []
    source_lines = text.splitlines(keepends=True)

    def item(node: ast.AST, reason: str, original: str, replacement: str | None = None) -> dict:
        return {
            "file": path.relative_to(src.parent).as_posix(),
            "line": node.lineno,
            "kind": type(node).__name__,
            "reason": reason,
            "old": original,
            "new": replacement,
        }

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        a, b = _span(text, node)
        old = text[a:b]
        new: str | None = None
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module in final:
                # Keep imported objects and aliases unchanged; modify module path only.
                old_module = node.module
                pattern = re.compile(r"^(from\s+)" + re.escape(old_module) + r"(\s+import\b)")
                new, count = pattern.subn(lambda m: m.group(1) + final[old_module] + m.group(2),
                                          old, count=1)
                if count != 1:
                    manual.append(item(node, "UNHANDLED_FROM_IMPORT_FORMAT", old))
                    continue
            elif node.module == "atlaspm":
                mapped = [a for a in node.names if f"atlaspm.{a.name}" in final]
                if not mapped:
                    continue
                # Short forms may bind a name from atlaspm.__init__ instead of a
                # module on some projects; AtlasPM __init__ only defines __version__.
                if ("#" in old or "\n" in old or "\r" in old or
                        not source_lines[node.lineno - 1][:node.col_offset].isspace()
                        and node.col_offset != 0 or
                        any(a.name == "*" for a in node.names)):
                    manual.append(item(node, "COMPOUND_OR_COMMENTED_PACKAGE_IMPORT", old))
                    continue
                indent = source_lines[node.lineno - 1][:node.col_offset]
                statements = []
                for entry in node.names:
                    alias = f"atlaspm.{entry.name}"
                    if alias in final:
                        parent, leaf = final[alias].rsplit(".", 1)
                        binding = entry.asname or entry.name
                        renamed = f" as {binding}" if binding != leaf else ""
                        statements.append(f"from {parent} import {leaf}{renamed}")
                    else:
                        renamed = f" as {entry.asname}" if entry.asname else ""
                        statements.append(f"from atlaspm import {entry.name}{renamed}")
                new = ("\n" + indent).join(statements)
        elif isinstance(node, ast.Import):
            changed = old
            for entry in node.names:
                if entry.name not in final:
                    continue
                if not entry.asname:
                    manual.append(item(node, "UNALIASED_DOTTED_IMPORT_BINDING_REVIEW",
                                       f"import {entry.name}"))
                    continue
                # import atlaspm.old as x binds x to module object; the canonical
                # import form retains x and points to the identical final module.
                pattern = re.compile(r"(?<![\w.])" + re.escape(entry.name) +
                                     r"(?=\s+as\s+" + re.escape(entry.asname) + r"\b)")
                changed, count = pattern.subn(final[entry.name], changed, count=1)
                if count != 1:
                    manual.append(item(node, "UNHANDLED_ALIASED_DOTTED_IMPORT_FORMAT", old))
            if changed != old:
                new = changed
        if new is not None and new != old:
            edits.append({**item(node, "AUTOMATIC_MODULE_PATH_ONLY", old, new),
                          "start": a, "end": b})

    # Explicit importlib calls can change the value of __module__, the source
    # of audit reports or a test patch target, so never rewrite their strings.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and node.args and
                isinstance(node.args[0], ast.Constant) and
                isinstance(node.args[0].value, str) and node.args[0].value in final):
            manual.append(item(node, "DYNAMIC_IMPORT_REQUIRES_REVIEW", node.args[0].value))

    edits.sort(key=lambda e: (e["start"], e["end"]))
    for first, second in zip(edits, edits[1:]):
        if first["end"] > second["start"]:
            raise ValueError(f"OVERLAPPING_IMPORT_PATCHES: {path}")
    candidate = text
    for entry in reversed(edits):
        candidate = (candidate[:entry["start"]] + entry["new"] +
                     candidate[entry["end"]:])
    try:
        ast.parse(candidate, filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"IMPORT_REWRITE_NOT_PARSEABLE: {path}: {exc}") from exc
    return edits, manual


def build_plan(root: Path) -> dict:
    src = root / "src"
    if not (src / "atlaspm").is_dir():
        raise ValueError(f"INVALID_ATLASPM_PROJECT_ROOT: {root}")
    aliases, chains = shim_map(src)
    edits: list[dict] = []
    manual: list[dict] = []
    scanned = 0
    for path in sorted((src / "atlaspm").rglob("*.py")):
        if module_name(path, src) in aliases:
            continue
        scanned += 1
        e, m = _plan_file(path, src, aliases)
        edits.extend(e)
        manual.extend(m)
    # Offset values only belong to the in-memory apply step, not the review
    # contract (which is stable across machines with different path roots).
    public_edits = [{k: v for k, v in e.items() if k not in ("start", "end")}
                    for e in edits]
    public_manual = manual
    review = {"schema": SCHEMA, "final_alias_targets": aliases,
              "automatic_replacements": public_edits, "manual_review": public_manual}
    plan_hash = hashlib.sha256(json.dumps(review, sort_keys=True, ensure_ascii=False,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema": SCHEMA,
        "root": str(root),
        "source_files_scanned": scanned,
        "compatibility_aliases": len(aliases),
        "multi_hop_aliases": chains,
        "automatic_replacements": public_edits,
        "manual_review": public_manual,
        "affected_source_files": sorted({e["file"] for e in edits}),
        "plan_sha256": plan_hash,
        "training_or_execution_permissions_changed": False,
    }


def apply_plan(root: Path, report: dict, expected_hash: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash or ""):
        raise ValueError("APPLY_REQUIRES_EXPLICIT_REVIEWED_PLAN_SHA256")
    if expected_hash != report["plan_sha256"]:
        raise ValueError("DRY_RUN_PLAN_MISMATCH")
    if report["manual_review"]:
        raise ValueError("MANUAL_IMPORT_REVIEW_REQUIRED_BEFORE_BULK_APPLY")
    check = subprocess.run(["git", "status", "--porcelain", "--", "src/atlaspm"],
                           cwd=root, text=True, capture_output=True, check=True)
    if check.stdout.strip():
        raise ValueError("REQUIRES_CLEAN_SOURCE_TREE")
    src = root / "src"
    aliases, _ = shim_map(src)
    planned = report["affected_source_files"]
    new_content: dict[Path, str] = {}
    for rel in planned:
        file = root / rel
        original = file.read_text(encoding="utf-8")
        edits, more_manual = _plan_file(file, src, aliases)
        if more_manual:
            raise ValueError("MANUAL_IMPORT_REVIEW_REQUIRED")
        revised = original
        for edit in reversed(edits):
            revised = revised[:edit["start"]] + edit["new"] + revised[edit["end"]:]
        ast.parse(revised, filename=str(file))
        new_content[file] = revised
    # All target files validated before the first write.
    for file, revised in new_content.items():
        file.write_text(revised, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    mode.add_argument("--apply", action="store_true", help="Only with previously approved SHA")
    parser.add_argument("--expect-plan-hash", default=None)
    parser.add_argument("--format", choices=["json", "text"], default="text")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_plan(root)
    if args.apply:
        apply_plan(root, report, args.expect_plan_hash)
    if args.format == "json":
        if args.summary_only:
            report = {k: (len(v) if k in ("automatic_replacements", "manual_review") else v)
                      for k, v in report.items()
                      if k not in ("multi_hop_aliases", "affected_source_files")}
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Shim aliases: {report['compatibility_aliases']} (multi-hop: "
              f"{len(report['multi_hop_aliases'])})")
        print(f"Source files scanned (excluding shims): {report['source_files_scanned']}")
        print(f"Safe import replacements: {len(report['automatic_replacements'])}, "
              f"files: {len(report['affected_source_files'])}")
        print(f"Manual review items: {len(report['manual_review'])}")
        print(f"Review plan SHA-256: {report['plan_sha256']}")
        if not args.summary_only:
            for edit in report["automatic_replacements"]:
                print(f"SAFE  {edit['file']}:{edit['line']} "
                      f"{edit['old']!r} -> {edit['new']!r}")
            for entry in report["manual_review"]:
                print(f"MANUAL {entry['file']}:{entry['line']} "
                      f"{entry['reason']}: {entry['old']!r}")
        if args.apply:
            print(f"Applied approved preview to {len(report['affected_source_files'])} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
