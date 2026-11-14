# Isolated recovery and release preparation

Status: recovery mechanics were verified in the isolated candidate on October 27.
The owner-authorized private release now uses them on the normal production
entry. See the [current production operation](shana_private_release.md#actual-private-production-operation)
for authority, backups and remaining acceptance limits. The dated receipts below
retain their original scope.

## Owner controls

Home's Recovery section calls authenticated dashboard
`GET/POST /api/coordination/maintenance`, which forwards to Shana-owned
`/v1/coordination/maintenance`. Every request declares `owner_private`.
POST actions are `check`, `backup` and `rehearse`, with a bounded request ID.
A rehearsal also names an existing verified backup ID. The dashboard does not
open the personal database or select a restore destination.

`check` reads SQLite's actual quick check and reports pending turns, available
space and snapshot status. The UI labels a backup verified only when its stored
status is `verified_snapshot`.

`backup` serializes the live SQLite connection under the active owner fence,
copies immutable artifacts and closes only the copied owner. It exports through
the existing snapshot validator. The live journal and experiences accepted
after the snapshot remain in place. Repeating a request ID returns its recorded
outcome; a failed or interrupted intent is not silently retried.

`rehearse` restores one verified snapshot into a fresh private directory. It
checks the copied database's integrity, count, closed owner and artifact hashes.
It does not activate that identity or overwrite the current state. Only one
recovery operation can run at a time; interrupted intents become
`failed_review_required` after the owner generation changes.

## Storage and privacy

Backups live beneath the isolated state's `recovery/` directory. Directories
are owner-only and snapshot files use mode 0600 on Linux. This is a local
protected copy, not off-device redundancy or an encrypted archival service.
No upload or automatic deletion is configured.

Each snapshot is limited to 32 MiB with a 256 MiB free-space reserve. There are
at most eight backup intents and eight restore-rehearsal intents, including
failures. Reaching capacity produces an explicit blocker. Retention cleanup is
an owner operation; increasing a limit is not a substitute for a retention
decision. Restore copies mark inherited backup catalogs historical and
unavailable locally because the backup files themselves are not recursively
duplicated.

Transfer preserves canonical identity, personal records, commitments, activity
meaning, artifacts and request fingerprints. It invalidates live screen/game,
rehearsal, phone and checked-audio transport authority and ephemeral channel
review payloads. Consumers reconnect explicitly; old speech and unknown effects
are not replayed. Current channel records cannot be projected into the old P1
reader merely by removing unfamiliar rows. That compatibility check fails closed.

Explicit SQLite connection closing was verified with garbage collection
disabled: twenty export/restore cycles retained three descriptors, instead of
the earlier seven-descriptor growth per cycle. This is a resource regression
check, not proof of years of unattended operation.

## Release boundary

The [delivery plan's handover gate](shana_delivery_plan.md#h-hand-over-live-ownership-deliberately)
requires a pinned release, covered effect paths, reconciled work, actual
reversal and the owner's activation decision. The October 30 decision authorized
private production testing. The new `gamma-private-release-api` and
`gamma-private-release-dashboard` units now own ports 8000/8001; the four legacy
units are retained loaded, inactive and fenced. Preserve post-cutover experiences
through the compatible current reader rather than restarting an older database.

The candidate reuses configured inference and backend admission. No model or
voice replacement was selected, downloaded or promoted. Familiar-history,
unfamiliar-task and human character comparisons are required before accepting
a replacement. Stored identity alone cannot prove behavioral continuity.

Current evidence and remaining checks are recorded in
[the P6-P9 work record](shana_remaining_phases.md). Recovery tests do not establish
an accepted live migration or off-device disaster recovery.

## Current connected receipt

The October 27 actual local-model HTTP rehearsal completed in 53.122 seconds.
Authenticated Home/recovery controls created an idempotent verified snapshot
while the owner stayed active. A later accepted exchange remained in current
state and was absent from the older restore copy. Killing the exact test child
left a nonempty stored owner; reopening claimed generation 2 and rejected the
old token before a write. The latest closed state then reopened from a fresh
portable copy with its identity, preference, later exchange and unfinished owner
commitment intact. All three owned host processes exited.

Independent read-only inspection checked the actual SQLite rows, closed owners,
0600 database files, six completed provider HTTP calls and all 378 archived
source members. The report and audit are under
`data/coordination/remaining-20261027/recovery-a55d5407292647e39f5d90ffddf6e25c/`.
Report SHA-256:
`235cf5642040a3e0af97957a25329bad1b7c5bdb390ff6779663e96045219495`.

This reused the existing model. The factual recalls were correct; an unsolicited
lecture and imperative wording remain interaction-quality limits. A previous
verifier startup failed because its state path was outside the isolated checkout.
That failure and its invalid empty-PID absence assertion are retained separately;
the corrected verifier tracks actual children, including failed starts.

An offline wheel build from a disposable source copy also passed. Its 327 required
source/assets matched the wheel bytes, and six current capability modules plus
the actual Home asset imported from the extracted wheel. Wheel SHA-256:
`8eaa9be741fa0253c0ba4fd435f2418f8b50cd939dc0240f2ee2e32605aa4988`.
The package was not installed into production. Its receipt is
`data/coordination/remaining-20261027/package-74508a0389c845acb146a7b302a73a36/`.

## Actual private archive round trip, 2026-10-30

A finite observed drain captured the legacy database, auxiliary state and whole
work/learning evidence. The original services were restored with new PIDs and
checked API authentication/Dashboard health. A durable 460,705,306-byte Windows
archive was independently hashed and its actual file ACL permits only the owner
and SYSTEM through a protected parent. This is a manually retained archive,
separate from the dashboard's local eight-intent backup catalog.

The exact Windows bytes returned to fresh private Gamma storage. The corrected
reader extracted 19,576 logical entries while observing 39,152 raw headers,
rejecting traversal, links, special/sparse members, duplicates and resource
excess. Seventeen Gamma cases cover the extractor, including pre-body metadata
limits and raw-header counting on the actual Python 3.14 parser path. The first
successful copy exposed an unused header hook; that original report is retained
without accepting that guard claim. The corrected actual replay is separate.

The actual current-reader verifier rechecked 17,343 raw files, 630,729,578 bytes,
16 tables and 32 legacy references. Repeated import was idempotent, operational
authority stayed absent, stale ownership was rejected, and the labelled later
synthetic exchange and unfinished commitment survived export/restore/reopen.
Input archive and extracted bundle were unchanged, with zero provider/effect
calls. The raw corrected report is
`data/coordination/finish-20261030/offdevice-corrected-final-report.json`, SHA-256
`501c3945475a09e053032721fd2f2d81c1a03f8ddf89471856754d76c8bb6a27`.

This verifies recovery from the manually retained off-device archive using the
compatible current reader. It does not rebuild an entire failed machine or
accept an alternative model/voice, human character recognition, semantic
adoption or permanent production cutover. The 900-second helper deadline uses
Python signal handling and cannot guarantee preemption of uninterruptible
kernel I/O.

A later bounded comparison preserved the same copied identity, the later
observatory experience, the unfinished blueprint commitment and the post-swap
amber decision through Qwen3.8:27b, the already installed Gemma4:e4b, and a Qwen
rollback. The original driver report retains `complete=false` after a telemetry
schema-marker assertion. An independent offline join of actual body-derived
model fields and private-prompt markers verified five primary and five safety
calls, all returned, alongside the intact portable state and actual child cleanup.
A separate voice-only continuation rendered the same authored sentence with the
existing XVec Soft and Dataset Ref Soft presets in 15.23 seconds, with zero new
language-model calls and two successful TTS requests. Both tested stock-profile
configurations passed the strict release voice pin. These are technical comparison
and recovery receipts, not adoption, personality or listening acceptance. No model
or voice choice was promoted. The separate reports, original verifier failure,
offline audit and samples are retained under
`data/coordination/p9/replacement-comparison/`.
