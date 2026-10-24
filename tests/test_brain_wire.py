import unittest
import importlib.util
import sys
import os
import json

# Load wire.py from source path
WIRE_PATH = os.path.join(os.path.dirname(__file__), '..', 'src', 'gamma', 'brain', 'wire.py')
spec = importlib.util.spec_from_file_location('gamma.brain.wire', WIRE_PATH)
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)

class TestWireProtocol(unittest.TestCase):

    def setUp(self):
        self.valid_message = {
            'schema_version': 1,
            'generation': 'a' * 32,
            'request_id': 'b' * 32,
            'type': 'execute',
            'payload': 'test'
        }

    def test_roundtrip_unicode(self):
        msg = dict(self.valid_message)
        msg['payload'] = 'héllo wörld 世界 😀'
        data = wire.encode(msg)
        decoded = wire.decode(data)
        self.assertEqual(decoded, msg)

    def test_encode_returns_bytes_with_newline(self):
        data = wire.encode(self.valid_message)
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.endswith(b'\n'))

    def test_version_constant(self):
        self.assertEqual(wire.VERSION, 1)
        self.assertEqual(wire.MAX_FRAME, 1048576)

    def test_decode_valid_ready_none_request_id(self):
        msg = {
            'schema_version': 1,
            'generation': 'c' * 32,
            'request_id': None,
            'type': 'ready'
        }
        data = wire.encode(msg)
        self.assertEqual(wire.decode(data), msg)

    def test_duplicate_key_rejected(self):
        raw = wire.encode(self.valid_message).replace(b'"payload":"test"', b'"payload":{"a":1,"a":2}')
        with self.assertRaises(ValueError):
            wire.decode(raw)

    def test_malformed_json(self):
        raw = b'{bad json}\n'
        with self.assertRaises(ValueError):
            wire.decode(raw)

    def test_oversized_frame(self):
        big_message = {
            'schema_version': 1,
            'generation': 'f' * 32,
            'request_id': 'a' * 32,
            'type': 'result',
            'data': 'A' * (wire.MAX_FRAME)  # will be larger after encoding
        }
        with self.assertRaises(ValueError):
            wire.encode(big_message)

    def test_nan_rejected(self):
        msg = dict(self.valid_message)
        msg['field'] = float('nan')
        with self.assertRaises(ValueError):
            wire.encode(msg)

    def test_nonfinite_rejected(self):
        msg = dict(self.valid_message)
        msg['field'] = float('inf')
        with self.assertRaises(ValueError):
            wire.encode(msg)

    def test_bad_schema_version_type_bool(self):
        msg = dict(self.valid_message)
        msg['schema_version'] = True
        with self.assertRaises(ValueError):
            wire.encode(msg)

    def test_bad_generation_case(self):
        msg = dict(self.valid_message)
        msg['generation'] = 'A' * 32
        with self.assertRaises(ValueError):
            wire.encode(msg)

    def test_bad_request_missing(self):
        msg = dict(self.valid_message)
        del msg['request_id']
        with self.assertRaises(ValueError):
            wire.encode(msg)

    def test_bad_type(self):
        msg = dict(self.valid_message)
        msg['type'] = 'invalid'
        with self.assertRaises(ValueError):
            wire.encode(msg)

    def test_request_id_invalid_for_non_ready(self):
        msg = dict(self.valid_message)
        msg['type'] = 'execute'
        msg['request_id'] = None
        with self.assertRaises(ValueError):
            wire.encode(msg)

    def test_decode_non_bytes_input(self):
        with self.assertRaises(ValueError):
            wire.decode('some string')

    def test_decode_empty(self):
        with self.assertRaises(ValueError):
            wire.decode(b'')

    def test_decoding_rejects_nonfinite_and_unterminated(self):
        raw = wire.encode(self.valid_message)
        for bad in [raw[:-1], *[raw.replace(b'"test"', value) for value in [b'NaN', b'Infinity', b'1e999']]]:
            with self.subTest(raw=bad), self.assertRaises(ValueError):
                wire.decode(bad)

    def test_ids_reject_trailing_newline(self):
        for field in ("generation", "request_id"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                wire.encode(dict(self.valid_message, **{field: "a" * 32 + "\n"}))

if __name__ == '__main__':
    unittest.main()
