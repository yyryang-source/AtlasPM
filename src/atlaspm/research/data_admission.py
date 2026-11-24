"""Research-facing real-data boundary. Existing synthetic training is unchanged."""
from atlaspm.data.unified import AdmissionDenied
from atlaspm.data.unified_pipeline import factor_preflight


def inspect_real_factors(start_month, end_month, *, memory="256MB", threads=1):
    """Return actual local data diagnostics, not a model-training panel."""
    return factor_preflight(start_month, end_month, memory=memory, threads=threads)


def require_real_training_admission():
    """No independently certified real-data admission issuer exists yet.

    Do not accept a caller's boolean, self-authored manifest or exploratory
    receipt as authority. Existing synthetic trainers retain their own guard.
    """
    raise AdmissionDenied("REAL_TRAINING_DATA_NOT_APPROVED")
