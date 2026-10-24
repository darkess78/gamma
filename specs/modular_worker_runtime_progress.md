# Modular runtime implementation progress

The accepted objective is the full refactor described in the proposal and audit.
Implementation is staged in the isolated Gamma-PC worktree, using Gamma's source
as its baseline. No commit, push, production checkout update or production
service restart has been authorized or performed.

| Phase | Current evidence | Remaining acceptance |
| --- | --- | --- |
| 0. Baseline | Source comparison, isolated import, regression tests and synthetic text/voice/stream timings in `modular_worker_runtime_phase0.md` | Actual GPU cold/warm and live adapter baselines |
| 1. Lifecycle and logs | Authenticated dashboard to real improvement worker; serialized lifecycle controls, correlated bounded logs, CLI compatibility and browser start/filter/stop in `modular_worker_runtime_lifecycle_slice.md` | Apply each additional adapter's real dependency/readiness contract as it migrates |
| 2. State ownership | Authority transactions cover conversation, stream context/goals, persona/core memory, tools, Presence/performer controls, viewer trust and publication records. Owned effects, operator invalidation, scoped recovery and crash evidence are inventoried in `modular_state_writer_inventory.md` | No implementation acceptance outstanding for this state slice; production migration/rollback remains an operational action, not performed here |
| 3. Delivery and safety | Required review before output; atomic approved preparation/publication; durable semantic consumer claims without restart replay; final Presence/audience/policy/expiry/cancellation checks; browser and cancellable CLI receipts; VTube execution checks after authentication. Inventory and acceptance in `modular_delivery_inventory.md` and `modular_worker_runtime_delivery_slice.md` | No remaining implementation item in this delivery slice after task acceptance checks. Production migration is not performed; hardware audio/avatar lifecycle acceptance remains Phase 5 |
| 4. Persistent brain | Managed text/image/Wake, stream policy, roundtrip/core-live voice and silent/proactive generation use one versioned independent brain process. Core retains state/tool/delivery authority; authenticated lifecycle and crash/restart fences passed real HTTP/process tests in `modular_worker_runtime_brain_process.md` | Bounded silent goal selection, observation coalescing, foreground yielding and internal result persistence are implemented in `modular_worker_runtime_silent_slice.md`; production background capacity and broader latency baselines remain separate operational acceptance |
| 5. Voice and avatar | Persistent STT/TTS, core live capture/final identity and intended Qwen profile verified with real GPU audio. Supervised Windows microphone/playback, TTS-off text, STT restart and transcript-confirmed interruption passed; receiver reconnect did not resume old audio. See `modular_voice_acceptance.md` | Enabled RVC and avatar boot/idle/interruption/restart evidence remain separate. Decision/review fixtures were synthetic; five pre-existing legacy tests conflict with retirement stubs. No production migration or further legacy removal performed |
| 6. Existing integrations | Improvement lifecycle and Twitch IRC/EventSub common controls, protocol-specific readiness, local TLS/WebSocket lifecycle acceptance and durable IRC/EventSub fast ingress receipts with duplicate/crash checks in `modular_worker_runtime_integration_slice.md` | Live Twitch reconnect fault injection, admission priority and uncertain receipt reconciliation, EventSub server-requested session migration, Discord and Minecraft with protocol-specific acceptance |
| 7. New capabilities | Not implemented | Individually scoped adapters with real execution/cancellation and resource/permission checks |

Progress must be supported by executable behavior. Proposed APIs, dashboard
inventory entries and passing mock tests do not establish that an adapter or
replacement path exists. Keep legacy paths until their replacements pass the
proposal's acceptance checks, without sending duplicate speech or external actions.

## Shared admission slice

`modular_worker_runtime_admission_slice.md` records the completed provider caller
audit and cross-root SQLite admission, endpoint/resource budgets, interactive
reservations, waiting deadlines, retained uncertain work, evidence-based
reconciliation and operator visibility. Runtime protocols preserve background
priority and suppress duplicate worker request execution. Model-work defaults to
one reserved interactive slot; background inference and additional concurrency
require measured capacity before enabling them. Sequential real Whisper/Qwen
checks passed with exact readback and unchanged production identities. This
implements shared model admission; model residency/eviction and deployment of
existing production callers are separate operational work.

## Durable delivery acceptance

The final delivery task passed 1252 Python tests and 100 subtests, with 3
unrelated skips. JavaScript syntax/protocol checks and `git diff --check` passed.
`modular_delivery_acceptance.md` records real crash, loopback HTTP/WebSocket,
CLI child-process and delayed VTube transport evidence.


## Voice acceptance checkpoint

See [modular_voice_acceptance.md](modular_voice_acceptance.md) for the current
voice evidence. Real Whisper/Qwen GPU inference with the intended voice profile
reaches Chromium playback receipts. TTS stop preserves transcription and text;
STT restart and crash recovery preserve the brain identity. Duplicate final
controls and obsolete polling are guarded, and live jobs expose decision/grant
correlation. Supervised Windows microphone capture, audible playback, TTS-off
text output and transcript-confirmed interruption passed. Reconnection and STT
restart did not resume old audio. Decision/review fixtures were synthetic; selected
audio device names were not supplied. RVC is disabled in the intended profile. Existing legacy retirement
stubs were present before this task; no further retirement was performed.

## Task 6A conversation checkpoint, 2026-09-25

Deterministic acceptance is complete for the two reproduced defects. Core-live
cancellation previously let a held generation commit state; cancellation now
fences the actual commit and authorization boundary. Undelivered generated text
previously reappeared as confirmed assistant history, including cached summary
and last-action context; receipt-qualified prompt projection now prevents that.
Completed effects and user inputs are preserved. Text sent to a WebSocket
recipient remains text delivery evidence when audio is canceled.

`tests/test_conversation_acceptance.py` passes 13 integration cases. The canonical
candidate suite passed 1267 tests and 100 subtests, with 3 existing skips and 9
dependency/process warnings in 110.76 seconds. It ran through
`PYTHONPATH=$PWD/src .venv/bin/python -m pytest -p no:cacheprovider -q --tb=short`
inside a no-network sandbox with disposable state and production read-only.
Parent and child imports resolved to this candidate's src directory.
Source/configuration fingerprints and recovery archives are private under
`/home/neety/.local/state/gamma/task6a/20260925T005055Z`.

The five historical legacy assertions were run separately from their original
HEAD test file and still fail, with two other assertions passing. Before Task 6A,
the dirty candidate already replaced those tests with explicit retirement and
core-manager delegation tests. No additional test retirement or weakened
assertions were performed here. Original chunked/incremental/event-only output,
legacy JSON chunk bridging and legacy-manager cancellation expectations remain
unresolved as compatibility behavior.

Task 6 remains partially accepted. Real generation/review/audio and supervised
hardware acceptance are blocked by provider ownership. Production adapters do
not use the candidate shared admission ledger and production already owns GPU
processes. Empty Ollama residency and an empty ledger do not establish safe
coexistence. No real model was invoked, no selection changed, and no production
service was restarted. Real-provider timings and audible-latency samples were not
collected. Task 7 is not started or recommended from this checkpoint.

Remaining acceptance requires established safe provider ownership, then the
existing private browser/client path with real routed generation and required
review, real Whisper/Qwen, a fictional preference/correction conversation,
generation/playback interruption and follow-up, and supervised microphone/speaker
confirmation. Record actual model identities and correlated per-stage timings;
do not reuse synthetic verdicts or old hardware evidence as current acceptance.
