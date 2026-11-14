# Dataset Card: OSRS Screen-State Recognition

Status: Frozen baseline selected; one-time final test evaluation complete

## Purpose

The planned dataset will support six-class classification of manually uploaded
Old School RuneScape screenshots. It will evaluate whether a limited,
controlled classifier can tolerate safe visual-content variation within one
fixed capture profile while recognizing a small set of interface-driven states.

## Labels

- `normal_gameplay`
- `bank_open`
- `dialogue_or_choice_open`
- `shop_open`
- `grand_exchange_open`
- `unknown_other`

The detailed definitions are maintained in `label_taxonomy.md`.

## Source And Consent

Screenshots are manually captured from gameplay owned or controlled by the
project owner. Every included image must have `source_consent=owner_captured`.
Downloaded or third-party screenshots are not part of the initial dataset.

V1 uses Neety's normal RuneLite setup, maximized in windowed mode on a
2560x1440 primary monitor, with custom window chrome, the Windows taskbar
visible below the client, and the plugin sidebar normally open. A small
manually triggered Tkinter helper captures the complete primary monitor without
cropping, resizing, or normalization. Source-capture files are therefore exact
2560x1440 PNGs. The game uses Resizable - Classic and Stretched Mode at 90
percent, with Increased Performance enabled, integer scaling disabled, and
keep aspect ratio enabled. The earlier 2558x1438 calibration was superseded
before dataset acceptance; no image was accepted under it. The saved RuneLite
Game size value 765x503 is not the active maximized capture resolution.
Evaluation does not establish support for any untested configuration.

The deterministic `fixed_game_canvas_v1` crop uses half-open coordinates
`(0, 23, 2287, 1392)` for every image, with no per-image adjustment. It produces
a 2287x1369 reviewed image with aspect ratio 2287:1369 (decimal 1.670562454).
The crop preserves the complete required game viewport and interface regions
but excludes the title area and account/display name, plugin sidebar and narrow
icon strip, Windows taskbar, and surrounding desktop. Collection must stop for
recalibration if source or window geometry, sidebar width, display setup, or
layout changes. Later preprocessing uses RGB.

The sidebar is not a dataset feature and its private contents are not recorded.
Stable normal in-canvas overlays may remain under one fixed plugin profile, but
label-revealing overlays are disabled and unexpected material obstruction is
grounds for exclusion. `overlay_notes` is `fixed_profile_v1`, `none` where
applicable, or a concise nonprivate review/exclusion exception.

The calibration image is not a dataset record. `session_001` contains six
private 2560x1440 raw screenshots and reviewed images `img_0001` through
`img_0006`, one per approved label. The owner confirmed every label and crop
and approved the remaining visible in-game content; all six final-manifest
rows are `inclusion_status=included` and `redaction_checked=true`.

`session_002` adds six validated private 2560x1440 raw screenshots and reviewed
images `img_0007` through `img_0012`, again one per approved label. The owner
confirmed all six labels and crops, confirmed complete class-defining content,
and approved the remaining visible in-game content. Its six final rows are
`inclusion_status=included`, `redaction_checked=true`, and
`split=unassigned`; session 002 is complete.

`session_003` adds six validated private 2560x1440 raw screenshots and reviewed
images `img_0013` through `img_0018`, one per label. The frozen crop remains
valid, the intended label variation is present, and exact source/reviewed
duplicates were not found across the 18-image collection. Its draft manifest,
technical audit, and review contact sheet remain private and unchanged. The
owner confirmed the labels, crops, and complete class-defining content and
approved the remaining visible in-game content. Its separate accepted manifest
and owner-review record contain six `included`, `redaction_checked=true`,
`split=unassigned` rows.

The mini-pilot is therefore 18 of 18 collected, owner-reviewed, and included.
Every class has exactly three accepted examples, and each session contributes
exactly one example per class. The canonical private manifest and protocol
audit confirm unique IDs, reviewed paths, source groups, source hashes, and
reviewed hashes; no exact source or reviewed duplicate was found. All 18 splits
remain `unassigned`, and deterministic protocol validation passed. No model
training, feature extraction, evaluation, Gamma runtime integration, live
monitoring, gameplay control, or multi-game implementation has occurred.

That 18-image set remains historical protocol-validation evidence and is
excluded from the new model dataset. The separate `session_004` archive has 60
standardized chat-open images, exactly 10 per class, with reviewed IDs
`img_0019` through `img_0078`. Technical intake found no duplicate source
bytes, decoded source pixels, reviewed bytes, or reviewed pixels. The owner
confirmed every label, passed every image through privacy review, and accepted
all 60. Private `session_004.csv` and `chat_open_feasibility_60.csv` preserve
that accepted state with all splits unassigned and 54 owner-approved source
groups. Five groups are non-singletons and the remaining 49 are singletons.
The separate deterministic split has 36/12/12 rows overall and 6/2/2 per
class. All non-singletons are in training, validation and test use only
singletons, and no source group crosses splits.

Raw screenshots, reviewed images, draft and final session manifests, audit JSON,
and review records/contact sheets remain local, private, and ignored by Git. No
screenshots are committed by default. A specifically reviewed and redacted
subset may be tracked later only after an explicit owner decision.

## Privacy

Prefer images with no private or account-identifying information. Every final
dataset image must be manually reviewed before entry.
The title area, Windows taskbar, plugin sidebar, account names, chat identities,
private messages, login information, unrelated desktop content, and identifying
plugin data are prohibited in reviewed images. Prefer captures without private
chat, clan identities, account-identifying content, or private notifications
inside the game canvas.

Redaction must not erase or add class-defining UI evidence or create a
label-specific shortcut. Use one consistent solid opaque mask for nonessential
private areas, not blur. Images that cannot be safely redacted without changing
the task are excluded and documented.

## Collection Stages And Inclusion

The protocol-validation mini-pilot is 18 images: three per class across three
sessions, with every label represented once per session. It validates capture,
labeling, privacy review, and manifest conventions and is not sufficient for
final evaluation. The completed pilot validates manual collection, fixed source
geometry and cropping, label clarity, balanced session design, opaque IDs,
manifest and audit structure, cross-session uniqueness, privacy review, owner
approval provenance, and accepted/canonical manifest generation. It does not
validate classifier performance, broad OSRS coverage, cross-display
robustness, multi-game capability, automated gameplay, or runtime readiness.

The earlier “18 accepted plus 42 additional across sessions 004-010” plan is
superseded. The new feasibility dataset is the separate 60-image `session_004`
set. All 60 share that one capture-session identity; sub-sessions must not be
invented. Owner label/privacy review and acceptance are complete, and the
accepted dataset has exactly 10 images per class. Every split remains
`unassigned` in the canonical acceptance record, with all 54 final source
groups recorded. The owner-approved proposal was converted mechanically to
final `session_004_group_####` IDs. A separate seed-4710 grouped split assigns
36/12/12 rows overall and 6/2/2 per class without crossing groups. Since every
image comes from `session_004`, this is source-group-aware rather than
capture-session-held-out evaluation; that limitation must be discussed in the
final report. The train/validation-only baseline-selection pass is complete.
The frozen Logistic Regression `C=0.01` pipeline was refitted once on the 48
train+validation images, eight per class, and evaluated exactly once on the
12-image test set. No post-test fitting, tuning, or model change occurred.

An image is eligible for training or evaluation only when
`inclusion_status=included`, `redaction_checked=true`,
`source_consent=owner_captured`, and its label is approved. Excluded images
have a concise `exclusion_reason`; included images leave it empty.

## Frozen Feasibility Baseline

The environment-constrained v1 preprocessing uses the entire 2287x1369
reviewed RGB PNG. It preserves aspect ratio, fits the image completely inside a
96x64 RGB canvas with Pillow LANCZOS interpolation, centers it on fixed black
padding, scales channels to float32 `[0.0, 1.0]`, and flattens row-major HWC RGB
values to 18,432 features. It does not crop, distort, or augment images.

Every candidate is a scikit-learn pipeline whose `StandardScaler` and
classifier are fitted on the 36 training rows only. Logistic Regression and
Linear SVM candidates are selected using the 12 validation rows only; a
nearest-template cosine classifier using the same raw features is a
non-selectable comparison. Selection reports accuracy, macro and per-class
precision, recall, and F1, a fixed-order confusion matrix, and explicit
`unknown_other` false acceptance and rejection behavior. Detailed metrics and
model artifacts remain private and ignored.

The selected Logistic Regression `C=0.01` pipeline was refitted once on the 48
train+validation images, exactly eight per class. The 12-image test set was
then evaluated exactly once, and no post-test fitting or tuning occurred.
Exact results remain private under `artifacts/model_baseline_v1/final_test/`.
With only two test examples per class, and with every image coming from the
single `session_004` capture profile and one fixed layout and configuration,
this source-group-aware evaluation is not capture-session-held-out. It is
session-specific feasibility evidence rather than cross-session, cross-client,
or deployment generalization. Compatible offline pretrained embeddings remain
preferred future work when weights become available.

## Intended Use

- class-project dataset exploration;
- constrained Gamma multimodal-prompt baseline;
- ROI/template-similarity baseline where stable UI anchors permit it;
- pretrained image embeddings with Logistic Regression or Linear SVM;
- grouped offline evaluation and variation analysis.

## Prohibited Use

- account identification or player tracking;
- gameplay automation or control;
- item, NPC, or world-coordinate recognition;
- automatic screenshot collection;
- claims about all OSRS clients, layouts, plugins, or game states;
- runtime Gamma integration before offline evaluation and separate approval.

## Variation And Bias Risks

- The owner's fixed RuneLite configuration and play habits may dominate the
  dataset.
- Some classes have large central interfaces and may be easier than
  `normal_gameplay` or `unknown_other`.
- Adjacent-frame collection can create severe train/test leakage.
- Redaction patterns can become accidental label cues.
- `unknown_other` can become an incoherent class or an image-quality shortcut;
  it should contain prominent unmodeled interfaces rather than ordinary
  gameplay with inventory visible.
- Rare UI variants may have too few examples for meaningful per-slice metrics.
- A roughly balanced pilot will not represent real gameplay prevalence and
  should not be interpreted as calibrated deployment performance.

## Quality And Leakage Checks

Before training:

1. Validate manifest fields and allowed labels.
2. Verify affirmative consent and privacy review for every image.
3. Check exact file hashes and perceptual near duplicates.
4. Review `capture_session_id` and `source_group_id` grouping. Common-origin,
   adjacent-frame, repeated-source, crop, or edited-derivative groups cannot
   cross train, validation, and test.
5. Report class counts and counts by session and safe content variation.
6. Inspect representative images and ambiguous cases per class.
7. Keep splits `unassigned` through capture and review; assign grouped splits
   only after these checks, keeping complete sessions together where practical.
8. Keep the final test set untouched during prompt, template, embedding, model,
   hyperparameter, and threshold selection.

## Evaluation Expectations

Report accuracy; macro and per-class precision, recall, and F1; confusion
matrix; `unknown_other` errors; and meaningful slices by capture session and
safe visual-content variation. Do not manufacture results for underpopulated
slices.

Validation-selection details are private. No final test result is currently
available.
