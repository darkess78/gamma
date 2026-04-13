from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from .profile import SpeakerProfile, TrustLevel, UNKNOWN_PUBLIC

if TYPE_CHECKING:
    from ..schemas.conversation import SpeakerContext


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "users.toml"


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    return tomllib.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


def _parse_game_usernames(raw: str) -> list[str]:
    return [u.strip().lower() for u in raw.split(",") if u.strip()]


class IdentityResolver:
    """Resolves a SpeakerContext to a SpeakerProfile using config/users.toml."""

    def __init__(self) -> None:
        cfg = _load_config()
        self._owner = self._build_owner(cfg.get("owner", {}))
        self._owner_platform_ids_cache: dict[str, str] = cfg.get("owner", {}).get("platform_ids", {})
        self._guests = self._build_guests(cfg.get("guests", {}))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, ctx: "SpeakerContext | None") -> SpeakerProfile:
        """Return the SpeakerProfile for the given context.

        Falls back to the owner profile when ctx is None (local solo use).
        Returns UNKNOWN_PUBLIC when the speaker cannot be identified.
        """
        if ctx is None:
            # No context provided — local solo use, assume owner.
            return self._owner

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

        if source == "discord":
            return self._match_discord(platform_id)
        if source == "game":
            return self._match_game(platform_id)

        return None

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
