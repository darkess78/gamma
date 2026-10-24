import os
import sys

from gamma.voice import worker_client
from gamma.voice.stt_client import STTWorkerClient


def test_loader_environment_exists_at_child_start_without_mutating_parent(monkeypatch, tmp_path):
    before = os.environ.get("LD_LIBRARY_PATH")
    explicit = {**os.environ, "FIXTURE_OWNED": "yes"}
    def prepare(env):
        env["LD_LIBRARY_PATH"] = "fixture-loader-at-exec"
        return env
    monkeypatch.setattr(worker_client, "prepend_cuda_library_path", prepare)
    child = '''
import json,os,sys
def send(**kw): print(json.dumps(dict(schema_version=1,pid=os.getpid(),**kw)),flush=True)
send(type='ready',model_loaded=False)
for line in sys.stdin:
    item=json.loads(line)
    if item['type']=='shutdown':
        send(type='stopped',request_id=item['request_id']); break
    send(type='result',request_id=item['request_id'],transcript=os.environ['LD_LIBRARY_PATH']+':'+os.environ['FIXTURE_OWNED'])
'''
    client = STTWorkerClient([sys.executable, "-u", "-c", child], cwd=tmp_path, env=explicit, timeout=3)
    try:
        assert client.transcribe_audio("fixture") == "fixture-loader-at-exec:yes"
        assert explicit.get("LD_LIBRARY_PATH") == before
        assert os.environ.get("LD_LIBRARY_PATH") == before
    finally:
        client.close()
