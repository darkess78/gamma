from __future__ import annotations

from time import perf_counter

from .conversation.service import ConversationService


def main() -> None:
    service = ConversationService()
    print("gamma local loop. type 'quit' to stop.")
    while True:
        user_text = input("you> ").strip()
        if user_text.lower() in {"quit", "exit"}:
            break
        if not user_text:
            continue
        started_at = perf_counter()
        response = service.respond(user_text)
        elapsed_seconds = perf_counter() - started_at
        print(f"assistant> {response.spoken_text}")
        print(f"[timing] reply generated in {elapsed_seconds:.2f}s")


if __name__ == "__main__":
    main()
