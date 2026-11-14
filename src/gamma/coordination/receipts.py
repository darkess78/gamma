"""Factual private practice receipts."""

import re

_HEX_RE = re.compile(r'^[0-9a-f]{64}$')

def _is_true(value):
    return value is True

def _is_valid_hex64(value):
    return isinstance(value, str) and bool(_HEX_RE.fullmatch(value))

def work_status_text(activity):
    if activity is None:
        return "No authorized practical task."

    aid = activity.get("id", "unknown")
    goal = activity.get("goal", "unknown")
    status = activity.get("status", "unknown")
    mode = activity.get("mode", "")
    kind = activity.get("kind", "")
    source_sha256 = activity.get("source_sha256", "")
    checks_source_sha256 = activity.get("checks_source_sha256", "")
    checks = activity.get("checks", [])
    attempts = activity.get("attempt_count", 0)
    reason = activity.get("reason", "")
    help_needed = activity.get("help_needed", "")
    desired_state = activity.get("desired_state", "")
    research_ref = activity.get("research_ref", "")
    base = f"The {goal} task, run {aid}"

    if status == "blocked":
        parts = [f"{base} is blocked."]
        if reason:
            parts.append(f"Reason: {reason}.")
        parts.append(f"Attempts: {attempts}.")
        if help_needed:
            parts.append(f"Help needed: {help_needed}.")
        parts.append("Completion is not verified.")
        return " ".join(parts)

    if status == "completed":
        if mode == "research":
            if not _is_valid_hex64(source_sha256) or not _is_valid_hex64(research_ref):
                return f"{base} has incomplete research evidence. Not verified."
            research_bits = []
            if _is_valid_hex64(source_sha256):
                research_bits.append(f"Source hash: {source_sha256}.")
            if _is_valid_hex64(research_ref):
                research_bits.append(f"Research ref: {research_ref}.")
            research_bits.append(f"Attempts: {attempts}.")
            research_bits.append("No code was built.")
            return f"{base} is a completed research outcome. " + " ".join(research_bits)

        if not isinstance(checks, list) or not checks or any(not isinstance(c,dict) or not c.get('name') for c in checks):
            return f"{base} completed, but no nonempty checks are present. Not verified."
        nonempty_checks = checks

        verified_candidate = True
        for c in nonempty_checks:
            if not _is_true(c.get("passed")):
                verified_candidate = False
                break
            if c.get("exit_code") != 0 or not isinstance(c.get("exit_code"), int) or isinstance(c.get("exit_code"), bool):
                verified_candidate = False
                break

        if not _is_valid_hex64(source_sha256):
            verified_candidate = False
        if not _is_valid_hex64(checks_source_sha256):
            verified_candidate = False
        if checks_source_sha256 != source_sha256:
            verified_candidate = False

        check_names = ", ".join(str(c.get("name", "unknown")) for c in nonempty_checks)
        if verified_candidate:
            return (f"{base} is a verified candidate only. "
                    f"Source hash: {source_sha256}. "
                    f"Attempts: {attempts}. "
                    f"Checks: {check_names}. "
                    f"Activation is pending user approval.")
        return (f"{base} completed, but is not verified. "
                f"Source hash: {source_sha256 if _is_valid_hex64(source_sha256) else 'missing or invalid'}. "
                f"Checks source hash: {checks_source_sha256 if _is_valid_hex64(checks_source_sha256) else 'missing or invalid'}. "
                f"Attempts: {attempts}. Checks: {check_names}. Not verified.")

    if status == "needs_checker":
        return f"{base} needs review after {attempts} attempts. Reason: {reason}. {help_needed} Completion is not verified."

    if status == "running":
        if desired_state == "pause":
            return f"{base}. Running. Pause requested. Not verified."
        return f"{base}. Running. Not verified."

    if status == "paused":
        if desired_state == "pause":
            return f"{base}. Paused. Pause acknowledged. Not verified."
        return f"{base}. Paused. Not verified."

    return f"{base}. Status: {status}. Not verified."

def status_text(activity):
    if activity is None:
        return "No authorized practice."

    aid = activity.get("id", "unknown")
    goal = activity.get("goal", "unknown").replace('_', ' ')
    status = activity.get("status", "unknown")
    task_results = activity.get("task_results", [])
    reason = activity.get("reason", "")
    desired_state = activity.get("desired_state", "")

    base = f"The {goal} exercise, run {aid}"

    if status == "completed":
        completed_success = False
        if task_results:
            all_verified = True
            for tr in task_results:
                if tr.get("status") != "verified_practice":
                    all_verified = False
                    break
                if not _is_true(tr.get("checks_passed")):
                    all_verified = False
                    break
                if not _is_true(tr.get("independent_evaluation_passed")):
                    all_verified = False
                    break
            completed_success = all_verified

        if completed_success:
            return f"{base}, passed its protected checks and independent evaluation. This verifies offline practice only."
        return f"{base}. Not verified."

    if status == "blocked":
        if "budget_exhausted" in reason:
            return f"{base} is blocked because its time budget expired. Completion is not verified."
        if "inference_unavailable" in reason or "uncertain" in reason:
            return f"{base}. Blocked. Inference unavailable. No automatic replay."
        if "invalid" in reason or "checkpoint" in reason:
            return f"{base}. Blocked. Invalid artifacts. Evidence review required."
        return f"{base}. Blocked. Reason: {reason}"

    if status == "needs_checker":
        if task_results:
            attempt_count = sum(t.get("attempt_count", 0) for t in task_results)
            failed_checks = sorted({c for t in task_results for c in t.get("failed_public_checks", [])})
            fail_text = ", ".join(str(c) for c in failed_checks) if failed_checks else "no details"
            detail = 'attempt limit reached' if any(t.get('reason') == 'attempt_limit' for t in task_results) else 'verification did not pass'
            return f"{base} needs review after {attempt_count} attempts, {detail}. Failed checks: {fail_text}. Completion is not verified."
        return f"{base}. Needs checker."

    if status == "running":
        if desired_state == "pause":
            return f"{base}. Running. Pause requested. Not verified."
        return f"{base}. Running. Not verified."

    if status == "paused":
        if desired_state == "pause":
            return f"{base}. Paused. Pause acknowledged. Not verified."
        return f"{base}. Paused. Not verified."

    return f"{base}. Status: {status}. Not verified."
