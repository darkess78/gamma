from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from gamma.config import settings
from gamma.persona.assistant_state import AssistantStateStore
from gamma.persona.emotion_service import EmotionMemoryService
from gamma.persona import state_io
from test_conversation_recipient import Capture, PRIVATE, SHARED, fixture_state, prompt_layers

KINDS = ("feeling", "emotion")


def store(kind, path):
    return AssistantStateStore(path) if kind == "feeling" else EmotionMemoryService(path)


def update(item, label):
    kwargs = dict(emotion="excited", user_text="thank you " + label, reply_text="reply " + label)
    return item.update(**kwargs) if isinstance(item, AssistantStateStore) else item.update_from_turn(**kwargs)


def read(item):
    return item.load() if isinstance(item, AssistantStateStore) else item.load_bundle()


def notes(kind, item):
    value = read(item)
    return value.notes if kind == "feeling" else value["state"].notes


def preserved(kind, item, count, labels):
    value = read(item)
    state = value if kind == "feeling" else value["state"]
    assert len(state.notes) == count
    assert len(state.recent_emotions) == count
    assert state.current_emotion == "excited"
    for label in labels:
        assert any(label in note for note in state.notes)
    if kind == "emotion":
        assert len(value["episodes"]) == count
        assert len(value["patterns"]) == 1
        assert value["patterns"][0].evidence_count == count
        assert value["patterns"][0].confidence == pytest.approx(min(.95, .55 + .08 * (count - 1)))


@pytest.mark.skipif(os.name != "posix", reason="real flock observation on Gamma PC")
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("alias", [False, True])
def test_entire_mutation_serializes_separate_thread_instances(tmp_path, monkeypatch, kind, alias):
    import fcntl
    path = tmp_path / "state.json"
    a = store(kind, path)
    alternate = tmp_path / "alias.json"
    if alias:
        alternate.symlink_to(path)
    b = store(kind, alternate if alias else path)
    loaded, release, attempted, b_read = [threading.Event() for _ in range(4)]
    method = "load" if kind == "feeling" else "load_bundle"
    load_a, load_b = getattr(a, method), getattr(b, method)
    def hold():
        value = load_a()
        loaded.set()
        assert release.wait(8)
        return value
    def observe_read():
        b_read.set()
        return load_b()
    monkeypatch.setattr(a, method, hold)
    monkeypatch.setattr(b, method, observe_read)
    flock = fcntl.flock
    def observe_lock(fd, flags):
        if threading.current_thread().name == "writer-B" and flags == fcntl.LOCK_EX:
            attempted.set()
        return flock(fd, flags)
    monkeypatch.setattr(fcntl, "flock", observe_lock)
    errors = []
    def run(item):
        try: update(item, threading.current_thread().name)
        except BaseException as exc: errors.append(exc)
    first = threading.Thread(target=run, args=(a,), name="writer-A")
    second = threading.Thread(target=run, args=(b,), name="writer-B")
    first.start()
    try:
        assert loaded.wait(5)
        second.start()
        attempted_seen = attempted.wait(2)
        read_before_release = b_read.is_set()
    finally:
        release.set()
        first.join(10)
        if second.ident: second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    monkeypatch.setattr(a, method, load_a)
    preserved(kind, a, 2, ["writer-A", "writer-B"])
    assert attempted_seen, "B never acquired the full mutation lock"
    assert not read_before_release, "B read stale state before holding its lock"
    if alias: assert alternate.is_symlink()


CHILD = r"""
import fcntl, json, sys, time
from pathlib import Path
from gamma.persona.assistant_state import AssistantStateStore
from gamma.persona.emotion_service import EmotionMemoryService
kind,path,label,root,mode = sys.argv[1:]
root=Path(root);path=Path(path)
item=AssistantStateStore(path) if kind=="feeling" else EmotionMemoryService(path)
method="load" if kind=="feeling" else "load_bundle"
old=getattr(item,method)
def load():
    result=old()
    if mode=="hold":
        (root/(label+".loaded")).write_text("ready")
        until=time.monotonic()+12
        while not (root/"release").exists():
            if time.monotonic()>until: raise TimeoutError("release")
            time.sleep(.01)
    return result
setattr(item,method,load)
lock=fcntl.flock
def flock(fd,flags):
    if flags==fcntl.LOCK_EX: (root/(label+".attempt")).write_text("ready")
    return lock(fd,flags)
fcntl.flock=flock
kwargs=dict(emotion="excited",user_text="thank you "+label,reply_text="reply "+label)
item.update(**kwargs) if kind=="feeling" else item.update_from_turn(**kwargs)
(root/(label+".done")).write_text("done")
"""


def wait_file(path, child=None):
    deadline = time.monotonic() + 8
    while not path.exists():
        if child is not None and child.poll() is not None:
            raise AssertionError("child exited " + str(child.returncode))
        assert time.monotonic() < deadline, str(path)
        time.sleep(.01)


@pytest.mark.skipif(os.name != "posix", reason="fresh Gamma processes")
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("death", [False, True])
def test_fresh_process_overlap_and_owned_child_death(tmp_path, kind, death):
    path = tmp_path / "state.json"
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    def launch(label, mode):
        return subprocess.Popen([sys.executable, "-B", "-c", CHILD, kind, str(path),
                                 label, str(tmp_path), mode], env=environment,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    a = launch("child-A", "hold")
    b = None
    try:
        wait_file(tmp_path / "child-A.loaded", a)
        lock = path.with_name(path.name + ".lock")
        inode = lock.stat().st_ino
        b = launch("child-B", "normal")
        wait_file(tmp_path / "child-B.attempt", b)
        assert not (tmp_path / "child-B.done").exists()
        if death: a.kill()
        else: (tmp_path / "release").write_text("go")
        a.communicate(timeout=10)
        _, error = b.communicate(timeout=10)
        assert b.returncode == 0, error.decode()
        assert lock.stat().st_ino == inode, "stable sidecar was replaced or removed"
        preserved(kind, store(kind, path), 1 if death else 2,
                  ["child-B"] if death else ["child-A", "child-B"])
    finally:
        for child in (a, b):
            if child is not None:
                if child.poll() is None: child.kill()
                child.communicate(timeout=5)


@pytest.mark.parametrize("kind", KINDS)
def test_independent_paths_progress_while_another_writer_holds_lock(tmp_path, kind):
    path, other = tmp_path / "one.json", tmp_path / "two.json"
    with ThreadPoolExecutor(max_workers=1) as pool:
        with state_io.state_file_lock(path):
            future = pool.submit(update, store(kind, other), "independent")
            future.result(timeout=5)
    preserved(kind, store(kind, other), 1, ["independent"])


class SplitWriter:
    def __init__(self, stream, partial, release, fail=False):
        self.stream, self.partial, self.release, self.fail = stream, partial, release, fail
    def __enter__(self): return self
    def __exit__(self, *args): return self.stream.__exit__(*args)
    def write(self, text):
        middle = len(text) // 2
        self.stream.write(text[:middle])
        self.stream.flush()
        self.partial.set()
        if self.fail: raise OSError("injected partial write")
        assert self.release.wait(8)
        self.stream.write(text[middle:])
        return len(text)


@pytest.mark.parametrize("kind", KINDS)
def test_reader_observes_previous_complete_json_during_partial_temp_write(tmp_path, monkeypatch, kind):
    path = tmp_path / "state.json"
    item = store(kind, path)
    update(item, "old-α")
    old = path.read_bytes()
    partial, release = threading.Event(), threading.Event()
    fdopen = state_io.os.fdopen
    def split(*args, **kwargs):
        return SplitWriter(fdopen(*args, **kwargs), partial, release)
    monkeypatch.setattr(state_io.os, "fdopen", split)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(update, store(kind, path), "new-β")
        try:
            assert partial.wait(5)
            assert path.read_bytes() == old
            for _ in range(20):
                assert json.loads(path.read_text(encoding="utf-8"))
                assert notes(kind, item) == notes(kind, store(kind, path))
                assert not any("new-β" in note for note in notes(kind, item))
        finally: release.set()
        future.result(timeout=10)
    preserved(kind, item, 2, ["old-α", "new-β"])
    assert not list(tmp_path.glob("state.json.tmp-*"))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("failure", ["replace", "write"])
def test_failed_save_retains_bytes_and_releases_lock(tmp_path, monkeypatch, kind, failure):
    path = tmp_path / "state.json"
    item = store(kind, path)
    update(item, "accepted")
    old, mode = path.read_bytes(), path.stat().st_mode
    with monkeypatch.context() as patch:
        if failure == "replace":
            def fail(*args): raise OSError("injected replace")
            patch.setattr(state_io.os, "replace", fail)
        else:
            fdopen = state_io.os.fdopen
            patch.setattr(state_io.os, "fdopen", lambda *args, **kwargs:
                SplitWriter(fdopen(*args, **kwargs), threading.Event(), threading.Event(), fail=True))
        with pytest.raises(OSError, match="injected"):
            update(item, "rejected")
    assert path.read_bytes() == old
    assert path.stat().st_mode == mode
    assert not list(tmp_path.glob("state.json.tmp-*"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(update, store(kind, path), "after-failure").result(timeout=5)
    preserved(kind, item, 2, ["accepted", "after-failure"])
    assert all("rejected" not in note for note in notes(kind, item))


@pytest.mark.parametrize("kind", KINDS)
def test_caps_and_sequential_semantics_preserved(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(settings, "assistant_emotion_episode_threshold", .5)
    item = store(kind, tmp_path / "state.json")
    result = None
    for number in range(45): result = update(item, str(number))
    value = read(item)
    state = value if kind == "feeling" else value["state"]
    assert len(state.notes) == len(state.recent_emotions) == 8
    assert all(str(number) in state.notes[number - 37] for number in range(37, 45))
    if kind == "feeling":
        assert result == state
        invalid = item.update(emotion="UNKNOWN", user_text="", reply_text="")
        assert invalid.current_emotion == "neutral" and len(invalid.notes) == 8
    else:
        assert result is None
        assert len(value["episodes"]) == 40
        assert value["patterns"][0].evidence_count == 45
        assert value["patterns"][0].confidence == .95
        assert len(item.dashboard_payload()["episodes"]) == 8


@pytest.mark.parametrize("recipient", [None, SHARED, PRIVATE])
def test_actual_conversation_writes_and_recipient_views(fixture_state, recipient):
    service = fixture_state.service
    canary = "JSON_WRITER_PRIVATE_CANARY"
    response = service.respond("thank you " + canary, fast_mode=True, recipient=PRIVATE,
                               session_id="writer-path")
    assert response.display_text == "Safe fixture reply."
    feeling = AssistantStateStore(fixture_state.data / "assistant_state.json").load()
    emotion = EmotionMemoryService(fixture_state.data / "assistant_emotion_memory.json").load_bundle()
    assert any(canary in note for note in feeling.notes)
    assert any(canary in note for note in emotion["state"].notes)
    service._llm = Capture()
    service.respond("fixture topic", fast_mode=True, recipient=recipient, evaluation_mode=True)
    layers = prompt_layers(service._llm)
    assert any(canary in part for part in layers) == (recipient is PRIVATE)


def test_pattern_cap_and_existing_field_shapes(tmp_path):
    from gamma.persona.emotion_models import AssistantEmotionState, EmotionalPattern
    path = tmp_path / "emotion.json"
    item = EmotionMemoryService(path)
    patterns = [EmotionalPattern(pattern_text="prior " + str(n), emotion_family="happy",
                confidence=.6, evidence_count=2, subject_scope="user", last_reinforced_at="prior")
                for n in range(20)]
    item._save(state=AssistantEmotionState(), episodes=[], patterns=patterns)
    update(item, "new-pattern")
    bundle = item.load_bundle()
    assert len(bundle["patterns"]) == 20
    assert bundle["patterns"][0].pattern_text == "prior 1"
    assert bundle["patterns"][-1].evidence_count == 1
    assert set(json.loads(path.read_text())) == {"state", "episodes", "patterns"}
    assert set(json.loads(path.read_text())["state"]) == {
        "current_emotion", "intensity", "emotional_target", "cause_summary",
        "updated_at", "decay_turns_remaining", "recent_emotions", "notes"}


def test_feeling_normalization_and_unreadable_json_defaults_unchanged(tmp_path):
    path = tmp_path / "feeling.json"
    path.write_text(json.dumps(dict(current_emotion=" UNSUPPORTED ", recent_emotions=[" HAPPY ", "bad"] * 10,
        notes=["  long   text  " + "x" * 200] * 10, updated_at=42)))
    item = AssistantStateStore(path)
    state = item.load()
    assert state.current_emotion == "neutral" and state.recent_emotions == ["happy"] * 8
    assert len(state.notes) == 8 and all(len(note) == 180 and note.startswith("long text ") for note in state.notes)
    assert state.updated_at == "42"
    path.write_text("{")
    assert item.load().current_emotion == "neutral"
    assert EmotionMemoryService(path).load_bundle()["state"].current_emotion == "neutral"


@pytest.mark.parametrize("raise_inside", [False, True])
def test_windows_branch_lock_and_unlock_protocol_on_gamma(tmp_path, monkeypatch, raise_inside):
    # This checks the selected Windows API protocol with a fake msvcrt module.
    # Native Windows locking remains a separate runtime verification.
    from types import SimpleNamespace
    calls = []
    fake = SimpleNamespace(LK_LOCK=1, LK_UNLCK=2)
    def locking(fd, operation, length):
        calls.append((operation, length, os.lseek(fd, 0, os.SEEK_CUR), os.fstat(fd).st_size))
    fake.locking = locking
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(state_io, "os", SimpleNamespace(name="nt", fstat=os.fstat))
    path = tmp_path / "state.json"
    def use():
        with state_io.state_file_lock(path):
            if raise_inside: raise ValueError("body")
    if raise_inside:
        with pytest.raises(ValueError, match="body"): use()
    else: use()
    assert calls == [(1, 1, 0, 1), (2, 1, 0, 1)]
    assert path.with_name(path.name + ".lock").read_bytes() == b"0"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("alias_kind", ["absolute", "relative", "absolute_missing_parent", "relative_missing_parent"])
def test_canonical_destination_preserves_seeded_state_through_lexical_alias(
    tmp_path, monkeypatch, kind, alias_kind
):
    path = tmp_path / "state.json"
    owner = store(kind, path)
    update(owner, "ALREADY_ACCEPTED")
    monkeypatch.chdir(tmp_path)
    if alias_kind == "absolute":
        alias = path
    elif alias_kind == "relative":
        alias = Path("state.json")
    elif alias_kind == "absolute_missing_parent":
        alias = tmp_path / "missing-component" / ".." / "state.json"
    else:
        alias = Path("missing-component") / ".." / "state.json"
    assert alias.resolve() == path.resolve()
    aliased = store(kind, alias)
    assert any("ALREADY_ACCEPTED" in note for note in notes(kind, aliased)), (
        "canonical destination must also be used for existence and read"
    )
    update(aliased, "NEXT_ACCEPTED")
    preserved(kind, owner, 2, ["ALREADY_ACCEPTED", "NEXT_ACCEPTED"])
    assert notes(kind, aliased) == notes(kind, owner)
