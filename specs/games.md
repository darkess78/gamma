# Games: the Gamma game protocol

Status: version 1, 2026-11-14. Off by default; tested with the example
tic-tac-toe game and a random chooser, not yet with a real model or a real game.

Inspired by the Neuro SDK.

A game (or a mod for one) connects to Gamma over a WebSocket, declares the
actions Shana may take, tells her what is happening, and asks her to decide
when it is her turn. Gamma replies with the action she picked. The game stays
in charge of its own rules: it applies the action and reports whether it
worked.

The protocol is meant to be small enough to implement in an afternoon. A
working Python client is in `src/gamma/games/client.py` (about 150 lines), and
`src/gamma/games/examples/tictactoe.py` is a complete game built on it.

## Connecting

In `config/app.local.toml` on Gamma PC:

```toml
games_enabled = true
games_key = "<long random string>"
games_chooser = "model"   # or "random" to test a game without a model
```

The game connects to `ws://<gamma-pc>:8000/v1/games/play?key=<games_key>`.
When games are off or the key is wrong, the connection is closed with code
4401 before it is accepted. The route has its own key because game mods often
cannot send the API bearer token. Treat the key like a password and keep the
port off the public internet.

Try it from any machine with the repo installed:

```
python -m gamma.games.examples.tictactoe "ws://<gamma-pc>:8000/v1/games/play?key=<games_key>"
```

## Messages

Every message is one JSON object in one text frame, with a string `type` and
its other fields at the top level. Unknown fields are ignored. A message Gamma
can't use gets an `error` reply; the connection stays open.

### Game to Gamma

| type | fields | meaning |
|---|---|---|
| `hello` | `protocol` (int, must be `1`), `game` (string), `capabilities` (list of strings, optional) | Must be first. Starts a new session; sending it again resets declared actions and any open decision. |
| `actions/declare` | `actions`: list of `{name, description, params?}` | Adds or replaces actions. `name` matches `^[a-z][a-z0-9_]{0,63}$`. `params` is a schema of `type: "object"` (see below); leave it out for an action without params. Up to 64 actions. |
| `actions/remove` | `names`: list of strings | Removes actions. Unknown names are ignored. |
| `observe` | `text` (string), `importance` (`low` / `normal` / `high`, default `normal`), `comment` (bool, default false) | Something happened. Kept as context (last 40) for later decisions; `high` items stay in the prompt longer. `comment: true` means Shana may talk about it unprompted. |
| `decide` | `id` (string, chosen by the game), `prompt` (string), `actions` (list of declared names), `state` (string, optional), `deadline_ms` (int, default 15000, clamped to 1000-120000), `priority` (`low` / `normal` / `high`), `speak` (bool, default true), `remember` (bool, default true) | Asks Shana to pick one of `actions`. Gamma answers with `act` or `pass`. `speak: false` means no `say` lines for this decision. `remember: false` keeps `prompt` and `state` out of later context. |
| `outcome` | `id`, `ok` (bool), `message` (string, optional) | The result of the last `act`. `ok: false` makes Gamma try again (see Retries); the message is shown to Shana. |
| `cancel` | `id` | The game no longer needs this decision. No reply. |
| `bye` | `reason` (optional) | The game is leaving. Gamma closes the connection. |

### Gamma to game

| type | fields | meaning |
|---|---|---|
| `welcome` | `protocol`, `session` (string), `character` (`"shana"`), `display_name`, `limits` (`max_attempts`, `outcome_timeout_ms`, `deadline_ms` range, `max_actions`) | Reply to `hello`. |
| `declared` | `actions` (names accepted), `errors` (list of strings) | Reply to `actions/declare`. Invalid actions are skipped and explained in `errors`. |
| `act` | `id` (the decision's id), `attempt` (1, 2, ...), `action`, `params` (object, `{}` when none) | Shana's choice. `params` has already been checked against the declared schema. Reply with `outcome`. |
| `say` | `id` (the decision it belongs to), `text` | A line Shana says about her move, sent just before its `act`. Show it as a subtitle or ignore it. |
| `pass` | `id`, `reason`, `message` | Gamma will not answer this decision. Reasons: `attempts` (gave up after retries), `deadline`, `busy` (another decision is open), `unknown_action`, `no_actions` (the allowed actions were removed). The game should continue without her move. |
| `error` | `message`, `about` (the message type it refers to, optional) | Something was wrong with a game message. Not fatal. |
| `bye` | `reason` | Gamma is closing the connection (sent after an unsupported `protocol`). |

### Params schemas

A subset of JSON Schema. Supported keywords: `type` (`object`, `string`,
`integer`, `number`, `boolean`, `array`), `properties`, `required`, `enum`,
`minimum`, `maximum`, `minLength`, `maxLength`, `minItems`, `maxItems`,
`items`, `description`, `title`. Anything else (`pattern`, `oneOf`, `$ref`,
...) makes the declaration fail with an error, so a game never relies on a
check Gamma doesn't make. Objects don't accept properties they don't declare.
`true`/`false` never count as integers or numbers.

### Rules

- One decision at a time. A `decide` while another is open gets
  `pass` with reason `busy`.
- Params are validated before anything is sent. If the model names an action
  that isn't allowed, or its params don't fit the schema, Gamma asks again
  internally with the problem in the prompt; the game only sees valid `act`s.
- Retries: every attempt to choose counts, whether it failed validation or the
  game rejected it. After 3 attempts Gamma sends `pass` (`attempts`).
- An `act` with no `outcome` within 10 seconds counts as rejected.
- The deadline is the game's: if it passes before Gamma has a valid choice,
  Gamma sends `pass` (`deadline`) instead of a late `act`. A retry after a
  rejection also needs time left before the deadline.
- An `outcome` for an id that isn't waiting (late, duplicate, cancelled) is
  ignored.

## Example exchange

```
game   {"type":"hello","protocol":1,"game":"Tic-tac-toe","capabilities":["say"]}
gamma  {"type":"welcome","protocol":1,"session":"3f2a...","character":"shana","display_name":"Shana","limits":{...}}
game   {"type":"actions/declare","actions":[{"name":"place","description":"Put your X in an empty cell",
         "params":{"type":"object","properties":{"cell":{"type":"integer","minimum":1,"maximum":9}},"required":["cell"]}}]}
gamma  {"type":"declared","actions":["place"],"errors":[]}
game   {"type":"observe","text":"New game. You are X and move first.","importance":"high"}
game   {"type":"decide","id":"turn-1","prompt":"Your move: place an X.","actions":["place"],
         "state":"1 2 3\n4 5 6\n7 8 9","deadline_ms":15000}
gamma  {"type":"say","id":"turn-1","text":"Center, obviously."}
gamma  {"type":"act","id":"turn-1","attempt":1,"action":"place","params":{"cell":5}}
game   {"type":"outcome","id":"turn-1","ok":true,"message":"X in cell 5."}
game   {"type":"observe","text":"O played cell 1."}
game   {"type":"decide","id":"turn-2","prompt":"Your move: place an X.","actions":["place"],"state":"O 2 3\n4 X 6\n7 8 9"}
gamma  {"type":"act","id":"turn-2","attempt":1,"action":"place","params":{"cell":1}}
game   {"type":"outcome","id":"turn-2","ok":false,"message":"cell 1 is taken"}
gamma  {"type":"act","id":"turn-2","attempt":2,"action":"place","params":{"cell":9}}
game   {"type":"outcome","id":"turn-2","ok":true}
...
game   {"type":"bye","reason":"game over"}
```

## Code

- `src/gamma/games/protocol.py`: `GameSession` (transport-free: `receive()`
  and `tick()` return the replies), schema checking and validation, and the
  choosers. `RandomChooser` picks any allowed action with valid random params,
  for testing a game without a model. `ModelChooser` prompts any `gamma.llm`
  adapter for `{"action", "params", "say"}` JSON.
- `src/gamma/api/routes_games.py`: the WebSocket route. Model calls run in a
  worker thread.
- `src/gamma/games/stream_output.py`: her `say` lines and `observe` events
  marked `comment: true` become `game_state` stream events. A line is said as
  written, and a comment event gets a one-sentence reaction from the model.
  Both go through the stream's safety review, pacing and subtitle/voice output.
  The game is an unknown speaker, with no private memory and no tools. Turn it
  off with `games_stream_output = false`.
- `src/gamma/games/client.py`: reference client. `decide_and_apply()` runs a
  whole decide/act/outcome loop with a callback.
- `src/gamma/games/examples/tictactoe.py`: example game.
- `tests/test_game_protocol.py`.

## Not done yet

- No speech-timing messages (letting the game wait until she finishes talking).
- `priority` is accepted and passed to the chooser but doesn't change anything.
- No schema-constrained decoding; invalid answers are caught by validation and
  retried instead.
- Memory of past games (W2), and a trial with a real game and the real model,
  which needs the GPU.
