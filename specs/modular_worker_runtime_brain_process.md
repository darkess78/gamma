# Independent brain worker

Implemented in `/home/neety/Documents/gamma-modular-phase1`. This checkpoint is
not a production deployment. No machine-local configuration or service was changed.

## Runtime and authority

The managed Shana API constructs `ConversationService(use_worker=True)`. Its
compatibility methods retain exchange ownership, persistence, resolved speaker
permissions, required safety review, delivery grants and publication in the core.
They send cognition to one persistent `gamma.brain.worker` child. That child
assembles persona and retrieved context, budgets prompts, selects structured
responses and tool intentions, realizes tool results into communicable text,
and produces bounded memory/state proposals. It never constructs continuity,
memory-writer or delivery-authority instances.

The child requests bounded memory/continuity/persona reads and tool execution
through a private core callback protocol. Core binds each callback to the original
speaker, session, evaluation flag and proposal container. A worker-supplied speaker,
session or permission cannot replace those bindings. Tool execution retains the
existing allowlist and capability checks. Required speech review stays a core
capability. Static persona/configuration files are read by the child; mutable
state reads go through the authority. Private plans are not exposed or persisted.

Conversation text, image analysis/conversation, Presence Wake, stream turn policy,
voice roundtrip, core live voice and silent/proactive conversation generation use
this process. Scheduler eligibility, Presence admission, speech pacing and delivery
remain core policy. The existing bounded queue serializes input and preserves its
foreground/background fairness. The child uses the configured provider runtime,
including the persistent inference child when `brain_model_runtime="worker"`, and
provider-level shared admission. Process readiness does not claim model residency.

The live HTTP facade now selects the core pipeline for both former runtime
settings. The old `incremental_experimental` mode is accepted at this facade and
uses the same committed simple response path as its previous compatibility
implementation. The directly instantiated core adapter remains strict by default.
Dashboard LLM/voice smoke commands and the microphone CLI now use
`ConversationClient` to submit text to the authenticated Shana API. Legacy direct
service constructors and the unselected bundled live module remain for injected
tests and offline compatibility. The managed API does not launch that bundled
conversation subprocess or fall back to in-process cognition.

## Protocol and lifecycle

Protocol version 1 uses UTF-8 JSON lines over private supervised stdin/stdout.
Frames are capped at 1 MiB. Envelope validation rejects unsupported versions,
invalid generation/request identifiers, duplicate keys, malformed frames and
nonfinite numbers. No pickle, arbitrary class lookup, object method dispatch,
worker-selected SQL, or state-write endpoint is exposed. Images use the existing
private bounded, hash-checked snapshot manifest, not inline arbitrary file paths.
Each request permits at most 64 callbacks and has a 180-second transport deadline.
Core authority calls retain their execution slot until they actually return.

`GET /v1/brain/status` retains queue fields and adds `worker`, including state,
PID, generation, readiness, active flag, protocol version and safe failure code.
`POST /v1/brain/{start,pause,resume,stop}` uses existing API authentication.
Dashboard `/api/brain/*` proxies retain session authentication; `/dashboard/workers`
has Start, Pause, Resume and Stop controls and shows process failure independently
of API health.

API lifespan attempts worker Start without making API availability depend on
success. Start is idempotent while ready and never starts a duplicate owner. The
worker holds a cross-process ownership lock for its lifetime. Pause rejects new
input and holds queued work while allowing an active decision to finish. Resume
reuses that process. Stop invalidates its generation, cancels waiting queue work,
terminates and reaps the exact child, and leaves API/dashboard running. A blocked
core callback keeps the active slot and prevents replacement until it exits.
A crash is reported as failed; only explicit Start creates a replacement.

Every result is checked against both request and process generation. Short core
commit/grant sections share the generation fence with Stop. Silent and stream
state commits use the same fence, including stream events that do not invoke a
model. Deferred delivery checks the fence before dispatch. A stopped result cannot
commit or publish after restart. Existing state-version, operator invalidation,
public-output policy and grant checks still apply independently. Provider capacity
is not released merely because the brain process exits.

A typed computation rejection leaves the child usable. Conversation rejection
retains HTTP 400, provider/configuration failures retain their existing mappings,
and unsupported mock image analysis retains its compatibility detail. Transport,
crash and obsolete-generation failures return 503 through ingress handlers.
Provider diagnostics and private request data are not returned as failure logs.

## Inspected evidence

`scripts/verify_brain_process.py` launches disposable authenticated API and dashboard
servers with mock LLM and stub STT/TTS. It checks text, stream and legacy-mode live voice, a voice-derived request and
a committed silent cycle in one brain PID; Pause/Resume PID reuse; authenticated
Start/Stop; unauthenticated control rejection; actual SIGKILL; fresh generation;
and successful text after restart. Real dashboard HTML/status and API health remain
responsive while the brain is stopped or failed. Production systemd PIDs are
compared before and after and remain unchanged.

`tests/test_brain_process.py` runs real child processes for duplicate ownership,
crash, stalled transport, obsolete replies, explicit restart, blocked authority
callbacks and late-result rejection. A real SQLite assertion proves Stop between
result and commit creates no outbox decision or delivery grant and fails the
pending exchange. Callback tests prove worker fields cannot replace core identity,
evaluation policy or proposal ownership. `tests/test_brain_wire.py` covers framing,
version/type/identifier checks, duplicates, nonfinite values and byte limits.

The verifier also runs the actual `gamma.run_llm_test` command and checks it
returns a response through the API without replacing the brain PID.

The scoped free-router wire proposal was reviewed and corrected before integration.
Its initial tests missed unterminated frames/nonfinite decode and included two
negative tests that failed for unrelated reasons. Gamma's occupied model endpoint
was left alone. Application tests and the HTTP verifier ran only on Gamma PC.

Validation checkpoint: full suite 1143 passed, 3 skipped, 100 subtests; focused
process/protocol/silent/core-live/stream tests after additional error/fence checks:
47 passed and 6 subtests. Final command results are recorded in the task report. Tests use
fictional data in disposable roots and do not establish real microphone recognition,
GPU latency, audible playback or production deployment readiness. Bounded silent
goal selection and observation handling are now implemented and checked separately
in `modular_worker_runtime_silent_slice.md`. Hardware/GPU and remaining adapter
acceptance retain their separate proposal requirements.

## Re-run

```bash
cd /home/neety/Documents/gamma-modular-phase1
PYTHONPATH=$PWD/src .venv/bin/python -m pytest tests/test_brain_process.py tests/test_brain_wire.py tests/test_silent_preparation.py -q
PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_brain_process.py
node --check src/gamma/dashboard/static/workers.js
git diff --check
```
