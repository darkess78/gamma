# Brain ingress implementation slice

The independent-process checkpoint in [modular_worker_runtime_brain_process.md](modular_worker_runtime_brain_process.md) supersedes the historical thread-only and opt-in core-live descriptions below.

## Scope

The first shared ingress paths are `POST /v1/conversation/respond`,
`POST /v1/conversation/respond-with-image`, `POST /v1/vision/analyze`, and
`POST /v1/stream/events`. The periodic proactive scheduler submits its complete
eligibility evaluation to the same queue. The API stream brain receives the
same conversation-service instance as the text facade.
`POST /v1/presence/wake` prepares the operator transition immediately, then queues
the opening with the accepted control revision.
`POST /v1/voice/roundtrip` now submits its conversation stage to the same queue
and receives the API's shared conversation-service instance.
Their response schemas and existing conversation-error mappings remain unchanged.
They submit work to one persistent process-owned execution thread. The text
handler waits outside the ASGI event loop; async image handlers await a wrapped
future. They resolve the conversation service inside that worker. No model is
loaded merely by creating the queue or querying status. Image uploads are bounded
by the configured vision byte limit before entering the queue.

This is the ingress seam for the persistent brain migration, not the completed
dedicated brain process or shared model-admission authority. Direct integration
callers and bundled live-voice callers still need migration.
Core live turns, voice roundtrips and text/image conversation routes release the brain queue after
decision commit and grant issuance, then execute delivery separately. The text
HTTP facade keeps its response schema and waits for delivery on the request
thread. Evaluation requests skip delivery. Other compatibility callbacks still
include synthesis when requested and need the same migration.

`ConversationService.respond(..., defer_delivery=True)` returns the committed,
approved response before synthesis or dispatch. `deliver_response` checks that
grant again and performs synthesis and artifact dispatch. The core live runtime
uses this split and reports `synthesizing` or `delivering` after brain execution.
Speech calls through the shared conversation service serialize under its TTS
lock; this is not cross-process resource admission. Text-only delivery does not
wait for that lock. Provider execution retains the live job slot until its actual
thread exits, including when its awaiting task is cancelled. Cancellation revokes
the grant and prevents subsequent publication.

A blocked-provider test proves a second brain job executes during live synthesis,
checks execution-thread separation, cancels the awaiting live task, verifies the
live capacity stays occupied, and confirms no output is published after the
provider exits. A separate decision test verifies durable commit before deferred
delivery and rejection of a grant cancelled before synthesis.
An HTTP test blocks spoken delivery, completes a second text request, verifies
that status remains responsive and the brain is idle during synthesis, and
checks that evaluation requests never invoke delivery.

Image conversation delivery uses a bounded executor with eight outstanding slots.
Cancelling the request revokes its grant. Calls waiting for a thread check
cancellation before invoking the provider; active calls retain their slot until
the provider exits. Overflow revokes the pending grant and returns HTTP 503.
The executor rejects inherited use from another process. This remains local
delivery capacity, not shared provider or GPU admission. Tests block image
synthesis while completing another brain job, verify cancellation revocation,
and prove a cancelled call waiting for a thread never reaches the provider.

Voice roundtrip delivery has eight outstanding slots per service instance.
Cancellation revokes the response grant immediately; its provider thread retains
the slot until execution exits. Waiting work observes cancellation before calling
the provider. This bound is local delivery accounting, not global GPU admission.
Roundtrip compatibility responses still contain inline audio, but those bytes now
come from the checked immutable delivery snapshot, read outside the event loop.
They no longer read the mutable producer path. Migrating that consumer contract
to explicit playback leases remains outstanding.

Roundtrip tests hold a delivery provider while another brain job completes,
cancel the caller and verify capacity remains held, and distinguish approved
snapshot bytes from modified producer bytes. The disposable HTTP verifier also
exercises a synthesized roundtrip through the real route and approved snapshot.

Voice uploads now use a shared bounded copier for roundtrip, transcription, core
live and legacy live job storage. `voice_upload_max_bytes` defaults to 33554432
and can be set with `SHANA_VOICE_UPLOAD_MAX_BYTES`. Copying runs outside the event
loop, uses chunks no larger than 64 KiB and rejects excess data before writing it.
The destination is created exclusively with private permissions. A cancelled
awaiter waits for its copying thread to exit and remove its partial file.
Oversized HTTP voice uploads return 413 before inference. Legacy live turn IDs
now use the core runtime's restricted identifier format before file creation.
Tests cover the exact boundary, overflow, cancellation, existing-file preservation,
route rejection and traversal IDs. This bounds application storage after multipart
parsing; it does not claim a transport-wide request-size limit.
The dashboard live WebSocket also enforces the configured byte limit before
extending its per-turn audio buffer. Overflow clears the turn and sends an
`audio_limit` error; a subsequent valid turn can run on the same connection.
Interrupt-probe audio is checked before recognition. Oversized JSON controls
close the connection with code 1009. Tests cover overflow, subsequent-turn
recovery, rejected probes and control-frame closure.

## Persistent STT process interface

`python -m gamma.voice.stt_worker` provides a version-1 JSON-lines interface over
supervised stdin/stdout. It accepts `transcribe`, `status` and `shutdown` requests
with bounded request IDs and frames. Input files must resolve under the configured
runtime uploads directory and satisfy the voice byte limit. One recognizer is
retained across requests. Results contain transcription text only; this worker
does not run conversation, write memory or publish speech. Provider diagnostics
are redirected to stderr. Initial protocol readiness explicitly reports that the
model is not loaded.

`scripts/verify_stt_worker.py` runs the real child process with a disposable root
and stub recognizer, verifies two transcripts from the same worker PID, checks
loaded status and graceful shutdown. Core-client integration, supervisor controls,
restart/cancellation accounting and real-model readiness are still outstanding;
the application continues to use its existing STT path at this checkpoint.

## Queue contract

The queue supports authenticated `POST /v1/brain/pause` and `/v1/brain/resume`,
with session-authenticated dashboard proxies and controls on `/dashboard/workers`.
Pause returns immediately, rejects new submissions with HTTP 503 and holds queued
decisions. An active call retains its slot and finishes normally. Status reports
`pausing` while that call runs, then `paused`. Resume wakes the existing execution
thread; it does not construct another queue or provider. Shutdown still cancels
waiting jobs and retains active capacity until the call exits. Closed queues
reject pause/resume with HTTP 409. Pause is not a speech mute or a process stop.

The coding-worker proposal was reviewed and integrated with additional shutdown
coverage. Tests exercise a real blocked call and queued successor, unchanged
execution-thread identity on resume, rejection while paused and shutdown while
paused. The Chromium fixture clicks both dashboard controls, verifies HTTP 503
for a new paused conversation, rejects unauthenticated resume, then completes
the real synthetic voice capture/playback checks after resuming.

There is one active callable and at most 32 waiting requests. This conservative
bound prevents unbounded backlog; it is not a throughput claim or a size chosen
from GPU benchmarks. Overflow and shutdown return HTTP 503 with `Retry-After: 1`.
Pending requests cancelled during shutdown also return 503. The queue snapshots
context variables at submission so request correlation follows execution.

`GET /v1/brain/status` reports `state`, `pending`, and `active`, under the existing
Shana API authentication policy. States are `idle` before execution starts,
`running` while the persistent execution thread is available, `draining` after
shutdown while that thread remains alive, and `stopped` once it exits. Running
with `active: false` means the worker is waiting, not that a model is ready.

Application shutdown rejects new queue work and cancels waiting calls. It waits
up to five seconds for active execution, then reports draining if necessary.
The queue and active call remain retained. A subsequent application lifespan
cannot replace a draining queue. A timeout does not release its execution lane.
This is not provider cancellation and does not revoke an already-active turn.
Cancellation of an async request cancels its waiting job when possible; an active
job retains its lane until execution exits. Nested submission from the execution
thread is rejected rather than deadlocking.

Stream ingress takes an independent copy of the input and evaluates current
Presence inside the worker, after queue delay. Sleep or Break set while waiting
therefore applies to that event. Stream Stop advances an ingress generation;
waiting requests from the prior generation return HTTP 409 without calling the
brain. This invalidates waiting stream ingress, not an already-running model
call. Existing delivery cancellation remains responsible for active output.
Scheduler shutdown cancels a waiting cycle; eligibility is evaluated only once
the queue runs that cycle. Queue overload suppresses the cycle until a later tick.

Presence Wake now checks the prepared control revision, mode, audience and output
permissions before generation and again before publishing. A superseded opening
returns `wake.status: suppressed` with reason `presence_changed` without restoring
the earlier mode. Ordinary interaction bookkeeping does not invalidate an opening.
If queue admission fails, the prepared Wake transition remains visible and the
request returns 503; no opening runs outside the queue.

## Presence persistence

Loaded Presence snapshots include a storage `revision`. Writes compare that
revision under the existing cross-process file lock and reject stale snapshots
with `StaleRevision`. Operator transitions hold the lock across read and write
and increment a separate `control_revision`. Reads do not write, except for the
existing stale Go Live downgrade after restart. Proactive bookkeeping discards
stale updates rather than overwriting a newer operator state.

The JSON compatibility format remains in place. Legacy files begin at revision
zero, and explicitly unversioned replacement calls remain supported for fixture
and legacy initialization. Normal runtime read/modify/write paths carry the
loaded revision. Atomic replacement uses a unique temporary file, file fsync and
cleanup on failure. Malformed existing JSON or revisions are not overwritten.
The compact writer came from a reviewed, test-passing local coding-worker proposal.

This is not yet the final core-state request protocol for an independent brain
process. State and output are still separate transactions. The post-generation
Wake check does not make publication atomic with a concurrent operator command;
the target-bound delivery boundary and remaining legacy publication paths still
need migration. Legacy Wake `spoken` reporting also still predates receiver
receipts and must be replaced before claiming complete delivery migration.

## Verification

Queue tests exercise one persistent thread, bounded waiting, failed calls,
queued cancellation, active drain, reentrant rejection, context propagation and
callbacks outside locks. HTTP tests exercise overload and closed-queue 503s,
responsive status while execution is held, both image routes sharing the worker,
upload limits and cancellation without releasing active capacity. Lifecycle tests
reject a replacement queue until the prior execution exits. The disposable
authenticated HTTP/state verifier exercises the real text route, queue status,
state commit and delivery against mock inference.
Additional checks hold execution while Presence changes, invalidate waiting
stream events through the real Stop route, and stop a scheduler with a cycle
waiting behind active work. Route unit tests own their queue lifetimes so test
order cannot reuse a queue closed by an earlier application-lifespan test.

Gamma validation passed 858 tests and 94 subtests, with 3 skipped, including
revision conflicts, failed-write preservation, queued/running Wake supersession,
ordinary interaction updates and read-only status checks.
Roundtrip checks also cover off-loop initialization, shared brain execution,
transcription overload, cancelled waiting recognition, retained active input and
provider failure cleanup. Authenticated HTTP with stub STT committed a voice
conversation and removed the upload. Worker-generated CRLF line endings were
normalized before the clean diff check. The existing
Starlette/httpx and multithreaded-fork deprecation warnings remain. The real
authenticated HTTP/state verifier passed, `git diff --check` passed, the original
tracked worktree diff was unchanged, and production service PIDs remained 2222
and 4615. These checks do not establish real GPU latency or the remaining input
migrations.

The local Qwen and free-router coding jobs both failed to return usable proposals
within their bounded retries. The queue was implemented directly and tested on
Gamma; neither failed proposal was applied.

## Remaining acceptance

### Opt-in live runtime

`SHANA_LIVE_VOICE_RUNTIME=core`, or shared setting `live_voice_runtime="core"`,
selects `CoreLiveTurnRuntime` behind the existing live-turn API. The default stays
`subprocess` while the legacy browser client still expects inline audio chunks.
The new adapter supports `simple_chunked` only, rejects other modes explicitly,
and currently produces one completed approved speech artifact rather than
incremental unreviewed chunks. It never launches a per-turn conversation process.
It reuses the roundtrip transcription stage and shared brain/conversation service.

At most eight jobs remain active, including cancelled calls whose inference has
not exited. Up to 100 job records remain in process memory. Duplicate retained
turn IDs and unsafe IDs are rejected before upload. This is bounded transient
history, not durable idempotency or restart recovery. Generation completion is
reported as job `completed`; playback completion remains a separate receiver
receipt. No inline audio is returned by this adapter.

Presence changes invalidate work between stages. Before publication the runtime
checks the response against the current delivery authority and publishes approved
audio references to the dashboard monitor. Cancellation marks the job immediately,
retains active capacity until execution exits, and revokes late or completed-job
grants. Application shutdown closes admission and requests cancellation of active
core jobs without constructing an unused runtime. Independent worker restart and
shutdown recovery still need the durable job protocol.

Focused tests cover shared execution, duplicate/invalid input, unsupported modes,
denied publication, changed Presence, late-grant revocation and retained active
capacity. Authenticated HTTP with mock inference and stub STT/TTS completed the
new live path, committed state, retrieved approved audio, then received 410 after
job cancellation. This is not real microphone/GPU acceptance or proof that the
legacy live browser client has migrated. The coding-worker job exceeded its
output limit; this adapter was implemented and tested directly.

The voice roundtrip and transcription facades initialize recognizers and run STT
plus audio analysis off the ASGI event loop. One recognizer call runs at a time
per facade, with at most eight admitted transcription jobs including the active
one. Overload returns 503. This conservative local bound is not shared GPU or
endpoint admission across workers. Cancelled waiting jobs skip inference; an
active provider retains its upload until it exits, then the execution owner
removes that file. Cancellation does not pretend to abort active recognition.
The bounded worker proposal was reviewed, then extended with lazy initialization,
serialized recognition and admission before Gamma behavior tests.

The existing roundtrip audio response remains a compatibility transport. It
still needs approved artifact references and receiver playback acknowledgements.
The bundled live-voice subprocess remains the compatibility default. Upload size/streaming
limits, independent STT lifecycle and shared resource admission remain part of
the voice-worker migration. The HTTP acceptance fixture uses a text upload read
by the configured stub STT backend; it does not prove microphone capture, real
recognition accuracy or GPU latency.

Migrate every input path to one typed job protocol, extract independent brain
lifecycle with authenticated core state requests, add asynchronous job receipts,
and enforce deadlines/cancellation at decision and delivery boundaries. Add
priority/fairness and shared endpoint admission across review, perception,
brain, improvement and voice. Implement bounded silent cycles independent of
listeners and test real model cold/warm latency before enabling concurrency.
# Dashboard playback transport

The dashboard now provides session-authenticated `/api/playback/events` and
`/api/playback/audio/{grant_id}` routes. The WebSocket requires a matching Origin
and forwards only receiver capabilities, heartbeats, start requests and receipts.
Its upstream target is fixed to `dashboard_monitor`, with replay disabled.
The Shana bearer stays on the server. Audio fetches reject redirects, cap the
response at 32 MiB and preserve the approved artifact hash and no-store header.
Both directions close when either connection ends.

`scripts/verify_modular_state.py` starts a disposable dashboard alongside the
isolated API and calls `verify_playback_proxy.py`. Real HTTP and WebSocket checks
cover session login, a lease and offer, verified audio bytes, a start permit,
cancellation revocation and subsequent HTTP 410. These checks use synthetic
providers and protocol messages; they do not establish audible browser playback.
The live browser still needs migration before the core runtime becomes default.

The live dashboard socket now announces `turn_submitting` with a generated turn
ID before invoking the remote job starter, and passes that ID to the job. This
lets the browser identify its turn before a fast provider publishes audio.
`test_live_turn_identity.py` checks the announcement inside the starter callback
and checks that the terminal result retains the same identity.
`PlaybackRequests.loadDashboardAudio` uses the fixed same-origin proxy route,
requires a grant and verifies the downloaded bytes using the same bounded hash
check as monitor playback. Node checks cover its URL, cookie mode, absence of a
bearer header, malformed references, hash mismatch and oversized audio.
The live page now opens the session-authenticated receiver before capture and
consumes only core events associated with its announced turn. Early offers have
a five-second expiry and a 16-item buffer. Audio is fetched and hashed before
requesting a start permit. Browser media events report started/completed; errors,
timeouts and interruption have separate receipts. Revocation and disconnect
abort fetches, release blob URLs and stop playback. The monitor on the same page
suspends its audio receiver while live voice owns playback.

`tests/test_live_receiver.cjs` executes the actual live script against simulated
WebSocket and media objects, checking unrelated turns, early offers, permit
ordering, start receipts, revocation and disconnect cleanup.
`tests/test_live_offers.cjs` checks correlation, expiry, duplicate rejection and
buffer bounds.

`PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_live_browser.py` launches
disposable loopback Shana and dashboard applications, then real headless Chromium.
The browser captures a generated microphone stream through the live page's audio
graph, submits it through the dashboard socket and plays an approved 12-second
reply tone from a verified blob URL. The verifier cancels the first grant and
observes stopped playback with an empty audio source, then starts another session
and observes natural completion. It checks SQLite receipts for `interrupted` and
`completed`, rejects page errors and verifies unchanged production service PIDs.
The run passed on Gamma. Recognition and reply generation use explicit synthetic
providers; this does not prove hardware microphone quality, real model latency,
GPU resource admission or nonbrowser adapter readiness.

The browser driver defaults to Gamma's existing Playwright package and Chromium
cache. `GAMMA_PLAYWRIGHT_PACKAGE` can select another installed Node-compatible
Playwright package. The fixture never loads machine-local configuration files.

The persistent STT transport now owns a child process through `STTWorkerClient`.
Requests are serialized with immediate rejection while busy. Protocol frames and
request identities are bounded and checked; malformed replies, blocked writes
and response timeouts terminate and reap the owned child without replaying the
failed request. A subsequent explicit request may start a new child. Graceful
close is allowed only while idle.

Gamma validation passed 900 tests, 3 skipped tests and 94 subtests. Real child
process tests cover reuse, malformed frames, busy rejection, blocked stdin and
timeout cleanup. `scripts/verify_stt_worker.py` also drove the actual worker
module through the client twice in one process and verified that close reaped it.
Recognition used the synthetic stub. Application integration, dashboard lifecycle
controls, real model readiness and GPU admission remain outstanding.

### Persistent STT application integration

`SHANA_STT_RUNTIME=worker` now routes the application's shared transcription
service to one persistent child. Roundtrip, transcription-only and core live
voice use this service. The default remains `inprocess` during migration.
Worker mode accepts local STT providers only. It does not treat terminating a
client as cancellation of inference on a remote server.

Application shutdown closes transcription admission, drains the current owned
operation and closes the child. Waiting uploads are rejected and cleaned up.
The runtime's status read remains available while inference is active.

The real HTTP verifier now runs with worker STT. It checks that roundtrip and
core live requests share the same live child and that API shutdown reaps it.
The service verifier checks repeated uploads and rejection after shutdown.
Recognition remains synthetic. Dashboard controls, structured worker logs,
real-model readiness, GPU admission and independent TTS remain outstanding.

### STT operator controls and telemetry

The workers page now exposes STT Start, Stop and Restart through authenticated
Shana routes at `/v1/voice/stt/*` and session-authenticated dashboard proxies at
`/api/stt/*`. Stop disables transcription and reaps the child. An incoming upload
cannot silently restart a stopped worker. Start enables transcription; Restart
replaces the child. Controls reject an active transcription rather than waiting
behind it. Failed starts leave admission disabled. Application shutdown remains
permanent for that service instance.

Start confirms the process protocol connection, not loaded-model readiness.
The page states that the model loads on the first job. In-process mode exposes
no worker controls. Local provider restrictions remain in force.

The core owner writes bounded, rotating lifecycle and job events through the
existing WorkerEvents implementation. These identify the core producer instance;
they are not child model diagnostic logs. Events include generated trace IDs and
controlled outcomes, excluding input paths, transcripts and provider exception
text. `/api/stt/logs` supports trace, event type and severity filters. The page
provides trace and event type controls. Tests use the actual log writer and reader
to check filtering and content exclusion.

The disposable two-server verifier exercises Stop, blocked transcription, Start,
Restart and filtered logs through the authenticated dashboard. Its Chromium check
clicks the real page buttons and checks trace and job-type filtering. Real model
readiness, child diagnostic telemetry, GPU admission and independent TTS are still
required for Phase 5 acceptance.

### Persistent TTS process interface

`gamma.voice.tts_worker` now accepts bounded, versioned synthesis requests over
an owned process channel. It validates text, emotion and styles before creating
the provider and reuses that provider across requests. Responses exclude speech
text and provider exception details. Audio output must be inside the configured
producer directory. The worker snapshots it into bounded content-addressed files;
the client verifies the path scope, size and digest before returning a TTSResult.

STT and TTS clients share `PersistentWorkerClient`, retaining serialized operations,
PID/request identity checks, bounded frames, write/response deadlines and owned
child cleanup. Existing STT client behavior remains covered by process tests.

`scripts/verify_tts_worker.py` starts the actual child with the synthetic tone
provider, synthesizes twice in one process, verifies both WAV files and observes
process exit on close. Tests also reject malformed input before provider creation,
output outside its directory and modified snapshot bytes.

The TTS application path has not switched to this interface yet. Delivery grants
and publication remain core responsibilities. Integration must preserve checks
before and after synthesis and account for a Qwen server job that may continue
after its adapter dies or times out. No real model or GPU readiness is established
by the synthetic process verifier. Worker artifact retention and operator controls
also remain to be integrated before this path becomes the application default.

### TTS application integration and uncertain resource ownership

`SHANA_TTS_RUNTIME=worker` now selects one shared persistent TTS child for
conversation, Wake and stream synthesis. Roundtrip and core live delivery reuse
that conversation path. The existing delivery authority still checks the grant
before and after synthesis and controls publication. Application shutdown drains
and closes the TTS runtime. The migration default remains `inprocess`.

Worker-mode synthesis acquires a durable SQLite lease before submitting work.
The initial capacity is one TTS operation across API processes sharing the runtime
data directory. Successful verified output releases that lease. Any uncertain
adapter failure leaves it occupied; process death, elapsed time and restart never
implicitly release it. This is limited TTS admission, not the full shared model
and GPU admission system required by the proposal. Legacy in-process consumers
and unrelated external callers do not yet participate.

The two-server verifier uses worker TTS and checks shared child identity across
roundtrip/core live requests, released leases after successful jobs, approved
audio transport and shutdown reaping. `verify_tts_uncertain.py` uses the real Qwen
HTTP adapter against a controlled loopback server. It holds remote work open past
the adapter timeout, observes the child exit and verifies that a new admission
attempt remains blocked by the durable uncertain lease.

Qwen's current service has no request-status reconciliation contract. Recovery
of an uncertain lease therefore remains unimplemented. Do not remove the lease
based on timeout or an uncorrelated health response. Job completion evidence and
operator-facing recovery must be added before worker TTS becomes the default.
Hardware/model readiness, complete admission coverage, artifact retention and
TTS dashboard lifecycle controls also remain open.

### TTS dashboard lifecycle

The workers page now has a synthesis panel backed by authenticated
`/v1/voice/tts/status` and Start/Stop/Restart routes. The dashboard uses fixed
`/api/tts/*` proxies. Status includes process liveness, admission state and control
availability, excluding lease tokens. Start confirms adapter connectivity only.

Stop disables synthesis until explicit Start and leaves text and STT available.
Controls reject active synthesis. Start and Restart reject any existing resource
lease, including uncertain leases; Stop and process shutdown never remove one.
The browser disables Start/Restart while the resource is occupied. The server
independently enforces the same rule.

The live two-server verifier checks unauthenticated control rejection, successful
text and recognition with TTS stopped, failed speech without automatic restart,
and subsequent restored synthesis. Chromium clicks TTS Start, Stop and Restart
on the real workers page and observes a changed child PID. Focused tests verify
that controls preserve uncertain leases and omit their tokens from status.
Uncertain-job reconciliation, TTS lifecycle/job telemetry and full resource
admission coverage remain incomplete.

### Qwen completion evidence

Worker-mode TTS now carries its admission identity through the child protocol and
Qwen adapter as `X-Shana-Job-ID`. The isolated Qwen server implementation records
active and terminal jobs without storing text or audio in the ledger. It rejects
duplicate retained identities before synthesis. The ledger holds at most 256
jobs and evicts only terminal entries; unknown or evicted jobs are not proof of
completion. Each server process has a fresh generation identifier.

The Qwen service adds `GET /jobs/{32-character-lowercase-hex-id}`, returning
schema version, generation, job ID and active/completed/failed/unknown state.
`GET /health` also includes generation. Existing POST requests without a job ID
remain supported. The server remains synchronous, so status calls can time out
while synthesis is active. Such a timeout must preserve uncertainty.

`verify_qwen_jobs.py` exercises the real HTTP handler with a controlled synthesis
function and no model loading. It disconnects the caller during work, observes
active state, then verifies terminal status after the function exits and rejects
a duplicate request without executing synthesis twice. The real adapter timeout
verifier also confirms that the durable lease identity reaches the provider.

This contract has not been deployed to the production Qwen service. Runtime
reconciliation remains pending. In particular, Qwen completion alone does not
prove that later RVC processing has exited. Recovery must account for all active
stages and must not treat unknown state, generation changes or health alone as
terminal evidence.

### Evidence-based TTS reconciliation

Before synthesis, the worker now writes a bounded private receipt containing its
host, PID, process birth time, provider, endpoint and whether RVC is enabled. It
atomically marks the receipt completed only after the full TTS service, including
post-processing, returns. Neither receipt contains speech text or audio. Invalid
request fields are rejected before acquiring a resource lease.

The dashboard's Check TTS completion action calls authenticated reconciliation.
It first proves the recorded worker generation exited. A completed pipeline
receipt then proves all stages returned. For an incomplete receipt, recovery is
currently supported only for Qwen with RVC disabled and a matching terminal job
status from the recorded endpoint. Live workers, unknown/active jobs, unavailable
status, unresolved external post-processing and missing/malformed receipts keep
the lease. Release compares the exact lease identity, protecting a replacement
lease against stale evidence. Reconciliation never retries synthesis and leaves
recovered synthesis disabled until explicit Start.

The real adapter/HTTP verifier now tests recovery while remote work is active
and after terminal evidence arrives. Only the latter succeeds. Focused tests
cover live-worker refusal, unresolved RVC/Piper stages, completed whole-pipeline
evidence, receipt bounds, and replacement-lease protection.

Recovery from incomplete RVC/Piper work, missing receipts and receipts on other
hosts still needs ownership evidence. Receipt/artifact retention and TTS event
telemetry remain outstanding. Production Qwen has not been updated or restarted;
its old API cannot provide the new terminal job evidence.

### TTS lifecycle and job telemetry

The core-owned TTS runtime now emits bounded rotating lifecycle and synthesis
job events through WorkerEvents. Control results include an independent trace ID.
Job traces are generated separately from admission identities. Controlled reason
codes describe failed synthesis, control and lease release. Events exclude speech
text, provider URLs, exception strings and lease tokens. Logging failures cannot
change provider results or release resource leases.

Authenticated `/v1/voice/tts/logs` and session-authenticated `/api/tts/logs` expose
bounded trace, event-type and severity filters. The workers page shows matching
control events after an action and provides TTS-specific filtering and log-loss
information. Real-writer tests check correlation, error severity, reconciliation
failure events, content exclusion and tolerance of unavailable logging.

The two-server verifier checks log authentication and exact lifecycle trace
filtering. Its Chromium driver checks automatic restart-event display, unmatched
trace results and synthesis-job filtering on the actual page. These are core
producer events; separate child model diagnostics remain outside this telemetry.

### Real STT GPU acceptance

The persistent recognizer now reports the actual CTranslate2 model device and
indices after inference. The GPU verifier checks this in addition to the child
PID's NVIDIA allocation, so merely creating a CUDA context cannot stand in for
running Whisper on the GPU.

The first real-model test exposed a missing CUDA 12 loader path. Updating the
process environment inside Python was too late for this path. Owned inference
children now receive the existing CUDA library helper's environment before exec.
The helper includes the installed `/usr/local/lib/ollama/cuda_v12` bundle already
used by repository launch scripts, when present on Linux. No machine-local
configuration or installed package was changed.

`verify_stt_gpu.py` uses a cached local model with network access disabled for
Hugging Face and generates fictional speech with FFmpeg/libflite. On Gamma's RTX
3090, the cached base.en model took 2.114 seconds for the first worker request,
including process/model startup, and 0.144 seconds for the repeated request.
Both transcriptions had zero normalized word error on the fixed 14-word fixture.
The same worker PID reported CTranslate2 CUDA device 0 and a 498 MiB NVIDIA
allocation. Shutdown reaped the child; production Shana/dashboard PIDs stayed
unchanged. These are single-run component measurements, not a machine-cold
benchmark, microphone test or end-to-end conversation latency.

Run on Gamma with:

```bash
PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_stt_gpu.py --model /path/to/cached/base.en/snapshot --gpu 0
```

The verifier requires at least 2 GiB free before the bounded base.en check. This
preflight observation is not a cross-process GPU reservation. Shared admission,
real TTS readiness and hardware capture still need verification.

### Real Qwen GPU synthesis acceptance

`verify_tts_gpu.py` starts a separate loopback Qwen server from cached Base model
weights in a temporary project. It uses Gamma's existing TTS Python environment,
selects GPU 0, disables network model downloads and creates a reference voice
with FFmpeg/libflite. It excludes machine-local configuration and deliberately
selects no saved voice profile. The production Qwen server is not called.

The real persistent TTS client and durable runtime synthesize two fictional
sentences. Verification checks complete PCM frames, duration and signal amplitude,
then runs the generated audio through the real GPU Whisper worker and compares
its words with the requested text. Both leases must be released after success.
Cleanup reaps the test Qwen server, TTS child and STT child even on a failure.
Protected production PID birth times and Shana/dashboard PIDs are checked.

On Gamma's RTX 3090, the isolated Qwen server loaded in 7.076 seconds. The first
TTS request took 4.916 seconds and produced 4.04 seconds of audio. The repeated
request took 2.879 seconds and produced 4.28 seconds of audio. Both 24 kHz clips
were non-silent and both Whisper readbacks matched all requested words. All test
processes exited; production remained unchanged. These are bounded component
measurements with cached weights and a synthetic reference, not production voice
quality, microphone or conversational latency benchmarks.

The verifier requires explicit cached Qwen/Whisper model paths and a TTS Python
path. It checks 8 GiB of free GPU memory and available RAM before launch. As with
the STT verifier, this is a preflight check rather than shared GPU admission.
The RVC stage is disabled in this fixture and remains separately unverified.
## Shared voice admission

Worker-mode STT and TTS now acquire their private resource and one shared
`voice-inference` slot in the same SQLite transaction. A conflict rolls back
every insertion. Release and uncertain-state updates also require the same
owner for every resource and roll back on any mismatch.

An STT failure releases its reservation only when its local inference child is
known to have exited. TTS failures retain both resources because remote Qwen
execution can outlive the adapter. TTS reconciliation releases the recorded
bundle only after the existing completion evidence passes. Restarting a worker
does not clear uncertain capacity.

This is a conservative single-slot execution limit for these two worker paths.
It does not account for model residency or GPU memory, and does not yet govern
LLM, safety, vision, improvement or legacy in-process inference. Recovery of an
STT lease abandoned by a core crash still needs durable ownership evidence.

`test_shared_voice_admission.py` checks conflict rollback, stale-owner rollback,
cross-capability exclusion and STT failure accounting. The HTTP acceptance
script additionally reserves the shared slot from another process, checks STT
and TTS rejection with no partial claims, and verifies text still works.

STT status keeps inference device metadata under `inference_runtime`. The
dashboard's `runtime` field remains the worker mode even after Stop. Browser
acceptance exposed the earlier field collision, which unit tests now cover.

## Explicit finite silent cycles

The existing scheduler now has an independent internal-cycle coordinator. It is
disabled on construction and after restart. Authenticated operators enable a
budget of 1 to 8 cycles with a 5 to 3600 second cooldown through
`POST /v1/autonomy/silent/enable`. `GET /v1/autonomy/silent` reports active work,
remaining budget, completed cycles and a content-free reason code. Disable uses
`POST /v1/autonomy/silent/disable`. The dashboard provides a fixed one-cycle
profile and a Disable button through authenticated `/api/autonomy/silent` proxies.

Cycles execute on the shared brain queue, obey its pause state, and require Awake
or Live Presence, resource readiness and quiet-hours eligibility. They do not
require an output listener and do not consult TTS readiness. Sleep and Break
remain suppressed. Proactive output keeps its existing listener and mute gates.

Inference uses the conversation service's side-effect-free evaluation path with
an unrecognized scheduler identity. It cannot acquire owner memory or tool
permissions through the local-user default. The coordinator commits the bounded
activity summary and structured working state through an owned continuity
exchange. It retains no generated display or speech text, creates no delivery
grant, and records the outbox entry as `internal_only`. It does not feed earlier
generated summaries back as input or pretend the internal event was a user turn.

Disable invalidates staged results and leaves active capacity occupied until
inference exits. Failed attempts consume the finite budget. Results taking 60
seconds or more are discarded; that deadline does not claim remote inference was
cancelled. Presence changes invalidate a staged result. The operator control lock
serializes Disable with the short commit operation.

Remaining work includes per-provider token and execution admission, configured
goal selection and event prioritization, atomic coordination of Presence updates
with other decision/publication paths, and the dedicated brain process. This implementation does
not enable unattended continuous cognition or claim those broader gates exist.

## Interactive and background queue order

The shared brain queue accepts internal `foreground` and `background` priorities.
Request paths default to foreground. The periodic scheduler explicitly submits
silent cycles and proactive evaluation as background work. Each queue retains
FIFO order and both share the same total pending capacity. Foreground work can
overtake waiting background jobs. After at most eight foreground selections,
waiting background work gets a turn so continuous ingress cannot starve it.

Priority never interrupts active inference. Pause, cancellation and shutdown
retain the existing ownership rules for active work and cancel waiting jobs in
both queues. Status and the worker page report each queue's pending count.
`test_brain_priority.py` uses a held execution slot to check actual ordering,
fairness, shared capacity and cancellation rather than thread timing guesses.
Provider scheduling and GPU resource admission remain separate work.

## Presence ordering for silent commits

The final silent-cycle permission check and continuity commit now share the same
cross-process Presence lock used by operator transitions and JSON writers. A
transition that wins first invalidates the prepared cycle. A transition that
arrives during the short commit waits until that commit completes. Model
inference holds no Presence lock, so operator controls remain available while
generation is running. This guard does not make external publication atomic.

The concurrent-process test holds the real database commit boundary while a
spawned operator process attempts the same file lock. The operator must observe
lock contention, then successfully change Presence after the database reports
the cycle completed. Existing stale-control tests cover rejection before commit.

## Wake delivery outside brain execution

The Wake HTTP facade now queues `decide_wake`, then runs `deliver_wake` outside
the brain queue through an eight-slot delivery executor. The compatibility
`PresenceService.wake` method still runs both stages for direct callers. Wake
generation uses the same Presence guard for its state commit, so an operator
transition during inference rejects the stale decision before granting delivery.

Wake responses retain their fields. Generated audio now reports `audio_ready`
instead of `spoken`, and its durable output record is `ready` with `spoken=false`.
Actual playback remains a receiver receipt. Text-only Wake retains `text_only`.
Deferred Wake grants now carry their audience for the common delivery checks.
Delivery checks Presence before and after synthesis, cancels superseded grants,
and suppresses output events if a newer transition arrived during synthesis.

Tests hold a Wake delivery open and execute another job on the actual brain
queue, then apply Sleep and verify no greeting events or durable output escape.
The HTTP acceptance fixture requests Wake with a real WebSocket listener and
checks the approved audio digest and the not-yet-played durable state. Fully
atomic final publication with operator transitions and nonbrowser dispatch
remain separate work; this slice does not claim those are solved.

## Ordered stream HTTP delivery

The stream HTTP facade now queues the decision and safety review on the brain
lane, then waits outside that lane for an ordered delivery future. Decisions
submit delivery in their accepted order. The delivery lane runs one job at a
time with at most eight waiting jobs. A full or closed lane cancels the new
grant; shutdown cancels queued grants while active synthesis keeps its slot.
Restart cannot replace a lane that is still draining active inference.

Required safety review and decision-state work precede submission. Temporary
event context and goal proposals remain in the decision stage. Synthesis,
grant dispatch, output event publication and the final trace run in delivery.
The API checks its accepted stream-stop epoch and Presence control fingerprint
before and after synthesis. A superseded job cancels its grant and publishes
no events. A synthesis lock also protects the shared synthesizer from remaining
direct compatibility callers.

Tests hold the first synthesis, finish a second brain decision, and prove its
output cannot overtake the first. A second case changes delivery permission
during synthesis and proves only the following eligible output is dispatched.
HTTP/WebSocket acceptance verifies the returned stream grant and audio digest.
Direct legacy `handle_event` callers retain synchronous compatibility. They are
not covered by the HTTP lane's ordering guarantee. The periodic scheduler now
uses the same ordered lane as described below. Atomic final publication and
shared provider admission remain outstanding.

## Proactive delivery migration

The periodic scheduler now submits proactive decision work as a background brain
job, awaits the ordered stream delivery future outside brain execution, then
queues outcome accounting back onto the brain lane. Its direct `evaluate_once`
compatibility method remains synchronous unless deferred delivery is requested.
The periodic runtime no longer performs synthesis inside its brain callback.

Each attempt reserves its cooldown timestamp and topic budget before generation.
A failed provider or suppressed output cannot create an unbounded retry loop.
The scheduler records `emitted` only after output events exist. Delivery checks
the scheduler generation, Presence, human interaction timestamp, listener and
target mute before and after synthesis. Stop and restart invalidate older jobs;
waiting jobs can be cancelled, while an active provider keeps its delivery slot
until it exits. General scheduler failures retain a stable reason code rather
than storing provider exception text in Presence status.

Proactive conversation input is journaled as an event and does not reset the
human interaction timestamp. Otherwise its own decision would invalidate its
permission check or postpone the next human-idle interval. State commits and
post-delivery accounting remain under the existing ownership and Presence guards.

Scheduler tests use the actual brain and ordered delivery threads with a held
synthetic provider. They verify continued interactive decisions, delayed emitted
accounting, stop/restart rejection, and failure cooldown. The conversation test
verifies the committed event role and absence of a human-clock update. These are
controlled-provider tests; they do not establish production stream behavior or
complete the remaining atomic publication/provider-admission work.
# Direct text inference process

`SHANA_BRAIN_MODEL_RUNTIME=worker` opts the shared conversation service into a
persistent JSON-lines inference child. The default remains `inprocess`. Only
mock/local/Ollama text providers are supported. With routing enabled, the core
router retains prompt building, route selection and compaction. Its selected
provider receives the final prompt through the same worker runtime. Hosted
inference remains unsupported in worker mode. Endpoint overrides must match
the configured local endpoint or an operator-configured local resource endpoint;
both core and child enforce that list. Prompts cannot supply a new endpoint.

The child constructs its adapter lazily and reuses it. It has no conversation,
state-authority, tool or delivery object. Requests and replies have UTF-8 byte
limits, and escaped JSON frames are checked before acquiring capacity. Protocol
errors expose a fixed reason rather than provider exception content.

Core admission holds a durable `brain-inference` lease until a valid reply.
Timeout, malformed reply or client failure leaves an uncertain lease. Child
death does not establish completion of a remote Ollama job. Automatic recovery
and replay are deliberately absent until there is provider completion evidence.
An HTTP 400/413/422 context rejection is a completed request. The worker returns
a content-free typed rejection, releases capacity and lets the core router rebuild
the prompt within a smaller budget. Other errors retain uncertain capacity.
This initial slot does not yet support hosted inference and does not
reserve GPU memory. Separate project/runtime roots still need an explicit shared
provider authority before claiming host-wide admission.

This is an inference-process migration, not the completed dedicated brain.
Context building, structured decisions and state/tool authority still run in
the core. The workers page exposes Start, Stop and Restart through authenticated
`/api/brain-model/*` proxies to `/v1/brain-model/*`. Controls reject while inference
is active. Stop disables lazy inference restart; Start and Restart reject occupied
leases. Starting the process does not establish remote model readiness. Correlated
content-free lifecycle and job logs support bounded type and trace filtering with
loss visibility. Production configuration and services are unchanged.

The routed-worker test runs a real persistent child against a loopback HTTP
provider. It verifies two prompt-builder budgets, the transmitted model and
rebuilt prompts, context-rejection tracing, released admission, process reuse
and shutdown reaping. This proves the protocol and retry path with a controlled
provider; it does not establish live-model output quality or hosted/vision support.

## Safety reviewer admission

In worker mode, required speech review uses the same persistent inference child
and durable capacity lease as conversation inference. The deterministic policy
and verdict parser stay in the core. A stopped worker, occupied lease or busy
runtime returns an unavailable review without a direct HTTP fallback. Busy
review rejects immediately rather than waiting behind an unbounded mutex queue.
Required-review failure continues to prohibit synthesis and publication.

The review operation preserves its selected model, temperature and 20-second
maximum provider timeout. Responses are limited to 64 KiB before JSON parsing
and 16 KiB of UTF-8 review text. Malformed verdicts block delivery even after a
completed provider response releases capacity. Uncertain transport failures keep
the lease occupied. The in-process compatibility path uses the same bounded
HTTP helper, without claiming shared admission for that legacy mode.

Tests use a real persistent child and controlled loopback HTTP provider to prove
process reuse, request settings, valid and malformed verdicts, and unavailable
review after Stop or an uncertain lease. A StreamBrain test observes synthesis
and dispatch with an allowing reviewer, then neither after Stop. This is not
production-model safety-quality acceptance or a separate reviewer lifecycle.

## Shared adapter factory

The adapter factory now honors worker mode for nonrouted calls as well as routed
calls. Conversation, improvement proposal/grounding/candidate helpers and other
factory consumers therefore use the same durable admission file within one
runtime root. Unknown runtime settings fail rather than constructing a direct
provider. Router callbacks continue to run in the core.

Improvement worker and command-line entry points close their owned inference
client on exit, including exceptions. Library use of the improvement `run`
function keeps lifecycle ownership with its embedding application. Two command
entry points are tested in separate processes against a lease held by the test
process: active and uncertain capacity prevent child launch and remain unchanged.
Normal and failing command exits reap an actually started inference child.
These tests use controlled command bodies and mock inference; they do not run
an autonomous candidate experiment or establish low-priority retry scheduling.

## Local image inference

Worker mode supports configured local vision through the existing VisionService
and LocalLLMAdapter. Core creates private UUID-named snapshots, sends only bounded
metadata over the pipe, and removes its snapshots after completion or failure.
Each manifest contains at most four images, each capped by the smaller of the
configured vision byte limit and 8 MB. Original filenames are not transmitted.
The child checks IDs, MIME types, regular files, sizes and SHA-256 before creating
an adapter. Image payloads never accept an arbitrary path from a request.

The local adapter retains its model capability probe. A confirmed missing vision
capability returns a typed rejection and releases capacity without replacing the
child; subsequent text inference remains usable. Transport uncertainty retains
capacity. Staging errors before submission release their lease.

Tests cover private permissions, tampering, symlinks, manifests, cleanup and
capacity ownership. A real child sends a synthetic PNG to a loopback HTTP provider
through VisionService; the receiver verifies exact image bytes and the capability
probe. It then rejects vision and proves ordinary text still works in that same
child. This does not establish live vision-model accuracy.

## Image retention

Core startup and its existing 30-second maintenance loop scan image snapshots in
batches of at most 64 directory entries. They remove only regular UUID-named
snapshots older than 24 hours under the dedicated image directory. Enumeration
progresses across batches; unrelated names, symlinks, nested and recent files stay.

Cleanup takes a short SQLite transaction that excludes new inference admission.
It runs only when `brain-inference` has no lease. Every active or uncertain lease
protects all snapshots, regardless of age. This closes the check/delete/admit race
without creating a maintenance lease that could survive a process crash. The
guard covers only bounded local filesystem work, never inference or network I/O.

Tests cover retained files, bounded scan progress, core startup/shutdown, a competing
process acquiring capacity, and maintenance-process death. Retention does not
reconcile uncertain provider jobs; snapshots remain protected until separate
completion evidence permits releasing that inference lease.

## Cancellation returns waiting queue capacity

Cancelling a Future that is still waiting removes its entry from either brain
priority queue immediately. This works while the active provider is stalled or
the brain is paused. A replacement can use the released slot without waiting
for that provider to return. Active provider work retains its execution slot.
The queue's cancellation callback runs before caller callbacks, so a failing
caller callback cannot retain the cancelled entry. Existing FIFO order and
foreground/background fairness are preserved.

Tests cover both lanes, pause/resume, callback failure, active cancellation
refusal and a real HTTP ingress queue filled to its admission limit. This does
not yet reserve admission for owner input or implement observation coalescing.
