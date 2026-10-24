"""Read-only conversation projection. Generated decisions are not delivery evidence."""
import json

from sqlalchemy import select

from ..delivery.digest import fingerprint
from .state_authority import (
    delivery_grants, delivery_payloads, dispatch_attempts, playback_offers, playback_receipts,
)


def history_line(session, entry):
    if entry.role != "assistant":
        status = "" if entry.completion_status == "completed" else f" [response {entry.completion_status}]"
        return f"{entry.role}{status}: {entry.text}"

    # Match the original decision, target and audience, even after cancellation.
    grants = session.execute(select(delivery_grants).where(
        delivery_grants.c.decision_id == entry.exchange_id,
        delivery_grants.c.target == entry.output_target,
        delivery_grants.c.audience == entry.privacy_scope,
    )).mappings().all()
    audio_states = set()
    text_sent = False
    spoken = None
    semantic = fingerprint({"type": "subtitle_update", "effect": entry.text})
    for grant in grants:
        attempts = session.execute(select(dispatch_attempts.c.consumer).where(
            dispatch_attempts.c.grant_id == grant["id"],
            dispatch_attempts.c.exchange_id == entry.exchange_id,
            dispatch_attempts.c.target == entry.output_target,
            dispatch_attempts.c.status == "dispatched",
        )).scalars()
        # The bus queue is not a receiver. Only completed connection sends count.
        text_sent |= any(item.startswith("websocket_text:") and item.endswith(":" + semantic) for item in attempts)
        states = set(session.execute(select(playback_receipts.c.status).select_from(
            playback_receipts.join(playback_offers,
                (playback_receipts.c.lease_id == playback_offers.c.lease_id)
                & (playback_receipts.c.event_id == playback_offers.c.event_id))
        ).where(playback_offers.c.grant_id == grant["id"],
                playback_offers.c.target == entry.output_target)).scalars())
        audio_states.update(states)
        if "completed" in states:
            payload = session.execute(select(delivery_payloads.c.payload).where(
                delivery_payloads.c.grant_id == grant["id"])).scalar_one_or_none()
            if payload:
                try:
                    value = json.loads(payload).get("speech_text")
                    if isinstance(value, str):
                        spoken = value
                except (ValueError, TypeError):
                    pass

    target = f"target={entry.output_target}, audience={entry.privacy_scope}"
    evidence = []
    if text_sent:
        evidence.append(f"assistant [text sent, {target}; reading unconfirmed]: {entry.text}")
    if spoken:
        evidence.append(f"assistant [audio completed, {target}]: {spoken}")
    elif "interrupted" in audio_states:
        evidence.append(f"assistant [audio interrupted, {target}; heard words unknown]")
    elif "started" in audio_states:
        evidence.append(f"assistant [audio started, completion unknown, {target}; heard words unknown]")
    else:
        evidence.append(f"assistant [audio delivery unknown, {target}; no completed playback evidence]")
    if not text_sent and not spoken:
        evidence.append("Generated reply omitted; it is not confirmed communicated.")
    return "\n".join(evidence)
