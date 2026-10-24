"""OS-backed exclusion between adapter trials and repository inference."""
from .autonomous_store import execution_lock


def candidate_busy(coding):
    try:
        with execution_lock(coding / 'candidates'):
            return False
    except (OSError, ValueError):
        return True
