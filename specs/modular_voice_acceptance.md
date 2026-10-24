# Modular voice acceptance checkpoint

Date: 2026-09-19
Implementation: `/home/neety/Documents/gamma-modular-phase1` on Gamma PC.

## Scope and acceptance status

The architecture direction is accepted. Modular voice pipeline acceptance passed
with real Whisper/Qwen audio and supervised physical Windows browser input.
The operator confirmed intelligible playback, text-only responses with TTS
stopped, and successful interruption without resumption. STT restart preserved
the brain identity. Decision content and required-review verdicts remain labelled
synthetic fixtures; this is not an acceptance claim for model judgement quality.

The checkout already disabled legacy subprocess output before this task:
`run_live_voice_worker.py` raises a retirement error and the compatibility job
manager delegates to core live. Those pre-existing edits were preserved. This
task made no further legacy removals; five obsolete legacy tests still fail.

## Implementation

- A stopped or closed TTS worker resolves an incoming speech request to reviewed
  text-only output before decision authorization. It leaves the brain and STT
  available and never starts TTS implicitly. Responses identify
  `speech_unavailable=tts_worker_stopped` in TTS metadata. A worker failure after
  authorization still fails closed; this does not replay or reauthorize speech.
- Duplicate `end_turn` controls cannot submit another final or reset processing
  state. Starting a new capture cancels the previous pending remote turn and
  polling. Poll results recheck their identity after the fetch completes.
- Live job responses expose `decision_trace_id` and `delivery_grant`, allowing
  the final utterance ID to be joined to its committed decision, reviewed grant,
  synthesized artifact and receiver playback receipt. These IDs do not replace
  the independent worker protocol identities or prove audibility.
- Shared-provider STT admission denial maps to retryable queue unavailability
  and HTTP 503 instead of an unhandled provider exception. Proven reaped local
  work releases its owned voice capacity; uncertain work remains protected.
- A performer transport closing during send closes its receiver lease without
  an unhandled ASGI disconnect error. Unrelated RuntimeErrors still propagate.

Cancellation tests hold recognition, generation and synthesis open and confirm
that obsolete work cannot publish. Provider work retains capacity until it exits.
No admission allocation is released merely because a caller stopped waiting.

## Executable real-audio check

`scripts/verify_voice_acceptance.py` and its Chromium driver create disposable,
authenticated, loopback API/dashboard instances and an isolated cached Qwen
server. They use real persistent Whisper and Qwen workers. The intended profile
is resolved read-only from the specified source root; relative reference paths
are anchored there. Only that profile is reproduced in a private temporary
fixture. Existing configuration files and production services are not changed.
Private reference audio is read in place on Gamma. Generated voice artifacts,
fixture state and temporary profile data are removed at the end.

The generated microphone fixture says: "The quiet garden has seven yellow
flowers." Browser Web Audio supplies the waveform to the actual capture and VAD
code. Decision content uses the mock adapter; required review calls a labelled
synthetic reviewer on a separate loopback server. Therefore the check verifies
real audio inference, lifecycle and delivery boundaries, not model decision or
safety judgement quality.

The test requires:

1. One VAD final and one submitted turn for the first captured phrase.
2. A real Chromium `playing` event and natural completion with a durable receipt.
3. TTS stop followed by successful recognition and reviewed text without audio.
4. STT restart with a different STT PID and unchanged brain PID/generation.
5. STT process crash, successful subsequent recognition, and unchanged brain.
6. Cancellation during actual playback, media removal and an interrupted receipt.
7. A trace joining live turn, decision, delivery grant and playback status.
8. Actual Whisper and Qwen GPU allocations, non-silent synthesized PCM, and
   Whisper readback of generated replies within the stated word-error bound.
9. Unchanged production service PIDs and cleanup of the private fixture.

Run on Gamma, from the implementation checkout:

```bash
PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_voice_acceptance.py \
  --profile-root /home/neety/Documents/gamma-main \
  --whisper-model /path/to/cached/faster-whisper-base.en/snapshot \
  --qwen-model /path/to/cached/Qwen3-TTS-12Hz-1.7B-Base/snapshot \
  --tts-python /home/neety/Documents/gamma-main/.venv-tts/bin/python
```

The verifier uses the shared admission policy. Another active model job may
correctly deny a turn; wait for capacity and rerun. Do not clear another job's
allocation or expand resource limits to obtain acceptance.

## Evidence and limits

The intended profile is `qwen_shana_dataset_xvec_soft`, with RVC disabled.
The real-audio lifecycle run completed both natural and interrupted playback.
It preserved transcription/text after TTS stop and recovered STT after both a
restart and a process crash without changing the brain's identity. Required
review ran four times through its real boundary, using synthetic verdicts.
The final run produced 5.16-second and 4.76-second, 24 kHz WAV replies with RMS
levels 0.1012 and 0.0876. Both achieved zero word error in independent Whisper
readback. The final verifier exited successfully and confirmed unchanged
production PIDs and private-fixture cleanup. Final browser page errors were empty.
The first successful run revealed a server-side
ASGI send/disconnect race, which was subsequently fixed and regression-tested.

The combined focused suite passed 196 tests and 73 subtests. It covers core live,
final-input identity, delivery boundaries/grants/receipts, independent voice
workers, reconciliation, shared admission, API/dashboard routes, roundtrip
ingress and CUDA environment setup. Starlette's existing httpx deprecation
warning remains. The legacy voice suite has five failures against pre-existing
retirement stubs; eight tests passed in that run. It is not reported as green.

RVC is optional and disabled in the intended profile. This task does not enable
RVC or claim real conversion quality. Existing conservative reconciliation tests
continue to reject release based only on incomplete RVC/Piper evidence.

## Supervised physical hardware acceptance

The Windows Edge browser used its selected physical audio endpoints through an
SSH localhost tunnel to the disposable Gamma fixture. Gamma itself has no sound
cards. The operator did not identify the selected microphone or playback device,
so this evidence is specific to that browser session, not a named-device matrix.

- The known garden phrase produced one final turn, one decision/grant and one
  completed playback receipt. The operator confirmed it was easy to hear.
- With TTS explicitly stopped and STT restarted, another physical utterance
  produced a text-only response and no playback. The operator confirmed both.
  Brain PID `809340` and generation `2deb4f9e812841909c918f2856738297` stayed
  unchanged; the restarted STT PID was `810027`.
- During the longer physical reply, transcript-confirmed barge-in changed the
  browser state to interrupted, cleared its queue and produced an `interrupted`
  receiver receipt. The operator confirmed the original reply stopped and did
  not resume during the ten-second listening interval.
- A further STT restart (PID `810886`) preserved the same brain identity. After
  browser reload and live receiver reconnection, the queue remained empty and
  the audio element was paused at zero with no source. No stale reply resumed.
- There was one final per test utterance. An additional initial microphone
  utterance was recorded as a separate turn, not a duplicate final. Physical
  transcription was not uniformly exact: the TTS-stopped capture omitted the
  beginning of the requested phrase and the longer phrase had word substitutions.
  This acceptance establishes routing, lifecycle and interruption, not zero
  transcription error across microphones or acoustic conditions.

The physical completed trace joins utterance `8c760dab5752445fbf910114e5d3c191`,
decision `ffbe00f200c14799aa2fe3a55c8f74f8`, grant
`7a55ad3faebb416a8556f6f07e93ebaa`, and completed receipt event
`92b8bbad5c7d4322b7529c9954b73f3e`.

The text-only turn is `eeaadf5a6f014f75bd7596917b6cabf9`, with
`playback_expected=false`. The interrupted turn is
`33ab1d0a9da0491e9faa3625d7400529`, decision
`474667d5400142a1ab233396a73f5c2e`, grant
`db7e3d21959445d8b17333b4cb3bdb49`, and interrupted receipt event
`f9e938d84cb84232a67ea90b8a02415b`. Processing status remains completed;
the receiver receipt records the subsequent playback interruption.

Content-free hardware evidence is in `data/runtime/voice-hardware-evidence.json`.
The private fixture exited successfully, removed its temporary audio/configuration
and preserved production service PIDs. The microphone session and owned tunnel
were stopped. No raw microphone recording or private reference was exported.
No commit, push, deployment, production restart or existing machine-local
configuration change was performed. Enabled RVC remains outside this evidence.

## Final trace and verification artifacts

The completed-playback trace is:

- Utterance: `3b54fec19e964e59838e2cf274d31417`
- Committed decision: `942bd161a347461e88ddd28e35194178`
- Reviewed delivery grant: `a945b2124a6e41bda7e2b1c659a4aef2`
- Receiver receipt: `completed`

The interrupted trace is:

- Utterance: `027e061b9a034bd68d703cabd9a0241b`
- Committed decision: `c1b69cad6baa4572bcc0f5ac3a58035d`
- Delivery grant: `569618c9fbc94a0088413db71e66527e`
- Receiver receipt: `interrupted`

Content-free IDs and measured synthetic-fixture results are retained in
`data/runtime/voice-acceptance-evidence.json`. Full local verifier and test logs
are `data/runtime/voice-acceptance-final3.log` and
`data/runtime/voice-acceptance-tests-final.log`. The task-only review diff is
`data/runtime/voice-acceptance-changes.diff`. These ignored artifacts are not
committed. JavaScript syntax and `git diff --check` passed.

Files changed by this task:

- `src/gamma/conversation/service.py`
- `src/gamma/voice/core_live.py`
- `src/gamma/voice/live.py`
- `src/gamma/voice/stt_runtime.py`
- `src/gamma/schemas/voice.py`
- `src/gamma/api/routes.py`
- `tests/test_core_live.py`
- `tests/test_live_turn_identity.py`
- `tests/test_delivery_boundary.py`
- `tests/test_stt_runtime.py`
- `tests/test_playback_leases.py`
- `scripts/verify_voice_acceptance.py`
- `scripts/verify_voice_acceptance.cjs`
- `specs/modular_voice_acceptance.md`
- `specs/modular_worker_runtime_progress.md`
- `specs/current_implementations.md`
- `specs/live_voice.md`
- `specs/voice.md`
