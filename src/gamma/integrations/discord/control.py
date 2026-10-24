"""Private, serialized dashboard commands. Interrupted actions never replay."""
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class ControlMailbox:
    def __init__(self, root):
        self.root = Path(root)
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ValueError('Invalid command storage')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != 'nt': os.chmod(self.root, 0o700)

    def _path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
            raise ValueError('Invalid command ID')
        path = self.root / (identifier + '.json')
        if path.is_symlink(): raise ValueError('Invalid command storage')
        return path

    @contextmanager
    def _lock(self):
        path = self.root / 'mailbox.lock'
        try: fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError: raise ValueError('Command storage busy') from None
        try: yield
        finally:
            os.close(fd)
            path.unlink()

    def _write(self, item):
        target = self._path(item['id'])
        temporary = self.root / (uuid.uuid4().hex + '.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(item, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        finally: temporary.unlink(missing_ok=True)

    def _records(self):
        return sorted((self.read(p.stem) for p in self.root.glob('*.json')), key=lambda x:x['created_at'])

    def read(self, identifier):
        path = self._path(identifier)
        if path.stat().st_size > 4096: raise ValueError('Invalid command record')
        item = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(item, dict) or item.get('id') != identifier: raise ValueError('Invalid command record')
        return item

    def submit(self, command):
        if command not in {'join','leave','pause','resume','status'}: raise ValueError('Invalid voice command')
        with self._lock():
            records = self._records()
            if any(x['status'] in {'queued','running'} for x in records):
                raise ValueError('Previous command outcome pending; check status')
            for record in records[:-31]: self._path(record['id']).unlink()
            item = {'id':uuid.uuid4().hex, 'command':command, 'status':'queued', 'created_at':time.time()}
            self._write(item)
            return item

    def claim(self):
        with self._lock():
            records = self._records()
            if any(x['status']=='running' for x in records): return None
            for item in records:
                if item['status'] != 'queued': continue
                if time.time() - item['created_at'] > 60:
                    item.update(status='failed', result={'ok':False,'reason':'expired'})
                    self._write(item)
                    continue
                item['status'] = 'running'
                self._write(item)
                return item
        return None

    def complete(self, identifier, result):
        with self._lock():
            item = self.read(identifier)
            if item['status'] != 'running': raise ValueError('Command not running')
            cleaned = {'ok':result.get('ok') is True}
            for key in ('state','reason'):
                value = result.get(key)
                if isinstance(value, str) and re.fullmatch('[a-z_]{1,80}',value): cleaned[key]=value
            item.update(status='completed', result=cleaned)
            self._write(item)
