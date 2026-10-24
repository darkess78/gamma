import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec=importlib.util.spec_from_file_location('receive_gate',Path(__file__).parents[1]/'src/gamma/integrations/discord/receive_gate.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class GateTest(unittest.TestCase):
    def build(self,version=1,ready=True,user=1):
        self.calls=[]
        def decrypt(uid,media,payload):self.calls.append((uid,media,payload));return b'plain-opus'
        self.session=SimpleNamespace(ready=ready,decrypt=decrypt)
        self.connection=SimpleNamespace(dave_protocol_version=version,dave_session=self.session)
        self.client=SimpleNamespace(_connection=self.connection,_get_id_from_ssrc=lambda _:user)
        return module.DaveReceiveGate(self.client,media_type='audio')
    def test_decrypts_authenticated_audio(self):
        gate=self.build(); self.assertEqual(gate.decode(b'ciphertext',ssrc=10,payload_type=120),b'plain-opus')
        self.assertEqual(self.calls,[(1,'audio',b'ciphertext')])
    def test_rejects_unready_and_downgraded(self):
        for version,ready in [(0,True),(1,False),(-1,True),(None,True)]:
            gate=self.build(version=version,ready=ready)
            self.assertIsNone(gate.decode(b'ciphertext',ssrc=10,payload_type=120));self.assertEqual(self.calls,[])
    def test_non_audio_unknown_user_empty_and_silence_dropped(self):
        for payload,ptype,user in [(b'ciphertext',101,1),(b'ciphertext',120,None),(b'ciphertext',120,0),(b'',120,1),(b'\xf8\xff\xfe',120,1)]:
            gate=self.build(user=user)
            self.assertIsNone(gate.decode(payload,ssrc=10,payload_type=ptype));self.assertEqual(self.calls,[])
    def test_crypto_failure_never_falls_back(self):
        gate=self.build()
        def fail(*args):raise RuntimeError('secret key and audio')
        self.session.decrypt=fail
        self.assertIsNone(gate.decode(b'ciphertext',ssrc=10,payload_type=120))
    def test_missing_or_malformed_state(self):
        for state in [None,SimpleNamespace(),SimpleNamespace(dave_protocol_version=1,dave_session=None)]:
            gate=self.build();self.client._connection=state
            self.assertIsNone(gate.decode(b'ciphertext',ssrc=10,payload_type=120))
    def test_empty_wrong_type_and_oversize_output(self):
        for value in [b'',None,'wrong',b'x'*65537]:
            gate=self.build();self.session.decrypt=lambda *args:value
            self.assertIsNone(gate.decode(b'ciphertext',ssrc=10,payload_type=120))
    def test_input_bounds(self):
        gate=self.build()
        for value in [None,'ciphertext',b'x'*65537]:
            self.assertIsNone(gate.decode(value,ssrc=10,payload_type=120))
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
