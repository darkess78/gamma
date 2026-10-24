# Worker refactor source audit

Date: 2026-09-19
Scope: Source inspection, not runtime or integration acceptance.

The Windows checkout was at `0da6caa`; Gamma PC was at
`38e3fbff4796e0c8ed56a2fcda9836d7a8e2273a`. Gamma PC was clean before the
documentation changes. Its additional source changes include LLM capability and
local adapter handling and dashboard service/UI changes. Its history also includes
coursework. Implementation must start from Gamma PC's source and preserve that
work. This audit did not inspect private runtime databases, credentials, or
conversation transcripts.

Paths below are relative to the repository root. The proposal is in
[modular_worker_runtime_proposal.md](modular_worker_runtime_proposal.md).

## Findings that determine the worker boundaries

| Area and source | Observed behavior | Refactor consequence |
| --- | --- | --- |
| `src/gamma/main.py:lifespan`, `api/routes.py:get_proactive_scheduler` | API startup constructs the scheduler, StreamBrain and performer dependencies. API routes own numerous process-local lazy singletons. | Introduce an explicit runtime composition root. Keep API availability separate from brain/model readiness. |
| `conversation/service.py:ConversationService.__init__`, `stream/brain.py:StreamBrain.__init__`, `voice/roundtrip.py:VoiceRoundtripService.__init__`, `run_live_voice_worker.py:main`, `voice/controller.py` | Several paths instantiate conversation, memory and emotional-state services. A live subprocess constructs STT, conversation and StreamBrain. | There is no single persistent brain instance across these entry points. Route them through one brain queue and explicit state owner. |
| `memory/continuity.py:ContinuityService.__init__`, `mark_pending_interrupted` | Construction invokes a database-wide scan changing pending journal entries to interrupted, without filtering by a dead worker generation. | Move recovery to controlled startup reconciliation with generation/lease ownership before adding more stateful workers. This is a source-level concurrency hazard, not a reproduced incident in this audit. |
| `conversation/service.py:_respond` | Multiple paths synthesize TTS directly; fast mode can start a background memory thread. | Extract decision, durable commit, and delivery. Background memory work needs observable jobs and receipts rather than an untracked daemon thread. |
| `proactive.py:ProactiveScheduler` | Scheduler eligibility includes output listeners, mute state and active generating/synthesizing/speaking turns. | Separate cognition admission from output admission. Preserve public Presence policy while allowing explicitly enabled silent work. |
| `voice/live_runtime.py:LiveTurnRuntime`, `voice/live_jobs.py:LiveVoiceJobManager` | Existing abstract turn API delegates to per-turn subprocesses and JSON status/output files with cancellation and history. | Reuse the public facade as the migration seam. Replace its bundled execution behind the interface rather than rewrite every browser caller. |
| `performer/turns.py:SpokenTurnStore.apply_event`, `voice/live_jobs.py:_publish_live_performer_events` | Chunk readiness sets speaking state; terminal synthesis/job status can publish speech-ended events. | Split decision status, synthesis status, and actual playback status. A generated chunk is not proof anyone heard it. |
| `dashboard/static/monitor.js` | Browser owns audio queue progression. Its audio-ended handler advances the local queue. | Add per-target playback acknowledgements and listener leases. A headless playback adapter is needed for operation independent of an open browser. |
| `performer/bus.py:PerformerEventBus` | Bus uses an in-process deque, asyncio subscriber queues and a thread lock. It is a presentation bus, not a cross-process durable job broker. | Keep its presentation contract; add explicit ingress/job transport and ownership rather than hand it to subprocesses as if it were shared. |
| `performer/vtube_studio.py:VTubeStudioAdapter._action_for_event`, `VTubeStudioRunner` | Expression/motion hotkeys have transport support. The mouth-level branch says its transport is not connected. Runner depends on the process-local bus. | Avatar boot/idle, remote subscription and real lip-sync transport need implementation and live tests. Existing classes do not establish those capabilities. |
| `supervisor/manager.py:_start_shana_dependencies`, `_stop_shana_dependencies` | Starting/stopping Shana groups audio-understanding and TTS dependency actions. Local STT is reported in-process; Ollama is external. | Replace implicit bundled lifecycle with profiles, dependency types, and ownership. Do not equate stopping a provider client with stopping its externally owned daemon. |
| `resources/coordinator.py:ResourcePlacementCoordinator`, `llm/router_adapter.py` | Routing and resource advice exist; reservations are process-local class state. The resource spec explicitly excludes managed Ollama lifecycle and automatic model eviction. | Introduce one shared admission authority before claiming cross-worker GPU limits. Reuse route policy and capabilities; loading, eviction and migration remain distinct later capabilities. |
| `safety/policy.py:SpeechSafetyPolicy.apply`, `safety/llm_reviewer.py:SpeechLLMReviewer.review` | Layered policy exists. Level `none` returns before its privacy step. The reviewer calls Ollama directly; missing/invalid action may become allow, and exceptions return soften. | Explicitly define policy profiles and mandatory checks. Route review through shared model admission; invalid/unavailable required review must not authorize delivery. Separate style controls from permission/privacy enforcement. |
| `stream/brain.py:_synthesize_with_parallel_review` | Parallel review can approve playback while review is pending, with a late-interrupt mechanism. | Delivery must hold publication until required review passes. Cancelling after playback starts cannot retract already heard speech. |
| `tools/registry.py`, `conversation/service.py:_execute_tool_calls`, `stream/actions.py:ActionPlanner` | Registry contains memory/status tools; execution calls `tool.run` directly. ActionPlanner describes tool calls/results after conversation returns. | Add a pre-execution capability broker. A post-execution action plan is an audit record, not the authority for a newly added external action. This does not imply all current tools are unauthenticated. |
| `integrations/twitch/worker.py`, `eventsub.py`, `integrations/discord/worker.py` | Separate ingestion workers, normalization, backoff and state already exist. Twitch posts normalized events to Shana. | Retain connection adapters. Give accepted ingress a fast queued receipt so chat ingestion does not wait on a full brain turn. |
| `integrations/discord/runtime.py:start`, `handle_output_event` | Runtime methods set local state and record output payloads. Specs mark actual replies/voice as disabled experimental behavior. | Show capability-level status. A running status flag or recorded event is not a delivered Discord reply. |
| `integrations/minecraft/coordinator.py`, `sidecars/minecraft/src/runtime.ts` | Versioned Python/TypeScript protocol, handshake, heartbeat, command IDs, deadlines, cancellation, emergency stop and game-state checks already exist. | Use this as a concrete cross-language adapter example. Wrap its existing contract first; do not weaken it into a generic unrestricted command runner. |
| `vision/service.py`, `voice/audio_understanding.py`, `audio_understanding_server.py` | Image analysis and optional sound/affect analysis exist. Audio understanding has a sidecar and fallback behavior. Image preparation stores artifacts. | Split capture from inference. Add observation timestamps, expiry and artifact retention; do not assume continuous desktop capture already exists. |
| `improvement/worker.py`, `work_queue.py`, `coordinator.py` | Durable work requests, safe-stage pause/stop, idle timeout, bounded cycles and isolated experiments exist. | Register improvement as an optional worker and reuse lifecycle ideas. Keep research/evaluation workloads below interactive work and outside live state/output stores. |
| `observability.py`, `conversation/service.py:_append_timing_log`, `llm/router_adapter.py:_record_route_event`, `stream/trace.py`, `stream/output.py`, `voice/live_jobs.py` | Logs, timings, routing traces, stream traces/output and live-job history use several separate files/contracts. | Add a common event envelope and compatibility readers. A collector alone will not unify existing event semantics. |
| `dashboard/main.py`, `dashboard/service.py`, `dashboard/shana_client.py` | Dashboard has authenticated lifecycle and domain proxies, but service code combines process management, configuration, metrics and log readers. | Split dashboard services by responsibility and show a common worker inventory without recreating assistant state inside the dashboard. |

## Important distinctions

- Process health, model loaded, provider reachable, worker ready, and capability
  authorized are different facts and need separate dashboard fields.
- Memory, identity, persona, Presence and safety policy are logical boundaries.
  Giving each its own LLM or process is not necessary for modularity.
- A worker can run idle without spending inference compute. Process lifetime,
  active job execution and model residency need separate controls.
- Retained output text is not retained permission to speak it after restart.
- Platform inputs, private activity summaries, public output, and evaluation data
  need separate visibility and retention policies.
- Runtime failure recovery must be scoped to the worker instance/session. A new
  object must not globally recover another still-running worker's work.

## Existing tests to preserve and extend

The inspected repository includes conversation continuity, proactive scheduler,
Presence, live voice runtime, stream output, observability, resource policy/probe,
sidecar allocations, audio sidecar runtime, VTube Studio, Twitch, Discord,
Minecraft protocol/coordinator/WebSocket/app wiring, and improvement work-queue
tests. `sidecars/minecraft/test/` contains protocol, runtime, command dispatcher,
companion, safety, and smoke-harness tests.

The corresponding test files were inventoried, not executed in this documentation
task. Existing test names do not establish independent worker acceptance. The
proposal defines additional observable concurrency and failure scenarios.

## What remains unknown

Actual cold/warm startup times, worker memory footprint, simultaneous-model GPU
capacity, interrupted-provider cancellation behavior, audio device routing,
VTube Studio authentication/hotkey readiness, and real game movement require
runtime measurements. Source inspection cannot select reliable queue sizes,
timeouts or latency targets. Set these from a controlled Gamma-PC baseline.

No claim about Neuro-sama's private implementation is needed for this design.
