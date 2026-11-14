"""Shared Shana API services, created lazily. Route modules call these through the module so tests can replace them in one place."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import WebSocket

from ..config import settings
from ..conversation.service import ConversationService
from ..integrations.discord import DiscordRuntime
from ..integrations.twitch.trust import ViewerTrustStore
from ..memory.service import MemoryService
from ..performer.bus import PerformerEventBus, get_performer_event_bus
from ..performer.vtube_studio import VTubeStudioAdapter, VTubeStudioRunner
from ..presence import PresenceService
from ..proactive import ProactiveScheduler
from ..stream.brain import StreamBrain
from ..stream.output import StreamOutputLogService
from ..stream.replay import StreamReplayService
from ..stream.self_goals import StreamSelfGoalStore
from ..stream.temp_memory import StreamTempMemoryStore
from ..system.lazy_singleton import LazySingleton
from ..system.status import SystemStatusService
from ..voice.live_runtime import LiveTurnRuntime, SubprocessLiveTurnRuntime
from ..voice.roundtrip import VoiceRoundtripService


PERFORMER_STATIC_DIR = Path(__file__).resolve().parents[1] / "performer" / "static"


DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"


DASHBOARD_STATIC_DIR = DASHBOARD_DIR / "static"


SHANA_DEFAULT_IMAGE = settings.project_root / "images" / "shana" / "jacket shana mouth closed eyes open.png"


conversation_service = LazySingleton[ConversationService]()


presence_service = LazySingleton[PresenceService]()


proactive_scheduler = LazySingleton[ProactiveScheduler]()


system_status_service = LazySingleton[SystemStatusService]()


memory_service = LazySingleton[MemoryService]()


viewer_trust_store = LazySingleton[ViewerTrustStore]()


voice_roundtrip_service = LazySingleton[VoiceRoundtripService]()


live_turn_runtime = LazySingleton[LiveTurnRuntime]()


stream_brain = LazySingleton[StreamBrain]()


stream_replay_service = LazySingleton[StreamReplayService]()


stream_output_log_service = LazySingleton[StreamOutputLogService]()


stream_temp_memory_store = LazySingleton[StreamTempMemoryStore]()


stream_self_goal_store = LazySingleton[StreamSelfGoalStore]()


performer_event_bus = LazySingleton[PerformerEventBus]()


vtube_studio_adapter = LazySingleton[VTubeStudioAdapter]()


vtube_studio_runner = LazySingleton[VTubeStudioRunner]()


discord_runtime = LazySingleton[DiscordRuntime]()


def get_conversation_service() -> ConversationService:
    """Get conversation service instance.
    
    Returns:
        ConversationService: Lazy singleton instance.
    """
    return conversation_service.get(ConversationService)


def get_presence_service() -> PresenceService:
    return presence_service.get(lambda: PresenceService(conversation=get_conversation_service(), bus=get_performer_bus()))


def get_proactive_scheduler() -> ProactiveScheduler:
    return proactive_scheduler.get(lambda: ProactiveScheduler(stream_brain=get_stream_brain(), bus=get_performer_bus()))


def get_system_status_service() -> SystemStatusService:
    """Get system status service instance.
    
    Returns:
        SystemStatusService: Lazy singleton instance.
    """
    return system_status_service.get(SystemStatusService)


def get_memory_service() -> MemoryService:
    return memory_service.get(MemoryService)


def get_viewer_trust_store() -> ViewerTrustStore:
    return viewer_trust_store.get(ViewerTrustStore)


def _viewer_trust_payload(record) -> dict:
    return {
        "platform": record.platform,
        "platform_user_id": record.platform_user_id,
        "display_name": record.display_name,
        "trust_level": record.trust_level,
        "notes": record.notes,
        "pronunciation_alias": record.pronunciation_alias,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def get_voice_roundtrip_service() -> VoiceRoundtripService:
    """Get voice roundtrip service instance.
    
    Returns:
        VoiceRoundtripService: Lazy singleton instance.
    """
    return voice_roundtrip_service.get(VoiceRoundtripService)


def get_live_turn_runtime() -> LiveTurnRuntime:
    """Get live turn runtime instance.
    
    Returns:
        LiveTurnRuntime: Lazy singleton instance.
    """
    return live_turn_runtime.get(SubprocessLiveTurnRuntime)


def get_stream_brain() -> StreamBrain:
    """Get stream brain instance.
    
    Returns:
        StreamBrain: Lazy singleton instance.
    """
    return stream_brain.get(StreamBrain)


def get_stream_replay_service() -> StreamReplayService:
    """Get stream replay service instance.
    
    Returns:
        StreamReplayService: Lazy singleton instance.
    """
    return stream_replay_service.get(StreamReplayService)


def get_stream_output_log_service() -> StreamOutputLogService:
    """Get stream output log service instance.
    
    Returns:
        StreamOutputLogService: Lazy singleton instance.
    """
    return stream_output_log_service.get(StreamOutputLogService)


def get_performer_bus() -> PerformerEventBus:
    """Get performer event bus instance.
    
    Returns:
        PerformerEventBus: Lazy singleton instance.
    """
    return performer_event_bus.get(get_performer_event_bus)


def get_vtube_studio_adapter() -> VTubeStudioAdapter:
    """Get VTube Studio adapter instance.
    
    Returns:
        VTubeStudioAdapter: Lazy singleton instance.
    """
    return vtube_studio_adapter.get(VTubeStudioAdapter)


def get_vtube_studio_runner() -> VTubeStudioRunner:
    """Get VTube Studio runner instance.
    
    Returns:
        VTubeStudioRunner: Lazy singleton instance.
    """
    return vtube_studio_runner.get(lambda: VTubeStudioRunner(get_performer_bus(), get_vtube_studio_adapter()))


def get_discord_runtime() -> DiscordRuntime:
    """Get Discord runtime instance.
    
    Returns:
        DiscordRuntime: Lazy singleton instance.
    """
    return discord_runtime.get(DiscordRuntime)


def get_stream_temp_memory_store() -> StreamTempMemoryStore:
    """Get stream temp memory store instance.
    
    Returns:
        StreamTempMemoryStore: Lazy singleton instance.
    """
    return stream_temp_memory_store.get(StreamTempMemoryStore)


def get_stream_self_goal_store() -> StreamSelfGoalStore:
    """Get stream self goal store instance.
    
    Returns:
        StreamSelfGoalStore: Lazy singleton instance.
    """
    return stream_self_goal_store.get(StreamSelfGoalStore)


def _cancel_active_live_turns(*, reason: str) -> dict:
    """Cancel all active live turns with reason.
    
    Args:
        reason: Cancel reason string.
        
    Returns:
        dict: Cancellation results with counts and turn summaries.
    """
    cancel_reason = f"stream_stop:{reason}"
    try:
        cancelled = get_live_turn_runtime().cancel_active_turns(reason=cancel_reason)
    except Exception as exc:
        return {
            "cancel_reason": cancel_reason,
            "cancelled_count": 0,
            "cancelled_turns": [],
            "error": str(exc),
        }
    return {
        "cancel_reason": cancel_reason,
        "cancelled_count": len(cancelled),
        "cancelled_turns": [
            {
                "turn_id": turn.turn_id,
                "status": turn.status,
                "cancel_reason": turn.cancel_reason,
                "cancel_latency_ms": turn.cancel_latency_ms,
            }
            for turn in cancelled
        ],
    }


def _websocket_api_auth_ok(websocket: WebSocket) -> bool:
    """Check WebSocket API authentication.
    
    Args:
        websocket: WebSocket connection.
        
    Returns:
        bool: True if authenticated or API auth disabled.
    """
    if not settings.api_auth_enabled:
        return True
    expected = f"Bearer {settings.api_bearer_token}"
    auth_header = websocket.headers.get("authorization", "")
    if settings.api_bearer_token and secrets.compare_digest(auth_header, expected):
        return True
    token = websocket.query_params.get("token", "")
    return bool(settings.api_bearer_token and secrets.compare_digest(token, settings.api_bearer_token))
