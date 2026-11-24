"""Research-facing real-data boundary. Existing synthetic training is unchanged."""


def inspect_real_factors(start_month, end_month, *, memory="256MB", threads=1):
    """Return actual local data diagnostics, not a model-training panel."""
    from atlaspm.data.unified_pipeline import factor_preflight
    return factor_preflight(start_month, end_month, memory=memory, threads=threads)


def require_real_training_admission():
    """No independently certified real-data admission issuer exists yet.

    Do not accept a caller's boolean, self-authored manifest or exploratory
    receipt as authority. Preserve the trainer's existing ValueError contract.
    """
    raise ValueError("REAL_TRAINING_DATA_NOT_APPROVED")


def build_research_dataset(start_month, end_month, *, memory="256MB", threads=1, **options):
    """Canonical exploratory bridge using V6 storage, labels and folds."""
    from atlaspm.research.real_dataset_v6 import build
    if "verify_lineage" in options:
        raise ValueError("Unified lineage verification cannot be overridden")
    return build(start_month,end_month,memory=memory,threads=threads,
                 verify_lineage=True,**options)


def inspect_research_dataset(dataset_id, *, memory="256MB", threads=1, **options):
    from atlaspm.research.real_dataset_preflight_v61 import run
    from atlaspm.research.real_dataset_v6 import inspect
    manifest=inspect(dataset_id)
    if not manifest.get("lineage_id"):
        raise ValueError("Dataset lacks unified factor lineage; rebuild through canonical bridge")
    return run(dataset_id,memory=memory,threads=threads,**options)
