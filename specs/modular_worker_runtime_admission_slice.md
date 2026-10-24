# Shared model admission and accounting

Status: implemented in the isolated modular checkout, not deployed.
Date: 2026-09-19, Gamma PC.

## Authority and budgets

`resources/shared_ledger.py` owns SQLite transactions for model-work admission.
The default database is `~/.local/state/gamma/admission.sqlite3`, independent of
`settings.project_root` and `settings.data_dir`. Separate runtime roots using this
code and OS account therefore share capacity. `GAMMA_ADMISSION_DB` is an explicit
isolation override for tests or separately managed provider fleets. Giving
callers of the same provider different databases defeats shared admission.

`config/admission.toml` is the portable policy. A persisted policy fingerprint
rejects disagreements between runtime roots. Resource-capacity disagreements also
fail closed. Do not delete or replace a live database to change limits. Policy
changes require a drained, coordinated maintenance migration of every caller;
there is no live policy-edit or force-release endpoint in this slice.

Every real model call demands one `model-work` unit. HTTP calls also demand one
endpoint unit. Operation paths and local hostname/interface/loopback aliases do
not create separate provider slots. Remote DNS aliases and proxy URLs must be
accounted for by a common configured budget; arbitrary external callers cannot
be controlled by this client-side authority.

Budgets are integer dimensions. Each has `capacity` and `reserved` values.
`workloads.<name>.demands` can add dimensions such as `ram_mb`, `gpu0_mb` or CPU
workers for model, safety, STT, TTS and perception work. All dimensions and endpoint
capacity are acquired atomically. Background allocations must fit below each
resource's `capacity - reserved`; interactive work may use the full capacity.
A conflict never leaves partial allocations. `endpoints.<endpoint-key>` can set
an endpoint's capacity and reserved units; the hashed key is visible in status.

The initial policy has one model-work unit, all reserved for interactive work,
and one unit per endpoint. This deliberately rejects background model calls
until additional capacity is measured and configured. It does not disable
non-model improvement observation or silent state-only work. Supported larger
budgets and background/interactive overlap are exercised by the scheduler tests;
they are not enabled on the GPUs by those tests.

Waiting work has a monotonic deadline and a persisted operator-visible expiry.
The default admission wait is two seconds. This is a fail-fast latency policy,
not a prediction of inference duration: measured cold voice work can exceed it.
A busy caller may receive a capacity error rather than wait behind that work.
There are at most 128 waiting requests, with at least 32 positions unavailable to
background traffic. Conflicting interactive waiters precede background waiters;
equal-priority conflicts are FIFO. There is no background fairness claim while
the initial policy reserves all execution capacity.

These budgets account for executing work and configured resource estimates.
They do not evict models, manage Ollama, or pretend that resident VRAM disappears
when inference completes. Observed idle GPU memory and model residency remain
separate resource-routing facts. Memory-demand dimensions require measured model
and device profiles before they can be used as physical memory guarantees.

## Caller audit

| Caller | Admission boundary |
| --- | --- |
| Direct and routed brain LLM calls | Local Ollama HTTP request and hosted adapter; worker child uses those same adapters |
| Safety model review | `safety/review_provider.py` HTTP request, including worker review |
| Vision and model advisers | Shared LLM adapters; no separate direct inference transport was found in vision |
| Improvement analysis, grounding, candidate generation, semantic review and evaluation | Existing call contexts classify these as background; child transport preserves the classification |
| Silent/background brain queue | Context priority survives queue dispatch and model-worker IPC |
| Faster-Whisper | Model construction and synchronous transcription, including iteration of all segments |
| Hosted STT | Concrete STT backend; SDK automatic retries disabled |
| Qwen TTS | HTTP request through the complete response read; provider job ID retained for reconciliation |
| Hosted TTS | Concrete backend through complete streaming file write; SDK automatic retries disabled |
| Piper and RVC | Recorded direct subprocess through `communicate` and confirmed exit |
| Audio perception | Hugging Face inference and lazy model loading; remote audio-understanding HTTP call |
| Stub/mock adapters | No model computation, so no execution budget consumed |

The legacy per-runtime leases remain for worker controls, image retention and
compatibility. They no longer provide the cross-root provider authority. A
worker's explicit `admission_busy` response releases its legacy lease because no
provider work was dispatched. Ambiguous transport failure retains it. Existing
legacy TTS receipt reconciliation remains available alongside global admission
reconciliation.

## Completion and recovery

An active lease never expires because its deadline, heartbeat or client timeout
elapsed. Synchronous in-process compute retains capacity until the call actually
returns. A timed-out caller waiting on another thread cannot free that thread's
allocation. Remote transport failure marks the lease uncertain and retains all
resource units. The router does not fall back or retry an uncertain or
admission-rejected request. Hosted SDK automatic retries are disabled.

Worker request identities become durable deduplication identities at the provider
boundary. Prompt data is hashed where needed for distinct compacted requests;
prompts, audio and credentials are not stored in the ledger. Completed identities
remain tombstones. Replaying an identical worker request after reconstruction
cannot execute it again. Completed/expired metadata currently requires offline
retention management; deleting tombstones loses that deduplication history.

Reconciliation never submits inference. It can release native in-process work
when `/proc` boot identity and PID start time prove the owner has exited, including
PID reuse. It can release a recorded direct model subprocess after that exact
process and its owned POSIX process group exit, or a Qwen request after its matching `/jobs/<id>` response reports
a terminal state. Unknown identities, unreachable status, running jobs and
missing evidence retain capacity. Reconciliation may inspect a live owner's
uncertain request because that call has already unwound; it cannot clear a live
owner's active request while dispatch may still occur.

An Ollama listening process exiting does not prove its runner exited. Neither
`/api/ps` model residency nor an empty client queue proves completion. Without a
request-specific terminal receipt, uncertain Ollama or hosted work remains
unresolved. There is intentionally no TTL or operator force-release shortcut.
The legacy runtime can also remain blocked pending its own receipt evidence;
global reconciliation does not erase unrelated per-runtime leases.

## Operator view

The authenticated workers page displays current allocations and their resource
units, priority, workload, process generation, waiting deadlines and unresolved
leases. `/api/workers` includes `admission` alongside the existing worker list.
`GET /api/resources/admission` reads the same authority.
`POST /api/resources/admission/reconcile` checks evidence without replaying jobs
or restarting services. Both inherit dashboard authentication. The view uses
text rendering for returned values and exposes no prompt or credential content.

## Gamma evidence

Validation used `/home/neety/Documents/gamma-modular-phase1` and
`PYTHONPATH=$PWD/src .venv/bin/python`. Imports resolved to that checkout's
`src/gamma`. Model benchmarks used isolated databases and temporary runtime roots,
with cached weights, generated fictional speech and a synthetic reference voice.
No production restart, deployment, commit, push or machine-local config edit was
performed.

The starting GPU observation was 14,748 MiB used of 24,576 MiB on the RTX 3090 and
5,208 MiB of 8,192 MiB on the RTX 3060 Ti. Residency changed naturally during this
session; these are observations, not reserved headroom. They do not establish a
safe multi-model background concurrency limit.

| Measured component | First request | Repeated request | Other evidence |
| --- | ---: | ---: | --- |
| Whisper base.en, RTX 3090 | 2.216 s including child/model startup | 0.165 s | 498 MiB for the inference PID; zero normalized word error on both fixtures |
| Qwen 1.7B Base synthesis, RTX 3090 | 4.490 s | 2.188 s | Separate server load 7.173 s; 24 kHz non-silent audio; zero word error on both Whisper readbacks |

Both verifiers reaped their children and confirmed unchanged production process
identities. These are component measurements, not hardware microphone, production
voice quality, multi-model saturation or whole-conversation benchmarks. The
one-operation default is supported by those sequential measurements. More GPU
concurrency has not been established and is not enabled.

`tests/test_shared_model_admission.py` exercises real SQLite transactions, eight
competing processes with measured peak occupancy one, four actual LLM client
processes with different runtime/data roots and one provider peak, multiple
budget dimensions, interactive reserve/ordering, timeout with a still-running
HTTP provider, model-worker death, thread timeout, unknown/PID-reused owners,
Qwen terminal receipt recovery, durable duplicate suppression, concrete caller
bypasses, policy conflicts, and authenticated operator HTML/API routes.

Re-run focused admission tests with:

```bash
PYTHONPATH=$PWD/src .venv/bin/python -m pytest tests/test_shared_model_admission.py -q
```

Benchmark artifacts are retained under ignored `data/admission-verification/`:
`whisper.json`, `qwen.json`, and the focused regression log. The existing
`scripts/verify_stt_gpu.py` and `scripts/verify_tts_gpu.py` accept explicit cached
model paths and reproduce the component checks. Supply the same isolated
`GAMMA_ADMISSION_DB` to every child of a test fleet.

Production and the original Gamma/Windows checkouts were not migrated. They will
not acquire these new leases until a separately authorized rollout adopts this
code and the same shared authority. The default shared state location is per OS
account, not a distributed broker across machines or users.

## Final validation record

- Full concurrent-checkout suite: 1,147 passed, 100 subtests passed, three skipped,
  94.38 seconds. Log: `data/admission-verification/full-tests.txt`.
- Final focused suite after the direct-subprocess group-evidence refinement:
  73 passed, 19.36 seconds. It includes 35 admission cases plus model, safety,
  STT/TTS, image-worker and brain-priority regressions.
- The full-run skips were the explicitly opt-in candidate sandbox smoke test and
  two no-GPU tests skipped because CTranslate2 detected a GPU. No hosted API was
  called, and no real RVC quality or hardware microphone test was attempted.
- `node --check src/gamma/dashboard/static/workers.js` and `git diff --check`
  passed. The new untracked core, test and specification files also passed
  `git diff --no-index --check` against `/dev/null`.
- A local Qwen worker proposal for process-generation parsing was inspected and
  adjusted before integration. Its sandbox compile check could not write
  bytecode to the read-only snapshot; the integrated code was tested on Gamma.
  A preceding larger proposal exhausted its output limit and was not integrated.

## Files changed by this admission task

New files:

- `config/admission.toml`
- `src/gamma/resources/shared_ledger.py`
- `src/gamma/resources/shared_execution.py`
- `src/gamma/resources/process_identity.py`
- `tests/conftest.py`
- `tests/test_shared_model_admission.py`
- `specs/modular_worker_runtime_admission_slice.md`

Scoped edits to existing work, including files already untracked at task start:

- `src/gamma/brain/model_runtime.py`, `model_worker.py`, `queue.py`
- `src/gamma/llm/local_adapter.py`, `openai_adapter.py`, `router_adapter.py`
- `src/gamma/safety/review_provider.py`
- `src/gamma/voice/stt.py`, `stt_worker.py`, `tts.py`, `tts_worker.py`,
  `tts_runtime.py`, `worker_client.py`, `audio_understanding.py`
- `src/gamma/dashboard/worker_routes.py`, `static/workers.html`, `static/workers.js`
- `specs/README.md`, `resource_routing.md`, `current_implementations.md`,
  `modular_worker_runtime_progress.md`

Concurrent state and brain-process work was preserved. The full-suite count
includes those tasks' tests; it is not a count of admission-only tests.
