"""Snapshot keeps experiences and no-replay receipts, never connection/audio leases."""
from gamma.coordination.core import Core
from gamma.coordination.interaction import PrivateInteraction
from gamma.coordination.store import Store
from gamma.coordination.transfer import export_snapshot, restore_snapshot
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse


def make(root):
    store=Store(root)
    return Core(store,draft=lambda *_:AssistantResponse(display_text='A private observation.',delivery_mode='text_only'),
        runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}))


def test_copied_state_keeps_current_turns_and_never_replays_delivery(tmp_path):
    core=make(tmp_path/'state');transport=PrivateInteraction(core)
    transport.connect('desktop')
    obs={'audience':'owner_private','request_id':'once','consumer_id':'desktop',
        'session_id':'private','channel':'desktop_text','speaker':'owner','text':'A fictional experience.'}
    result=transport.observe(obs)
    assert result['output_id']
    original=core.store.read('turn','once')
    transport.close();core.close()
    manifest=export_snapshot(tmp_path/'state',tmp_path/'bundle')
    assert manifest['operational_delivery_invalidated']
    assert all(not name.endswith('.wav') for name in manifest['files'])
    restore_snapshot(tmp_path/'bundle',tmp_path/'restored')
    restored=make(tmp_path/'restored');replacement=PrivateInteraction(restored)
    try:
        assert restored.store.read('turn','once')==original
        assert replacement.status()['voice_enabled'] is False
        replacement.connect('desktop')
        receipt=replacement.observe(obs)
        assert receipt=={'ok':True,'status':'cancelled','reason':'snapshot_no_replay','output_id':None}
        assert replacement.next_output('desktop')['output'] is None
    finally:
        replacement.close();restored.close()
