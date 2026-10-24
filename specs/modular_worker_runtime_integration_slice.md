# Existing integration worker migration

## Twitch IRC lifecycle

The IRC ingestion worker now uses the common lifecycle controller. Both the
Workers page endpoints and the existing Twitch dashboard Start/Stop endpoints
write the same desired-state record and correlated lifecycle events. Start and
Restart validate IRC configuration before touching the process. Restart targets
only the owned worker process. Automatic restart remains disabled.

A live PID is insufficient for readiness. The worker starts in `starting` and
becomes `ready` only after the IRC server acknowledges its own nickname with
numeric 001 and confirms its own JOIN to the configured channel. Another user's
JOIN and text containing acknowledgement words cannot establish readiness.
Reconnection resets both acknowledgements. Connection failures report degraded
health while the existing reconnect policy runs. SIGTERM requests shutdown and
interrupts the worker's socket and reconnect wait.

The common bounded event log records state changes and lifecycle trace IDs. It
does not include IRC credentials or chat contents. Existing domain logs retain
their established behavior. The worker manifest declares its dependency on
Shana; this slice does not introduce cross-service automatic startup.

`tests/test_twitch_worker_lifecycle.py` starts an authenticated isolated dashboard
and a real supervised IRC child against a temporary local TLS server. It checks
unauthorized control rejection, readiness before and after the worker's own JOIN,
PING/PONG, filtered correlated logs, Restart with a different PID and the prior
child reaped, and Stop. It also exercises the existing Twitch dashboard routes
and checks their shared desired state. All credentials and IRC traffic are
synthetic. The fixture cleans up its own processes and TLS listener.

## Fast stream receipts

IRC ingestion now uses authenticated `POST /v1/stream/jobs`, which returns a 202
receipt after bounded admission to the existing shared brain queue. The legacy
`POST /v1/stream/events` retains its synchronous result contract. Both entry
points use the same Presence and stream-stop epoch checks and ordered delivery.
The receipt completes after delivery preparation finishes; it is not evidence
that a listener played audio or that an external action occurred.

`GET /v1/stream/jobs/{job_id}` returns the durable receipt. The status endpoint at
`GET /v1/stream/jobs/status` reports active count, history count and oldest active
age. `POST /v1/stream/jobs/{job_id}/cancel` cancels a decision still waiting in the
queue. An active decision or delivery remains accounted for until it finishes;
the cancellation response explicitly reports false for active work.

The SQLite store admits at most 32 active receipts and retains at most 4096
records. Admission returns 503 when either limit or the shared brain queue is
full. Accepted request bodies are limited to 64 KiB. Job IDs contain 1 to 128
ASCII letters, digits, underscores or hyphens; `status` is reserved. Only IDs,
payload hashes, owner identity and status timestamps/reason codes are stored.
The ingress store does not retain message text or full model results.

IRC job IDs are derived from channel plus Twitch message ID. Repeated IDs with
the same canonical payload return the existing receipt without execution. A
changed payload returns 409. Request correlation IDs and transport timestamps
are excluded from the hash; other fields and speech/fast-mode flags are bound.
Messages without a Twitch message ID retain a fresh event ID and cannot claim
reconnect deduplication. Completed, failed and cancelled history becomes eligible
for removal 24 hours after its last transition. Deduplication is limited to
retained receipts; older retries must not be assumed safe.

Receipt owners include hostname, PID and process birth time. A same-host owner
proven dead makes its active receipts uncertain. Live, inaccessible and foreign
owners are preserved. Uncertain receipts are never automatically replayed or
removed by retention. This avoids replaying actions whose completion was lost
when the core died. Explicit reconciliation of uncertain receipts remains future
work; enough unresolved receipts can exhaust the history limit.

`tests/test_stream_ingress.py` checks concurrent claims, conflicting payloads,
capacity, terminal-state races, cancellation, retention and owner uncertainty.
Its real HTTP test fills all 32 ingress slots while a brain turn is stalled,
verifies overload rejection, cancels one waiting job and admits a replacement
without releasing the active turn. It also holds a brain turn open while
Twitch's actual normalization
and HTTP client obtain a receipt and deduplicate a second arrival. Health and
cancellation remain responsive. It then kills only the fixture core, restarts it
and proves that the interrupted request is uncertain and does not execute again.
A subsequent new request completes through the real stream pipeline with mock
inference. All traffic and credentials are synthetic.

Live Twitch acceptance, network reconnect fault injection, admission priority
reservations, observation coalescing, EventSub, Discord and Minecraft remain
separate migration work. HTTP forwarding still waits for the short admission
request, with a five-second transport timeout. There is no automatic resend after
an unknown HTTP outcome. No production process is restarted by these tests.

## EventSub queued notifications

EventSub notification forwarding now uses the same fast receipt endpoint as IRC,
with a five-second HTTP timeout and no automatic resend or synchronous fallback.
The HTTP call runs in a thread so it does not block the async event loop. The
notification coroutine awaits that bounded admission call before reading the
next notification. A transport cancellation does not prove that a request was
never accepted; stable IDs and receipt lookup remain necessary after uncertainty.

Notification IDs are derived from the configured broadcaster plus EventSub
message ID. The original message ID remains in metadata for correlation. Missing
IDs retain fresh event IDs and have no reconnect deduplication guarantee. The
existing domain notification count measures successful receipt responses,
including duplicates; it is not a count of executed brain decisions.

The shared queued client rejects request bodies larger than 64 KiB before sending
and validates HTTP 202, the matching job ID, a recognized receipt state and an
exact boolean duplicate flag. Invalid responses are not acknowledgements and do
not trigger retries. Legacy synchronous clients retain their existing endpoint
and result format.

The local WebSocket acceptance fixture sends a notification twice, then a
keepalive, while the real core HTTP fixture has a stalled brain turn. Exactly one
EventSub receipt is admitted and the keepalive is processed. A separate test
holds the HTTP call open and proves that other async work can run. No Twitch
credentials or external connections are used. Common EventSub lifecycle and subscription readiness are implemented below.
Live Twitch reconnect acceptance remains outstanding.

## Ingress wire version

The new queued endpoint requires an integer `schema_version` of `1` alongside the
existing stream event fields. Its admission and lookup receipts also carry
`schema_version: 1`. Both IRC and EventSub clients send and validate this version.
Missing versions, boolean/string substitutes and unsupported integers are
rejected before admission. The synchronous `/v1/stream/events` contract is
unchanged. This change applies to the isolated refactor; no production client has
been switched to the queued contract.

The HTTP test verifies rejected requests create no receipt. Client tests verify
both outgoing version serialization and rejection of missing or unsupported
receipt versions. Receipt versioning is independent of SQLite storage and does
not discard existing durable records.

## EventSub common lifecycle

The common worker inventory now exposes EventSub Start, Stop and Restart. The
existing Twitch EventSub dashboard buttons use that same controller, including
desired state, exact process ownership and lifecycle trace IDs. Configuration is
checked before Start or Restart touches a process.

The child starts with a `starting` heartbeat. It becomes ready only with at least
one successful subscription and no setup failures. Partial subscription success
is degraded. A revocation latches degraded state, so subsequent keepalives and
notifications cannot falsely restore readiness. A new connection clears prior
subscription results before fresh setup. Reconnect/backoff is degraded; fatal
authorization or subscription errors are failed. SIGTERM cancels the owned async
worker task and records stopped state. An HTTP request already executing in a
thread may still finish; shutdown does not claim cancellation of that request.

The common log emits bounded, content-free connection states with the lifecycle
trace. It does not copy credentials, payloads or domain exception text into the
shared log viewer.

`tests/test_eventsub_worker_lifecycle.py` starts an authenticated dashboard and
real supervised EventSub processes. A disposable source copy changes only the
three external endpoint constants to local HTTP/WebSocket fixtures. It verifies
pending subscription readiness, successful setup, forced disconnect and fresh
setup, revocation followed by keepalives, correlated filtered logs, Restart with
old-child reaping, and both common and legacy Start/Stop routes. It cleans up its
own children through the fixture controller. Tests also cover partial success
and absent subscriptions. No live Twitch session or production restart is used.

Server-requested session migration and live Twitch subscription acceptance are
still separate protocol gates. The fixture proves reconnect after an unexpected
socket close, not every Twitch reconnect scenario.
