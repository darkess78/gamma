# P5 isolated review instructions

Use Brave for Gamma/Shana browser work. Listening acceptance is deferred by the
owner. These instructions prepare a later finite review, not activation.

## Start a private review

Run the Windows controller through the installed hidden CLI wrapper and
pythonw.exe, following the [foundation launcher instructions](shana_foundation_review_runbook.md).
The controller arguments for this slice are:

```text
scripts/review_coordination_windows.pyw
  --host neety@10.78.78.13
  --identity-file C:/Users/darke/.ssh/codex_gamma_ed25519
  --remote-project /home/neety/Documents/gamma-coordination-core-20261023
  --model qwen3.8:27b --port 18705 --duration 1800
  --private-voice --voice-profile qwen_shana_dataset_xvec_soft
```

Keep the launching wrapper alive with `-Wait`. CLI helpers, SSH and nested
children must use CREATE_NO_WINDOW. A visible terminal is unnecessary.
Do not start a second controller over an active review. `--status` verifies
the current controller creation time and deadline. `--stop` requests only that
controller's bounded shutdown. The controller retains logs and removes its
temporary credentials after it closes.

Once status is ready, open this URL in Brave:

```text
http://127.0.0.1:18705/dashboard/presence
```

The optional `scripts/private_presence_client.pyw --browser brave --url <URL>`
prepares a separate app window. It starts no Shana service. Sign in using the
temporary credentials file identified by controller status. Keep that file
private; it is not part of the review package.

The owner confirms the actual private audience and microphone source, then
enables speech and listening. Capture starts only on the listening control.
Browser microphone permission stays under the owner's control. Text remains
available independently. Current response latency can exceed 20 seconds.

## What to assess

Try a question without a name cue, a name cue, thinking aloud and speech to
someone else. Try neutral and emotional subjects without requiring cheerfulness.
Judge whether wording, disagreement, timing and voice remain recognizable.
The current face is provisional presentation artwork.

While audio plays, interrupt with a different phrase. Stop can invalidate a
pending generation. Speech during an active Core decision can receive busy;
two active audio requests are bounded and additional clips can be dropped.
Exact deliberate repetition can resemble playback echo. Test the intended
microphone, speakers/headphones, room noise and overlapping speech directly.

Hide/show the avatar, move/compact the panel and mute speech while a private task
continues. Then sleep and wake. Hiding, capability disable and sleep should have
different effects. Reconnect must not replay old audio. A played receipt records
the browser's media event, not proof of hearing.

The technical tests use actual models/audio plus separate controlled browser
fixtures. They do not replace this physical and subjective review. Record missed
intended turns, false replies, first-audio delay, interruptions and any stale
playback. Discord remains disconnected until a later explicit private test is
selected. No current review instruction authorizes production activation.

## Reproduce the technical checks

Run inside the isolated Gamma candidate with its virtual environment and
`PYTHONPATH=$PWD/src TMPDIR=/tmp`. Sources must resolve under its `src/gamma`.

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_private_audio_browser.py --source-snapshot data/coordination/p4/source-discussion-29af417b5756464391c40f1a14ee0a88/final-snapshot
.venv/bin/python scripts/verify_private_work_voice.py --real-audio
```

Run model checks sequentially, with source/checker files frozen. The selected
closed snapshot must be available; it is deliberately excluded from the source
archive. Generated input speech is labelled as a controlled fixture. Browser
drivers bind actual HTTP audio and media acknowledgments. Saved receipts identify
the source and models used. Review [implementation evidence and limits](shana_private_interaction.md)
before interpreting a pass.
