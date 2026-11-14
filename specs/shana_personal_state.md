# P2 isolated personal state

Status: Implemented in the isolated candidate under the user's P2 goal. No activation.

## Authority and representation

The existing coordinator owns accepted personal state in its generation-checked
SQLite record store. Completed private exchanges, sources, record revisions,
administrative edits, account links and queued intentions commit under that
owner. The legacy conversation adapter remains side-effect-free. Its private
coordination prompt uses the canonical identity and selected recall rather than
reading a second set of mutable legacy memory/emotion files.

Keep original experiences, reported claims, observations, current beliefs,
preferences, temporary mood, administrative character edits and pending learning
hypotheses distinct. A model proposal cannot grant permissions, confirm an
account link, rewrite the authored identity or turn a report into verification.
Account identity, familiarity, opinion and access authority are separate.

Current personal records have stable IDs, subject IDs, source references,
audience, certainty, revisions and status. Corrections retain the original
experience and a superseded revision. Forgetting excludes the record and its
source from recall; source deletion redacts canonical text and derived records.
An administrative edit changes current authored settings with an audit event,
without fabricating a dialogue or remembered experience.

## Recall and retention

Start with measured lexical retrieval, without embeddings or a new database.
Visibility and subject filtering precede reading content. Only bounded relevant
records and compact provenance enter a prompt. Search/archive material is not
automatically an accepted personal belief. Unverified and incomplete sources
remain labeled that way.

Portable configurable defaults retain raw text for 30 days and at most 500
source turns per session, with a global raw-source limit of 5,000. Current accepted facts and
unfinished commitments are retained separately. Expired source text can leave
a compact reference/hash; Shana must not claim exact original wording after it
is pruned. No continuous raw audio/video archive is introduced. Maintenance
creates reviewable connection/learning proposals and never changes personality
or trains weights while asleep.

Active and queued inputs retain their finite delivery deadline, independently
of completed-conversation text retention. A zero-day policy cannot prune an
input during its current inference call. The accepted reply can be delivered
while its raw journal projection is pruned immediately afterward. A real
connected-run maintenance race and a deterministic blocked-inference check
verified this distinction.

## Lifecycle, copying and reversal

Inputs received asleep are durably queued and do not wake Shana. User/system
wake permits processing in order with current authority checks. Catch-up
distinguishes completed, failed, waiting and changed-situation evidence. Worker
outcomes record their real source and observation time without invented awake
experience. Temporary mood can settle without cancelling authorized work.

Legacy SQLite data is copied read-only into an isolated rehearsal bundle.
Workers receive only code and fictional fixtures. Stable legacy IDs, source
dates, attribution, accounts, checkpoint records and manual edits are reconciled
privately. Ambiguous identities and unsupported data are retained/quarantined,
never silently guessed or promoted. The production source and services remain
unchanged.

The key/value journal remains readable by the P1 coordinator. Reversal uses
older code against the current compatible journal, not an old database snapshot
that would erase later experiences. A real old-code/new-code rehearsal must
preserve post-migration exchanges, edits, corrections and unfinished work.
Portable snapshots include content-addressed practice artifacts and personal
provenance. One candidate identity lease prevents simultaneous owners of copied
personal state within the isolated project.

## Acceptance

Use separate sessions and actual process replacement to recover an unfinished
goal, earlier decision, partial recollection and correction with honest sources.
Reject an incorrect account link. Verify private facts and task details never
enter shared/unbound views, including diagnostics. Demonstrate familiarity
without fabricated intimacy and mood continuity without missed work.

Run both fictional and private copied-data reconciliation, plus reversal that
retains all later experiences. Begin a saved familiar-situation set for later
model/voice comparisons. Real model and browser results must remain distinct
from scripted fixtures and source-only conclusions. Human recognition and live
activation remain separate from technical acceptance.

## Implemented interfaces

The authenticated coordinator exposes owner-private GET and POST
`/v1/coordination/personal`. The separate private Monitor proxies these at
`/api/coordination/personal` and provides `/dashboard/personal`. Production
routes and services are unchanged. Public/unbound audiences cannot use this
path. The browser requires explicit private-owner confirmation before fetching
state and clears its private DOM when that confirmation is removed.

Owner controls support authored identity edits, sourced report corrections,
source removal, people, confirmed exact account links/unlinking and bounded
retention settings. Administrative controls invalidate in-flight model
decisions. Model proposals cannot use those administrative interfaces. Account
links add no action permissions, including imported legacy trust/owner labels.

Removal follows source dependencies through derived memories, revision text,
replies, authored-edit audits, person/account projections, working notes and
activity request text. Content-free IDs, hashes, dates and tombstones remain.
Removal can redact a whole source and dependent replies. Original backups stay
outside active recall and are not physically erased. Reimport cannot bypass a
removal or unlink tombstone by changing text or primary keys.

When returning from P1, exact model-prompt dependencies are unavailable. Newly
backfilled old-reader replies conservatively depend on their possible canonical
inputs. Later source removal therefore also redacts those possible quotations.

Resolved owner commitments remain searchable as explicitly reported history;
this never verifies a worker result. Pending self-development and imported
checkpoints remain proposals without execution authority. Sleep catch-up lists
completed work, failures/review, waiting work, changed communications and
maintenance proposals. User/system wake is required.

## Verification record

Application execution, inference and tests ran on Gamma in
`/home/neety/Documents/gamma-coordination-core-20261023`, with `PYTHONPATH`
pointing at its `src`. Windows helpers used the hidden launcher. Workers
received source code and fictional fixtures.

Four scoped coding-worker proposals covered lexical recall, legacy row
conversion, canonical prompt rendering and snapshot validation. Results,
proposal diffs and source baselines were reviewed before integration. The
recall proposal failed its supplied check and was repaired; passed proposals
also received additional validation. A fifth context-compaction submission
stopped at the occupied-endpoint guard without inference and was implemented
as a small reviewed helper. No paid-provider fallback was used.

| Requirement | Observed evidence |
| --- | --- |
| Automatic recall across sessions | Real Qwen3.8 27B retained a preference, decision and unfinished call without a save command, then answered in another session. |
| Correction, restart and partial recollection | The real-model journey revised a stable preference, recovered current/earlier reports after process replacement and used retained records after raw-text pruning. |
| Owner browser flow | Actual authenticated forms edited character, created/corrected/forgot a report, linked a confirmed account and cleared the private display. |
| Ownership and removal | SQLite checks rejected simultaneous owners of a copied identity, withheld stale replies, rolled back injected partial commits and checked transitive removal. |
| Sleep and mood | Duplicate queued messages survived restart and ran once after wake. Mood settled without changing personality or permissions. Existing composed practice tests cover authorized continuation and verified after-sleep outcomes. |
| Legacy reconciliation | The private copy retained all 14 journal primary keys, one person, two exact account links, four summaries, four checkpoints, one self-goal and five auxiliary sources. Claims remain unverified. |
| Reversal retaining later experiences | Actual preserved P1 source, verified against 61 baseline files, completed a turn over the current compatible journal and returned to P2 without dropping later records, edits, corrections, queued messages, deadlines or artifacts. |
| Retrieval baseline | 22 saved fictional situations passed expected-top, unknown, audience and person checks. A separate 16-query corpus checks lexical matching. No embeddings or training. |
| Prompt bounds and disclosure | Exact private prompts fit the bounded model context with full authored configuration, maximum optional context and a 7,996-character input. Input, authority and activity IDs remain protected. Public conversation regressions and private error-field disclosure checks passed. |

Private copied-state evidence is at
`data/coordination/p2/private-copy-e975f8a4984841dd975dc9c9c0abe5be/report.json`
on Gamma. Its current-state snapshot restored 98 journal records and 23 files
exactly. The old-provider capture made no inference requests and saved only
prompt digests and check booleans. Private history and prompt text were neither
sent to workers nor copied into reports.

The real connected journey at
`data/coordination/p2/connected-24a8eb6f9b27433a816c255f189a4c68/report.json`
passed nine checks in 119.75 seconds. Earlier repeats exposed an evasive recall
answer and missing durable proposals after instruction changes. Those failed
runs remain saved. Current instructions require useful proposals and answers
grounded in recalled reports. A repeat on final source is recorded separately
in the review manifest.

P1 worker integration also passed again on P2 source in 142.44 seconds:
`data/coordination/foundation-c94e52c213a64b0999af6fd56b115dde/report.json`.
Its ten checks included actual Qwen-selected quiet practice while focused,
conversation during work, task switching/pausing, process replacement, saved
checks during sleep, one catch-up and a targeted checked status receipt. An
earlier integration run paraphrased a recalled worker result instead of using
the receipt capability. The explicit activity-status rule fixed that regression.

The familiar situations are saved in
`specs/fixtures/shana_familiar_situations.json`. State and retrieval checks remain
separate from human recognition, social quality and later voice comparisons.

Final P2 connected evidence on the current source is
`data/coordination/p2/connected-9f2bf5f249aa4be0970a2cdb3988c6a7/report.json`.
All ten checks passed in 139.17 seconds, including the real relationship reply
separating familiarity, disagreement and authority, and the zero-day-retention
case after its maintenance-race fix. The authenticated browser reported no
JavaScript errors. The review package contains its sanitized report alongside
worker-flow, private-copy and preservation reports.

## Reproduce and review

Run only in the isolated Gamma checkout with its virtual environment and
`PYTHONPATH=$PWD/src TMPDIR=/tmp`:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_personal_flow.py --model qwen3.8:27b --endpoint http://127.0.0.1:11434
.venv/bin/python scripts/verify_personal_reversal.py
```

`verify_personal_copy.py` additionally takes explicit paths to the copied
manifest and closed P1 foundation state. It never opens live production stores.
Snapshot export requires a closed owner; restore requires a fresh directory.
The P1 reversal wrapper holds the same candidate-global identity lease. Direct
old-code startup without that wrapper is not a supported handover.

The finite hidden Windows review launcher in
`shana_foundation_review_runbook.md` also opens P2 personal state. Review uses
separate state and ephemeral credentials. Verification leaves its hosts closed.

## Limits and activation boundary

The SQLite backup was consistent and auxiliary files were stable during capture,
but legacy stores lacked a global atomic snapshot. Cross-store relationships
retain that uncertainty. Ambiguous historical names do not confirm accounts.
The 5,418 temporary stream rows remain backed-up aggregate quarantine evidence,
outside personal recall.

Lexical recall can miss synonym-only questions. The scenario set is small and
model output can vary. Material personality changes require explicit owner
edits; public memory sharing has no reviewed P2 publication path. Human
recognition, live voice, broad autonomous work, weight training and production
activation remain outside this milestone. Activation requires explicit user
approval and a separate live cutover review.

One broad regression attempt exposed an intermittent lock during concurrent
legacy `ContinuityService` WAL initialization. That module is unchanged by P2;
its initialization callback runs before the legacy schema serialization lock.
P2's accepted-state owner has its own process/identity lease and transaction
guard. The failed attempt is retained in the review package as
`full-suite-initial-race.log`; focused repetitions and the final broad result
are recorded in the verification index. This is a legacy initialization risk
to include in any later live-cutover review, not an authorization to alter
production.

The focused legacy diagnostic reproduced that lock in 3 of 20 fresh processes;
17 passed. Both the legacy module and its test are unchanged from HEAD. A
passing final broad run does not resolve this separately recorded risk. P2
starts its private adapter under the single-owner lease rather than allowing
those competing initializations. Do not generalize the isolated P2 result to
unrestricted concurrent legacy startup.

## Current owner profile, October 31

The owner-authenticated personal control adds action owner_profile with bounded
values name (1-60 printable characters), pronunciation (1-80 printable
characters), and optional known_person_id naming an existing active person.
This action always edits the reserved canonical person owner. Generic person
editing continues to reject owner and self, and other profile fields are rejected.
It grants no account or execution authority and does not promote imported people,
memories or relationship claims. The explicit identity reference associates the
current owner with an existing person without merging historical certainty.

The current preferred name is a sourced administrative confirmation, not a
fabricated past conversation. Configured profiles add personal.owner with the
fixed owner role/person ID, confirmed name, pronunciation and source reference.
It survives prompt compaction under the unchanged token budget. Dependent turns
record both the current confirmation and any referenced historical person source.
Without a configured profile, the previous default prompt bytes remain unchanged.

Forgetting or deleting the profile source resets the reserved owner in place to
its generic display name and invalidates dependent replies. Forgetting only the
historical reference removes that association while preserving a separately
confirmed current name. The owner role and historical account authority stay
unchanged. Tests exercise actual Core/HTTP controls, exact prompt rendering,
compaction, source erasure and unchanged legacy records.
