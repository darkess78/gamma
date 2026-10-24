"""Discord dashboard status, implemented by Gamma's repository coding loop."""


def summarize(process, state, now):
    # Check if process is a dict and running is exactly True
    if not isinstance(process, dict):
        return {'connected': False, 'voice': 'disconnected', 'label': 'Bot stopped'}

    running = process.get('running')
    if running is not True:
        return {'connected': False, 'voice': 'disconnected', 'label': 'Bot stopped'}

    # Running is confirmed. Now check top-level state.updated_at.
    # state must be a dict
    if not isinstance(state, dict):
        return {'connected': False, 'voice': 'unknown', 'label': 'Bot running; status unverified'}

    # Only use top-level state.updated_at; ignore any heartbeat objects.
    updated_at = state.get('updated_at')

    # updated_at must be int/float excluding bool
    if isinstance(updated_at, bool):
        return {'connected': False, 'voice': 'unknown', 'label': 'Bot running; status unverified'}
    if not isinstance(updated_at, (int, float)):
        return {'connected': False, 'voice': 'unknown', 'label': 'Bot running; status unverified'}

    # Check finite
    if isinstance(updated_at, float) and (updated_at != updated_at or updated_at in (float('inf'), float('-inf'))):
        return {'connected': False, 'voice': 'unknown', 'label': 'Bot running; status unverified'}

    # now must be a number to calculate age
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        return {'connected': False, 'voice': 'unknown', 'label': 'Bot running; status unverified'}
    if isinstance(now, float) and (now != now or now in (float('inf'), float('-inf'))):
        return {'connected': False, 'voice': 'unknown', 'label': 'Bot running; status unverified'}

    age = now - updated_at

    # age must be between 0 and 5 inclusive
    if age < 0 or age > 5:
        return {'connected': False, 'voice': 'unknown', 'label': 'Bot running; status unverified'}

    # Fresh timestamp. Check connected status.
    connected = state.get('connected')
    if connected is not True:
        return {'connected': False, 'voice': 'disconnected', 'label': 'Bot reconnecting'}

    # Connected is True. Extract voice state.
    voice_data = state.get('voice')
    voice_state = 'unknown'
    if isinstance(voice_data, dict):
        vs = voice_data.get('state')
        if vs in ('listening', 'paused', 'disconnected', 'error', 'unavailable'):
            voice_state = vs

    return {
        'connected': True,
        'voice': voice_state,
        'label': f'Bot connected; voice {voice_state}'
    }
