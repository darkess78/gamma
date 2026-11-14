# Batch 06 Memory-Triage Authoring Specification

Status: **Owner reviewed; candidate drafting and owner-approved canonical migration complete.**

Scope: **Finalized semantic inventory, reviewed candidate drafting, and completed migration provenance.**

Theme: **Stream and game memory-triage coverage.**

Planned size: **exactly 50 future primary-eligible examples.**

Planned balance: **exactly 25 `keep` and 25 `skip`.**

The owner approved the finalized semantic inventory for review-only candidate
drafting, reviewed the resulting 50 candidate rows, and explicitly approved
all 50 for canonical migration. The Batch 6 migration is complete and draft
statuses record `approved_for_migration`. Training, evaluation, split
assignment, notebook execution, runtime integration, staging, committing, and
pushing remain blocked. The migration was based on direct inspection of the
actual 307-row pre-migration canonical dataset. This is not a decision to
retain real viewer, account, or stream data.

## 1. Current Evidence Inspected

The review covered repository `README.md`, `specs/README.md`,
`specs/current_implementations.md`, and
`specs/class-projects/cs4700-memory-triage.md`; the research workspace and data
READMEs; the pilot and canonical primary CSVs; the labeling guide, dataset
card, pilot review, blinded self-relabel protocol, and final expansion plan;
all Batch 01 through Batch 05 authoring specifications, candidate-draft CSVs,
candidate reviews, and migration records; and the notebook structure and cell
source without executing a cell.

Pre-migration CSV inspection confirmed 307 canonical rows: 155 `keep` and 152 `skip`.
Provenance consists of 57 pilot rows and 50 rows from each of Batch 01 through
Batch 05. All 307 rows have `primary_eligible=true` and `split=unassigned`.
Row-level release provenance remains 107 rows from `primary_v0_1`, followed by
50 rows each from `primary_v0_2`, `primary_v0_3`, `primary_v0_4`, and
`primary_v0_5`; the latest release value applies only to Batch 05 rows.

Every canonical source-group and paraphrase-group value is nonblank and unique.
Shared pilot groups exist only around policy-only source rows excluded from the
canonical primary dataset. This convention supports one independently drafted
concept per current primary row and requires later Batch 6 grouping review
rather than preassigning group identifiers in this plan.

Candidate-text inspection found no dedicated stream/game slice. A few rows use
words such as audience, viewer, channel, or demonstration in unrelated review,
accessibility, routing, or presentation contexts. They do not cover gameplay
progress, spoiler boundaries, coaching across sessions, stream-series
commitments, viewer-interaction preferences, or the practical line between a
game session and future-stream value. The prior corpus is strong on abstract
workflow, project, evidence, role, and interpretation facts, but needs a more
practical stream/game slice.

## 2. Stream/Game Memory-Triage Framing

The central test is whether the information would improve support or
interaction in a separate future stream or game conversation. Durable value
can come from a preference, constraint, goal, responsibility, correction, or
milestone whose consequence carries across sessions. Immediate context can be
highly useful during play and still be a `skip` when its value ends with a
particular match, run, stream, event, or moment.

A game topic cannot determine the label. Both labels must use credible game,
stream, audience, production, community, and account-progress surfaces. A
persistent progression fact is `keep` only when it changes later support,
planning, recommendations, or interaction. A vivid boss status, strong build,
valuable drop, or active stream plan can remain `skip` when the text establishes
only local session value.

The slice should include durable preferences and constraints without assuming
that every player or viewer fact deserves retention. Structured-profile-like
facts are permitted as synthetic classification concepts, but they do not
authorize a player profile, progress tracker, or memory schema. This is
research-data planning, not runtime memory policy, stream moderation policy, or
a decision about how any retained fact would be stored.

## 3. Current Coverage And Gap Analysis

### High-density families to avoid renaming

Existing rows densely cover communication and accessibility preferences;
approval, testing, privacy, evidence, handoff, and reversible-work rules;
named-system architecture, compatibility, provenance, lifecycle, fallback,
and interpretation facts; generic reviewer and coordinator roles; meaningful
project milestones; transient states; one-artifact instructions; obsolete or
unadopted facts; local technical conditions; source-authority defects; and
isolated events. Batch 4 and Batch 5 add the specific evidence, attribution,
interpretation, role, exception, correction, replay, metric, portability,
layout, aggregate, and lookup boundaries listed in this task's anti-overlap
constraints. Adding a game or channel name to those families would not create
new coverage.

### Existing adjacency and confirmed gaps

Stream/game-adjacent wording is sparse and incidental: accessibility review of
captions, audience-facing summaries, cross-channel routing, public
demonstrations, and community-interface roles. None supplies a coherent
cross-label test for gameplay progression, a named synthetic account
restriction, future spoiler/help behavior, a recurring stream format, or the
difference between an audience convention and one chat moment. Confirmed gaps
therefore include durable play boundaries, account/challenge constraints,
future-relevant progression, recurring series direction, recurring audience
interaction, gameplay production/accessibility defaults, and matched
session-bounded hard negatives on the same surfaces.

### Boundaries and owner-scrutiny risks

The owner should scrutinize each of these distinctions during review:

- durable game goal versus one session goal;
- lasting account or challenge constraint versus a fact consumed by a named
  game mode, quest mechanism, or account display convention;
- recurring spoiler or hint preference versus one request for help;
- recurring stream interaction preference versus a scoped analytic,
  directory, credit, or archive interpretation;
- meaningful progression milestone versus one match, loot, or quest event;
- recurring series commitment versus a single event or stream title;
- persistent setup preference versus a presentation fact tied to the material
  being shown; and
- stable community convention versus a non-governing badge, listing, or
  archive label.

Tentative opportunities include cross-session coaching calibration, progress
that changes future recommendations, durable challenge integrity, recurring
viewer-choice formats, stable collaboration duties, series/account continuity
rules, and preparation conventions that affect both play and broadcast
support. Each can drift into a generic preference, system rule, role fact, or
milestone already present. A slot should be rejected if its stream/game
consequence disappears when the surface nouns are removed, or if it merely
retells a Batch 4/5 boundary.

## 4. Batch 6 Semantic Coverage Strategy

All 50 slots directly use a stream, game, streaming-community, gameplay,
series, account-progress, or game-session surface. Game/stream terminology
must appear meaningfully in both labels. Mentioning a game, stream, viewer,
chat, account, quest, boss, match, build, loot, or setup cannot determine a
label by itself.

Future drafting must spread syntax, tense, synthetic names, source styles, and
levels of detail across both labels. `Keep` examples must establish a real
cross-session consequence. `Skip` examples must provide a precise,
text-contained reason for session, episode, artifact, or relationship scope.
High-confidence skips may not depend as a group on words such as current,
today, this stream, one-time, temporary, or just now; high-confidence keeps may
not depend as a group on always, recurring, ongoing, persistent, permanent, or
standing.

Skip coverage must also vary the reason future value fails: game mechanics may
consume a fact, a stream measure may answer a narrower question, or a label,
credit, directory entry, or presentation choice may carry less meaning than
its surface wording suggests. No fallback construction should dominate the
revised rows.

A slot is not novel merely because a game title, stream title, character,
weapon, quest, boss, viewer, or channel name changes. Later text must use only
synthetic games, streams, accounts, and communities, never real platforms,
usernames, accounts, games, or identifiable communities. No slot may contain
sensitive health, identity, private-chat, credential, purchase-history,
location, or real-life relationship data. No concept may become a policy-only
moderation rule.

## 5. Fifty-Slot Semantic Inventory

These are concept descriptions, not candidate text or drafting templates. Each
slot is intended to become one standalone, primary-eligible example only after
separate owner-approved drafting and review.

| Slot | Planned label | Stream/game surface | Semantic family | Decision boundary being tested | Scope requirement | Candidate type or source-style target | Novelty / anti-overlap note | Planned grouping boundary |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B06-01 | `keep` | Synthetic adventure-game campaign | Spoiler boundary | Reveal tolerance carries across later campaign sessions and changes assistance | Name synthetic game or campaign | Preference; extracted play boundary | Not generic privacy/permission or artifact scope; no prior spoiler family | One reveal-depth concept; exclude hint frequency and single-scene requests |
| B06-02 | `keep` | Synthetic puzzle-game sessions | Coaching style | Preferred escalation from prompts to direct help governs later puzzle support | Name synthetic game | Preference; coaching profile | Distinct from generic explanation depth and clarification preferences | One coaching progression; no variants by puzzle type |
| B06-03 | `keep` | Synthetic strategy-game account | Long-lived game goal | Achievement direction shapes later planning and recommendation tradeoffs | Name synthetic game and goal domain | Project/profile; goal summary | Avoid generic project objective and simple game liking | One goal arc; exclude session targets and milestone completion |
| B06-04 | `keep` | Synthetic challenge-run save | Challenge constraint | Self-imposed restriction determines valid future tactics across the run | Name synthetic game and challenge | Boundary; account/challenge profile | Not a generic system rule or forced calibration choice | One restriction set; group all later tactic variants with it |
| B06-05 | `keep` | Synthetic game account progression | Consequential milestone | Unlocked capability changes which later routes or recommendations are feasible | Name synthetic account and consequence | Episodic; progression milestone | Requires future-support consequence, not a loot/event record | One unlock consequence; exclude score and attempt-count variants |
| B06-06 | `keep` | Synthetic cooperative game | Stable play preference | Durable role or pace preference guides later cooperative-session planning | Name synthetic game/context | Preference/profile; play-style summary | Avoid generic role facts and broad “likes game” statements | One play-style dimension; no character-name substitutions |
| B06-07 | `keep` | Synthetic competitive-game practice | Feedback boundary | Post-match coaching focus remains useful across later practice sessions | Name synthetic game and practice context | Preference; coaching-review style | Not generic reviewer feedback or one replay critique | One feedback dimension; exclude match-result observations |
| B06-08 | `keep` | Synthetic game account | Account restriction | Account-level access limitation rules out categories of later advice | Name synthetic game/account context | Profile/boundary; constraint summary | Structured-profile-like only; no credentials or platform facts | One safe restriction; exclude current inventory/loadout |
| B06-09 | `keep` | Synthetic quest series | Durable correction | Corrected interpretation of a game system changes later route planning | Name synthetic game/system | Project; correction summary | Must differ from generic metric/schema/authority corrections in B02–B05 | One mechanic interpretation; no patch-note or single-quest variants |
| B06-10 | `keep` | Synthetic episodic game series | Series commitment | Chosen campaign direction creates a future content and play obligation | Name synthetic series | Project; continuing commitment | Not outside promise, deferred choice, or single event plan | One campaign commitment; exclude stream scheduling |
| B06-11 | `keep` | Synthetic gameplay stream | Audience-interaction preference | Preferred timing for audience input governs later broadcasts | Name synthetic stream context | Preference; audience-interaction profile | New stream surface, not generic interruption or approval workflow | One input-timing rule; exclude poll mechanisms and viewer suggestions |
| B06-12 | `keep` | Synthetic channel chat | Hint/spoiler convention | Established chat participation boundary shapes future stream interaction | Name synthetic channel/series | Boundary; community convention | Must remain interaction preference, not moderation policy | One safe convention; exclude enforcement or sanctions |
| B06-13 | `keep` | Synthetic gameplay broadcast | Production/accessibility preference | Stable presentation need changes later game-stream output | Name synthetic broadcast context | Preference; accessibility profile | Extends accessibility only through meaningful gameplay presentation | One accessibility dimension; no generic caption or layout paraphrases |
| B06-14 | `keep` | Synthetic stream series | Recurring format direction | Adopted segment structure guides future episodes | Name synthetic series | Project; format commitment | Not generic ordering/layout or one stream title | One series format; exclude episode-level run sheet |
| B06-15 | `keep` | Synthetic streaming community | Community interaction preference | Preferred handling of audience choices affects later participation design | Name synthetic community/series | Preference; community profile | Not group-selection attribution or audience-summary inference | One participation mode; exclude isolated poll outcomes |
| B06-16 | `keep` | Synthetic co-stream series | Stable collaboration responsibility | Safe recurring on-stream duty changes future preparation and handoff | Name synthetic series and role scope | Profile; recurring role | Avoid generic recorder/reviewer/liaison roles from prior batches | One stream-specific duty; no real identity or authority inflation |
| B06-17 | `keep` | Synthetic channel production | Durable setup preference | Stable gameplay presentation setup improves later broadcasts | Name synthetic channel/context | Preference/project; production profile | Do not overfill setup preferences or restate system compatibility | One production choice with viewer consequence; exclude troubleshooting |
| B06-18 | `keep` | Synthetic challenge stream | Audience-boundary preference | Degree of audience steering accepted across the series guides later interaction | Name synthetic challenge series | Boundary; stream interaction profile | Distinct from spoiler depth and generic approval | One steering boundary; exclude one viewer or episode vote |
| B06-19 | `keep` | Synthetic stream archive series | Content-format commitment | Maintained recap approach supports continuity for later episodes | Name synthetic series | Project; production commitment | Not generic audience summary, replay semantics, or filing/layout | One recap purpose; exclude artifact formatting |
| B06-20 | `keep` | Synthetic community game night | Stable community convention | Repeated participation arrangement creates future hosting responsibility | Name synthetic event series/community | Profile/project; commitment | Must create an ongoing consequence, not a ceremonial role or event reaction | One hosting responsibility; exclude moderation rules and single events |
| B06-21 | `keep` | Synthetic campaign stream and game account | Bridge: campaign-obligation continuity | A maintained ledger of promises made during play constrains later game routes and prepares future broadcasts to surface conflicting obligations before choices | Name synthetic account and series; state both route-support and audience-context consequences | Project/profile; series-continuity record | Distinct from recap format, campaign direction, progression phase, mechanic correction, and spoiler/help families; the bridge is the future consequence of player-made commitments | One campaign-obligation ledger; exclude episode summaries and generic decision records |
| B06-22 | `keep` | Synthetic expedition game and stream opening | Bridge: save-handoff preparation | A cross-session save-position convention preserves a usable broadcast handoff and shapes both gameplay stopping advice and the next stream opening | Name synthetic game and series; identify the save-state and presentation consequences | Boundary/project; preparation convention | New game/stream preparation consequence, not coaching, setup compatibility, series format, or a progression milestone | One save-handoff convention; exclude technical setup and generic recap wording |
| B06-23 | `keep` | Synthetic progression series | Bridge: milestone-to-format | Game milestone changes the next phase of stream content and future recommendations | Name synthetic game/series and consequence | Episodic/project; milestone | Not a simple unlock, title change, or checkpoint state | One phase transition; exclude individual loot and episode plans |
| B06-24 | `keep` | Synthetic exploration game and stream community | Bridge: shared landmark vocabulary | Community-created landmark terms remain aligned with gameplay route notes so later play guidance and stream discussion refer to the same places | Name synthetic game and series; state both navigation and audience-context consequences | Project/profile; cross-session terminology convention | Stream/game vocabulary alignment is distinct from mechanic correction, generic terminology preference, recap continuity, and audience steering | One shared landmark lexicon; exclude corrected mechanics, episode titles, and chat jokes |
| B06-25 | `keep` | Synthetic campaign account and stream series | Bridge: on-stream decision reservation | A series constraint reserves irreversible campaign choices for broadcasts, changing safe account preparation and future audience-facing decision segments | Name synthetic account and series; distinguish preparation from irreversible decisions | Boundary/project; account-series decision constraint | Not a challenge restriction, audience-selected choice, campaign direction, or special-event rule; it governs where consequential decisions happen | One decision-reservation rule; exclude off-broadcast story progression variants grouped with B06-21 |
| B06-26 | `skip` | Synthetic action-game run | Encounter loadout | Equipment choice solves a narrow encounter without establishing later play preference | Name synthetic game/run and encounter scope | Project-like; session extraction | Hard negative to B06-06/B06-08; not generic no-selection | One loadout episode; no weapon/boss substitutions |
| B06-27 | `skip` | Synthetic boss attempt | Attempt status | Position within an attempt helps immediate play but changes no later guidance | Name synthetic boss/run scope | Episodic; gameplay state | Not checkpoint semantics or meaningful milestone | One attempt state; exclude counts, loot, and strategy |
| B06-28 | `skip` | Synthetic quest session | Quest-step context | Completed step identifies session position without future planning consequence | Name synthetic quest/session | Episodic; progress-looking hard negative | Contrast B06-05 without using obsolete wording as the cue | One quest step; exclude campaign phase changes |
| B06-29 | `skip` | Synthetic competitive match | Match tactic | Opponent-specific tactic does not establish a general coaching preference | Name synthetic match condition | Boundary; tactical note | Not generic local-environment exception or surrogate compatibility | One matchup tactic; exclude loadout and score variants |
| B06-30 | `skip` | Synthetic arena draft mode | Match-issued roster state | A drafted companion looks account-relevant but the game mode returns it to the arena pool when the match closes | Name synthetic game mode and its explicit state-reset rule | Profile-like; gameplay-state extraction | Text-solvable state consumption rather than loot, missing relevance, build testing, or account restriction | One mode-issued roster fact; exclude inventory drops and selected builds |
| B06-31 | `skip` | Synthetic ranked match | Score/attempt result | Performance result records an episode without changing practice direction | Name synthetic match | Episodic; result summary | Not wrong-question measurement or metric interpretation | One result type; no score-number variants |
| B06-32 | `skip` | Synthetic infiltration quest | Quest-issued identity state | A convincing character identity is issued and consumed by one quest's access mechanism rather than becoming an account profile fact | Name synthetic game, quest mechanism, and consumption point | Profile-like; quest-state extraction | Not a mock state, role assignment, build trial, or missing-information case | One quest-issued identity; exclude lasting character customization |
| B06-33 | `skip` | Synthetic racing game account | Event-seed ranking display | A prominent rank is calculated only within a named seed bracket and is replaced when the account enters another seed | Name synthetic game and bracket-scoping rule | Profile-like; account-display interpretation | Distinct from match result and generic finite role; tests local ranking versus account standing | One seed-bracket rank; exclude scores and durable league tiers |
| B06-34 | `skip` | Synthetic labyrinth instance | Instance-bound map annotation | A route mark is valid only for a procedurally generated instance and the game discards it when that instance closes | Name synthetic game and explicit instance lifecycle | Episodic; gameplay navigation state | Game-mechanic scope, not technical conditions, missing anchors, or loot | One generated-instance annotation; exclude campaign route corrections |
| B06-35 | `skip` | Synthetic disguise quest | Quest-required appearance | An apparent cosmetic preference is fully explained by a quest access rule that restores the account appearance after the restricted area | Name synthetic game, disguise purpose, and restoration rule | Preference-like; gameplay-state extraction | Hard negative to stable play/style preferences without mock data, teaching fixtures, or absence reasoning | One quest-bound appearance; exclude player-selected cosmetics and loadouts |
| B06-36 | `skip` | Synthetic gameplay stream | Episode title/theme | Broadcast framing organizes one episode without selecting a series direction | Name synthetic episode and series | Project-like; stream metadata | Hard negative to B06-14; not generic layout or ordering | One episode framing concept; exclude segment order |
| B06-37 | `skip` | Synthetic gameplay-stream analytics | Exposure-inflated audience count | A large audience figure includes automatic preview impressions and therefore measures exposure rather than engaged viewing or content preference | Name synthetic stream report and the included exposure source | Project-like; analytics interpretation | Stream-specific measurement boundary, distinct from poll attribution, viewer suggestion, and generic aggregate-to-member inference | One exposure-versus-engagement measure; exclude causal performance claims |
| B06-38 | `skip` | Synthetic channel directory | Discovery-category listing | A game category attached by a directory index supports finding a broadcast but does not establish the channel's content direction or community preference | Name synthetic channel/series and directory function | Project-like; metadata interpretation | Locator function on a stream surface, not episode ordering, run-sheet logic, or filing-location paraphrase; tests discovery metadata versus editorial direction | One discovery listing; exclude archive headings and episode themes |
| B06-39 | `skip` | Synthetic channel chat | Chat joke/analogy | Shared joke explains a moment but creates no convention or game interpretation | Name synthetic stream moment | Episodic; community reaction | Not generic casual reaction alone or B03 local analogy | One joke origin; exclude poll/reaction variants |
| B06-40 | `skip` | Synthetic stealth-game broadcast | Encounter-caused commentary behavior | A noise-reactive encounter explains subdued commentary during one passage without establishing a hosting or narration preference | Name synthetic game broadcast and the encounter mechanism that bounds the behavior | Preference-like; encounter-behavior extraction | The game state causes the apparent presentation choice; distinct from coaching, challenge rules, troubleshooting, and durable production preferences | One encounter-caused commentary behavior; exclude broader speaking style and accessibility needs |
| B06-41 | `skip` | Synthetic channel community badge | Participation-history badge | An impressive badge records entry into an early audience cohort but carries no production authority, moderation power, or hosting responsibility | Name synthetic channel and the badge's defined meaning | Profile-like; community-status interpretation | Distinct from finite guest roles, special-event rules, and ceremonial project recognition; tests community status versus operational responsibility | One cohort badge meaning; exclude identities and permission policy |
| B06-42 | `skip` | Synthetic quest-stream dialogue | In-world time versus broadcast schedule | A future-sounding time reference schedules an encounter inside the active quest rather than another broadcast or community gathering | Name synthetic quest stream and make the fictional clock domain explicit | Project-like; scoped time-reference interpretation | Stream/game time-domain boundary rather than a missing date, cancelled plan, event configuration, or series commitment | One in-world rendezvous reference; exclude real broadcast schedules and recurring events |
| B06-43 | `skip` | Synthetic stream chat | Transient reaction context | Audience reaction explains an immediate presentation change only | Name synthetic broadcast moment | Episodic; chat-context note | Not generic audience summary or causal overreach | One reaction-driven adjustment; exclude joke and technical issue |
| B06-44 | `skip` | Synthetic tabletop-game broadcast | Consumable set-piece state | A prominent set arrangement exists because the physical game piece is progressively consumed during one round, without defining a reusable broadcast setup | Name synthetic tabletop broadcast and the set piece's gameplay lifecycle | Episodic; physical-game state extraction | A material game component is altered by play; distinct from contribution, recognition, role, run-sheet, technical setup, and artifact-label families | One consumable set-piece lifecycle; exclude durable scenery and production-layout preferences |
| B06-45 | `skip` | Synthetic atmospheric-game broadcast | Incidental scene light | A visible color cast comes from a bounded game scene reflecting onto the host rather than a selected channel palette or later production choice | Name synthetic game broadcast and the optical cause within the scene | Project-like; incidental production observation | Scene-emitted light is evidence about that captured moment, not source fidelity, overlay state, troubleshooting, or a reusable presentation setting | One scene-caused color cast; exclude stable visual identity and accessibility choices |
| B06-46 | `skip` | Synthetic boss-attempt stream | Bridge: one help request | Direct-help request applies to a particular attempt without changing later hint boundary | Name synthetic game, stream, and attempt | Boundary; interaction extraction | Hard negative to B06-21; no reliance on “just this time” wording | One attempt/help decision; exclude viewer suggestion |
| B06-47 | `skip` | Synthetic game-stream replay commentary | Bridge: replay-perspective choice | A cautious route recommendation is explained by a replay camera that hides off-screen hazards, so it governs neither live account strategy nor later stream presentation | Name synthetic game, replay context, and visibility limitation | Boundary/project; combined replay interpretation | Plausibly reusable advice defeated by replay perspective; not an event restriction, technical condition, challenge, or mock state | One replay-view recommendation; exclude mechanic corrections and coaching preferences |
| B06-48 | `skip` | Synthetic quest broadcast | Bridge: episode goal | Quest target organizes one broadcast but does not establish campaign direction | Name synthetic quest and episode | Project/episodic; session goal | Hard negative to B06-10/B06-23; not generic one-off planning | One episode objective; exclude quest-step state |
| B06-49 | `skip` | Synthetic campaign account and stream archive | Bridge: logistical stopping point | An account location looks like campaign direction but was chosen solely to create a clean recording break and commits neither later play nor series content | Name synthetic game, archive break, and resulting account location | Project/episodic; combined handoff fact | Not an audience choice, episode goal, missing selection, or narrow configuration; distinguishes archive logistics from campaign intent | One recording-break location; exclude durable save-handoff conventions grouped with B06-22 |
| B06-50 | `skip` | Synthetic game stream and subtitle track | Bridge: localized display alias | A character alias appears in one language track to fit subtitle timing and governs neither the game account identity nor the community's later terminology | Name synthetic game/series and localization purpose | Profile/project; combined representation fact | Surface looks like durable game-stream vocabulary but is source-track representation, not technical compatibility, collective choice, or placeholder naming | One localized subtitle alias; exclude shared landmark vocabulary grouped with B06-24 |

## 6. Coverage And Balance Audit

The inventory contains exactly 25 `keep` and 25 `skip` slots. Within each
label, slots 1–10 of its block are primarily gameplay/account/progression,
slots 11–20 are primarily stream/audience/production/community, and slots
21–25 bridge game and stream consequences. Thus each label contains 10
gameplay/account concepts, 10 stream/community concepts, and 5 bridge concepts.

| Planned label | Gameplay/account | Stream/community | Bridge | Total |
| --- | ---: | ---: | ---: | ---: |
| `keep` | 10 | 10 | 5 | 25 |
| `skip` | 10 | 10 | 5 | 25 |

Source-style coverage is deliberately mixed across preference, profile,
project/system, boundary, and episodic/milestone forms in both labels. Both
labels contain meaningful gameplay and streaming/community examples. Matched
surfaces put durable spoiler, coaching, progression, challenge, format,
interaction, collaboration, preparation, and account-series continuity
concepts beside encounter mechanics, scoped analytics, directory metadata,
credits, archive labels, and representation-bound bridge facts. This prevents
“game topic = label” shortcut learning and requires the future consequence to
carry the decision.

Unlike Batches 1–5, this plan concentrates every slot on the practical
cross-session stream/game boundary rather than expanding abstract workflow,
project, evidence, interpretation, or role coverage. All 50 slots are intended
primary eligible. No slot is policy-only, depends on existing-memory state, or
represents a production retention-policy decision.

The approximately 200 `keep` / 200 `skip` target remains current in the actual
expansion plan. After the approved migration, the canonical balance is 180
`keep`, 177 `skip`, and 357 total, leaving approximately 20 `keep` and 23
`skip` toward the target.

## 7. Completed Owner Review And Migration Gates

The owner approved revision of the listed held concepts for review-only
candidate drafting, approved all 50 final rows, and approved their canonical
migration. The migration re-audited the final drafts against the 307-row
canonical dataset for privacy, scope, exact duplicates, conceptual overlap,
lexical shortcuts, stream/game-topic shortcut risk, and source/paraphrase
grouping. All 50 rows were appended in order as `mtb06_0001` through
`mtb06_0050` without changing prior canonical rows.

Training remains blocked until the final target, data cleaning,
group review, and frozen untouched group-aware holdout steps are complete.
Evaluation, split assignment, and notebook execution remain blocked until
separately approved. Runtime integration remains blocked and requires a
separate explicitly approved task. Any future classifier remains advisory-only
and disabled by default unless a later approved runtime task changes that
status. Staging, committing, and pushing also remain blocked.
