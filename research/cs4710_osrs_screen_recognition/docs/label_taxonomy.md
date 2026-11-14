# OSRS Screen-State Label Taxonomy

Status: Draft `draft-v0.1`

Assign exactly one label to each reviewed screenshot. Classify the dominant
interaction state visible in the screenshot, not the player's inferred intent
or the underlying world location.

## `normal_gameplay`

Use when normal game-world interaction is visible and none of the targeted
bank, dialogue/choice, shop, or Grand Exchange interfaces is open.

Include:

- walking, skilling, or combat with the standard HUD;
- inventory, equipment, prayer, spellbook, or other normal side tabs;
- an ordinary right-click context menu when normal gameplay remains the clear
  dominant state.

Exclude:

- login, loading, disconnected, settings-only, or title screens;
- targeted modal interfaces covered by another label;
- any bank, dialogue/choice, NPC shop, or Grand Exchange interface.

## `bank_open`

Use only when the recognizable standard OSRS bank interface is visibly open,
sufficiently complete, and the dominant central interaction surface.

Include:

- the standard bank interface and its inventory panel;
- bank search or bank-tab variants that remain clearly identifiable as bank
  UI.

Exclude:

- deposit boxes or ambiguous storage interfaces;
- shops, Grand Exchange, player trades, or ordinary inventory tabs.

## `dialogue_or_choice_open`

Use when a recognizable dialogue box, continue prompt, or selectable
dialogue-response interface is the dominant interaction state.

Include:

- dialogue text with a continue prompt;
- selectable conversation responses;
- quest or tutorial dialogue presented through the normal dialogue UI.

Exclude:

- ordinary public or private chat;
- right-click context menus;
- incidental text in another interface.

## `shop_open`

Use when a standard NPC shop/buy/sell interface is visibly open.

Include:

- stock grids and buy/sell controls clearly associated with an NPC shop;

Exclude:

- Grand Exchange offer screens;
- player-to-player trade windows;
- bank or deposit-box interfaces.

## `grand_exchange_open`

Use when a recognizable Grand Exchange overview, offer setup, item search,
collection, or offer-status interface is visibly open.

Include:

- empty or active offer-slot overview;
- item search or offer setup tied to the Grand Exchange;
- collection/status screens clearly identified as Grand Exchange UI.

Exclude:

- NPC shops;
- ordinary banks, even when located near the Grand Exchange;
- plugin price panels without the actual Grand Exchange interface open.

## `unknown_other`

Use for a genuine unmodeled or ambiguous screen state when none of the five
targeted states confidently applies.

Include prominent unmodeled states such as:

- settings, world map, quest journal, death, login, loading, disconnected, or
  warning screens;
- player trade, deposit box, an unmodeled minigame interface, or another
  unmodeled interface;
- screenshots where multiple targeted interfaces appear and dominance cannot
  be established.

Do not use `unknown_other` primarily for corrupted, blurry, broken,
destructively cropped, privacy-invalid, or protocol-invalid images. Exclude
unusable captures instead so image quality does not become a state shortcut.

## Tie-Breaking Rules

1. Prefer the most specific visible targeted interface over
   `normal_gameplay`.
2. Label the interface that controls the current interaction, not a background
   panel visible behind it.
3. If multiple modeled interfaces are visible, label the dominant task-blocking
   interface and record the ambiguity in `notes`.
4. A normal side tab does not replace `normal_gameplay`.
5. Plugin overlays alone do not establish a targeted state.
6. Exclude a crop or alteration that removes class-defining evidence. Every v1
   reviewed image uses the same calibrated `fixed_game_canvas_v1` crop and
   must preserve the complete game canvas, including complete class-defining
   bank, dialogue, shop, and Grand Exchange interfaces.
7. When no modeled state is clearly dominant, use `unknown_other` and document
   the ambiguity.
