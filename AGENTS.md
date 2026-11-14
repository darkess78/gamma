# Gamma Agent Guide

This file is the operating guide for coding agents and LLMs working in this
repository. Follow it before making changes.

## First Reads

Read only the material relevant to the task, but start with:

1. `README.md` for setup, providers, and runtime commands.
2. `specs/README.md` for the specification hierarchy.
3. `specs/current_implementations.md` for implemented behavior.
4. The domain spec for the area being changed.

For networking, Nginx/OpenResty, service ports, public URLs, dashboard startup,
or proxy routing, read this protected source first:

```text
specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md
```

## Protected Files

Never edit, rename, delete, replace, regenerate, chmod, unlock, or remove the
immutable attribute from:

```text
specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md
```

Agents may read and cite that file. Proposed deployment architecture changes
must go into a separate document and require explicit human approval.

Do not edit `.env`, `config/app.local.toml`, or `config/voices.local.toml`
unless the user specifically requests a machine-local configuration change.
Never expose secrets from those files in output, tests, commits, or logs.

## Repository Shape

Gamma uses a `src/` Python package layout:

```text
src/gamma/main.py                 Shana FastAPI application
src/gamma/api/routes.py           Shana API routes
src/gamma/dashboard/main.py       Separate dashboard FastAPI application
src/gamma/dashboard/static/       Browser dashboard assets
src/gamma/conversation/           Conversation orchestration
src/gamma/voice/                  STT, TTS, roundtrip, and live voice
src/gamma/memory/                 SQLite/SQLModel memory
src/gamma/llm/                    Provider adapters and routing
src/gamma/performer/              Output event bus and performer views
src/gamma/stream/                 Stream decision and output pipeline
src/gamma/integrations/           Twitch and Discord adapters
src/gamma/supervisor/             Background process management
config/                           Layered shareable configuration
tests/                            Pytest suite
scripts/                          Platform launch and service scripts
deploy/                           Nginx and systemd templates
specs/                            Architecture and behavior sources
data/                             Ignored runtime state and generated output
```

The untracked top-level `gamma/` directory is stale runtime bytecode, not source
code. Never import from it, add files to it, or treat it as authoritative.
Imports must resolve to `src/gamma`.

## Runtime Boundaries

Gamma intentionally runs two applications:

```text
Shana API:  port 8000
Dashboard:  port 8001
```

Do not merge them or assume their routes are interchangeable.

Dashboard-owned routes include:

```text
/dashboard*
/api/*
/static/*
/login
/logout
/monitor*
/overlay/*
```

Shana-owned routes include:

```text
/health
/v1/*
/performer*
/stream/*
```

Public HTTPS and internal bind addresses are separate configuration concepts.
Preserve `SHANA_*_PUBLIC_SCHEME`, `SHANA_*_PUBLIC_HOST`, and
`SHANA_*_PUBLIC_PORT` when changing URL generation.

## Python Environment

Use the repository virtual environment:

```bash
.venv/bin/python
```

Do not use the system Python for normal repository commands. If dependencies
are missing:

```bash
.venv/bin/python -m pip install -e '.[dev]'
```

Confirm source resolution when import behavior is suspicious:

```bash
.venv/bin/python -c 'import gamma; print(gamma.__file__)'
```

The result must point inside `src/gamma`.

## Configuration Model

Application configuration precedence:

```text
config/app.example.toml
config/app.toml
config/app.local.toml
.env
process environment
```

Voice configuration precedence:

```text
config/voices.example.toml
config/voices.presets.toml
config/voices.toml
config/voices.local.toml
```

Use tracked example/shared files for portable defaults. Use ignored local files
for machine-specific paths, credentials, GPU selection, and provider secrets.
Do not silently move a local override into tracked configuration.

## Engineering Rules

- Inspect existing code and tests before choosing an implementation.
- Match established patterns before adding abstractions or dependencies.
- Keep changes scoped to the requested behavior.
- Do not overwrite or revert unrelated dirty-worktree changes.
- Treat new files and modifications you did not create as user-owned.
- Keep shared runtime code cross-platform unless the file is explicitly
  platform-specific.
- Use `pathlib.Path` for filesystem paths and structured parsers for TOML,
  YAML, JSON, and URLs.
- Avoid blocking work in async request handlers. Follow existing thread/process
  isolation patterns for STT, LLM, TTS, and live voice jobs.
- Preserve API response schemas and browser route contracts unless the task
  explicitly requires a migration.
- Never weaken authentication, safety filters, privacy guards, or stream
  moderation merely to make a test pass.
- Do not commit runtime databases, generated audio, logs, models, credentials,
  or other contents of `data/`.

## Dashboard Changes

The dashboard is a separate application with modular JavaScript files.

When changing dashboard behavior:

- Update the relevant module in `src/gamma/dashboard/static/`.
- Do not restore logic to the legacy monolithic `dashboard.js`.
- Preserve HTTPS-compatible and browser-reachable URL handling.
- Account for WebSocket routes, browser microphone secure-context
  requirements, and reverse-proxy headers.
- Test both page HTML routes and `/api/*` routes.
- Run JavaScript syntax checks for every changed JavaScript file:

```bash
node --check src/gamma/dashboard/static/<changed-file>.js
```

## Tests And Validation

Run the narrowest relevant tests during development, then broaden based on
blast radius.

Common focused suites:

```bash
.venv/bin/python -m pytest tests/test_dashboard_routes.py tests/test_api_routes.py -q
.venv/bin/python -m pytest tests/test_live_voice_runtime.py -q
.venv/bin/python -m pytest tests/test_stream_brain.py tests/test_stream_output.py -q
.venv/bin/python -m pytest tests/test_memory_service.py -q
.venv/bin/python -m pytest tests/test_llm_router.py -q
```

Full suite:

```bash
.venv/bin/python -m pytest -q
```

Before finishing:

- Run `git diff --check`.
- Report tests that ran and their result.
- Report tests that could not run and why.
- For service or proxy changes, verify actual HTTP endpoints rather than only
  configuration syntax.

## Service Operations

Use the supervisor for Shana and dashboard lifecycle:

```bash
.venv/bin/python -m gamma.supervisor.cli status all
.venv/bin/python -m gamma.supervisor.cli restart shana
.venv/bin/python -m gamma.supervisor.cli restart dashboard
```

NPM-facing local proxy:

```bash
./scripts/start_local_proxy.sh
./scripts/stop_local_proxy.sh
```

Do not kill broad process groups or use destructive cleanup commands when the
supervisor can target the service.

System Nginx changes require:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Never reload after a failed syntax test.

## Git And Generated State

The worktree may already be dirty. Do not reset, checkout, clean, delete, or
reformat unrelated changes.

Before editing, inspect:

```bash
git status --short
```

Do not add these to version control:

```text
.env
.venv/
data/*
config/app.local.toml
config/voices.local.toml
top-level gamma/
Python caches and generated logs/audio
```

## Completion Standard

A task is complete only when:

1. The requested behavior is implemented.
2. Relevant tests pass.
3. Runtime behavior is verified when services or networking are involved.
4. Specs or examples are updated when the public contract changes.
5. Protected deployment rules remain intact.
6. The final report states what changed, what was verified, and any remaining
   operational requirement.
