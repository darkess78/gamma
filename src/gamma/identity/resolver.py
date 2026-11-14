from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from ..config import settings
from ..memory.service import MemoryService
from .profile import SpeakerProfile, TrustLevel, UNKNOWN_PUBLIC

if TYPE_CHECKING:
    from ..schemas.conversation import SpeakerContext


_CONFIG_PATH = settings.project_root / "config" / "users.toml"


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def _load_config() -> dict:
    """``users.toml`` (a tracked template) with ``users.local.toml`` (untracked: real names and ids) on top."""
    config: dict = {}
    for path in (_CONFIG_PATH, _CONFIG_PATH.with_name("users.local.toml")):
        if path.exists():
            config = _merge(config, tomllib.loads(path.read_text(encoding="utf-8")))
    return config


def _parse_game_usernames(raw: str) -> list[str]:
    return [u.strip().lower() for u in raw.split(",") if u.strip()]


class IdentityResolver:
    """Resolves a SpeakerContext to a SpeakerProfile using config/users.toml."""

    def __init__(self) -> None:
        cfg = _load_config()
        self._owner = self._build_owner(cfg.get("owner", {}))
        self._owner_platform_ids_cache: dict[str, str] = cfg.get("owner", {}).get("platform_ids", {})
        self._guests = self._build_guests(cfg.get("guests", {}))
        self._memory = MemoryService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def owner_profile(self) -> SpeakerProfile:
        """The owner. Callers must already know the speaker is the owner
        (a verified owner channel, or a choice the owner made in the dashboard)."""
        return self._owner

    def resolve(self, ctx: "SpeakerContext | None") -> SpeakerProfile:
        """Return the SpeakerProfile for the given context.

        A caller that names no speaker is unknown, never the owner: the owner is
        recognised only by a verified owner channel (``owner_profile``) or a
        stored platform id. Returns UNKNOWN_PUBLIC when the speaker cannot be
        identified.
        """
        if ctx is None:
            return UNKNOWN_PUBLIC

        profile = self._resolve_by_platform(ctx)
        if profile is not None:
            return profile

        # Unrecognized speaker
        return UNKNOWN_PUBLIC

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _resolve_by_platform(self, ctx: "SpeakerContext") -> SpeakerProfile | None:
        source = (ctx.source or "").lower()
        platform_id = (ctx.platform_id or "").strip()

        if not platform_id:
            return None

        stored = self._memory.resolve_person_identity(source, platform_id)
        if stored is not None:
            trust = str(stored.get("trust") or "guest")
            if trust not in {"owner", "trusted", "guest", "public"}:
                trust = "guest"
            return SpeakerProfile(
                name=str(stored.get("name") or "Unknown"),
                trust=trust,  # type: ignore[arg-type]
                notes=str(stored.get("notes") or ""),
                is_owner=trust == "owner",
                resolved_via=source,
            )

        if source == "discord":
            matched = self._match_discord(platform_id)
            if matched is not None:
                return matched
        if source == "game":
            return self._match_game(platform_id)
        if source in {"twitch", "discord"}:
            return self._viewer_profile(source, platform_id)
        return None

    def _viewer_profile(self, source: str, platform_id: str) -> SpeakerProfile | None:
        """Unlinked viewers: the viewer trust store can raise them to guest, never higher."""
        try:
            from ..integrations.twitch.trust import ViewerTrustStore
            from .standing import standing_for
            standing = standing_for(source, platform_id, trust_store=ViewerTrustStore())
        except Exception:
            return None
        if standing.identity_trust != "guest":
            return None
        return SpeakerProfile(name="Viewer", trust="guest", resolved_via=f"{source}_viewer_trust")

    def _match_discord(self, discord_id: str) -> SpeakerProfile | None:
        owner_ids = self._owner_platform_ids_cache
        if discord_id == owner_ids.get("discord", ""):
            profile = SpeakerProfile(
                name=self._owner.name,
                trust=self._owner.trust,
                notes=self._owner.notes,
                is_owner=True,
                resolved_via="discord",
            )
            return profile

        for guest in self._guests:
            if discord_id == guest["platform_ids"].get("discord", ""):
                return SpeakerProfile(
                    name=guest["name"],
                    trust=guest["trust"],
                    notes=guest.get("notes", ""),
                    is_owner=False,
                    resolved_via="discord",
                )
        return None

    def _match_game(self, username: str) -> SpeakerProfile | None:
        lowered = username.lower()
        owner_games = _parse_game_usernames(self._owner_platform_ids_cache.get("game", ""))
        if lowered in owner_games:
            return SpeakerProfile(
                name=self._owner.name,
                trust=self._owner.trust,
                notes=self._owner.notes,
                is_owner=True,
                resolved_via="game",
            )

        for guest in self._guests:
            guest_games = _parse_game_usernames(guest["platform_ids"].get("game", ""))
            if lowered in guest_games:
                return SpeakerProfile(
                    name=guest["name"],
                    trust=guest["trust"],
                    notes=guest.get("notes", ""),
                    is_owner=False,
                    resolved_via="game",
                )
        return None

    # ------------------------------------------------------------------
    # Config parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _build_owner(owner_cfg: dict) -> SpeakerProfile:
        if not owner_cfg:
            return SpeakerProfile(
                name="Owner",
                trust="owner",
                is_owner=True,
                resolved_via="assumed",
            )
        return SpeakerProfile(
            name=owner_cfg.get("name", "Owner"),
            trust="owner",
            notes=owner_cfg.get("notes", ""),
            is_owner=True,
            resolved_via="assumed",
        )

    @staticmethod
    def _build_guests(guests_cfg: dict) -> list[dict]:
        guests: list[dict] = []
        for _key, entry in guests_cfg.items():
            if not isinstance(entry, dict):
                continue
            raw_trust = entry.get("trust", "guest")
            trust: TrustLevel = raw_trust if raw_trust in ("trusted", "guest") else "guest"
            guests.append({
                "name": entry.get("name", "Guest"),
                "trust": trust,
                "notes": entry.get("notes", ""),
                "platform_ids": entry.get("platform_ids", {}),
            })
        return guests
