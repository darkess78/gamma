from gamma.voice.stt import normalize_transcript
from gamma.voice.tts import BaseFileTTSBackend


def test_stt_normalizes_common_shana_name_variants() -> None:
    assert normalize_transcript("Hey Shauna, can you hear me?") == "Hey Shana, can you hear me?"
    assert normalize_transcript("okay china, stop") == "okay Shana, stop"
    assert normalize_transcript("We ordered china for the table.") == "We ordered china for the table."


def test_tts_uses_speech_only_pronunciations_for_names() -> None:
    assert BaseFileTTSBackend._normalize_text("Shana is talking to Neety.") == "Shawna is talking to Nee-tee."
    assert BaseFileTTSBackend._normalize_text("NEETY asked SHANA.") == "Nee-tee asked Shawna."
