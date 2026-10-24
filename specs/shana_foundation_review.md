# Shana foundation review and next steps

Status: Source and runtime comparison completed within the read-only scope below; conversational quality remains unverified.
Captured: 2026-10-23.
Direction: [Shana foundation direction](shana_foundation_direction.md).

Implementation update, 2026-10-23: the user authorized GitHub checkpoints and a
first isolated partial rebuild in a new chat. The selected direction is a fresh
coordination core with selective reuse. The audit below records the evidence and
recommendations at review time; the [handoff](shana_coordination_core_handoff.md)
records subsequent decisions and the first acceptance milestone. Live activation
remains subject to user approval.

## Current recommendation

Keep Gamma's useful integrations, existing data, provider interfaces, and
verified safeguards. Concentrate replacement or consolidation on how Shana
chooses and continues activities, coordinates work with conversation, and commits
experience and delivery outcomes. The modular candidate contains relevant code
to assess for reuse, but it is not a replacement production tree to activate.

This supports discussing a small fresh coordination core with reused components.
It does not establish a need to rewrite every integration or choose a new memory
database. No foundation option or implementation is selected by this report.

## What was checked this time

The comparison covers D1-D11 through targeted source reads and searches, current
process ownership, two read-only HTTP requests, installed-source comparisons,
and dated acceptance records. It is not an exhaustive line-by-line audit.

| Evidence | Observation on 2026-10-23 | What it establishes |
| --- | --- | --- |
| Production checkout | `/home/neety/Documents/gamma-main`, HEAD `38e3fbff4796e0c8ed56a2fcda9836d7a8e2273a`, with extensive uncommitted work | HEAD alone is not the installed implementation. |
| Installed source | Captured 231 files under `src/gamma` with `.py`, `.md`, `.js`, or `.mjs` extensions. All 231 exist in the accepted composition source; 229 match exactly and two match the recorded accepted overrides. | The examined production code includes the later foundation activation and stop repair. This is stronger than an old proposal or Git label. |
| Current service owners | Shana PID 32730, Dashboard PID 33020, coding-idle PID 33077, coding-autonomous PID 51714. All four units are running from `gamma-main` and report October 19 start times. | These are current process observations, not proof of response quality or active integrations. |
| Read-only HTTP | Anonymous `8000/health` returned 401; `8001/login` returned 200. Source requires API authentication for `/health`. | The unauthenticated health result is consistent with the installed authentication boundary. No credentials or private API data were read. |
| Windows checkout | After normalizing CRLF/LF, 191 captured production files match Windows, 33 differ, and seven production files are absent from Windows. | A Windows-only review would miss installed attribution, journal-lifecycle, disclosure, and inference-admission changes. |
| Modular candidate | `/home/neety/Documents/gamma-modular-phase1`, same Git HEAD but different working files; 256 selected source files captured | This is a separate candidate with code for state authority, brain workers, silent work, shared admission, and delivery receipts. Its functionality is not established by sharing a commit with production. |
| Earlier evidence | Accepted composition source and October 19 acceptance receipts remain under `/home/neety/Documents/gamma-foundation-backups/`. The older `/tmp/gamma-foundation-implementation-20261008-f3a2bq47` path is absent. | Durable backup paths now matter more than old temporary paths. |

The two production overrides are `coding/autonomous.py` and
`dashboard/main.py`. Their hashes match the accepted overrides recorded by the
earlier review. The final activation receipt reports acceptance; the stop-fix
receipt separately limits its behavioral checks to disposable state using
installed code. This audit read those records and compared installed source;
it did not rerun their tests.

The newer residency experiment is explicitly `conditional_contract_proof_only`
and still requires independent review. It records no proof of production
identity or retention preservation. It is not an installed model-performance
improvement.

These observations supersede older blanket statements that the foundation
repairs are all unactivated. They do not mean the full Shana vision is achieved.

## Comparison against the vision

"Present" below means inspected code exists in production. "Partial" means it
supports part of the requested experience. "Wanted" means the complete behavior
was not established in the inspected implementation or evidence.

| Vision | What Gamma actually has | Recommendation |
| --- | --- | --- |
| D1: continuous identity and growth | Present persona assembly, authored baseline, stored feelings, emotion episodes, patterns, and memory. The prompt still gives entertainment global priority. No general process for developing and revising her own interests or personality was established. | Keep the confirmed starting tendencies and context assembly. Change the global priority and rigid trait instructions to match the agreed situational character. Define how experience warrants a lasting change before selecting training. [E1] |
| D2: attention and initiative | Present stream-event decisions, silence thresholds, cooldowns, listener checks, and a structured response contract. Production's proactive path generates a short check-in and disables tools. Conversation, stream, voice, and coding take different entry paths. | Consolidate or replace activity selection so conversation, quiet work, commitments, and personal interests can be considered together. Keep mechanical limits and controls. The candidate's queue and bounded silent-work code are reuse candidates, not a finished situational algorithm. [E2, E11] |
| D3: mood and natural expression | Present two persisted feeling/emotion stores, limited emotion labels, deterministic note/pattern updates, and voice-style hints. The emotion decay counter is set and rendered; no decrement or elapsed-time settling was located in the captured production sources. | Keep useful history and file-write safeguards. Clarify one authoritative mood update policy and distinguish expression, temporary mood, and lasting preference. Test naturalness through actual interaction later. [E3] |
| D4: personal projects and motivation | Partial coding jobs, working checkpoints, and proposed/approved stream self-goals. Candidate silent work selects approved goals with finite cycles. No general personal-project lifecycle, reversible loss of interest, or boredom-versus-blocker assessment was established. | Reuse goal IDs, checkpoints, and controls. Define the shared activity lifecycle before adding another independent goal store or a boredom timer. [E4, E11] |
| D5: accountable independent work | Present owner coding-goal tools, planning, bounded attempts, checker review, protected checks, application receipts, rollback, and pause/resume/stop. Separate foreground inference admission exists. The running worker can apply and activate changes after its own checks. | Keep the executor safeguards and progress records. Change the promotion boundary to the newly requested user review before affecting running Shana. Connect jobs to Shana's commitments and catch-up. Concurrent conversational quality remains unmeasured. [E5, E6] |
| D6: useful, honest memory | Present SQLite facts, episodes, scoped journals/checkpoints, source attribution, uncertainty labels, manual-edit provenance, selective writes, and keyword retrieval. Core memories are still described in the prompt as permanent and always true. Fast-mode fallback memory extraction uses a daemon thread that suppresses exceptions. | Preserve data and the recent attribution/ordering repairs. Change blanket certainty for correctable beliefs, expose memory-job outcomes, and define retention/consolidation and retrieval needs. There is no evidence-based reason yet to replace SQLite. [E1, E7] |
| D7: relationships across accounts | Present linked account records and identity resolution to a shared person. Trust and private-memory access are explicit. Public/shared recipients deliberately receive restricted dynamic context. | Keep the person/account distinction and privacy boundaries. Add an explicit way to retain and use audience-appropriate shared experience, and separate permissions from Shana's opinion of someone. Rich relationship development and learned comfort are still wanted. [E8] |
| D8: shared gaming and streaming | Present Twitch/event normalization, stream response policy, pacing, temporary context, and bounded self-goal proposals. `game_state` events currently defer as unconnected. Minecraft has a narrow command protocol. | Keep adapters and bounded game contracts. Change stream activity selection to support the agreed broad plan and initiative. General game learning, a personal play style, and sustained solo hosting remain wanted, not accepted capabilities. [E2, E9] |
| D9: natural voice and desktop presence | Present STT/TTS interfaces, a replaceable live-turn facade, per-turn subprocess execution, Discord voice code, and performer/VTube interfaces. Mic transcripts route as direct turns. VTube mouth-level transport explicitly remains unconnected. | Keep provider and integration interfaces. Assess reuse of candidate independent voice workers and playback receipts. Address who speech is directed at, actual delivery confirmation, and desktop presence as separate missing experiences. Voice quality and conversational timing need later listening evidence. [E9, E10] |
| D10: sleep and worker catch-up | Present Presence modes, public/Discord output gating, independent coding services, and a wake opening using lifecycle timestamps and selected memories. Local direct inputs can still proceed through the nonpublic path during sleep. The wake builder does not assemble worker outcomes. | Keep separate process/output controls. Change sleep to the agreed active-participation semantics and define decision waiting plus catch-up. Memory consolidation and deliberate system waking remain wanted. [E12] |
| D11: continuity when parts change | Present persisted records and provider boundaries. Candidate state transactions, generation checks, and delivery ledgers address useful technical problems. No established evaluation shows a replacement model preserves the developed Shana, or transfers learned skill. | Keep model-independent records and evaluate candidate mechanisms selectively. Add a continuity comparison based on familiar histories and actual behavior before adopting a model or voice. Avoid copying the older candidate over newer production repairs. [E1, E7, E11] |

## Changes with the strongest present justification

### Preserve the repairs already made

The previously identified constructor-wide journal interruption hazard is
repaired in production. `ContinuityService` construction no longer invokes
recovery; application-owned `JournalRecovery` reconciles verifiably dead owners.
Scoped input ordering, source attribution, and controlled public disclosure are
also present. Any replacement should preserve those behaviors rather than
reintroduce their older versions. [E7]

### Replace scattered activity selection with one coherent responsibility

The existing structured response contract is useful, but it runs after several
different input and activity decisions. `StreamBrain.decide` uses event kind,
mentions, priorities, and fixed policy branches. `ProactiveScheduler` waits for
an output listener and no active turn, then asks for a brief observation or
question. Coding services independently execute queued work.

That supports parts of the vision but does not yet supply a common decision
about quietly continuing a project, answering, changing activity, or following
an interest. This is the main coordination gap to resolve. It does not justify
removing input validation, permissions, deadlines, or output pacing. [E2]

### Separate preparing an improvement from activating it

The installed `RepositoryAgent.main` supplies `activate_source` by default.
After checks and independent model review, `execute` applies source and invokes
that callback. The callback restarts the selected managed service. A final user
approval step is absent from this path, which conflicts with D5's newly recorded
direction. This audit did not invoke it. [E5]

There is also a concrete integration mismatch to review: `activate_source`
expects an anonymous `8000/health` request to return 200, whereas the installed
auth middleware covers that route and the audit request returned 401. This is
a source-and-HTTP finding, not a reproduced activation/rollback incident. [E6]

### Preserve outcomes separately from attempts

Production's performer turn store marks speech chunk readiness as speaking and
speech-ended publication as completed. That status is not evidence the user
heard the output. The candidate has a separate playback ledger with receiver
leases and acknowledgments worth assessing for reuse. Likewise, remembering a
mistake, recording a job result, and actually improving at a skill should remain
separate claims. No previously rejected Monitor/UI verification was retried. [E10]

## What to reuse from each existing version

- Production is the baseline for current attribution, privacy, journal recovery,
  ordering, owner controls, coding safeguards, and authentication. Preserve its
  records and accepted fixes when evaluating a different foundation.
- The modular candidate is useful for its explicit state transactions, brain
  process boundary, bounded silent work, independent voice execution, shared
  admission, and playback receipts. Its examined code predates several newer
  production modules and lacks the production coding/Discord additions. Its
  `BrainAuthority` also exposes persona context without the newer recipient
  distinction visible in production. Carry-forward and integration review are
  required before reuse; neither a common commit nor older acceptance is enough.
- Windows is suitable for documentation and coordination. It is not a complete
  representation of currently installed Gamma. Do not synchronize whole trees
  over one another to reconcile the versions.

## Foundation options

| Option | Fit and tradeoff |
| --- | --- |
| Consolidate production in place | Preserves recent fixes and minimizes migration. It still needs a deliberate activity/state boundary; adding more event-specific branches would leave the main concern unresolved. |
| Define a small fresh coordination core and reuse proven components | Best current discussion direction. It targets the missing activity and experience responsibilities while retaining valuable integrations, data, and safeguards. Candidate code may reduce how much is new. Exact boundaries and migration still need choosing. |
| Activate the modular candidate wholesale | Not supported. It is a separate older working tree and does not contain all newer production changes. Its silent-work slice is narrower than the desired personal autonomy. |
| Rewrite everything | Not justified by this audit. It would make working adapters and accepted safeguards new work again without establishing better character, growth, or judgment. Replacing a component remains reasonable when a defined boundary cannot be repaired simply. |

These are recommendations for the next design conversation. They are not an
implementation plan or instructions to start any work.

## Next discussion

Choose what the first coherent foundation must demonstrate and which component
owns activity decisions, durable personal changes, worker handoff, and delivery
outcomes. Walk one connected experience through those owners: focused work,
conversation while a job runs, a switch of activity, sleep, and catching up.
Use the existing familiar-game and linked-person examples to expose missing
ownership decisions without writing a separate policy for every situation.

General phone operation, unrestricted game learning, independent social plans,
event-driven waking, and self-planned solo streams can remain future goals.
Their current absence does not require building their infrastructure now.

## Evidence anchors and practical limits

Paths below refer to the captured Gamma-PC tree, not the different Windows
source. Line numbers identify the reviewed snapshot. The inventory, exact file
hashes, source copies, HTTP observations, and hidden-launch receipts are under
`C:/Users/darke/AppData/Local/Temp/gamma-foundation-comparison-2026-10-23/`.

| Anchor | Source and relevant location |
| --- | --- |
| E1 | Production `persona/core.md:12-38`; `persona/loader.py:81-104,148-198`; `conversation/turn_contract.py` structured response parsing |
| E2 | Production `api/routes.py:68-139`; `conversation/service.py:74-86`; `stream/brain.py:146-214,593-765`; `proactive.py:55-156`; `run_live_voice_worker.py:772` |
| E3 | Production `persona/assistant_state.py:101-139`; `persona/emotion_service.py:48-103,104-168`; `persona/emotion_models.py:43-70`; complete captured-source search for `decay_turns_remaining` |
| E4 | Production `memory/continuity.py:72-108`; `stream/self_goals.py:14-40`; `task_board.py:4-110` |
| E5 | Production `tools/coding.py:84-123`; `coding/autonomous.py:38-123,413-471,486-502`; `coding/project_transaction.py:28-85`; `coding/project_policy.py:11-23` |
| E6 | Production `coding/project_activation.py:10-50`; `llm/backend_admission.py:74-113`; `llm/local_adapter.py:97-112`; `main.py:37-55`; current anonymous health HTTP status |
| E7 | Production `memory/continuity.py:123-177,270-284,381-412`; `memory/journal_lifecycle.py:11-61`; `memory/models.py:18-70`; `memory/attribution.py:6-23,47-94`; `memory/service.py:318-400`; `conversation/service.py:830-835,1076-1083` |
| E8 | Production `memory/models.py:73-112`; `memory/service.py:700-727`; `identity/resolver.py:41-80`; `persona/loader.py:86-104,148-188`; `schemas/conversation.py:30-72` |
| E9 | Production `stream/brain.py:622-623,682-688`; `vision/service.py:22-43,60-105`; `integrations/minecraft/protocol.py:82-85,228-243`; `performer/vtube_studio.py:360-376`; Discord voice/control modules present in production |
| E10 | Production `voice/live_runtime.py:12-17,49-80`; `performer/turns.py:51-85`; `performer/bus.py:55-72`; candidate `delivery/playback.py:18-42,109-131` and independent STT/TTS worker files |
| E11 | Candidate `brain/runtime.py:23-46`; `brain/process.py:24-49`; `brain/authority.py:17-46`; `brain/silent_work.py:15-37`; `brain/silent.py:73-138`; `memory/state_authority.py:105-145,254-287` |
| E12 | Production `presence.py:107-151,267-311,507-535`; current independent coding service owners |

The accepted composition source is
`/home/neety/Documents/gamma-foundation-backups/composition-accepted-20261011-30sqjm4c/source`.
The inspected acceptance records include:

- `activation-main-final-accepted-20261019-ref325xp/verification.json`, SHA-256
  `20df49eb2a12bcf0dd1f70f28e2d1399e0e84eb467a45e9f67a7350352714682`.
- `main-stop-installed-accepted-20261019-ypxlgn26/verification.json`, SHA-256
  `111217b90521611724b51c5b4c689c927c473dfebb31c1b72416e4fe453af9bb`.
- `compatibility-contract-20261019-mjaj4etj/verification.json`, SHA-256
  `5aad13d62c9989e5b21bc1d16a7e3f2f84b7dbfcbacb0b3cf70d7d5e8df82953`,
  explicitly a conditional proof with independent review still required.

Those records are under the same `gamma-foundation-backups` directory. Their
recorded test results remain historical evidence. No new inference, training,
tests, application imports, private-memory/database reads, Gamma UI/voice delivery,
job submission, activation, or service changes ran for this comparison. The
audit does not establish current latency, audibility, mood naturalness,
relationship quality, learned play, or continuity across real model swaps.

The closing preservation check compared all 231 captured production source
files and 256 candidate source files against their opening hashes. None changed.
All four observed service PIDs also remained unchanged. Only Windows discussion
documentation and the related Trello notes were updated by this comparison.

## Initial documentary capture, retained as background

The sections below preserve the earlier documentation-only comparison. The
source/runtime findings above are the current assessment and resolve its
previously pending comparison step. Older descriptions of unactivated work
must be read with the October 19 activation evidence above.

Gamma already has relevant components, specifications, experimental work, and
staged evidence. That supports reviewing what can be reused. It does not yet
establish that the full desired Shana experience works, or that a rewrite is
necessary.

This capture reread the Windows README, spec index, implementation inventory,
product, persona, memory, Presence, integration, Minecraft, audio-understanding,
coding-improvement, and modular-worker proposal documents. It also read the
existing direction board and its foundation history. It performed no new code
audit, inference, tests, runtime checks, migration, or service operation.

Descriptions below mean "documented in the inspected material." They are not
current live acceptance. Document dates, Windows source, Gamma-PC production,
the separate modular candidate, and other staged repair work must stay distinct.
Historical staged successes should be retained and traced, not called production
behavior or discarded because a fresh foundation is being considered.

## Initial map of existing work

| Desired direction | Relevant existing material | What still needs establishing |
| --- | --- | --- |
| D1: identity and growth | [Persona](persona.md), authored character, prompt assembly, emotion and memory records | Whether experience changes behavior coherently while retaining recognizable identity. Prompt assembly is not proof of learned personality. |
| D2: context and initiative | [Presence](shana_presence.md), bounded proactive scheduler, stream decisions, [earlier worker proposal](archive/2026-10-23/modular_worker_runtime_proposal.md) | How private conversation, observation, ongoing work, and streams share attention without competing owners or a large exception list. |
| D3: mood and expression | Persona/emotion persistence, [audio context](audio_understanding.md), voice profiles | Carrying mood across activities while preserving task reliability; natural expressive delivery requires actual listening evidence. |
| D4-D5: projects and independent work | [Coding improvement](coding_improvement.md), [improvement](improvement.md), checkpoints, job controls; earlier staged recovery/cancellation evidence | General goal ownership, real progress, bounded help seeking, and normal conversation during work. Narrow executor evidence does not establish the whole experience. |
| D6: memory and honest recall | [Memory](memory.md), journals, summaries, checkpoints, scoped retrieval; earlier staged source-attribution work | Desired retention, uncertainty, correction, consolidation, and demonstrated usefulness over time. Verify how staged work relates to the current runtime. |
| D7: relationships | Known-person and linked-account records, speaker resolution, privacy rules | Correct account links and relationship development across actual interactions without public/private information crossing audiences. |
| D8: streams | Stream decisions, Twitch IRC/EventSub, replay, performer events | Sustained, natural participation and enough independent activity to carry a stream. Event handling alone does not establish this. |
| D8: learning to play | [Minecraft companion](minecraft_companion.md), game observations/actions, separate offline screen-recognition work | Game-specific real control, learned mechanics, improvement after mistakes, and personal play style. The narrow Minecraft contract does not establish these. |
| D9: voice, Discord, avatar | [Live voice](live_voice.md), [integrations](integrations.md), performer/VTube adapter, historical Discord MVP evidence | Current end-to-end conversational quality, interruption, recognition of address, and visible desktop presence. Optional adapters and an audible MVP are narrower evidence. |
| D10: sleep | Existing Presence modes, independent jobs and lifecycle controls | New sleep semantics, worker handoff and catch-up, consolidation, and separate presentation/capability controls. |
| D11: replaceable components | Provider adapters, persisted records, [earlier modular proposal](archive/2026-10-23/modular_worker_runtime_proposal.md), earlier staged state work | Preservation of meaning and behavior during replacement, migration, and recovery; no accepted universal identity or skill-transfer method. |

## Conflicts to reconcile explicitly

| Existing statement | Current direction | Treatment in later planning |
| --- | --- | --- |
| [Earlier product plan](archive/2026-10-23/product.md) prioritizes assistant core and freezes new streamer integrations; authored persona says to be entertaining first | One Shana adapts to the purpose, including important work and casual play | The product entry point now links the new direction and preserves the old plan in the archive. Her runtime persona remains unchanged; its global priority needs reconciliation before implementation. |
| [Presence](shana_presence.md) permits explicit local requests during sleep | Sleep pauses active participation; workers continue existing instructions and decisions await waking | Keep the current behavior documented separately until a replacement sleep contract is chosen. |
| [Memory](memory.md) has specific journal retention defaults | Backup-memory extent and retention remain open | Preserve current operational settings; do not inherit their limits as new product requirements. |
| The implementation inventory says Discord replies/voice remain disabled; the integration spec and newer history describe a reply/voice path | Existing capability needs honest classification | Trace each checkout and current runtime before reconciling the inventory. Do not infer that Discord is absent or fully proven. |
| September worker proposal and later staged foundation repairs contain useful design and acceptance evidence | Reuse is an option; a full restart remains an open choice | Locate exact source, receipts, and active runtime. Keep source-only, synthetic, staged, and live evidence separate. |

The existing [foundation card](https://trello.com/c/5a57yINu/15-which-foundation-changes-are-needed)
contains earlier work and its limits, including staged acceptance and unfinished
readiness/activation work. Its dated "current job" is historical context to
refresh, not an instruction to resume it. No earlier verification rejection or
unaccepted result is resolved by this documentation capture.

## Suggested next work

### 1. Reconcile the direction with actual Gamma

For each direction section, identify the owning code and state, its callers,
and the evidence for behavior. Record implemented, partial, proposed, wanted,
or unknown, with checkout, date, and source. Review the existing production,
candidate, and staged-repair records before recommending new abstractions.

The output should recommend what to keep, adapt, replace, defer, or investigate,
with a concrete reason. It should explain ownership and interactions across the
whole system, rather than produce an unconnected feature inventory. Runtime
checks, if later authorized, belong on Gamma PC and should exercise the intended
path. This capture does not start that work.

### 2. Choose a small set of observable experiences

Use situations the user has already described, adding questions only where a
choice affects the foundation. Candidate experiences are:

- Focused homework followed by relaxed gaming and a stream. Attention and
  initiative change with purpose while commitments and mood carry forward.
- A task running while normal conversation continues, including a real blocker,
  a request to switch, and an honest completion report.
- Repeated interaction with a linked person across channels, including a shared
  memory, growing comfort, disagreement, and an uncertain recollection.
- Sleep while an instructed worker continues, followed by wake and catch-up.
- A familiar gaming situation revisited after a mistake, with observable change
  in play and continuity of shared goals.
- A replacement model or voice tried against those same histories. Check
  recognizable judgment and expression as well as facts and task results.

These are evaluation examples, not six independent behavior engines or a promise
to implement all of them in the first milestone.

### 3. Compare foundation options against that evidence

Compare consolidation of existing code, reuse of the modular/staged work, and
a smaller fresh implementation. Explain which ownership problems each option
solves, what it preserves, and the cost of migration and future replacement.

Then describe the smallest coherent first foundation and what it deliberately
defers. Interfaces and separate processes should answer demonstrated needs.
Keep the broader Shana direction visible without building speculative systems
for every eventual capability. No option is selected by this note.

## Tracking and completion of this capture

The direction file holds detailed product intent. This note holds the initial
comparison and proposed next work. Trello should link those records and track
the next discussion/review, retaining older evidence in place.

Saving the notes does not mark the foundation complete or authorize a rewrite,
training, activation, or deployment. The next substantive decision is what
existing work fits the desired experience and what needs to change.

## Documentation organization and Trello

Seven older planning and assessment documents were moved, with contents
unchanged, to the [2026-10-23 archive](archive/2026-10-23/README.md).
`product.md` and `roadmap.md` now point to this discussion, and the spec index
links current direction separately from older plans. Active runtime references
remain in place for evidence-based reconciliation.

The existing [foundation question](https://trello.com/c/5a57yINu/15-which-foundation-changes-are-needed)
tracks the discussion and proposed next steps. The existing
[character-growth decision](https://trello.com/c/UBqAFn5a/2-let-shanas-character-grow)
has a dated clarification of starting tendencies and continuity. Earlier
descriptions, decisions, and staged-work history remain preserved.
