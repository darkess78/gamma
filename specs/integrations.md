# Integrations

Status: Current
Last verified: 2026-06-22

Integrations normalize external inputs into stream events and consume generic
performer outputs. They do not call conversation internals directly.

## Twitch

- IRC chat ingestion with authentication, reconnect/backoff, bot ignores, and durable state
- EventSub WebSocket ingestion with token validation and subscription evidence
- chat sanitization, trust overrides, dry-run replay, and per-event runtime controls
- normalized follows, raids, subscriptions, bits, redeems, and moderation-style events
- stream output remains gated by Presence, safety, pacing, and target policy

## Discord

- optional allowlisted text worker for one guild/channel
- normalized identity and message metadata posted to the Shana stream API
- Shana replies in the configured text channel after output review; mentions are disabled on sent messages
- `!shana join`, `leave`, `pause`, `resume`, and `status` require the configured owner Discord user ID
- bounded per-speaker PCM becomes STT input, then an explicit Discord identity stream turn and approved TTS playback
- the pinned receive extension uses Discord.py's DAVE session to authenticate Opus before decoding, with no plaintext fallback
- Discord output stays on `discord_call`, including in Go Live; Sleep and Break suppress conversational output
- Wake allows the configured Discord conversation without enabling Twitch or public stream output
- credentials and guild/channel/owner identifiers stay in ignored machine-local configuration

### Staged Discord playback reply accounting

In the staged foundation rework, a successful voice callback counts one reply
only if its playback generation is still current when that callback arrives.
Pause, leave or a generation change before the callback invalidates the success.
A genuine successful callback received before a later owner stop still counts,
even if the waiting coroutine resumes afterward. Stale/no-start playback,
callback errors, timeout and task cancellation do not count as successful replies.
Existing owner controls, queue invalidation, path checks, review gates and cleanup
remain unchanged. This is adapter callback accounting, not proof of audible
playback, and has not been activated in production.

## VTube Studio

- optional adapter translates generic expression/motion events into hotkey requests
- disabled by default and not part of the core assistant path
- endpoint, token, and hotkey mapping remain machine-local configuration

## Future Integration Rule

New integrations are frozen during the Persistent Shana milestone. A future
adapter must expose bounded inputs/actions, preserve auditability, and avoid
adding platform-specific logic to conversation, voice, or StreamBrain.

# Shana dashboard controls

The Presence dashboard exposes the configured Shana Discord bot's
start/stop lifecycle and voice join, leave, pause and resume controls. Voice
commands go through `/api/discord/voice/control`; command receipts are available
at `/api/discord/voice/commands/{command_id}`. Existing dashboard authentication
and proxy access policies apply. The dashboard uses the configured owner identity
rather than accepting a caller-supplied Discord user ID.
Credentials never enter dashboard responses. The bot writes fresh connection and
voice status; stale observations are shown as unverified. Interrupted command
outcomes remain pending rather than being replayed.
