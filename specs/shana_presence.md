# Shana Presence

Status: Current
Last verified: 2026-06-22

Shana Presence is the operator-level lifecycle state above backend process
controls. It describes what Shana is allowed to do, not whether `uvicorn` or
sidecar processes exist.

## Modes

- `sleep`: no autonomous or public behavior. Explicit local requests may still
  be handled by normal routes, but stream-facing output is suppressed.
- `wake`: local/manual/mic interaction is allowed through dashboard monitor
  paths. Public stream output remains muted.
- `go_live`: stream-facing co-host behavior is allowed for the current Shana
  backend session. Public voice, subtitles, ambient chat handling, proactive
  behavior, and safety review are enabled by Presence policy.
- `break`: Twitch/EventSub observation may continue, but public/proactive
  output is suppressed and the public performer target is muted/cleared.

## Runtime State

Presence state is runtime state stored under:

```text
data/runtime/presence/state.json
```

It is not provider configuration and is not tracked source. The state includes:

- `mode`, `desired_mode`, and `requires_confirmation`
- autonomy flags for proactive idle, ambient chat, and self-goals
- input flags for local mic and Twitch observation
- output flags for dashboard monitor, stream public, voice, and subtitles
- safety flags for dry run and LLM safety review
- current activity summary

## Restart Rule

Public/live output must not resume automatically after the Shana backend
restarts. If the persisted state says `go_live` but its confirmation happened
before the current Shana process booted, stream handling downgrades the
effective state to `wake`, keeps `desired_mode` as `go_live`, and sets
`requires_confirmation`.

## Dashboard Boundary

Presence dashboard APIs live on the dashboard app under `/api/presence*`.
Stream and performer enforcement remains on the Shana API side through
`/v1/stream/events` and `/v1/performer/*`. This preserves the locked two-app
network boundary.
