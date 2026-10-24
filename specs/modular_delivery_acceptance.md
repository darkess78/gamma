# Durable approved-decision delivery acceptance

Worktree: `/home/neety/Documents/gamma-modular-phase1` on Gamma PC.

The producer and consumer migration is implemented. The complete path inventory
is in [modular_delivery_inventory.md](modular_delivery_inventory.md), and the
contract is in [modular_worker_runtime_delivery_slice.md](modular_worker_runtime_delivery_slice.md).

## What changed

- Required review applies to the exact final text, including deterministic
  rewrites and privacy refusals. Timeout, unavailability, malformed responses,
  block and soften verdicts release no output or cached replacement speech.
- Approved outward-facing content and preparation publish in one durable
  transaction. A semantic consumer claim commits before an adapter call.
  Failed or uncertain attempts cannot be retried by changing event identifiers.
- Grants and consumers recheck Presence, audience context, current policy,
  expiry, target controls and cancellation. Clear and mute cancel outstanding
  grants; unmuting cannot revive them. VTube execution checks again after
  asynchronous connection/authentication.
- Browser, core live and CLI playback use receiver-owned grants, offers,
  immutable audio, start permits, revocation and receipts. The old live manager
  delegates to core; standalone legacy synthesis/publication paths reject.
  Roundtrip returns a grant and URL, with no base64 audio. Legacy artifact URLs
  return HTTP 410. Recording stream traces no longer dispatches them.
- `delivery_state=prepared` is separate from adapter attempt `dispatched` and
  receiver receipt `completed`. Legacy grant/outbox status `dispatched` remains
  a preparation compatibility value. Producer events do not establish playback.

## Real acceptance evidence

| Scenario | Executable evidence and observed outcome |
| --- | --- |
| Pending, failed or denied review | `test_delivery_boundary.py` and `test_required_delivery_review.py` hold review open, time it out, fail it, or return malformed/block/soften verdicts. No synthesis, output events or delivery grants escape. Direct privacy refusal follows the same gate. |
| Cancellation during synthesis | `test_delivery_grants.py` holds synthesis with synchronization events, cancels the persisted grant, then releases the provider. The late result is rejected. |
| Crash between commit and dispatch | `test_delivery_dispatch.py` starts real child processes and exits abruptly after decision commit, preparation and an adapter write. Recovery emits nothing; the adapter-write case has exactly one external fixture line and an uncertain attempt. |
| Two receiver ownership | `test_delivery_execution.py` runs a disposable loopback Uvicorn server and two actual WebSockets. A receiver cannot acknowledge the other's lease. Its own start/completion succeeds; cancellation revokes the other offer and makes audio HTTP 410. |
| CLI playback and reconnect | The actual CLI consumer authenticates, fetches verified bytes, obtains a permit and launches an isolated child player. Successful exit has a server-confirmed completed receipt. Cancellation terminates the child and records interruption. Reconnecting cannot consume the same grant again. |
| Adapter delay and duplicate effects | `test_vtube_delivery.py` uses a real local WebSocket peer. Cancellation, Presence, policy or mute changes during delayed authentication prevent hotkey transmission. An approved effect sends once even if an attacker changes its UUID or irrelevant fields. |

All runtime tests use Gamma's repository interpreter with `PYTHONPATH=$PWD/src`.
Fixtures use temporary state, synthetic content, loopback ports and disposable
players. They do not run production speech, contact a VTube instance, or claim
hardware speaker audibility. Final test totals are recorded below.

## Scope and preservation

Existing work in the isolated checkout was preserved. No commits, pushes,
production deployments, service restarts, secrets or machine-local configuration
changes were performed. Both original source checkouts were left untouched.

Discord speech/replies and Minecraft conversational chat remain unimplemented
and disabled. Existing Minecraft command authorization and cancellation are a
separate bounded adapter contract. Hardware audio, avatar lifecycle and lip sync
remain separate acceptance work.

The queue cancellation fixture needed capacity for the documented reserved
foreground slot. Its correction preserves the full-queue rejection assertion.
Deterministic speech-rule unit tests now explicitly omit optional model review;
separate integration tests enforce required review. Neither change weakens
runtime safety or admission.

## Final validation

`PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q --tb=short`

Result: **1252 passed, 3 skipped, 100 subtests passed**, in 120.51 seconds.
Nine dependency/process deprecation warnings remain. The three skips are two
CPU-fallback checks inapplicable because CTranslate2 detects a GPU, and the
explicit opt-in improvement sandbox smoke requiring its Linux/systemd setup.
They are unrelated to the delivery acceptance scenarios.

`node --check src/gamma/dashboard/static/live.js` passed. The Node live-offer,
live-receiver and playback-request tests passed. `git diff --check` passed.
Import verification resolved to this isolated worktree's `src/gamma/__init__.py`.

The full test transcript is retained locally on Gamma at
`/tmp/gamma-delivery-verified-20260919.log`. This report, the inventory and a copy
of that transcript are also provided as local review artifacts in the task.
