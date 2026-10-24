import wave
import array
import sys
import math


def pcm_metrics(path):
    with wave.open(str(path), 'rb') as w:
        if w.getcomptype() != 'NONE':
            raise ValueError("compression must be NONE")
        if w.getsampwidth() != 2:
            raise ValueError("sample width must be 2")
        rate = w.getframerate()
        if rate <= 0:
            raise ValueError("framerate must be positive")
        ch = w.getnchannels()
        if ch < 1:
            raise ValueError("channels must be >= 1")
        nframes = w.getnframes()
        if nframes < 1:
            raise ValueError("frames must be >= 1")
        expected = nframes * ch
        sum_sq = 0
        peak = 0
        count = 0
        while True:
            raw = w.readframes(4096)
            if not raw:
                break
            a = array.array('h', raw)
            if sys.byteorder == 'big':
                a.byteswap()
            count += len(a)
            for s in a:
                sum_sq += s * s
                abs_s = -s if s < 0 else s
                if abs_s > peak:
                    peak = abs_s
    if count != expected:
        raise ValueError("sample count mismatch")
    return {
        'duration_seconds': nframes / rate,
        'rms': math.sqrt(sum_sq / count) / 32768,
        'peak': peak / 32768,
        'sample_rate': rate,
    }
