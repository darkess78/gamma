from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


class SpeakerContext(BaseModel):
    """Identity hint provided by the caller.

    source     - where the message came from: "local", "discord", "game"
    platform_id - the platform-specific identifier (Discord user ID, game username, etc.)
    voice_id   - reserved for future voice-biometric fingerprint matching
    """

    source: str
    platform_id: str | None = None
    voice_id: str | None = None


class ConversationRequest(BaseModel):
    user_text: str
    session_id: str | None = None
    synthesize_speech: bool = False
    speaker: SpeakerContext | None = None
    fast_mode: bool = False
    evaluation_mode: bool = False


@dataclass(frozen=True, slots=True)
class RecipientContext:
    """Internal-only recipient context. Not a field on ConversationRequest.

    Trusted ingress must establish authorization before setting the owner flag.
    Freezing and validating the value do not authenticate its caller.

    kind           - "unknown", "owner_private", or "shared"
    target_policy  - None, "dashboard_monitor", "stream_public", or "discord_call"
    owner_authorized - exact bool; True only for owner_private
    """

    kind: str = "unknown"
    target_policy: str | None = None
    owner_authorized: bool = False

    def __post_init__(self) -> None:
        if type(self.kind) is not str:
            raise TypeError("kind must be str")
        allowed_kinds = ("unknown", "owner_private", "shared")
        if self.kind not in allowed_kinds:
            raise ValueError(f"kind must be one of {allowed_kinds}, got {self.kind!r}")
        if self.target_policy is not None:
            if type(self.target_policy) is not str:
                raise TypeError("target_policy must be str or None")
            allowed_targets = (None, "dashboard_monitor", "stream_public", "discord_call")
            if self.target_policy not in allowed_targets:
                raise ValueError(
                    f"target_policy must be one of {allowed_targets}, got {self.target_policy!r}"
                )
        if type(self.owner_authorized) is not bool:
            raise TypeError("owner_authorized must be bool")
        if self.kind == "owner_private":
            if not self.owner_authorized:
                raise ValueError("owner_private requires owner_authorized to be True")
            if self.target_policy not in (None, "dashboard_monitor"):
                raise ValueError(
                    "owner_private target_policy must be None or dashboard_monitor, "
                    f"got {self.target_policy!r}"
                )
        else:
            if self.owner_authorized:
                raise ValueError(f"{self.kind} kind requires owner_authorized to be False")

    @property
    def allows_private_memory(self) -> bool:
        return self.kind == "owner_private" and self.owner_authorized

    @property
    def privacy_scope(self) -> str:
        if self.kind == "owner_private":
            return "local_private"
        if self.kind == "shared":
            return "public"
        return "local_generic"
