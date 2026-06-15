# Audio Understanding Plan

## Status

Implementation plan for adding speaker-affect and non-speech audio-event
understanding to Gamma.

Initial implementation state:

- shared audio normalization supports WAV and FFmpeg-readable compressed input
- basic signal-level prosody analysis consumes normalized mono 16 kHz PCM
- audio-event label normalization, confidence filtering, timestamp merging,
  minimum duration, and cooldown policy are implemented
- STT remains transcript-only
- Hugging Face adapters are implemented for:
  - `superb/wav2vec2-base-superb-er` speaker emotion
  - `MIT/ast-finetuned-audioset-10-10-0.4593` audio events
- both model-backed adapters remain disabled by default

This document is the reference for implementation work in this area.

## Goals

Gamma should be able to attach cautious, structured audio observations to a
voice turn:

- speaker delivery and probable emotion
- non-speech events such as laughter, coughing, and clapping
- timestamps and confidence values
- analyzer identity and latency

These observations may help conversation tone and stream policy, but must not
be treated as user-stated facts.

## Non-Goals

- identifying a person from their voice
- diagnosing physical or mental health
- inferring intent, truthfulness, consent, or danger from emotion alone
- storing transient detected emotion as durable user memory
- making STT responsible for general audio classification

## Architecture

Audio understanding is a parallel layer beside STT:

```text
audio upload
  -> shared audio normalization
  -> STT adapter
  -> prosody and speaker-emotion adapter
  -> audio-event adapter
  -> VoiceInputContext
  -> live event metadata and bounded conversation context
```

STT continues to return transcription. Audio-understanding adapters must remain
replaceable and independently configurable.

## Data Contract

`VoiceInputContext` contains:

- `speaker_affect`
  - probable emotion or `unknown`
  - confidence
  - energy, pace, and delivery labels
  - analyzer source
- `events`
  - normalized label
  - confidence
  - start and end timestamps
  - analyzer source
- low-level features useful for diagnostics
- analyzer version, timing, and failure detail

The context is optional on transcription, roundtrip, and live-job responses so
existing clients remain compatible.

## Supported Labels

The initial speaker-emotion vocabulary is:

- `neutral`
- `happy`
- `sad`
- `angry`
- `fearful`
- `surprised`
- `disgusted`
- `uncertain`
- `unknown`

The initial audio-event allowlist is:

- `laughter`
- `cough`
- `sneeze`
- `clapping`
- `crying`
- `sigh`
- `gasp`
- `throat_clearing`
- `music`
- `alarm`
- `door_knock`

Provider-specific labels must be normalized to this vocabulary before leaving
the adapter.

## Behavioral Policy

- Low-confidence observations remain diagnostic metadata only.
- Speaker-emotion inference requires a non-empty STT transcript by default.
- Prompts use bounded summaries, never raw model output.
- Prompt summaries explicitly state that observations are uncertain and are
  not user-stated facts.
- A single cough, sigh, or low-confidence event should not force a response.
- Laughter may affect response tone when confidence is high.
- Repeated alarms or distress-like events may trigger a cautious check-in, but
  never an assertion about an emergency.
- Detected affect and events must not be written to durable memory.
- Analyzer failure must not fail STT or the voice turn.
- Sound-only live turns with approved events are recorded as `audio_event`
  stream inputs and do not force conversation generation.

## Configuration

Portable configuration belongs in `config/app.example.toml`:

```toml
audio_understanding_enabled = true
audio_understanding_prompt_enabled = false
speaker_emotion_provider = "disabled"
speaker_emotion_model = "superb/wav2vec2-base-superb-er"
audio_event_provider = "disabled"
audio_event_model = "MIT/ast-finetuned-audioset-10-10-0.4593"
audio_analysis_device = "cpu"
audio_model_local_files_only = false
speaker_emotion_min_confidence = 0.65
audio_event_min_confidence = 0.70
audio_event_labels = ["laughter", "cough", "clapping"]
audio_event_min_duration_ms = 100
audio_event_merge_gap_ms = 250
audio_event_cooldown_ms = 500
audio_analysis_max_seconds = 30
audio_analysis_timeout_ms = 500
```

Machine-specific model paths and GPU selection belong in
`config/app.local.toml`.

## Delivery Phases

### Phase 1: Contracts And Propagation

- Status: implemented
- add public schemas for affect, audio events, and voice input context
- add an `AudioUnderstandingService` with injectable adapters
- preserve the existing lightweight prosody analyzer
- expose audio context in transcription, roundtrip, and live-job payloads
- attach audio context to live `mic_transcript` event metadata
- add a confidence-gated prompt-summary builder
- keep prompt use disabled by default

### Phase 2: Shared Audio Normalization

- Status: implemented
- decode WAV, WebM/Opus, OGG, MP3, and M4A
- produce mono 16 kHz float PCM once per turn
- reuse normalized samples across STT-supporting analysis adapters
- enforce duration, size, and timeout limits
- remove the current WAV-only limitation from prosody analysis

### Phase 3: Audio-Event Model

- Status: Hugging Face AST backend and postprocessing implemented;
  production rollout pending evaluation and persistent model hosting
- benchmark YAMNet, PANNs, and suitable ONNX AudioSet classifiers
- select a local backend based on accuracy, dependency size, and latency
- implement windowed inference and adjacent-event merging
- apply per-label thresholds, minimum durations, and cooldowns
- run in diagnostics-only mode first

### Phase 4: Speaker-Emotion Model

- Status: Hugging Face SUPERB wav2vec2 backend implemented; production rollout
  pending evaluation and persistent model hosting
- benchmark wav2vec2, HuBERT, and suitable ONNX emotion classifiers
- evaluate using real Gamma microphone conditions
- normalize model labels to Gamma's vocabulary
- return `uncertain` below the configured confidence threshold
- run in diagnostics-only mode first

### Phase 5: Policy And User Experience

- enable bounded prompt context after evaluation
- support non-speech-only stream events for approved labels
- add dashboard diagnostics and configuration controls
- expose provider health and analysis latency
- document operator guidance and known limitations

## Evaluation

Maintain an ignored local evaluation audio set with a tracked manifest. Measure:

- per-label precision, recall, and false-positive rate
- speaker-emotion confusion matrix
- false audio events triggered by ordinary speech
- CPU and GPU latency
- added end-to-end live-turn latency
- behavior when audio is noisy, clipped, quiet, or contains multiple speakers

Model-backed providers must not be enabled by default until:

- representative microphone recordings have been evaluated
- thresholds are documented
- false-positive behavior is acceptable
- live-turn latency remains within the configured budget

## Current Model Evaluation

Selected initial model cards:

- `https://huggingface.co/superb/wav2vec2-base-superb-er`
- `https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593`

Ollama was evaluated as an integration option, but the installed models and
current Ollama interface do not expose a dedicated raw-audio classification
contract. Dedicated Hugging Face classifiers are therefore the initial
backend.

Synthetic one-second audio smoke results on the current CPU environment:

- speaker emotion cold cached load plus inference: approximately 2.0 seconds
- speaker emotion warm inference: approximately 257 milliseconds
- AST events cold cached load plus inference: approximately 1.5 seconds
- AST events warm inference: approximately 1.32 seconds
- end-to-end cached CLI run with both models: approximately 2.45 seconds

The emotion model assigned high-confidence `neutral` to a synthetic sine wave
when a transcript was supplied manually. Speaker-emotion output therefore needs
reliable speech-presence gating and real microphone evaluation before it can
influence conversation behavior.

Gamma's live worker is currently a fresh process per turn. Enabling both
Hugging Face providers there reloads both models for every turn, which is not
acceptable for the normal low-latency path. A persistent loopback sidecar is
implemented and managed independently by the supervisor. Deployment and GPU
placement are defined in `audio_understanding_deployment_proposal.md`.

After models have been downloaded, set `audio_model_local_files_only = true`
for deterministic offline runtime behavior.

## Test Requirements

- schema compatibility tests
- adapter and provider-selection tests
- confidence-gating tests
- analyzer failure-isolation tests
- live-worker metadata propagation tests
- transcription and roundtrip response tests
- event-merging and threshold tests when a model backend is added
- WAV and compressed-browser-audio normalization tests in Phase 2

## Completion Criteria

The feature is complete when Gamma can reliably produce timestamped,
confidence-scored speaker-affect and approved non-speech events, expose them to
operators, and use them through conservative policy without making voice turns
less reliable.
