# CS 4710 OSRS Screen Recognition Offline Workspace

Status: Frozen baseline selected; one-time final test evaluation complete

This offline workspace supports the project defined by
[`specs/class-projects/cs4710-osrs-screen-recognition.md`](../../specs/class-projects/cs4710-osrs-screen-recognition.md).
The specification contains the maintained Gamma integration decisions; this
folder contains the private dataset workflow and offline baseline tooling.

## First Scope

Given one manually uploaded OSRS screenshot, predict exactly one label:

- `normal_gameplay`
- `bank_open`
- `dialogue_or_choice_open`
- `shop_open`
- `grand_exchange_open`
- `unknown_other`

V1 uses one fixed profile: a manually triggered Tkinter helper captures the
complete primary monitor as an exact 2560x1440 PNG after the owner confirms
that RuneLite is visible and maximized in windowed mode
(`display_mode=maximized_windowed`). The helper does not crop, resize, or
normalize the source. The game uses Resizable - Classic and Stretched Mode at
90 percent, with Increased Performance enabled, integer scaling disabled, and
keep-aspect-ratio enabled
(`ui_layout_or_scale=resizable_classic_stretched_90pct`). The earlier
2558x1438 calibration was superseded before dataset acceptance; no image was
accepted under it. The saved 765x503 RuneLite Game size is not the active
maximized capture resolution.

The calibrated `fixed_game_canvas_v1` crop uses half-open coordinates
`(0, 23, 2287, 1392)` and is applied identically to every v1 source capture.
It produces a 2287x1369 reviewed image with aspect ratio 2287:1369 (decimal
1.670562454). The crop preserves the required game viewport and interface
regions while excluding the RuneLite title area and account name, normally
open plugin sidebar and narrow icon strip, Windows taskbar, and surrounding
desktop. No per-image adjustment is allowed; collection must stop for
recalibration if window geometry, sidebar width, or the display setup changes.
The fixed in-canvas overlay profile may retain stable normal overlays, but
label-revealing overlays are disabled. Later preprocessing uses RGB.

All three sessions of the 18-image protocol-validation mini-pilot are complete.
The owner confirmed every label and crop, confirmed complete class-defining
content, and approved the remaining visible in-game content for the private
local dataset. All 18 rows are `inclusion_status=included`, all 18 are
`redaction_checked=true`, and all 18 remain `split=unassigned`. Each session
contains exactly one example of each label, so every label has three examples.
All image IDs, reviewed paths, source groups, source hashes, and reviewed hashes
are unique; no exact source or reviewed duplicate was found.

The private canonical `protocol_validation_18.csv` and its machine-readable
protocol audit record the accepted combined pilot. Deterministic validation
passed for manual collection, source geometry, fixed cropping, labels, balanced
session design, manifest structure, intake audits, cross-session uniqueness,
privacy review, owner-approval provenance, and accepted-manifest generation.
This result validates the dataset process, not classifier performance, broad
OSRS coverage, cross-display robustness, multi-game capability, automated
gameplay, or runtime readiness. No model training, feature extraction,
evaluation, dataset splitting, live monitoring, gameplay control, multi-game
implementation, or Gamma runtime integration has occurred.

The historical 18-image sessions 001-003 set remains protocol-validation
evidence and is not part of the new model dataset. A separate standardized
chat-open archive for `session_004` has now passed technical draft intake. It
contains 60 owner-captured candidate images, exactly 10 per class, mapped in
verified selection-manifest row order to `img_0019` through `img_0078`.
The owner confirmed all 60 labels, passed all 60 images through privacy review,
and accepted all 60. Private `session_004.csv` and
`chat_open_feasibility_60.csv` record exactly 10 accepted images per class with
`inclusion_status=included`, `redaction_checked=true`, and
`split=unassigned`. Owner source-group review approved 54 final groups: five
non-singleton groups and 49 singleton groups. The deterministic grouped split
contains 36 train, 12 validation, and 12 test images, with 6/2/2 per class.
All non-singleton groups are in training, validation and test contain only
singletons, and no group crosses splits. The historical 18-image
protocol-validation dataset remains separate. The first train/validation-only
baseline selection is complete. The frozen Logistic Regression `C=0.01`
pipeline was refitted once on the 48 train+validation images, eight per class,
and evaluated exactly once on the 12-image test set. No post-test fitting,
tuning, or model change occurred.

The raw and reviewed images and all session records remain ignored and private.
The pre-approval record is preserved at
`research/cs4710_osrs_screen_recognition/data/manifests/session_001_draft.csv`,
`research/cs4710_osrs_screen_recognition/data/manifests/session_001_audit.json`,
and
`research/cs4710_osrs_screen_recognition/reports/session_001_review_contact_sheet.png`.
The accepted state is recorded separately at
`research/cs4710_osrs_screen_recognition/data/manifests/session_001.csv` and
`research/cs4710_osrs_screen_recognition/data/manifests/session_001_review.json`.
The preserved second-session pre-approval package is
`research/cs4710_osrs_screen_recognition/data/manifests/session_002_draft.csv`,
`research/cs4710_osrs_screen_recognition/data/manifests/session_002_audit.json`,
and
`research/cs4710_osrs_screen_recognition/reports/session_002_review_contact_sheet.png`.
Its accepted state is recorded separately at
`research/cs4710_osrs_screen_recognition/data/manifests/session_002.csv` and
`research/cs4710_osrs_screen_recognition/data/manifests/session_002_review.json`.
The third-session pre-approval package is
`research/cs4710_osrs_screen_recognition/data/manifests/session_003_draft.csv`,
`research/cs4710_osrs_screen_recognition/data/manifests/session_003_audit.json`,
and
`research/cs4710_osrs_screen_recognition/reports/session_003_review_contact_sheet.png`.
Its accepted state is recorded separately in private `session_003.csv` and
`session_003_review.json`. The combined private pilot is recorded in
`protocol_validation_18.csv` and `protocol_validation_18_audit.json`.
The earlier calibration image is not a dataset record.

## Explicit Non-Goals

- real-time, automatic, periodic, background, or continuous capture;
- game automation or gameplay control;
- object detection or bounding boxes;
- NPC, item, or world-coordinate identification;
- a CNN trained from scratch;
- modifications to Gamma's dashboard or API routes.
- support claims for untested clients, layouts, resolutions, or plugins.

## Contents

- [`docs/label_taxonomy.md`](docs/label_taxonomy.md)
- [`docs/screenshot_capture_protocol.md`](docs/screenshot_capture_protocol.md)
- [`docs/dataset_card.md`](docs/dataset_card.md)
- [`data/README.md`](data/README.md)
- [`data/osrs_screen_manifest_template.csv`](data/osrs_screen_manifest_template.csv)
- `src/crop_v1.py` — deterministic one-image v1 crop utility
- `src/intake_session_v1.py` — explicit multi-session, six-example private
  intake utility with fixed OSRS plans for sessions 001, 002, and 003
- `src/build_accepted_manifest_v1.py` — accepted-session merger and validator
  for the private canonical pilot manifest
- `src/intake_balanced_archive_v1.py` — safe, transactional balanced-archive
  validator and draft-intake utility
- `src/model_baseline_v1.py` — metadata validation, deterministic pixel-feature
  extraction, train-only fitting, and validation-only baseline selection
- `tests/` — synthetic crop, intake, balanced-archive, accepted-manifest, split,
  and baseline tests; no real screenshots
- `notebooks/01_dataset_audit.ipynb` — output-free local audit notebook that
  validates the canonical accepted pilot and displays images by session

## Planned Phases

1. Owner reviews taxonomy and tie-breaking rules.
2. **Complete:** Owner review and deterministic validation of the accepted
   18-image protocol mini-pilot.
3. **Complete:** Technical draft intake of the separate 60-image balanced
   `session_004` candidate set, with 10 candidates per class.
4. **Complete:** Owner-approved source-group finalization and deterministic
   36/12/12 grouped split preparation.
5. **Complete:** Select the controlled environment-constrained pixel baseline
   using train and validation only.
6. **Future:** Compare compatible offline pretrained image embeddings if
   weights are deliberately provisioned.
7. **Complete:** Refit the frozen Logistic Regression `C=0.01` pipeline once
   on train+validation and evaluate the grouped test set exactly once, without
   post-test tuning; the 18-image mini-pilot is not final evaluation data.
8. Consider Gamma presentation/integration only in a later approved task.

## Future Gamma Boundary

This OSRS project is Gamma's first controlled game-perception experiment, but
the current assignment remains an offline six-state OSRS image classifier. Its
crop, intake, manifest, audit, and evaluation work may later inform
game-specific perception profiles. A future second game would provide the
evidence needed to extract genuinely shared abstractions; do not build a
speculative multi-game framework now.

The three collected intake sessions now demonstrate only these session-level
abstractions: explicit capture-session plans, one deterministic crop profile,
opaque IDs, manifest and audit provenance, and owner approval gates. These
mechanisms remain OSRS-specific and are not yet claimed to be game-independent;
a second actual game is required before extracting shared multi-game
abstractions.

Future live perception would replace manual screenshots with an explicitly
authorized frame source. Gameplay control would be a separate layer consuming
structured observations with confidence: a classifier or perception model must
never directly press keys or click. Any action layer would require
game-specific adapters,
explicit authorization, bounded action sets, rate limits, emergency-stop
behavior, validation, and independent testing. Gameplay automation, live screen
monitoring, multi-game implementation, and Gamma runtime integration remain
outside the CS 4710 assignment.

## Session 004 Feasibility Dataset

The earlier plan to combine the accepted 18-image protocol set with 42 images
across sessions 004-010 is superseded. The new model-dataset candidate is a
separate 60-image standardized chat-open set, all captured under the single
identity `session_004`; no fake sub-sessions are assigned. Its private raw
files, reviewed crops, provenance CSVs, draft manifest, technical audit, and
review contact sheet have been generated.

The accepted set has exactly 10 images per class and uses opaque IDs
`img_0019` through `img_0078`. Owner label and privacy review is complete for
all 60. The owner approved the source-group proposal, establishing 54 final
groups: five groups contain related images and the other 49 are singletons.
Those groups are recorded in the still-unsplit accepted manifests. The
separate split manifest uses seed 4710 and has 36 train, 12 validation, and 12
test rows, exactly 6/2/2 per label. Every non-singleton group is in training;
validation and test contain only singleton groups, and no group crosses
splits. Because every row still comes from `capture_session_id=session_004`,
this is source-group-aware preparation, not capture-session-held-out
evaluation, and that limitation must be reported. Train/validation feature
extraction and model selection are complete without notebook execution. The
frozen pipeline was fitted once on 48 train+validation images, eight per class,
and the 12-image test set was evaluated exactly once with no post-test tuning.

## Frozen Train/Validation Baseline

Because no compatible pretrained vision weights were available offline, the
first baseline uses the complete reviewed frame resized with aspect ratio
preserved onto a black 96x64 RGB canvas, then flattened to 18,432 float32 pixel
features. No crop, distortion, or augmentation is added at this stage.

Training-only `StandardScaler` pipelines with Logistic Regression and Linear
SVM were compared using validation metrics only. A single-nearest-template
cosine classifier over the same raw features was recorded as a comparison and
was not eligible for selection. The selected Logistic Regression `C=0.01`
configuration was frozen, refitted once on all 48 train+validation images, and
evaluated exactly once on the untouched test set. No model choice, fitting, or
tuning occurred after test prediction.

Exact final metrics remain private under
`artifacts/model_baseline_v1/final_test/`. The evaluation has only 12 test
images, two per class, all from one capture session and one fixed layout and
configuration. It is source-group-aware but not capture-session-held-out, so
it is feasibility evidence rather than deployment-level generalization.
Compatible offline pretrained embeddings remain preferred future work when
weights become deliberately available.

Private generated outputs are ignored under:

- `artifacts/model_baseline_v1/features/train_validation_features.npz`
- `artifacts/model_baseline_v1/models/selected_train_only_model.joblib`
- `artifacts/model_baseline_v1/metrics/validation_results.json`
- `artifacts/model_baseline_v1/metrics/selection_audit.json`
- `artifacts/model_baseline_v1/plots/validation_confusion_matrix.png`
- `artifacts/model_baseline_v1/final_test/`
