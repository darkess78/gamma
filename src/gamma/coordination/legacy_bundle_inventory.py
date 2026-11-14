"""Bounded file proof for the fixed legacy capture procedure."""
import hashlib
import os
import stat

from .store import private_path


def stat_fields(info):
    return {'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
            'ctime_ns': info.st_ctime_ns, 'mode': stat.S_IMODE(info.st_mode),
            'device': info.st_dev, 'inode': info.st_ino}


def fingerprint(path, *, limit):
    """Hash one unchanged regular file; never follow a link or wait on a FIFO."""
    if type(limit) is not int or limit <= 0:
        raise ValueError('legacy_file_limit_must_be_positive_integer')
    path = private_path(path)
    descriptor = None
    try:
        original = path.lstat()
        if not stat.S_ISREG(original.st_mode) or original.st_size > limit:
            raise ValueError('legacy_file_not_regular_or_exceeds_limit')
        expected = stat_fields(original)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or stat_fields(opened) != expected:
            raise ValueError('legacy_file_changed_before_read')
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(descriptor, 65536):
            total += len(chunk)
            if total > limit:
                raise ValueError('legacy_file_grew_beyond_limit')
            digest.update(chunk)
        named = private_path(path).lstat()
        if (not stat.S_ISREG(named.st_mode) or stat_fields(named) != expected
                or stat_fields(os.fstat(descriptor)) != expected
                or total != expected['size']):
            raise ValueError('legacy_file_changed_during_read')
        return {**expected, 'sha256': digest.hexdigest()}
    except OSError as exc:
        raise ValueError('legacy_file_unreadable_or_changed') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
