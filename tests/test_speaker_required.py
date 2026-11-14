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
