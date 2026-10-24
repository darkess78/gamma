# Bounded silent autonomy

Implemented in `/home/neety/Documents/gamma-modular-phase1`. This is an isolated
worktree change. It does not enable production autonomy, change local configuration,
restart production services, or grant speech or external-action permission.

## Work selection and observations

The existing proactive scheduler now supplies `SilentCycles` with `SilentWork`.
The selector reads up to 32 recent approved goals from `StreamSelfGoalStore`.
Proposed, rejected and cleared goals are ineligible. Relevant fresh observations
take precedence; otherwise a goal receives one internal preparation step per
explicit enable operation. New observations may wake another step for that goal.
An empty eligible set reports `no_work` and consumes no inference budget.

Authenticated `POST /v1/autonomy/silent/observations` accepts `goal_id`, `key`,
`text` and optional `ttl_seconds`. The referenced goal must already be approved.
This is an input adapter for bounded observations, not permission to execute their
contents. Observation text remains untrusted data in the model prompt.

The in-memory inbox holds at most 32 observations. Keys are limited to 80
characters, text to 2000 characters and TTL to 1 through 3600 seconds, default
120. A matching goal/key replaces the older value and expiry. At capacity, a new
key evicts the oldest retained value. Status reports depth, capacity, coalesced,
expired and dropped counts, plus an overload flag. Status contains no observation
text. Selection removes at most four matching observations. Both selection and
status prune expiry. Results whose selected observations expire during inference
are rejected at commit. Stop clears the inbox. Restart drops this disposable input.

Adapters for continuous desktop capture or new game sources are outside this
slice; they can submit observations through the authenticated API.

## Attention and controls

Existing enable and disable APIs remain. Enable accepts integer `budget` from 1
through 8, `cooldown_seconds` from 5 through 3600 and `cycle_seconds` from 1
through 60. Defaults are 1, 60 and 60. Each attempted preparation consumes one
cycle, including failures. No-work and policy suppression consume none. Completed
or failed attempts establish the cooldown. Startup remains disabled.

`POST /v1/autonomy/silent/pause` invalidates the current cycle generation and
holds the remaining budget. Resume permits later cycles without resetting budget
or cooldown. Disable invalidates the generation, clears observations and prevents
further cycles. Enable cannot replace an active attempt. Brain Pause and Stop
also invalidate silent work; brain Resume resumes its held budget. Sleep, Break,
quiet hours and the existing resource gate still suppress internal inference.
Listeners, TTS availability and public-output permission are not prerequisites.

The shared brain queue reserves one waiting slot against background saturation.
Every accepted foreground submission advances an attention generation. Silent
work starts only when no foreground job is pending. Its commit shares a short
queue guard with foreground admission, Pause and Stop, so an owner arrival before
commit discards the prepared result. General queue fairness remains; if fairness
selects a silent tick while foreground work is pending, that tick yields without
calling the model. Queue status includes capacity, rejection count and generation.

An already running provider call is not assumed cancellable. Its execution slot
stays occupied until it returns or the provider's existing failure protocol takes
over. The cycle deadline rejects late results; it does not falsely report freed
provider capacity. Controls remain available while inference runs. Pending owner
input executes after the active call drains, before further silent inference.

Silent generation has a fixed 512-token output cap at the local and hosted
adapter boundaries. The persistent model protocol carries this restriction.
Ollama silent calls disable thinking output. Existing prompt-size and context
bounds still apply. No previous internal result is appended into an unbounded
self-generated prompt loop.

## Approved internal results

Cognition uses the existing persistent brain and model workers. It runs in
evaluation mode with tools, speech and delivery disabled. The prompt requests the
useful conclusion itself, such as a comparison or missing fact, never private
reasoning or a statement merely saying analysis finished.

Core commits only a bounded summary, selected goal ID, observation keys, silent
delivery marker and false external-action marker. The goal's approval and exact
selected fields are rechecked inside the existing state-authority transaction.
Presence, process generation and foreground cancellation fences also apply.
Model-proposed tools, memory candidates and arbitrary state updates are not
applied by this path.

The result is recorded in the `internal_only` decision outbox payload and in the
`autonomy:silent` working-state checkpoint. No assistant speech journal entry or
delivery grant is created. The authenticated status API exposes the latest
approved result, budget, generation and bounded reason code. The existing
continuity API can read the persisted working state after process restart.
In-memory enabled status and permission to communicate never resume from a result.

## Evidence and re-run

`tests/test_silent_autonomy.py` checks approved selection, no-work behavior,
10,000-observation saturation, owner reservation, stalled-provider controls,
approval revocation, expiry during inference and durable results without grants.
The observation tests include the reviewed local-Qwen worker proposal and the
corrected acceptance assertion. Existing queue, process, continuity and scheduler
tests cover compatibility and generation ownership.

`scripts/verify_silent_autonomy.py` launches a disposable authenticated API with
its actual persistent brain and inference workers. A scripted HTTP provider
makes the concurrency checks repeatable. It stops the disposable TTS worker,
checks zero subscribers, submits approved fictional work, reads the resulting
SQLite state and checks zero grants. With inference stalled, 40 additional keys
retain 32 observations and report eight drops; Pause/Disable stay responsive,
late work is discarded and pending owner input completes.

The optional real-model check uses an already installed Ollama model. It refuses
to evict another resident model and holds the normal shared model reservation
around the entire disposable run. Its inner admission ledger is test-only. This
proves sequential cognition without changing production admission policy or
claiming spare concurrent GPU capacity. Provider failures retain uncertain
capacity unless a complete provider response proves completion.

```bash
cd /home/neety/Documents/gamma-modular-phase1
PYTHONPATH=$PWD/src .venv/bin/python -m pytest tests/test_silent_autonomy.py tests/test_silent_cycles.py tests/test_silent_preparation.py tests/test_silent_observations.py -q
PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_silent_autonomy.py
PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_silent_autonomy.py --model qwen3.5:4b
git diff --check
```

The production admission default still reserves the sole model slot for
interactive work. Enabling production background model capacity requires the
separate measured admission-policy operation. This task does not perform it.

## Observed checkpoint

- Full suite: 1238 passed, 3 skipped, 100 subtests. After the final freshness
  test and prompt refinement, the focused silent suite passed 53 tests.
- Scripted HTTP acceptance: useful persisted comparison; zero delivery grants;
  no playback subscribers; TTS stopped; 32 retained observations and eight drops;
  Pause/Disable response in 0.002 seconds while inference was stalled; owner
  request completed and cancelled work produced no second internal commit.
- Real `qwen3.5:4b` inference on Gamma PC: a silent cycle completed in 6.05
  seconds and persisted the correct comparison of eight versus three bolts,
  including the difference of five. The actual provider request carried
  `num_predict=512` and `think=false`. TTS remained stopped and no grants or
  assistant speech entries were created. The same brain process handled the
  cycle and subsequent status/control requests.
- Real inference used a temporary Ollama process on GPU 0 after checking at
  least 8 GiB free, the existing read-only model cache, a private loopback port
  and the shared model reservation. Production Ollama/API/dashboard processes
  were not restarted. The temporary provider and its runners were reaped.
- The first real-model response reported that analysis had completed without
  stating its finding. The strengthened internal-result instruction corrected
  that failure. A CPU-only cold attempt exceeded the acceptance deadline; this
  is not evidence of CPU latency acceptance. No CPU timeout granted output.
  Test reservations were released only after a complete response, an explicit
  provider rejection, or verified termination of the owned provider and runners.

The focused verifier accepts `--endpoint` for a separately managed temporary
provider and `--cpu` to request CPU inference explicitly. The measured successful
checkpoint used GPU inference, not the CPU path. Concurrent production capacity
and general cold/warm model performance remain outside this check.

Final focused validation after the foreground-during-selection race check:
68 passed across silent and brain queue tests. The scripted HTTP verifier passed
again with 0.002-second controls. The existing brain-process HTTP verifier passed
all lifecycle, text, live, stream, silent, crash/restart and unchanged-production
checks after its disposable live fixture selected Wake and seeded an approved
goal. `git diff --check` passed. No silent-acceptance reservations remained
unresolved. All application tests and inference ran on Gamma PC.

## Files changed by this task

Existing concurrent delivery edits were preserved with baseline comparisons and
three-way integration. No files in either original checkout were edited by this
task. The task's source, test and specification files are:

```text
src/gamma/brain/observations.py
src/gamma/brain/silent_work.py
src/gamma/brain/silent.py
src/gamma/brain/queue.py
src/gamma/brain/model_runtime.py
src/gamma/brain/model_worker.py
src/gamma/proactive.py
src/gamma/api/routes.py
src/gamma/conversation/service.py
src/gamma/memory/continuity.py
src/gamma/llm/local_adapter.py
src/gamma/llm/openai_adapter.py
tests/test_silent_autonomy.py
tests/test_silent_observations.py
scripts/verify_silent_autonomy.py
scripts/verify_brain_process.py
specs/modular_worker_runtime_silent_slice.md
specs/modular_worker_runtime_progress.md
specs/modular_worker_runtime_brain_process.md
specs/current_implementations.md
specs/shana_presence.md
specs/README.md
```
