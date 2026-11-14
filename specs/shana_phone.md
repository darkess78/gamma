# Shana on the phone

Status: P8 implementation under technical verification. Android was selected by
the owner on 2026-10-27. The owner suggested a mobile-friendly dashboard rather
than a separate app. Phone access therefore uses the authenticated dashboard,
with a responsive page at `/dashboard/phone`. It uses the existing Shana owner,
personal history, work and sleep state. It does not run a phone agent.

## Everyday use

Sign in to the dashboard in Brave on Android. Confirm that private output is
appropriate, then select Connect. Device registration and reconnection happen
inside that authenticated session. No token needs to be copied by the owner.
Conversation uses Monitor's `monitor-private` session. The Work link opens the
existing task results. The phone stores no conversation history or private
database. Its browser session retains a device access key and, when needed, one
unresolved request identifier.

Manage browser connections is available after privacy confirmation, even before
connecting a device. It lists safe labels and last-seen state. The signed-in
owner can revoke a lost browser key without knowing that key, freeing an active
device slot. Revocation clears pending transport payloads and invalidates the
old epoch. It preserves conversation history, commitments and request fingerprints.

Availability and reminders are in a collapsed settings section. Reminders start
disabled. Enabling them grants up to eight hours of considered contact, at most
three times a day and at least fifteen minutes apart. Busy, quiet, focused work,
sleep, an offline device or expired authority blocks a new offer. The shared
Core considers an unfinished owner commitment. A timer does not decide what to
say, and the transport cannot invent a reminder or mark a commitment finished.
An optional explicit schedule only makes a commitment eligible for consideration.

An accepted offer has separate ready, offered, browser-received and acknowledged
states. Got it ends that reminder exchange while the underlying commitment stays
unfinished. An uncertain delivery is not automatically resent. Checking a
pending input reads its canonical saved state and does not repeat the input.
Reconnects and owner restarts preserve the same identity and accepted work.

## Privacy and operation

The existing dashboard session authenticates the owner. Each device also has a
random access key, stored only as a hash on Gamma, and a changing connection
epoch. Secrets use JSON request bodies, never URLs. Browser privacy loss clears
the private view, aborts pending requests and disconnects the device. A late
reply cannot reappear. The connection lease also expires when heartbeats stop.

Notifications are optional, generic browser alerts while this page is open.
They contain only "Shana" and "You have a private update." No message text is
placed in a notification. An explicitly enabled notification-only service worker
supports the mobile notification API. It has a fixed phone-page destination and
no push subscription, network handler, content cache or background brain. A
failed alert does not disconnect chat. There is no closed-phone delivery or
native Android application. Leaving or hiding the page disconnects its private
consumer. Actual Android permission, lock-screen and reconnect behavior still
need a physical-device check. See the [Android review procedure](shana_phone_review.md).

Canonical source removal physically erases matching derived reminder text in the
same transaction when `redact_sources` is called by PersonalState. Reads also
check the canonical source and revision. Copied-state restore revokes device
keys, disables contact authority and clears phone message text. It retains
request fingerprints and reminder/acknowledgment history, and requires fresh
device registration. It does not delete canonical commitments or conversations.

Storage is bounded to eight active devices, 256 retained device records, 256
reminder exchanges and 512 input mappings. A full transport reports capacity
instead of discarding an unresolved effect or silently replaying it. A later
retention policy must preserve no-nag and idempotency tombstones.

## Integration

`PhoneAccess(core)` uses the Core's Store, generation token and clock. The app
constructs one instance as `app.state.phone`. `phone_routes` registers typed
Shana routes under `/v1/coordination/phone/*` and authenticated dashboard proxies
under `/api/coordination/phone/*`. The page is dashboard-owned. No new listener
or public route owner is introduced.

The owner loop calls `consider_due` at a bounded cadence. Its internal review
backoff is five minutes. The service calls `Core.review_channel` using
`phone_reminder` and current sourced commitment context. After that call it
rechecks the source hash, revision, owner sequence, device epoch, policy hash,
availability and deadline before retaining a ready message. A ready message
must still match its review context and be offered within sixty seconds.

The consideration context explicitly identifies the commitment's owner, source
kind, certainty and unfinished status. Scheduling means eligibility to consider
contact. It does not prove that a condition in the commitment happened. Recent
rehearsal controller states are separately labeled observations. A stopped or
expired controller does not prove the rehearsal's broad goal was accomplished.
There is currently no independently verified prerequisite-event producer, so the
verified-prerequisites list is empty. Controller evidence is hashed and rechecked
after model judgment and before an offer, including transitions that do not
change the Core's foreground sequence.

## Verification

Focused tests exercise the real Core/store, actual HTTP routes and typed proxies.
Dialogue in those tests is an explicitly scripted fixture. The actual `phone.js`
module also runs against controlled browser/network events for privacy changes,
uncertain sends, literal text rendering and generic notifications. These checks
do not establish physical Android behavior or general conversational judgment.

```bash
PYTHONPATH=src TMPDIR=/tmp .venv/bin/python -m pytest \
  tests/test_phone_access.py tests/test_phone_routes.py tests/test_phone_browser.py -q
node --check src/gamma/dashboard/static/phone.js
node --check src/gamma/dashboard/static/phone_worker.js
```

The explicit real-model verifier starts a fresh finite isolated host, authenticates
to the actual dashboard, moves context from desktop to phone HTTP requests, waits
for an actual considered reminder, records client receipt and acknowledgment,
tests offline/sleep recovery and checks physical erasure of derived text. It
keeps original failures and compares source bytes before and after the run.

```bash
PYTHONPATH=src TMPDIR=/tmp .venv/bin/python scripts/verify_phone_flow.py --run-real-model
```

Receipts belong under `data/coordination/remaining-20261027/p8/`. A successful HTTP
verifier still does not prove a person received an Android notification, found
the reminder useful or accepted the mobile layout. The shared dashboard and
physical phone checks must be reported separately.

The 2026-10-27 real HTTP flow `real-a75770a4a0b04fd8a21d600c881d9e8c`
passed its mechanical checks in 96.856 seconds, with 356 executed source files
archived and unchanged during the run. It also exposed an unsupported claim that
the rehearsal had finished, and a later reply misattributed the owner's commitment
as Shana's assigned work. The original complete flag describes mechanical
verification. Considerate-contact acceptance failed. Earlier ambiguous-question
and verifier-helper failures remain retained. See `audit-before-typed-context.json`
beside those receipts. Tone and physical Android use remain unaccepted.

The typed-context repair has focused regression coverage. Its separate actual
semantic collector records unknown-prerequisite, active-controller and observed
controller-stop cases without judging its own wording:

```bash
PYTHONPATH=src TMPDIR=/tmp .venv/bin/python scripts/verify_phone_semantics.py --run-real-model
```

Inspect each actual reply or silence against its supplied source and controller
facts. Its collection-complete flag is not semantic acceptance.

The three-case collection `semantic-fdcb20665beb478db39f61e5ba39d909` completed in
140.689 seconds with 360 executed source files archived and unchanged. Unknown
and active prerequisites stayed future-framed. The stopped-controller case
referred to the owner's unfinished notes without claiming the rehearsal goal
was accomplished. All three outputs reached the HTTP test client and were
acknowledged. `semantic-review.json` records this narrow factual regression
result. Every case still chose a reminder, and tone remained direct or pushy.
Later source review found that the shared observation prompt also retained a
categorical conversation-reply instruction. Structural channel observations now
explicitly permit reply, acknowledgment, silence or deferral; owner and private
voice prompt bytes remain unchanged. The 202 focused checks cover actual prompt
rendering, spoof rejection, Core silence and phone/stream/audio behavior. This removed an instruction conflict. The October 30 follow-up below tests the resulting channel instructions on a new source pin. The original three cases remain negative quality evidence, not proof that
the conflict caused their wording. Evidence:
`data/coordination/p7/channel-observation-review/review.json`.
General considerate contact, ordinary conversation quality and physical Android
use remain unaccepted. The earlier counterexamples remain retained.

## October 30 follow-up

A fresh pinned-source local-model run completed the three reminder scenarios in 134.265 seconds. Two messages reached and were acknowledged by the authenticated HTTP client; the observed-controller-stop case chose silence. All three test hosts exited and executed source remained unchanged. Neither delivered message invented prerequisite completion. The active-rehearsal wording remained directive, so owner tone acceptance stays open.

Receipts and the executed-source archive are under `data/coordination/finish-20261030/phone-followup-*`. This is desktop HTTP evidence, not physical Android, closed-phone push, human acknowledgment or a broad considerate-contact guarantee. The current alerts require the dashboard to remain open.
