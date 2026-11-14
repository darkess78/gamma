# Shana private streaming rehearsal

Status: private technical rehearsal verified on 2026-10-27.
Actual private HTTP text and generated WAV receipt paths are verified.
Public co-hosting, browser media playback, user-assessed quality and meaningful
four-hour hosting are not accepted by this technical slice.

## Work claims and received commentary

The recorded four-hour run completed its technical criteria, but its semantic
review found unsupported promises and completion claims. One contribution said
it would add tests without an admitted edit. Later contributions treated that
promise as a completed mixed-input probe and claimed code-block and nested-bracket
exclusion which the actual line-based extractor did not implement. Another
contribution said JSON output was missing while the complete supplied event
already contained that output. The artifact was not changed to match these claims.

Received commentary now carries fixed `assistant_commentary` and
`consumer_reported_text_received_only` provenance, `execution_evidence=false` and
an explicit partial-text marker. Only the last six bounded rows enter context.
The stream projection states that this review admits and dispatches no work.
These fields describe the transport receipt; they do not judge whether a quoted
sentence is true or read a private task database.

Current activity events can also expose a bounded complete JSON array as
`current_work_observations`. It retains literal reported task names, artifact
hashes, status/check labels and probe outputs, before earlier commentary. Its
fixed provenance is `caller_supplied_current_event_data` with
`execution_evidence=false`. A supplied status of "passed" grants no trust or
authority. `output_field_present` records whether the source JSON included that
field; false, zero, empty arrays and null remain distinct supplied values.
Arbitrary chat/lull JSON is not projected. Malformed, duplicate-key, nonfinite,
unsupported or deep data stays unknown, not missing or failed. The selector
accepts at most four records, eight probes each, 1,200 input characters,
256 structural nodes and twenty levels; reported outputs have separate
twelve-level and 2,048-byte bounds.

The channel-only turn contract distinguishes proposed steps from admitted work
and checked results. It prohibits execution promises from an observation, limits
correctness claims to the supplied artifact and concrete probe inputs/outputs,
and keeps omitted results unknown. Earlier commentary cannot corroborate itself.
Ordinary owner, private-voice and desktop-text prompt bytes retain their existing
frozen hashes. There is no output-text regex, second grounding model, changed
safety filter or added work authority.

Three deterministic regressions first failed against the old projection and
contract. The scoped fix passed 59 actual-Core, renderer and stream tests,
including the frozen prompt checks. These tests check prompt/context wiring,
provenance, delivered-history and absent work effects. They do not prove that an
arbitrary false model response is mechanically rejected.

The first four-case actual configured-Core follow-up collected all responses in
60.399 seconds, with stable source and released ownership. It stopped making
execution promises and the lull case stayed quiet, but one response still
substituted old metrics task names for the supplied notes task receipts and
called supplied JSON missing. That counterexample motivated the current-data
projection above. A cautious statement that caller data is not independently
verified remains valid. Two further context regressions ran red before this
projection; the affected stream, phone and prompt suites then passed 171 tests.
The next actual four-case replay collected in 62.727 seconds. It still answered
one current notes event with stale metrics/Unicode commentary, although the
actual primary request contained the correct current task labels and outputs.
Both negative real runs remain in the review packet.

For a current activity event with supported records, the prompt now omits prior
assistant commentary text which has no binding to those reported artifacts.
Receipt metadata and an explicit omitted-not-absent count remain. Stored history,
current caller data and chat/lull history are preserved. Actual primary-request
tests first reproduced the leak through reconstructed and delivered-Store history.
They now show those old sentences absent through all JSON escaping layers. A
separate current metrics record deliberately containing an old sentence remains
unchanged and unverified; no word filter removes legitimate supplied records.
The affected suites passed 174 tests, including frozen owner/voice prompt hashes.

The final four-case actual configured-Core replay collected in 52.225 seconds
with stable source, released ownership, four primary calls and two normal safety
calls. Events 15 and 18 chose silence. Event 22 suggested a general empty/whitespace
edge check without claiming it ran. Event 25 described the supplied notes and
notes_filter empty-input and pending/done probe outputs. It made no execution
promise, invented completed probe, missing-JSON claim or stale metrics substitution
in this sample. Its redundant next-step question remains an owner quality-review
item. This bounded correction does not accept the earlier four-hour hosting or
guarantee arbitrary model wording.

The final report is
`data/coordination/finish-20261030/stream-grounding/omitted-commentary-real-report.json`,
SHA-256 `0561a0bbf955770e3fdac10994adf4f0311df0ed6f1548ea9422bf6e3c912325`.
Its source archive SHA-256 is
`4c026b43069122529e86faa8d1405f71a297f2ac02588683e75f178a33c83382`.
An independent read checked every one of its 341 members against the recorded
source hashes. The immutable source pin is
`a6a3a728c894b91ba535c6d8f32d7170c80399cadacc9cc2ab4db8836b854056`,
with the required project-root marker separately pinned and retained.

The self-contained authored reconstruction is in
`tests/fixtures/stream_grounding_replay.json`. It retains the recorded current
events and last-six received-text excerpts, but original ACK timestamps and full
original model prompts were not retained. It is not a byte-identical prompt dump.
The default-inert `scripts/verify_stream_grounding.py` can collect four bounded
actual configured-Core responses for independent semantic review:

```bash
PYTHONPATH=src TMPDIR=/tmp .venv/bin/python scripts/verify_stream_grounding.py \
  --run-real-model --output /absolute/candidate/data/coordination/fresh-grounding-review
```

This direct-Core replay keeps the normal local adapter, admission and safety
review, disables background loops, uses fresh isolated state and caps provider
dispatches at twelve. It records source pins/archive and actual primary-request
grounding flags. Its responses require independent review; collecting them is
not stream HTTP delivery, hearing, browser acceptance or a new four-hour result.

## What works

`StreamSession` reuses the current Core, identity and generation. The same
`Core.review_channel` makes semantic choices inside the owner-selected plan.
The channel review cannot dispatch work, alter personal records, call tools or
grant public publication. Viewer text is an observation, never an owner command.

The owner chooses a gameplay or coding format, a broad plan, a finite duration
up to four hours and at most 480 decisions. The service supports focused quiet,
owner-away and owner-return controls. Returning shows only consumer-reported
received contributions, with unreceived output kept distinct. An expired session
cannot renew its original budget by reconnecting.

Twitch's existing chat sanitizer removes bot input and converts known injection
or spam into category descriptions. Rehearsal facts select only the admitted
plan, event, focus/presence and six bounded received contributions. These facts
do not establish a safe public publishing system. The actual recipient remains
the authenticated private owner.

## Delivery and recovery

The dashboard's typed `/api/coordination/stream/*` routes proxy to the separate
owner API's `/v1/coordination/stream/*` routes. Both applications retain their
authentication. No new listener, proxy rule, Twitch connection, OBS controller,
performer consumer or public publisher is installed by this slice.

Each event is bound to its content fingerprint. It must be fresh within 30
seconds, including at decision return. Only one decision and one undelivered
contribution can be active. Foreground work wins. Mechanical cooldown and a
rolling speech allowance prevent model calls during known delivery constraints.
Speech duration is an estimate from text, not measured audio evidence.

An active consumer has a 45-second heartbeat lease. `next` reserves an output
before returning it. A lost response has an unknown outcome and is not replayed.
The consumer can acknowledge started, delivered or failed; delivered is its
report of receiving text and does not prove human hearing. Stop, disconnect,
lease expiry, sleep, a changed foreground sequence and owner restart fence
pending decisions and old output. Model work already submitted can finish, but
its invalidated result cannot be released.

Copied-state transfer disconnects all leases, stops active sessions, erases
transport output text and cancels pending output/events. Historical delivered
statuses and counts remain historical receipts. Event fingerprints remain, so
an old request does not restart inference. A fresh explicit plan is required.

Personal source erasure also reaches dependent transport caches in the same
transaction. Each contribution binds to its actual checked channel review; older
rows resolve through their stored event fingerprint. When the existing personal
forget policy revokes that review, its stream text and cached owner-return
catch-up are removed. Checked audio text and owned WAV files are removed
immediately, including chunks that were already generated. An in-flight provider
may finish, but its late bytes cannot be released. Delivered/played history,
counts and no-replay fingerprints remain; unrelated canonical turns are retained.
Both recent-context selection and delivery independently validate the source, so
a redacted or unbound contribution cannot return through a later `_recent` view.

This closes a reproduced cross-phase privacy bug: a forgotten authored identity
fact had survived in delivered stream text and entered the next Core review.
Eight new real-Core erasure cases cover persistent caches, cached catch-up,
legacy bindings, generated and played audio, exact WAV unlink, in-flight
synthesis, unrelated canonical history and both authenticated HTTP applications.
The combined affected suites passed 161 cases on 2026-10-27. Red and green logs,
source hashes and the reviewed worker helper are retained under
`data/coordination/p7/source-erasure-review/` and `erasure-worker/review/`.

The service retains at most 32 session records, 512 events per session, 480
outputs per session and six contributions in model context. Reaching a bound
produces a visible refusal. It does not silently reset limits or erase evidence.

## Dashboard review

Use Brave at `/dashboard/rehearsal`. Confirm that the screen is private, choose
an idea and format, then start or connect to an existing rehearsal. The page
supports away/return, focused quiet and stop controls. Stop remains available
while a model response is pending. Examples are clearly labeled rehearsal input;
they do not pretend to be received Twitch events.

The client polls only after an explicit private connection. It acknowledges text
only after the actual feed element contains it. A privacy change clears the feed,
disconnects, stops the session and fences late results. A late start is cleaned
up before a new connection attempt is allowed. Uncertain sends are not repeated.
These client guards have nine independent Node contract checks. They do not
replace the separate visual and interaction review in Brave.

## Checked private speech

The optional listening checkbox starts off. After private confirmation and a
stream connection, it can send a canonical stream output ID to the checked audio
bridge. The bridge loads the actual completed `channel_review` which produced
that output. It binds the event fingerprint, checked text/context hashes, both
consumer leases, session revision, owner generation, foreground sequence and
local listening consent. It creates no owner message or personal experience.

The existing P5 interaction instance owns synthesis, bounded PCM chunks, audio
files, fetching and playback acknowledgments. No second voice service, speech
queue or model is created. Commentary yields when existing synthesis is busy and
allows at most one pending utterance per consumer. Already checked fresh text can
finish its admitted utterance within the shared review/session deadline, capped
by P5's 180-second delivery window. Source changes are rechecked after each chunk
and on every fetch/acknowledgment. Erasure clears dependent operational text and
audio. Stop, sleep, reconnect, consent loss and foreground changes fence late
provider bytes. Global voice and speech settings are not toggled by this client.

Text shown in the feed uses the existing stream delivery receipt. Audio uses
separate `started` and `played` receipts from media events, serialized in order.
Stop speech turns local listening off, aborts pending media requests and pauses
the current audio immediately. Busy, partial and uncertain synthesis is not
requested again automatically. A played prefix remains a historical playback
receipt after a later provider failure, while unplayed output is cancelled.

The checked bridge and P5 regression suite passed 127 cases, including 33 new
actual-Core source, interruption and queue checks and two full HTTP/snapshot
checks using controlled PCM providers. Ten additional JavaScript media-race
checks pass, alongside the nine text-flow checks. These do not establish browser
media playback, physical hearing or natural voice quality.

A second owner's microphone consumer can recognize currently active checked
stream speech from the same Core as echo. This guard requires the actual checked
source and both live leases. It does not match arbitrary consumer history. A
revoked source during recognition cancels intake before creating an owner turn.
Intentional exact repetition during active speech and uncertain cross-device
acoustics still need supervised acceptance.

## Checks

Run on the isolated Gamma checkout with the repository virtual environment:

```bash
PYTHONPATH=src TMPDIR=/tmp .venv/bin/python -m pytest \
  tests/test_stream_session.py tests/test_stream_session_policy.py \
  tests/test_stream_session_routes.py -q
node --check src/gamma/dashboard/static/stream_rehearsal.js
node tests/stream_rehearsal_browser_contract.cjs
```

The finite real-model replay uses both authenticated applications and the local
configured Qwen model. It drives both formats, reads actual HTTP text, submits
receipts and checks focused quiet, owner return, reconnect and cleanup:

```bash
PYTHONPATH=src TMPDIR=/tmp .venv/bin/python scripts/verify_stream_session.py
```

The replay uses authored event fixtures. Its result is not a live Twitch or OSRS
session, a physical listening result, a public delivery result or a four-hour
hosting result. The report pins actual source bytes before and after execution.
Source changes during verification prevent a passing connected receipt.

The recorded real-model replay completed in 78.272 seconds with matching source
hashes and its host stopped. It received three text contributions across the two
formats. A gameplay lull stayed quiet; a coding lull continued the selected
topic. The latter began "Good point" without a new viewer point, an ordinary
wording limit retained for later quality review. The same fresh identity owned
all four reviews. The report and exact executed source are retained under
`data/coordination/p7/connected-ae18c2d682f1429990a5ccd5dab17190/`.
The focused service, budget and authenticated-proxy suite passed 44 tests.

The actual configured voice verifier completed in 40.080 seconds, with identical
before/after source maps and its host stopped. It used the existing
`qwen_shana_dataset_xvec_soft` voice and actual local Qwen primary reviews for
both formats, with two additional safety-generation calls. Authenticated HTTP
consumers received three PCM WAV chunks. These are declared HTTP delivery
receipts, not browser `played` events or proof of human hearing. Measured
event-to-first-WAV delays were 15.061 seconds for gameplay and 15.682 seconds for
coding. The shorter first-audio-ready field measures synthesis only.

The coding run stopped 0.175 seconds into its second actual provider call. That
call returned 3.925 seconds after stop; the late WAV was withheld. Reconnecting
left listening off and did not synthesize the old output again. Both channel
reviews shared one fresh identity, created no owner turn and left the global
voice/speech switches off. Source, exact executed Python, small generated WAVs,
chunk text/WAV hash joins and independent timing audit are retained under
`data/coordination/p7/actual-audio-f6519d216aa2425a91110fed67edfcf2/`.
Run the finite verifier only in an allocated inference/synthesis slot:

```bash
PYTHONPATH=src TMPDIR=/tmp .venv/bin/python scripts/verify_channel_audio.py
```

The coding reply suggested null-return and mixed-delimiter handling without a
supplied parser contract. Those are unverified design suggestions, not established
project behavior. Semantic quality and the owner's preferred voice remain open.

The requesty worker attempted the isolated speech-budget helper on 2026-10-27.
It returned no usable content at its output limit. No proposal was integrated,
and no paid fallback was used. Codex implemented the helper independently and
ran its boundary tests on Gamma. The worker result and review are retained under
`data/coordination/p7/worker/`.

The local ollama worker's strict checked-text helper passed its three protected
tests. Codex reviewed its source hashes, result and proposal before integrating
the equivalent helper and exercising the real Core paths above. Its reviewed
proposal is retained under `data/coordination/p7/audio-worker/review/`.

## Remaining acceptance

P7 still needs supervised sessions with appropriate contributions, comfortable
quiet, callbacks and privacy, followed by longer owner absences and a meaningful
four-hour session. Uptime and continuous speech do not establish hosting quality.
Actual browser audio playback and the owner's assessment remain necessary. Public launch
requires a concrete destination and separate publication approval. The private
rehearsal API rejects public targets.
