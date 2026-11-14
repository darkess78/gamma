# OSRS Screen Recognition Data

Tracked contents in this directory remain limited to this guide and the CSV
manifest template. Private ignored artifacts cover accepted `session_001`,
`session_002`, and `session_003`, plus the canonical 18-row protocol manifest
and combined audit. They also cover the separate accepted 60-image
`session_004` dataset. All 18 historical reviewed images are accepted private
protocol-validation records; the 60 new images are accepted private
feasibility-dataset records. None are approved for Git tracking.

V1 source images are exact 2560x1440 PNGs captured from Neety's complete
primary monitor by a small manually triggered Tkinter helper after RuneLite is
confirmed visible and maximized in windowed mode. The helper does not crop,
resize, or normalize. The game uses Resizable - Classic and Stretched Mode at
90 percent, with Increased Performance enabled, integer scaling disabled, and
keep aspect ratio enabled. The earlier 2558x1438 calibration was superseded
before dataset acceptance, and no image was accepted under it.
`fixed_game_canvas_v1` applies the same half-open crop
`(0, 23, 2287, 1392)` to every v1 image, producing a 2287x1369 reviewed image
with aspect ratio 2287:1369 (decimal 1.670562454). No per-image adjustment is
allowed. Later preprocessing uses RGB.

`session_001` is accepted: the owner reviewed `img_0001` through `img_0006`,
confirmed their labels and crops, and approved the remaining visible in-game
content for the private local dataset. Its six final-manifest rows are
`inclusion_status=included` with `redaction_checked=true`.

`session_002` adds six validated raw screenshots and reviewed images
`img_0007` through `img_0012`. Its private
`data/manifests/session_002_draft.csv`,
`data/manifests/session_002_audit.json`, and session-specific review contact
sheet preserve the pre-approval intake state. The owner confirmed all six
labels and crops, confirmed complete class-defining content, and approved the
remaining visible in-game content. The accepted state is recorded in private
`data/manifests/session_002.csv` and
`data/manifests/session_002_review.json`; every final row is
`inclusion_status=included`, `redaction_checked=true`, and
`split=unassigned`.

`session_003` adds six validated raw screenshots and private reviewed images
`img_0013` through `img_0018`, with one example per class. Its private
`data/manifests/session_003_draft.csv`,
`data/manifests/session_003_audit.json`, and session-specific review contact
sheet preserve the pre-approval state. The owner confirmed the six labels,
crops, and complete class-defining content and approved the remaining visible
in-game content. Private `data/manifests/session_003.csv` and
`data/manifests/session_003_review.json` record six `included`,
`redaction_checked=true`, `split=unassigned` rows separately.

Thus all 18 mini-pilot screenshots are collected, owner-reviewed, and included.
Every class has exactly three accepted examples, one per session. All 18 image
IDs, reviewed paths, source groups, source hashes, and reviewed hashes are
unique; no exact source or reviewed duplicate was found. Private
`data/manifests/protocol_validation_18.csv` is the canonical combined accepted
manifest, and `data/manifests/protocol_validation_18_audit.json` records the
passed deterministic protocol checks. All 18 splits remain `unassigned`. No
training, feature extraction, evaluation, runtime integration, live monitoring,
gameplay control, or multi-game implementation has occurred.

The session_002 final manifest preserves the draft's
`camera_angle_bucket=north_facing` value on all six rows.
The session_003 draft also uses `camera_angle_bucket=north_facing` on all six
rows because the orientation is technically clear.
`zoom_bucket` remains blank because no calibrated near/medium/far threshold is
documented, and `location_or_activity_bucket` remains blank because no
controlled vocabulary for that field is currently defined. `not_applicable`
was not used for either field.

The earlier plan to add 42 images across `session_004`-`session_010` to the 18
accepted protocol images is superseded. The historical sessions 001-003 set
remains separate protocol-validation evidence and is not part of the new model
dataset. The new standardized chat-open archive uses the single true capture
identity `session_004` and contains 60 technically validated owner-captured
candidate images, exactly 10 per class. Draft intake preserved the 60 raw PNGs,
created reviewed crops `img_0019`-`img_0078`, and wrote the provenance CSVs,
draft manifest, audit JSON, and review contact sheet.

The owner confirmed all 60 candidate labels, passed all 60 images through
privacy review, and accepted all 60. Private `data/manifests/session_004.csv`
and `data/manifests/chat_open_feasibility_60.csv` contain the same 60 records,
exactly 10 per class, with `inclusion_status=included`,
`redaction_checked=true`, `source_consent=owner_captured`, and
`split=unassigned`. The canonical feasibility manifest contains no historical
`img_0001`-`img_0018` rows. Owner review approved 54 final source groups:
five non-singleton groups and 49 singleton groups. Both accepted manifests now
record those nonblank groups while remaining `split=unassigned`.
`chat_open_feasibility_60_split_v1.csv` is the separate deterministic grouped
split: 36 train, 12 validation, and 12 test, with 6/2/2 per class. All
non-singletons are in training, validation and test contain only singletons,
and no group crosses splits. All 60 rows share `session_004`, so this is not
capture-session-held-out evaluation. No feature extraction, notebook
execution, model training, or evaluation has begun.

Future image storage must follow the capture protocol and repository data
policy. Raw and reviewed screenshots, draft/session manifests, audit JSON, and
review contact sheets remain local, private, and ignored. Only a specifically
reviewed and redacted subset may be tracked after an explicit owner decision.

## Manifest Columns

- `image_id`: opaque unique image identifier.
- `relative_image_path`: relative path only; no private absolute paths.
- `label`: one of the six taxonomy labels.
- `capture_session_id`: opaque collection-session group, such as
  `session_001`.
- `source_group_id`: common-origin or near-duplicate group that cannot cross
  train, validation, and test. Related rows may share one nonblank value.
  Blank values are permitted only while grouping is pending and
  `split=unassigned`; every row must have a resolved group before split
  assignment.
- `inclusion_status`: `needs_review`, `included`, or `excluded`.
- `exclusion_reason`: empty when included; concise reason when excluded.
- `resolution`: final reviewed-image width and height after
  `fixed_game_canvas_v1`; fixed value `2287x1369` under this calibration.
- `aspect_ratio`: final reviewed-image aspect ratio after the fixed crop;
  fixed value `2287:1369` under this calibration. The 2560x1440 source-file
  geometry does not replace these reviewed-image values.
- `client_type`: fixed value `runelite`.
- `display_mode`: fixed value `maximized_windowed`.
- `ui_layout_or_scale`: fixed value
  `resizable_classic_stretched_90pct`.
- `crop_policy`: fixed value `fixed_game_canvas_v1`.
- `camera_angle_bucket`: `north_facing`, `east_facing`, `south_facing`,
  `west_facing`, `mixed_or_unclear`, or `not_applicable`.
- `zoom_bucket`: `near`, `medium`, `far`, or `not_applicable`.
- `location_or_activity_bucket`: coarse non-identifying variation category.
- `overlay_notes`: `fixed_profile_v1` for the stable visible in-canvas overlay
  profile, `none` where applicable, or a concise nonprivate exception when an
  unexpected overlay causes review or exclusion. Do not record private sidebar
  contents, account data, or notification text.
- `redaction_method`: `none` or `opaque_mask`.
- `redaction_checked`: explicit privacy-review boolean.
- `source_consent`: fixed value `owner_captured`.
- `split`: `unassigned` during capture/review; later `train`, `validation`, or
  `test` after audit.
- `notes`: ambiguity, exclusion, or review notes.

Use opaque IDs and paths only: `img_0001`, `img_0001.png`, and
`reviewed/img_0001.png`. Do not encode labels, activities, locations, dates,
account names, or world numbers. An image is eligible for training or
evaluation only when its inclusion status is `included`, redaction check is
`true`, consent is `owner_captured`, and its label is one of the six approved
values.

The template intentionally contains no file hashes, perceptual hashes, model
outputs, probabilities, embeddings, or generated-feature paths. Later audit
tooling may produce those separately.
