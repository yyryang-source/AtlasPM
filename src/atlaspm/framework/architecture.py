"""Read-only inspection of current offline foundation contracts."""
from __future__ import annotations

import argparse
from pathlib import Path

from atlaspm.framework.workflow import STAGES, ASSET_SLEEVES, workflow_report
from atlaspm.framework.trading_lifecycle import PHASES, architecture_report
from atlaspm.research.training_registry import FLAGS

SCHEMA="atlaspm_offline_foundation_v1"


def inspect(parser=None):
    from atlaspm.cli import build_parser
    from atlaspm.commands import broker_cli,quant_cli,selection_cli,framework_cli
    from atlaspm import __file__ as package_file
    root=Path(package_file).resolve().parent
    parser=build_parser() if parser is None else parser
    sub=[a for a in parser._actions if isinstance(a,argparse._SubParsersAction)]
    groups={"quant":set(quant_cli.COMMANDS),"broker":set(broker_cli.COMMANDS),
            "selection":set(selection_cli.COMMANDS),"framework":set(framework_cli.COMMANDS),
            "other":{"market-db","portfolio-engine","autonomous-alpha"}}
    expected=set().union(*groups.values())
    names=set(sub[0].choices) if len(sub)==1 else set()
    errors=[]
    if len(sub)!=1 or names!=expected or sum(map(len,groups.values()))!=len(expected):
        errors.append("CLI_REGISTRY_MISMATCH")
    workflow=workflow_report()
    lifecycle=architecture_report()
    modules=sorted({m for item in (*STAGES,*ASSET_SLEEVES,*PHASES) for m in item.existing_modules})
    missing=[]
    for m in modules:
        parts=m.split(".")
        if parts[0]!="atlaspm" or len(parts)<2:
            missing.append(m)
            continue
        path=root.joinpath(*parts[1:])
        if not path.with_suffix(".py").is_file() and not (path/"__init__.py").is_file():
            missing.append(m)
    if missing:
        errors.append("ARCHITECTURE_MODULE_MISSING")
    for report in (workflow,lifecycle):
        if any(report.get(k) is not False for k in
               ("live_execution_enabled","automatic_broker_orders_enabled")):
            errors.append("ARCHITECTURE_PERMISSION_MISMATCH")
    return {"schema":SCHEMA,**FLAGS,"architecture_only":True,
            "status":"OFFLINE_FOUNDATION_CONTRACT_PASS" if not errors else "OFFLINE_FOUNDATION_CONTRACT_FAIL",
            "cli_commands":sorted(names),"cli_groups":{k:sorted(v) for k,v in groups.items()},
            "workflow_stages":len(workflow["stages"]),"lifecycle_phases":len(lifecycle["phases"]),
            "lifecycle_steps":len(lifecycle["transitions"]),"mapped_modules":modules,
            "missing_modules":missing,"errors":errors,
            "workflow":workflow,"lifecycle":lifecycle,
            "warning":"Static registry and permission contracts only; not operational, provider, or trading acceptance."}
