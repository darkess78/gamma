"""One answer to "how much does Shana trust this account?"

There used to be two unconnected systems: person records (owner/trusted/guest/
public, linked to Discord/Twitch accounts in the dashboard) and Twitch's
seven-level viewer trust store. ``standing_for`` combines them:

1. the configured owner ids (``config/users.toml``) -> owner;
2. a person record linked to this account -> that person's trust;
3. the viewer trust store's level for the account;
4. otherwise ``new_viewer``.

The seven-level scale drives chat priority, spam handling and viewer notes;
``identity_trust`` maps it onto the four-level scale used for memory and tools.
Unlinked viewers never get more than ``guest``: tools and private memory need a
person record the owner created.
"""
from __future__ import annotations

from dataclasses import dataclass

VIEWER_LEVELS = ("owner", "trusted", "regular", "normal", "new_viewer", "suspicious", "blocked")
_PERSON_TO_LEVEL = {"owner": "owner", "trusted": "trusted", "guest": "regular", "public": "normal"}


@dataclass(frozen=True)
class Standing:
    platform: str
    platform_id: str
    level: str
    person_name: str | None = None
    linked: bool = False

    @property
    def identity_trust(self) -> str:
        if self.linked and self.level in ("owner", "trusted"):
            return self.level
        if self.level in ("owner", "trusted", "regular"):
            return "guest"
        return "public"


def standing_for(platform: str, platform_id: str | None, *, owner_ids: dict | None = None,
                 memory=None, trust_store=None) -> Standing:
    platform = (platform or "").strip().lower()
    platform_id = (platform_id or "").strip()
    if not platform_id:
        return Standing(platform, "", "new_viewer")
    owner_id = str((owner_ids or {}).get(platform) or "").strip()
    if owner_id and owner_id == platform_id:
        return Standing(platform, platform_id, "owner", linked=True)
    if memory is not None:
        person = memory.resolve_person_identity(platform, platform_id)
        if person:
            level = _PERSON_TO_LEVEL.get(str(person.get("trust") or "guest"), "regular")
            return Standing(platform, platform_id, level, str(person.get("name") or "") or None, linked=True)
    if trust_store is not None:
        level = trust_store.trust_level_for(platform=platform, platform_user_id=platform_id, default="new_viewer")
        if level == "owner":  # only the configured owner id is the owner
            level = "trusted"
        return Standing(platform, platform_id, level if level in VIEWER_LEVELS else "new_viewer")
    return Standing(platform, platform_id, "new_viewer")
