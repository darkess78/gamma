# Fresh private legacy capture

This is a fixed Gamma capture procedure for a proposed first canonical import.
It does not activate Shana, stop services, restore old controls or establish an
atomic production cutover. The earlier October 24 P2 copy remains historical
evidence. Its fixed verifier counts do not constitute fresh acceptance.

Implementation: `src/gamma/coordination/legacy_bundle.py` and
`scripts/capture_legacy_bundle.py`. The bounded file proof is in
`src/gamma/coordination/legacy_bundle_inventory.py`. The current-reader import
contract is in [shana_personal_state.md](shana_personal_state.md); release/drain
and reversal limits are in [shana_private_release.md](shana_private_release.md).

## Commands and authorization boundary

Run from the pinned candidate checkout on Gamma with its virtual environment.
The default and `status` command only observe the fixed source paths and known
writers. They do not hash payloads, create a bundle, load Gamma configuration,
open live SQLite or contact an inference provider.

```bash
.venv/bin/python scripts/capture_legacy_bundle.py
.venv/bin/python scripts/capture_legacy_bundle.py status
```

An operator must first review the finite drain, destination capacity, retention
and restart plan. Wait for shared model/voice use to finish. Stop neither old
Shana nor shared sidecars as part of this tool. The explicit capture command is:

```bash
.venv/bin/python scripts/capture_legacy_bundle.py \
  --source /home/neety/Documents/gamma-main \
  capture --destination /private/parent/fresh-shana-legacy-bundle
.venv/bin/python scripts/capture_legacy_bundle.py \
  inspect --bundle /private/parent/fresh-shana-legacy-bundle
```

The destination parent must already exist. The destination must be fresh,
absolute, outside the source and free of symlink ancestors. The tool creates it
with mode 0700. It never overwrites an existing destination, including a failed
attempt. Choose a fresh destination for a reviewed retry. `inspect` reads declared
capture status only; its `archive_hashes_reverified=false` result is explicit.
There is no assume-drained, force, autostop, restart or activation CLI option.

The API observer injection supports controlled filesystem tests. The real CLI
uses the existing `UNITS`, `MODULE_COMPONENTS` and `observe_target` census. Every
named user unit and known module/ASGI target must be observed inactive before
and after capture. Failed/unknown observations block completion. These are
enumerated observations, not an OS fence against unknown or restarted writers.
Keep the operational drain in place through verification and target ownership.

## Fixed source and evidence scope

The current logical database is `data/memory/gamma.db`. The auxiliary allowlist is:

- `data/assistant_state.json` and `data/assistant_emotion_memory.json`
- `src/gamma/persona/relationship_state.json`, `core.md` and `style.json`
- `config/persona.yaml` and optional `data/core_memories.md`

Missing auxiliary files receive explicit markers. The whole `data/memory` tree
and `data/coding/practice`, `lessons`, `projects`, `autonomous`, `candidates` and
`data/improvement` are archived privately. Optional
`data/runtime/presence/state.json` and `data/coding/idle-state.json` are archived
as operational evidence. Old worker state, active flags, lessons, historical
memory backups, owner/cursor/output records and presence do not become fresh
execution authority. No archive-only root is automatically restored or recalled.

The separate code pin hashes `src/gamma` without bytecode, `pyproject.toml` and
shared top-level TOML/YAML/JSON configuration. It requires the actual legacy
main, dashboard, memory-service and persona-loader files. It does not require
coordination-only release files that production legacy code does not contain.
Code is pinned as hashes, not copied into the runtime archive. Machine `.env`
and `*.local.toml` files are excluded from this code pin.

Within raw evidence, exact `.env`/`.env.*` names, `app.local.toml`,
`voices.local.toml`, private SSH key basenames and private-key extensions cause
an explicit failure before those files are opened. Exact `.env.example`,
`.env.template` and `.env.sample` names are allowed. This is a narrow filename
policy, not a content or secret audit. Other private artifact payloads remain
owner-only raw backup. They are not printed, interpreted, sent to workers or
promoted into recall. No broad key/credential substring heuristic rejects
ordinary source files. Symlinks and special files also fail capture.

## Bounds and copying

The runtime inventory permits at most 30,000 files, 60,000 total entries, 1 GiB
of declared file bytes and 128 MiB per file. Missing entries count toward the
entry bound. Directory listings are bounded before sorting. Code inventory
has separate bounded file/entry/byte limits. Capture reserves 512 MiB free and
allows additional capacity for metadata, recovery copies and the standalone DB.
Bulk copying checks that the reserve remains available. Insufficient space
blocks the attempt; no downloads or automatic cleanup make room for it.

The 900-second deadline is cooperative. It bounds checked loops, backup progress
and SQLite query progress; it is not a hard OS timeout for an unresponsive
filesystem or kernel call.

Paired runtime inventories record membership, missing paths, file hashes, sizes,
ctime/mtime, device/inode and permission mode. They ignore atime. Ordinary OS
access-time updates from reading are not treated as content changes. Source
code hashes are compared separately before and after. Copying opens source
files read-only with nofollow/nonblocking descriptors and verifies before/after
descriptor and named-path identity. Each new 0600 copy is reread and hashed;
its inode must be independent of the source. New evidence directories are 0700.
The procedure never creates source files, writes source SQLite recovery state
or hardlinks source files into the bundle.

Raw database/WAL/journal evidence is copied first. Only independent copies in
the private `scratch/` directory are opened by SQLite for recovery. Raw SHM
evidence can be archived, but recovery creates its own scratch SHM. SQLite then
backs up into `database/memory.sqlite3`, switches the generated DB to DELETE
journaling, checks integrity and records table counts. Both connections close;
no live database connection or live WAL checkpoint is used. Scratch files stay
private and retained. Unknown table semantics belong to the importer, which
records converted, checkpoint, operational, unsupported and metadata dispositions.

## Bundle and failure contract

`manifest.json` uses portable paths:

```json
{
  "database": {"snapshot_path": "database/memory.sqlite3"},
  "copied_files": [{
    "relative_path": "data/assistant_state.json",
    "snapshot_path": "raw/data/assistant_state.json"
  }],
  "inventory_path": "inventory.json"
}
```

The full manifest also contains hashes, table counts, before/after drain
observations, a fresh `capture_id` UUID and actual current `user@host` author
metadata. `inventory.json` contains protected paired archival proofs and the
separate code pin. The flags explicitly report globally atomic capture, an OS
writer fence, activation and imported execution authority as false. A successful
paired census only describes the observed capture window.

Failures retain the partial private destination and a content-free failure code
in `capture-status.json` where storage still permits it. No raw exception message,
private content or arbitrary process diagnostics enter that status. A killed
process or failed storage write can leave an incomplete status and temporary
private metadata files. There is no broad deletion or automatic resend/retry.

A late final-status failure can leave `manifest.status=complete` alongside
`capture-status.status=failed`. Fresh verification/import acceptance must require
the final capture status to be complete, match capture identifiers and verify
all declared hashes/proofs. A manifest alone is insufficient. The existing
historical importer supports older manifests and has not been broadened into an
archive verifier. A separate dynamic fresh-copy verification protocol must
check this capture contract before offering the bundle for canonical import.

## Observed validation, 2026-10-27

The Gamma suite passed 45 tests: 34 builder cases plus 11 independent file-proof
cases. All sources were disposable fixtures and all drain observations were
controlled. Tests prove connected portable import through the actual Core and
importer; a committed WAL-only row reaches the standalone DB without changing
source bytes or mtimes. They cover appearing writers, changed/missing/member
paths, separate code drift, exact secret-path refusal, symlinks/FIFOs, resource
bounds, copied-byte corruption, actual SQLite query interruption, final-status
failure and descriptor cleanup. Twenty repeated successful SQLite captures
showed zero descriptor growth. No production capture, service operation, model
call or human acceptance is implied by these tests.

## Dynamic mechanical review

`scripts/verify_legacy_bundle.py` replaces no historical receipt. With only an
absolute `--bundle`, it reads declared metadata and explicitly reports that
verification has not run. It does not probe services or open SQLite. The
mechanical import/reversal rehearsal requires an explicit fresh private output:

```bash
.venv/bin/python scripts/verify_legacy_bundle.py --bundle /private/full-bundle
.venv/bin/python scripts/verify_legacy_bundle.py --bundle /private/full-bundle \
  --verify --output /private/fresh-mechanical-review
```

The verifier requires final capture status, manifest and inventory to be complete
with matching capture IDs. It checks paired inventories, exact raw membership
including implied directories, private independent copies, every declared raw
hash/size, code-pin metadata, and the standalone database's immutable read-only
integrity/table counts. It checks all input proofs again after the rehearsal.
The import portion additionally permits at most 200,000 total database rows and
256 MiB of database/auxiliary or serialized row input. Paths are absolute at the
command boundary; relative members are bounded and reject traversal and links.
The 900-second deadline remains cooperative, including SQLite query progress.

Only the checked standalone DB and allowed auxiliary files enter an independent
`verified_import_projection`. That minimal manifest names its role and original
full-bundle proofs; it has no complete capture-status file. Old work/learning
archives remain outside execution state and are retained by inventory reference.
The actual Core repeats import, checks table payload digests/dispositions and
legacy refs, tests shared-audience privacy, and retains a labelled synthetic
exchange and unfinished owner commitment through closed snapshot, restore and
current-reader reopen. Its identity locks and all state belong to the fresh
output. Fixed replies and provider/effect tripwires supply no model or tool work.
Reports contain author metadata, counts, hashes and factual checks, not private
source text. These checks do not accept semantic adoption, human comfort or
permanent activation.

The combined Gamma suite passed 226 tests and eight subtests, including 32 new
review cases with synthetic captures and controlled observations. It includes
the actual CLI, incomplete/corrupt/escaped/extra/FIFO members, file/row bounds,
an interrupted immutable SQLite query, blocked provider/work attempts and raw
evidence changed after rehearsal. No production capture or import ran.

## Descriptor receipt correction

An independent combined test run exposed a shared-process descriptor assertion
shrinking from 13 to 12 as unrelated resources were collected. The original
failure remains at
`C:/Users/darke/.codex/workers/logs/shana-remaining-1793106280507-61712/stdout.log`.
The corrected test uses a fresh Linux subprocess, imports before its baseline,
disables GC without collecting, runs 20 actual synthetic captures and requires
an unchanged exact descriptor map plus zero source/output handles after every
capture. It observed 3 to 3. Parent's independent affected-order rerun passed
65 tests and retained the proof under
`data/coordination/remaining-20261027/legacy-capture-review/` as
`descriptor-proof-source.json`, `descriptor-review.json` and
`descriptor-rerun.log`. Capture runtime remained unchanged at SHA-256
`33db449a08e017454c8e526292127a4ba4fd87b60eded3f558bb932ad241a64a`;
the corrected capture-test SHA-256 is
`463337e5e946abf67a866e27f2dc65863bfe532c02ee3b6f750b7e3225f6dc42`.

## Actual fresh capture and restoration, 2026-10-30

Between 7:44 and 7:45 p.m. Denver time, the authorized finite rehearsal captured the
actual legacy source under an observed drain and restored the original services.
The raw operation receipt is
`data/coordination/finish-20261030/legacy-capture-operation.json`, SHA-256
`a95956101f40123dda05c620a604b3995062da237a7701ab9d14cdb55fedb8eb`.
It is separate from the October 24 historical copy and the October 27 synthetic
tests.

The capture contains 17,343 raw files and 630,729,578 bytes. All 16 tables passed
the dynamic immutable-reader census. The actual current Core imported 32 distinct
legacy references, checked shared-recall privacy, repeated import idempotently,
exported the latest closed state and reopened a compatible restore. A labelled
synthetic later exchange and unfinished owner commitment survived that reversal.
The verifier recorded zero provider/effect calls and no imported operational
authority. Raw work and learning archives stayed outside recall/execution state.
These are mechanical results; `semantic_adoption_accepted` and human acceptance
remain false.

The operator used an owned transient user oneshot with actual unit properties
checked before stopping anything: an 18-minute-20-second start timeout,
15-minute stop timeout, control-group termination and the exact-folder
`ExecStopPost` restore command. Restoration refused to begin while the original
capture PID/start ticks remained present. It then started API, checked unauthenticated
401/authenticated 200, started Dashboard and checked health 200, then restored
the originally active selectors. All four original services returned active.
Their PIDs changed intentionally: API 32730 to 1178332, Dashboard 33020 to
1178724, autonomous selector 51714 to 1178754 and idle selector 33077 to 1178756.
This is not an unchanged-PID preservation claim.

The bundle is point-in-time private evidence after the old runtime resumed.
There was no candidate installation or production ownership cutover, no global
atomic legacy snapshot and no fence against unknown noncooperating writers.
The durable private Windows archive now contains 460,705,306 bytes, SHA-256
`43f30d85b6838aa88c820ceed81fa40b004413a972544600435c90b89e01a88c`.
Independent hashing of that actual file matches Gamma. The file inherits access
only for `Gaming\darke` and `SYSTEM` from its protected parent directory.
The receipt is `data/coordination/finish-20261030/private-backup-independent-review.json`.
The exact Windows archive also completed a fresh return-trip rehearsal into
private Gamma storage. Corrected safe extraction observed 19,576 logical entries
and 39,152 raw headers. The actual current reader reverified all 17,343 raw
files, 630,729,578 bytes, 16 tables and 32 legacy references, with repeated import,
current-reader reopening and both later synthetic fixtures intact. Input archive
and extracted bundle were unchanged; provider/effect calls stayed zero.
The raw corrected report is
`data/coordination/finish-20261030/offdevice-corrected-final-report.json`, SHA-256
`501c3945475a09e053032721fd2f2d81c1a03f8ddf89471856754d76c8bb6a27`.

An earlier extraction succeeded but counted zero raw headers because Python
3.14 bypassed the public parser hook. That report remains retained without
accepting its header-bound claim. The corrected helper guards before metadata
body reads, has 17 passing Gamma cases including three regressions that failed
the old version, and fails closed if actual raw-header observation is missing.
This is manual off-device archive recovery into a compatible current reader,
not a whole-machine rebuild or accepted semantic adoption.
