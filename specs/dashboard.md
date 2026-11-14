# Dashboard

The dashboard (`gamma.dashboard`, port 8001) is how the owner runs Shana from a
browser. All processing happens on Gamma PC; the dashboard only shows state and
sends commands.

## Pages

Every page except Talk is the same shell, `static/index.html`. The server
injects `window.GAMMA_DASHBOARD_PAGE`, and `nav.js` shows only the sections whose
`data-dashboard-tab` matches that page. Each page polls one bounded status
endpoint every 10 seconds, and skips the poll while the tab is hidden.

| Page | URL | Purpose | Polls |
| --- | --- | --- | --- |
| Home | `/dashboard` | Presence mode (Sleep / Wake / Go Live / Break) with Wake audience options; what Shana is doing (mood, topic, objective, next action, last thing she said); service and GPU health; recent stream output; links to Talk, Performer and Subtitles. | `/api/status/summary`, `/api/presence`, `/api/stream/outputs/recent` |
| Talk | `/dashboard/monitor` | Private text chat and live voice with Shana, plus output playback. Separate page (`monitor.html`) that embeds `live_voice_panel.html`. Keep it open in its own tab. | its own WebSocket and `/api/monitor/status` |
| Shana | `/dashboard/shana` | Voice: speech engine, voice profile, test, profile editor, generated audio. Brain and hearing: LLM and STT status and tests. Behaviour: speech safety, model routing, emotion, idle chatter (one Save button). | `/api/status/settings` |
| People | `/dashboard/people` | Known people with linked accounts and trust, memories (add, edit, forget, forget recent), and Twitch viewer trust (collapsed). | `/api/status/memory` |
| Integrations | `/dashboard/integrations` | Twitch chat worker and EventSub start/stop and stream settings; Discord bot and voice controls; output targets (mute/unmute monitor, stream, Discord call), stop stream speech, overlay links; stream activity feeds (collapsed, loaded only while open). | `/api/status/summary`, `/api/discord/text-worker/status` |
| Coding | `/dashboard/coding` | Owner coding goals: goal form, work list with each task's progress, pause/resume/stop, and the proposed diff for work awaiting review. | `/api/coding/work` |
| System | `/dashboard/system` | Shana start/restart/stop, Gamma PC CPU/RAM/disk/GPU, service health, provider details, and collapsed diagnostics: presence internals, startup admission, sidecar GPU allocations, timings, stdout/stderr, Twitch replay test, raw status JSON. | `/api/status/diagnostics` |

The top bar on every page shows Shana's state (running and presence mode), the
page links, and **Stop Output**, which stops stream speech and clears the
monitor, stream and Discord targets. Action results appear in one line under
the top bar.

Settings forms (Behaviour, Twitch, voice profile editor) stop accepting polled
values once you edit them, so the poll cannot wipe unsaved changes. Saving
clears that state.

## Retired URLs

These redirect (307, query string kept) to the page that now holds the content:

| Old | New |
| --- | --- |
| `/dashboard/live`, `/dashboard/talk`, `/talk` | `/dashboard/monitor` |
| `/dashboard/presence`, `/presence` | `/dashboard` |
| `/dashboard/status` | `/dashboard/system` |
| `/dashboard/stream`, `/dashboard/twitch` | `/dashboard/integrations` |
| `/dashboard/memory` | `/dashboard/people` |
| `/dashboard/improvement` | `/dashboard/coding` |
| `/dashboard/settings` | `/dashboard/shana` |

`/monitor` still redirects to `/dashboard/monitor`. Output views are unchanged:
`/performer` (Shana API) and `/overlay/subtitles`.

## Frontend files

All plain JavaScript with no build step or dependencies.

- `nav.js`: page visibility, mobile menu, output-view links, the shared action line.
- `status.js`: per-page status polling and rendering, `action()`, voice selection.
- `presence.mjs`: Home presence controls and activity.
- `api.js`: Twitch settings, viewer trust, replay, stream activity, stop actions.
- `stream.js`: HTML renderers for stream feeds.
- `controls.js`: Behaviour save, voice profile editor, generated audio.
- `memory.js`: People page records and modals.
- `discord.mjs`, `autonomous-coding.mjs`: Discord and Coding pages.
- `monitor.js`, `live.js`, `monitor.html`, `live_voice_panel.html`: Talk page only.
- `overlay.html`: subtitle overlay.

## Backend additions for this layout

- `/api/status/summary` also returns `assistant.emotion` (current mood) and
  `discord.text_worker`, for Home.
- `GET /api/coding/work/{id}/proposal` returns the saved `proposal.diff` for work
  in `awaiting_owner_review`. There is no apply button yet; the owner reviews the
  diff and applies it by hand.
