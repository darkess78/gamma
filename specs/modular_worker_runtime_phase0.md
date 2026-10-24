# Modular runtime baseline

Date: 2026-09-19. This records Phase 0 evidence for the first lifecycle/log slice.
It does not establish the proposed persistent brain, single state writer, or
independent delivery pipeline.

## Source and isolation

Windows source HEAD was `0da6caa6d33c8cc9ba8c7169981c18762da6ff9f`.
Gamma PC source HEAD was `38e3fbff4796e0c8ed56a2fcda9836d7a8e2273a`.
Both contained existing modifications to AGENTS.md, README.md, the deployment
reference, specification index, architecture, and Minecraft implementation plan,
plus the untracked worker proposal and audit. None were reverted.

Implementation uses a detached worktree at
`/home/neety/Documents/gamma-modular-phase1`, based on Gamma PC HEAD. The existing
documentation diff and both proposal files were copied from Gamma PC. Its venv
links to `/home/neety/Documents/gamma-main/.venv`; `PYTHONPATH=$PWD/src` pins
imports to the isolated source. Python reported
`/home/neety/Documents/gamma-modular-phase1/src/gamma/__init__.py`.
No private databases, local configuration, credentials, or coursework data were
copied into test state. Production source was not replaced.

The source diff between the two HEADs covers dashboard service/index,
`llm/capabilities.py`, and `llm/local_adapter.py`, with 62 additions and 13
deletions. These Gamma changes remain in the implementation baseline.

## Conversation and state ownership map

| Entry or constructor | Existing ownership and writes |
| --- | --- |
| `api/routes.py:get_conversation_service`, `get_memory_service` | Separate lazy conversation and memory objects in the API process; direct operator memory routes remain separate writers. |
| `conversation/service.py:ConversationService.__init__` | Builds MemoryService, AssistantStateStore, EmotionMemoryService and ContinuityService. Response completion and the fast-mode background path can persist conversation-derived state. |
| `stream/brain.py:StreamBrain.__init__` | Builds a conversation service unless supplied; owns stream processing, output and temporary stream state alongside conversation writes. |
| `presence.py` wake handling | Can construct a conversation service and memory service; Presence also persists its own audience/mode/runtime state. |
| `run_live_voice_worker.py:main` | Each subprocess constructs conversation and StreamBrain state; this is an independent writer, not the persistent API brain. |
| `voice/controller.py`, `voice/roundtrip.py` | Construct or accept conversation services and perform turns through them. |
| `run_llm_test.py`, `run_voice_roundtrip.py` | Standalone commands construct conversation services and can touch configured state. Do not use production data for baseline fixtures. |
| `identity/resolver.py` | Constructs MemoryService for identity resolution. This reader/service construction must be included when extracting ownership. |
| `tools/builtin.py` | Four MemoryService constructors; memory tools can perform direct authorized writes. Tool execution remains inside the existing conversation path in this slice. |
| `persona/loader.py`, `system/status.py` | Construct emotion/memory service readers. Construction must not become an implicit recovery or mutation boundary. |
| `memory/continuity.py:ContinuityService.__init__` | Calls `mark_pending_interrupted` on construction. Recovery is currently database-wide and is a specific Phase 2 hazard. |
| `memory/service.py`, `persona/assistant_state.py`, `persona/emotion_service.py` | Persistence implementations reachable through the above objects. No single cross-process authority or versioned decision/outbox transaction yet. |
| `voice/live_jobs.py`, `performer/turns.py`, `stream/output.py` | Persist or mutate job, presentation and delivery state. Synthesis events can still be interpreted as speaking or completion before actual playback acknowledgement. |
| `stream` temporary state/self-goal stores, integration worker state, `improvement/work_queue.py` | Domain-owned state remains separate. Improvement experiments use isolated state; this slice does not authorize them to mutate live assistant state. |

This map combines the source audit with current constructor and singleton
searches. It is a migration inventory, not a claim that service construction
has already been consolidated.

## Baseline verification

Before runtime edits, the focused Gamma-PC suite passed **191 tests and 73
subtests** in 5.40 seconds. It covered dashboard/API routes, observability,
conversation pipeline/continuity, live voice runtime, stream brain/output, and
Presence. No failures occurred in this baseline. The existing FastAPI TestClient
dependency emitted a Starlette deprecation warning.

Representative existing fixtures were run twice against the unmodified source:

| Fixture | First invocation | Repeated invocation | Module import |
| --- | ---: | ---: | ---: |
| Structured text-only turn, no TTS | 21.47 ms | 11.95 ms | 636.78 ms |
| Simple chunked voice through StreamBrain | 0.50 ms | 0.20 ms | 1105.45 ms |
| Mic stream event to reply/output | 1.23 ms | 0.89 ms | 7.61 ms |

These are synthetic contract fixtures with mocked inference/audio, not measured
GPU model cold/warm latency or live microphone/playback evidence. All six runs
passed. Live model startup, GPU residency/concurrency, real audio and avatar
baselines remain necessary before their respective later phases. No model was
unloaded or production service restarted to obtain these numbers.

Production Shana PID was 2222 and dashboard PID was 4615. Both retained those
PIDs through Phase 1 acceptance. The original Gamma-PC tracked documentation
diff was byte-compared after the work and remained unchanged.
