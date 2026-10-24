from __future__ import annotations

import json
import sys

from .conversation.client import ConversationClient


def main() -> None:
    """Run LLM test.
    
    Args:
        text: Test input (default from command line).
    
    Prints:
        JSON with input, reply, emotion, internal_summary.
    """
    text = " ".join(sys.argv[1:]).strip() or "Dashboard LLM smoke test."
    response = ConversationClient().respond(text, synthesize_speech=False)
    print(
        json.dumps(
            {
                "input": text,
                "reply": response.display_text or "",
                "speech_text": response.speech_text or "",
                "delivery_mode": response.delivery_mode,
                "emotion": response.emotion,
                "internal_summary": response.internal_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
