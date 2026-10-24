# LLM Routing

Status: Current with experimental extensions
Last verified: 2026-06-22

## Providers

- `mock` for safe tests
- `openai` for hosted generation and vision
- `local`/`ollama` for Ollama-compatible generation

Adapters implement the shared LLM interface and accept optional call context,
model override, endpoint override, and image inputs.

## Router Behavior

The router classifies calls by purpose rather than inspecting arbitrary prompt
content for agentic behavior. Current families include persona conversation,
heavy persona work, lightweight chat, metadata/tagging, voice helpers, tool
finalization, and vision.

Routing considers:

- configured profile and default provider/model
- fast/light input thresholds
- persona-sensitive fallback restrictions
- provider capability, especially vision
- provider failure backoff
- optional hosted escalation
- optional resource-routing endpoint advice
- configured model context/persona capability metadata

`presence_wake` is a dedicated persona-sensitive, primary-quality route. Its
short event instruction never selects the light path by itself. Each fallback
receives a prompt rebuilt for that candidate's usable context budget, and
models below the required context/persona capability are skipped.

## Context Budgeting

Model capability records distinguish advertised context, configured usable
input, an optional model-specific runtime context, reserved output, safety
margin, persona/vision/tool capability, and provenance. Prompt assembly budgets
the complete request for each candidate. When a local model has a configured
runtime context, the Ollama adapter sends that value explicitly as `num_ctx`;
models without one continue to use their existing Ollama defaults.
Persona, boundaries, privacy/safety rules, current input, and current speaker
are mandatory; working state, summary, memories, recent turns, and background
are compacted in priority order.

Provider context-overflow responses are typed separately from availability
failures. They do not create provider backoff. The router rebuilds once under
a smaller candidate budget and then selects only a compatible fallback.

Every routed call records provider, model, purpose, fallback attempts, timing,
prompt estimate, context limit/reserves, compaction, failure/backoff state, and
optional placement metadata. Failed and skipped candidates also carry a stable,
non-content `error_class` (`context_budget`, `capability_mismatch`,
`provider_unavailable`, `context_overflow`, or `provider_error`) so aggregate
analysis does not have to parse free-form detail strings.

## Product Rules

- the primary model remains responsible for persona and orchestration
- specialist/light models are subordinate helpers
- routing failure must fall back predictably or return a clear service error
- experimental resource placement may not silently change provider/model policy
- new model families require route and fallback tests rather than dashboard conditionals

## Staged shared-backend admission

Cooperating Gamma local generation callers share OS-backed admission under the
same account home at `.cache/gamma/inference-admission`. It is independent of
checkout, project/data directories and TMPDIR. LocalLLMAdapter and enabled
SpeechLLMReviewer calls register foreground waiters before waiting for a lease.
LocalPracticeLearner chat/generate requests are background work, including
repository planning, checker calls and practice. An active coding transport
finishes; a registered foreground waiter then prevents any new background
transport from winning that backend. Independent endpoints and non-generation
work can continue. The lease remains held through response read and JSON decode.

Resource identity uses normalized scheme, hostname, effective port and base path,
not model name. Credentials, query strings, prompts, job content and raw endpoint
URLs are absent from lock records; filenames contain a hash and optional random
waiter token. Same-account/same-home callers on the same filesystem cooperate.
Different DNS/address aliases, users, hosts or home directories are not unified.
This is not GPU scheduling or control of arbitrary external inference processes.
Existing advisory routing reservations remain advisory.

Admission waits have bounded deadlines, separate from transport timeouts.
Background owner pause/stop and authorized budget checks remain effective while
waiting and are rechecked before dispatch. Candidate and resident-model guards
remain conservative; admission does not evict a resident model or relax idle
practice policy. Pausing does not hold an active backend lease awaiting resume.
An already-running request is not forcefully preempted or replayed.

Exceptions and interruptions release client ownership, and OS locks recover when
a process exits. Abandoned foreground markers are detected by their released OS
locks, without guessing PID liveness. A timeout or process exit does not establish
that upstream inference stopped or completed. There is no automatic retry.
An outer publication-review timeout may return before its held reviewer transport
ends; the accepted publication gate still withholds the original response.

Threaded and separate-process synthetic transport checks on Gamma validate this
staged ordering and cleanup. Linux execution is verified; the Windows lock branch
has source review only. Production activation, audible playback, live inference
quality and actual GPU contention are not established by these checks.
