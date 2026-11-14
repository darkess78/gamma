# OSRS Screenshot Capture Protocol

Status: Historical protocol preserved; session 004 grouped split prepared

## Ownership And Consent

- Capture only screenshots from gameplay the owner is authorized to record and
  use for the class project.
- Set `source_consent=owner_captured` only after confirming ownership and
  permission.
- Do not download screenshots from search results, community sites, videos, or
  other players for the initial dataset.
- Use manual screenshots only. Do not use automatic, periodic, background, or
  continuous capture.

## Fixed V1 Capture Profile

Use this profile for every v1 image:

- client: RuneLite (`client_type=runelite`);
- source environment: Neety's normal setup, a 2560x1440 primary monitor with
  RuneLite maximized in windowed mode (`display_mode=maximized_windowed`),
  custom window chrome enabled, the Windows taskbar visible below the client,
  and the plugin sidebar normally open on the right;
- capture method: a small manually triggered Tkinter helper captures the
  complete primary monitor only after the owner checks the RuneLite state; it
  does not crop, resize, or normalize the image;
- source-capture file geometry: exactly 2560x1440 for the calibrated v1 setup;
- game layout and scaling: Resizable - Classic with Stretched Mode enabled at
  90 percent, Increased Performance enabled, integer scaling disabled, and
  keep aspect ratio enabled
  (`ui_layout_or_scale=resizable_classic_stretched_90pct`);
- reviewed capture area: the complete OSRS game canvas under the frozen
  deterministic crop (`crop_policy=fixed_game_canvas_v1`);
- image format: PNG; later preprocessing color mode: RGB.

Future v1 source captures must match 2560x1440 unless the project deliberately
recalibrates. The earlier 2558x1438 source calibration was superseded before
dataset acceptance, and no image was accepted under it. The saved RuneLite
Game size value 765x503 is not the active maximized capture resolution and must
not be used as the source or reviewed-image resolution.
The previously uploaded example screenshot was supplied only to establish the
normal setup, may have been resized during upload, and must not become a
dataset record or a source for crop measurements.

## Frozen Crop Calibration

The approved `fixed_game_canvas_v1` calibration is:

- half-open crop rectangle `(0, 23, 2287, 1392)`;
- `left=0`, `top=23`, `right=2287`, `bottom=1392`;
- reviewed-image resolution `2287x1369`;
- reviewed-image aspect ratio `2287:1369` (decimal `1.670562454`).

Apply these exact coordinates identically to every v1 source image. No manual,
arbitrary, or per-image crop adjustment is allowed. The crop excludes the
RuneLite title area and any account/display name in it, plugin sidebar, narrow
vertical sidebar icon strip, Windows taskbar, and surrounding desktop. It
preserves the central game viewport, chat box and tabs, minimap, inventory and
game-tab area, and complete class-defining bank, dialogue/choice, shop, and
Grand Exchange interfaces.

Stop collection and deliberately recalibrate before continuing if the source
file is not 2560x1440 or if window geometry, monitor scaling, sidebar width,
display setup, or layout changes. In the manifest, `resolution=2287x1369` and
`aspect_ratio=2287:1369` describe the final reviewed image after the fixed
crop, not the 2560x1440 source-file geometry.

The calibration image established the initial measurements only and is not a
dataset record. Native-pixel inspection of all six authorized `session_001`
sources confirmed one common title/sidebar/taskbar geometry and the same crop
boundaries after the source contract changed to complete-monitor capture.

`session_001` is complete: six 2560x1440 raw screenshots were manually
collected and produced six 2287x1369 private reviewed PNGs. The owner reviewed
`img_0001` through `img_0006`, confirmed every label and crop, and approved the
remaining visible in-game content for the private local dataset without
redaction or recapture. All six final-manifest rows are `included` with
`redaction_checked=true` and `split=unassigned`. The draft manifest, technical
audit JSON, and original review contact sheet remain unchanged as the
pre-approval record; the accepted manifest and owner-review decision are stored
separately. Session 001 is one of three sessions in the 18-image
protocol-validation mini-pilot. No training or evaluation has occurred.

The plugin sidebar is outside the crop: its contents are not dataset features
and must not be inventoried or recorded. In-game overlays rendered inside the
game canvas are different. Keep the v1 plugin configuration fixed across every
session. Stable normal overlays may remain, but disable task-specific or
interface-changing overlays that directly reveal a target label or appear only
for one class as a shortcut. Exclude an image if an unexpected overlay
materially obstructs or changes the target interface. Use
`overlay_notes=fixed_profile_v1` for the stable visible profile or `none` where
applicable; otherwise add only a concise nonprivate exception explaining
review or exclusion. Do not record plugin names, account data, private
notification text, or sidebar contents unless a specific nonprivate plugin
name is genuinely required to explain an exclusion.

## Privacy Review Before Dataset Entry

Review every screenshot before copying it into a future dataset directory.
Prefer captures with no private or account-identifying information. Do not put
account names, character names, world numbers, private-chat identities, clan
identities, login details, private notifications, locations, activities,
dates, or labels in filenames, paths, image IDs, session IDs, or notes.

The RuneLite title area must never appear in a reviewed image because it may
show the account/display name. The Windows taskbar and plugin sidebar must also
never appear. Prefer captures without private chat, clan identities, account
identifiers, or private notifications inside the game canvas.

Redact or exclude:

- account names and login information;
- private messages, public chat names, friend lists, clan names, or chat text
  not required for the label;
- server/world details when they could identify the owner or session;
- plugin panels containing private identifiers or paths;
- notifications or desktop content outside the game window.

Exclude an image when redaction would cover or distort class-defining evidence.
When a nonessential private area must be redacted, use one consistent solid
opaque mask (`redaction_method=opaque_mask`), not blur. Otherwise use
`redaction_method=none`. Redaction must not create a label-specific shortcut.
Set `redaction_checked=true` only after reviewing the final image that will be
used, not merely the original capture.

## Protocol-Validation Mini-Pilot

- Capture exactly 18 images: three per class across three capture sessions.
- Include one example of every label in every session.
- Use it to validate capture, labeling, privacy review, and manifest
  conventions.
- Do not use this mini-pilot as final model evaluation data.

The mini-pilot is complete. All 18 screenshots are owner-reviewed, included,
and `redaction_checked=true`. Every class has exactly three examples, with one
example per class in each of three sessions. The canonical private manifest and
protocol audit confirm valid source/reviewed geometry, the fixed crop, unique
opaque IDs, paths, and source groups, valid approval provenance, and no exact
source or reviewed duplicates. Deterministic protocol validation passed. All 18
splits remain `unassigned`; no feature extraction, training, or evaluation has
occurred.

## Session_002 Accepted State

The second session retained the complete v1 capture profile and common frozen
crop. Six raw PNGs were validated and produced reviewed images `img_0007`
through `img_0012`, one per class. The owner confirmed all six labels and
crops, confirmed that the class-defining interfaces are complete, and approved
the remaining visible in-game content without redaction or recapture. All six
final rows are `included` with `redaction_checked=true` and
`split=unassigned`; session 002 is complete. The private draft manifest,
technical audit, and original review contact sheet remain the unchanged
pre-approval record, while the final manifest and owner-review decision record
the accepted state separately.

## Session_003 Accepted State

The third session retained the complete fixed v1 capture profile and common
crop. Six raw PNGs were validated and produced reviewed images `img_0013`
through `img_0018`, one per class. The session adds the intended third-session
variation: ordinary gameplay without a modeled interface, a bank
search/filtered state, portrait/continue dialogue, a third distinct shop, a
populated Grand Exchange sell-offer setup, and an equipment-stat interface as
`unknown_other`. The non-model audit found no exact source or reviewed
duplicates and confirmed one example per class per session.

The owner confirmed every assigned label and crop, confirmed complete
class-defining content, approved the remaining visible in-game content, and
included all six examples without redaction or recapture. Every final row is
`included`, `redaction_checked=true`, and `split=unassigned`. The private draft,
technical audit, and original review contact sheet remain unchanged; the
accepted manifest and safe owner-review provenance record the final decision
separately.

## Minimum 60-Image Feasibility Dataset

The earlier plan to combine the accepted 18-image sessions 001-003 protocol set
with 42 captures across sessions 004-010 is superseded. The historical set
remains separate protocol-validation evidence. The new model dataset is one
standardized chat-open `session_004` archive with 60 owner-captured images,
exactly 10 per class. Technical draft intake produced reviewed images
`img_0019` through `img_0078`. The owner confirmed all 60 labels, passed all 60
images through privacy review, and accepted all 60. Private `session_004.csv`
and `chat_open_feasibility_60.csv` record the accepted state.

All images share the true `capture_session_id=session_004`; no sub-sessions
were invented. The owner approved 54 final source groups: five non-singleton
groups and 49 singletons. The accepted manifests record those groups and
remain `split=unassigned`. The separate seed-4710 split contains 36 train, 12
validation, and 12 test rows, exactly 6/2/2 per class. All non-singletons are
in training, validation and test contain only singletons, and no group crosses
splits. Because all rows share `session_004`, this is not
capture-session-held-out evaluation. No feature extraction, notebook
execution, model training, or evaluation has begun.

## Variation Review For Session 004

Keep the client, display mode, source environment, layout/scaling, calibrated
crop, reviewed-image dimensions, PNG format, and in-canvas overlay profile
fixed. Review the candidate archive for safe visual-content factors:

- camera direction;
- camera zoom;
- in-game background;
- coarse location/activity category;
- interface sub-state;
- interface contents.

Do not interpret within-session content variation as independent
capture-session evidence. Across the 10 candidates for each label, review this
coverage:

| Label | Variation checklist |
| --- | --- |
| `normal_gameplay` | movement/travel; combat; gathering; production/skilling; indoor and outdoor environments; crowded and sparse scenes; varied camera directions and zoom when the buckets can be documented safely |
| `bank_open` | standard tab; alternate tabs; search results; filtered/tagged results; deposit inventory or deposit box only where the taxonomy permits; varied bank backgrounds; different visible item arrangements |
| `dialogue_or_choice_open` | NPC statement; player statement; continue prompt; selectable responses; item/object dialogue; multi-line text; portrait and non-portrait presentations |
| `shop_open` | different NPC shops; sparse and dense stock; varied world backgrounds and stock categories; varied quantities; avoid near-duplicate shop layouts where practical |
| `grand_exchange_open` | overview; buy setup; sell setup; item search; populated item selection; active offer; completed offer; collection where practical |
| `unknown_other` | distribute across distinct valid unmodeled interfaces such as settings, equipment stats, world map, quest journal, world switcher, minigame UI, deposit box, equipment comparison, or another clearly distinct interface; do not overrepresent one subtype |

The matrix guided review and is not evidence of cross-session generalization.
Label, privacy, inclusion, and source-group review are complete for all 60,
and the grouped split is prepared. Model work remains deferred.

## Capture Session And Leakage Rules

- Assign an opaque `capture_session_id`, such as `session_001`, to each session.
- Assign `source_group_id` to common-origin and near-duplicate groups. Images
  from the same interaction, adjacent frame sequence, original screenshot,
  crop, edited derivative, or repeated source must share one source group.
- Do not create derived crops or augmented copies as separate raw v1 examples.
- Adjacent frames and near-duplicates do not count as independent examples.
- Keep complete capture sessions grouped where practical. No
  `source_group_id` may cross train, validation, and test.
- Do not choose a representative from a burst for training and another frame
  from the same burst for testing.
- Keep `split=unassigned` during capture and review. Assign `train`,
  `validation`, or `test` only after exact hash, perceptual similarity, and
  manual grouping review.
- Prefer holding out complete sessions and at least one meaningful variation
  combination for final testing.
- Do not describe session variation within the selected configuration as
  evidence of support for other clients or layouts.

## File And Manifest Rules

- Use opaque image IDs and filenames, such as `img_0001` and `img_0001.png`.
  Use opaque session IDs such as `session_001`. Do not encode labels,
  activities, locations, dates, account names, or world numbers.
- Store only relative paths in the manifest.
- Record `resolution=2287x1369` and `aspect_ratio=2287:1369` for every reviewed
  image under this calibration, plus client/layout, variation buckets,
  overlays, consent, redaction, and notes. The 2560x1440 source-file geometry
  does not replace the reviewed-image values.
- Preserve the unmodified label-relevant interface after redaction.
- Set `inclusion_status` to `needs_review`, `included`, or `excluded`.
  `exclusion_reason` is empty when included and gives a concise reason when
  excluded.
- Use an image for training or evaluation only when
  `inclusion_status=included`, `redaction_checked=true`,
  `source_consent=owner_captured`, and its label is one of the six approved
  values.

## Augmentation Cautions

Future training-only augmentation may be considered after raw examples have
been audited and split. Potentially reasonable transformations include:

- mild brightness/contrast/color changes;
- modest resize and letterbox variation;
- small translations that retain the full label-defining interface;
- light compression artifacts representative of actual captures.

Avoid:

- horizontal flips, which mirror text and fixed interface placement;
- vertical flips or arbitrary rotation;
- strong perspective warps not produced by screen capture;
- crops that remove the evidence defining the label;
- synthetic overlays that leak the class name;
- augmentation before splitting, because derived images must inherit the
  source image's split.

## Collection Review Checklist

1. Owner/consent confirmed.
2. Privacy redaction reviewed on the final image.
3. Label assigned using the current taxonomy.
4. Capture session and `source_group_id` assigned.
5. Variation metadata recorded.
6. Ambiguity documented.
7. Split remains unassigned until the dataset audit.

## Version-Control Boundary

Raw and private screenshots remain local and ignored by Git. Reviewed images,
draft and final session manifests, audit JSON, owner-review records, and review
contact sheets also remain private and ignored. Screenshots are not committed
by default. A screenshot may be tracked only as part of a specifically
reviewed and redacted subset after an explicit owner decision. The manifest
template alone does not authorize image tracking.
