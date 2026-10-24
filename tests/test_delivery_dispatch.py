"""Durable dispatch acceptance with real SQLite transactions and process exit."""
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, update

from state_fixtures import commit_response
from gamma.delivery.authority import DeliveryAuthority, DeliveryDenied
from gamma.memory.state_authority import dispatch_attempts, delivery_grants, generations, publications
from gamma.schemas.response import AssistantResponse


def fixture(url):
    authority = DeliveryAuthority(database_url=url)
    response = AssistantResponse(spoken_text="Approved fixture", internal_summary="private fixture")
    commit_response(authority, response)
    context = dict(target="dashboard_monitor", audience="private", policy="fixture-policy")
    grant = authority.issue(response, **context)
    return authority, response, context, grant


def test_durable_material_excludes_private_state_and_preparation_is_not_dispatch(tmp_path):
    authority, response, context, grant = fixture(f"sqlite:///{tmp_path / 'state.db'}")
    authority.dispatch(grant, response, **context)
    saved, saved_context = authority.response_for_grant(grant)
    assert saved.display_text == response.display_text
    assert saved.internal_summary is None
    assert saved_context == context
    assert saved.tts_metadata["delivery_state"] == "prepared"
    assert response.tts_metadata["delivery_state"] == "prepared"
    with authority._session() as session:
        assert session.execute(select(dispatch_attempts)).first() is None
    claim = authority.claim_dispatch(grant, saved, consumer="monitor:text", **saved_context)
    authority.finish_dispatch(claim, succeeded=True)
    with authority._session() as session:
        assert session.execute(select(dispatch_attempts.c.status)).scalar_one() == "dispatched"
        assert session.execute(select(publications.c.spoken)).scalar_one() == 0
    with pytest.raises(DeliveryDenied, match="already_claimed"):
        authority.claim_dispatch(grant, response, consumer="monitor:text", **context)


def test_atomic_consumer_claims_and_failed_adapter_cannot_retry(tmp_path):
    authority, response, context, grant = fixture(f"sqlite:///{tmp_path / 'state.db'}")
    authority.dispatch(grant, response, **context)
    def claim():
        try:
            return authority.claim_dispatch(grant, response, consumer="monitor:audio", **context)
        except DeliveryDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    winner, = [result for result in results if result]
    authority.finish_dispatch(winner, succeeded=False)
    assert claim() is None
    authority.claim_dispatch(grant, response, consumer="stream:audio", **context)


def crash_child(url, evidence, stage):
    authority, response, context, grant = fixture(url)
    if stage != "committed":
        authority.dispatch(grant, response, **context)
    if stage == "adapter":
        authority.claim_dispatch(grant, response, consumer="fixture:text", **context)
        with open(evidence, "a") as output:
            output.write("one external output\n")
    # Simulate sudden death, skipping finalizers and dispatch completion.
    os._exit(0)


@pytest.mark.parametrize("stage", ["committed", "prepared", "adapter"])
def test_crash_recovery_never_replays_old_output(tmp_path, stage):
    url = f"sqlite:///{tmp_path / 'state.db'}"
    evidence = tmp_path / "adapter.txt"
    process = multiprocessing.get_context("spawn").Process(target=crash_child, args=(url, str(evidence), stage))
    process.start()
    process.join(15)
    assert process.exitcode == 0
    authority = DeliveryAuthority(database_url=url)
    with authority._session() as session:
        generation = session.execute(select(generations.c.id)).scalar_one()
        session.execute(update(generations).values(lease_until=0))
        authority._state.recover(session, generation)
        session.commit()
    # Repeated recovery is safe and does not create a delivery executor/replay.
    with authority._session() as session:
        assert authority._state.recover(session, generation) == []
        attempts = session.execute(select(dispatch_attempts)).mappings().all()
        if stage == "adapter":
            assert len(attempts) == 1 and attempts[0]["status"] == "uncertain"
            assert evidence.read_text().splitlines() == ["one external output"]
        else:
            assert attempts == [] and not evidence.exists()
        assert session.execute(select(publications).where(publications.c.status == "ready")).first() is None
        grant = session.execute(select(delivery_grants.c.id)).scalar_one()
    # A caller attempting to reload a prepared output also receives a denial.
    with pytest.raises(DeliveryDenied):
        authority.response_for_grant(grant)


def test_retention_drops_approved_payload_but_keeps_no_retry_receipt(tmp_path):
    from gamma.delivery.maintenance import DeliveryMaintenance
    from gamma.memory.state_authority import delivery_payloads
    import time
    authority, response, context, grant = fixture(f"sqlite:///{tmp_path / 'state.db'}")
    authority.dispatch(grant, response, **context)
    claim = authority.claim_dispatch(grant, response, consumer="fixture:text", **context)
    authority.finish_dispatch(claim, succeeded=True)
    maintenance = DeliveryMaintenance(database_url=str(authority.engine.url))
    try:
        result = maintenance.sweep(now=time.time() + 200000)
        assert result["grants"] == 1
        with authority._session() as session:
            assert session.execute(select(delivery_payloads)).first() is None
            assert session.execute(select(dispatch_attempts.c.id)).scalar_one() == claim
    finally:
        maintenance.close()
