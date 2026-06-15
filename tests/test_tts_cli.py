from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from gamma.run_tts_test import _parse_args


class TtsCliTest(unittest.TestCase):
    def test_text_accepts_multiple_words(self) -> None:
        with patch.object(sys, "argv", ["gamma.run_tts_test", "hello", "from", "dashboard"]):
            args = _parse_args()

        self.assertEqual(args.text, ["hello", "from", "dashboard"])
