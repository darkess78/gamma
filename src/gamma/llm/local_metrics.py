"""Allowlisted numeric Ollama timings, without prompts or provider output."""
import math

def _is_valid_int(value, max_value):
    if type(value) is not int:
        return False
    if value < 0 or value > max_value:
        return False
    return True


def local_generation_metrics(payload):
    if not isinstance(payload, dict):
        return {}

    result = {}

    duration_map = {
        'total_duration': 'total_ms',
        'load_duration': 'load_ms',
        'prompt_eval_duration': 'prompt_eval_ms',
        'eval_duration': 'eval_ms',
    }

    for key, out_key in duration_map.items():
        value = payload.get(key)
        if _is_valid_int(value, 10**18):
            result[out_key] = round(value / 1_000_000, 3)

    count_map = {
        'prompt_eval_count': 'prompt_eval_count',
        'eval_count': 'eval_count',
    }

    for key, out_key in count_map.items():
        value = payload.get(key)
        if _is_valid_int(value, 10**9):
            result[out_key] = value

    eval_count = result.get('eval_count')
    eval_ms = result.get('eval_ms')
    if eval_count is not None and eval_ms is not None and eval_ms > 0:
        result['tokens_per_second'] = round(eval_count * 1000 / eval_ms, 3)

    return result


def local_cache_counts(payload):
    """Separate native cached and processed counts without changing timing normalization."""
    if not isinstance(payload, dict):
        return {}
    total, cached = payload.get('prompt_eval_count'), payload.get('prompt_eval_cached_count')
    if not _is_valid_int(total, 10**9) or not _is_valid_int(cached, 10**9) or cached > total:
        return {}
    return {'cached_n': cached, 'processed_n': total - cached}


def allowlisted_local_timing(mapping):
    """Copy only bounded numeric stage metrics, including an allowed review."""
    if not isinstance(mapping, dict):
        return {}
    result = {}
    durations = {'total_ms', 'load_ms', 'prompt_eval_ms', 'eval_ms',
                 'review_ms', 'admission_ms', 'http_ms', 'verification_ms', 'prime_ms'}
    counts = {'prompt_eval_count', 'eval_count', 'tokens_per_second', 'cached_n', 'processed_n',
              'prime_cached_n', 'prime_processed_n', 'prefix_cache_verified',
              'prefix_prime_attempted', 'prefix_prime_succeeded'}
    for key in durations | counts:
        value = mapping.get(key)
        maximum = 10**12 if key in durations else 10**9
        if type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value):
            result[key] = value
    return result
