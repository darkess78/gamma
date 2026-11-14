"""A caller that names no speaker is unknown; the owner must be proven."""
from gamma.conversation.service import ConversationService
from gamma.identity.profile import UNKNOWN_PUBLIC
from gamma.identity.resolver import IdentityResolver
from gamma.schemas.conversation import RecipientContext


def test_no_speaker_is_unknown_not_the_owner():
    assert IdentityResolver().resolve(None) is UNKNOWN_PUBLIC


def test_owner_only_by_owner_channel_or_in_process_owner_recipient():
    service = ConversationService.__new__(ConversationService)
    service._identity = IdentityResolver()
    unknown, shared = RecipientContext(), RecipientContext(kind="shared")
    private = RecipientContext(kind="owner_private", owner_authorized=True)
    assert not service._speaker(None, unknown, False).is_owner
    assert not service._speaker(None, shared, False).is_owner
    assert service._speaker(None, private, False).is_owner
    assert service._speaker(None, shared, True).is_owner


def test_live_voice_worker_is_owner_only_with_the_verified_flag():
    from types import SimpleNamespace
    from gamma.run_live_voice_worker import live_speaker
    service = SimpleNamespace(_identity=IdentityResolver())
    assert live_speaker(service, SimpleNamespace(owner_verified=True)).is_owner
    assert not live_speaker(service, SimpleNamespace(owner_verified=False)).is_owner
    assert not live_speaker(service, SimpleNamespace()).is_owner


def test_real_identity_lives_in_the_untracked_local_file(tmp_path, monkeypatch):
    from gamma.identity import resolver
    (tmp_path / "users.toml").write_text('[owner]\nname = "Owner"\ntrust = "owner"\n[owner.platform_ids]\ngame = ""\n')
    (tmp_path / "users.local.toml").write_text('[owner]\nname = "RealName"\n[owner.platform_ids]\ngame = "realhandle"\n')
    monkeypatch.setattr(resolver, "_CONFIG_PATH", tmp_path / "users.toml")
    config = resolver._load_config()
    assert config["owner"]["name"] == "RealName" and config["owner"]["trust"] == "owner"
    assert config["owner"]["platform_ids"]["game"] == "realhandle"
