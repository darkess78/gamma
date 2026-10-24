# Approved decision producer and consumer inventory

Inspected on Gamma PC in `/home/neety/Documents/gamma-modular-phase1`. This inventory separates decision output, control messages, and explicit diagnostic previews. Original checkouts, local configuration, deployment and production services were not changed by this task.

| Producer / consumer | Boundary and disposition |
| --- | --- |
| `conversation/service.py:deliver_response`, text and image API, `ConversationClient` | Own committed decision, exact-content delivery grant, synthesis and publication. Durable semantic adapter claims precede execution; committed preparation is retained without automatic replay. |
| `stream/brain.py:process_event`, Twitch ingress, Discord ingress, replay ingress | Shared reviewed response path; synthesis and authority dispatch precede adapter output. Failed, denied or delayed required review releases no candidate output or cached filtered fallback; successful required review precedes output; final Presence/context checks and durable dispatch claims apply to every adapter. |
| `stream/brain.py:record_result` | Trace-only: recording a result never dispatches its events. |
| `presence.py:deliver_wake` | Checks expected Presence around owned delivery, then produces stream events. Uses the same grant-bound durable adapter boundary. |
| `proactive.py` | Scheduled proposals enter the owned conversation/stream path; no separate direct transport found. Silent preparation is not permission to deliver. |
| `voice/core_live.py` | Core-owned STT, committed review, cancellable synthesis and performer publication. Subtitle and audio events carry the approved target/grant and pass the shared durable dispatch boundary. |
| `voice/roundtrip.py` | Returns the grant and approved URL without legacy base64 bytes; fetching preparation is not permission to play or proof of playback. |
| `voice/live_jobs.py:LiveVoiceJobManager` | Public start/get/cancel/history facade now delegates to the API-owned core runtime, including cancellation. Old JSON chunk publication explicitly rejects. Does not launch per-turn workers through the facade. |
| `run_live_voice_worker.py` | Retired direct subprocess main and chunk generation entry points raise an explicit error before generation/publication. Incremental compatibility entry reaches the same rejection. Callers use the core live API. |
| `voice/live_runtime.py:SubprocessLiveTurnRuntime` | Retained public facade delegates to the migrated manager. API selection of the legacy setting now maps to core runtime. |
| `run_voice_mode.py`, `voice/controller.py` | CLI input uses managed `ConversationClient`; playback now consumes an approved response through `voice/approved_playback.py`, never its producer pathname. |
| `voice/approved_playback.py` | Unique WebSocket lease, server-built delivery offer, authenticated immutable audio read, SHA-256 verification, one-use start permit, ongoing revocation, and exact child-process termination. Child exit produces a receiver receipt; it is not proof of speaker audibility. Requires a cancellable `aplay` or `afplay` process; no detached Windows winsound fallback. |
| `run_voice_roundtrip.py`, `run_llm_test.py` | Managed conversation inputs and explicit local response/artifact reporting. No autonomous player was found in these entry points. |
| `stream/output.py:StreamOutputDispatcher` | Adapter fan-out validates approved content and claims each semantic effect before execution. Adapter success/failure is durable; uncertain claims do not retry. |
| `performer/bus.py:publish` | Shared event producer/queue validates grants and claims semantic publication. Consumers recheck context; replay cannot authorize execution. |
| `api/routes.py:performer_events` | Audio offers/prepare/revocation/receipts bind to the connection. CLI `delivery_offer` is server-built from its grant and selected lease; a durable claim prevents re-offering the same CLI grant after reconnect. |
| `delivery/playback.py` | Durable lease/offer/receipt ledger requires a grant and checks exact target, current permission and snapshot integrity. Receivers cannot acknowledge each other offers. |
| `dashboard/static/live.js` | Raw base64 playback branch removed. Queued reply requires a grant-bound offer, immutable audio load and start permit. |
| `dashboard/static/monitor.js` and `performer/static/performer.html` | Existing audio queues already require connection-owned offer, immutable load, permit, receipt and revocation. Their base64 source helper cannot independently pass that boundary. Server-side non-audio execution checks also validate current grants, target and Presence. |
| `dashboard/playback_proxy.py`, `playback_relay.py`, `static/playback-requests.js`, `static/live-offers.js` | Transport/receiver helpers for the same authenticated grant and connection protocol. They do not independently authorize a decision. |
| `performer/vtube_studio.py:VTubeStudioRunner` | Rejects replay and validates grant, target, Presence, audience, policy, expiry, mute and cancellation. Rechecks after asynchronous VTube authentication immediately before execution and claims semantic actions durably. |
| `integrations/twitch/worker.py`, `eventsub.py` | External ingress only. IRC sends registration/PONG control traffic; no assistant chat send discovered. |
| `integrations/discord/worker.py`, `runtime.py` | Text ingress and local capability/output records only; actual Discord replies/voice remain disabled experimental behavior. Recording a payload is not delivery. |
| `integrations/minecraft/coordinator.py` | Separate bounded command coordinator with deadline, session liveness, duplicate IDs and emergency-stop checks. Handshake advertises `minecraft_chat_output_enabled=False`; no conversational reply adapter exists. Command control is not a delivery-grant bypass. |
| `performer/bus.py:set_target_muted/clear_target`, `stream/brain.py:stop_stream` | Explicit stop/clear controls must remain deliverable without a speech grant; they revoke output and never introduce new approved content. |
| `run_tts_test.py`, TTS worker/provider, dashboard artifact player and artifact download | Explicit synthesis/diagnostic preparation surfaces. Legacy artifact routes return HTTP 410; active delivery uses the current approved URL and receiver protocol. |

## Migration acceptance evidence

Gamma command: `PYTHONPATH=$PWD/src .venv/bin/python -m pytest tests/test_approved_cli_playback.py tests/test_live_voice_runtime.py tests/test_voice_upload_routes.py -q`.

Result: **18 passed**. This includes actual isolated shell-player process execution and exit status; revocation kills the exact running `sleep` child and confirms its PID no longer exists; connection loss also terminates that child. Tests reject raw-path legacy playback, cross-receiver offers before downloading, modified audio before requesting a start permit, and grantless CLI playback before connecting, reject both legacy synthesis entry points before model calls, reject old JSON publication, verify facade delegation/cancellation, and reject invalid upload identifiers before file creation. These checks do not establish hardware speaker audibility, microphone capture, or real model quality.

Integration acceptance is exercised by `tests/test_delivery_execution.py`, `tests/test_vtube_delivery.py`, `tests/test_delivery_dispatch.py` and `tests/test_required_delivery_review.py`, including stale execution, authentication delay, durable dispatch/crash boundaries, no replay and unavailable required review. Final aggregate counts belong to the task acceptance report.

## Durable state and recovery semantics

`tts_metadata.delivery_state=prepared` means approved content and immutable audio are ready. The legacy grant row status `dispatched` remains a compatibility value for preparation; it does not prove adapter execution. Per-consumer dispatch attempts distinguish claimed/uncertain work from returned adapter dispatch or failure. Playback receipts independently distinguish offered, started, completed, interrupted and failed playback.

Approved payload, publication and preparation records commit together. Consumer claims commit before external effects and use decision/target/semantic-consumer identity rather than regenerated event UUIDs. A crash after a claim leaves uncertain work that is not replayed. A crash before dispatch preserves audit material without granting restart replay permission. Stale generations cannot execute old grants. Recovery favors omission over duplicate output; new output requires a fresh decision and approval.

Phase 3 implementation acceptance is complete within the isolated worktree once task acceptance checks pass. Hardware speaker audibility, microphone and avatar lifecycle acceptance remain Phase 5 work; no production deployment or restart was performed.
