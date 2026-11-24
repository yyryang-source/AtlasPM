"""Repository-wide source inventory; advisory duplication and hard structural checks."""
from __future__ import annotations

import argparse
import ast
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path


def module_name(path,src):
    parts=list(path.relative_to(src).with_suffix("").parts)
    if parts[-1]=="__init__":
        parts.pop()
    return ".".join(parts)


def alias_target(tree):
    for node in tree.body:
        if not isinstance(node,ast.Assign) or len(node.targets)!=1:
            continue
        target=node.targets[0]
        if (isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name)
                and target.value.id=="_modules" and isinstance(target.slice,ast.Name)
                and target.slice.id=="__name__" and isinstance(node.value,ast.Call)
                and isinstance(node.value.func,ast.Name) and node.value.func.id=="_import_module"
                and len(node.value.args)==1 and isinstance(node.value.args[0],ast.Constant)
                and isinstance(node.value.args[0].value,str)):
            return node.value.args[0].value
    return None


def audit(root):
    src=root/"src"
    modules={}
    errors=[]
    duplicates=defaultdict(list)
    counts=Counter()
    aliases={}
    tests=0
    for folder in ("src","tests","scripts"):
        for path in sorted((root/folder).rglob("*.py")):
            relative=path.relative_to(root).as_posix()
            try:
                tree=ast.parse(path.read_text(encoding="utf-8"),filename=relative)
            except (SyntaxError,UnicodeError) as exc:
                errors.append(f"{relative}: {exc}")
                continue
            if folder=="tests":
                tests+=1
                continue
            if folder!="src":
                counts["maintenance_scripts"]+=1
                continue
            name=module_name(path,src)
            if name in modules:
                errors.append(f"duplicate module: {name}")
            modules[name]=relative
            target=alias_target(tree)
            if target:
                aliases[name]=target
                counts["compatibility_shims"]+=1
                continue
            parts=name.split(".")
            counts[parts[1] if len(parts)>1 else "package_root"]+=1
            for node in ast.walk(tree):
                if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    continue
                body=node.body
                if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):
                    body=body[1:]
                # Only substantial identical bodies; matches are advisory, not dead code.
                if len(body)<5 or sum(sum(1 for _ in ast.walk(n)) for n in body)<60:
                    continue
                digest=hashlib.sha256("|".join(ast.dump(n,include_attributes=False) for n in body).encode()).hexdigest()
                duplicates[digest].append(f"{relative}:{node.lineno}:{node.name}")
    chains=[]
    for name,target in sorted(aliases.items()):
        seen={name}
        depth=1
        while target in aliases:
            if target in seen:
                errors.append(f"alias cycle: {name}")
                break
            seen.add(target)
            target=aliases[target]
            depth+=1
        if target not in modules:
            errors.append(f"missing alias target: {name} -> {target}")
        if depth>1:
            chains.append({"alias":name,"target":target,"depth":depth})
    return dict(source_modules=len(modules),test_files=tests,
                domain_counts=dict(sorted(counts.items())),
                alias_count=len(aliases),multi_hop_aliases=chains,
                duplicate_function_candidates=[v for v in duplicates.values() if len(v)>1],
                errors=errors,
                note="Duplicate bodies and compatibility paths require review; no automatic deletion.")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[2])
    args=parser.parse_args()
    report=audit(args.root.resolve())
    print(json.dumps(report,indent=2))
    return 1 if report["errors"] else 0


if __name__=="__main__":
    raise SystemExit(main())
