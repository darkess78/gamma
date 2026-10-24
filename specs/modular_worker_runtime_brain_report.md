# Brain worker implementation report

Implementation worktree: `/home/neety/Documents/gamma-modular-phase1` on Gamma PC.
Original Gamma and Windows checkouts were not edited. Existing and concurrent
state/admission changes were preserved with baseline hashes, three-way review and
contextual integration. No commits, pushes, deployment, production service restarts,
machine-local configuration edits or secret changes were made.

## Result

Managed text, image, Wake, stream policy, roundtrip/core-live voice, silent cycles
and proactive generation cross a versioned private worker protocol. Context assembly,
structured decisions, response realization and tool intentions execute in one
persistent child. Persistence, resolved permissions, required safety and delivery
remain with the core authorities. Dashboard smoke commands and the microphone CLI
send conversation input through the API.

Start, Pause, Resume, Stop, readiness and failure status are independently exposed
through authenticated API/dashboard controls. Stop invalidates unfinished work and
reaps only the owned child. Restart is generation-fenced and cannot overlap another
worker for the runtime root. Active core callbacks retain their slot until they
return. Late replies cannot commit a decision or create a delivery grant.

Protocol and lifecycle details: `specs/modular_worker_runtime_brain_process.md`.

## Changed files for this task

All paths below are relative to the implementation worktree. Several existing files
also contain preserved work from the other authorized refactor tasks.

- New protocol/runtime: `src/gamma/brain/authority.py`, `process.py`, `worker.py`, `wire.py`.
- Conversation and context: `src/gamma/conversation/service.py`, `src/gamma/conversation/client.py`, `src/gamma/persona/loader.py`, `src/gamma/stream/brain.py`.
- Application compatibility/lifecycle: `src/gamma/api/routes.py`, `src/gamma/main.py`, `src/gamma/voice/core_live.py`.
- CLI input compatibility: `src/gamma/run_llm_test.py`, `src/gamma/run_voice_roundtrip.py`, `src/gamma/run_voice_mode.py`.
- Dashboard: `src/gamma/dashboard/worker_routes.py`, `src/gamma/dashboard/static/workers.html`, `src/gamma/dashboard/static/workers.js`.
- Verification: `scripts/verify_brain_process.py`, `tests/test_brain_process.py`, `tests/test_brain_wire.py`.
- Documentation: `README.md`, `specs/README.md`, `specs/architecture.md`, `specs/current_implementations.md`, `specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md`, `specs/modular_worker_runtime_brain_slice.md`, `specs/modular_worker_runtime_brain_process.md`, `specs/modular_worker_runtime_progress.md`, this report.

## Commands and results

All application execution used Gamma PC and the implementation worktree virtualenv.

```bash
cd /home/neety/Documents/gamma-modular-phase1
SHANA_PROJECT_ROOT=/tmp/gamma-brain-suite-20260920 \
SHANA_DATABASE_URL=sqlite:////tmp/gamma-brain-suite-20260920/data/memory/fixture.db \
SHANA_LLM_PROVIDER=mock SHANA_LLM_ROUTER_ENABLED=false \
SHANA_SPEECH_FILTER_LLM_ENABLED=false \
PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q
```

Result: **1143 passed, 3 skipped, 100 subtests passed**. Full output is retained at
`/tmp/gamma-brain-full-final.log`. The three skips are the explicitly enabled
candidate sandbox smoke test and two CPU-fallback tests that do not apply when
CTranslate2 detects a GPU. Existing Starlette/httpx and multithreaded-fork warnings
remain.

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest \
  tests/test_brain_process.py tests/test_brain_wire.py \
  tests/test_silent_preparation.py tests/test_core_live.py \
  tests/test_stream_ingress.py tests/test_stream_delivery.py -q
```

Result after final error/fence checks: **47 passed, 6 subtests passed**.
The final protocol/process rerun after bounded retrieval adjustment passed
**28 tests and 6 subtests**.

```bash
PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_brain_process.py
node --check src/gamma/dashboard/static/workers.js
git diff --check
```

All passed. HTTP evidence is retained at `/tmp/gamma-brain-acceptance-final.json`.
The final fixture used API PID **753941** and ended with restarted brain PID
**754169**. Both were disposable processes and were shut down by the verifier.
Inspected assertions cover:

- Actual dashboard smoke CLI submits to API and reuses the brain PID.
- Text, voice-derived input and a committed silent cycle use the same child.
- Stream input and legacy-mode live voice use the shared worker.
- Pause/Resume reuse its PID and paused ingress returns 503.
- Dashboard controls require authentication.
- Real Stop and SIGKILL leave API health, dashboard HTML and dashboard status responsive.
- Explicit restart uses a new PID/generation and accepts subsequent text.
- Production systemd service PIDs are unchanged.

Real-process tests separately cover duplicate-owner rejection, obsolete reply IDs,
hung transport, callback drain, nonreplaying restart and a real SQLite assertion
that a stopped late result produces no outbox decision or delivery grant.

The Gamma coding-worker proposal declined because its model endpoint was occupied.
A free-router wire proposal was reviewed and corrected before integration. Its
initial passing tests missed important framing cases; the corrected tests ran
independently on Gamma. No paid fallback or production model interruption occurred.

## Remaining limits

The new acceptance uses mock LLM and stub transcription/synthesis. It proves actual
process boundaries, HTTP compatibility, authority behavior and failure recovery.
Real microphone capture, model-quality/latency measurements, audible playback,
production configuration and deployment were not exercised by this task. Existing
legacy direct constructors and the unselected bundled live launcher remain for
tests/offline compatibility. Autonomous goal selection, observation coalescing,
new adapters and nonbrowser delivery remain separate proposal phases.
