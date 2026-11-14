from __future__ import annotations

import json
import re
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import anyio
from starlette.requests import Request

import gamma.config as config_module
from gamma.config import settings
from gamma.dashboard import main
from gamma.dashboard import auth, deps, pages, routes_stream, routes_voice
from gamma.dashboard.service import DashboardService
from gamma.resources.runtime_registry import ResourceRoutingPolicy, ResourceRoutingRegistry
from gamma.schemas.response import AssistantResponse, VisionAnalysis
from gamma.system.status import SystemStatusService
from gamma.voice.voice_profiles import list_voice_profiles


class _JsonRequest:
    def __init__(self, payload: dict, *, content_type: str = "application/json") -> None:
        self._payload = payload
        self.headers = {"content-type": content_type}

    async def json(self) -> dict:
        return self._payload


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, *args, **kwargs) -> None:
        self.closed = True


class _FakeUploadFile:
    def __init__(self, filename: str, content: bytes, content_type: str) -> None:
        self.filename = filename
        self._content = content
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


def _upload_file(filename: str, content: bytes, content_type: str) -> _FakeUploadFile:
    return _FakeUploadFile(filename, content, content_type)


DASHBOARD_NAV_PATHS = (
    "/dashboard",
    "/dashboard/monitor",
    "/dashboard/shana",
    "/dashboard/people",
    "/dashboard/integrations",
    "/dashboard/coding",
    "/dashboard/system",
)

DASHBOARD_PAGE_ROUTES = (
    (pages.dashboard, "dashboard"),
    (pages.dashboard_shana_page, "shana"),
    (pages.dashboard_people_page, "people"),
    (pages.dashboard_integrations_page, "integrations"),
    (pages.dashboard_coding_page, "coding"),
    (pages.dashboard_system_page, "system"),
)

LEGACY_PAGE_REDIRECTS = (
    "/dashboard/live",
    "/dashboard/talk",
    "/talk",
    "/dashboard/presence",
    "/presence",
    "/dashboard/status",
    "/dashboard/stream",
    "/dashboard/twitch",
    "/dashboard/memory",
    "/dashboard/improvement",
    "/dashboard/settings",
)


def _request_with_headers(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/dashboard/monitor",
            "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()],
            "query_string": b"",
            "server": ("127.0.0.1", 8001),
            "scheme": "http",
        }
    )


class DashboardRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._http_auth_patcher = patch.object(main, "is_authenticated", return_value=True)
        self._ws_auth_patcher = patch.object(auth, "websocket_is_authenticated", return_value=True)
        self.mock_service = Mock()
        self._service_patcher = patch.object(deps, "get_dashboard_service", return_value=self.mock_service)
        self._http_auth_patcher.start()
        self._ws_auth_patcher.start()
        self._service_patcher.start()

    def tearDown(self) -> None:
        self._ws_auth_patcher.stop()
        self._http_auth_patcher.stop()
        self._service_patcher.stop()

    def test_internal_shana_url_uses_loopback_for_wildcard_bind(self) -> None:
        with (
            patch.object(settings, "shana_bind_host", "0.0.0.0"),
            patch.object(settings, "shana_port", 8000),
        ):
            self.assertEqual(settings.shana_internal_base_url, "http://127.0.0.1:8000")

    def test_runtime_status_probes_internal_shana_url(self) -> None:
        service = DashboardService()
        with (
            patch.object(settings, "shana_bind_host", "0.0.0.0"),
            patch.object(settings, "shana_port", 8000),
            patch.object(service._shana, "safe_get", return_value={"status": "ok"}) as probe,
            patch.object(service._process_manager, "find_process", return_value=None),
            patch.object(service, "_machine_status", return_value={}),
        ):
            payload = service.build_runtime_status()

        self.assertTrue(payload["shana"]["api_health"]["ok"])
        probe.assert_called_once_with("/health")

    def test_local_sidecar_connection_refused_has_clear_health_detail(self) -> None:
        error = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with patch("gamma.system.status.urllib.request.urlopen", side_effect=error):
            payload = SystemStatusService()._check_http_health("http://127.0.0.1:9882/health")

        self.assertEqual(payload, {"ok": False, "detail": "sidecar-not-running"})

    def test_favicon_is_served(self) -> None:
        response = pages.favicon()

        self.assertEqual(response.media_type, "image/svg+xml")
        self.assertEqual(Path(response.path), main.STATIC_DIR / "favicon.svg")

    def test_output_pages_are_served_with_shana_api_config(self) -> None:
        with (
            patch.object(settings, "shana_public_scheme", "http"),
            patch.object(settings, "shana_public_host", "192.168.1.50"),
            patch.object(settings, "shana_public_port", 8000),
            patch.object(settings, "dashboard_public_host", "192.168.1.50"),
            patch.object(settings, "dashboard_public_port", 8001),
            patch.object(settings, "dashboard_public_scheme", "http"),
        ):
            dashboard = pages.dashboard()
            talk = pages.dashboard_talk_page()
            monitor_redirect = pages.monitor_page()
            monitor = pages.dashboard_monitor_page()
            performer = pages.performer_redirect()
            overlay = pages.subtitle_overlay_page()

        body = dashboard.body.decode("utf-8")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn('window.GAMMA_SHANA_BASE_URL = "http://192.168.1.50:8000"', body)
        self.assertIn('window.GAMMA_DASHBOARD_BASE_URL = "http://192.168.1.50:8001"', body)
        self.assertIn('window.GAMMA_DASHBOARD_PAGE = "dashboard"', body)
        for path in DASHBOARD_NAV_PATHS:
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', body)
        for path in LEGACY_PAGE_REDIRECTS:
            with self.subTest(retired=path):
                self.assertNotIn(f'href="{path}"', body)
        self.assertIn('rel="icon" href="/static/favicon.svg"', body)
        self.assertNotIn('src="/static/monitor.js', body)
        self.assertEqual(talk.status_code, 307)
        self.assertEqual(talk.headers["location"], "/dashboard/monitor")
        self.assertEqual(monitor_redirect.status_code, 307)
        self.assertEqual(monitor_redirect.headers["location"], "/dashboard/monitor")
        self.assertEqual(monitor.status_code, 200)
        self.assertIn('window.GAMMA_SHANA_BASE_URL = "http://192.168.1.50:8000"', monitor.body.decode("utf-8"))
        self.assertIn('window.GAMMA_DASHBOARD_BASE_URL = "http://192.168.1.50:8001"', monitor.body.decode("utf-8"))
        self.assertIn('window.GAMMA_DASHBOARD_PAGE = "monitor"', monitor.body.decode("utf-8"))
        self.assertIn('id="audioEnableButton"', monitor.body.decode("utf-8"))
        self.assertIn('id="themeSelect"', monitor.body.decode("utf-8"))
        self.assertEqual(performer.headers["location"], "http://192.168.1.50:8000/performer")
        self.assertEqual(overlay.status_code, 200)
        self.assertIn('window.GAMMA_SHANA_BASE_URL = "http://192.168.1.50:8000"', overlay.body.decode("utf-8"))
        self.assertIn('window.GAMMA_DASHBOARD_BASE_URL = "http://192.168.1.50:8001"', overlay.body.decode("utf-8"))

    def test_dashboard_app_public_page_routes_are_registered(self) -> None:
        registered_paths = {getattr(route, "path", "") for route in main.app.routes}
        for path in [*DASHBOARD_NAV_PATHS, *LEGACY_PAGE_REDIRECTS]:
            with self.subTest(path=path):
                self.assertIn(path, registered_paths)

    def test_dashboard_app_public_page_routes_return_html(self) -> None:
        with (
            patch.object(settings, "dashboard_public_scheme", "https"),
            patch.object(settings, "dashboard_public_host", "gamma.neety.me"),
            patch.object(settings, "dashboard_public_port", 443),
        ):
            for route_call, page_name in DASHBOARD_PAGE_ROUTES:
                with self.subTest(page_name=page_name):
                    response = route_call()
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.media_type, "text/html")
                    self.assertIn(f'window.GAMMA_DASHBOARD_PAGE = "{page_name}"', response.body.decode("utf-8"))

    def test_rendered_dashboard_navbar_links_are_relative(self) -> None:
        """Navbar links use same-origin relative paths so they work behind any proxy."""
        body = pages.dashboard().body.decode("utf-8")

        for path in DASHBOARD_NAV_PATHS:
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', body)
                self.assertNotIn(f'href="http://127.0.0.1:8001{path}"', body)
        # Overlay/subtitles is external and is the only link rewritten to an absolute URL.
        self.assertNotIn('href="https://', body)

    def test_rendered_dashboard_uses_public_https_api_base(self) -> None:
        with (
            patch.object(settings, "shana_public_scheme", "https"),
            patch.object(settings, "shana_public_host", "gamma.neety.me"),
            patch.object(settings, "shana_public_port", 443),
        ):
            response = pages.dashboard()

        self.assertIn(
            'window.GAMMA_SHANA_BASE_URL = "https://gamma.neety.me"',
            response.body.decode("utf-8"),
        )

    def test_rendered_dashboard_links_use_public_dashboard_base(self) -> None:
        """Navbar links stay relative; the external subtitle overlay uses the public base."""
        with (
            patch.object(settings, "dashboard_public_scheme", "https"),
            patch.object(settings, "dashboard_public_host", "gamma.neety.me"),
            patch.object(settings, "dashboard_public_port", 443),
        ):
            response = pages.dashboard()
        body = response.body.decode("utf-8")

        for path in DASHBOARD_NAV_PATHS:
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', body)
        self.assertIn('href="https://gamma.neety.me/overlay/subtitles"', body)
        self.assertNotIn('href="/overlay/subtitles"', body)

    def test_dashboard_section_pages_are_served_with_page_config(self) -> None:
        for route_call, page_name in DASHBOARD_PAGE_ROUTES:
            with self.subTest(page_name=page_name):
                body = route_call().body.decode("utf-8")
                self.assertIn(f'window.GAMMA_DASHBOARD_PAGE = "{page_name}"', body)
                self.assertIn('class="app-nav"', body)
                self.assertIn("Stop Output", body)

    def test_retired_dashboard_urls_redirect_to_their_new_pages(self) -> None:
        redirects = {
            pages.dashboard_live_page: "/dashboard/monitor",
            pages.dashboard_talk_page: "/dashboard/monitor",
            pages.talk_page_redirect: "/dashboard/monitor",
            pages.dashboard_presence_page: "/dashboard",
            pages.presence_page_redirect: "/dashboard",
            pages.dashboard_status_page: "/dashboard/system",
            pages.dashboard_stream_page: "/dashboard/integrations",
            pages.dashboard_twitch_page: "/dashboard/integrations",
            pages.dashboard_memory_page: "/dashboard/people",
            pages.dashboard_improvement_page: "/dashboard/coding",
            pages.dashboard_settings_page: "/dashboard/shana",
        }
        self.assertEqual(set(redirects.values()) - {"/dashboard/monitor"}, set(DASHBOARD_NAV_PATHS) - {"/dashboard/monitor"})
        for route_call, location in redirects.items():
            with self.subTest(route=route_call.__name__):
                response = route_call()
                self.assertEqual(response.status_code, 307)
                self.assertEqual(response.headers["location"], location)

        request = _request_with_headers({"host": "gamma.neety.me"})
        request.scope["query_string"] = b"api_base=https%3A%2F%2Fgamma.neety.me"
        self.assertEqual(
            pages.dashboard_live_page(request).headers["location"],
            "/dashboard/monitor?api_base=https%3A%2F%2Fgamma.neety.me",
        )

    def test_talk_routes_redirect_to_monitor(self) -> None:
        response = pages.talk_page_redirect()
        dashboard_response = pages.dashboard_talk_page()

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard/monitor")
        self.assertEqual(dashboard_response.headers["location"], "/dashboard/monitor")
        self.assertFalse((main.STATIC_DIR / "talk.html").exists())
        self.assertFalse((main.STATIC_DIR / "talk.mjs").exists())

    def test_presence_short_path_redirects_to_home(self) -> None:
        response = pages.presence_page_redirect()

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/dashboard")

    def test_monitor_page_embeds_live_voice_panel_partial(self) -> None:
        monitor_body = pages.dashboard_monitor_page().body.decode("utf-8")
        dashboard_body = pages.dashboard().body.decode("utf-8")
        partial = pages.LIVE_VOICE_PANEL.read_text(encoding="utf-8").strip()

        self.assertIn(partial, monitor_body)
        self.assertNotIn("GAMMA_LIVE_VOICE_PANEL", monitor_body)
        # Live voice moved to Talk; the dashboard shell no longer carries a second copy.
        self.assertNotIn('id="liveVoiceButton"', dashboard_body)

    def test_every_dashboard_section_belongs_to_a_page_and_every_page_has_sections(self) -> None:
        html = pages.INDEX_PAGE.read_text(encoding="utf-8")
        tabs = re.findall(r'data-dashboard-tab="([^"]+)"', html)
        page_names = {name for _route, name in DASHBOARD_PAGE_ROUTES}
        used = {tab for value in tabs for tab in value.split()}
        self.assertEqual(used, page_names)

    def test_dashboard_scripts_and_styles_exist(self) -> None:
        html = pages.INDEX_PAGE.read_text(encoding="utf-8")
        assets = re.findall(r'(?:src|href)="/static/([^"?]+)', html)
        self.assertIn("dashboard.css", assets)
        for asset in assets:
            with self.subTest(asset=asset):
                self.assertTrue((pages.STATIC_DIR / asset).is_file())
        for removed in ("render.js", "providers.js", "init.js", "live.js"):
            self.assertNotIn(f"/static/{removed}", html)

    def test_dashboard_pages_render_forwarded_https_public_base_urls(self) -> None:
        request = _request_with_headers(
            {
                "host": "gamma.neety.me",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "gamma.neety.me",
            }
        )
        with (
            patch.object(settings, "shana_public_scheme", "http"),
            patch.object(settings, "shana_public_host", "127.0.0.1"),
            patch.object(settings, "shana_public_port", 8000),
            patch.object(settings, "dashboard_public_scheme", "http"),
            patch.object(settings, "dashboard_public_host", "127.0.0.1"),
            patch.object(settings, "dashboard_public_port", 8001),
        ):
            response = pages.dashboard_monitor_page(request)

        body = response.body.decode("utf-8")
        self.assertIn('window.GAMMA_SHANA_BASE_URL = "https://gamma.neety.me"', body)
        self.assertIn('window.GAMMA_DASHBOARD_BASE_URL = "https://gamma.neety.me"', body)
        self.assertNotIn('window.GAMMA_SHANA_BASE_URL = "http://127.0.0.1:8000"', body)

    def test_systemd_user_lifecycle_controls_shana_unit(self) -> None:
        service = DashboardService()
        completed = Mock(returncode=0, stdout="", stderr="")
        with (
            patch.dict("os.environ", {"GAMMA_LIFECYCLE_MANAGER": "systemd-user"}),
            patch("gamma.dashboard.lifecycle.subprocess.run", return_value=completed) as run,
        ):
            payload = service.stop_shana()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["detail"], "shana-systemd-stop-completed")
        run.assert_called_once_with(
            ["systemctl", "--user", "stop", "gamma-shana.service"],
            cwd=settings.project_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def test_systemd_user_lifecycle_preserves_dashboard_stop(self) -> None:
        service = DashboardService()
        with (
            patch.dict("os.environ", {"GAMMA_LIFECYCLE_MANAGER": "systemd-user"}),
            patch.object(service, "_schedule_stop") as schedule_stop,
        ):
            payload = service.stop_dashboard()

        self.assertFalse(payload["ok"])
        self.assertIn("always-on systemd user service", payload["detail"])
        schedule_stop.assert_not_called()

    def test_dashboard_page_omits_self_stop_control(self) -> None:
        body = pages.dashboard().body.decode("utf-8")

        self.assertNotIn("/api/dashboard/stop", body)
        self.assertNotIn("Stop Dashboard", body)
        self.assertIn("Stop Shana + Workers", body)

    def test_stream_ready_status_reports_filtered_audio_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "filtered.wav"
            audio_path.write_bytes(b"RIFF")
            with (
                patch.object(settings, "stream_filtered_audio_path", str(audio_path)),
                patch("gamma.dashboard.service.ShanaApiClient.safe_get", return_value={"status": "ok"}),
            ):
                payload = DashboardService().stream_ready_status()

        self.assertIn(payload["mode"], {"offline_replay_ready", "twitch_connect_ready", "dry_run_connected", "voice_ready", "not_ready"})
        self.assertTrue(payload["safety_gate"]["enabled"])
        self.assertTrue(payload["filtered_audio"]["exists"])
        self.assertEqual(payload["filtered_audio"]["resolved_path"], str(audio_path))
        self.assertTrue(any(check["id"] == "filtered_audio" and check["status"] == "ok" for check in payload["checks"]))

    def test_performer_output_status_reports_bus_stats(self) -> None:
        service = DashboardService()
        performer_payload = {
            "ok": True,
            "recent_event": {"type": "subtitle_update", "turn_id": "turn-1", "target_policy": "stream_public"},
            "recent_by_target": {
                "stream_public": {"type": "subtitle_update", "turn_id": "turn-1", "target_policy": "stream_public"},
                "dashboard_monitor": {"type": "subtitle_update", "turn_id": "turn-2", "target_policy": "dashboard_monitor"},
            },
            "adapters": {"vtube_studio": {"enabled": True, "configured": True, "connected": False}},
            "stats": {
                "subscriber_count": 2,
                "history_count": 5,
                "target_policies": ["stream_public", "dashboard_monitor", "discord_call"],
                "subscribers_by_target": {"stream_public": 1, "dashboard_monitor": 1},
                "muted_targets": ["stream_public"],
                "subscribers": [
                    {"client_name": "stream_pc_performer", "target_policy": "stream_public", "client_host": "10.78.78.15"},
                    {"client_name": "gaming_pc_monitor", "target_policy": "dashboard_monitor", "client_host": "10.78.78.29"},
                ],
            },
        }
        with patch.object(service._shana, "safe_get", return_value=performer_payload) as probe:
            payload = service.performer_output_status()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stats"]["subscriber_count"], 2)
        self.assertEqual(payload["stats"]["subscribers"][0]["client_host"], "10.78.78.15")
        self.assertEqual(payload["recent_event"]["type"], "subtitle_update")
        self.assertEqual(payload["recent_by_target"]["dashboard_monitor"]["turn_id"], "turn-2")
        self.assertTrue(payload["adapters"]["vtube_studio"]["enabled"])
        self.assertIn("/v1/performer/status", probe.call_args.args[0])

    def test_stream_ready_status_reports_blocking_preflight_issues(self) -> None:
        service = DashboardService()
        with (
            patch.object(settings, "twitch_channel", ""),
            patch.object(settings, "twitch_bot_username", ""),
            patch.object(settings, "twitch_oauth_token", ""),
            patch.object(settings, "twitch_client_id", ""),
            patch.object(settings, "twitch_broadcaster_user_id", ""),
            patch.object(settings, "api_auth_enabled", True),
            patch.object(settings, "api_bearer_token", ""),
            patch("gamma.dashboard.service.ShanaApiClient.safe_get", return_value={"ok": False, "detail": "unreachable"}),
        ):
            payload = service.stream_ready_status()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["mode"], "not_ready")
        self.assertGreaterEqual(payload["blocker_count"], 4)
        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(checks["api"]["status"], "block")
        self.assertEqual(checks["irc_config"]["status"], "block")
        self.assertEqual(checks["eventsub_config"]["status"], "block")
        self.assertEqual(checks["api_auth"]["status"], "block")

    def test_stream_ready_status_reports_twitch_connect_ready(self) -> None:
        service = DashboardService()
        with (
            patch.object(settings, "twitch_channel", "shana"),
            patch.object(settings, "twitch_bot_username", "bot"),
            patch.object(settings, "twitch_oauth_token", "oauth:test"),
            patch.object(settings, "twitch_client_id", "client"),
            patch.object(settings, "twitch_broadcaster_user_id", "broadcaster"),
            patch.object(settings, "twitch_dry_run", True),
            patch.object(service._shana, "safe_get", return_value={"status": "ok"}),
            patch.object(service._process_manager, "module_status", return_value={"process": {"running": False}}),
        ):
            payload = service.stream_ready_status()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "twitch_connect_ready")
        self.assertIn("Start the IRC and EventSub workers", payload["next_step"])

    def test_stream_ready_status_marks_stale_worker_state(self) -> None:
        service = DashboardService()
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        with (
            patch.object(settings, "twitch_channel", "shana"),
            patch.object(settings, "twitch_bot_username", "bot"),
            patch.object(settings, "twitch_oauth_token", "oauth:test"),
            patch.object(settings, "twitch_client_id", "client"),
            patch.object(settings, "twitch_broadcaster_user_id", "broadcaster"),
            patch.object(settings, "twitch_eventsub_enabled", True),
            patch.object(service._shana, "safe_get", return_value={"status": "ok"}),
            patch.object(service._process_manager, "module_status", return_value={"process": {"running": True}}),
            patch("gamma.dashboard.integrations_admin.read_twitch_worker_state", return_value={"connected": True, "updated_at": stale_at, "message_count": 2}),
            patch("gamma.dashboard.integrations_admin.read_twitch_eventsub_state", return_value={"connected": True, "updated_at": stale_at, "notification_count": 1}),
        ):
            payload = service.stream_ready_status()

        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(checks["irc_runtime"]["status"], "warn")
        self.assertTrue(checks["irc_runtime"]["stale"])
        self.assertGreater(checks["irc_runtime"]["evidence"]["age_seconds"], 120)

    def test_stream_ready_status_warns_on_post_errors_and_voice_enabled(self) -> None:
        service = DashboardService()
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with (
            patch.object(settings, "twitch_channel", "shana"),
            patch.object(settings, "twitch_bot_username", "bot"),
            patch.object(settings, "twitch_oauth_token", "oauth:test"),
            patch.object(settings, "twitch_client_id", "client"),
            patch.object(settings, "twitch_broadcaster_user_id", "broadcaster"),
            patch.object(settings, "twitch_eventsub_enabled", True),
            patch.object(settings, "twitch_voice_enabled", True),
            patch("gamma.dashboard.integrations_admin.load_app_file_config", return_value={"twitch_voice_enabled": True}),
            patch.object(service._shana, "safe_get", return_value={"status": "ok"}),
            patch.object(service._process_manager, "module_status", return_value={"process": {"running": True}}),
            patch(
                "gamma.dashboard.integrations_admin.read_twitch_worker_state",
                return_value={"connected": True, "updated_at": updated_at, "message_count": 2, "last_post_error": "api down"},
            ),
            patch(
                "gamma.dashboard.integrations_admin.read_twitch_eventsub_state",
                return_value={"connected": True, "updated_at": updated_at, "notification_count": 1, "last_post_error": "api down"},
            ),
        ):
            payload = service.stream_ready_status()

        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(checks["irc_posting"]["status"], "warn")
        self.assertEqual(checks["eventsub_posting"]["status"], "warn")
        self.assertEqual(checks["voice_disabled_for_validation"]["status"], "warn")

    def test_twitch_status_reports_missing_config(self) -> None:
        service = DashboardService()
        with (
            patch.object(settings, "twitch_channel", ""),
            patch.object(settings, "twitch_bot_username", ""),
            patch.object(settings, "twitch_oauth_token", ""),
            patch.object(settings, "twitch_client_id", ""),
            patch.object(settings, "twitch_broadcaster_user_id", ""),
        ):
            irc = service.twitch_worker_status()
            eventsub = service.twitch_eventsub_status()

        self.assertIn("twitch_channel", irc["missing_config"])
        self.assertIn("twitch_bot_username", irc["missing_config"])
        self.assertIn("twitch_client_id", eventsub["missing_config"])
        self.assertIn("twitch_broadcaster_user_id", eventsub["missing_config"])

    def test_twitch_dry_run_replay_uses_builtin_safe_settings(self) -> None:
        service = DashboardService()
        replay_results = [
            {
                "input_event": {"kind": "chat_message", "metadata": {"input_safety": {"category": "normal"}}},
                "decision": {"decision": "reply"},
            },
            {
                "input_event": {"kind": "chat_message", "metadata": {"input_safety": {"category": "spam_or_scam"}}},
                "decision": {"decision": "acknowledge"},
            },
        ]
        with patch("gamma.dashboard.integrations_admin.replay_jsonl_text", return_value=replay_results) as replay:
            payload = service.run_twitch_dry_run_replay()

        self.assertEqual(payload["scenario"], "dry_run_readiness")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["summary"]["event_count"], 2)
        self.assertEqual(payload["summary"]["decisions_by_kind"]["chat_message"]["reply"], 1)
        self.assertEqual(payload["summary"]["safety_categories"]["spam_or_scam"], 1)
        self.assertEqual(service._latest_twitch_replay_summary["scenario"], "dry_run_readiness")
        _, kwargs = replay.call_args
        self.assertEqual(kwargs["session_id"], "twitch-dry-run-readiness")
        self.assertFalse(kwargs["synthesize_speech"])
        self.assertTrue(kwargs["fast_mode"])

    def test_stream_rehearsal_service_posts_normalized_event_to_stream_api(self) -> None:
        service = DashboardService()
        with patch.object(service, "_post_remote_json", return_value={"decision": {"decision": "reply"}, "output_events": []}) as post:
            service.start_stream_rehearsal(
                {
                    "session_id": "local-session",
                    "synthesize_speech": True,
                    "fast_mode": False,
                    "output_target_policy": "dashboard_monitor",
                }
            )
            payload = service.inject_stream_rehearsal_event(
                {
                    "event_type": "chat",
                    "text": "Shana, react to the boss fight.",
                    "display_name": "Dev",
                    "priority": 7,
                }
            )

        self.assertTrue(payload["ok"])
        post.assert_called_once()
        path, event = post.call_args.args
        self.assertEqual(path, "/v1/stream/events?synthesize_speech=true&fast_mode=false")
        self.assertEqual(post.call_args.kwargs["timeout"], 180)
        self.assertEqual(event["kind"], "chat_message")
        self.assertEqual(event["text"], "Shana, react to the boss fight.")
        self.assertEqual(event["session_id"], "local-session")
        self.assertEqual(event["actor"]["source"], "local_rehearsal")
        self.assertEqual(event["actor"]["display_name"], "Dev")
        self.assertEqual(event["metadata"]["rehearsal"], True)
        self.assertEqual(event["metadata"]["output_target_policy"], "dashboard_monitor")
        self.assertEqual(service.stream_rehearsal_status()["state"]["event_count"], 1)

    def test_stream_rehearsal_service_requires_running_session_and_text(self) -> None:
        service = DashboardService()
        with self.assertRaises(ValueError):
            service.inject_stream_rehearsal_event({"event_type": "chat", "text": "Shana hi"})

        service.start_stream_rehearsal({})
        with self.assertRaises(ValueError):
            service.inject_stream_rehearsal_event({"event_type": "chat", "text": " "})

    def test_monitor_input_route_and_service_use_stream_event_path(self) -> None:
        payload = {"input_mode": "owner_mic", "text": "Shana, can you hear me?", "session_id": "monitor-local-stream"}
        route_result = {"ok": True, "result": {"decision": {"decision": "reply"}}}
        with patch.object(self.mock_service, "submit_monitor_stream_input", return_value=route_result) as method:
            response = anyio.run(routes_stream.dashboard_monitor_input, _JsonRequest(payload))

        self.assertEqual(response, route_result)
        method.assert_called_once_with(payload)

        service = DashboardService()
        controls = {"dry_run": True, "voice_enabled": False, "subtitles_enabled": True, "ambient_chat_enabled": True}
        with (
            patch.object(service, "twitch_runtime_settings", return_value=dict(controls)),
            patch.object(service, "_post_remote_json", return_value={"input_event": {"event_id": "event-1"}, "decision": {"decision": "reply"}}) as post,
        ):
            result = service.submit_monitor_stream_input(payload)

        self.assertTrue(result["ok"])
        path, event = post.call_args.args
        self.assertEqual(path, "/v1/stream/events?synthesize_speech=true&fast_mode=true")
        self.assertEqual(post.call_args.kwargs["timeout"], 180)
        self.assertEqual(event["kind"], "mic_transcript")
        self.assertEqual(event["session_id"], "monitor-local-stream")
        self.assertEqual(event["actor"]["source"], "local_monitor")
        self.assertEqual(event["actor"]["roles"], ["owner", "operator"])
        self.assertEqual(event["metadata"]["output_target_policy"], "dashboard_monitor")
        self.assertEqual(event["metadata"]["twitch_controls"]["ambient_chat_enabled"], True)
        self.assertEqual(event["metadata"]["twitch_controls"]["dry_run"], False)

    def test_presence_service_mode_side_effects(self) -> None:
        service = DashboardService()
        with (
            patch.object(service._shana, "post", return_value={"ok": True, "state": {"mode": "wake"}}) as post,
            patch.object(service, "clear_performer_target", return_value={"ok": True, "cleared": True}) as clear_target,
            patch.object(service, "set_performer_target_mute", return_value={"ok": True, "muted": True}) as mute_target,
        ):
            wake = service.set_presence_mode("wake")

        self.assertEqual(wake["state"]["mode"], "wake")
        self.assertEqual(post.call_args.args[0], "/v1/presence/wake")
        clear_target.assert_called_once_with("stream_public", reason="presence_wake")
        mute_target.assert_any_call("stream_public", muted=True, reason="presence_wake")
        mute_target.assert_any_call("dashboard_monitor", muted=False, reason="presence_wake")

        with (
            patch.object(
                service._shana,
                "post",
                return_value={"ok": True, "state": {"mode": "go_live", "safety": {"dry_run": False}}},
            ) as post,
            patch.object(service, "set_performer_target_mute", return_value={"ok": True, "muted": False}) as mute_target,
        ):
            live = service.set_presence_mode("go_live", confirm_public_output=True)

        self.assertEqual(live["state"]["mode"], "go_live")
        self.assertFalse(live["state"]["safety"]["dry_run"])
        self.assertEqual(post.call_args.args[0], "/v1/presence/mode")
        mute_target.assert_any_call("stream_public", muted=False, reason="presence_go_live")

        with self.assertRaises(ValueError):
            service.set_presence_mode("go_live", confirm_public_output=False)

    def test_tts_catalog_excludes_removed_local_and_stub_providers(self) -> None:
        service = DashboardService()
        with (
            patch.object(service, "_remote_system_status", return_value={
                "app": {},
                "providers": {
                    "llm": {"provider": "local"},
                    "stt": {"provider": "faster-whisper"},
                    "tts": {"provider": "qwen-tts", "profile_id": "", "health": {"ok": True}},
                },
                "recent_artifacts": [],
            }),
            patch.object(service, "build_runtime_status", return_value={"shana": {}, "machine": {}}),
            patch.object(service, "selected_tts_provider", return_value="qwen-tts"),
            patch.object(service, "selected_tts_profile", return_value=None),
            patch.object(service, "performer_output_status", return_value={}),
            patch.object(service, "twitch_worker_status", return_value={}),
            patch.object(service, "twitch_eventsub_status", return_value={}),
            patch.object(service, "stream_ready_status", return_value={}),
        ):
            payload = service.build_status()

        self.assertEqual(payload["providers"]["tts"]["available_providers"], ["qwen-tts", "piper", "openai"])
        self.assertNotIn("local", payload["providers"]["tts"]["available_providers"])
        self.assertNotIn("stub", payload["providers"]["tts"]["available_providers"])

    def test_dashboard_status_uses_lightweight_presence_summary(self) -> None:
        service = DashboardService()
        with (
            patch.object(service, "_remote_system_status", return_value={
                "app": {},
                "providers": {
                    "llm": {"provider": "local"},
                    "stt": {"provider": "faster-whisper"},
                    "tts": {"provider": "qwen-tts", "profile_id": "", "health": {"ok": True}},
                },
                "recent_artifacts": [],
            }),
            patch.object(service, "build_runtime_status", return_value={"shana": {}, "machine": {}}),
            patch.object(service, "selected_tts_provider", return_value="qwen-tts"),
            patch.object(service, "selected_tts_profile", return_value=None),
            patch.object(service, "performer_output_status", return_value={}),
            patch.object(service, "twitch_worker_status", return_value={}),
            patch.object(service, "twitch_eventsub_status", return_value={}),
            patch.object(service, "stream_ready_status", return_value={}),
            patch.object(service, "presence_summary", return_value={"ok": True, "state": {"mode": "wake"}}) as summary,
            patch.object(service, "presence_status", side_effect=AssertionError("full presence status should not run")),
        ):
            payload = service.build_status()

        self.assertEqual(payload["presence"]["state"]["mode"], "wake")
        summary.assert_called_once_with()

    def test_monitor_status_uses_lightweight_status_payload(self) -> None:
        service = DashboardService()
        with (
            patch.object(service, "_remote_system_status", return_value={
                "providers": {
                    "llm": {"provider": "local"},
                    "stt": {"provider": "faster-whisper"},
                    "tts": {"provider": "qwen", "profile_id": "default", "health": {"ok": True}},
                },
            }),
            patch.object(
                service,
                "build_runtime_status",
                return_value={"shana": {"api_health": {"ok": True}}, "machine": {"cpu_percent": 4.0}},
            ),
            patch.object(service, "selected_tts_provider", return_value="qwen-tts"),
            patch.object(service, "selected_tts_profile", return_value="default"),
            patch.object(service, "performer_output_status", return_value={"ok": True, "stats": {}}),
            patch.object(service, "twitch_worker_status", return_value={"process": {"running": False}}),
            patch.object(service, "twitch_eventsub_status", return_value={"process": {"running": False}}),
        ):
            payload = service.build_monitor_status()

        self.assertTrue(payload["ok"])
        self.assertIn("providers", payload)
        self.assertIn("shana", payload)
        self.assertIn("machine", payload)
        self.assertIn("twitch", payload)
        self.assertIn("performer", payload)
        self.assertNotIn("memory", payload)
        self.assertNotIn("timings", payload)
        self.assertNotIn("startup_admission", payload)
        self.assertNotIn("sidecar_allocations", payload)
        self.assertEqual(payload["providers"]["tts"]["provider"], "qwen-tts")

    def test_header_status_avoids_full_system_status(self) -> None:
        service = DashboardService()
        with (
            patch.object(service, "_build_shana_runtime_status", return_value={"api_health": {"ok": True}}),
            patch.object(service._process_manager, "module_status", return_value={"process": {"running": False}}),
            patch.object(service, "presence_summary", return_value={"ok": True, "state": {"mode": "wake"}}),
            patch.object(service, "performer_output_status", return_value={"ok": True, "recent_event": None}),
            patch.object(service, "_remote_system_status", side_effect=AssertionError("full status must not run")),
            patch.object(service, "_machine_status", side_effect=AssertionError("machine status must not run")),
        ):
            payload = service.build_header_status()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["presence"]["state"]["mode"], "wake")
        self.assertTrue(payload["shana"]["api_health"]["ok"])
        self.assertIn("configured", payload["twitch"]["worker"])
        self.assertIn("configured", payload["twitch"]["eventsub"])
        self.assertIn("enabled", payload["twitch"]["eventsub"])

    def test_diagnostics_status_removes_large_duplicate_and_history_payloads(self) -> None:
        service = DashboardService()
        full_status = {
            "dashboard": {"url": "http://dashboard"},
            "providers": {
                "tts": {
                    "available_profiles": [
                        {"id": "voice", "label": "Voice", "provider": "qwen-tts", "values": {"blob": "x" * 20_000}}
                    ],
                    "editor_profile": {"id": "voice", "values": {"speed": 1}},
                }
            },
            "shana": {
                "system_status": {"ok": True, "payload": {"blob": "x" * 100_000}},
                "logs": {"stdout_tail": "o" * 10_000, "stderr_tail": "e" * 10_000},
            },
            "memory_db": {"recent_items": [{"text": "m" * 20_000}]},
            "assistant": {"settings": {"blob": "x" * 20_000}},
            "timings": {"entries": [{"blob": "x" * 20_000}], "summary": {"count": 1}},
            "llm_routing": {"placement_shadow": {"entries": [{"id": index} for index in range(10)]}},
            "startup_admission": {"entries": [{"id": index} for index in range(10)], "summary": {}},
            "sidecar_allocations": {"entries": [{"id": index} for index in range(10)], "summary": {}},
            "twitch": {
                "worker": {"process": {"running": False}, "configured": True, "state": {"blob": "x" * 20_000}},
                "eventsub": {"process": {"running": False}, "configured": True, "enabled": True},
                "stream_ready": {"ok": False, "mode": "stopped", "checks": [{"blob": "x" * 20_000}]},
            },
        }
        with patch.object(service, "build_status", return_value=full_status):
            payload = service.build_diagnostics_status()

        self.assertNotIn("payload", payload["shana"]["system_status"])
        self.assertNotIn("values", payload["providers"]["tts"]["available_profiles"][0])
        self.assertNotIn("llm_routing", payload)
        self.assertEqual(payload["timings"], {"summary": {"count": 1}})
        self.assertLess(len(json.dumps(payload)), 20_000)

    def test_settings_status_retains_controls_without_large_histories(self) -> None:
        service = DashboardService()
        full_status = {
            "providers": {"tts": {"editor_profile": {"id": "voice"}, "available_profiles": []}},
            "shana": {
                "system_status": {"ok": True, "payload": {"blob": "x" * 100_000}},
                "logs": {"stdout_path": "out", "stderr_path": "err", "stdout_tail": "x" * 20_000},
            },
            "memory_db": {"recent_items": [{"blob": "x" * 20_000}]},
            "assistant": {"settings": {"proactive_idle_enabled": False}},
            "timings": {"entries": [{"blob": "x" * 20_000}], "summary": {"count": 1}},
            "llm_routing": {"placement_shadow": {"entries": [{"blob": "x" * 20_000}]}},
            "startup_admission": {"entries": [{"blob": "x" * 20_000}]},
            "sidecar_allocations": {"entries": [{"blob": "x" * 20_000}]},
            "recent_artifacts": [],
            "twitch": {"worker": {}, "eventsub": {}, "stream_ready": {}},
            "performer": {"ok": True, "stats": {}, "recent_by_target": {}},
        }
        with patch.object(service, "build_status", return_value=full_status):
            payload = service.build_settings_status()

        self.assertNotIn("payload", payload["shana"]["system_status"])
        self.assertEqual(payload["shana"]["logs"]["stdout_tail"], "")
        self.assertEqual(payload["assistant"]["settings"]["proactive_idle_enabled"], False)
        self.assertEqual(payload["providers"]["tts"]["editor_profile"]["id"], "voice")
        self.assertEqual(payload["memory_db"]["recent_items"], [])
        self.assertLess(len(json.dumps(payload)), 20_000)

    def test_memory_status_uses_header_contract_plus_bounded_memory_snapshot(self) -> None:
        service = DashboardService()
        with (
            patch.object(service, "build_header_status", return_value={"ok": True, "memory_db": {}}),
            patch.object(
                service._shana,
                "safe_get",
                return_value={"stats": {"count": 1}, "known_people": [{"id": 1}], "recent_items": [{"id": 2}]},
            ) as get,
        ):
            payload = service.build_memory_status()

        self.assertEqual(payload["memory_db"]["known_people"], [{"id": 1}])
        self.assertEqual(payload["memory_db"]["recent_items"], [{"id": 2}])
        get.assert_called_once_with("/v1/memory", params={"limit": 100})

    def test_qwen_presets_are_discovered_and_returned_by_dashboard_status(self) -> None:
        service = DashboardService()
        qwen_profiles = [profile for profile in list_voice_profiles() if profile.provider == "qwen-tts"]
        self.assertGreater(len(qwen_profiles), 1)
        with (
            patch.object(service, "_remote_system_status", return_value={
                "app": {},
                "providers": {
                    "llm": {"provider": "local"},
                    "stt": {"provider": "faster-whisper"},
                    "tts": {"provider": "qwen", "profile_id": "", "health": {"ok": True}},
                },
                "recent_artifacts": [],
            }),
            patch.object(service, "build_runtime_status", return_value={"shana": {}, "machine": {}}),
            patch(
                "gamma.dashboard.voice_admin.load_desired_tts_selection",
                return_value={"tts_provider": "stub", "tts_profile": ""},
            ),
            patch.object(settings, "tts_provider", "qwen"),
            patch.object(service, "performer_output_status", return_value={}),
            patch.object(service, "twitch_worker_status", return_value={}),
            patch.object(service, "twitch_eventsub_status", return_value={}),
            patch.object(service, "stream_ready_status", return_value={}),
        ):
            payload = service.build_status()

        tts = payload["providers"]["tts"]
        self.assertEqual(tts["selected_provider"], "qwen-tts")
        returned_ids = {
            profile["id"]
            for profile in tts["available_profiles"]
            if profile["provider"] == "qwen-tts"
        }
        self.assertEqual(returned_ids, {profile.profile_id for profile in qwen_profiles})
        self.assertGreater(len(returned_ids), 1)

    def test_invalid_optional_voice_preset_file_keeps_default_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir)
            (config_dir / "voices.example.toml").write_text(
                '[profiles.default_qwen]\nlabel = "Default Qwen"\nprovider = "qwen-tts"\n',
                encoding="utf-8",
            )
            (config_dir / "voices.presets.toml").write_text("[profiles.invalid\n", encoding="utf-8")
            with patch.object(config_module, "CONFIG_DIR", config_dir):
                payload = config_module.load_voices_file_config()

        self.assertEqual(payload["profiles"]["default_qwen"]["provider"], "qwen-tts")
        self.assertEqual(len(payload["profiles"]), 1)

    def test_audio_routes_respect_extension_media_type(self) -> None:
        settings.audio_output_dir.mkdir(parents=True, exist_ok=True)
        test_path = settings.audio_output_dir / "dashboard-test.mp3"
        test_path.write_bytes(b"fake audio")
        try:
            response = routes_voice.serve_audio(test_path.name)
            self.assertEqual(response.media_type, "audio/mpeg")

            delete_response = routes_voice.delete_audio(test_path.name)
            self.assertEqual(delete_response["ok"], True)
            self.assertFalse(test_path.exists())
        finally:
            test_path.unlink(missing_ok=True)

    def test_vision_routes(self) -> None:
        analysis = VisionAnalysis(summary="Image summary")
        with patch.object(self.mock_service, "analyze_remote_image", return_value=analysis) as method:
            response = anyio.run(
                routes_voice.dashboard_vision_analyze,
                _upload_file("image.png", b"png", "image/png"),
                "what is this?",
                "photo",
            )
        self.assertEqual(response.summary, "Image summary")
        self.assertEqual(method.call_args.kwargs["vision_mode"], "photo")

        assistant_response = AssistantResponse(spoken_text="Looks good.")
        with patch.object(self.mock_service, "respond_remote_image", return_value=assistant_response) as method:
            response = anyio.run(
                routes_voice.dashboard_vision_respond,
                _upload_file("image.png", b"png", "image/png"),
                "ask gamma",
                "screen",
                "sess-1",
                False,
            )
        self.assertEqual(response.spoken_text, "Looks good.")
        self.assertEqual(method.call_args.kwargs["vision_mode"], "screen")

    def test_live_voice_websocket(self) -> None:
        async def fake_handle(websocket) -> None:
            await websocket.accept()
            await websocket.send_json({"type": "ready"})
            await websocket.close()

        live_voice_session = Mock()
        live_voice_session.handle = AsyncMock(side_effect=fake_handle)
        with patch.object(deps, "get_live_voice_session", return_value=live_voice_session):
            websocket = _FakeWebSocket()
            anyio.run(main.dashboard_live_voice, websocket)
        self.assertEqual(websocket.sent, [{"type": "ready"}])
        self.assertEqual(live_voice_session.handle.await_count, 1)

    def test_router_capability_status_includes_hosted_and_local_scopes(self) -> None:
        service = DashboardService()
        with patch.object(settings, "openai_api_key", "test-key"):
            entries = service._build_router_capability_status(
                {
                    "provider": "local",
                    "health": {"ok": True, "detail": "ready"},
                    "vision_capability": {"ok": False, "detail": "vision-disabled-in-config"},
                    "router_hosted_provider": "openai",
                }
            )
        scopes = {(entry["provider"], entry["scope"]) for entry in entries}
        self.assertIn(("local", "text"), scopes)
        self.assertIn(("local", "vision"), scopes)
        self.assertIn(("openai", "text"), scopes)

    def test_assistant_runtime_settings_reads_config_without_error(self) -> None:
        service = DashboardService()
        payload = service.assistant_runtime_settings()
        self.assertIn("speech_filter_level", payload)
        self.assertIn("speech_filter_llm_temperature", payload)
        self.assertIn("stream_safety_review_timeout_seconds", payload)
        self.assertIn("stream_safety_review_timeout_action", payload)
        self.assertIn("llm_router_profile", payload)
        self.assertIn("assistant_state_enabled", payload)

    def test_dashboard_service_saves_assistant_safety_tuning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "app.local.toml"
            with (
                patch("gamma.dashboard.settings_admin.app_local_config_path", return_value=path),
                patch(
                    "gamma.dashboard.integrations_admin.load_app_file_config",
                    return_value={
                        "speech_filter_heuristic_enabled": False,
                        "speech_filter_llm_enabled": True,
                        "speech_filter_llm_model": "llama3.2:3b",
                        "speech_filter_llm_temperature": 0.15,
                        "stream_safety_review_timeout_seconds": 1.25,
                        "stream_safety_review_timeout_action": "defer",
                    },
                ),
            ):
                service = DashboardService()
                result = service.save_assistant_runtime_settings(
                    {
                        "speech_filter_heuristic_enabled": False,
                        "speech_filter_llm_enabled": True,
                        "speech_filter_llm_model": "llama3.2:3b",
                        "speech_filter_llm_temperature": 0.15,
                        "stream_safety_review_timeout_seconds": 1.25,
                        "stream_safety_review_timeout_action": "defer",
                    }
                )
                saved = path.read_text(encoding="utf-8")

        self.assertTrue(result["ok"])
        self.assertIn("speech_filter_heuristic_enabled = false", saved)
        self.assertIn("speech_filter_llm_enabled = true", saved)
        self.assertIn('speech_filter_llm_model = "llama3.2:3b"', saved)
        self.assertIn("speech_filter_llm_temperature = 0.15", saved)
        self.assertIn("stream_safety_review_timeout_seconds = 1.25", saved)
        self.assertIn('stream_safety_review_timeout_action = "defer"', saved)

    def test_dashboard_file_helpers_read_only_recent_entries(self) -> None:
        service = DashboardService()
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            stdout_log = runtime_dir / "shana.stdout.log"
            stdout_log.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

            timings_log = runtime_dir / "conversation.timings.jsonl"
            for index in range(6):
                payload = {"timing_ms": {"total_ms": index + 1}}
                with timings_log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload) + "\n")

            routes_log = runtime_dir / "llm.routes.jsonl"
            for index in range(8):
                payload = {"status": "ok", "provider": "local", "route_family": f"family-{index}", "duration_ms": index + 0.5}
                with routes_log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload) + "\n")
            supervisor_log = runtime_dir / "logs" / "supervisor.jsonl"
            supervisor_log.parent.mkdir(parents=True, exist_ok=True)
            supervisor_log.write_text(
                json.dumps(
                    {
                        "event": "resource.startup_admission.selected",
                        "provider": "qwen-tts",
                        "kind": "qwen-tts",
                        "selected": {"target_id": "qwen-gpu", "device": "cuda:0"},
                    }
                ) + "\n",
                encoding="utf-8",
            )

            with patch.object(settings, "data_dir", Path(temp_dir)):
                self.assertEqual(service._tail(stdout_log, limit=2), "three\nfour")
                timings = service._recent_timings(limit=3)
                routes = service._recent_llm_routes(limit=4)
                startup_admission = service._recent_startup_admission(limit=4)
                sidecar_allocations = service._recent_sidecar_allocations(limit=4)

        self.assertEqual([entry["timing_ms"]["total_ms"] for entry in timings["entries"]], [4, 5, 6])
        self.assertEqual([entry["route_family"] for entry in routes["entries"]], ["family-4", "family-5", "family-6", "family-7"])
        self.assertEqual(startup_admission["summary"]["selected_count"], 1)
        self.assertEqual(startup_admission["summary"]["target_counts"], {"qwen-gpu": 1})
        self.assertEqual(sidecar_allocations["summary"]["count"], 0)

    def test_recent_startup_admission_extracts_supervisor_decisions(self) -> None:
        service = DashboardService()
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "runtime" / "logs"
            log_dir.mkdir(parents=True)
            supervisor_log = log_dir / "supervisor.jsonl"
            payloads = [
                {"event": "unrelated.event", "message": "ignore"},
                {
                    "timestamp": "2026-06-16T09:00:00Z",
                    "event": "resource.startup_admission.selected",
                    "provider": "qwen-tts",
                    "kind": "qwen-tts",
                    "modality": "speech",
                    "model": "qwen-tts",
                    "workload_id": "qwen-tts:startup",
                    "estimated_vram_mb": 9216,
                    "estimate_source": "configured_fallback",
                    "estimate_ttl_seconds": 300,
                    "minimum_headroom_mb": 1024,
                    "status": "selected",
                    "snapshot_age_seconds": 0.1,
                    "selected": {
                        "target_id": "qwen_tts_gpu_0",
                        "endpoint_ref": "qwen_tts_local",
                        "device": "cuda:0",
                        "gpu_index": 0,
                        "gpu_uuid": "GPU-0",
                        "free_vram_mb": 15000,
                        "projected_headroom_mb": 3700,
                        "reason": "gpu-headroom",
                        "score": 3700.0,
                    },
                    "rejected": {"qwen_tts_gpu_1": "insufficient_vram_headroom"},
                },
                {
                    "timestamp": "2026-06-16T09:00:01Z",
                    "event": "resource.startup_admission.bypassed",
                    "provider": "qwen-tts",
                    "kind": "qwen-tts",
                    "modality": "speech",
                    "model": "qwen-tts",
                    "requested_device": "cuda:0",
                },
                {
                    "timestamp": "2026-06-16T09:00:02Z",
                    "event": "resource.startup_admission.rejected",
                    "provider": "audio-understanding",
                    "kind": "audio-understanding",
                    "modality": "audio",
                    "status": "no_fit",
                    "rejected": {"audio_gpu": "insufficient_vram_headroom"},
                },
            ]
            with supervisor_log.open("w", encoding="utf-8") as handle:
                handle.write("{not json}\n")
                for payload in payloads:
                    handle.write(json.dumps(payload) + "\n")

            with patch.object(settings, "data_dir", Path(temp_dir)):
                admission = service._recent_startup_admission(limit=10)

        self.assertEqual(admission["summary"]["count"], 3)
        self.assertEqual(admission["summary"]["selected_count"], 1)
        self.assertEqual(admission["summary"]["rejected_count"], 1)
        self.assertEqual(admission["summary"]["bypassed_count"], 1)
        self.assertEqual(admission["summary"]["target_counts"], {"qwen_tts_gpu_0": 1})
        selected = admission["entries"][0]
        self.assertEqual(selected["target_id"], "qwen_tts_gpu_0")
        self.assertEqual(selected["device"], "cuda:0")
        self.assertEqual(selected["estimated_vram_mb"], 9216)
        self.assertEqual(selected["estimate_source"], "configured_fallback")
        self.assertEqual(selected["estimate_ttl_seconds"], 300)
        self.assertEqual(selected["rejected"], {"qwen_tts_gpu_1": "insufficient_vram_headroom"})
        self.assertEqual(admission["entries"][1]["requested_device"], "cuda:0")

    def test_recent_sidecar_allocations_extracts_observed_vram(self) -> None:
        service = DashboardService()
        current_timestamp = datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "runtime" / "logs"
            log_dir.mkdir(parents=True)
            supervisor_log = log_dir / "supervisor.jsonl"
            payloads = [
                {"event": "resource.startup_admission.selected", "message": "ignore"},
                {
                    "timestamp": current_timestamp,
                    "event": "resource.sidecar_allocation.observed",
                    "provider": "qwen-tts",
                    "kind": "qwen-tts",
                    "pid": 111,
                    "process_running": True,
                    "estimated_vram_mb": 9216,
                    "observed_vram_mb": 8900,
                    "allocation_delta_mb": -316,
                    "gpu_allocations": [{"gpu_index": 0, "gpu_uuid": "GPU-0", "used_memory_mb": 8900}],
                    "gpu_process_match_count": 1,
                    "snapshot_sampled_at": current_timestamp,
                    "gpu_status": "ok",
                },
                {
                    "timestamp": current_timestamp,
                    "event": "resource.sidecar_allocation.observed",
                    "provider": "audio-understanding",
                    "kind": "audio-understanding",
                    "pid": 222,
                    "process_running": True,
                    "estimated_vram_mb": 1536,
                    "observed_vram_mb": 1018,
                    "allocation_delta_mb": -518,
                    "gpu_allocations": [{"gpu_index": 1, "gpu_uuid": "GPU-1", "used_memory_mb": 1018}],
                    "gpu_process_match_count": 1,
                    "snapshot_sampled_at": current_timestamp,
                    "gpu_status": "ok",
                },
                {
                    "timestamp": "2000-01-01T00:00:00Z",
                    "event": "resource.sidecar_allocation.observed",
                    "provider": "stale-sidecar",
                    "kind": "stale-sidecar",
                    "pid": 333,
                    "process_running": False,
                    "estimated_vram_mb": 2048,
                    "observed_vram_mb": 2048,
                    "allocation_delta_mb": 0,
                    "gpu_allocations": [{"gpu_index": 1, "gpu_uuid": "GPU-1", "used_memory_mb": 2048}],
                    "gpu_process_match_count": 1,
                    "snapshot_sampled_at": "2000-01-01T00:00:00Z",
                    "gpu_status": "ok",
                },
            ]
            with supervisor_log.open("w", encoding="utf-8") as handle:
                handle.write("{not json}\n")
                for payload in payloads:
                    handle.write(json.dumps(payload) + "\n")

            registry = ResourceRoutingRegistry(policy=ResourceRoutingPolicy(sidecar_allocation_ttl_seconds=300), targets=())
            with (
                patch.object(settings, "data_dir", Path(temp_dir)),
                patch("gamma.dashboard.status_views.load_resource_routing_registry", return_value=registry),
            ):
                allocations = service._recent_sidecar_allocations(limit=10)

        self.assertEqual(allocations["summary"]["count"], 3)
        self.assertEqual(allocations["summary"]["current_count"], 2)
        self.assertEqual(allocations["summary"]["fresh_count"], 2)
        self.assertEqual(allocations["summary"]["stale_count"], 1)
        self.assertEqual(allocations["summary"]["ttl_seconds"], 300)
        self.assertEqual(allocations["summary"]["provider_counts"], {"qwen-tts": 1, "audio-understanding": 1, "stale-sidecar": 1})
        self.assertEqual(allocations["summary"]["observed_vram_mb"], 9918)
        self.assertEqual(allocations["summary"]["estimated_vram_mb"], 10752)
        self.assertEqual(allocations["summary"]["allocation_delta_mb"], -834)
        first = allocations["entries"][0]
        self.assertEqual(first["provider"], "qwen-tts")
        self.assertFalse(first["stale"])
        self.assertGreaterEqual(first["age_seconds"], 0)
        self.assertEqual(first["gpu_allocations"], [{"gpu_index": 0, "gpu_uuid": "GPU-0", "used_memory_mb": 8900}])
        self.assertTrue(allocations["entries"][2]["stale"])


if __name__ == "__main__":
    unittest.main()
