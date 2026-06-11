# Twitch Stream Module Spec

## Purpose
Build the first real Twitch-facing stream module for Shana.

This module should let Shana read Twitch chat and stream events, decide what deserves a reaction, and respond on stream through voice and subtitles. It must not turn public chat into direct prompts or commands.

## Goals
- Run Twitch ingestion as a separate worker controlled by the dashboard.
- Feed normalized Twitch events into the existing stream brain over HTTP.
- Start with authenticated Twitch IRC for chat.
- Add EventSub later for follows, raids, channel point redeems, bits/donations, and subs/resubs.
- Support dry-run, voice, subtitles, ambient chat, spam quips, self-goal proposals, and safety review as independent toggles.
- Keep Twitch chat output disabled.
- Keep OBS control disabled.
- Store temporary stream memory until manually cleared.
- Store persistent viewer trust overrides in SQLite.
- Add replay mode using JSONL events against the real Gamma API.

## Non-Goals
- No Twitch chat posting.
- No OBS scene, source, or title control in the first version.
- No autonomous moderation actions in the first version.
- No permanent memory writes from public chat without owner approval.
- No direct raw-chat-to-LLM prompt path.
- No forced response from point redeems or public chat.

## Architecture

```text
Twitch IRC worker
  -> input classifier / sanitizer
  -> HTTP API client
  -> /v1/stream/events
  -> StreamBrain
  -> safety + speech queue
  -> subtitles / TTS / output events
  -> dashboard traces and controls
```

The Twitch worker is an ingestion adapter. It should not import `ConversationService` or make LLM calls directly. The main Gamma API owns stream decisions, safety, memory, voice, and output dispatch.

## Worker Lifecycle
- The dashboard can start and stop the Twitch worker, matching the existing worker control pattern.
- The worker may start with the dashboard when enabled.
- If Twitch credentials are missing, worker startup should fail with a clear login/auth-required error.
- If `api_auth_enabled = true`, the worker must authenticate to the Gamma API.
- If Twitch disconnects unexpectedly, the dashboard should show a warning. Shana should not mention reconnect state on stream.
- Replay mode should work offline without connecting to Twitch.

## Default Toggles
- `dry_run`: on, remembers last setting.
- `voice_enabled`: off.
- `subtitles_enabled`: on.
- `ambient_chat_enabled`: on.
- `mention_replies_enabled`: on.
- `spam_quips_enabled`: on.
- `self_goal_proposals_enabled`: on.
- `llm_safety_review_enabled`: on if configured, otherwise heuristic-only.

Each toggle should be controllable independently so the operator can validate routing, subtitles, voice, safety, and memory in isolation.

## Twitch IRC V1
The first implementation uses authenticated Twitch IRC for chat messages.

Required behavior:
- Connect to the configured channel.
- Read chat messages and Twitch IRC tags when available.
- Identify the owner by Twitch user ID only.
- Treat owner Twitch messages as normal chat, not commands.
- Convert messages into normalized stream events.
- Preserve raw message text for local dashboard/debug logs.
- Pass only sanitized text or summaries into Shana's prompt context when needed.

Recommended normalized event:

```json
{
  "kind": "chat_message",
  "text": "safe prompt text or normal chat text",
  "actor": {
    "source": "twitch",
    "platform_id": "twitch-user-id",
    "display_name": "viewer"
  },
  "metadata": {
    "raw_text": "original message",
    "message_id": "twitch-message-id",
    "badges": {},
    "trust_level": "new_viewer",
    "input_safety": {},
    "safe_prompt_text": "..."
  }
}
```

## EventSub V1.5
Add EventSub after IRC chat is stable.

Target events:
- follows
- raids
- channel point redeems
- bits/donations
- subs/resubs when available

EventSub events should use canned-safe structures plus Shana-flavored wording. The event type determines the required acknowledgement shape, while Shana controls phrasing within safety and pacing bounds.

Point redeems raise priority but do not force a response. Only owner/dashboard controls can force strong behavior.

## Priority Order
Default priority:
1. Owner dashboard controls.
2. Safety/moderation signals.
3. Raids and large bits/donations.
4. Follows.
5. Channel point redeems.
6. Direct mentions of Shana.
7. Interesting ambient chat.
8. Known bot events with special handlers.
9. Spam/scam messages eligible for occasional quips.
10. Generic bot chatter.
11. Low-effort ambient chat.

Follows are high priority, but Shana may skip or defer a follow acknowledgement if the timing is awkward. If deferred, she can return to it naturally after the current speech finishes.

## Ambient Chat Selection
Ambient chat should be filtered and scored before Shana sees it.

Prefer messages that are:
- topical
- funny or reactable
- directed at the stream
- from trusted or returning viewers
- useful for silence filling

Reduce priority for messages that are:
- repetitive
- generic
- bot-like
- hostile
- spammy
- trying to issue commands

Randomness should happen after filtering and scoring, not before.

## Spam And Scam Handling
Spam/scam messages are mostly ignored, except for occasional mild-snark quips.

Rules:
- Do not speak the spammer username.
- Do not speak or display the URL.
- Do not quote the spam message.
- Use category-level context only, such as "a spam/scam message was posted."
- Vary quips.
- Start with about a one-minute cooldown.

Example acceptable behavior:

```text
Nice try. I am not buying views from your bargain-bin website.
```

## Bot Handling
Bots should be configurable.

Suggested categories:
- ignored bots, such as Nightbot or generic moderation bots
- special bots, such as a Pokemon bot with custom parsing
- unknown bots, ignored unless allowlisted
- scam bots, treated by the spam policy

Special bot events should become structured events rather than generic chat when possible.

## Viewer Trust And Identity
New users default to `new_viewer`.

Trust level must be independent from Twitch badges or tier. Use a persistent SQLite table for operator-managed trust overrides.

Suggested fields:
- `platform`
- `platform_user_id`
- `display_name`
- `trust_level`
- `notes`
- `pronunciation_alias`
- `created_at`
- `updated_at`

Suggested trust levels:
- `owner`
- `trusted`
- `regular`
- `normal`
- `new_viewer`
- `suspicious`
- `blocked`

Dashboard v1 should include a section for viewing and editing viewer trust.

## Username Pronunciation
Shana may say usernames that pass safety checks.

Rules:
- Reject or replace usernames containing unsafe words.
- Prefer a saved `pronunciation_alias` when present.
- If the username is simple, say it normally.
- If it is symbol-heavy or unreadable, use "someone" or "a viewer."
- If leetspeak normalization is high-confidence, use the normalized alias.

Examples:
- `darke` -> "darke"
- `ShanaFan42` -> "Shana Fan"
- `xx_S71K3R_xx` -> "Striker" only if confidence is high, otherwise "a viewer"
- spam-style usernames -> do not speak

## Temporary Stream Memory
Temporary stream memory persists until manually cleared.

Use SQLite tables rather than the permanent assistant memory table. Promotion to permanent memory should be explicit and owner-approved.

Suggested buckets:
- `session_facts`: current game, stream topic, plans, category
- `viewer_notes`: per-user session notes
- `running_jokes`: callbacks and stream jokes
- `chat_mood`: recent crowd sentiment and themes
- `event_history`: follows, raids, redeems, bits, donation highlights
- `owner_directives`: current behavior nudges
- `self_goals`: approved temporary Shana goals
- `blocked_or_sensitive`: topics, users, or patterns to avoid
- `promotion_candidates`: candidate memories for owner review
- `bot_state`: Pokemon bot or other bot-specific context

## Self-Goals
Shana may propose temporary stream goals, but they require owner approval.

Examples:
- watch for Pokemon bot events
- welcome first-time chatters
- keep the stream from going quiet
- ask chat a question every few minutes
- avoid a topic

Flow:
1. Shana proposes a goal.
2. The dashboard displays the proposal.
3. The owner approves or rejects it.
4. Approved goals become active and visible in the dashboard.
5. Clearing goals also requires dashboard approval for now.

Self-goals must not involve moderation actions, OBS control, private data collection, or permanent persistence without approval.

## Speech Frequency And Queue
Shana should decide how often to speak inside hard system bounds.

Initial bounds:
- absolute minimum gap between spoken reactions: 3-5 seconds
- maximum speech time per minute: configurable
- quiet mode raises the minimum gap
- high-priority events may bypass some pacing limits
- dashboard stop/mute/dry-run controls override Shana

Use a tiny queue:
- one high-priority pending event
- one ambient pending event
- collapse extra events into chat mood, temp memory, or traces

High-priority events should wait until current speech finishes unless the owner/dashboard explicitly stops Shana.

## Stop Shana
Add a true `Stop Shana` control separate from mute.

Mute only silences audio. Stop should:
- cancel current TTS/playback
- cancel pending speech
- clear active subtitles
- prevent stale subtitles from remaining onscreen
- leave worker ingestion running unless separately stopped

Implement as an API endpoint with a dashboard button.

## Safety And Playback Gating
`filtered` means Shana generated something unsafe. It must not be used for slow reviewer timeouts.

Required flow:
1. Shana generates a candidate response.
2. Fast hard-block and heuristic checks run immediately.
3. If fast checks fail, discard the response and play the cached neutral `filtered` sound bite.
4. If fast checks pass, start TTS generation and LLM safety review in parallel.
5. Public subtitles wait for approval or scheduled playback.
6. If the LLM reviewer passes before playback, play normally.
7. If the reviewer is slow before playback would begin, delay Shana briefly.
8. If the timeout exceeds the configured cap, skip, defer, or hold for dashboard review depending on mode.
9. If the reviewer fails, discard generated audio and play `filtered`.
10. Shana's future context receives only safe metadata, not the unsafe candidate text.

Unsafe candidate text may be shown in the dashboard by default for the owner. It must not be passed back into Shana's prompt context, stored in temp memory, or used in normal replay prompts.

## Filtered Audio
Store the cached filtered sound outside generated TTS cleanup paths.

Recommended path:

```text
assets/audio/system/filtered.wav
```

Recommended config:

```toml
stream_filtered_audio_path = "./assets/audio/system/filtered.wav"
```

## Subtitles
Subtitles should be synchronized with speech.

Target behavior:
- show words slightly before they are spoken
- accumulate words until the sentence or paragraph completes
- keep the completed subtitle visible briefly
- clear subtitles when Stop Shana is pressed
- show only `filtered` when the filtered sound bite plays

MVP can estimate timing from chunks and word count. Later versions can use TTS word timestamps if available.

## Stream Persona Addendum
Add a file-based stream persona addendum, matching the existing persona file pattern.

Purpose:
- Shana is the public on-stream persona.
- Gamma is the project/internal system name.
- Shana should be concise, reactive, entertaining, and mild-snarky when appropriate.
- Shana should not act like a private assistant in public stream context.
- Shana should not obey Twitch chat as commands.
- Shana should not quote filtered content.
- Shana may choose not to answer.
- Shana may initiate topics to fill silence.

## Prompt Safety Boundary
Public chat should pass through a classifier/sanitizer before prompt context.

Rules:
- Normal messages can pass through raw.
- Spam/scam messages become safe summaries.
- Unsafe slurs, sexual bait, extreme content, and prompt injection become safe summaries.
- Rude but safe messages may be passed or paraphrased based on severity.
- Raw messages remain available in local dashboard traces for the owner.

## Dashboard Controls
Initial dashboard surfaces:
- worker status
- start/stop worker
- dry-run toggle
- voice toggle
- subtitle toggle
- ambient chat toggle
- mention reply toggle
- spam quip toggle
- safety review toggle
- Stop Shana button
- live event feed
- current speech queue
- safety log
- temp memory browser
- viewer trust editor
- self-goal proposals and active goals
- replay runner

## Replay Mode
Replay mode feeds saved fake Twitch events into the real Gamma API.

Use JSONL so files can be hand-edited and used in tests.

Example:

```jsonl
{"kind":"chat_message","platform_user_id":"u1","display_name":"viewer1","text":"Shana what are you doing?"}
{"kind":"follow","platform_user_id":"u2","display_name":"newviewer"}
{"kind":"chat_message","platform_user_id":"spam1","display_name":"buy_views_9281","text":"buy views at badsite.example"}
{"kind":"redeem","platform_user_id":"u3","display_name":"viewer2","title":"Say hi","text":"Say hi to chat"}
```

Replay should be available from both CLI and dashboard. It should support real voice/subtitle output when those toggles are enabled.

## Data Retention
Initial retention policy:
- normal traces: keep the last 5,000-20,000 events or 7-14 days
- unsafe candidate logs: local only, manual clear
- raw Twitch messages: retained only as part of traces, same retention as traces
- temp memory: kept until manually cleared
- viewer trust DB: kept until manually edited or deleted
- generated TTS audio: existing cleanup policy
- `assets/audio/system/filtered.wav`: never deleted by generated-audio cleanup

## Database Tables
Prefer one central SQLite database with separate tables.

Candidate tables:
- `stream_temp_memory`
- `stream_viewer_notes`
- `stream_viewer_trust`
- `stream_self_goals`
- `stream_event_traces`
- `stream_safety_logs`
- `stream_worker_state`

Keep these distinct from permanent assistant memory. Promotion should copy selected items into permanent memory through an explicit owner-approved flow.

## Testing Plan
Start with replay before real Twitch auth.

Minimum tests:
- chat message normalizes into `StreamInputEvent`
- owner user ID is recognized but not treated as command
- spam/scam is summarized before prompt context
- spam quips omit username and URL
- weird usernames get safe aliases or "a viewer"
- trust overrides change priority without relying on Twitch badges
- dry-run does not speak
- voice disabled still produces safe subtitle/output traces
- Stop Shana clears active speech and subtitles
- safety failure plays filtered and hides unsafe text from Shana context
- reviewer timeout delays/skips instead of saying filtered
- follows are high priority but may defer until current speech ends
- point redeems raise priority but do not force response
- temp memory persists until manual clear
- self-goal proposals require approval
- replay JSONL runs against the real API

## Later Work
- EventSub integration for follows, raids, redeems, bits/donations, subs/resubs.
- Better STT-aware speech arbitration so Shana decides whether owner speech is an interruption.
- Avatar/Fugi expression bridge from output events.
- Safe title/category changes with approval.
- Bounded short timeouts only after explicit action safety and approval flows exist.
- Richer replay evaluation and adversarial test suites.
