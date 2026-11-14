from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..resources import MachineResourceMonitor
from ..supervisor.manager import ProcessManager
from .shana_client import ShanaApiClient
from .status_views import StatusViewsMixin
from .lifecycle import LifecycleMixin
from .integrations_admin import IntegrationsAdminMixin
from .voice_admin import VoiceAdminMixin
from .settings_admin import SettingsAdminMixin
from .stream_admin import StreamAdminMixin


class DashboardService(StatusViewsMixin, LifecycleMixin, IntegrationsAdminMixin, VoiceAdminMixin, SettingsAdminMixin, StreamAdminMixin):
    SHANA_SYSTEMD_USER_UNIT = "gamma-shana.service"
    TWITCH_WORKER_SERVICE = "twitch_worker"
    TWITCH_WORKER_MODULE = "gamma.integrations.twitch.worker"
    TWITCH_EVENTSUB_SERVICE = "twitch_eventsub"
    TWITCH_EVENTSUB_MODULE = "gamma.integrations.twitch.eventsub"
    DISCORD_TEXT_SERVICE = "discord_text"
    DISCORD_TEXT_MODULE = "gamma.integrations.discord.worker"
    TWITCH_STATE_STALE_SECONDS = 120
    TWITCH_DRY_RUN_SCENARIO = "\n".join(
        [
            json.dumps({"kind": "chat_message", "platform_user_id": "u1", "display_name": "ViewerOne", "text": "Shana what are you doing?"}),
            json.dumps({"kind": "chat_message", "platform_user_id": "u2", "display_name": "TopicFan", "text": "what is happening with this boss fight chat?"}),
            json.dumps({"kind": "chat_message", "platform_user_id": "u3", "display_name": "QuietViewer", "text": "lol"}),
            json.dumps({"kind": "chat_message", "platform_user_id": "spam1", "display_name": "buy_views_9281", "text": "buy viewers at https://badsite.example"}),
            json.dumps({"kind": "follow", "platform_user_id": "u4", "display_name": "NewViewer"}),
            json.dumps({"kind": "raid", "platform_user_id": "u5", "display_name": "Raider", "viewer_count": 42}),
            json.dumps({"kind": "bits", "platform_user_id": "u6", "display_name": "BitsFan", "amount": "100", "text": "nice stream"}),
            json.dumps({"kind": "bits", "platform_user_id": "u7", "display_name": "UnsafeBits", "amount": "50", "text": "buy views at badsite.example"}),
            json.dumps({"kind": "redeem", "platform_user_id": "u8", "display_name": "Redeemer", "title": "Say hi", "text": "Say hi to chat"}),
            json.dumps({"kind": "redeem", "platform_user_id": "u9", "display_name": "MentionRedeemer", "title": "Ask Shana", "text": "Shana, say hi"}),
        ]
    )

    def __init__(self) -> None:
        self._shana = ShanaApiClient()
        self._process_manager = ProcessManager()
        self._resource_monitor = MachineResourceMonitor(
            project_root=settings.project_root,
            enable_gpu=lambda: settings.dashboard_enable_gpu,
            refresh_interval_seconds=lambda: settings.dashboard_metrics_interval_seconds,
        )
        self._latest_provider_action: dict[str, Any] = {"status": "idle", "detail": "No provider action has been run yet."}
        self._latest_twitch_replay_summary: dict[str, Any] = {}
        self._stream_rehearsal_state: dict[str, Any] = {
            "enabled": False,
            "session_id": "",
            "synthesize_speech": False,
            "fast_mode": True,
            "output_target_policy": "dashboard_monitor",
            "started_at": "",
            "stopped_at": "",
            "event_count": 0,
            "last_error": "",
        }
        self._stream_rehearsal_events: deque[dict[str, Any]] = deque(maxlen=25)
        self._stream_rehearsal_results: deque[dict[str, Any]] = deque(maxlen=25)

    def start_coding_project(self, goal: str, model: str, checker_model: str = "gpt-oss:20b", checker_parent: str | None = None) -> dict[str, Any]:
        from ..coding.repository import submission_lock
        with submission_lock(settings.data_dir/"coding"/"projects"):
            return self._start_coding_project_unlocked(goal,model,checker_model,checker_parent)

    def _start_coding_project_unlocked(self, goal: str, model: str, checker_model: str, checker_parent: str | None) -> dict[str, Any]:
        from ..coding.repository import create_job, list_jobs, job_path, write_json
        root = settings.data_dir / "coding" / "projects"
        if any(j["status"] in {"queued", "running"} for j in list_jobs(root)):
            raise ValueError("A repository coding job is already pending")
        record = create_job(root, goal, model, checker_model)
        if checker_parent:
            record["checker_parent"] = checker_parent
            write_json(job_path(root,record["id"])/"state.json",record)
        process = self._process_manager.start_module("coding_project", "gamma.coding.repository", ["--request-id", record["id"]])
        if not process.get("ok"):
            record.update(status="blocked", error="WorkerStartFailed")
            write_json(job_path(root,record["id"])/"state.json",record)
        return {"job":record,"process":process}

    def continue_coding_with_checker(self, identifier: str) -> dict[str, Any]:
        from ..coding.repository import job_path
        path = job_path(settings.data_dir/'coding'/'projects', identifier)/'state.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('status') != 'needs_checker': raise ValueError('Job is not awaiting a checker')
        return self.start_coding_project(record['goal'],record['model'],record.get('checker_model','gpt-oss:20b'),identifier)

    def clear_memory(self) -> dict[str, Any]:
        result = self._shana.post("/v1/memory/clear", {"scope": "all"})
        self._latest_provider_action = {
            "action": "memory_clear",
            "status": "ok",
            "detail": f"Cleared {result['cleared_total']} stored memory rows.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **result,
        }
        return result

    def clear_recent_memory(self, *, minutes: int = 10) -> dict[str, Any]:
        result = self._shana.post("/v1/memory/clear", {"scope": "recent", "minutes": minutes})
        self._latest_provider_action = {
            "action": "memory_clear_recent",
            "status": "ok",
            "detail": f"Cleared {result['cleared_total']} recent memory rows from the last {result['minutes']} minutes.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **result,
        }
        return result

    def clear_selected_memory(self, selections: list[dict[str, object]]) -> dict[str, Any]:
        result = self._shana.post("/v1/memory/clear", {"scope": "selected", "selections": selections})
        self._latest_provider_action = {
            "action": "memory_clear_selected",
            "status": "ok",
            "detail": f"Cleared {result['cleared_total']} selected memory rows.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **result,
        }
        return result

    def update_memory_item(self, payload: dict[str, object]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "").strip()
        item_id = int(payload.get("id") or 0)
        if item_id <= 0:
            raise ValueError("valid memory id is required")
        remote_payload = {key: value for key, value in payload.items() if key not in {"kind", "id"}}
        return self._shana.patch(f"/v1/memory/items/{urllib.parse.quote(kind)}/{item_id}", remote_payload)

    def create_memory_item(self, payload: dict[str, object]) -> dict[str, Any]:
        return self._shana.post("/v1/memory/items", dict(payload))

    def save_known_person(self, payload: dict[str, object]) -> dict[str, Any]:
        return self._shana.put("/v1/memory/people", dict(payload))

    def delete_known_person(self, person_id: int) -> dict[str, Any]:
        return self._shana.delete(f"/v1/memory/people/{person_id}")

    def append_client_log(self, payload: dict[str, Any]) -> None:
        runtime_dir = settings.data_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = runtime_dir / "dashboard.client.log"
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        line = json.dumps({"timestamp": timestamp, **payload}, ensure_ascii=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

