"""Versioned local password verifiers and protected dashboard credential records."""
import base64

_VERSION = 'scrypt-v1'
_MAX_LEN = 120
_SALT_LEN = 16
_DIGEST_LEN = 32


def parse_hash(value):
    if not isinstance(value, str):
        raise ValueError('value must be a string')
    if len(value) > _MAX_LEN:
        raise ValueError('value too long')
    parts = value.split('$')
    if len(parts) != 3:
        raise ValueError('wrong number of parts')
    if parts[0] != _VERSION:
        raise ValueError('wrong version')
    try:
        salt = base64.b64decode(parts[1], validate=True)
        digest = base64.b64decode(parts[2], validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError('invalid base64') from exc
    if len(salt) != _SALT_LEN:
        raise ValueError('salt must be 16 bytes')
    if len(digest) != _DIGEST_LEN:
        raise ValueError('digest must be 32 bytes')
    if base64.b64encode(salt).decode('ascii') != parts[1] or base64.b64encode(digest).decode('ascii') != parts[2]:
        raise ValueError('noncanonical base64')
    return salt, digest

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading

_KDF_SLOTS = threading.BoundedSemaphore(2)

def hash_password(password):
    if not isinstance(password,str) or len(password)<12 or len(password.encode('utf-8'))>1024:
        raise ValueError('password length outside allowed bounds')
    salt=secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode('utf-8'),salt=salt,n=131072,r=8,p=1,dklen=32,maxmem=268435456)
    return 'scrypt-v1$'+base64.b64encode(salt).decode('ascii')+'$'+base64.b64encode(digest).decode('ascii')

def verify_password(password,encoded):
    if not isinstance(password,str):
        return False
    try:
        raw=password.encode('utf-8')
    except UnicodeError:
        return False
    if len(raw)>1024:
        return False
    try:
        salt,expected=parse_hash(encoded)
    except ValueError:
        return False
    if not _KDF_SLOTS.acquire(blocking=False):
        return False
    try:
        actual=hashlib.scrypt(raw,salt=salt,n=131072,r=8,p=1,dklen=32,maxmem=268435456)
        return hmac.compare_digest(actual,expected)
    except (ValueError,MemoryError):
        return False
    finally:
        _KDF_SLOTS.release()

def validate_credentials_record(value):
    fields={'version','username','password_hash','session_secret','credential_version'}
    if not isinstance(value,dict) or set(value)!=fields or type(value['version']) is not int or value['version']!=1:
        raise ValueError('unsupported dashboard credential record')
    for key,pattern in [('username',r'[A-Za-z0-9_.-]{1,80}'),('session_secret',r'[A-Za-z0-9_-]{32,100}'),('credential_version',r'[a-f0-9]{32}')]:
        if not isinstance(value[key],str) or re.fullmatch(pattern,value[key]) is None:
            raise ValueError('invalid dashboard credential record')
    parse_hash(value['password_hash'])
    return value

def load_credentials_file(filename):
    path=Path(filename).expanduser()
    if not path.is_absolute() or path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('credential file must be an explicit regular path')
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(descriptor,'rb') as stream:
        meta=os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_size>8192:
            raise ValueError('invalid credential file')
        if os.name=='posix' and (meta.st_uid!=os.getuid() or meta.st_mode & 0o077):
            raise ValueError('credential file must be private to its owner')
        raw=stream.read(8193)
    if len(raw)>8192:
        raise ValueError('credential file too large')
    return validate_credentials_record(json.loads(raw))
