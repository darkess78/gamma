import importlib.util
import io
from pathlib import Path
import unittest
import wave

spec = importlib.util.spec_from_file_location('audio_buffer', Path(__file__).parents[1] / 'src/gamma/integrations/discord/audio_buffer.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BufferTest(unittest.TestCase):
    def create(self, **kwargs):
        return module.PCMUtteranceBuffer(sample_rate=48000, channels=2, sample_width=2, **kwargs)
    def check_wav(self, result, user_id, pcm):
        self.assertEqual(result['user_id'], user_id)
        with wave.open(io.BytesIO(result['wav']), 'rb') as stream:
            self.assertEqual((stream.getframerate(), stream.getnchannels(), stream.getsampwidth()), (48000, 2, 2))
            self.assertEqual(stream.readframes(stream.getnframes()), pcm)
    def test_independent_speakers_and_explicit_flush(self):
        buffer = self.create()
        a, b = b'\x01\x00\x02\x00' * 100, b'\x03\x00\x04\x00' * 80
        self.assertEqual(buffer.push('1', a, now=0), [])
        self.assertEqual(buffer.push('2', b, now=0.1), [])
        self.check_wav(buffer.flush('1'), '1', a)
        self.check_wav(buffer.flush('2'), '2', b)
        self.assertIsNone(buffer.flush('1'))
    def test_silence_timeout(self):
        buffer = self.create(silence_seconds=0.6)
        pcm = b'\x01\x00\x02\x00' * 100
        buffer.push('1', pcm, now=0)
        self.assertEqual(buffer.expire(now=0.59), [])
        items = buffer.expire(now=0.61)
        self.assertEqual(len(items), 1)
        self.check_wav(items[0], '1', pcm)
    def test_split_large_packets_without_loss_or_unbounded_buffers(self):
        buffer = self.create(max_seconds=0.01)
        # 480 stereo frames per utterance; packet crosses multiple limits.
        pcm = b'\x11\x00\x22\x00' * 1300
        emitted = buffer.push('1', pcm, now=0)
        tail = buffer.flush('1')
        if tail: emitted.append(tail)
        reconstructed = b''
        for item in emitted:
            with wave.open(io.BytesIO(item['wav']), 'rb') as stream:
                self.assertLessEqual(stream.getnframes(), 480)
                reconstructed += stream.readframes(stream.getnframes())
        self.assertEqual(reconstructed, pcm)
    def test_speaker_limit_rejects_new_speaker_preserves_old(self):
        buffer = self.create(max_users=2)
        pcm = b'\x01\x00\x02\x00' * 10
        buffer.push('1', pcm, now=0)
        buffer.push('2', pcm, now=0)
        self.assertEqual(buffer.push('3', pcm, now=0), [])
        self.assertIsNone(buffer.flush('3'))
        self.check_wav(buffer.flush('1'), '1', pcm)
    def test_bad_pcm_and_anonymous_do_not_allocate(self):
        buffer = self.create()
        for user, pcm in [('', b'1234'), ('1', b'123'), ('1', b''), ('1', '1234')]:
            self.assertEqual(buffer.push(user, pcm, now=0), [])
            self.assertIsNone(buffer.flush(user))
    def test_reset_discards_audio(self):
        buffer = self.create()
        buffer.push('1', b'1234', now=0)
        buffer.clear()
        self.assertIsNone(buffer.flush('1'))
    def test_invalid_configuration(self):
        for args in [{'max_users':0}, {'max_seconds':0}, {'silence_seconds':0}, {'max_seconds':float('inf')}, {'silence_seconds':float('nan')}]:
            with self.assertRaises(ValueError): self.create(**args)


if __name__ == '__main__': unittest.main()
