import importlib.util
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import psutil
import pytest

from gamma.voice import stt_client as module

CHILD = r'''
import json, os, sys, time
from pathlib import Path
def send(**payload):
    print(json.dumps(dict(schema_version=1,pid=os.getpid(),**payload)),flush=True)
send(type='ready',model_loaded=False)
for line in sys.stdin:
    request=json.loads(line)
    identity=request['request_id']
    if request['type']=='shutdown':
        send(type='stopped',request_id=identity)
        break
    value=request['input_path']
    if value=='slow':
        Path('entered').write_text(str(os.getpid()))
        time.sleep(5)
    if value=='wrong': identity='unrelated'
    if value=='oversize':
        print('x'*70000,flush=True)
        continue
    send(type='result',request_id=identity,transcript=value)
'''


def client(tmp_path, timeout=2):
    return module.STTWorkerClient([sys.executable, "-u", "-c", CHILD], cwd=tmp_path, timeout=timeout)


def test_same_child_serves_requests_and_shutdown_reaps_it(tmp_path):
    transport = client(tmp_path)
    try:
        assert transport.transcribe_audio("one") == "one"
        pid = transport.status()["pid"]
        assert psutil.pid_exists(pid)
        assert transport.transcribe_audio("two") == "two"
        assert transport.status()["pid"] == pid
    finally:
        transport.close()
    assert not psutil.pid_exists(pid)
    assert not transport.status()["running"]


@pytest.mark.parametrize("value", ["wrong", "oversize"])
def test_invalid_protocol_terminates_child_without_retry(tmp_path, value):
    transport = client(tmp_path)
    transport.start()
    pid = transport.status()["pid"]
    with pytest.raises(Exception): transport.transcribe_audio(value)
    assert not psutil.pid_exists(pid)
    assert not transport.status()["running"]
    transport.close()


def test_busy_controls_reject_and_timeout_reaps_exact_child(tmp_path):
    transport = client(tmp_path, timeout=0.5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(transport.transcribe_audio, "slow")
        deadline = time.monotonic() + 3
        while not (tmp_path / "entered").exists():
            assert time.monotonic() < deadline
            time.sleep(0.005)
        pid = int((tmp_path / "entered").read_text())
        assert transport.status()["busy"]
        with pytest.raises(Exception): transport.close()
        with pytest.raises(Exception): transport.transcribe_audio("other")
        with pytest.raises(Exception): future.result(4)
        assert not psutil.pid_exists(pid)
    transport.close()


def test_unread_input_cannot_bypass_timeout(tmp_path):
    child = "import json,os,time; print(json.dumps(dict(schema_version=1,pid=os.getpid(),type='ready',model_loaded=False)),flush=True); time.sleep(10)"
    transport = module.STTWorkerClient([sys.executable, "-u", "-c", child], cwd=tmp_path, timeout=0.5)
    transport.start()
    pid = transport.status()["pid"]
    started = time.monotonic()
    with pytest.raises(Exception): transport.transcribe_audio("\u1234" * 4096)
    assert time.monotonic() - started < 4
    assert not psutil.pid_exists(pid)
