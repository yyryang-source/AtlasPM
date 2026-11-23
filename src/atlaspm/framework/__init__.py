"""Non-executable standard quantitative research workflow contracts.

The registry is safe to inspect without market data, model weights or brokers.
"""

from atlaspm.framework.workflow import (
    ASSET_SLEEVES,
    CROSS_CUTTING_CONTROLS,
    REQUIRED_GATES,
    STAGES,
    AssetSleeve,
    WorkflowStage,
    mermaid_flowchart,
    validate_workflow,
    workflow_report,
)

__all__ = [
    "ASSET_SLEEVES", "CROSS_CUTTING_CONTROLS", "REQUIRED_GATES",
    "STAGES", "AssetSleeve", "WorkflowStage",
    "mermaid_flowchart", "validate_workflow", "workflow_report",
]
