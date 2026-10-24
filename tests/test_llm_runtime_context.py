from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from gamma.config import settings
from gamma.llm.capabilities import model_capability
from gamma.llm.local_adapter import LocalLLMAdapter


class LocalLLMRuntimeContextTest(unittest.TestCase):
    def _request_payload(self, *, model: str) -> dict[str, object]:
        response = MagicMock()
        response.read.return_value = b'{"response":"ok"}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("gamma.llm.local_adapter.request.urlopen", return_value=response) as urlopen:
            LocalLLMAdapter().generate_reply(
                "system",
                "hello",
                model_override=model,
            )

        request = urlopen.call_args.args[0]
        return json.loads(request.data.decode("utf-8"))

    def test_qwen38_uses_live_validated_runtime_context(self) -> None:
        capability = model_capability("local", "qwen3.8:27b")

        self.assertEqual(capability.advertised_context_tokens, 262144)
        self.assertEqual(capability.runtime_context_tokens, 65536)
        self.assertEqual(capability.usable_input_tokens, 57344)
        self.assertEqual(capability.reserved_output_tokens, 4096)
        self.assertEqual(capability.safety_margin_tokens, 4096)
        self.assertTrue(capability.persona_capable)
        self.assertTrue(capability.vision_capable)
        self.assertTrue(capability.tools_capable)

        payload = self._request_payload(model="qwen3.8:27b")
        self.assertEqual(payload["options"], {"num_ctx": 65536})

    def test_models_without_runtime_context_keep_ollama_default(self) -> None:
        original_keep_alive = settings.local_llm_keep_alive
        settings.local_llm_keep_alive = "30m"
        try:
            payload = self._request_payload(model="gpt-oss:20b")
        finally:
            settings.local_llm_keep_alive = original_keep_alive

        self.assertNotIn("options", payload)
        self.assertEqual(payload["keep_alive"], "30m")


if __name__ == "__main__":
    unittest.main()
