"""Typed finite release voice options; no coercion or arbitrary nested payloads."""
def _is_finite_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == value and value not in (float('inf'), float('-inf'))
    return False


def _check_range(value, low, high, inclusive_low=True, inclusive_high=True, *, name=None):
    if not _is_finite_number(value):
        raise ValueError('value must be a finite number')
    if inclusive_low and value < low:
        raise ValueError('value below minimum')
    if not inclusive_low and value <= low:
        raise ValueError('value must be greater than minimum')
    if inclusive_high and value > high:
        raise ValueError('value above maximum')
    if not inclusive_high and value >= high:
        raise ValueError('value must be less than maximum')


def _validate_nested_map(value, allowed_keys, low, high, name):
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be a dict')
    if not value:
        raise ValueError(f'{name} must be non-empty')
    if len(value) > 8:
        raise ValueError(f'{name} must have at most 8 entries')
    for k, v in value.items():
        if k not in allowed_keys:
            raise ValueError(f'{name} has unknown key {k!r}')
        _check_range(v, low, high, name=name)


def validate_voice_extra(extra, emotions):
    if not isinstance(extra, dict):
        raise ValueError('extra must be a dict')

    allowed_top = {
        'x_vector_only_mode', 'do_sample', 'seed', 'top_k', 'max_new_tokens',
        'min_new_tokens', 'temperature', 'top_p', 'repetition_penalty',
        'speed', 'output_peak', 'speed_by_emotion', 'output_peak_by_emotion'
    }

    for k in extra:
        if k not in allowed_top:
            raise ValueError(f'unknown key {k!r}')

    if 'x_vector_only_mode' in extra:
        if not isinstance(extra['x_vector_only_mode'], bool):
            raise ValueError('x_vector_only_mode must be bool')
    if 'do_sample' in extra:
        if not isinstance(extra['do_sample'], bool):
            raise ValueError('do_sample must be bool')

    if 'seed' in extra:
        v = extra['seed']
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError('seed must be int')
        if v < 0 or v > 2**32 - 1:
            raise ValueError('seed out of range')

    if 'top_k' in extra:
        v = extra['top_k']
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError('top_k must be int')
        if v < 0 or v > 1000:
            raise ValueError('top_k out of range')

    if 'max_new_tokens' in extra:
        v = extra['max_new_tokens']
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError('max_new_tokens must be int')
        if v < 1 or v > 2048:
            raise ValueError('max_new_tokens out of range')

    if 'min_new_tokens' in extra:
        v = extra['min_new_tokens']
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError('min_new_tokens must be int')
        if v < 0 or v > 2048:
            raise ValueError('min_new_tokens out of range')

    if 'min_new_tokens' in extra and 'max_new_tokens' in extra:
        if extra['min_new_tokens'] > extra['max_new_tokens']:
            raise ValueError('min_new_tokens must be <= max_new_tokens')

    if 'temperature' in extra:
        _check_range(extra['temperature'], 0, 2, name='temperature')
    if 'top_p' in extra:
        _check_range(extra['top_p'], 0, 1, inclusive_low=False, name='top_p')
    if 'repetition_penalty' in extra:
        _check_range(extra['repetition_penalty'], 0.5, 2, name='repetition_penalty')
    if 'speed' in extra:
        _check_range(extra['speed'], 0.75, 1.2, name='speed')
    if 'output_peak' in extra:
        _check_range(extra['output_peak'], 0.35, 0.95, name='output_peak')

    allowed_emotion_keys = set(emotions) | {'default'}

    if 'speed_by_emotion' in extra:
        _validate_nested_map(extra['speed_by_emotion'], allowed_emotion_keys, 0.75, 1.2, 'speed_by_emotion')
    if 'output_peak_by_emotion' in extra:
        _validate_nested_map(extra['output_peak_by_emotion'], allowed_emotion_keys, 0.48, 0.78, 'output_peak_by_emotion')

    return None
