# Worker lifecycle and log slice

Date: 2026-09-19. Implemented and tested in the isolated Gamma-PC worktree.
Not installed into production. See [baseline evidence](modular_worker_runtime_phase0.md).

## Scope

`/dashboard/workers` provides a common inventory over the existing Shana,
dashboard, audio-understanding, improvement, Twitch IRC/EventSub, and Discord
text processes. The first controllable adapter is the existing improvement
worker. It can start, report readiness and current work, restart, and stop
independently of Shana. It does not create another process manager.

Other cards expose process presence with unavailable readiness telemetry and
disabled controls. Their existing application/integration pages remain usable.
Pause/resume, autostart profiles, queue depth, resource leases, automatic retry,
and the rest of the proposed worker catalogue are not claimed as implemented.
Restart count records explicit restart commands; there is no automatic restart
loop or implied backoff policy.

The supervisor serializes start/stop/restart and module mutations across threads
and processes using an OS lock with a bounded wait. Nested dependency calls can
re-enter it. Process discovery requires an exact module argument and the same
resolved checkout working directory. An isolated checkout cannot adopt or stop
the production checkout's same-named processes. Existing systemd ownership of
the production applications remains unchanged.

Improvement worker starts from CLI, the existing Improvement page, and the new
worker page share the same adapter. A PID alone is not ready. A heartbeat must
match the running PID and postdate its creation. Heartbeat expiry makes the
worker degraded; unexpected exit makes it failed. Generic stop/restart refuses
a known busy worker and directs the operator to Improvement work controls.
SIGTERM lets an admitted job finish; the supervisor does not escalate a slow
improvement shutdown to SIGKILL. An operator sees a `worker-draining` result.
The improvement worker's existing idle timeout remains 300 seconds.

## HTTP contracts

All routes inherit dashboard session authentication. Blocking supervisor work
runs in FastAPI synchronous route handlers, outside its async event loop.

| Route | Result |
| --- | --- |
| `GET /api/workers` | Schema version and manifest/status cards. Unknown telemetry is null or unavailable. |
| `POST /api/workers/{worker}/{action}` | Allowlisted worker/control only; existing result fields plus trace ID and worker status. Unknown or unsupported controls return 400; lock timeout returns 409. An operation failure is an `ok: false` receipt. |
| `GET /api/worker-logs` | `workers` comma list, `event_type`, exact `severity`, `trace_id`, UTC `since`, opaque `before` cursor, `limit` from 1 to 200. |

These are additive dashboard-owned paths. Ports 8000/8001 and public proxy
routes are unchanged. The runtime controls are available while Shana is stopped.

## Logging contract and limits

New producers reuse `JsonLogFormatter`, redaction and correlation context.
Events carry schema version, worker and producer instance, event ID/type,
severity, trace, optional job, duration, outcome and reason code. Supervisor
start receipts propagate a trace and new instance to the child through its
environment. Improvement job IDs correlate `job.accepted`, `job.started`, and
`job.completed` across dashboard and worker processes.

The producer accepts a small metadata allowlist, excludes prompt/transcript,
raw exception and credential fields, and prevents caller fields from replacing
worker identity. Each producer has its own rotating file, 64 KiB plus two
backups. Pruning retains eight closed/terminated generations plus live producers
and the current producer. Active producers never rotate the same file. Storage
failures increment loss counters; an unavailable log sink does not halt work.
Heartbeat status reports degraded logging when losses occurred.

The query scans at most 256 directory entries and 24 files per selected worker,
reads at most 32 KiB per file and 512 KiB per request, returns at most 200 events,
deduplicates by instance/event identity, and ignores incomplete trailing lines.
The response exposes bytes/files read, skipped records, reported loss counts,
and a truncated-window flag. Pagination stays within that bounded retained
window. It is not an unlimited historical search. Loss totals describe the
retained window, not an independently durable lifetime counter. Viewer filters
do not alter producer storage or another viewer's filters.

Existing timing, routing, stream, live-job and supervisor log readers remain on
their existing pages/contracts. They are not silently replaced or claimed to
have been migrated to the new envelope. A separate collector and compatibility
adapters across all those producers remain follow-up work.

## Evidence

The final full Gamma-PC suite passed **731 tests and 84 subtests** in 7.16 seconds.
Three existing skipped tests cover opt-in candidate sandbox smoke and two CPU
fallback cases skipped when CTranslate2 sees GPUs. The existing FastAPI
TestClient dependency emits a Starlette deprecation warning.

`scripts/verify_modular_workers.py` uses disposable state, process-only test
credentials, a loopback dashboard on a temporary port, and the real improvement
worker. It checks:

- Unauthenticated requests are denied and authenticated HTML/API routes work.
- Start reaches heartbeat readiness; concurrent starts and CLI start keep one PID.
- Restart changes PID and generation; stop removes the worker while dashboard
  health remains available.
- A synthetic accepted job is rejected by the real runner's missing-Git-source
  gate before inference, with its accepted/started/completed trace retained.
- Worker/type/severity/trace filters, cursor pages, read bounds and loss fields.
- A deliberately crashed disposable worker is reported failed; the dashboard
  survives and can start a fresh generation.
- Production Shana/dashboard PIDs are unchanged, and disposable processes are
  stopped in cleanup.

Measured start-to-ready was 1091.6 ms; restart-to-ready was 1287.1 ms on one run.
These are control-plane timings, not model-loading performance promises.

Browser verification through a loopback SSH tunnel exercised Start, saw `ready`
and its three correlated lifecycle events, selected an error filter yielding
zero matches, then exercised Stop and saw `stopped`. Disabled controls were
visually inspected and restyled to be visibly unavailable. The temporary
dashboard and tunnel were then stopped. JavaScript syntax and diff whitespace
checks passed.

## Re-run on Gamma PC

```bash
cd /home/neety/Documents/gamma-modular-phase1
PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q
PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_modular_workers.py
node --check src/gamma/dashboard/static/workers.js
git diff --check
```

The Windows directory `C:/Users/darke/Documents/Projects/gamma-modular-phase1`
is a review/edit mirror of Gamma-PC source, not the original Windows checkout.
Two bounded coding-worker jobs were attempted, local Ollama then the configured
free Requesty provider. Both returned truncated responses without usable code.
Neither modified source. Codex implemented, reviewed and tested the fallback;
no paid or Claude worker was used.

## Next phase and limits

This establishes the first Phase 1 vertical slice. It does not move conversation,
memory, TTS, safety, playback, integrations or model admission into the proposed
runtime. Before broadening this inventory to more controllable adapters, add
their real readiness/dependency/ownership contracts and independent tests.
Before Phase 2 state extraction, protect live state with a verified backup and
define scoped generation recovery plus decision/outbox transactions. The
database-wide continuity recovery and existing premature playback approval
identified in the audit remain unresolved by this lifecycle work.

Windows OS-lock behavior was implemented but application tests ran only on
Gamma PC Linux, as requested. Live audio, avatar, GPU cold/warm behavior, and
uncancellable inference admission remain unverified. Nothing was committed,
pushed, copied into the production checkout, or activated in production.
