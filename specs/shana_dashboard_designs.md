# Ten dashboard directions

Status: Night + Signal is the default only for the isolated coordination dashboard, following the owner's acceptance and explicit default request on October 30. Production stays unchanged. The original ten remain available as temporary comparisons.

The owner requested ten distinct styles after
finding the current dashboard uneven. These previews retain the existing page
functions, authentication and privacy confirmations.

Open `/dashboard/designs` in the authenticated dashboard. Compare Home,
Activities, Rehearsal and Phone samples at desktop or phone width. Each card has
a larger static sample and links to the existing working pages. Their style is
carried by a whitelisted `?style=` query. The preview bar changes style in place
or ends the preview. It does not save a preference or change Shana's state.

| Direction | Composition and emphasis | Tradeoff |
| --- | --- | --- |
| Quiet desk | Narrow left rail, warm charcoal, sage accents, serif headings, quiet working column | Fewer simultaneous panels |
| Night studio | Dark plum, large type, spacious panels, bottom navigation dock | Uses some vertical space |
| Field notebook | One reading column, dark amber pages, serif body text, section tabs | More scrolling |
| Signal room | Monospace labels, dense aligned panels, full-width control strip | More technical atmosphere |
| Soft companion | Centered pill navigation, rounded cards, muted burgundy and rose accents, one column | Lower information density |
| Editorial gallery | Oversized serif masthead, asymmetric primary/supporting columns | Needs more screen area |
| Compact workspace | Wide modular grid, tight spacing, flat utility panels | Phone becomes a simpler stack |
| Paper panels | Right-hand folio navigation, square dark sage panels, strong page edges | Unfamiliar desktop navigation position |
| Calm horizon | Broad blue masthead, airy dark navy working sections | A larger header above controls |
| High clarity | Large single column, light-on-dark structure, strong focus and touch controls | Longer and less visually subtle |

The gallery examples are explicitly fictional. They never fetch conversation
history, capture a window, connect a stream, send a reminder or grant permission.
They are separate from the working-page links. Real pages keep the same control
IDs and existing handlers. Hidden private panels stay hidden, and changing a
style does not check a privacy box, enable a disabled action or reconnect a
device. No external fonts or services are used.

`coordination_designs.css` applies working-page rules only beneath a recognized
body preview attribute. `coordination_designs.js` accepts only the ten original IDs and the fixed combined
`night-signal` preview ID,
sets preview variables and carries the query between allowed dashboard links.
The isolated shell marks its pages with `data-shana-default-style="night-signal"`. An absent or invalid query uses that server-selected default. Without this marker, an absent or invalid query leaves the normal page alone. The module makes no API or storage calls. Ending a comparison removes its query and restores Night + Signal in the isolated app. Production templates do not receive the marker.

## Saved review

The standalone gallery at
`data/coordination/remaining-20261027/designs/shana-dashboard-directions.html`
contains its CSS and JavaScript. It can be opened later in Brave without a live
server. Its author metadata identifies the device that generated it. The
fictional sample switcher still works. Working-page links explain that a running
dashboard is needed instead of navigating to an invalid local path.

To rebuild to a fresh filename:

```bash
PYTHONPATH=src TMPDIR=/tmp .venv/bin/python scripts/verify_dashboard_designs.py \
  --report data/coordination/remaining-20261027/designs/verification.json \
  --standalone data/coordination/remaining-20261027/designs/new-gallery.html
```

## Evidence and limits

The verifier reads the actual JavaScript palette metadata. It checks all ten
role palettes, unique direction/layout labels, CSS delimiter balance, responsive
rules, focus rules and the hidden-panel guard. The contrast math checks planned
text roles at 4.5:1 and component borders at 3:1, without rounding before the
comparison. These are role checks, not a complete accessibility assessment.
See W3C's [text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
and [non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
guidance for the distinction.

The actual preview module also runs in a controlled Node document. It checks ten
styles across four sample pages, normal-default preservation, invalid query
rejection, unchanged privacy/disabled/hidden controls, navigation queries, no API
or preference writes, ending a preview and offline link handling. The saved HTML
has no external asset dependencies. The authenticated dashboard route has a
separate HTTP test.

```bash
PYTHONPATH=src TMPDIR=/tmp .venv/bin/python -m pytest tests/test_dashboard_designs.py -q
node --check src/gamma/dashboard/static/coordination_designs.js
```

The local Qwen coding worker supplied the reviewed contrast helper and passed
its four protected tests. Codex tightened exact color parsing to reject a trailing
newline, integrated the helper and added independent boundary coverage.

No browser automation or screenshots were used. Brave URL safeguards stopped
computer control earlier in this task. Actual layout, font rendering, visual
comfort and physical Android use remain unverified. The owner will review the
directions later. No visual acceptance or selected default is implied by the
structural checks.

## Dark-mode review update, October 30

All ten directions now use dark backgrounds and panels, including the gallery frame, comparison controls and large masthead. Their layouts, navigation, typography and accent palettes remain distinct. The normal dashboard default is unchanged. The earlier light-palette standalone is retained under `data/coordination/dark-dashboard-20261030/before/`; the usual standalone gallery path now contains the updated dark review. Browser visual acceptance remains with the owner.

## Owner shortlist, October 30

The owner prefers direction 2, Night studio, and direction 4, Signal room. Both remain available as separate previews. This is a shortlist, not approval of a new default or runtime activation.

The owner subsequently authorized that combination for isolated implementation and testing. It is available as Night + Signal. A production default and activation remain unapproved.

## Combined Night + Signal review, October 30

The isolated combined preview keeps Night studio's plum surfaces, spacious rounded panels, large system-ui headings and desktop navigation dock. Signal room supplies teal accents, panel dividers and aligned monospace status labels. Monospace is confined to statuses so ordinary reading stays comfortable. The dock wraps within the desktop width. Below 760px, navigation returns to normal flow above the content, avoiding a fixed dock over phone controls or the on-screen keyboard.

The gallery presents the combined preview first, with Home, Activities, Rehearsal and Phone samples at desktop or phone width. The original ten directions remain under the comparison disclosure. The combined preview also works on existing authenticated pages through `?style=night-signal`. Ending the preview restores the existing default. No preference, privacy confirmation, channel connection or model state is changed by the preview module.

Saved review: `data/coordination/combined-dashboard-20261030/night-signal-review.html`. This standalone contains fictional samples and no external assets. Open it manually in Brave. Browser URL safeguards prevented automatic local-file opening; no alternate browser-control or hosting workaround was used.

Verification on Gamma: six focused tests passed, including all eleven previews across four page paths in the controlled document, palette-role contrast, standalone export and authenticated route checks. A separate real loopback HTTP session verified temporary sign-in, unauthorized HTML/API denial, all four page routes, exact served asset bytes, preserved phone privacy controls and the actual isolated backend status proxy. The session used an explicitly marked mock model, performed no inference and shut down cleanly. Computed layout, font rendering, actual phone use and human visual acceptance remain unverified.

Evidence is under `data/coordination/combined-dashboard-20261030/`. The scoped local Qwen worker's status CSS was reviewed against its baseline and proposal before integration. Its larger initial job was rejected before inference by the context budget and was narrowed; no paid fallback was used.

## Isolated default and awake-test preparation

The owner accepted the combined direction and then explicitly requested it as the isolated dashboard default. It now applies without a query on Home, Talk, Presence, Activities, Rehearsal, Phone, Work, Personal and Growth. The original ten comparison styles remain opt-in. The earlier saved galleries and dated receipts describe the preview stage; they do not claim the new runtime default or physical acceptance.

Legacy private pages now keep their content in a proper main container. The private Monitor receives scoped color aliases; its production template is unchanged. Presence retains compact and left/right/center placement. Disabled buttons keep their disabled attributes and have a muted visual treatment. Hidden private content remains hidden.

Current evidence: `data/coordination/awake-test-ready-20261030/`. Ninety focused Gamma checks passed, followed by a six-test default recheck covering all nine page paths in the actual module contract. The actual Windows pythonw redirector, interpreter child and Git SSH process were observed. A twelve-hour mock session started and stopped cleanly; a separate five-second session expired automatically. Both removed temporary login files. Real authored-WAV STT, Core response, TTS and authenticated poll-then-audio download passed. No browser playback or physical microphone acceptance is inferred.
