"""Tic-tac-toe against a simple bot, played by Shana over Gamma's game protocol.

Shana is X and moves first; the bot (O) takes a winning cell, blocks, or plays
randomly. Run it against a Gamma server with games enabled:

    python -m gamma.games.examples.tictactoe "ws://<gamma-pc>:8000/v1/games/play?key=<games_key>"
"""
from __future__ import annotations

import random
import sys

from ..client import Action, GameClient

LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
PLACE = Action("place", "Put your X in an empty cell, numbered 1-9 left to right, top to bottom.",
               {"type": "object", "properties": {"cell": {"type": "integer", "minimum": 1, "maximum": 9}},
                "required": ["cell"]})
RESIGN = Action("resign", "Give up this game.")


class TicTacToe:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.board = [" "] * 9
        self.rng = rng or random.Random()

    def render(self) -> str:
        cells = [c if c != " " else str(i + 1) for i, c in enumerate(self.board)]
        return "\n".join(" ".join(cells[r * 3:r * 3 + 3]) for r in range(3))

    def winner(self) -> str | None:
        for a, b, c in LINES:
            if self.board[a] != " " and self.board[a] == self.board[b] == self.board[c]:
                return self.board[a]
        return "draw" if " " not in self.board else None

    def apply(self, action: str, params: dict) -> tuple[bool, str]:
        """Shana's move. Returns (ok, message) for the outcome."""
        if action == "resign":
            return True, "Shana resigned."
        cell = params.get("cell")
        if not isinstance(cell, int) or not 1 <= cell <= 9 or self.board[cell - 1] != " ":
            return False, f"cell {cell} is taken; pick an empty one"
        self.board[cell - 1] = "X"
        return True, f"X in cell {cell}."

    def bot_move(self) -> int:
        free = [i for i, c in enumerate(self.board) if c == " "]
        for mark in ("O", "X"):  # win if possible, else block
            for i in free:
                self.board[i] = mark
                done = self.winner() == mark
                self.board[i] = " "
                if done:
                    self.board[i] = "O"
                    return i + 1
        i = self.rng.choice(free)
        self.board[i] = "O"
        return i + 1


def play(client: GameClient, *, rng: random.Random | None = None, deadline_ms: int = 15_000,
         allow_resign: bool = True) -> str:
    """Play one game over a connected client. Returns "X", "O", "draw", or "resigned"/"passed"."""
    game = TicTacToe(rng)
    client.hello("Tic-tac-toe", capabilities=["say"])
    client.declare(PLACE, RESIGN)
    client.observe("New game of tic-tac-toe. You are X and move first.", importance="high")
    turn = 0
    while game.winner() is None:
        turn += 1
        answer = client.decide_and_apply(
            "Your move: place an X.", ["place", "resign"] if allow_resign else ["place"], game.apply,
            request_id=f"turn-{turn}", state=game.render(), deadline_ms=deadline_ms)
        if answer["type"] == "pass":
            client.bye("Shana passed")
            return "passed"
        if answer["action"] == "resign":
            client.bye("resigned")
            return "resigned"
        if game.winner() is None:
            cell = game.bot_move()
            client.observe(f"O played cell {cell}.")
    result = game.winner()
    client.observe({"X": "You won!", "O": "You lost.", "draw": "It's a draw."}[result], importance="high")
    client.bye("game over")
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    client = GameClient.connect(argv[1])
    print("Result:", play(client))
    for line in client.said:
        print("Shana:", line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
