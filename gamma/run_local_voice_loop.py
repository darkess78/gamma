from __future__ import annotations

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
        response = service.respond(user_text)
        print(f"assistant> {response.spoken_text}")


if __name__ == "__main__":
    main()
