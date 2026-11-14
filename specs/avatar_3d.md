# 3D avatar (VRM over the VMC protocol)

Status: First version implemented, off by default (2026-11-14). Not yet tried
against a real avatar app.

## Decision

**Model:** a VRM file built in VRoid Studio (free). Full model sheet and build
steps: [docs/shana_3d_model_sheet.md](../docs/shana_3d_model_sheet.md).

**Avatar app:** **Warudo** on the Windows streaming PC, with **VSeeFace** as
the fallback. Gamma drives either one the same way: VMC protocol messages
(OSC over UDP) sent from Gamma PC across the LAN.

Why:

| Option | Cost | 3D/VRM | Can Gamma drive it | Notes |
|---|---|---|---|---|
| Warudo | Free for personal use (Steam) | Yes | VMC receiver, default port 39539 | Actively developed; built-in idle animations, blueprints, Spout/virtual-camera output for OBS. Chosen. |
| VSeeFace | Free | Yes (VRM 0.x) | VMC receiver applies received blendshapes (on by default since 1.13.36m) | Light and simple. Development has slowed. Known catch: its own tracking/lip sync and received VMC expressions can override each other. Fallback. |
| VNyan | Free | Yes | VMC input; WebSocket/REST only trigger named nodes (fire-and-forget) | Would work through VMC too; no advantage over Warudo here. |
| VTube Studio | Free/paid | No, Live2D (2D) only | WebSocket API (Gamma already has an adapter) | Not a 3D option. Keep the existing adapter for a 2D model. |

VMC is an open, documented protocol (protocol.vmc.info) built on OSC. Using it
means Gamma has no lock-in: any VMC receiver (Warudo, VSeeFace, VNyan,
VMagicMirror) takes the same packets, and Gamma needs no new dependency.

## What Gamma sends

Module: `src/gamma/performer/avatar3d.py` (adapter, sender, runner) and
`src/gamma/performer/osc.py` (OSC encoder, about 50 lines, stdlib only).

The runner subscribes to the performer bus like the VTube Studio runner and
sends a full frame `fps` times a second (default 30): one
`/VMC/Ext/Blend/Val (string name, float value)` per managed blendshape,
followed by `/VMC/Ext/Blend/Apply`, wrapped in one OSC bundle. Every frame
carries every managed name, zeros included, so a missed UDP packet heals on
the next frame. On stop it sends one all-zero frame.

| Performer event | Effect |
|---|---|
| `expression_set` | Fades (150 ms) to the emotion's blendshape weights |
| `speech_started` | Marks the turn as voiced |
| `subtitle_update` | For a voiced turn: schedules mouth movement from the text |
| `speech_chunk_ready` | Schedules mouth movement from the chunk text; uses WAV length and loudness when the chunk carries WAV audio |
| `output_cleared` | Stops the mouth at once (interruptions, mute) |
| `speech_ended` | Nothing: events arrive when audio is generated, not when it finishes playing, so the scheduled mouth runs out on its own |
| `mouth_level` | Opens the mouth to `payload.level` for 150 ms (no producer yet) |
| `motion_trigger` | Ignored: body motion belongs to the avatar app |

Emotion mapping (VRM 0.x names; VRM 1.0 equivalents are used when
`naming = "vrm1"`):

| Gamma emotion | Blendshapes |
|---|---|
| neutral | none |
| happy | Joy 1.0 |
| teasing | Fun 0.7, Blink_R 0.5 |
| concerned | Sorrow 0.7 |
| excited | Joy 0.7, Surprised 0.6 |
| embarrassed | Joy 0.35, Sorrow 0.4 |
| annoyed | Angry 0.8 |

Any emotion can be remapped to custom clips with `[avatar3d.expressions]`.

Lip sync from text: each word takes `len(word) / lipsync_chars_per_second`,
split across its vowel groups; each syllable opens the matching A/I/U/E/O
shape on a half-sine; commas pause 0.25 s, sentence ends 0.4 s. A turn's mouth
comes from either its subtitle or its speech chunks, never both. New speech
queues after speech already scheduled.

Gamma also blinks her every 2.5 to 6 seconds (`auto_blink`). It sends no bones;
idle body motion, breathing and head sway come from the avatar app.

## Configuration

`config/app.toml` (defaults shown; also `SHANA_AVATAR3D_*` environment
variables):

```toml
[avatar3d]
enabled = false
host = "127.0.0.1"          # the Windows PC running Warudo/VSeeFace
port = 39539
naming = "vrm0"             # "vrm1" for a VRM 1.0 export
fps = 30
lipsync = "text"            # or "off" to let the app lip-sync from audio
lipsync_chars_per_second = 14.0
lipsync_delay_ms = 0
auto_blink = true
target_policy = "stream_public"   # "dashboard_monitor" also sees dashboard live voice
```

With `enabled = true` the runner starts with the Shana API. It can also be
started and stopped with `POST /v1/performer/adapters/avatar-3d/start` and
`/stop`; its state appears under `adapters.avatar3d` in
`GET /v1/performer/status`.

Manual check from any machine with Gamma's code:
`python -m gamma.performer.avatar3d --host <avatar PC IP>` cycles every
emotion and says a line in each.

## Setup on the Windows streaming PC

1. Build and export the model (VRM 0.0) following the model sheet.
2. Install Warudo from Steam. Load `Shana_nojacket.vrm` (or the jacket
   version) as the character.
3. Add VMC as the tracking source (Warudo's VMC receiver, port 39539). Leave
   face tracking off; Shana has no camera.
4. Pick an idle animation for the character so she breathes and shifts.
5. Lip sync, choose one:
   - **Gamma (default):** keep `lipsync = "text"`. Works with no audio
     routing; timing is approximate.
   - **The app, from audio (better timing):** route Shana's audio into a
     virtual cable (e.g. VB-CABLE), select that cable as the app's lip-sync
     microphone, and set `lipsync = "off"` in Gamma so the two don't fight.
6. Allow the packets through Windows Firewall (private network only, from
   Gamma PC):
   `New-NetFirewallRule -DisplayName "VMC from Gamma" -Direction Inbound -Protocol UDP -LocalPort 39539 -RemoteAddress <Gamma PC IP> -Profile Private -Action Allow`
7. On Gamma PC set `[avatar3d] enabled = true` and `host = "<Windows PC
   LAN IP>"`, then restart the Shana API (or call the start endpoint).
8. OBS: capture Warudo with its Spout2 output (OBS Spout2 plugin) or the
   virtual camera, with a transparent background.

If Warudo does not apply blendshapes from a blendshape-only VMC stream, use
VSeeFace: General settings → enable the VMC/OSC receiver on port 39539, and
turn off its own face tracking so received expressions are not overridden.

## Network

- Direction: Gamma PC → Windows PC only, UDP 39539. Nothing listens on Gamma
  PC; no new port is opened there.
- Volume: about 30 packets/s of roughly 600 bytes.
- VMC has no authentication. Keep the firewall rule limited to Gamma PC's
  address on the private network.

## Implemented vs not

Implemented:
- OSC encoder, VMC blendshape sender, emotion mapping (VRM 0.x and 1.0
  names, overridable), text lip sync with WAV duration/loudness, auto-blink,
  runner on the performer bus, start/stop/status endpoints, autostart when
  enabled, demo command.
- Tests: `tests/test_avatar3d_vmc.py` (byte-exact OSC, mapping, lip sync,
  localhost UDP, runner with a fake socket).

Not done:
- Not tried against Warudo or VSeeFace yet, and the model does not exist yet.
- Lip sync timing follows generation, not playback. The overlay plays audio
  a little later; `lipsync_delay_ms` is a fixed offset. Exact sync needs either
  the app's audio lip sync or a playback-time event from the audio player.
- No body/head motion from Gamma (`/VMC/Ext/Bone/Pos`), no gestures for
  `motion_trigger`, no jacket on/off switching, no dashboard controls.
