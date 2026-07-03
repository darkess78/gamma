# CS 4710: Gamma-Assisted OSRS Screen Recognition

Status: Offline planning; no screenshot dataset collected
Last verified: 2026-06-29

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
3. **Working decision:** Screenshots will come from owner gameplay later and
   must be manually reviewed and redacted before dataset entry.
4. **Working decision:** Adjacent, burst-captured, or near-identical images
   share a group and cannot cross data splits.
5. **Working decision:** Compare a constrained Gamma multimodal prompt, an
   ROI/template-similarity baseline where appropriate, and pretrained image
   embeddings with Logistic Regression or Linear SVM.
6. **Working decision:** A CNN trained from scratch is not part of v1.
7. **Working decision:** Evaluation reports macro and per-class precision,
   recall, and F1; a confusion matrix; `unknown_other` behavior; and variation
   slices where data permits.
8. **Working decision:** V1 supports one owner-selected primary client and
   layout only. Record the actual client, layout, scale, resolution, and plugin
   metadata; do not claim generalization to untested configurations.
9. **Working decision:** `unknown_other` represents a prominent unmodeled screen
   state such as settings, world map, quest journal, clue interface, death, or
   login. Ordinary gameplay with inventory visible remains
   `normal_gameplay`.
10. **Working decision:** If multiple modeled interfaces are visible, label the
    dominant task-blocking interface. Use `unknown_other` when no modeled state
    is clearly dominant or the screen is too modified or ambiguous.
11. **Working decision:** Raw screenshots remain local and ignored by Git. Only
    a specifically reviewed and redacted subset may be tracked later after an
    explicit owner decision.

## Dataset And Evaluation Contract

The manifest template is maintained at
`research/cs4710_osrs_screen_recognition/data/osrs_screen_manifest_template.csv`.
It records capture-session grouping, resolution, client/layout details,
camera/zoom/location variation, overlays, privacy review, consent, and split.

The pilot target is 10-20 reviewed screenshots per class across at least three
capture sessions. A class should not be declared usable merely because it has
many adjacent frames. Splits are assigned only after exact/near-duplicate and
capture-session grouping.

Evaluation must include:

- macro and per-class precision, recall, and F1;
- confusion matrix;
- explicit review of false acceptance/rejection for `unknown_other`;
- capture-session, resolution, UI-layout, and variation slices when sample
  counts are meaningful;
- a final untouched test set that was not used for prompt, template, feature,
  model, or threshold selection.

## Phases

1. **Current:** Review taxonomy, capture protocol, dataset card, and manifest.
2. Collect and redact a small owner-generated pilot across three or more
   sessions.
3. Audit class counts, variation coverage, exact duplicates, and near
   duplicates.
4. Run the constrained Gamma vision baseline and ROI/template baseline offline.
5. Extract pretrained image embeddings and train linear classifiers offline.
6. Evaluate on a grouped untouched test set and document limitations.
7. Only after evaluation, design an optional Gamma presentation/integration
   boundary in a separately approved runtime task.

## Non-Goals

- **Future work / intentionally out of scope:** Real-time capture, automatic
  screenshot collection, game automation, gameplay control, object detection,
  bounding boxes, item recognition, NPC identification, world-coordinate
  recognition, and a CNN trained from scratch.
- **Future work / intentionally out of scope:** No screenshots, fake images,
  model artifacts, live endpoints, runtime dependencies, or dashboard fixes are
  added during the scaffold phase.

## Decision Log

- 2026-06-29: Locked uploaded-screenshot classification as v1.
- 2026-06-29: Locked the six-class screen-state taxonomy.
- 2026-06-29: Required owner-generated, manually redacted screenshots and
  capture-session grouping.
- 2026-06-29: Selected generic Gamma vision and ROI/template comparisons, with
  pretrained embeddings plus a linear classifier as the primary candidate.
- 2026-06-29: Deferred runtime/browser repair and all game automation.
- 2026-06-29: Limited v1 claims to one owner-selected primary client/layout and
  required exact configuration metadata.
- 2026-06-29: Defined `unknown_other` as a prominent unmodeled/ambiguous state
  and chose the dominant task-blocking interface for multi-interface screens.
- 2026-06-29: Kept raw screenshots local and ignored pending an explicit review
  decision for any redacted tracked subset.

## Open Questions

- **Open question:** Which exact primary client, layout, UI scale, resolution,
  and plugin configuration will define the v1 collection boundary?
- **Open question:** What exact visual boundary separates `shop_open` from
  `grand_exchange_open` in ambiguous or plugin-modified layouts?
- **Open question:** What minimum per-class F1 and unknown-rejection behavior
  will count as a successful class-project result?
