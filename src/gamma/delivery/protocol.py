def validate_ack(message):
    if not isinstance(message, dict):
        raise ValueError("message must be a dict")

    allowed_keys = {'type', 'schema_version', 'lease_id', 'event_id', 'status'}
    if set(message.keys()) != allowed_keys:
        raise ValueError("unexpected or missing keys")

    if message['type'] != 'playback_ack':
        raise ValueError("type must be 'playback_ack'")

    if isinstance(message['schema_version'], bool) or not isinstance(message['schema_version'], int):
        raise ValueError("schema_version must be an integer")
    if message['schema_version'] != 1:
        raise ValueError("schema_version must be 1")

    if not isinstance(message['lease_id'], str) or not message['lease_id'] or len(message['lease_id']) > 128:
        raise ValueError("lease_id must be a nonempty string")

    if not isinstance(message['event_id'], str) or not message['event_id'] or len(message['event_id']) > 128:
        raise ValueError("event_id must be a nonempty string with at most 128 characters")

    if message['status'] not in ('started', 'completed', 'failed', 'interrupted'):
        raise ValueError("status must be one of: started, completed, failed, interrupted")

    return (message['lease_id'], message['event_id'], message['status'])


def next_status(current, requested):
    valid_current = {'offered', 'started', 'completed', 'failed', 'interrupted'}
    valid_requested = {'started', 'completed', 'failed', 'interrupted'}

    if current not in valid_current:
        raise ValueError("invalid current status")
    if requested not in valid_requested:
        raise ValueError("invalid requested status")

    if current == requested:
        return requested

    transitions = {
        'offered': {'started', 'failed', 'interrupted'},
        'started': {'completed', 'failed', 'interrupted'},
        'completed': set(),
        'failed': set(),
        'interrupted': set(),
    }

    if requested not in transitions[current]:
        raise ValueError("transition from '%s' to '%s' is not allowed" % (current, requested))

    return requested
