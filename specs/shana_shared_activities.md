# P6 shared activities

Status: implementation and connected verification in progress. P6 is not yet accepted.

The owner selected OSRS companionship first, delegated the first-game choice,
and approved temporary activation for testing on 2026-10-27. Minecraft in a
disposable private world supplies the separate bounded action and skill test.
OSRS account control is outside this implementation. The dashboard uses Brave.

## One owner and selected observations

`SharedActivities` uses the existing `Core`, identity, SQLite store and generation
token. Only `Core.review_channel` makes a semantic choice. Observations create
their own sourced records, never invented owner conversation turns. The service
cannot dispatch a tool, practice run, game action or personal edit from a reaction.

An owner selects one labeled application or game source for 30 to 1,800 seconds.
The source has an epoch, increasing frame sequence, finite review budget and
bounded cadence. Switching, stopping, sleep, expiry or restart invalidates the
capture lease. Wake and restoration do not enable capture again.

Each image preserves capture time, content hash and uncertainty. Input admission
requires a capture no more than 12 seconds old, with at most two seconds of clock
skew. An opaque selected-window handle is not a personal memory source. Its
authority comes from enabled state, source epoch and fixed lease expiry. Actual
capture interpretations and authored goals have separate erasable personal sources.

## Captured moments and finite watching

The primary dashboard action is **Share this moment**. It captures one bounded
image from the selected video, admits its actual bytes under the unchanged
12-second input rule, interprets it once, then asks the same Core to reflect or
choose quiet. Changing pixels does not turn that old image into current truth.
The response labels the original capture time and age. Its temporal scope is
`historical_capture`, current state is `unknown`, and action authority is false.
No image interpretation enters the fresh/current observation from this path.

Result acceptance has its own 45-second wall-clock and monotonic deadline. This
is not an extension of world freshness or hard preemption of an already submitted
provider request. Queued work can be cancelled; a late submitted result is withheld.
Source stop, reselect, fixed expiry, privacy
withdrawal, sleep, owner/generation/sequence change, foreground conversation,
changed goal/mode or erased dependencies can still withhold the result. An
unknown provider outcome is retained without automatic replay and disables that
source. No owner conversation turn, tool, game action or personal edit is created.

Watching is a separate, explicit opt-in. The owner selects **5, 15 or 30 minutes**,
with at most **10, 20 or 30 moments** respectively. Starting grants that same
selected window a new finite source lease using its observed `expected_epoch`;
then a separate watch record consumes its own nonrenewable quota. A source
reselection stops the old watch without refilling it. Reusing a Start request
returns its existing watch rather than creating fresh authority.

Only one moment workflow runs at a time. Base sampling gaps are 30, 45 and 60
seconds. A quiet choice backs off to 60 then 120 seconds. Busy or deferred work
waits at least 30 seconds, and completed work leaves at least ten seconds before
another sample. Actual vision, Core semantic and configured safety-review calls use the existing
backend admission once per call with background priority. An explicit manual
moment retains foreground admission. Queued cancellation is distinct from an
already submitted provider request; uncertain provider work is never retried.

An image-free heartbeat confirms only client consent/window availability, for
45 seconds at a time. It cannot update capture metadata, interpretation, source
expiry, watch expiry, quota or observation freshness. The browser sends it every
ten seconds while the actual selected track and private consent remain active.
Stop, page closure, track end or failed connection cancels client timers and
pending callbacks. The server stops on lost availability. Restart, reconnect and
snapshot restore never resume watching. A new owner Start is required.

The Core can receive at most three prior timestamped historical observations
from the same active watch, source epoch, goal and mode. Every source must still
be live. This view is bounded callback context, not a new memory system or proof
of events between captures. Earlier own text is included only after the browser
has inserted matching text into its DOM and sent a typed SHA-256 receipt.
`consumer_reported_received` proves neither human reading nor hearing. Retrying
an acknowledgment never resends text or regenerates a response. Unreceived own
text is excluded. Every prior source actually used becomes a dependency of the
derived result, so erasure clears dependent personal sources and both moment and
Core caches, including a result that finishes after forgetting. Prior context is
also capped at 6,000 encoded bytes, and shortened own text is labeled an excerpt.
Authored identity edits are separate dependencies from the opaque window handle.
Forgetting identity-only provenance erases its dependent own text while keeping
the independent window/watch consent, original expiry and consumed count. The
next capture in that same watch uses current canonical identity. Capture, goal,
source and privacy withdrawals retain their stronger source-revocation behavior.

After its quota is consumed, a watch is `exhausted`: it cannot sample or renew
availability. Only its final completed generated moment can obtain a text receipt
within that moment's original acceptance deadline and remaining source/availability
lease. The UI finishes this receipt, then closes the watch. Explicit stop, privacy
withdrawal, reselect, sleep, expiry, forgetting or restore revokes terminal delivery
immediately. Lost acknowledgment retries record once; they do not append text or
submit inference again. Independent image captions remain separate from operational
own text. The post-review-erasure hook clears dependent own-text caches even for
legacy rows with incomplete source dependencies or missing review bindings.

Moment records fail closed at 4,096 and watch/Start records at 1,024. Each watch
has at most 30 captures; prior context contains at most three views. Replay
fingerprints remain after expiry, erasure or copied-state invalidation. Raw image
bytes are not archived. These mechanics do not establish interpretation quality.

## Advanced current-view diagnostic

The older current/live path remains separate. Its 12-second freshness rule is
checked before and after inference, and changed pixels invalidate pending
meaning. It is under **Advanced: current-view diagnostic**, not presented as a
working continuous animated-game companion.

The dashboard's **Test a live view** sends a newly captured full frame every
two seconds only while interpreting and responding, for at most 45 seconds.
There is one main request and at most one refresh request. Completion, privacy
withdrawal, a new goal, page closure or timeout stops those requests. A timeout
also stops the selected source. No browser permission is automated.

Exact server-verified identical image bytes can renew a view while the original
perception or Core review is pending. Each receipt retains its real capture
time, sequence, source epoch and SHA-256. The original interpretation retains its
own original capture, inference times and personal source. Its text is never
copied onto changed pixels. A change and return to the original pixels still
invalidates pending work. The 12-second deadline applies to the latest actually
received identical frame; it is not extended for inference. Changed animated
game scenes therefore still need separate real-play acceptance.

One perception and four frame callers are admitted concurrently. Each source
lease permits at most 240 full frames (120 by default), and receipt fingerprints
fail closed at 4,096 rather than dropping old replay protection. Same-pixel
frames arriving faster than 0.2 seconds do not renew freshness. Even a changed
frame arriving too quickly invalidates the old meaning. Raw frames remain only
in request/inference memory. Forgetting the original source erases linked frame
metadata and derived replies while retaining replay fingerprints. Restored
snapshots cannot restore frame or reaction authority.

Owner-reported scene summaries remain `owner_reported_observation` sources.
The image path hashes actual submitted bytes and passes them to an explicitly
configured analyzer in memory. It stores the bounded interpretation, not raw
screenshots. An absent analyzer produces an honest unavailable result. Unknown views
retain uncertainty; a bank classifier is not evidence of a drop, death or tactic.

An authored shared goal is separate from an experience. Ordinary private Core
context can recall it and the fresh selected observation. Privacy removal clears
derived observation/reaction text and the goal, retaining only no-replay hashes.
Hideable avatar, sleep and selected screen access remain different controls.

## Private Minecraft actions and measured trials

`SharedMinecraft` wraps the existing Minecraft coordinator. Its backend must
explicitly attest a private disposable development world. It does not choose an
endpoint/account, download or start a server, connect an OSRS account, or restore
movement. The ordinary protocol, owner authorization, clear flat terrain envelope,
sidecar stop behavior and emergency latch remain in effect.

An explicit permit lasts at most 120 seconds and admits at most 12 commands.
Commands have at most 20-second deadlines. Follow leases cannot exceed 20 seconds;
look commands cannot exceed three seconds. Unknown actions, arbitrary coordinates
and method names are rejected. A result of uncertain delivery is retained without
automatic replay. Revocation requests emergency stop.

A local coordinator timeout is not an observed world result. Only an accepted,
typed terminal arriving through the authenticated WebSocket receive callback
gets `received_sidecar_terminal` provenance. Sent, acknowledged, local terminal
and received terminal are separate states. Independent observation of actual
world consequences remains part of the real acceptance test.

`ArrivalSpacingTrials` freezes baseline, practice and held-out variants before
execution. Each partition needs at least two scenarios. Variant hashes cannot
overlap, and baseline/held-out families must match. A backend environment probe
must attest the selected variant before an attempt. Browser or model claims
cannot provide that attestation. Each scenario has one attempt; unresolved effects
block further work.

The narrow learned parameter is the `come_here` arrival distance, from two to four
blocks. Follow commands intentionally stay active until their lease expires;
they do not return a reached-owner completion. They therefore cannot supply the
completion-rate metric used here. Practice tries a finite set of
distances, selects the best observed completion rate, then freezes that choice
for held-out variants. Held-out outcomes never feed back into selection. The
comparison reports its exact counts and one bounded improvement flag. This is
not weight training, general game competence, a developed play style, or proof
that remembered advice improves mechanics.

Copied-state restoration invalidates source/action authority, pending reactions,
pending game commands and environment probes. Goals and private history can
survive compatible restoration. Request fingerprints prevent replay.

## Evidence recorded on 2026-10-27

The historical moment/watch package passed 378 affected Gamma tests and nine
subtests in 21.89 seconds, with one existing Starlette deprecation warning.
This includes 18 controlled Node DOM cases and the actual configured app draft
closure through `ConversationService`, `PrivateTurnAdapter` and `SpeechSafetyFilter`
using controlled HTTP providers. Exactly three inference calls (vision, semantic,
safety) inherited watch background priority; manual sharing retained foreground
priority. Real admission queues proved stop/foreground cancellation before safety
HTTP without disabling review. SQL checks proved identity erasure, late-return
cache erasure and absence from the next same-watch prompt. Quota exhaustion,
final receipt/lost acknowledgment and explicit withdrawal passed separately.
These are connected software checks, not real-model, Brave, hearing or game tests.
The source and worker review packet is `data/coordination/p6/moment-watch/`.

An earlier combined activity/Minecraft focused suite passed 178 Gamma tests,
with one existing Starlette deprecation warning. The separate offline world
packet checks passed five tests and four subtests. Python compilation, Node
syntax checks and no-network self-tests also passed. Real child processes proved
CPU/file limits, targeted shutdown and safe cleanup of a temporary dependency
reference. These are mechanical checks, not real Minecraft movement evidence.

An actual local-model run exercised the authenticated dashboard, HTTP proxy,
shared Core and reaction commit against explicitly owner-reported OSRS scenes:

| Case | Observed result |
| --- | --- |
| Unchanged fishing activity with focused owner | The model chose silence in 9.228 seconds. |
| Reported Fishing level 70 matching shared goal | The checked text was `Seventy. Finally.` after 13.936 seconds. The service withheld it because the observation exceeded 12 seconds. |
| Withdrawal during an actual pending model review | The result was cancelled without text. |

The run lasted 42.297 seconds. Its selected source hashes remained unchanged and
its temporary host exited. No real OSRS window was captured, no physical output
was assessed, and no game action ran. The receipt is
`data/coordination/p6/connected-20521ebfbbcb4975984aecbe66a59bb1/report.json`
on Gamma. Windows has `data/coordination/p6/actual-model-report.json`.
The executed source archive SHA-256 is
`03357fe618b072d543b9abbffae474a635b454bc95a9a9f98bf782bb26d721b1`.
This pins the earlier code, before the later channel serialization changes.

A real configured `LocalLLMAdapter` also read an authored, in-memory 640×360 PNG
containing `TEST PANEL`, `BANK OPEN`, `COINS 42` and `DETAIL ?`. This was a
synthetic software panel, not an OSRS screen. The original one-frame run correctly
withheld a 14.506-second Core response after an 8.186-second perception step.
Its unchanged failed receipt remains
`data/coordination/p6/selected-image-73bce6cc0e3941dc96e87a06c2fb0326/report.json`.

The subsequent full-frame renewal run passed the connected path. Exactly one
actual image dispatch interpreted all four labels in 5.984 seconds. Ten newly
generated full PNG frames arrived at approximately two-second intervals during
perception and the 14.578-second Core review. Each contained 2,952 actual bytes
with the same server-verified hash. The original capture remained
`1793098027.7400203`; the newest actual capture was `1793098047.9050825`, and
the reaction committed at `1793098048.3188028`, only 0.414 seconds later.
The 12-second freshness limit stayed unchanged.

The committed text was: "Forty-two coins, huh? At least the bank is open. Not
that it matters if the rest of it is a guess." It refers to visible labels and
the explicitly unknown detail. Its dismissive tone still needs the owner's
assessment. It establishes neither useful OSRS companionship nor play quality.
The 22.941-second run preserved identity, stopped its source and exited its owned
temporary host. Selected Python hashes were stable; the executed archive SHA-256
is `1261c9db26df7b5de6024e3cbd2b6724edf2fc05fdc5d40f22294c3b4ede7cd9`.
Gamma's receipt is
`data/coordination/p6/selected-image-ec1215cedbf54604b41730f12d640eb4/report.json`;
Windows has `data/coordination/p6/actual-renewed-selected-image-report.json`.

The final renewal-focused checks passed 158 tests and four offline packet
subtests, including seven controlled Node lifecycle cases, all 13 existing
adversarial cases and copied-snapshot frame invalidation. These are controlled
DOM checks, not visual Brave acceptance.

## Remaining acceptance

The changing-frame diagnosis proved the earlier live path withheld every
controlled animated-frame reaction at the measured perception/review timing.
The replacement historical moment/watch contract is implemented with controlled
providers; real-model and owner-selected-window acceptance for this new contract
remain pending. Prior static-PNG renewal evidence below does not accept animated
OSRS companionship.

Finish current focused and integration checks, then verify an owner-selected
OSRS window through the actual configured analyzer and dashboard controls.
Review useful reactions, silence, uncertainty and shared-goal continuity during
real play. The actual first model timing demonstrates why freshness must stay
separate from a syntactically valid response.

No server JAR, `server.properties` or `eula.txt` was found in the bounded
candidate/main `sidecars/minecraft` and `data` inventory. A real action test needs
an existing disposable Java 1.21.11 offline world, or separately approved setup
with the owner's license acceptance. On 2026-10-27 the owner explicitly chose to
leave real Minecraft testing pending. No installation, license acceptance or
real game action is authorized by this technical packet. Do not label protocol fixtures as a real
world. Run the frozen trials with independently observed consequences before
claiming improvement on new variants. The owner's assessment of companionship
and play remains necessary. Selected-screen observation and companionship work
remain active; they were not part of that deferral.

The concrete automated test packet is `scripts/verify_shared_minecraft_world.py`
and its `.cjs` owner observer. Its ignored review packet is
`data/coordination/p6/minecraft-packet/REVIEW.md`. It requires exact owner-supplied
server/Java artifacts and an explicit owner license/test receipt. No download
code is present. The metadata lookup was blocked by automatic approval review
and was not retried. The download URL/hash remain unresolved rather than guessed.

## Optional speech for a captured moment

The Activities page has an explicit, default-off **Listen to new moments** control
and a local **Stop speech** control. Listening requires the private-audience
confirmation and an active selected window. It does not enable the microphone,
change global voice/speech settings, replay earlier moments or publish output.

After inserting a new completed reflection into the DOM, the browser reports its
exact text hash. When listening, that receipt also binds the actual private
consumer generation and lease. The typed authenticated
`POST /v1/coordination/channel-audio/moment/render` route and its dashboard proxy
accept only audience, consumer ID and moment ID. The backend joins the exact
completed canonical review, capture, selected source epoch, goal/mode, identity
and prior-source dependencies. Caller-supplied text or origin records are rejected.

The first audio claim must pass the original 45-second wall and monotonic moment
guard and use that same consumer's text receipt. Its fixed delivery grant lasts
no longer than 180 seconds and the existing review, selected-source and original
watch expirations. It never refreshes capture time, watch availability, budget or
source permission. After acceptance, public moment lookup closes the text
acceptance view without revoking an otherwise valid finite audio grant. The last
exhausted watch moment can finish only its already accepted grant; it cannot
start another capture or obtain new authority after its acceptance deadline.

The implementation uses the existing P5 synthesis lock, chunk renderer, WAV
integrity checks, consumer/output epochs and media receipts. Speech that exceeds
the existing whole-text or four-chunk limits is explicitly unavailable, with no
truncation or second model call. Busy speech is skipped. A claimed, failed, partial
or uncertain request is never automatically synthesized again. Stop, privacy,
source/goal/mode changes, foreground conversation, sleep, erasure, reconnect and
owner/generation/sequence changes revoke delivery; source controls and erasure
also unlink released owned WAVs. Local Stop pauses and clears media before
waiting for the backend. Bytes already received by a client cannot be recalled.

After natural watch exhaustion, another explicit **Share this moment** click may
grant one new image and review for 180 seconds on the still selected live window,
using its observed source epoch. This stops prior speech and does not refill or
resume the old watch. A new watch also requires a fresh explicit Start.

Controlled Gamma checks cover the actual Core, authenticated two-app HTTP,
canonical joins, same-consumer receipts, source/consumer races, post-acceptance
delivery, media ordering, uncertain outcomes, no duplicate synthesis, erasure and
source-grant controls. The finite `scripts/verify_moment_audio.py` verifier is
inert by default and requires `--run-real` for an authored-image and actual local
model/Qwen HTTP proof. That proof is separate from OSRS, physical Brave playback
and human listening acceptance.
