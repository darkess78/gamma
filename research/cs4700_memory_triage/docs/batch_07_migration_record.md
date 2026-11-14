# Batch 07 Migration Record

Status: Working primary-eligible dataset migration complete
Dataset release: `primary_v0_7`

## Scope And Owner Authorization

The owner explicitly approved canonical migration of the 43 reviewed rows in
`docs/batch_07_candidate_drafts.csv`, and only those rows. The authorization
did not permit candidate rewrites, scope expansion, training, evaluation,
runtime work, staging, committing, or pushing. No policy-only or
existing-memory-context row was added.

## Sources And Initial Integrity

The migration used the Batch 07 authoring specification, candidate-draft CSV,
and candidate-review record after inspection of the frozen pilot, the
357-row canonical dataset, the labeling and expansion documentation, and all
Batch 01 through Batch 06 authoring, draft, review, and migration artifacts.

Initial SHA-256 values were:

- frozen pilot CSV: `ef369b736ed030dbc0ec525ac10870e1bd870c17cdace08522866c28f43fed38`;
- pre-migration canonical CSV: `20c40384ffe0b160bb0d7f59031136fc215efe87d8ad06c15758710a1fbc965d`;
- Batch 07 authoring specification: `4cf01ea38a68bf64577ec09e4bc2e7d8c7259394cce97a269120cc5ece22f60f`;
- pre-migration Batch 07 candidate-draft CSV: `48330c08cf093af58da49241218095a4564298d09aff1792d3a90516d10d0aec`;
- pre-migration notebook: `1159a5f60f6eb370aecc61a3d23f59aad412a03cdec8401926db226d45d0805f`.

Before migration, the canonical dataset contained 357 rows: 180 `keep` and
177 `skip`. Its composition was 57 eligible pilot-derived rows and 50 rows
from each of Batch 01 through Batch 06. All 357 rows had
`primary_eligible=true` and `split=unassigned`. The frozen pilot hash matched
the required value above.

## Draft Verification And Mapping

The source draft parsed as exactly 43 rows under the established 12-column
schema. B07-01 through B07-20 were 20 `keep` rows and B07-21 through B07-43
were 23 `skip` rows. All source and paraphrase groups were unique, all rows
were high-confidence and primary eligible, and all statuses were
`pending_owner_review` before the authorized status update.

Source records map one-to-one in source order:

- B07-01 through B07-09 map to `mtb07_0001` through `mtb07_0009`;
- B07-10 through B07-19 map to `mtb07_0010` through `mtb07_0019`;
- B07-20 through B07-29 map to `mtb07_0020` through `mtb07_0029`;
- B07-30 through B07-39 map to `mtb07_0030` through `mtb07_0039`;
- B07-40 through B07-43 map to `mtb07_0040` through `mtb07_0043`.

The draft CSV status field alone changed to `approved_for_migration`. Parsed
comparison confirmed every non-status field, including candidate text,
remained identical.

## Canonical Metadata And Preservation

Batch 07 follows the Batch 06 canonical convention:
`candidate_source=assistant_assisted_draft`,
`labeler_id=owner_assisted_review`, `guide_version=v0.3`,
`privacy_checked=true`, `primary_eligible=true`, `batch_id=batch_07`,
`dataset_release=primary_v0_7`, and `split=unassigned`. Canonical `notes`
copies the draft `draft_notes` value exactly.

The original canonical file was retained outside the repository for binary
comparison. Its header and all 357 original data rows remain an exact
byte-for-byte prefix of the migrated file and retain their original order.
Only the 43 Batch 07 rows were appended.

## Final Dataset Validation

The canonical CSV retains the exact established 18-column schema and contains
400 rows: 200 `keep`, 200 `skip`, 57 pilot-derived rows, 50 rows from each of
Batch 01 through Batch 06, and 43 Batch 07 rows. Every row has
`primary_eligible=true` and `split=unassigned`. Batch 07 source records and
canonical IDs are complete, unique, sequential, and ordered; all Batch 07
rows use `primary_v0_7`. Each canonical Batch 07 candidate text and reviewed
draft field exactly matches its approved draft source.

## Audit Results

Normalized exact comparison found no duplicate within Batch 07 or between
Batch 07 and the prior 357 canonical rows. Normalized character-sequence
similarity at the established 0.72 suggestion threshold produced no flagged
pair within Batch 07 or against the prior corpus. The highest within-batch
score was 0.514 for B07-35 and B07-37; manual review confirmed distinct
unratified-advice and incident-workaround decisions.

The complete row-level semantic-neighbor audit in the candidate-review record
was rechecked against the Batch 01 through Batch 06 corpus and within Batch 07.
No candidate changed its approved semantic slot, and no likely duplicate,
paraphrase, or blocking overlap was found.

Privacy review confirmed newly authored synthetic content only, with no real
people, institutions, games, platforms, channels, accounts, viewers, schools,
instructors, user data, secrets, local runtime material, personal memories, or
Gamma behavior. Every row remains self-contained and labelable from its own
text; none depends on stored-memory state, hidden context, or a policy-only
decision. Cross-label review confirmed that domain, role, accessibility,
recurrence, quantity, importance, and bounded-duration wording do not provide
a mechanical label shortcut.

## Notebook And Ignore-Policy Validation

The notebook received only a factual Markdown status/provenance update for the
400-row `primary_v0_7` dataset. It was not executed. It remains valid JSON;
all code-cell sources are unchanged, execution counts remain null, and outputs
remain empty. No code cell or notebook structure changed.

The exact-file allowlist was extended only for this Batch 07 migration record;
the three pre-existing Batch 07 exceptions remain unchanged. The Batch 07
authoring specification, candidate drafts, candidate review, and migration
record are trackable. Representative screenshot, local mapping, model, export,
checkpoint, raw-data, and unrelated-notebook paths remain ignored under both
the CS 4700 and CS 4710 research roots.

## Boundary

No model training, tuning, evaluation, metric calculation, split assignment,
notebook execution, model, checkpoint, export, chart, generated artifact,
Gamma runtime integration or change, staging, commit, or push occurred. Any
checkpoint commit requires separate owner approval.
