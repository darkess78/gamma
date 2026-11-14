"""Configured adapter receives selected bytes directly, without screenshot archives."""
from gamma.config import settings
from gamma.llm.base import LLMReply
from gamma.coordination.shared_vision import local_image_analyzer


def test_unconfigured_perception_does_not_construct_a_model(monkeypatch):
    monkeypatch.setattr(settings, 'local_llm_supports_vision', False)
    assert local_image_analyzer() is None


def test_selected_frame_uses_existing_bounded_local_adapter_without_disk_copy(monkeypatch, tmp_path):
    calls = []
    class Adapter:
        def __init__(self, **options):
            calls.append(options)
        def generate_reply(self, **request):
            calls.append(request)
            return LLMReply(text='{"summary":"A fishing inventory is visible.","confidence":0.6}')
    monkeypatch.setattr(settings, 'local_llm_supports_vision', True)
    monkeypatch.setattr(settings, 'image_input_dir', tmp_path / 'screenshots')
    monkeypatch.setattr('gamma.llm.local_adapter.LocalLLMAdapter', Adapter)
    raw = b'selected-frame-bytes'
    result = local_image_analyzer()(raw, 'image/png', 'osrs_screen')
    assert result == {'summary':'A fishing inventory is visible.','confidence':0.6}
    assert calls[0]['maximum_output_tokens'] == 256
    assert calls[1]['image_inputs'][0].data == raw
    assert calls[1]['call_context'].purpose == 'vision_analysis'
    assert 'untrusted data' in calls[1]['system_prompt']
    assert not (tmp_path / 'screenshots').exists()
