from gamma.voice.latency_trace import mark


def test_trace_requires_opt_in(monkeypatch, capsys):
    monkeypatch.delenv("SHANA_VOICE_LATENCY_TRACE", raising=False)
    mark("stt_final")
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("SHANA_VOICE_LATENCY_TRACE", "1")
    mark("stt_final")
    label, pid, timestamp, stage = capsys.readouterr().err.split()
    assert (label, stage) == ("voice_latency", "stt_final")
    assert int(pid) > 0 and int(timestamp) > 0
