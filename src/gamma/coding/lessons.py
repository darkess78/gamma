"""Editable, evidence-linked lessons from fictional coding practice."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .practice import _identifier


class LessonStore:
    def __init__(self, root):
        self.root = Path(root)
        self._safe_root()

    def _safe_root(self):
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ValueError("invalid lesson storage")

    @contextmanager
    def _lock(self):
        self._safe_root()
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ".lock"
        if path.is_symlink():
            raise ValueError("invalid lesson lock")
        with path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                if path.stat().st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _path(self, identifier):
        _identifier(identifier)
        if not identifier.startswith("lesson-"):
            raise ValueError("invalid lesson id")
        path = self.root / (identifier + ".json")
        if path.is_symlink():
            raise ValueError("invalid lesson record")
        return path

    def _read(self, path):
        if path.is_symlink():
            raise ValueError("invalid lesson record")
        with path.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError("oversized lesson")
        data = json.loads(raw)
        _identifier(data["id"])
        _identifier(data["skill"])
        if data["id"] != "lesson-" + data["skill"] or type(data["active"]) is not bool:
            raise ValueError("invalid lesson")
        if not isinstance(data["text"], str) or not 1 <= len(data["text"]) <= 2000:
            raise ValueError("invalid lesson text")
        if data["authority"] not in {"owner_edited", "observed_practice", "observed_repository"}:
            raise ValueError("invalid lesson authority")
        if type(data["observation_count"]) is not int or data["observation_count"] < 0:
            raise ValueError("invalid observation count")
        runs = data["evidence_runs"]
        if not isinstance(runs, list) or len(runs) > 100:
            raise ValueError("invalid lesson evidence")
        for run in runs:
            _identifier(run)
        return {key: data[key] for key in ("id", "skill", "text", "active", "authority", "observation_count", "evidence_runs", "updated_at")}

    def _write(self, record):
        raw = json.dumps(record, ensure_ascii=False)
        if len(raw.encode()) > 16384:
            raise ValueError("oversized lesson")
        path = self._path(record["id"])
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as stream:
            stream.write(raw)
            temporary = Path(stream.name)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _history_path(self, identifier):
        _identifier(identifier)
        if not identifier.startswith("lesson-"):
            raise ValueError("invalid lesson id")
        path = self.root / (identifier + ".history.jsonl")
        if path.is_symlink():
            raise ValueError("invalid lesson history")
        return path

    def _append_history(self, record, provenance):
        self._safe_root()
        snapshot = {
            "text": record["text"],
            "active": record["active"],
            "authority": record["authority"],
            "evidence_runs": list(record["evidence_runs"]),
            "timestamp": record["updated_at"],
            "provenance": provenance,
        }
        path = self._history_path(record["id"])
        if path.is_symlink():
            raise ValueError("invalid lesson history")
        if path.exists():
            st = path.stat()
            if st.st_size > 327680:
                raise ValueError("oversized history")
            lines = path.read_text(encoding="utf-8").splitlines()
        else:
            lines = []
        lines.append(json.dumps(snapshot, ensure_ascii=False))
        lines = lines[-20:]
        content = "\n".join(lines) + "\n"
        fd = os.open(str(self.root / (".history_tmp_" + record["id"])), os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(self.root / (".history_tmp_" + record["id"])), str(path))
        except Exception:
            try:
                os.unlink(str(self.root / (".history_tmp_" + record["id"])))
            except OSError:
                pass
            raise

    def history(self, identifier):
        self._safe_root()
        path = self._history_path(identifier)
        if path.is_symlink():
            raise ValueError("invalid lesson history")
        if not path.exists():
            return []
        st = path.stat()
        if st.st_size > 327680:
            raise ValueError("oversized history")
        lines = path.read_text(encoding="utf-8").splitlines()
        snapshots = []
        for line in lines[-20:]:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                snapshots.append({
                    "text": data["text"],
                    "active": data["active"],
                    "authority": data["authority"],
                    "evidence_runs": list(data["evidence_runs"]),
                    "timestamp": data["timestamp"],
                    "updated_at": data["timestamp"],
                    "provenance": data.get("provenance", "unknown"),
                })
            except (ValueError, KeyError, TypeError):
                continue
        return list(reversed(snapshots))

    def ingest(self, result):
        if result.get("version") != 1 or result.get("production_verified") is not False:
            raise ValueError("invalid lesson observation")
        _identifier(result["id"])
        with self._lock():
            existing = {lesson["id"] for lesson in self.list()}
            for task in result.get("tasks", [])[:20]:
                if task.get("status") not in {"verified_practice", "needs_checker"}:
                    continue
                skill = task["skill"]
                _identifier(skill)
                identifier = "lesson-" + skill
                path = self._path(identifier)
                if path.exists():
                    record = self._read(path)
                else:
                    if len(existing) >= 50:
                        continue
                    record = {"id": identifier, "skill": skill, "text": "", "active": True,
                              "authority": "observed_practice", "observation_count": 0, "evidence_runs": []}
                    existing.add(identifier)
                if result["id"] in record["evidence_runs"]:
                    continue
                if record["authority"] != "owner_edited":
                    if task["status"] == "needs_checker" or task.get("attempt_count", 0) > 1:
                        record["text"] = "Offline practice required repair or checker help for this skill. Inspect executed failures, preserve existing behavior, and exercise the requested feature before reporting completion."
                    else:
                        record["text"] = "Offline regression and feature checks passed for this skill. Repeat both checks on new work; this narrow practice result does not establish production competence."
                record["evidence_runs"] = [result["id"], *record["evidence_runs"]][:100]
                record["observation_count"] += 1
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._write(record)
                self._append_history(record, "automatic_observation")

    def ingest_autonomous(self, result):
        if not isinstance(result, dict):
            return
        work_id = result.get("id")
        if not isinstance(work_id, str) or not work_id.startswith("work-"):
            return
        suffix = work_id[5:]
        if len(suffix) != 32 or any(c not in "0123456789abcdef" for c in suffix):
            return
        if result.get("status") != "completed":
            return
        tasks = result.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return
        has_verified = False
        for task in tasks:
            if not isinstance(task, dict):
                return
            status = task.get("status")
            if isinstance(status, (list, dict)) or status is None or isinstance(status, (int, bool)):
                return
            if status not in {"verified", "already_satisfied"}:
                return
            if status == "verified":
                has_verified = True
        if not has_verified:
            return
        application = result.get("application")
        if not isinstance(application, dict) or application.get("passed") is not True:
            return
        evidence = application.get("evidence")
        if not isinstance(evidence, dict):
            return
        installed = evidence.get("installed_checks")
        if not isinstance(installed, dict) or installed.get("passed") is not True:
            return
        count = installed.get("count")
        if type(count) is not int or count <= 0:
            return
        activation = evidence.get("activation")
        if not isinstance(activation, dict) or activation.get("passed") is not True:
            return
        skill = None
        for task in tasks:
            candidate = task.get("skill")
            if isinstance(candidate, str):
                try:
                    _identifier(candidate)
                    skill = candidate
                    break
                except (ValueError, TypeError):
                    continue
        if skill is None:
            skill = "repository_verification"
        identifier = "lesson-" + skill
        with self._lock():
            path = self._path(identifier)
            if path.exists():
                record = self._read(path)
            else:
                record = {
                    "id": identifier,
                    "skill": skill,
                    "text": "",
                    "active": True,
                    "authority": "observed_repository",
                    "observation_count": 0,
                    "evidence_runs": [],
                }
            if work_id in record["evidence_runs"]:
                return
            if record["authority"] != "owner_edited":
                record["text"] = "Autonomous repository verification passed for this skill. Preserve existing behavior and repeat protected regression and malformed-input checks on new work."
                record["authority"] = "observed_repository"
            record["evidence_runs"] = [work_id, *record["evidence_runs"]][:100]
            record["observation_count"] += 1
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write(record)
            self._append_history(record, "autonomous_ingest")

    def ingest_project(self, result):
        """Record narrow repository evidence without implying weight updates."""
        if result.get('status') != 'completed' or result.get('result', {}).get('passed') is not True:
            return
        tasks = result['result'].get('tasks', [])
        if not tasks or any(t.get('status') != 'completed' for t in tasks): return
        if any(not any(e.get('stage') == 'applying' and e.get('result', {}).get('passed') is True for e in t.get('evidence', [])) for t in tasks): return
        identifier = 'lesson-discord_status'
        with self._lock():
            path = self._path(identifier)
            record = self._read(path) if path.exists() else {
                'id':identifier, 'skill':'discord_status', 'active':True, 'authority':'observed_repository',
                'observation_count':0, 'evidence_runs':[], 'text':''}
            if result['id'] in record['evidence_runs']: return
            if record['authority'] != 'owner_edited':
                record['text'] = 'For Discord status, confirm process liveness and a fresh finite heartbeat before claiming connection. Require exact boolean flags, sanitize voice states, and run protected regression and withheld malformed-input checks on the installed source.'
                record['authority'] = 'observed_repository'
            record['evidence_runs'] = [result['id'], *record['evidence_runs']][:100]
            record['observation_count'] += 1
            record['updated_at'] = datetime.now(timezone.utc).isoformat()
            self._write(record)
            self._append_history(record, "automatic_observation")

    def list(self):
        self._safe_root()
        if not self.root.is_dir():
            return []
        records = []
        for count, path in enumerate(self.root.iterdir()):
            if count >= 200 or len(records) >= 50:
                break
            if path.suffix != ".json" or path.is_symlink():
                continue
            try:
                records.append(self._read(path))
            except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                continue
        return sorted(records, key=lambda r: r["id"])

    def edit(self, identifier, *, text, active):
        path = self._path(identifier)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000 or type(active) is not bool:
            raise ValueError("invalid lesson edit")
        with self._lock():
            record = self._read(path)
            record.update(text=text.strip(), active=active, authority="owner_edited",
                          updated_at=datetime.now(timezone.utc).isoformat())
            self._write(record)
            self._append_history(record, "owner_edit")
            return record
