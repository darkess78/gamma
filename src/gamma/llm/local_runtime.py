"""Finite request-local inference options for the pinned private release."""
import re
import math
from contextlib import contextmanager
from contextvars import ContextVar

_local_runtime_profile_var: ContextVar[dict] = ContextVar('local_runtime_profile', default={})

_KEEP_ALIVE_INT_MAX = 1800
_KEEP_ALIVE_STR_RE = re.compile(r'^[1-9][0-9]*[sm]$')
_REVIEW_ENDPOINT_RE = re.compile(r'^http://(127\.0\.0\.1|\[::1\]):11435/?$')


def _keep_alive_seconds(value):
    if isinstance(value, bool):
        raise ValueError('keep_alive must be int or str, not bool')
    if type(value) is int:
        if value < 0 or value > _KEEP_ALIVE_INT_MAX:
            raise ValueError('keep_alive int must be 0..1800')
        return value
    if type(value) is str:
        if value == '0':
            return 0
        if not _KEEP_ALIVE_STR_RE.fullmatch(value):
            raise ValueError('keep_alive str must match [1-9][0-9]*[sm]')
        unit = value[-1]
        number = int(value[:-1])
        seconds = number * (60 if unit == 'm' else 1)
        if seconds > _KEEP_ALIVE_INT_MAX:
            raise ValueError('keep_alive duration must be <= 1800 seconds')
        return seconds
    raise ValueError('keep_alive must be int or str')


def validate_keep_alive(value):
    _keep_alive_seconds(value)
    return value


def validate_review_endpoint(value):
    if type(value) is not str:
        raise ValueError('review_endpoint must be str')
    match = _REVIEW_ENDPOINT_RE.fullmatch(value)
    if not match:
        raise ValueError('review_endpoint must be http://127.0.0.1:11435 or http://[::1]:11435')
    return 'http://' + match.group(1) + ':11435'


def validate_review_num_gpu(value):
    """Allow only measured CPU or bounded 32-layer reviewer offload."""
    if type(value) is not int or value not in {0, 32}:
        raise ValueError('review_num_gpu must be int 0 or 32')
    return value


def validate_primary_temperature(value):
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise ValueError('primary_temperature must be finite int or float 0..1')
    return value


def validate_primary_prefix_cache(value):
    if type(value) is not bool:
        raise ValueError('primary_prefix_cache must be bool')
    return value


def validate_review_num_threads(value):
    if type(value) is not int or not 1 <= value <= 16:
        raise ValueError('review_num_threads must be int 1..16')
    return value


def current_local_runtime_profile():
    return dict(_local_runtime_profile_var.get())


@contextmanager
def local_runtime_profile(
    *, primary_keep_alive=None,
    review_endpoint=None,
    review_keep_alive=None,
    review_num_gpu=None,
    review_num_threads=None,
    primary_temperature=None,
    primary_prefix_cache=None,
):
    profile = {}
    if primary_keep_alive is not None:
        profile['primary_keep_alive'] = validate_keep_alive(primary_keep_alive)
    if review_endpoint is not None:
        profile['review_endpoint'] = validate_review_endpoint(review_endpoint)
    if review_keep_alive is not None:
        profile['review_keep_alive'] = validate_keep_alive(review_keep_alive)
    if review_num_gpu is not None:
        profile['review_num_gpu'] = validate_review_num_gpu(review_num_gpu)

    if review_num_threads is not None:
        profile['review_num_threads'] = validate_review_num_threads(review_num_threads)

    if primary_temperature is not None:
        profile['primary_temperature'] = validate_primary_temperature(primary_temperature)

    if primary_prefix_cache is not None:
        profile['primary_prefix_cache'] = validate_primary_prefix_cache(primary_prefix_cache)

    token = _local_runtime_profile_var.set(profile)
    try:
        yield dict(profile)
    finally:
        _local_runtime_profile_var.reset(token)
