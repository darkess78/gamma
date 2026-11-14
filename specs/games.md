# Games

Status: first version, 2026-11-14. Off by default; not yet tried with a real game.

Shana plays games through the [Neuro SDK protocol](https://github.com/VedalAI/neuro-sdk/blob/main/API/SPECIFICATION.md)
(MIT). Gamma is the server side: a game or mod connects over a WebSocket,
registers actions with simple JSON schemas, sends context, and "forces" Shana
to pick an action when it's her turn. Mods already written for Neuro-sama
(Balatro, Inscryption, Buckshot Roulette, Slay the Spire 2, Among Us and others)
can connect to Gamma instead, and new games get mods written against a known API
instead of a one-off Gamma integration each.

## Turning it on

In `config/app.local.toml` on Gamma PC:

```toml
games_neuro_enabled = true
games_neuro_key = "<long random string>"
games_neuro_chooser = "model"   # or "random" to test a mod without a model
```

Point the mod at `ws://<gamma-pc>:8000/v1/games/neuro?key=<games_neuro_key>`.
Mods built on the official Unity SDK read the URL from the `NEURO_SDK_WS_URL`
environment variable. Mods cannot send the API bearer token, which is why the
route has its own key.

## What is implemented

- `startup` (clears actions; acknowledged with `characterId: "shana"`),
  `context`, `actions/register`, `actions/unregister`, `actions/force`,
  `action/result`.
- One force at a time; a second force while one is open is ignored.
- A failed result retries the force, up to 3 attempts, then Shana gives up and
  the game gets nothing further for that force. A result that doesn't arrive
  within 20 seconds counts as a failure; late results are ignored.
- Non-ephemeral force state and queries are kept as context for later choices.
- Choosers: `RandomChooser` (like the SDK's Randy, for conformance testing) and
  `ModelChooser`, which asks the configured LLM for JSON and validates the
  action name; bad output falls back and is retried like a failed action.

Code: `src/gamma/games/neuro_protocol.py` (transport-free session) and
`src/gamma/api/routes_games.py` (WebSocket route).

## Not done yet

- `speech/started`, `speech/ended` and the `speech_finished` reply; the voice
  side-channel.
- What Shana says about a move (`Choice.say`) is collected but not yet spoken or
  shown as subtitles, and game context doesn't reach her normal conversation;
  both belong to the coordination core.
- Schema-constrained decoding for the model, and remembering past runs (W2).
- Trying it with a real mod, which needs the GPU for the model chooser.
