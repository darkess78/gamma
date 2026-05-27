from __future__ import annotations

import json
import sys

from .conversation.service import ConversationService


def main() -> None:
    text = " ".join(sys.argv[1:]).strip() or "Dashboard LLM smoke test."
    response = ConversationService().respond(text, synthesize_speech=False)
    print(
        json.dumps(
            {
                "input": text,
                "reply": response.spoken_text,
                "emotion": response.emotion,
                "internal_summary": response.internal_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
