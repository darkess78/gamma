# Delivery authorization implementation checkpoint

This checkpoint is in the isolated Gamma-PC worktree. Production services and
the primary checkout remain unchanged. The durable approved-decision delivery
slice is implemented. The acceptance evidence below is isolated from production; physical audio and avatar readiness
remain separate operational checks.

## Implemented behavior

Conversation generation no longer calls TTS. The compatibility facade first
commits its owned decision, then invokes the delivery synthesis helper. A stale
or failed decision never reaches synthesis. Presence Wake uses the same order,
with an `event` journal input so runtime events are not attributed to the user.
It does not create automatic user memory candidates.

StreamBrain requests a decision without synthesized audio. It reviews both display
and speech text before passing approved speech to the delivery helper. The old
parallel-review path, which allowed playback while review was pending and tried
to interrupt unsafe speech afterward, has been removed. Timeout, invalid verdict,
unavailable reviewer and denied review cannot release the proposed output. Voice
controls can disable synthesis while allowing reviewed text output.

The reviewer no longer converts malformed or missing actions into approval.
Missing models, invalid verdict schemas and nonfinite confidence values fail
closed. The `none` style profile no longer skips privacy checks, configured hard
rules or required LLM review. It still skips optional heuristic style filtering.

The shared synthesis helper clears stale audio, requires a persisted delivery grant
before provider use, preserves safety metadata, and records `delivery_state=synthesized`.
That state is not a playback receipt. A small coding-worker proposal supplied
the helper after three stdlib tests passed; Codex reviewed and integrated it.

Grants bind the exact display and speech text, emotion, styles, motions, delivery
mode, target, audience, policy fingerprint, process generation and expiry. They
are checked before and after synthesis and once before dispatch. Review policy
changes, including fallback model selection, invalidate outstanding grants.
Cancellation persists in SQLite; stream stop advances target epochs and cancels
outstanding grants. A process inheriting an authority cannot use its parent's
grants. The authenticated `/v1/delivery/{grant_id}/cancel` route is idempotent.
Generated decisions must already be committed before a grant can be issued.
Core canned responses without a decision trace remain an internal compatibility
path.

Audio dispatch now takes a bounded private snapshot under the state database's
`delivery-artifacts` directory. The artifact record binds the grant to SHA-256,
exact byte length, content type and review policy. A producer overwriting its
original file cannot change the approved snapshot. Modified, appended, empty,
oversized, symlink and nonregular artifacts are rejected. The read endpoint
returns the verified bytes themselves, avoiding a second file open after checking.
Grant cancellation, expiry, target epoch, process generation and review policy
are checked again on reads. HTTP responses use `Cache-Control: no-store`.

Stream events carry their grant ID. The performer adapter converts approved audio
to `/v1/delivery/{grant_id}/audio`; receiver offers verify the snapshot, target
and URL binding. Speech-ended events no longer carry a redundant audio reference.
The compatibility response retains the producer path and adds the approved URL
and digest in TTS metadata. The final migration rejects ungranted producers and returns HTTP 410 for the old
artifact URL. Active consumers use the grant and receiver protocol.

Performer WebSocket connections now have separate durable receiver leases. Audio
offers are tied to the lease, event ID, payload digest and expiry. Receipts follow
`offered -> started -> completed`, with failed and interrupted terminal outcomes.
Only the owning live connection can acknowledge its offer; completion before
start is rejected. Disconnect closes the lease and interrupts pending receipts,
including during task cancellation. Heartbeats renew a live lease, but cannot
revive one that expired. A replacement process cannot use old leases.

History replay keeps presentation context but strips audio. The sending path
checks the receiver's current audio readiness and mute state. Both monitor and
performer require an Enable Audio gesture, clear queued speech on disconnect,
and report browser `playing`, `ended` and error events. Muting or clearing output
reports interruption. This is separate from the core grant's dispatch status;
receipts from migrated stream events reference a grant-verified audio offer.

Both browsers now load approved audio with the configured bearer header, verify
its SHA-256, and request a short-lived start permit over their owning WebSocket.
The shared loader limits downloaded bytes, rejects redirects and releases blob
URLs on completion, interruption or failure. An offer alone cannot acknowledge
playback start. The server checks current grant, lease, offer expiry, Presence,
audience, audio readiness and mute/clear epochs before returning a five-second
permit. A permit cannot be requested twice. Sleep and Break deny browser audio;
public targets additionally require the current Go Live output permission.
Clearing a target or muting then unmuting cannot revive an earlier queued offer.
Browser cancellation also aborts an in-progress audio fetch or permit request.

The WebSocket now revalidates queued and playing receipts every half second while
connected. Grant cancellation, expiry, target epoch, policy, Presence, audience
and receiver readiness changes mark obsolete receipts interrupted and send an
event-specific revocation. Clients stop that item, release its media and remove
its queued request without cancelling unrelated event IDs. Expired receiver leases
close the connection. A late completion cannot turn an interrupted receipt into
a completed one. Per-receiver pending admission is capped at 128 offers, bounding
the revocation scan; this does not replace historical record retention.

Core lifecycle maintenance now runs a bounded retention batch at startup and on
the existing 30-second recovery tick. Delivery records retain a default 24-hour
history. Recent or active playback receipts preserve their grants and artifact
references. Expired receipt/offer pairs, unreferenced expired grants and empty
expired leases are removed in batches of at most 256 each. Snapshot enumeration
also advances by at most 256 directory entries per tick instead of rescanning the
whole directory. Only old, unreferenced regular snapshots with the owned hash
filename pattern are removed. Symlinks, unrelated names, fresh files and producer
files outside the dedicated root remain untouched. Counts and errors are logged;
cleanup failures defer retention without preventing core startup.

Dispatch holds the snapshot lock through database binding, preventing cleanup
from deleting an old deduplicated snapshot being reused. Indexed retention queries
support the bounded batches. Missing indexes follow the verified schema-backup
path before creation. Cleanup does not touch decision journals or tool outboxes.

## Earlier checkpoint evidence

The Gamma-PC full suite passed with 819 tests, 3 skips and 84 subtests. An event-
controlled test holds review open and proves neither synthesis nor dispatch
occurs until approval. Slow rejection, malformed review, synthesis suppression,
failed-decision and Presence Wake journal tests exercise the actual boundaries.
The disposable HTTP verifier also exercises the compatibility speech facade
with stub TTS. These checks do not establish actual device playback or lip sync.
Additional tests hold synthesis open while cancelling, change content and policy,
expire grants, reject a replacement generation and fork a real child process.
HTTP checks confirm cancellation authentication, persistence and repeat behavior.
Two simultaneous test WebSockets prove receiver isolation, ordered completion,
disconnect cleanup and reconnect suppression. Real monitor and performer pages
played a two-second synthetic silent WAV through HTMLMediaElement and produced
durable completed receipts. The performer's initial autoplay rejection produced
a failed receipt; the new Enable Audio gate was then verified with a completed
receipt. This verifies browser playback lifecycle, not speaker audibility or
lip-sync quality. The disposable preview server and SSH tunnel were stopped.
Artifact tests verify producer-file changes, snapshot tampering, substituted URLs,
target stop, expiry, changed review policy and replacement generations. The real
authenticated HTTP verifier fetches the exact approved bytes and then confirms
that cancellation changes the same audio URL to HTTP 410. A local coding worker
supplied the snapshot helper; review corrected its missing appended-byte check
and added bounded regular-file reads before integration.
The start-permit checks include actual WebSocket requests after Sleep, audience
changes, target clear and mute/unmute. Node tests cover request correlation,
timeouts, cancellation, authenticated audio loading, hash mismatch and oversized
downloads. The real monitor and performer were verified against a disposable
server that rejects audio requests without the fixture bearer header; completed
receipts prove the authenticated fetch and permit exchange reached browser
playback. JavaScript syntax checks passed for both scripts and performer inline
code. The new request helper began as a reviewed local coding-worker proposal.
Ongoing revocation tests exercise active WebSockets with no further client request,
as well as cancellation, changed policy, target epochs, late completion and queue
admission. Both real browser clients were observed playing a 20-second synthetic
clip before its grant was cancelled. Each then paused, cleared its source and
persisted an interrupted receipt before the clip's natural end. This proves the
browser stop behavior, without claiming instantaneous stop across a network.
Eight focused maintenance checks passed, including shared-reference preservation,
active and recent terminal evidence, bounded progress, file-error reporting,
startup scheduling, concurrent dispatch binding and index-backup migration. The
disposable authenticated HTTP acceptance script passed with maintenance enabled.
The scanner worker returned a truncated proposal; the integrated scanner was
implemented and validated directly instead.

## Producer status contract

The performer turn store and monitor now report `ready` when producer events
announce audio or finish synthesis. Producer `speaking` updates normalize to
`synthesizing`; producer `completed` updates normalize to `ready`. These events
cannot prove playback, even when named `speech_started` or `speech_ended` by a
legacy producer. The monitor labels this value **Production state**. Actual
browser playback remains recorded separately in connection-owned durable
receipts; this change does not aggregate receiver receipts into whole-turn
completion. Late producer events cannot resurrect an interrupted, cancelled or
failed turn in the store. Explicit store transitions retain their existing API.

The focused Gamma suite passed 128 tests and 74 subtests, including no-listener
producer events, delayed events after cancellation, bus serialization, playback
receipts, proactive scheduling and dashboard routes. Monitor JavaScript syntax
and `git diff --check` passed. The coding-worker proposal failed its sandbox
compile command because bytecode output targeted a read-only directory; review
also found unrelated metadata edits. Only the scoped behavior was implemented
directly and verified with the application dependencies on Gamma.
The full regression suite subsequently passed 823 tests and 94 subtests, with
3 skipped. Existing Starlette/httpx and multithreaded-fork deprecation warnings
remain. Both production service PIDs and the original tracked worktree diff
were unchanged after validation.

## Operational limits

The producer/consumer migration is enumerated in
`modular_delivery_inventory.md`. Active decision consumers require current grants;
legacy raw-path, base64 and artifact-URL delivery paths are retired. Review failure,
denial and timeout release no fallback speech. Recording a stream trace does not
dispatch it. Target clear and mute invalidate outstanding grants, so unmuting
cannot revive prepared output.

Browser start permits are bounded distributed permissions. Cancellation cannot
retract audio already played before the stop arrives. VTube hotkeys are discrete
commands and cannot be undone after an acknowledged send; queued hotkeys recheck
approval after connection/authentication and cannot be replayed. Physical speaker
audibility, production voice profiles, avatar animation/lip sync and live external
integrations remain outside this fixture-based delivery acceptance. Unsupported
Discord speech and Minecraft chat transports remain disabled.

## Durable dispatch acceptance, September 19

Preparation now stores only approved outward-facing response fields in
`gamma_delivery_payload`. Private summaries, tool results, memory candidates and
arbitrary provider metadata are excluded. `response.tts_metadata.delivery_state`
is `prepared` after publication, for text and audio. Publication rows are `ready`
and `spoken=0`. For compatibility, the grant and decision-outbox status named
`dispatched` still means that this preparation transaction committed; consumers
must use the dispatch-attempt table to establish actual adapter invocation.
Producer completion cannot turn the publication into confirmed playback.

Each consumer claims a stable semantic event key in `gamma_delivery_dispatch`
before invoking its adapter. The unique decision, target and consumer key rejects
reconstruction with a fresh event UUID. Adapter return records `dispatched` or
`failed`; neither status is a playback receipt. A crash after claiming leaves an
uncertain outcome, which recovery marks `uncertain` without retrying. Recovery
cancels dead-generation publications and grants. It never loads old payloads into
an executor. Connection-owned `completed` playback receipts remain the separate
record of confirmed browser playback.

Payload retention follows the expired grant batch and retained playback evidence.
Dispatch claims retain only identifiers, consumer keys and status with the
existing decision journal, so deleting expired approved payloads does not remove
the no-retry record. This slice does not introduce journal retention.

`tests/test_delivery_dispatch.py` starts real child processes that exit abruptly
after decision commit, preparation, or an adapter write. Reopening and recovering
the SQLite state never replays output. The adapter case retains exactly one
external fixture write and an uncertain claim. Concurrent claim tests admit one
consumer only. Retention tests remove approved material while keeping the claim.
`tests/test_delivery_execution.py` reconstructs event UUIDs and changes text,
target, audio URL, cancellation, Presence and policy before the consumer boundary.
All invalid variants leave the bus and dispatch journal empty.

A disposable loopback Uvicorn server with two actual WebSocket clients verifies
exact snapshot bytes, no-store HTTP, legacy artifact HTTP 410, connection-bound
permits and receipts, rejected stolen lease acknowledgements, cancellation-driven
revocation and cancelled audio HTTP 410. The server closes at fixture teardown.
This is protocol and HTTP evidence, not physical speaker or avatar evidence.


The CLI acceptance invokes `ApprovedAudioPlayback.play_response` against that
real loopback server with API authentication enabled and a synthetic fixture token.
Unauthenticated audio requests receive HTTP 401. The CLI obtains its own offer,
fetches approved bytes, requests a permit and runs a real cancellable child player
that checks those bytes without using an audio device. Normal exit produces a
completed receipt. Cancelling the grant while the child is running terminates it
and leaves an interrupted receipt. Temporary downloaded audio is removed in both
cases. A replacement connection cannot replay the already consumed grant.

The nonbrowser VTube adapter checks permission after connection and authentication,
immediately before sending. A real local WebSocket fixture delays authentication,
then changes cancellation, Presence, policy or mute state and observes zero hotkey
requests. A valid approved expression sends once; changing irrelevant payload
fields or the event UUID cannot authorize another send. Producer speech events do
not mark the adapter as speaking. These tests exercise transport behavior without
requiring a physical VTube Studio installation.
