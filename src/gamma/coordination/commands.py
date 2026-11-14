"""Validate private activity commands; state mutation belongs to Core."""
TERMINAL_STATES = {'completed', 'blocked', 'needs_checker', 'stopped'}


def _is_live(activity, runs):
    run = next((r for r in runs if r['id'] == activity.get('worker_run')), None)
    return bool(run and run['status'] not in TERMINAL_STATES
                and not (run['status'] == 'paused' and run.get('desired_state') == 'pause'))


def plan_command(action, run_id, exercise_id, activities, runs, permit, now):
    result = {'accepted': False, 'reason': 'not_allowed', 'action': action, 'target_id': run_id, 'pause_ids': []}
    act = next((a for a in activities if a['id'] == run_id), None)
    run = next((r for r in runs if r['id'] == run_id), None)
    state = run['status'] if run else act['status'] if act else None
    expires_at = run['expires_at'] if run else act.get('expires_at', 0) if act else 0
    if action == 'request_practice':
        if not permit or permit.get('expires_at', 0) <= now or permit.get('remaining_runs', 0) <= 0:
            result['reason'] = 'permit_invalid'
            return result
        if exercise_id not in permit.get('exercise_ids', []):
            result['reason'] = 'exercise_not_allowed'
            return result
        result['accepted'] = True
        result['reason'] = 'ok'
        return result

    if act is None:
        result['reason'] = 'unknown_activity'
        return result

    if action in ('pause', 'stop'):
        if state in TERMINAL_STATES or act['status'] in TERMINAL_STATES:
            result['reason'] = 'target_not_active'
            return result
        result['accepted'] = True
        result['reason'] = 'ok'
        return result

    if action == 'resume':
        if state != 'paused':
            result['reason'] = 'not_paused'
            return result
        if expires_at <= now:
            result['reason'] = 'expired'
            return result
        live_others = [a for a in activities if a['id'] != act['id'] and _is_live(a, runs)]
        if live_others:
            result['reason'] = 'other_live_worker'
            return result
        result['accepted'] = True
        result['reason'] = 'ok'
        return result

    if action == 'switch':
        if state not in ('waiting', 'paused'):
            result['reason'] = 'target_not_waiting_or_paused'
            return result
        if expires_at <= now:
            result['reason'] = 'expired'
            return result
        pause_ids = [a['worker_run'] for a in activities if a['id'] != act['id'] and _is_live(a, runs)]
        result['accepted'] = True
        result['reason'] = 'ok'
        result['pause_ids'] = pause_ids
        return result

    result['reason'] = 'unknown_action'
    return result
