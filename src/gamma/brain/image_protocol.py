import re


def validate_manifest(value, max_bytes=8000000):
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError('invalid image manifest')
    cap = min(max_bytes, 8000000)
    if type(value) is not list or len(value) > 4:
        raise ValueError('invalid image manifest')
    seen = set()
    result = []
    for item in value:
        if type(item) is not dict:
            raise ValueError('invalid image manifest')
        if set(item.keys()) != {'id', 'size', 'sha256', 'media_type'}:
            raise ValueError('invalid image manifest')
        id_val = item['id']
        if type(id_val) is not str or not re.fullmatch(r'[0-9a-f]{32}', id_val):
            raise ValueError('invalid image manifest')
        if id_val in seen:
            raise ValueError('invalid image manifest')
        seen.add(id_val)
        size_val = item['size']
        if type(size_val) is not int or not (1 <= size_val <= cap):
            raise ValueError('invalid image manifest')
        sha_val = item['sha256']
        if type(sha_val) is not str or not re.fullmatch(r'[0-9a-f]{64}', sha_val):
            raise ValueError('invalid image manifest')
        mt_val = item['media_type']
        if type(mt_val) is not str or mt_val not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
            raise ValueError('invalid image manifest')
        result.append({'id': id_val, 'size': size_val, 'sha256': sha_val, 'media_type': mt_val})
    return result
