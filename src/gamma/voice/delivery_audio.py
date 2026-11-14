"""Bounded, pitch-preserving delivery after the tuned TTS output is complete."""
import array
import io
import math
import os
import subprocess
import sys
import wave

PACE_MIN, PACE_MAX = 0.90, 1.25
LOUDNESS_MIN_DB, LOUDNESS_MAX_DB = -5.0, 5.0
_PEAK = 0.95
_MAX_BYTES = 4_000_000


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('voice_delivery_requires_finite_number')
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('voice_delivery_out_of_range')
    return float(value)


def _array(kind, raw):
    values = array.array(kind)
    values.frombytes(raw)
    if sys.byteorder != 'little':
        values.byteswap()
    return values


def _rms(values):
    return math.sqrt(sum(v * v for v in values) / len(values))


def transform_wave(raw: bytes, *, pace=1.0, loudness_db=0.0):
    """Return PCM16 WAV and applied delivery, with no model or pitch changes.

    Choices are absolute offsets from the incoming tuned WAV. Tempo is RMS
    matched before gain. Peak headroom limits uniform gain, without compression.
    """
    pace = _number(pace, PACE_MIN, PACE_MAX)
    loudness_db = _number(loudness_db, LOUDNESS_MIN_DB, LOUDNESS_MAX_DB)
    if not isinstance(raw, bytes) or len(raw) > _MAX_BYTES:
        raise ValueError('voice_delivery_invalid_wave')
    try:
        with wave.open(io.BytesIO(raw), 'rb') as w:
            rate, frames, channels = w.getframerate(), w.getnframes(), w.getnchannels()
            if (channels not in {1, 2} or w.getsampwidth() != 2
                    or not 8000 <= rate <= 48000 or not 0 < frames <= 90 * rate):
                raise ValueError('voice_delivery_invalid_wave')
            pcm = w.readframes(frames)
            if len(pcm) != frames * channels * 2:
                raise ValueError('voice_delivery_truncated_wave')
    except (wave.Error, EOFError) as exc:
        raise ValueError('voice_delivery_invalid_wave') from exc
    metadata = {'pace': pace, 'requested_loudness_db': loudness_db,
                'applied_loudness_db': loudness_db, 'headroom_limited': False}
    if pace == 1.0 and loudness_db == 0.0:
        return raw, metadata
    original = [v / 32768 for v in _array('h', pcm)]
    audio = original
    if pace != 1.0:
        cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-nostats', '-loglevel', 'error',
               '-threads', '1', '-filter_threads', '1', '-i', 'pipe:0',
               '-map_metadata', '-1', '-af', f'atempo={pace}', '-ar', str(rate),
               '-ac', str(channels), '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1']
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        try:
            result = subprocess.run(cmd, input=raw, capture_output=True, check=True,
                                    timeout=20, **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('voice_delivery_tempo_failed') from exc
        if (not result.stdout or len(result.stdout) % 4
                or len(result.stdout) > 90 * rate * channels * 4):
            raise ValueError('voice_delivery_invalid_tempo_output')
        audio = _array('f', result.stdout)
        if not all(math.isfinite(v) for v in audio):
            raise ValueError('voice_delivery_nonfinite_audio')
        if len(audio) % channels or abs(len(audio) / rate / channels - frames / rate / pace) > .15:
            raise ValueError('voice_delivery_unexpected_duration')
        original_rms, rendered_rms = _rms(original), _rms(audio)
        if original_rms and not rendered_rms:
            raise ValueError('voice_delivery_lost_audio')
        scale = original_rms / rendered_rms if rendered_rms else 1.0
        audio = [v * scale for v in audio]
    gain = 10 ** (loudness_db / 20)
    peak = max(abs(v) for v in audio)
    if peak and gain * peak > _PEAK:
        gain = _PEAK / peak
        metadata.update(applied_loudness_db=20 * math.log10(gain), headroom_limited=True)
    encoded = array.array('h', (round(v * gain * 32768) for v in audio))
    if sys.byteorder != 'little':
        encoded.byteswap()
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(encoded.tobytes())
    output = out.getvalue()
    if len(output) > _MAX_BYTES:
        raise ValueError('voice_delivery_output_too_large')
    return output, metadata
