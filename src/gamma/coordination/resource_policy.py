"""Strict byte accounting for finite local recovery operations."""


def storage_admitted(free_bytes, requested_bytes, reserve_bytes):
    if any(type(value) is not int or value < 0 for value in (free_bytes, requested_bytes, reserve_bytes)):
        raise ValueError('invalid_storage_byte_count')
    return free_bytes >= requested_bytes + reserve_bytes
