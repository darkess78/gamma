# P5 private voice and presence

Status: owner-private production DMs and voice passed real checks on October 31.
The owner confirmed hearing a reply while the bot stayed connected. The current
permanent-operation section below records scope and evidence. Earlier P5 receipts
retain their isolated, SDK-fixture scope; broader listening quality remains open.

## What changed

Private voice, desktop text and the prepared Discord adapter use the same Core,
canonical identity, personal history, activities and typed decision contract.
STT supplies an observation. The Core chooses a reply, acknowledgment, silence,
deferral or an already permitted capability. The existing conversation parser,
privacy and speech safety checks finalize its text before TTS can use it.
Ordinary private Monitor text still requests no speech.

The voice observation carries its channel, declared source, optional address
cue and recognition uncertainty. A name is optional. A declared other or unknown
speaker is rejected before private recall or decisions. The desktop owner-source
checkbox declares who is using the microphone; it does not recognize a voice.
Discord binds the exact configured account and private recipient instead.
Neither a name cue nor a speaker label expands action authority.

Voice serialization omits unused null fields and empty arrays. The parser fills
the same defaults. Every existing field, bound, capability and source rule is
retained. Per-call context selects this grammar without constructing another
conversation owner. Existing private text instructions and grammar retain their
previous exact hashes in that earlier P5 change. The October 31 candidate below
extends the same compact contract to admitted Monitor/phone owner inputs;
unconfigured defaults remain unchanged. Safety checks remain independent; the
optional release profile changes model retention and reviewer placement.

## Current owner questions

The October 31 candidate treats the current admitted owner utterance as the
request. A name cue is optional in a private owner conversation. A direct question,
including a repeated question or an uncertain memory question, calls for a checked
reply or acknowledgment; unknown recall calls for an honest answer or clarification.
Earlier identity statements or earlier answers do not turn the current question
into thinking aloud. Current explicit quiet, self-directed thinking aloud without
an answer request, questions clearly addressed to another person, and quoted
questions without a request to Shana retain silence. Typed decisions and the
existing authority/privacy checks still decide delivery; there is no keyword rule
that forces speech. Monitor and phone remain text-only. Quiet reviews do not gain
the trusted owner-input marker or an obligation to speak.

## Audio and delivery

The runtime accepts bounded PCM WAV observations, up to 12 seconds. A finalized
reply can produce up to four ordered chunks with a combined limit of 90 seconds
and 4 MB. The first chunk is available while later synthesis continues. Oversized
speech retains its display text and an explicit speech-limit receipt.

Each output binds the Core generation, accepted turn sequence, consumer lease,
output epoch, source turn and expiration. The runtime rechecks these before
synthesis, publication, audio fetch and acknowledgment. Forgetting its source,
sleep, a newer turn, privacy loss, disconnect or interruption removes release
authority. Restart disables voice and speech and cannot replay old audio.

Generated, offered, attempted, started, played and delivered are distinct states.
The browser reports started and played from actual media events. Discord text
records attempted before send and delivered after the SDK receipt. These are
consumer reports, not proof that a person heard or read the output. The Core sees
bounded delivery receipts; one played chunk cannot imply the entire answer was
heard. A later synthesis failure returns a partial receipt immediately and keeps
the earlier chunk's actual outcome.

There are at most two active callers, one foreground decision and one synthesis
callback. In the October 31 candidate, admitted audio transcribes before acquiring
the foreground slot, then waits at most 30 seconds in 50 ms slices. Each wait
rechecks awake state, lease, epoch and captured playback-source validity. Cancellation
or timeout produces a fixed receipt without inference replay. Text retains its
immediate busy receipt, and a third caller remains bounded. Confirmed non-echo
speech during older playback/synthesis cancels the remaining chunks. Source
removal, sleep, privacy loss and Stop retain their existing invalidation fences.
Core/backend checkpoints skip superseded queued work and later review stages;
already submitted backend calls can finish, with late results discarded.

The browser keeps capturing while audio plays. Echo comparison uses the
same-consumer playback active when audio arrived, so a later played receipt does
not erase that observation during STT. The comparison is conservative text
matching. An intentional exact repetition can still look like echo. Real room
echo, overlap and automatic speaker identification remain unaccepted.

## Timing and outcome diagnostics

Interaction receipts expose numeric monotonic durations in `timing_ms`: `stt_ms`,
`foreground_wait_ms`, `core_ms`, `synthesis_wait_ms`, `total_ms` and, when audio
is produced, `first_audio_ready_ms`. These measurements include component and enclosing durations; they should not
be summed. Conversation responses also retain bounded primary load,
prompt evaluation, generation, admission/HTTP and independent review timings.
The Discord transport keeps bounded numeric capture, end-of-speech, dispatch,
queue, request and playback-event records without transcripts or PCM.

A fixed backend `final_outcome` explains generated, silent, failed, busy or
cancelled results without copying model reasons or private content. Numeric
request timings and the original outcome survive ordinary receipt expiry.
Forgetting the source still removes operational private text/audio and replaces
the outcome with the fixed source-removal diagnosis; it cannot authorize replay.
Outputs have separate `attempted_at`, `started_at`, `played_at` and
`delivered_at` timestamps. `acknowledged_at` remains compatible, and duplicate
ACKs do not retime an event. First audio availability and transport playback
receipts remain distinct from a person's hearing acceptance.

## Presentation and Discord

`/dashboard/presence` is a thin private presentation client. It supports avatar
hide/show, compact layout, placement, speech mute, listening disable, sleep and
explicit reconnect. Hide and placement stay local. Mute and listening disable
are capability controls; sleep belongs to the Core. Hiding the avatar does not
stop conversation or existing work. The SVG face is provisional artwork.

Private confirmation is initially off. Removing it clears private text, ends
capture, aborts owned requests and disconnects the consumer. The client checks
exact current output state as well as generation and epoch, including source
invalidation that leaves the epoch unchanged. Chunk order, gaps, duplicate
fetches, delayed acknowledgments and stale permission callbacks are checked.

Use **Brave** for this project. `scripts/private_presence_client.pyw` defaults to
the installed Brave app-window executable. Its argument validation and hidden
helper launch paths were tested. Actual standalone Brave window behavior and
physical microphone/playback acceptance remain for the later owner review.
The brief interactive Edge review was stopped after the browser preference was
clarified. No existing user browser tabs were deliberately closed.

The separate Discord adapter is offline by default. It requires an explicit
dedicated test bot, pinned owner/DM IDs and, for voice, the configured private
guild/channel with only the owner and bot present. It reuses the existing owner
voice controls and secure receive boundary. Two fixed receive workers and a
bounded queue allow newer input during synthesis. Heartbeats renew an existing
lease without reconnecting or replaying. Provider acceptance of an in-flight
external send cannot be atomically revoked by SQLite; uncertainty remains
recorded and is not automatically retried.

No Discord login, message, voice join or external receipt was exercised. Real
Discord delivery and leave/rejoin acceptance remain deferred. The prepared
runner reads no token and imports no SDK until explicit network opt-in; a
production bot token is not selected implicitly.

## Evidence and remaining quality limits

All execution, inference and tests ran in the isolated Gamma candidate. Windows
helpers and nested CLI launches used pythonw and CREATE_NO_WINDOW with logs.
This does not certify the absence of every possible terminal flash.

| Check | Observed result |
| --- | --- |
| [Real audio and browser](../data/coordination/p5/audio-browser-f5da2f95df4b45dc8fcbc01ff961f572/report.json) | Qwen-generated input, actual Whisper transcription, same inherited Core identity, checked response, actual Qwen TTS and two browser Audio chunks. Every started/played ACK returned 200, each chunk fetched once. |
| [Real practical worker and voice](../data/coordination/p5/worker-voice-flow/dc906df520434d8d9969b5bf0ff98341/report.json) | One actual coding attempt, protected checks and independent CLI verification. Voice began during actual worker execution. Mute, sleep completion and wake retained identity, candidate, attempts and deadline. |
| [Captured client interruption](../data/coordination/p5/presence-capture-bc76532f7ad9451f8a8de6a8308913a7/report.json) | Actual browser capture/VAD/WAV/HTTP with declared synthetic microphone and providers. Second input reached Core while older TTS was held; third clip was bounded. Privacy loss aborted the request and withheld late output. |
| [Core and browser chunks](../data/coordination/p5/presence-chunks-a0edf50ac8614096b91df7bec61e2f30/report.json) | Actual Core and browser with synthetic draft/PCM. First playback preceded later synthesis completion; same-epoch source invalidation stopped cached audio. Ordered chunks had exact individual receipts. |
| [Independent review](../data/coordination/p5/review/discord-a3a9cbd0d7c94ca2a2dc8a5d8996d038/report.json) | 99 tests and 20 subtests with stable reviewed source. Includes owner/recipient isolation, repeatability, long playback, idle heartbeat, chunk callbacks and grammar/default preservation. SDK transport is a fixture. |
| [Conversation audit](../data/coordination/p5/review/conversation-audit.json) | Two thinking-aloud phrasings and speech to another person stayed silent. The name-cued answer addressed the question but inferred tension without evidence. The original lexical checker and its false result remain preserved. |
| [Full regression suite](../data/coordination/p5/review/full-suite.json) | 2,629 tests and 98 subtests passed. Three expected opt-in/CPU skips and three existing library warnings are retained. The receipt binds exact source resolution and log bytes. |

In the real two-chunk browser run, first fetch took 21.744 seconds and actual
playback began at 21.822 seconds. The audio-input HTTP request completed at
24.127 seconds; the final media ended at 29.974 seconds. This proves early
chunk release, not acceptable conversational latency. Observed primary and
safety-model cold loads totaled roughly nine seconds; full-context prefill was
another substantial cost. The primary Qwen and Gemma safety model digests stayed
equal before and after the run. The TTS profile was
`qwen_shana_dataset_xvec_soft` on the existing local Qwen sidecar.

The first connected run exposed unnecessary acknowledgments and an unrelated
name-cue answer. The later prompt/grammar check improved those narrow cases;
unsupported mood inference and exaggerated wording remain quality limits. A
source discussion separated the original two rotation questions from the
worker's generated limitation. These examples do not establish general semantic
accuracy, personality acceptance, natural pacing or real room performance.

Historical failed runs, the earlier source-freeze failure, red regressions and
failed/free-unavailable worker proposals remain saved. Reports are not relabelled
as passing after their source changes. The owner explicitly asked to finish the
technical package and leave listening acceptance for later.

## Review and recovery

The [review runbook](shana_private_interaction_review.md) uses separate loopback
listeners, temporary credentials, fresh private state and a finite deadline.
It starts no production unit and edits no machine-local configuration. The
preparation run's freshness check was repaired and tested before the real
review became ready; configuration creates its output directories only after
the fresh-state check.

Current snapshots retain canonical accepted turns and no-replay fingerprints.
Their copied transport records are explicitly invalidated, with flags off,
consumers disconnected and output caches cleared. Audio files are excluded.
Restore/reconnect cannot recreate old delivery. Original closed journals and
prior evidence remain intact.

P1-P4 review artifacts and production source/services are checked against the
saved baseline. Final source and offline wheel receipts bind the review bytes
on Windows, Gamma and in the archives. No package was installed. Activation,
handover, training, production edits and live channel migration remain outside
this isolated technical delivery.

## Permanent owner-private Discord, October 30

The owner authorized the existing ShanaNeety bot for owner DMs and owner-only
voice in the configured Shana Voice channel. The active private release is now
58a28d1830cb4fa19e9a5857fa22f73c, retaining canonical identity
ac3d5311cb6e460ab34ffc79abf79db9. Public Twitch, shared server conversation and
real Minecraft remain inactive. This section supersedes the earlier P5
prepared-only Discord status.

Open the ShanaGamma DM in Brave (the former ShanaNeety account). Ordinary DM questions use the same Core,
personal history and decisions as the dashboard. For voice, join Shana Voice
by yourself, then DM !shana join. The other commands are !shana leave,
!shana pause, !shana resume and !shana status. Commands return fixed receipts;
they are not model thoughts. Home still controls Shana's awake/sleep state.
A reconnect requires an explicit new voice join. Browser microphone/speech
controls remain independent of Discord's scoped voice controls.

The room was made private through the owner's authenticated Brave session and
ShanaNeety was added. The bot's own permission update received 403; no broader
bot permissions were granted. Discord administrators retain their platform
override. The adapter requires both a closed ordinary-member permission boundary
and current occupants limited to the owner and bot. It stops on an extra occupant
or a changed permission boundary. Neither cached presence nor this permission
check proves the room's physical privacy.

The gamma-private-release-discord user service is enabled and connected through
the installed Discord.py 2.7.1 SDK. It creates no Core. Its manifest binds exact
bot, owner, DM, guild and voice IDs; credentials are a separate owner-only file
at ~/.config/gamma/private-discord.json. Permanent HTTP requires the exact
release/manifest/identity/purpose and a manifest-derived API credential. Temporary
isolated launchers retain their original nonproduction-port and opt-in guards.

A held process lock prevents duplicate bot transports from replacing consumer
leases. Gateway resume checks the owner and recreates disconnected consumers.
A changed API generation, failed request or expired lease closes the transport;
systemd then reconnects it after fresh validation. Accepted external sends and
old audio are not replayed. Discord voice controls live in its consumer lease;
disabling them invalidates its pending audio without unmuting another consumer.
Existing global controls retain their previous semantics.

Private working notes, typed decisions and internal summaries are separate from
spoken output. Only finalized checked speech_text for a reply/acknowledgment and
an enabled voice capability reaches synthesis. Stay-silent/defer choices do not
speak those internal fields. PrivateTurnAdapter currently sets think=False;
continuous idle inner monologue and spontaneous boredom conversation remain
unimplemented, and this task did not change the model's reasoning mode.

The final Gamma suite passed 3,544 tests and 120 subtests in 110.60 seconds.
Three skips were the explicitly opt-in candidate sandbox smoke and two CPU-only
CTranslate2 cases on a GPU-capable host. The reviewed connected package passed
180 tests. Additional actual production checks proved gateway connection,
API-only restart recovery to generation 7, unchanged personal-content hashes,
rejection of a second real bot process without changing the first's PID or
consumer lease, HTTPS login/pages and live scoped-control isolation. SDK resume,
voice permission/callback and checked receive behavior also have fixture tests.
The owner confirmed a heard reply with the bot staying connected, backed by two
played chunk receipts. After removing the temporary diagnostic entry and deploying
the owner-profile update, the normal module entry joined voice, delivered another
real DM reply, played audio and remained listening with no service restart.

Receipts live under data/coordination/discord-production-20261030. Before the
upgrade, the current closed journal's 62 records and auxiliary conversation DB
were backed up. The incremental source/state/unit/permission backup is
C:/Users/darke/Documents/ShanaBackups/private-20261031-a157e86d/discord-upgrade-ff99b4dac7b3425c8ae5f872649ff994.tar.gz,
SHA-256 d969ccda3c84e9f549aa45b78937e0505fd00cfb1f70bf3d76a8ffc6e973751a.
It supplements the verified initial full activation archive. Runtime credentials,
including the bot token, have a separately verified Windows current-user DPAPI
backup in protected-config-with-discord.dpapi; no plaintext token is in the source
archive. Reversal must preserve the latest closed state and stop the new Discord
unit as well as API/dashboard before changing the manifest and source pins.

## Owner recognition and spoken-name correction, October 31

Live testing exposed two installed-SDK gaps despite the earlier fixture passes:
Discord.py 2.7.1 parses incoming DMs without hydrated recipients, and the SDK
message callback discarded the adapter result needed by fixed command feedback.
Both have actual-SDK parser/dispatch/permanent-runner regression tests. Hydration
now follows exact pinned owner/DM admission and verifies the SDK-resolved private
recipient before using it. The factory returns the adapter result to the permanent
wrapper. Incoming group, wrong-recipient, bot, webhook and other-channel messages
remain rejected. The real owner later confirmed a heard voice exchange; two
Discord output chunks have actual played receipts. The final normal service
entry was then restored and checked without the diagnostic drop-in.

The owner-name report exposed a separate context gap: generic owner and imported
Neety were separate records with no current speaker binding. The sourced
owner_profile contract in shana_personal_state.md supplies a protected current
owner identity without upgrading historical claims. The owner confirmed preferred
name Neety and pronunciation nee-tee. Qwen's existing Nee-tee replacement still
sounded wrong to the owner. An actual same-profile comparison rendered the
alternative knee tea, which the owner accepted provisionally while noting its
sharp t. A real model check after the current profile update answered, "I know
you as Neety." Historical account/memory hashes were unchanged by the correction.
The configured owner block survives context compaction; default unconfigured
prompts remain unchanged. The replacement affects synthesized text only; visible replies/history
keep Neety. This is a pronunciation adjustment, not a different voice/model.

Exact account username Shana was rejected by Discord as unavailable. The owner
approved ShanaGamma as the account name while retaining server nickname Shana.
The first attempt received USERNAME_RATE_LIMIT. One bounded retry after the
cooldown succeeded: the same bot account is now ShanaGamma, verified by Discord
REST and the Brave DM header. Server nickname remains Shana. No credential was
reset or rotated, and no bot restart was needed.

The final current package is
C:/Users/darke/Documents/ShanaBackups/private-20261031-a157e86d/current-package-58a28d1830cb4fa19e9a5857fa22f73c.tar.gz,
SHA-256 e4b484d5dfbade4e5fd0a5d35d2c91ed0b5da06563c4ae4909957e9c46fe7207.
All 724 member hashes were checked off Gamma. It contains both current pinned
354-file source inventories, voice assets, four service units, baseline and a
fresh 99-record portable snapshot. A restore rehearsal read the actual restored
database, preserved identity and Neety's owner profile, and never activated it
or overwrote live state. Shared virtual environments/model caches remain external
dependencies; this is not a tested whole-machine rebuild. Credentials are excluded
and retain their separate protected DPAPI backup.

## Latency candidate evidence, October 31

This is candidate evidence, preceding a production rollout or new human acceptance.
Real-model conditional checks passed 30/30 across private voice, desktop text,
Discord DM and direct Monitor/phone-compatible input. The same reviewer made the
expected allow/soften/block choices in all 16 CPU-versus-32-layer comparison runs;
its warm 32-layer checks took about 0.74–0.87 seconds.

The seven-case connected rehearsal passed local voice, Discord voice, desktop
text, Monitor and phone paths with actual local STT, primary generation, independent
review and Qwen TTS. Voice first-audio readiness was 7.29–8.80 seconds; Discord
first decoded PCM followed input end by 7.43–8.96 seconds. Audio was decoded
offline and playback ACKs were explicitly simulated for protocol verification.
This does not prove a live Discord delivery or a heard improvement. Full-context
primary prefill still took roughly 3.1–4.4 seconds as supplied context grew.

A proposed static-prefix reorder was tested and rejected: eight rich-context
calls answered correctly, but prefill stayed near 4.35–4.41 seconds and showed
no meaningful improvement. Its three source changes and new tests were removed
back to their exact before-bytes; experiment receipts remain preserved. This
experiment grants no new prompt mode or omitted context.

Measured summaries are under `data/coordination/voice-latency-fix-20261031/`,
especially `semantic-final-summary.json`, `review-safety-summary.json`,
`rehearsal-stage-metrics.json` and `stable-prefix-summary.json`. Focused queue,
diagnostics, source-forgetting and owner/profile test receipts are under
`data/coordination/silence-audit-20261031/`. Real room overlap, final live
Discord verification and the owner's latency/listening acceptance remain separate.

## Private production latency revision, October 31

The accepted repair is now active as release
`1c26e8b26fa14e3c9ffef83f6f25e5a8`, with the same canonical identity
`ac3d5311cb6e460ab34ffc79abf79db9` at generation 11. This later record
supersedes the candidate-only rollout and live-verification status above; it
does not relabel earlier runs. Current owner questions share the same policy
across private channels. Quiet reviews remain background, while manual selected
screenshot shares retain foreground priority and the inherited source checkpoint.

The final source/test inventory stayed unchanged through 3,736 passing tests
and 156 subtests, with three skips and 14 warnings. The final seven-case rehearsal
`full-rehearsal-c86252e7577e406e9a79a3ba985d4396` passed. Discord voice API intake
to first audio ready took 6.944/7.540 seconds; local voice took 8.110/8.166 seconds.
Monitor and phone replies took 6.278/6.396 seconds. Those rehearsal audio decodes
and playback ACKs retain their explicit offline/simulated scope.

The subsequent real Discord gateway check delivered a private DM; its backend
request took 7.917 seconds. Actual owner-microphone audio then traversed Discord
receive, STT, the same Core, independent speech review, Qwen TTS and Discord
playback. The live receipt recorded STT 454 ms, Core 6,902 ms and first audio
ready at 8,796 ms. Attempted, started and played events refer to the same output;
the transport counted one audio played and no failures or drops. These are
observed technical receipts, not a confirmation that the owner heard or accepted
the response. Human hearing, intended quiet, repeated questions and interruption
remain for owner confirmation. Human-like latency and cold-machine behavior
are not established by this small sample.

All nine private HTTPS pages passed with Night + Signal, expected authentication
failures and the existing public URL. Disabled Minecraft correctly returned 409.
No new public output or Minecraft action was enabled. Final evidence is under
`data/coordination/voice-latency-fix-20261031/`: `final-rehearsal-report.json`,
`full-suite-verified-result.json`, `production-runtime-verification.json`,
`production-latency-watch.json` on Gamma, and `https-production-check.json`.
The [release record](shana_private_release.md#private-latency-revision-october-31)
records state preservation and the independently checked backup.

### Subsequent live follow-up remains open

After that successful first output, the owner reported hearing the initial
identity answer but receiving no responses to later questions. The team is
investigating this live failure. The dated single-output receipts and passing
rehearsals above do not establish sustained conversation reliability, completion
of the latency repair, or broader hearing/interaction acceptance.

## Combined cache production revision, October 31

The latest active private release is `1a73976c75374dc6a555cba556e28f1a`,
generation 13, preserving identity `ac3d5311cb6e460ab34ffc79abf79db9`.
This record supersedes the earlier active-revision and open receiver-failure
status without rewriting those dated observations. The installed SDK's playback
stop had also stopped its microphone receiver. Playback now uses its dedicated
stop operation; the owner accepted the corrected continued reception. That
fix is separate from the remaining speed and interaction-quality evaluation.
SDK stop hints now respect the existing 600 ms silence boundary, and transport
status reports receive activity, queued utterances and processing utterances.

The selected private profile now serializes five existing JSON values compactly:
identity, provenance, approved guidance, sourced observations and recent turns.
Decoded objects, all fields/source IDs and literal text are retained. Private
static persona, boundaries, interaction policy and typed contract are arranged
before volatile context. A guarded one-token raw primer prepares the same static
prefix before each eligible normal draft. Its token is ignored and creates no
Core history, private memory, action or speech. Current input and complete context
still reach normal generation and independent speech review. Unselected/default
prompts remain byte-identical; quiet/channel observations do not gain an owner
input or this private layout.

The final combined run kept all 632 source/test files stable: 3,803 tests and
156 subtests passed, with three skips and 14 warnings. The real-model semantic
matrix passed 30/30. The seven-case real-provider rehearsal passed with API
intake-to-first-audio timings of 4.409/5.416 seconds for Discord voice and
6.118/5.457 seconds for local voice. Discord authored-input end to decoded PCM
took 5.142/6.265 seconds, including the 600 ms endpoint boundary. Desktop text,
Monitor and phone took 3.779/4.066/3.915 seconds. These audio decodes and played
ACKs remain explicitly offline/simulated; they are not heard-voice acceptance.

The subsequent actual approved DM was delivered; its request took 4.327 seconds.
The prior same-text check took 7.917 seconds. In the new normal draft, the full
5,988-token context remained: native metrics reported 3,233 cached and 2,755
processed tokens, 2,474 ms prompt evaluation, 170 ms priming, 877 ms independent
review and 3 ms model loading. This small comparison is evidence of a measured
improvement, not a latency percentile or a human-like-conversation claim.
At the latest status snapshot the receiver was active, with zero queued/processing
utterances and no drops or failures. The owner explicitly deferred fresh listening;
heard-voice and interaction-quality acceptance for this revision remain deferred.

The earlier reorder-only and batch-768 experiments remain rejected. The accepted
priming experiment explicitly created a reusable native prefix checkpoint;
reordering by itself had not done so. Version/model/template/family/digest guards
and startup preparation are detailed in the
[release contract](shana_private_release.md#combined-cache-production-revision-october-31).
Evidence: `combined-full-suite-result.json`, `combined-semantic-result.json`,
`combined-final-rehearsal-report.json`, `final-production-runtime-verification.json`
and `cache-combined-live-1793435941.json` under
`data/coordination/voice-latency-fix-20261031/`. The exact-revision archive is now
verified off Gamma: 735 member hashes and a 192-record closed snapshot passed
a non-activating restore rehearsal. See `cache-combined-package-offdevice.json`
and the release record for its exact archive hash and recovery limits. Earlier
packages retain their original coverage.

The later TTS conditioning-cache and full 43-layer reviewer experiments were
rejected. Six paired real-GPU bf16 TTS comparisons kept the same WAV bytes and
stable conditioning, but median synthesis changed only from 3.358672 to 3.328958
seconds: 29.7 ms (0.885%), below the 100 ms/5% adoption gate. The original sidecar
was restored. Matching installed source/config already selected SDPA; the warning
came from an unused 25 Hz module. No active CUDA attention kernel was traced or
claimed. The reviewer trial crossed its GPU reserve limit during warmup;
production remains at 32 layers.

### Restored production verification, October 31

Production remains generation 13, release `1a73976c75374dc6a555cba556e28f1a`,
with the same identity and 359 pins per source/reader role. All 632 source/test
files match the shipped 3,803-pass baseline. After restoration, 68 focused checks
and a fresh seven-case actual-provider rehearsal passed; the latter took
63.103 seconds. Its Discord voice API-to-first-audio times were 5.275/5.296 seconds
and authored-input-end to decoded PCM 6.099/6.219 seconds. Local voice took
4.888/5.773 seconds; desktop text, Monitor and phone took 3.804/3.474/4.015 seconds.
These used the same authored questions but were not a controlled end-to-end A/B.
Gateway events and played ACKs were simulated, with offline audio decoding and
no human hearing check. The earlier real DM result of 4.327 seconds remains a
dated same-generation observation; this restoration run adds no new network
voice result.

The final runtime check found DMs connected, voice disconnected by the owner,
zero queued/processing utterances and no drops or failures. All nine HTTPS pages
and authentication checks passed; Night + Signal was visually checked in Brave
(`final-night-signal-dashboard.png`). Canonical prefix preparation succeeded again:
3,233 tokens in 3,152.613 ms, with unchanged identity and no Core turn, fabricated
owner input or audio. Listening remains explicitly deferred; awake-at-computer
endurance and physical Android behavior remain unproved. The verified 735-file,
192-record backup retains its stated scope.

Evidence under `data/coordination/voice-latency-fix-20261031/` includes
`tts-cache-benchmark-result.json`, `tts-cache-window-result.json`,
`tts-prototype-rejection-restoration.json`, `restored-current-rehearsal-report.json`,
`final-post-tuning-runtime.json` and `post-tuning-canonical-warmup.json`.


## Mood-driven delivery revision, October 31

The owner requested deployment and a listening check of continuous emotional
speech. The private turn now accepts null for its delivery emotion. Null inherits
the canonical Core mood, including a bounded mood update in the same accepted
turn. An explicit label, including neutral, chooses delivery for the whole reply
without changing that continuing mood. Only state_updates.emotion changes mood.
The existing Core journal, mood expiry, acceptance transaction, cancellation and
privacy boundaries remain authoritative. No legacy feeling-state file is read
for private speech.

Hidden tags in structured final text are stripped and ignored. TTS resolves a
single delivery before multipart splitting, and the existing private adapter
passes that same resolved emotion to every chunk. The public legacy turn schema
is unchanged. The private prompt and grammar pins intentionally change; long
input and owner-profile compaction keep their existing context limits.

That speed/peak-based revision did not send natural-language delivery
instructions into the Base clone model. The output-peak map affects level. A later
installed-core audit showed that the forwarded speed parameter is ignored; the
bounded output processor below supplies actual pace changes. Listening, rather than control-selection tests,
determines whether the result sounds natural. Deployment and audio receipts are
recorded with the operation result below.

The deployed revision is `836e103b4c8443a4a263bdd920f0051d`, generation 16. The actual
API samples kept neutral/neutral and concerned/concerned across their two chunks;
the performance choice left continuing mood neutral. See the private-release
operation record for source pins, rollback, tests and the cutover backup.


## Rejected full-reference delivery experiment, October 31

This experiment was rolled back after the owner reported losing Shana's voice identity in the deployed sample. Its preset and implementation were removed from the candidate and production. The following is a historical technical record, not the current voice contract.

The owner found the first speed/peak-based samples tonally wrong, then preferred
the native full-reference auditions for their sound and consistent tone. That
feedback does not establish reliable recognition of the requested emotions.

The experimental `qwen_shana_reference_delivery` preset used the same pinned Shana
reference WAV with the exact audition transcript, full-reference ICL and
experimental Base-model instruction tokens. Neutral delivery adds no emotion
instruction. An explicit reply delivery, or inherited Core mood, selects one
instruction that remains the same across all speech chunks. Warm/concerned
instructions describe delivery without adding a scenario or changing the words.
Only accepted `state_updates.emotion` changes continuing mood.

During the experiment, the loopback Qwen server accepted two extra options:

- `clone_delivery_mode = "reference_instruct"` requires the Base model, an
  existing reference WAV and a non-empty matching transcript. It uses full
  reference conditioning and native instruction tokens; conflicting x-vector
  mode fails rather than silently discarding the delivery request.
- `preserve_model_audio = true` returns generated PCM without per-chunk peak
  normalization, fade or silence padding. The preset also disables RVC and
  denoising; emotion/style rate and gain maps are not applied in this mode.

A one-entry per-model reference-prompt cache keys on the resolved path, actual
audio SHA-256 and stripped transcript. New reference bytes, transcript or model
replace the cached prompt. Delivery instructions are tokenized separately on
each request; the cache never includes a previous generated utterance. CPU
real-model cold/cached comparison produced identical WAV bytes with the same
seed, and constructed the reference prompt only once. This is a different
full-reference path from the previously rejected x-vector cache prototype.

Existing presets and default Qwen HTTP behavior retain their earlier semantics.
That experimental release inventory included the actual Qwen sidecar script. Broader
conversation, emotional-label accuracy and owner live-listening acceptance
remain separate from these technical checks.


Current production uses the restored tuned x-vector preset and the original mood/choice routing fix. The full-reference experiment above is rejected for voice identity. See the private-release restoration record for exact pins and preserved state.


## Bounded delivery controls, October 31

The current private release adds optional `state_updates.voice_pace` from 0.90 to 1.25
and `state_updates.voice_loudness_db` from -5 to +5 dB. Null or omitted values
retain canonical `brain.voice_delivery` across replies and owner restarts. Values
are absolute around pace 1.0 and gain 0 dB; repeated choices never compound.
An accepted deliberate change is saved in the same transaction as the turn, and
its resolved values are captured once for all speech chunks. Mood does not
randomize or reset these settings. Setting 1.0 and 0 explicitly restores delivery.
There is no pitch field or permission to change the cloning/generation recipe.
The public legacy draft and response schemas remain unchanged.

The original tuned x-vector profile, reference, sidecar and model parameters stay
fixed. The installed clone core ignores the old forwarded speed parameter. Real
pace adjustment therefore uses FFmpeg `atempo` on each complete already-rendered
WAV, after its existing fade, padding and peak normalization. Tempo is RMS matched
before applying the requested gain. Gain is uniform and stops at 0.95 peak, with
requested/applied gain recorded per output. This avoids hard clipping without
compression; +5 dB can apply less when a waveform lacks headroom. Baseline 1/0
returns the exact original WAV bytes and does not invoke FFmpeg. The same bounded
private renderer serves local voice, Discord private voice and checked-channel
audio. No playback acknowledgement is synthesized by the server.

The owner accepted the earlier 10% slower audition and the later 25% faster and
loudness auditions, but rejected 25% slower as losing voice identity. Added pitch
shifts were rejected. The lower pace bound is consequently 0.90. Boundary
auditions used the approved two-sentence recording; this is not proof of
emotional-label accuracy or acceptance of every possible phrase.

Implementation audio was byte-identical to six offline audition cases: the
approved baseline, 0.90 and 1.25 pace, -5 dB, headroom-limited +5 dB, and a
combined fast/louder case. Runtime
activation and the resulting live listening check are recorded separately in
`shana_private_release.md`.
