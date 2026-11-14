# Proposed private release and copied-state handover

Status: the owner authorized private production testing, persistent login and then owner-private Discord on October 30. The frozen private release now owns ports 8000/8001 behind the existing HTTPS entry. Actual activation, restart and login receipts are recorded below. The earlier proposal and dated technical records retain their original scope.

## Release contract

The proposed subset is private text, voice, presence, practical work, personal
state, growth, selected-source observations, private rehearsals and the phone
page. The later authorized revision enables only owner-private Discord. Public
Twitch and real Minecraft actions remain inactive.
This is a proposed release scope, not a claim of owner acceptance of its quality
or a new permission to publish. Phone receipt, listening and visual acceptance
retain their current gaps.

The implemented entry path is the authenticated private dashboard and its typed
proxies. Existing legacy clients are not silently repointed to this owner.

A strict local manifest names the exact source root and SHA-256 members, closed
state snapshot/identity, compatible current-reader source reference, shared
identity-lock directory, API/dashboard role mapping (8000/8001), model/voice
references, and every included or inactive effect path. It contains no passwords,
API keys or private conversation text. Paths are resolved explicitly and reject
traversal/symlink escapes. There is no fallback to an old P1 reader or an arbitrary
source directory.

The manifest also pins the explicit original canonical identity-lock roots and
always includes the source/reader checkout defaults. The common and compatibility
leases are acquired in sorted order before claiming ownership and held for the
Core lifetime. Partial failure releases acquired leases without changing the
generation. Known old isolated Core readers using those declared roots cannot
co-own the identity. Unknown/unlisted writers remain outside that guarantee.

## Specific factories and launcher

New private-release factories reuse `Core`, `build_app` and the existing
dashboard `build_app`. Extract the existing configured provider/runner builder
from the isolated API factory into one shared helper; the isolated factory still
runs every current guard before using it. The new pair validates its own exact
manifest, authentication and release conditions before constructing either app.
It adds no cognition, publisher or model-driven activation endpoint.

`Core` and API `build_app` gain an optional explicit identity-lock root; omission
preserves today's checkout-relative default. Release instances must supply the
same manifest-pinned directory. The identity lease is acquired before claiming
the journal owner and retained through Core shutdown. This fences cooperating
release readers across distinct code/state checkouts. It cannot fence legacy
controllers which do not acquire that lock or write through Core.

The additive `additional_identity_lock_roots` default is empty, preserving
isolated startup. The release pair supplies its compatibility roots explicitly.

The CLI defaults to inert validation/preparation. Its only runnable mode in this
packet is finite loopback rehearsal, with fresh credentials on stdin, a maximum
1,800-second lease and nonproduction test ports. The manifest retains the intended
8000/8001 deployment roles while the rehearsal records actual test binds.
Production supervisor mappings and normal routes are not edited. A future live
launch remains a separate owner-approved operation.

The factories are actual callables, `private_release.create_api_app` and
`private_release.create_dashboard_app`. They validate source resolution,
authentication, current identity, local provider/model/voice references, explicit
conversation DB location and the exact matching owner metadata. An existing
common lock directory must be owned by the current user and have no group/other
permissions. Symlinks are rejected before path resolution. Expected identity is
checked before initializing metadata and atomically inside `Store.claim`.

Each internal API request is authenticated with a versioned, domain-separated
HMAC of the exact canonical manifest and explicit test/permanent purpose, using
the configured API bearer secret as its key. Both release factories derive this
credential locally; neither stores nor reports it. The release API rejects the
ordinary base credential and credentials for other manifests or purposes before
the handler runs. An ordinary API restarted at the same address likewise rejects
the old release dashboard's derived credential. This binds reads and mutations
without a separate check-then-send race. An unchanged manifest and identity can
restart together; any manifest change requires the matching dashboard restart.
Direct release API consumers must use `release_api_bearer` with that exact
manifest, purpose and configured base secret. The optional bearer parameters in
`configured_app` and `ShanaApiClient` leave omitted/default behavior unchanged,
reject explicit empty credentials and never mutate global settings.

The credential binding follow-up passed 143 focused tests and 73 subtests,
including real-Core HTTP traps proving wrong credentials cannot create a turn or
call its draft. The supported copied-source proof was repeated in
`private-release-auth-1a17d67fd991dce97b5dd`: 16.073 seconds, 344 archived base
source members, all owned hosts closed, zero provider requests. Its report SHA-256
is `3794bd7d3393673cf46412cb9917bfa1635a773b2b7b5a6d1395412d1b087b7c`;
archive SHA-256 is `0687d5a501dd1a5ad5d160b9a8d4fb9d0a3d464856f2a81b4b5bb5afee35624b`.
Both actual factories used the derived credential, and the ordinary base
credential received 401. The same controlled-provider and no-activation limits
below apply. An earlier same-address replacement diagnostic was rejected by
automatic approval review and was not executed or retried; the supported finite
procedure and authentication side-effect tests are the observed evidence.

Primary and work model references must match, vision has its own pinned reference,
and both outer routing and automatic local model routing must be off. Effective
voice options and actual reference-audio bytes have digest pins; missing named
profiles, changed reference/options, unreviewed extra options and remote or
credential-bearing voice endpoints fail closed. Digests contain no plaintext
reference text or credentials. Model weight contents were not revalidated.

The permanent path requires `status: owner_approved`,
`GAMMA_PRIVATE_RELEASE_ENABLE=1`, a complete scope and observed drain. The finite
test purpose accepts only disposable `private-release-test-<uuid>-a/b` source
checkouts and cannot use the normal production ports. These gates are not an
approval mechanism exposed to Core or a model tool.

## Optional private inference profile

The October 31 candidate accepts one additional trusted manifest field:
`"inference_profile": {"name": "gamma_warm_local_v1", "review_model": "gemma4:e4b"}`.
Omitting it preserves the previous defaults; explicit null or any other shape or
value is rejected. The full manifest, including this field, remains source-bound
and part of the derived credential. Owner messages, source excerpts and API
observations cannot select a profile, model or endpoint. The profile requires
the configured independent speech reviewer to be enabled with the same Gemma
model and the primary endpoint bound to loopback `http://127.0.0.1:11434`.

The selected profile retains the existing primary for `30m` and uses primary
temperature `0.2`. The unchanged independent `gemma4:e4b` reviewer uses
`http://127.0.0.1:11435`, `30m` retention, 32 GPU layers and eight CPU threads.
This changes placement and retention, not reviewer policy or its required check.
Per-call local runtime scope covers the same Core drafts and selected-frame
vision and resets afterward. Configured practical-work learners inherit primary
retention; their independent checker/model and backend admission remain intact.
All model/voice pins, source ownership, authentication, safety and typed action
limits continue to apply. No shared default or machine-local configuration is
changed merely by adding candidate support.

A permanent API startup with this profile performs content-free warmup in a
worker thread, through the existing backend admission boundary, with a maximum
30-second startup budget. Empty generation requests load the primary/reviewer;
a short silent PCM sample initializes STT without a Core turn or retained
transcript. A timed-out STT initializer may continue while its report says
`warming`. Status is reported at `/v1/coordination/private-release` under
`inference_warmup`; failures report degraded readiness and never bypass checks,
activate listening or synthesize unsolicited speech. Finite test factories and
profile-less startup do not implicitly warm models. STT/TTS factory caches also
single-initialize under a lock; transcription and synthesis run outside that lock.

This contract was first documented at the candidate checkpoint. The later
[October 31 revision](#private-latency-revision-october-31) records its production
deployment; the earlier [latency evidence](shana_private_interaction.md#latency-candidate-evidence-october-31)
retains its original test scope and limits. Human acceptance remains separate.

## Drain and reversal boundaries

The manifest enumerates the legacy Shana/dashboard services, proactive/stream/
voice selectors, autonomous/idle coding dispatch and external adapters. A release
preflight must record which are delegated or inactive, block new old-owner work,
and resolve uncertain queued effects without replay. It must not accept a bare
"drained" Boolean or treat generation numbers as a fence for old database writers.
The test mode uses only its explicitly disposable namespace and controlled child
processes. It does not stop production services or attest that they were drained.

Read-only drain probes cover the four named legacy user units and the actual
known Python module entry points: Twitch worker/EventSub, Discord text/private
runner, coding idle/autonomous and improvement worker. Exact module census is
restricted to the current user's processes. Failed observations block readiness.
Unknown entry points and noncooperating writers remain an explicit limitation;
stopping an observed service once cannot prove it will never be restarted.
The census also covers supported old ASGI targets and the known startup-script
forms. It excludes the new private-release factory targets.

Reversal means copying the latest closed journal and artifacts into the declared
compatible coordination reader, including post-handover exchanges and unfinished
work. It invalidates copied transports through existing transfer code. P1
projection remains rejected for current practical/growth/channel records. The
earlier non-atomic legacy import remains historical evidence, not an atomic
production migration claim.

Reversal holds the common identity lease through its stable closed-state check,
export and restore, together with the same compatibility leases. Its baseline
snapshot pin is immutable. The active journal
is allowed to progress; startup verifies identity/compatible reader rather than
requiring the original active DB bytes after new experiences.
Its result is point-in-time preparation, not atomic future activation. Maintain
the drain until the target claims ownership, or revalidate snapshot freshness;
do not restart a source owner in the gap and then activate its older copy.

## Historical operator preparation

Run the inert commands on Gamma from the pinned portable release checkout:

```bash
.venv/bin/python scripts/private_release.py prepare \
  --source /absolute/pinned-release --state /absolute/pinned-release/data/coordination/active \
  --baseline /private/closed-baseline --reader /absolute/compatible-reader \
  --lock-root /private/shared-identity-locks \
  --original-lock-root /absolute/original-checkout/data/coordination/identities \
  --model qwen3.8:27b \
  --voice qwen_shana_dataset_xvec_soft --output /private/proposed-release.json
.venv/bin/python scripts/private_release.py validate --manifest /private/proposed-release.json
```

The default validation is permanent preflight and reports missing approval or
observed drain as blockers. Preparation does not restore state or stop a service.
Protect and review the proposed manifest, source/configuration references and
baseline. Model/voice names are pinned references, not a new weight comparison.
Credentials remain in an operator-protected environment file, never this manifest
or command arguments. Its API/dashboard auth, project root, SQLite conversation
path, local provider/model and voice profile must match the manifest.

Only after the separate owner decision and concrete service/drain/rollback review,
the proposed **new** unit names are `gamma-private-release-api.service` and
`gamma-private-release-dashboard.service`. Reusing `gamma-shana.service` or
`gamma-dashboard.service` would conflict with the required inactive legacy units.
At the proposal stage no unit file was installed or changed. Their explicit factory commands were:

```bash
.venv/bin/python -m uvicorn gamma.coordination.private_release:create_api_app \
  --factory --host 127.0.0.1 --port 8000
.venv/bin/python -m uvicorn gamma.coordination.private_release:create_dashboard_app \
  --factory --host 127.0.0.1 --port 8001
```

Both need `GAMMA_PRIVATE_RELEASE_MANIFEST=/private/approved-release.json`,
`GAMMA_PRIVATE_RELEASE_PURPOSE=permanent`, `GAMMA_PRIVATE_RELEASE_ENABLE=1` and the
protected matching runtime environment. Dashboard additionally needs
`GAMMA_PRIVATE_RELEASE_API_ORIGIN=http://127.0.0.1:8000`. Start/check API owner
metadata before starting Dashboard. Preserve existing HTTPS public URL settings
and route roles. These commands are an operator proposal, not an authorization
or an automated cutover. Existing isolated factories and supervisor mappings
retain their original behavior.

Set `SHANA_LLM_ROUTER_ENABLED=false` and
`SHANA_LOCAL_LLM_ENABLE_ROUTING=false`. `GAMMA_PRACTICE_MODEL` must equal the
manifest primary, and `SHANA_LOCAL_LLM_VISION_MODEL` must match its vision
reference. Prepare from the actual pinned source checkout with its intended
voice configuration loaded. A matching profile name alone cannot satisfy the
effective voice/reference digest check.

## Observed verification

The final focused Gamma suite passed 103 tests with one existing Starlette/httpx
deprecation warning. It includes atomic identity admission, partial lease cleanup,
reversal race exclusion, source/reader pins, private-directory/symlink guards,
actual known process argv/census checks, default/explicit voice resolution,
effective voice drift and disabled short-text model routing. Existing actual
renderer tests retain their frozen default prompt hashes.

The configured mock boundary now returns only fixed synthetic text, with no
ConversationService inference or model-backed repair/safety call. Practice/work
proposal providers fail honestly before HTTP; mock STT/TTS/vision capabilities
remain unavailable. A trapping local HTTP server observed zero requests through
actual practice/P3 controls, valid private voice intake/text and full PNG input.
The normal local provider and safety paths remain in use for real inference.

The final copied-process rehearsal completed in 16.071 seconds. Actual API and
Dashboard factories enforced authentication. Old-default Core instances imported
from distinct source checkouts and the new release blocked one another through
the compatibility leases. A killed pending HTTP request reopened under the next
generation; an old-token write failed, and phone result lookup did not replay
provider work. Complete-lease current-reader reversal retained later exchanges,
one unfinished commitment, three content-addressed artifacts, the unchanged
deadline and two authored fixture attempts out of three. The finite CLI closed
automatically. The permanent-purpose API branch ran its actual default worker,
review and contact loops with controlled providers/drain observations established
before lifespan. No provider HTTP request reached its tripwire.

Receipt: `data/coordination/remaining-20261027/private-release-77139548d16246b595b0cd9416fc33d7/`.
Its independent review checked 342 archived base source members, two declared
fixture-config overrides, the reference WAV digest, actual read-only SQLite rows,
0600 database mode and all nine exact child PIDs absent. Report SHA-256:
`701a58d8ad1822bd3485177029aabd0c9fd29c9b6c016f71516c746da4e18fab`.
Executed archive SHA-256:
`da03a85ff5540c710acb7a5a0186cad684f5896e63b5191c48d067605b7676c8`.

The earlier failed direct-input replay expectation and the earlier 12.595-second
pass remain version-bound records. The scoped free OpenRouter worker returned no
usable proposal; no retry or paid fallback ran. Its result and baseline hashes are
retained under `data/coordination/remaining-20261027/release-worker/`.

These are controlled technical receipts. They do not prove real voice/model
replacement, atomic legacy import, noncooperating-writer fencing, production
activation, human comfort or atomic future activation of a prepared copy.

## Touch list and verification

New files: `src/gamma/coordination/private_release.py`,
`scripts/private_release.py`, `scripts/verify_private_release.py`,
`tests/test_private_release.py`, and this specification. If splitting the strict
manifest helpers keeps the module smaller, use
`src/gamma/coordination/private_release_manifest.py` as the only extra module.
Small shared hooks: optional `identity_lock_root` in `Core`/API `build_app`, and
extract the configured API builder without changing isolated guards. Parent
owns/reviews those shared edits before integration. No dashboard assets,
supervisor mappings, live configuration, normal state or production files change.

Meaningful process-level checks use real HTTP, SQLite, file locks and exact child
PIDs with controlled providers, without model calls: two release checkouts with
one identity cannot co-own; a forced crash permits a later generation; delayed
old work cannot commit; an uncertain input/output is not replayed; compatible
current-reader reversal preserves a later exchange, unfinished commitment,
artifacts and consumed limits; tampered pins, missing drain coverage and an
incompatible P1 reversal fail before mutation. All receipts distinguish controlled
providers from real inference. Runtime/tests run only on Gamma with hidden
Windows launchers. No live cutover, listener on 8000/8001, UI or model call runs.

Uncertain transport sends are inspected through their result endpoint, without
automatic transport resend. Core's existing recovery may resume uncommitted
computation; this is distinct from replaying a committed external effect or
releasing copied old output. Rehearsal receipts retain that distinction and the
first failed overly broad verifier expectation.

## Actual legacy-state audit, 2026-10-27

The read-only audit at 12:38–12:40 UTC did not capture, import, stop or restart
production. It used fixed source references, file metadata, SQLite read-only
aggregate counts, selected safe path settings and service/process metadata.
It did not print personal content or credentials, import the old runtime,
invoke models or touch the active four-hour test.

The four old user units were active in `/home/neety/Documents/gamma-main`.
Their main PIDs were API 32730, Dashboard 33020, autonomous coding 51714 and
idle coding 33077. Each used `Restart=on-failure` and had no `ExecStop`.
The autonomous selector held its executor lock. Presence recorded `wake` and
a current-turn field while its state file continued changing. Those fields
alone do not prove active inference, and this was not a quiescent window.
`ServiceManager.stop('shana')` also stops STT/audio/TTS dependencies, so an
operational drain must account for other consumers of those sidecars.

The safe configured-path reconstruction resolved all four processes to
`data/memory/gamma.db`. No production `data/coordination` directory existed.
The database was 4,030,464 bytes, last modified October 20, with no adjacent
WAL or SHM file. Its counts matched the historical October 24 copy: 14 completed
journal entries, one person, two account links, four rolling summaries, four
working checkpoints, one self-goal and 5,418 temporary stream rows. It also
contained one completed durable output. Profile facts, episodic memories,
viewer trust, owner/cursor rows and scoped summaries/checkpoints were empty.
The four checkpoints contained zero `commitments_json` array items. Imported
checkpoint blobs remain unverified review provenance, not proof that an
unfinished task actually exists.

The historical capture began October 24 at 04:41:02 UTC. Its manifest explicitly
set `bundle_atomic_across_legacy_stores=false`. It covered the database and
`data/assistant_state.json`, `data/assistant_emotion_memory.json`,
`src/gamma/persona/relationship_state.json`, `config/persona.yaml` and
`src/gamma/persona/core.md`. Their observed sizes and mtimes remained historical;
that does not prove content equality. `data/core_memories.md` remained absent.
The old prompt also loads `src/gamma/persona/style.json`, which exists but was
outside that five-file capture. The memory directory contained four additional
historical files from June 10–11, including two SQLite images. Preserve those
as backup provenance rather than importing them as independent current history.

Separate worker and learning evidence also needs preservation:

| Root under `data/` | Files | Bytes | Largest file, bytes |
| --- | ---: | ---: | ---: |
| `coding/practice` | 5 | 31,218 | 7,728 |
| `coding/lessons` | 8 | 5,450 | 2,235 |
| `coding/projects` | 63 | 391,431 | 25,069 |
| `coding/autonomous` | 6,856 | 78,237,444 | 727,783 |
| `coding/candidates` | 42 | 23,676,873 | 23,631,240 |
| `improvement` | 10,356 | 523,011,860 | 2,245,935 |

The metadata pass found no file or directory symlinks in those roots. Their
stored job statuses contained no queued/running jobs. Five jobs needed checking,
one improvement series was ready for holdout, and six lessons were marked active.
Those are provenance and unfinished review states. They grant no new execution
authority or current capability claim. Keep associated artifacts and history;
do not automatically resume old workers, controls, transport receipts or effects.

A fresh migration still requires a reviewed finite drain and restart plan after
shared model/voice use ends. Record the initial service states and source/reader
pins, block intake and selectors, settle ambiguous in-flight effects, and hold
all enumerated writers drained across the complete capture and target claim.
Capture database consistency plus whole-bundle before/after file hashes and
census. Abort on drift or a new writer. Retain the raw private evidence on a
durable destination with room for at least the observed 625,354,276-byte work
roots, historical memory files and database/source overhead. Separate source
copies do not establish a globally atomic legacy snapshot.

On a copied target, verify dynamic coverage, repeated reconciliation, current
reader restore, correction/forget protection, artifact integrity, unfinished
review states and preservation of post-import experiences through reversal.
Do not use the historical verifier's fixed counts or five-file expectation as
fresh acceptance. If returning to the old runtime after a finite rehearsal,
restart only initially active units, API/Dashboard before selectors, and verify
health, authentication and absence of effect replay. These operations have not
been performed by this audit.

## Legacy table disposition repair

`personal_import.reconcile` now records each supplied table's disposition,
row count and payload SHA-256 in the migration audit. The five convertible
personal tables retain existing correction, forgetting and account-link rules.
Summary/checkpoint tables keep their existing unverified provenance treatment.
Actual operational tables `conversationjournalowner`, `conversationinputcursor`
and `stream_viewer_trust` now receive content-free quarantine references, alongside
the existing operational names and aliases. Nonempty unknown tables receive the
same count/hash treatment with an unsupported-table status. Their raw payloads
remain solely in the protected copied backup. `sqlite_sequence` receives an
explicit metadata disposition. Empty tables produce a census entry without a
quarantine reference. `quarantined` counts distinct references identified by the
reconciliation, rather than raw rows, newly inserted records or SQLite metadata.

`reconcile_snapshot` accepts portable `database.snapshot_path` and each
`copied_files[].snapshot_path` relative to the protected snapshot root. Existing
absolute manifests still work. Parent traversal, ambiguous relative backslashes,
symlinks and paths outside the copy fail before canonical insertion. SQLite
table identifiers are quoted rather than silently skipping unknown names that
contain punctuation. This provides table coverage without granting imported
owner leases, input acknowledgements, pending output or action authority.

The Gamma personal-state suite passed 149 tests and eight subtests, including
20 new disposition/path cases. Tests use disposable fictional databases and
actual Core/SQLite reconciliation. Private row markers remained absent from
canonical records and recall; owner/account/permit state remained unchanged;
existing sequence invalidation stayed in effect. Historical absolute and new
portable manifests both preserved copied bytes. No real-data capture/import,
service operation or inference was part of this repair.

## Stock Qwen profile compatibility, 2026-10-30

The release validator now accepts the existing `qwen_shana_dataset_xvec_soft`
and `qwen_shana_dataset_ref_soft` profiles without dropping their bounded
emotion settings. `private_release_voice.validate_voice_extra` permits only
fixed typed scalar options and the two maps `speed_by_emotion` and
`output_peak_by_emotion`. Map keys are the seven canonical emotion tags plus
`default`, with at most eight entries. Unknown keys, arbitrary deeper objects,
nonfinite values, booleans in numeric fields and out-of-range resource options
remain rejected. No TTS backend, server resource limit or machine-local voice
configuration was changed by this compatibility repair.

The retained Gamma receipt is
`data/coordination/p9/replacement-comparison/h-tests.json`: 92 tests passed in
8.60 seconds with one existing Starlette warning. Separate source-pinned actual
release-factory proofs for both profiles exercised authenticated private HTTP,
resolved configuration/reference hashes and reference-byte drift rejection
before ownership claim. Both owned children were observed absent. Their paths
are `h-qwen_shana_dataset_xvec_soft-proof.json` and
`h-qwen_shana_dataset_ref_soft-proof.json` in that same directory.
These proofs used controlled semantic replies and performed zero model calls
or actual synthesis. They establish manifest/factory compatibility, not an
accepted voice/model replacement, listening quality or production activation.

## Owner-authorized production test and persistent login, October 30

The owner requested the full private candidate on the normal production entry so everyday testing can use persistent state. This authorizes a backed-up private ownership cutover; public streaming, Discord and real Minecraft remain inactive. It does not establish human listening or natural-behavior acceptance, and spontaneous idle chatter remains a capability gap. Earlier proposed-only statements above are dated history.

The requested login is `darkess78` with a password entered directly by the owner in `scripts/dashboard_password_windows.pyw`. The helper stores the saved login encrypted with Windows current-user DPAPI. No plaintext password is saved or sent through chat, arguments or logs. Gamma receives only a versioned salted scrypt verifier and independent session-signing material in `~/.config/gamma-dashboard/credentials.json`, mode 0600. Set `SHANA_DASHBOARD_CREDENTIALS_FILE` to that absolute file for both services. This record is outside source inventories and survives restarts. Password derivation uses fixed scrypt N=131072, r=8, p=1 with a random sixteen-byte salt and a 32-byte verifier.

A configured credential file takes precedence over legacy plaintext settings. Missing, malformed, unowned, overly permissive, symlinked or nonregular files fail closed. A FIFO is rejected without blocking. Legacy configuration remains supported when no credential file is configured. Login hashing runs outside the async event loop and has bounded concurrent derivations. A cookie is signed from the same credential snapshot that verified the password, and binds the verifier/version so changing credentials invalidates old HTTP and WebSocket sessions. Temporary review launchers explicitly clear the persistent-file setting.

Source, state and deployment receipts for this operation are under `data/coordination/private-production-20261030/`. The frozen production source and compatible current reader are separate from ongoing candidate edits. Keep the old four units loaded but inactive for the observed-drain contract. New private-release API/dashboard services own 8000/8001, with explicit independent voice dependencies; the prior service cgroup otherwise takes its voice sidecars down during stop. Preserve the existing public URL and proxy contracts, and use a fresh capture/import rather than a verifier's synthetic review state. Reversal must preserve post-cutover state through the pinned current reader.

## Actual private production operation

Observed October 30, 2026. Release `9d0ba259a8b041b2a788000ae95f6c94` now
serves `https://gamma.neety.me/dashboard/home`. Username: `darkess78`;
password: the one entered by the owner in the local setup window. The encrypted
saved login is at `%LOCALAPPDATA%/Shana/dashboard-login.dpapi`. DPAPI binds that
saved copy to the Windows account; it is not a portable password-manager export.
The Gamma verifier persists independently across app restarts. Rerunning the
local setup window alone does not install changed credentials on Gamma.

Night + Signal is the default on all nine dashboard pages. Shana is awake with
relaxed focus. In Presence, confirm the private audience and microphone source,
unmute speech and start listening when ready. For OSRS or other surroundings,
Activities can watch an explicitly selected window for a finite lease; renewal
is explicit. The host itself no longer expires after the old 12-hour review
lease. Closing the page ends its consumer; Home Sleep is the everyday pause.
Do not use the old isolated Start/Stop test shortcuts for this production host.

### Ownership and recovery

- Release root: `/home/neety/Documents/shana-releases/private-20261031-a157e86d`.
  Frozen `source` and compatible `reader` are separate from candidate edits.
  Canonical state: `source/data/coordination/active`. Identity:
  `ac3d5311cb6e460ab34ffc79abf79db9`.
- New `gamma-private-release-{qwen,api,dashboard}.service` units are enabled and
  active. User lingering is enabled. A host reboot during owner-requested system
  updates was observed; all three units restarted automatically, retained identity,
  personal-content hashes and the new conversation, and passed HTTPS login again.
  The four old units are disabled, loaded and inactive, with a condition drop-in
  keyed to `~/.config/gamma/private-release-active`. Do not remove the marker or
  start an old unit while the new owner is running.
- Fresh observed-drain legacy capture and mechanical verification preceded the
  canonical import. Only `verified-import-projection` was imported; synthetic
  verifier review state was excluded. Original production files remain intact.
- Closed canonical baseline and source/unit/capture backup:
  `C:/Users/darke/Documents/ShanaBackups/private-20261031-a157e86d/activation-backup.tar.gz`.
  Its 458,225,665 bytes match Gamma SHA-256
  `512aa6358336053897060e2d3b8a0594efed121ca9cb991b143d94b9d7210ff4`.
  Independent streaming inspection read 18,258 files, verified gzip CRC/EOF and
  the baseline SQLite hash. This does not prove whole-machine restoration.
  Protected runtime configuration is separately saved as `protected-config.dpapi`
  in that directory, with an actual current-user encrypt/decrypt round trip.
- To free storage, one inactive historical 10,369,105,561-byte
  `activation-phase-b-20261019-eqgszvcd/pre/base.tar.zst` archive was relocated
  to the matching path under `C:/Users/darke/Documents/ShanaBackups`.
  Full source/destination hashes matched before its Gamma copy was removed;
  the original location retains an off-device restore index. This was a
  reversible relocation, not deletion of the only backup.
- Preserve both dependency trees: frozen checkouts link to the candidate
  `.venv`, and Qwen uses `gamma-main/.venv-tts`. The release is not independent
  of them. Startup inventory checks pin src/config and the two release CLI
  scripts; not every executable dependency is covered by that inventory.
- Reversal must first close the active owner and copy its latest state into the
  pinned current reader under the shared identity leases. Preserve post-cutover
  experiences. Do not replay the initial baseline or restart the old legacy
  database as if it contained new conversations. No post-activation rollback
  was performed during this handover.

### Actual acceptance evidence and limits

The complete candidate suite passed 3,456 tests and 120 subtests, with three
skips and three dependency warnings. Focused auth/release/UI checks passed.
A later focused run passed 75 tests and six subtests, including both stock voice
factory proofs; one opt-in legacy candidate sandbox smoke remained skipped
because its explicit GAMMA_RUN_CANDIDATE_SANDBOX_SMOKE opt-in was not set.
Actual HTTPS checks used the Windows trusted CA store without disabling TLS
verification: chosen login 200, wrong password 401, unauthenticated API 401,
Secure/HttpOnly/SameSite cookie, all nine pages 200 with the default style and
the connected personal/work/growth/activity/rehearsal/phone/recovery proxies.
Minecraft status correctly reported unavailable (409).

The release-bound API credential passed; its ordinary base credential returned
401. API/dashboard services were actually restarted: generation advanced from
3 to 4, identity stayed unchanged, and all protected personal content retained
its exact hash. Saved credentials still authenticated afterward. Original four
service PIDs remained zero. The API's generic `isolated_owner_private` and
`production_verified=false` fields retain the earlier schema; exact active
release identity/purpose is reported by `/v1/coordination/private-release`.
Its noncooperating-writer flag does not attest to OS service fencing; the separate
systemd/census receipts record that observed operational state.

A real authored WAV traversed production HTTPS intake, local STT, local model,
checked synthesis, output offering and audio download (RIFF/WAVE, 97,964 bytes).
First audio arrived in 28.125 seconds. No played acknowledgment was fabricated.
The technical consumer was interrupted/disconnected and all speech/voice
capabilities were switched off afterward. This proves transport, not that the
owner heard or accepted the voice. Responses can still be overly insistent;
latency and natural interaction need everyday evaluation.

At the initial activation checkpoint, public streaming and Discord remained
disconnected; the later Discord revision below supersedes that Discord status.
Real Minecraft remains deferred.
Spontaneous boredom conversation is not implemented merely by keeping Shana
awake. Phone reminders require the page to remain open; physical Android,
private-room behavior and extended owner listening remain acceptance items.
Receipts: `data/coordination/private-production-20261030/`, especially
`https-production-check.json`, `runtime-after-restart-command.json`,
`production-voice-check.json`, `archive-integrity.json` and the backup receipts.

## Private Discord revision

The active revision is now `58a28d1830cb4fa19e9a5857fa22f73c` (generation 10 at
verification), retaining identity `ac3d5311cb6e460ab34ffc79abf79db9`. Its optional exact
`discord` binding permits only `live_discord: owner_private`; all previous manifest
forms retain Discord inactive, and public Twitch/real Minecraft remain inactive.
API, dashboard and the new `gamma-private-release-discord.service` share the new
manifest-bound credential. Same identity and latest state are retained. See the
[permanent Discord operation](shana_private_interaction.md#permanent-owner-private-discord-october-30)
for owner commands, observed verification, private-room permissions and backups.

The current revision includes installed-SDK DM recipient/result fixes, protected
current-owner context and the provisionally accepted Neety pronunciation. The
normal Discord unit entry passed real DM/voice checks after diagnostics were
removed. The final suite passed 3,544 tests and 120 subtests; three environment
guards skipped. A fresh 99-record snapshot passed a non-activating restore
rehearsal, and the complete current source/state package was verified off Gamma.
Exact backup hashes and remaining name/listening limits are in the linked Discord
operation record. No Nginx routes or credential values changed.

## Private latency revision, October 31

Release `1c26e8b26fa14e3c9ffef83f6f25e5a8` supersedes
`58a28d1830cb4fa19e9a5857fa22f73c`. The API, dashboard and Discord now share
the new manifest-bound credential and the explicit `gamma_warm_local_v1` profile.
Canonical identity `ac3d5311cb6e460ab34ffc79abf79db9` remains at generation 11.
Both frozen source and compatible reader have 358 matching source pins. The
cutover preserved all 101 closed records and the current owner profile without
replaying a previous baseline. Protected configuration, model/voice references
and effect permissions remained unchanged. Startup warmup reported ready for
the primary, unchanged independent reviewer and STT.

The final full suite passed 3,736 tests and 156 subtests, with three skips and
14 warnings; source/test hashes stayed unchanged during the run. The final
seven-case real-provider rehearsal passed before activation. Actual production
verification then checked all four enabled private units, the disabled legacy
owners, HTTPS login/authentication and all nine Night + Signal pages. Minecraft
remains inactive with its expected 409 receipt; public Twitch remains inactive.
A real Discord DM and owner-microphone-to-played-output path succeeded. The
[interaction record](shana_private_interaction.md#private-production-latency-revision-october-31)
keeps their stage timings and outstanding human-confirmation limits.

The new off-device current package is
`C:/Users/darke/Documents/ShanaBackups/private-20261031-a157e86d/current-package-1c26e8b26fa14e3c9ffef83f6f25e5a8.tar.gz`,
2,872,483 bytes, SHA-256
`44b6994a8bf93a051868902992bf31142f9dffe46ead3aa5d6f22cbaf0eb5474`.
All 733 member hashes were verified off Gamma. Its fresh 105-record snapshot
passed a non-activating restore rehearsal, preserving the same identity without
overwriting current state; copied delivery authority was invalidated. Plaintext
credentials are excluded. Shared virtual environments and model caches remain
external dependencies, with protected credentials backed up separately using
Windows current-user DPAPI. This does not prove a whole-machine rebuild.

Receipts are under `data/coordination/voice-latency-fix-20261031/`, including
`activation-result.json` on Gamma, `production-runtime-verification.json`,
`https-production-check.json` and `current-package-offdevice.json`. Earlier
packages, failed runs, rejected prefix experiments and candidate-only snapshots
remain historical evidence. Any later reversal must preserve the latest state
and stop the matching private owners before changing source/manifest pins.

The owner subsequently reported no further responses after the first heard
identity answer. That live follow-up is unresolved and remains under diagnosis.
The activated revision and dated successful receipts above are retained; they
do not establish sustained conversation reliability or completion of the repair.
No broader listening or interaction acceptance is recorded.

## Combined cache production revision, October 31

The latest revision is `1a73976c75374dc6a555cba556e28f1a`, generation 13,
with unchanged canonical identity `ac3d5311cb6e460ab34ffc79abf79db9`.
Both frozen source and compatible reader have 359 matching source pins. The
cutover preserved all 188 closed records, the current owner profile, protected
configuration, model/voice references and effect permissions. Latest state was
not replayed from an earlier baseline. Earlier release IDs and backup records
above retain their dated scope.

The explicit `gamma_warm_local_v1` profile now selects `primary_prefix_cache`.
Its private renderer minifies only JSON separator whitespace in five serialization
sites and moves intact static layers ahead of dynamic context/history. No fields,
source provenance, commitments, history or authority are omitted by these changes.
The profile-less/default path remains unchanged. Normal eligible private drafts
use a one-token raw primer, under the existing backend admission and cancellation
checkpoint, before their complete structured request. Primer output is discarded
and never enters Core history, speech, memory or tools.

Priming is restricted to loopback `http://127.0.0.1:11434`, the pinned
`qwen3.8:27b` digest, Ollama `0.34.2`, the exact template hash and both declared
family/architecture `qwen35`. The helper rejects images, duplicate dynamic
boundaries, missing private-policy/contract headers, embedded control tags and
out-of-bound prefixes. Metadata mismatch or provider failure skips this optional
optimization; cancellation/identity exceptions still propagate and no draft is
retried. The unchanged Gemma reviewer still independently checks final speech.

Permanent startup builds the canonical base from the real read-only
`personal_identity/self` record, without a fake received message or owner marker.
It prepares that prefix within the existing 30-second warmup budget before
Discord auto-join. Actual startup reported ready after priming 3,233 tokens in
3,166 ms and initializing STT in 1,952 ms. The observed foreground native cache
reuse and dated [interaction timings](shana_private_interaction.md#combined-cache-production-revision-october-31)
are distinct from the rejected reorder-only and batch-768 experiments.

The combined full suite passed 3,803 tests and 156 subtests, with three skips and
14 warnings; all 632 inventoried source/test files stayed unchanged. Semantic
checks passed 30/30 and the final connected rehearsal 7/7. Normal production
then delivered the approved DM in 4.327 seconds, with the receiver active and
no queued/processing utterances, drops or failures at the recorded checkpoint.
The owner explicitly deferred fresh listening; heard-voice and interaction-quality
acceptance for this revision remain deferred.

The exact-revision off-device package is now complete:
`C:/Users/darke/Documents/ShanaBackups/private-20261031-a157e86d/current-package-1a73976c75374dc6a555cba556e28f1a.tar.gz`,
2,904,774 bytes, SHA-256
`290d78bda36fbe0e54f0508a977e94582ce71504ff78afaa9721cac71b5c6b92`.
All 735 member hashes were verified off Gamma. Its 192-record closed snapshot
passed an actual non-activating restore rehearsal, preserving the same identity
without overwriting current state. Source and reader still have 359 pins each.
Plaintext credentials are excluded, and shared environments/model caches remain
external dependencies. This is not a whole-machine rebuild proof. The earlier
733-member/105-record package retains its previous-revision coverage.

Both later tuning proposals were rejected without changing this production
revision. Full 43-layer reviewer warmup reduced observed free GPU memory to
1,396 MiB, below the 2,048 MiB reserve. The experiment stopped before any 43-layer
safety or steady-state cases; 15/15 executed baseline cases had passed. Restoring
32 layers returned 3,208 MiB free. This was a reserve rejection, not a complete
32-versus-43 quality/performance comparison.

The six-pair real-GPU bf16 TTS conditioning-cache comparison preserved WAV bytes
and conditioning but saved only 29.7 ms (0.885%): median 3.358672 to 3.328958
seconds, below the 100 ms/5% gate. The prototype was not shipped. Its separate
3,822-pass suite does not replace the shipped generation-13 suite. Only the
prototype sidecar, pin and test changes were reverted; all 632 source/test hashes
now exactly match the existing 3,803-pass baseline, including 359 source pins
per role. After restoration, 68 focused checks and a fresh seven-case real-provider
rehearsal passed. That rehearsal used offline decode and simulated gateway/playback
ACKs, not live owner voice or human acceptance.

The final check retained the same release, identity, source pins, configuration,
model/voice references and effect permissions. DMs were connected, voice later
disconnected by the owner, and queue/processing/drop/failure counts were zero.
HTTPS authentication and all nine Night + Signal pages passed, with a saved
Brave visual check. Canonical prefix preparation again processed 3,233 tokens
in 3,152.613 ms using prefix SHA-256
`7958b676275efb625c86d21bd45933615333764cf0e02d0bd3ea20a05fc3a313`;
it created no Core turn, owner input or audio. Owner listening remains explicitly
deferred; awake endurance and Android hardware acceptance remain unproved.
The verified 735-member/192-record archive above remains the exact-revision
backup; it is not a whole-machine rebuild proof.

Rejection/restoration receipts include `review-full-offload-rejection-summary.json`,
`tts-cache-window-result.json`, `tts-prototype-rejection-restoration.json`,
`restored-current-rehearsal-result.json`, `restored-current-rehearsal-report.json`,
`final-post-tuning-runtime.json` and `post-tuning-canonical-warmup.json`.

Receipts: `cache-combined-activation-result.json` on Gamma,
`final-production-runtime-verification.json`, `combined-full-suite-result.json`,
`combined-semantic-result.json`, `combined-final-rehearsal-report.json` and
`cache-combined-live-1793435941.json` and `cache-combined-package-offdevice.json` under
`data/coordination/voice-latency-fix-20261031/`. No new public effects are enabled.


## Mood delivery production revision, October 31

The owner authorized deployment and a listening check. Revision
`836e103b4c8443a4a263bdd920f0051d` now runs at generation 16 with the same canonical identity.
Seven source files were updated in the frozen release and compatible reader;
both inventories have 360 matching pins. All 113 checked closed personal and
conversation records retained their exact values. The cutover snapshot contains
194 records. Models, voice pins, effect permissions and protected configuration
were retained. No legacy service was started.

The first startup check reached Dashboard before its listener was ready and
safely rolled back source without replacing current state. The successful retry
uses bounded HTTP readiness waits. Authenticated HTTPS health and release
metadata, plus the HTTPS login page, returned 200 with certificate validation.
All four private-release units are active; Qwen kept its existing process.

The disposable Gamma full suite passed 3,802 tests and 156 subtests, with eight
skips and 14 warnings. The two stock-voice factory cases were then exercised
successfully after adding their existing reference WAV to the disposable test
copy. The remaining six skips require three private replay fixtures, an explicit
sandbox-smoke opt-in, or a machine without an available CTranslate2 GPU.
Long-input/owner-profile compaction retains the original token budget.

The actual authenticated speech API generated two sentences in each listening
sample. The mood sample kept neutral delivery for both chunks. The deliberate
concerned sample kept concerned delivery for both chunks while the continuing
mood stayed neutral. They are 6.16 and 6.88 seconds. PCM integrity, ordering and
non-silence were checked. Export receipts do not claim playback or human hearing;
subjective listening acceptance remains with the owner.

The exact source/reader and no-replay cutover snapshot package was copied to
`C:/Users/darke/Documents/ShanaBackups/private-20261031-a157e86d/tts-emotion-836e103b4c8443a4a263bdd920f0051d/source-and-cutover-836e103b4c8443a4a263bdd920f0051d.tar.gz`. Its 730 members were hash checked. Archive SHA-256 is
`0e1366e9f67df8239cf54aae98b31d142f3a87a514cc72038d512b41a35ea1aa`. The snapshot predates the two listening-check
turns. Protected environment/login material and model caches remain external
recovery dependencies; this is not a whole-machine restoration rehearsal.


## Full-reference voice production revision, October 31

The owner preferred the full-reference auditions for their sound and steadier
tone, while leaving emotional-label accuracy unconfirmed. Revision
`a5b47e3e07de4f42bb1419d91d9054d0` is now active at generation 17 with the same
canonical identity. The selected profile is `qwen_shana_reference_delivery`.
This is experimental native Base delivery conditioning, not an acceptance claim
for each named emotion. The previous x-vector preset remains available.

Seven scoped source/configuration files were deployed to the frozen source and
compatible reader. Their inventories have 362 matching pins, including the new
conditioner and actual Qwen server script. The only private environment change
selects the new voice profile; credentials and repository-local protected config
were retained. All four private-release services restarted under their existing
systemd owners. No legacy service or additional external effect was enabled.

The cutover preserved all 117 checked closed personal/conversation records. Its
closed snapshot contains 205 records. Model references, identity and effect
permissions are unchanged. Real GPU TTS produced a valid 3.52-second native-mode
WAV before completing activation. Free memory after that check was 21,537 MiB
and 2,454 MiB on the two GPUs, above the existing 2,048 MiB reserve. Login,
authenticated health and release metadata returned HTTPS 200 with certificate
validation; both source inventories matched the manifest.

The final disposable Gamma suite passed 3,816 tests and 160 subtests, with six
existing fixture/opt-in/hardware skips and 14 dependency warnings. Focused tests
include an actual HTTP client-to-model round trip, raw PCM equality, strict
option validation, legacy-path isolation, cache replacement/lifetime and all
three stock/private voice factory profiles. The initial broad-run fixture failure
was a missing performer image in the disposable copy; restoring the known image
and document fixtures preceded the clean final suite. No product assertion was
weakened. A seeded CPU real-model cache comparison produced identical WAV hashes
and constructed the full reference once. It does not establish a GPU latency
improvement. `git diff --check` passed.

Authenticated live speech then generated two three-sentence replies. Current
mood used neutral on all three chunks, 6.88 seconds total. Chosen warm delivery
used happy on all three chunks, 6.16 seconds total, while continuing mood stayed
neutral. All chunk order/PCM/non-silence/hash checks passed; cached local ASR
recovered all sentences. These export receipts never claim real-time playback
or human hearing. The owner has not yet heard these post-deployment clips.

The 739-member current-source/reader and cutover-snapshot archive is
`source-and-cutover-a5b47e3e07de4f42bb1419d91d9054d0.tar.gz`, SHA-256
`663817c180c1ceafb59b0081c7fe1ded3f8861b25db5cb8c24705774cb8ca992`.
Credentials and model caches are excluded; protected environment material remains
a separate recovery dependency. The 205-record snapshot predates the two new
listening turns. This is not a whole-machine restoration proof. Operation logs:
`C:/Users/darke/.codex/workers/logs/gamma-tts-conditioning-integration-20261031/`.

The archive was copied to `C:/Users/darke/Documents/ShanaBackups/private-20261031-a157e86d/tts-reference-a5b47e3e07de4f42bb1419d91d9054d0/source-and-cutover-a5b47e3e07de4f42bb1419d91d9054d0.tar.gz`. Windows verification checked all 739 member hashes, sizes and safe member names successfully, without extracting files.


## Voice-identity regression and exact restoration, October 31

The owner rejected the deployed full-reference clip because it lost the familiar
Shana voice/tone. Preference for the earlier CPU auditions did not establish
acceptance of the deployed GPU/sentence-chunked setup. ASR, PCM integrity, delivery
labels and passing software tests did not validate speaker identity. No listening
or timbre-analysis tool was available to the agent; it must not claim to have
heard or perceptually validated these files.

The experiment changed x-vector-only cloning to full-reference ICL, sampling
from temperature .32/top-k 6/top-p .52 to .36/8/.58, the reference transcript,
minimum generation tokens and existing audio processing. The audition generated
a whole utterance on CPU/float32, while production generated sentence chunks on
GPU/bfloat16 with different delivery wording. These differences were bundled;
no controlled test isolates a single acoustic cause. The active base instruction
was empty, ruling out an additional inherited base prompt in this case.

Production is restored to exact revision `836e103b4c8443a4a263bdd920f0051d`, now
generation 18, with `qwen_shana_dataset_xvec_soft`. Source and reader inventories
match the prior 360 pins; the original sidecar hash was separately verified.
The effective voice configuration and reference-audio pin exactly match the
pre-experiment manifest. Only the private environment's profile selection was
changed back; current credentials and other settings were retained.

The latest 218-record closed snapshot was preserved, all 121 checked closed
personal/conversation records retained their values, and the current state was
never replaced with an older snapshot. Mood remains neutral and the original
mood/choice continuity fix remains active. HTTPS login, authenticated health and
release endpoints returned 200, and real TTS generated a 5.96-second WAV through
the restored preset. This verifies configuration restoration and synthesis, not
a human listening verdict.

The exact experiment files were quarantined under
`C:/Users/darke/.codex/workers/logs/gamma-tts-voice-recovery-20261031/rejected-candidate/`.
Only files still matching this task's tested hashes were restored in the working
tree; unrelated changes were preserved. Future acoustic work must preserve the
tuned voice configuration and compare the actual deployment path. Emotion
selection, pleasant sound, accurate emotion labels and speaker identity are
separate acceptance properties.

Restored-source verification passed 65 focused tests and five subtests, with one existing dependency warning and no skips. The recovery archive was copied to `C:/Users/darke/Documents/ShanaBackups/private-20261031-a157e86d/voice-recovery-1927fe0e0002419495e2c10383424491/restored-source-and-state-1927fe0e0002419495e2c10383424491.tar.gz`; all 732 members were hash/size/path verified. SHA-256: `68f927a074273bffcc7d82715a8c80c44ff89ac12c6dced033589114c511c81b`. Credentials/model caches remain excluded and whole-machine restoration remains unproved.


## Bounded pace and loudness activation, October 31

Release `582148e5dce441f5a8714fb6cb0ac99e` is active at generation 19 with 361 exact
source pins and a matching reader. Canonical identity remains
`ac3d5311cb6e460ab34ffc79abf79db9`. The latest closed snapshot contains
218 records; all 121 preserved personal/turn records retained their exact hashes.
API, dashboard and Discord were coordinated for the manifest-bound credential.
The Qwen sidecar stayed running. Models, voice pin, protected configuration,
reference audio, generation settings, routing and effect permissions are unchanged.

Private typed delivery choices now persist across sentences, replies and owner
restarts. Pace is 0.90..1.25 and requested loudness is -5..+5 dB relative to the
tuned baseline; pitch remains fixed. Uniform gain stops at 0.95 peak. See the
private interaction contract for processing order and owner boundary feedback.

The full isolated Gamma suite passed 3,822 tests and 156 subtests in 374.23 seconds.
Six skips were three unavailable private P4 replay fixtures, one explicitly
opt-in candidate sandbox smoke, and two CPU-fallback cases inapplicable while
CTranslate2 detects GPUs. Existing dependency deprecations produced 14 warnings.
Actual FFmpeg tempo tests, a closed snapshot export/restore of nondefault delivery,
and byte-for-byte comparison against six offline audition cases also passed.

After activation, HTTPS login, authenticated health, release and status returned
200; source/reader inventories and the original voice configuration pin matched.
The actual private API/model/TTS/audio-export path generated slow/quiet, fast/louder
and baseline two-sentence replies. Each reply kept neutral delivery and the same
requested controls on both chunks. Durations were 7.1499, 5.2213 and 6.8 seconds.
Fast/louder requested +5 dB and applied +4.2267/+4.2906 dB to keep peak below
clipping. The final saved delivery is baseline 1.0/0 dB. These are new stochastic
TTS renders, not a single-recording effect comparison. Audio was exported with
cancelled receipts; no played/heard acknowledgement was fabricated. Perceived
identity and emotional acting on these live exports still require owner listening.

The rollback archive contains the exact before-source/reader pins, closed snapshot,
voice reference and sidecar, manifests and receipt. Credentials and model caches
are excluded. It is copied to
`C:/Users/darke/Documents/ShanaBackups/private-20261031-a157e86d/tts-delivery-upgrade-582148e5dce441f5a8714fb6cb0ac99e/source-and-closed-snapshot-582148e5dce441f5a8714fb6cb0ac99e.tar.gz`.
All 727 members were checked by path, size and SHA-256 on this PC without extraction.
Archive SHA-256: `82c4e22751b0a61d1b53bd994459225600836075e6d385b6cf26bc78ea504bf3`.
Evidence and rerunnable checks are under
`C:/Users/darke/.codex/workers/logs/gamma-tts-bounded-delivery-20261031/`.
Whole-machine restoration remains outside this verification.
