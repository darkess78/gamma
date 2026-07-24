# CS 4710: Gamma-Assisted OSRS Screen Recognition

Status: Frozen baseline selected; one-time final test evaluation complete
Last verified: 2026-07-24

Working title: **Gamma-Assisted OSRS Screen Recognition**

## Objective

Classify a manually uploaded Old School RuneScape screenshot into one limited
screen-state label. The first version is a controlled computer-vision project,
not a general game-understanding or automation system.

## Confirmed Gamma Architecture

### Generic image path

- **Confirmed:** `VisionService.prepare_image()` in
  `src/gamma/vision/service.py` validates non-empty JPEG, PNG, WebP, or GIF
  uploads, enforces `vision_max_image_bytes`, hashes image content, and stores
  accepted input under the configured image input directory.
- **Confirmed:** `VisionService.analyze_image()` sends the prepared image and a
  text prompt to a vision-capable LLM and normalizes free-form model JSON into
  `VisionAnalysis`.
- **Confirmed:** `VisionAnalysis` in `src/gamma/schemas/response.py` contains a
  summary, visible text, object descriptions, text blocks, interface elements,
  document structure, likely actions, spatial notes, follow-ups, and an overall
  confidence value. It does not contain bounding boxes, masks, or a controlled
  OSRS label.
- **Confirmed:** `ConversationService.respond_with_image()` first obtains a
  structured generic analysis, then passes both the image and that context into
  the ordinary response pipeline.

### Routes and providers

- **Confirmed:** Shana routes are `POST /v1/vision/analyze` and
  `POST /v1/conversation/respond-with-image` in
  `src/gamma/api/routes.py`.
- **Confirmed:** Dashboard proxy routes are `POST /api/vision/analyze` and
  `POST /api/vision/respond` in `src/gamma/dashboard/main.py`, delegated through
  `DashboardService.analyze_remote_image()` and
  `DashboardService.respond_remote_image()`.
- **Confirmed:** `OpenAIAdapter` sends base64 data-URL image inputs through the
  OpenAI Responses API. `LocalLLMAdapter` sends base64 image arrays to Ollama
  after a configured capability check.
- **Confirmed:** `tests/test_llm_router.py` covers hosted and local vision route
  selection. `tests/test_dashboard_routes.py::test_vision_routes` covers the
  dashboard proxy methods. There is no dedicated `VisionService` unit test or
  controlled OSRS classifier test.

### Confirmed dashboard defect

- **Confirmed:** `src/gamma/dashboard/static/index.html` renders a vision upload
  control whose buttons call `analyzeVisionImage()` and
  `askGammaAboutImage()`.
- **Confirmed:** Neither function is defined or exported by the current
  dashboard static JavaScript. `src/gamma/dashboard/static/live.js` retains
  only unused vision-selection variables, while memory/navigation modules
  retain disconnected history helpers.
- **Working decision:** This defect must be fixed and tested in a separate
  runtime task. It is documented here but intentionally not changed by the
  offline scaffold.

## Generic LLM Vision Versus Controlled Classification

- **Confirmed:** Current Gamma vision is prompt-driven generative analysis.
  Labels, wording, and confidence are supplied by an LLM and are not tied to a
  frozen six-class decision boundary.
- **Working decision:** The CS 4710 classifier uses a documented dataset,
  fixed taxonomy, reproducible preprocessing, leakage-safe splits, and
  per-class evaluation. Generic Gamma vision is a comparison baseline, not
  ground truth and not the primary classifier.

## Locked Working Decisions

1. **Working decision:** V1 accepts manually uploaded screenshots and predicts
   one of six screen states.
2. **Working decision:** The label taxonomy is:
   `normal_gameplay`, `bank_open`, `dialogue_or_choice_open`, `shop_open`,
   `grand_exchange_open`, and `unknown_other`.
3. **Working decision:** Screenshots come from manually triggered captures of
   owner gameplay and must be reviewed and privacy-checked before dataset
   entry. The owner reviewed `img_0001` through `img_0006`, confirmed their
   labels and crops, approved the remaining visible in-game content for the
   private local dataset, and included all six `session_001` examples. The
   owner separately gave the same confirmations and inclusion approval for
   `img_0007` through `img_0012` in `session_002`, and for `img_0013` through
   `img_0018` in `session_003`. All 18 examples are included with
   `redaction_checked=true` and `split=unassigned`. They remain historical
   protocol-validation evidence and are not part of the new model dataset.
   The owner confirmed all 60 `session_004` labels, passed all 60 images
   through privacy review, and accepted all 60. The owner then approved 54
   final source groups: five non-singleton groups and 49 singleton groups. The
   accepted manifests retain `split=unassigned`; a separate deterministic
   grouped split contains 36/12/12 rows overall and 6/2/2 per class.
4. **Working decision:** Adjacent, burst-captured, or near-identical images
   share a group and cannot cross data splits.
5. **Working decision:** The first controlled baseline uses deterministic
   96x64 letterboxed RGB pixels because compatible pretrained vision weights
   were not available offline. It compares train-only Logistic Regression and
   Linear SVM pipelines on validation and records nearest-template cosine
   similarity as a non-selectable comparison. Compatible offline pretrained
   embeddings remain preferred future work when weights are provisioned. The
   selected Logistic Regression `C=0.01` pipeline was refitted once on all 48
   train+validation images, eight per class, and the 12-image test set was
   evaluated exactly once with no post-test fitting or tuning. Exact results
   remain private under `artifacts/model_baseline_v1/final_test/`.
6. **Working decision:** A CNN trained from scratch is not part of v1.
7. **Working decision:** Evaluation reports accuracy; macro and per-class
   precision, recall, and F1; a confusion matrix; `unknown_other` behavior; and
   variation slices where data permits.
8. **Working decision:** V1 uses Neety's normal RuneLite setup as one fixed
   capture profile: RuneLite (`client_type=runelite`) maximized in windowed
   mode on a 2560x1440 monitor
   (`display_mode=maximized_windowed`), with the Windows taskbar visible below
   the client. A small manually triggered Tkinter helper captures the complete
   primary monitor without cropping, resizing, or normalization, producing
   exact 2560x1440 PNG source files. The game uses Resizable - Classic,
   RuneLite Stretched Mode at 90 percent, Increased Performance enabled,
   integer scaling disabled, and keep-aspect-ratio enabled
   (`ui_layout_or_scale=resizable_classic_stretched_90pct`). The earlier
   2558x1438 calibration was superseded before dataset acceptance; no image was
   accepted under it. Later preprocessing uses RGB. The saved RuneLite Game
   size value 765x503 is not the active maximized capture resolution.
9. **Working decision:** `unknown_other` represents a prominent unmodeled screen
   state such as settings, world map, quest journal, clue interface, death, or
   login. Ordinary gameplay with inventory visible remains
   `normal_gameplay`.
10. **Working decision:** If multiple modeled interfaces are visible, label the
    dominant task-blocking interface. Use `unknown_other` when no modeled state
    is clearly dominant or the valid capture is genuinely ambiguous. Exclude
    protocol-invalid captures instead of using image quality as a label.
11. **Working decision:** Raw and private screenshots remain local and ignored
    by Git. No screenshots are committed by default; tracking any reviewed,
    redacted subset requires a separate explicit owner decision.
12. **Working decision:** Capture is manual only. Automatic, periodic,
    background, or continuous capture is prohibited in v1.
13. **Working decision:** Every reviewed image uses the calibrated,
    deterministic game-canvas crop (`crop_policy=fixed_game_canvas_v1`) with
    half-open coordinates `(0, 23, 2287, 1392)` (`left=0`, `top=23`,
    `right=2287`, `bottom=1392`). The identical crop is applied to every v1
    image; per-image crop adjustment is prohibited. It produces a 2287x1369
    reviewed image with aspect ratio 2287:1369 (decimal 1.670562454). It
    preserves the central game viewport, chat box and tabs, minimap, inventory
    and game-tab area, and interfaces needed for the targeted states while
    excluding RuneLite's title area and account/display name, plugin sidebar
    and narrow sidebar icon strip, Windows taskbar, and surrounding desktop.
    Source validation requires exact 2560x1440 files. Collection must stop for
    recalibration if window geometry, sidebar width, or the display setup
    changes.
14. **Working decision:** The plugin sidebar is normally open but is always
    outside the reviewed crop and is not a dataset feature. Keep the v1
    in-canvas overlay configuration fixed. Stable normal overlays may remain,
    but task-specific or interface-changing overlays that directly reveal a
    target label are disabled. Use `overlay_notes=fixed_profile_v1` for the
    stable visible profile or `none` when applicable; record only a concise,
    nonprivate exception when an unexpected overlay causes review or exclusion.

## Dataset And Evaluation Contract

The manifest template is maintained at
`research/cs4710_osrs_screen_recognition/data/osrs_screen_manifest_template.csv`.
It records capture-session and common-source grouping, the fixed capture
profile, safe content variation, overlays, privacy review, inclusion status,
consent, and split.

Collection has two distinct stages:

1. A protocol-validation mini-pilot contains exactly 18 images: three per
   class across three capture sessions, with one example of every label in
   every session. It validates capture, labeling, privacy review, and manifest
   conventions and is not sufficient for final model evaluation.
2. The completed review gate produced a separate minimum feasibility dataset:
   exactly 60 accepted `session_004` images, 10 per class. It does not
   incorporate the 18 historical images. Label, privacy, inclusion,
   source-group, and leakage review are complete, and the grouped split is
   assigned in a separate manifest.

All three mini-pilot sessions are collected. `session_001` contains one
private owner-captured example of each label: six raw PNGs were validated at
2560x1440, and the common v1 crop produced reviewed images `img_0001` through
`img_0006` at 2287x1369. The owner confirmed their labels and crops and
approved the remaining visible in-game content. All six final-manifest rows
are `included` with `redaction_checked=true` and `split=unassigned`; the draft,
audit, contact sheet, final manifest, and owner decision remain private.

`session_002` adds one more owner-captured example per class. Its six raw PNGs
were validated under the same source/crop contract and produced private
reviewed images `img_0007` through `img_0012`. The owner confirmed all six
labels and crops, confirmed that the class-defining interfaces are complete,
and approved the remaining visible in-game content. All six final rows are
`included` with `redaction_checked=true` and `split=unassigned`; the preserved
draft, technical audit, original review contact sheet, final manifest, and
owner-review record remain private.

`session_003` adds the third owner-captured example per class. Its six raw PNGs
were validated under the unchanged source/crop contract and produced private
reviewed images `img_0013` through `img_0018`, a draft manifest, technical
audit, and owner-review contact sheet. The owner confirmed all six labels,
crops, and complete class-defining interfaces and approved the remaining
visible in-game content. All six final rows are `included` with
`redaction_checked=true` and `split=unassigned`; the accepted manifest and
safe owner-review provenance record remain private alongside the preserved
pre-approval artifacts.

The canonical private `protocol_validation_18.csv` combines the three accepted
sessions in order. Its private machine-readable protocol audit confirms 18
included and privacy-checked rows, three examples per label, six rows per
session, exactly one example per label per session, contiguous opaque IDs and
source groups, valid fixed-crop reviewed images, valid owner-review records,
and zero exact source or reviewed duplicates. Deterministic protocol validation
passed. All 18 splits remain `unassigned`. No model training, feature
extraction, evaluation, live monitoring, gameplay control, multi-game
implementation, or Gamma runtime integration has occurred.

The technically validated `session_004` archive contains 60 standardized
chat-open images, exactly 10 per class. Verified selection-manifest row order
maps them to `img_0019` through `img_0078`. The owner confirmed every label,
passed every image through privacy review, and accepted all 60. Private
`session_004.csv` and `chat_open_feasibility_60.csv` contain the same accepted
records and exclude the historical 18-image protocol set. All 60 share the
single true `capture_session_id=session_004`; fake sub-sessions are prohibited.
The owner-approved proposal maps mechanically to 54 nonblank
`session_004_group_####` IDs in both still-unsplit accepted manifests. The
separate seed-4710 split assigns 36 train, 12 validation, and 12 test rows,
exactly 6/2/2 per class. All non-singletons are in training, validation and
test contain only singletons, and no group crosses splits. Since every row is
from `session_004`, the result is source-group-aware, not
capture-session-held-out evaluation. Train/validation feature extraction and
model selection are complete without notebook execution. The frozen Logistic
Regression `C=0.01` pipeline was refitted once on the 48 train+validation
images, eight per class, and evaluated exactly once on the 12-image test set.
No post-test fitting, tuning, or model change occurred.

A class should not be declared usable merely because it has many adjacent
frames. During capture and review, `split=unassigned`. Splits are assigned only
after the dataset audit. Complete capture sessions remain grouped where
practical, and each common-origin or near-duplicate group receives one
`source_group_id` that cannot cross train, validation, and test.

Only rows with `inclusion_status=included`, `redaction_checked=true`,
`source_consent=owner_captured`, and one of the six approved labels may enter
training or evaluation. `exclusion_reason` is empty for included images and
contains a concise reason for excluded images.

Evaluation must include:

- macro and per-class precision, recall, and F1;
- confusion matrix;
- explicit review of false acceptance/rejection for `unknown_other`;
- capture-session and safe visual-content variation slices when sample counts
  are meaningful;
- a final untouched test set that was not used for prompt, template, feature,
  model, or threshold selection.

## Protocol-Validation Result And Next Gate

The 18-image mini-pilot passed every applicable deterministic gate. It
validates the manual capture procedure, fixed source geometry, fixed crop,
six-label usage, balanced session design, opaque identifiers, manifest schema,
intake audit generation, cross-session uniqueness checks, privacy-review gate,
owner-approval provenance, accepted session manifests, and canonical-manifest
generation.

This result validates the dataset protocol, not classifier accuracy,
generalization, broad OSRS coverage, cross-display robustness, gameplay
understanding, multi-game capability, or runtime readiness. The earlier plan
for 42 additions across sessions 004-010 is superseded by the separate accepted
60-image `session_004` set. Source-group review and grouped split preparation
are complete. The canonical accepted manifest remains unassigned, and the
separate split cannot support capture-session-held-out claims because every row
shares `session_004`.

## Phases

1. Review taxonomy, capture protocol, dataset card, and manifest.
2. **Complete:** Owner inclusion/privacy review and deterministic validation of
   the accepted 18-image protocol mini-pilot.
3. **Complete:** Technical draft intake of the balanced 60-image
   `session_004` candidate set.
4. **Complete:** Owner-approved source-group finalization and deterministic
   grouped split preparation.
5. **Complete:** Run the environment-constrained 96x64 letterboxed RGB
   train/validation baseline selection with Logistic Regression, Linear SVM,
   and a nearest-template comparison.
6. **Future:** Extract compatible pretrained image embeddings and compare
   linear classifiers if offline weights are deliberately provisioned.
7. **Complete:** Refit the frozen Logistic Regression `C=0.01` pipeline once
   on train+validation and evaluate the grouped untouched test set exactly
   once, with no post-test fitting or tuning.
8. Only after evaluation, design an optional Gamma presentation/integration
   boundary in a separately approved runtime task.

## Non-Goals

- **Future work / intentionally out of scope:** Real-time capture, automatic
  screenshot collection, game automation, gameplay control, object detection,
  bounding boxes, item recognition, NPC identification, world-coordinate
  recognition, and a CNN trained from scratch.
- **Future work / intentionally out of scope:** Private screenshots, intake
  artifacts, and baseline outputs remain ignored; no live endpoints, runtime
  dependencies, or dashboard fixes are added by the offline baseline work.
- **Current limited abstraction:** Three collected sessions demonstrate explicit
  capture-session plans, a deterministic crop profile, opaque IDs, manifest
  provenance, and a human approval gate. These remain OSRS-specific and do not
  establish game-independent profile registration.
- **Future boundary:** A later authorized live perception system could replace
  manual screenshots with a frame source and emit structured observations with
  confidence. A perception model must never directly click or press keys.
  Gameplay action remains a separate layer requiring explicit authorization,
  game-specific adapters, bounded actions, rate limits, validation, an
  emergency stop, and independent tests. Do not extract generic multi-game
  interfaces until a second real game provides evidence for them.

## Decision Log

- 2026-06-29: Locked uploaded-screenshot classification as v1.
- 2026-06-29: Locked the six-class screen-state taxonomy.
- 2026-06-29: Required owner-generated, manually redacted screenshots and
  capture-session grouping.
- 2026-06-29: Selected generic Gamma vision and ROI/template comparisons, with
  pretrained embeddings plus a linear classifier as the primary candidate.
- 2026-06-29: Deferred runtime/browser repair and all game automation.
- 2026-06-29: Limited v1 claims to one primary configuration and required exact
  configuration metadata.
- 2026-06-29: Defined `unknown_other` as a prominent unmodeled/ambiguous state
  and chose the dominant task-blocking interface for multi-interface screens.
- 2026-06-29: Kept raw screenshots local and ignored pending an explicit review
  decision for any redacted tracked subset.
- 2026-07-14: Fixed v1 to manually captured PNGs from Neety's maximized
  windowed RuneLite setup on a 2560x1440 monitor, using Resizable - Classic and
  Stretched Mode at 90 percent.
- 2026-07-14: Required one-time calibration and freezing of a deterministic
  complete-game-canvas crop.
- 2026-07-14: Added the 18-image protocol-validation gate, the subsequent
  60-120-image feasibility pilot, inclusion review, and source-group leakage
  controls.
- 2026-07-15: Froze the 2558x1438 source-capture geometry, half-open crop
  `(0, 23, 2287, 1392)`, and 2287x1369 reviewed-image geometry with aspect
  ratio 2287:1369 for `fixed_game_canvas_v1`.
- 2026-07-20: Recalibrated the source contract to the manual helper's actual
  full-monitor 2560x1440 PNG output before any dataset image was accepted.
  Native-pixel inspection of all six `session_001` sources confirmed the same
  common half-open game-canvas crop `(0, 23, 2287, 1392)` and 2287x1369 output,
  so the policy remains `fixed_game_canvas_v1`.
- 2026-07-20: Generated the private `session_001` reviewed drafts, draft
  manifest, technical audit, and review contact sheet. All six examples remain
  pending human privacy and inclusion review; no training or evaluation has
  occurred.
- 2026-07-20: The owner reviewed `img_0001` through `img_0006`, confirmed every
  label and crop, approved the remaining visible in-game content for the
  private local dataset, and included all six with `redaction_checked=true` and
  `split=unassigned`. The pre-approval artifacts were preserved, and separate
  private final-manifest and owner-review records were created. No training or
  evaluation occurred.
- 2026-07-20: Validated six `session_002` sources under the unchanged crop,
  generalized the intake utility around explicit session plans, and generated
  private reviewed images `img_0007` through `img_0012`, a draft manifest,
  technical audit, and review contact sheet. At intake completion, all six were
  pending owner inclusion/privacy review and no final manifest or owner-review
  record existed.
- 2026-07-20: The owner reviewed `img_0007` through `img_0012`, confirmed all
  labels, crops, and complete class-defining content, approved the remaining
  visible in-game content, and included all six with
  `redaction_checked=true` and `split=unassigned`. The pre-approval artifacts
  were preserved, and separate private final-manifest and owner-review records
  were created. No training or evaluation occurred.
- 2026-07-20: Validated all six `session_003` sources under the unchanged crop,
  generated private reviewed images `img_0013` through `img_0018`, a pending
  draft manifest, technical audit, and review contact sheet, and completed the
  deterministic 18-image protocol audit. Session 003 remains pending owner
  inclusion/privacy review; no split or model work occurred.
- 2026-07-20: The owner approved all six `session_003` labels, crops,
  class-defining content, and remaining visible in-game content. Created the
  separate private accepted manifest and safe owner-review record while
  preserving the intake artifacts.
- 2026-07-20: Revalidated all three accepted sessions and created a private
  canonical 18-row manifest plus protocol audit. The balanced three-session
  dataset protocol passed with no exact source or reviewed duplicates. Adopted
  60 accepted images, 10 per class, as the minimum feasibility target, to be
  reached through balanced sessions 004-010 before split or model work.
- 2026-07-24: Superseded the sessions 004-010 expansion plan with a separate
  60-image standardized chat-open `session_004` candidate set. Technical draft
  intake created `img_0019` through `img_0078`, exactly 10 candidates per
  class, with all rows pending label/privacy and grouping/leakage review. No
  accepted manifest, split, feature, or model work was created.
- 2026-07-24: The owner confirmed all 60 session 004 labels, passed all 60
  images through privacy review, and accepted all 60. Created the private
  accepted session and canonical 60-image manifests with blank source groups
  and unassigned splits, plus a separate algorithmic grouping proposal pending
  owner review. No feature extraction, training, or evaluation began.
- 2026-07-24: The owner approved all five proposed non-singleton source groups
  and every remaining singleton proposal. Finalized 54 source groups and
  created a deterministic seed-4710 grouped split with 36/12/12 rows overall
  and 6/2/2 per class. Every non-singleton group is in training, validation and
  test contain only singletons, and no group crosses splits. All data still
  comes from `session_004`, so this is not capture-session-held-out evaluation.
  No feature extraction, notebook execution, model training, or evaluation
  began.
- 2026-07-24: Completed the train/validation-only controlled baseline using
  deterministic 96x64 letterboxed RGB features. Compared train-only
  `StandardScaler` pipelines for Logistic Regression and Linear SVM on
  validation and recorded nearest-template cosine results as a non-selectable
  comparison. The reviewed learned configuration remains fitted on training
  only. No test image was decoded or evaluated, and final test evaluation
  remains pending.
- 2026-07-24: Froze Logistic Regression `C=0.01`, refitted it once on all 48
  train+validation images (eight per class), and evaluated the 12-image test
  set exactly once. No post-test fitting, tuning, or model change occurred.
  Exact results remain private. The evidence is limited to two test examples
  per class from one capture session and one fixed layout and configuration;
  it is source-group-aware, not capture-session-held-out or deployment-level.

## Open Questions

- **Open question:** What minimum per-class F1 and unknown-rejection behavior
  will count as a successful class-project result?
