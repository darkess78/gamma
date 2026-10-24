# Gamma network deployment reference

The historical filename is retained for existing links. This document is
editable and does not impose a separate architecture-approval requirement.
The owner removed that restriction on 2026-09-19. Use the deployment details
below as a baseline, verify them against the affected environment, and update
them when an authorized change alters the deployment.

Status: working and verified on June 10, 2026  
Public hostname: `gamma.neety.me`  
Gamma host: `10.78.78.13`

## Purpose

This document records the network and runtime changes that restored the Gamma
dashboard after it returned `502 Bad Gateway`, loaded stale Python code, and
generated browser mixed-content URLs.

Preserve the documented route ownership during internal refactors. A requested
routing migration must update callers, proxy configuration, and verification
together; merging ports alone is not a complete migration.

## Working Topology

Gamma has two separate FastAPI applications:

| Service | Internal listener | Responsibility |
| --- | --- | --- |
| Shana API | `0.0.0.0:8000` | `/v1/*`, performer, stream, health, and fallback routes |
| Dashboard | `0.0.0.0:8001` | `/dashboard*`, `/api/*`, `/static/*`, login, monitor, and overlays |

There are also two valid proxy entry points:

### External Nginx Proxy Manager path

```text
Browser
  -> HTTPS https://gamma.neety.me
  -> external Nginx Proxy Manager
  -> HTTP 10.78.78.13:8080
  -> repo-owned local Nginx
  -> 127.0.0.1:8000 or 127.0.0.1:8001
```

The external NPM server terminates TLS. It must forward:

```text
Scheme: http
Forward host: 10.78.78.13
Forward port: 8080
WebSocket support: enabled
```

The canonical port-`8080` route configuration is:

```text
deploy/nginx/gamma-proxy.conf
```

The repo-owned Nginx wrapper configuration and controls are:

```text
deploy/nginx/nginx.conf
scripts/start_local_proxy.sh
scripts/stop_local_proxy.sh
```

### Direct HTTPS and split-DNS path

```text
Browser
  -> HTTPS https://gamma.neety.me
  -> system Nginx on 10.78.78.13:443
  -> 127.0.0.1:8000 or 127.0.0.1:8001
```

The canonical direct HTTPS configuration is:

```text
deploy/nginx/gamma-direct-https.conf
```

It is installed as:

```text
/etc/nginx/conf.d/gamma-proxy.conf
```

Installation:

```bash
sudo install -o root -g root -m 0644 \
  deploy/nginx/gamma-direct-https.conf \
  /etc/nginx/conf.d/gamma-proxy.conf
sudo nginx -t
sudo systemctl reload nginx
```

Do not install `gamma-proxy.conf` as the system port-`443` configuration. It is
specifically the NPM-facing port-`8080` configuration.

## Required Path Routing

These routes belong to the dashboard process on port `8001`:

Coding-practice operator controls (`/api/coding/practice`,
`/api/coding/lessons/*`, and `/api/coding/idle-policy`) also belong to the
dashboard. Existing `/api/*` proxy routing and dashboard authentication apply;
no new listener or proxy change is required. Practice inference runs in a
separate module process, not an HTTP request handler.

```text
/dashboard
/dashboard/*
/api/*
/static/*
/login
/logout
/monitor
/monitor/*
/overlay/*
```

These routes belong to the Shana API process on port `8000`:

```text
/health
/performer
/performer/*
/v1/*
/stream/*
all remaining fallback routes
```

`/api/*` must not be routed to port `8000`. Doing so produces dashboard API
`404` responses even when the dashboard HTML itself loads correctly.

WebSocket upgrade headers and long read/send timeouts are required for live
voice, performer events, stream output, and other long-lived connections.

## Required Public URL Configuration

The private bind ports and public browser URLs are separate concepts.

The working `.env` values are:

```dotenv
SHANA_BIND_HOST=0.0.0.0
SHANA_PORT=8000
SHANA_PUBLIC_HOST=gamma.neety.me
SHANA_PUBLIC_SCHEME=https
SHANA_PUBLIC_PORT=443

SHANA_DASHBOARD_BIND_HOST=0.0.0.0
SHANA_DASHBOARD_PORT=8001
SHANA_DASHBOARD_PUBLIC_HOST=gamma.neety.me
SHANA_DASHBOARD_PUBLIC_SCHEME=https
SHANA_DASHBOARD_PUBLIC_PORT=443
```

The public API and dashboard bases must both render as:

```text
https://gamma.neety.me
```

They must not render as either of these:

```text
http://gamma.neety.me:8000
http://gamma.neety.me:8001
```

HTTP API URLs embedded in an HTTPS dashboard cause browser mixed-content
blocking. This can break microphone/live voice and performer requests even
though the initial dashboard page returns `200`.

`src/gamma/config.py` therefore models the Shana public scheme and public port
separately from the internal bind port.

## Python Runtime Requirements

The repository uses a `src/` package layout. Runtime imports must resolve to:

```text
/home/neety/Documents/gamma-main/src/gamma
```

They must not resolve to the untracked top-level directory:

```text
/home/neety/Documents/gamma-main/gamma
```

That top-level directory contained stale bytecode and caused the dashboard to
look for static files under `gamma/dashboard/static`, resulting in:

```text
FileNotFoundError: gamma/dashboard/static/index.html
```

The launchers now put `src` first on `PYTHONPATH`. The repo virtual environment
must also be installed in editable mode:

```bash
.venv/bin/python -m pip install -e '.[dev]'
```

Confirm the import path:

```bash
.venv/bin/python -c \
  'import gamma; print(gamma.__file__)'
```

Expected output contains:

```text
/home/neety/Documents/gamma-main/src/gamma/__init__.py
```

The dashboard shell launcher uses the repository virtualenv at:

```text
/home/neety/Documents/gamma-main/.venv
```

It must not look for `scripts/.venv`.

## Service Operations

Restart the dashboard:

```bash
.venv/bin/python -m gamma.supervisor.cli restart dashboard
```

Check the dashboard:

```bash
.venv/bin/python -m gamma.supervisor.cli status dashboard
```

Start or reload the NPM-facing local proxy:

```bash
./scripts/start_local_proxy.sh
```

Stop the NPM-facing local proxy:

```bash
./scripts/stop_local_proxy.sh
```

The relevant listeners should be:

```text
443   system Nginx direct HTTPS
8000  Shana API
8001  dashboard
8080  repo-owned NPM-facing Nginx
```

## Verification

Direct HTTPS checks:

```bash
curl -k -sS -D - -o /dev/null https://gamma.neety.me/dashboard
curl -k -sS -D - -o /dev/null https://gamma.neety.me/api/status
curl -k -sS -D - -o /dev/null https://gamma.neety.me/v1/system/status
curl -k -sS -D - -o /dev/null \
  'https://gamma.neety.me/static/dashboard.css?v=20260528b'
```

Every command above should return `HTTP/1.1 200 OK`.

NPM-facing proxy checks:

```bash
curl -sS -D - -o /dev/null \
  -H 'Host: gamma.neety.me' \
  -H 'X-Forwarded-Proto: https' \
  -H 'X-Forwarded-Port: 443' \
  http://127.0.0.1:8080/dashboard

curl -sS -D - -o /dev/null \
  -H 'Host: gamma.neety.me' \
  -H 'X-Forwarded-Proto: https' \
  -H 'X-Forwarded-Port: 443' \
  http://127.0.0.1:8080/api/status
```

Both commands should return `200`.

Check rendered public URLs:

```bash
curl -k -sS https://gamma.neety.me/dashboard \
  | grep -o 'GAMMA_\(SHANA\|DASHBOARD\)_BASE_URL = "[^"]*'
```

Expected:

```text
GAMMA_SHANA_BASE_URL = "https://gamma.neety.me
GAMMA_DASHBOARD_BASE_URL = "https://gamma.neety.me
```

Focused regression tests:

```bash
.venv/bin/python -m pytest \
  tests/test_dashboard_routes.py \
  tests/test_api_routes.py -q
```

Verified result at the time of this document:

```text
42 passed, 50 subtests passed
```

## Original Failure Causes

The outage had multiple independent causes:

1. External NPM forwarded to `10.78.78.13:8080`, but nothing listened on
   `8080`, producing `502 Bad Gateway`.
2. The active system Nginx configuration had been changed from the intended
   NPM-facing `8080` listener to a direct `443` listener.
3. The dashboard imported stale bytecode from the top-level `gamma/` directory
   instead of current code from `src/gamma`.
4. The repo virtualenv was incomplete and did not have Gamma installed in
   editable mode.
5. The dashboard public URL used HTTPS, but the Shana API URL still rendered as
   HTTP on port `8000`, creating browser mixed-content failures.
6. Direct HTTPS initially routed `/api/*` to port `8000`, so dashboard API
   requests returned `404`.

Fixing only one of these issues is insufficient. A page returning `200` does
not prove that dashboard APIs, static assets, WebSockets, or microphone/live
voice flows are correctly routed.

## Deployment checks

Future LLMs and automation must follow these rules:

1. Read this document before changing Nginx, OpenResty, public URL settings,
   dashboard launchers, service ports, or proxy routes.
2. Update this document when an authorized change alters the deployment.
3. Do not merge the dashboard and Shana applications onto one port.
4. Do not route `/api/*` to port `8000`.
5. Do not remove the port-`8080` listener while external NPM targets it.
6. Do not replace public HTTPS URLs with internal HTTP bind URLs.
7. Do not launch Gamma from stale top-level `gamma/` bytecode.
8. Do not change the active system Nginx file without running `nginx -t`.
9. Do not declare the deployment fixed until all verification endpoints return
   the expected status.
10. Requested architecture work can proceed within the user's authorized scope
    without a separate approval step imposed by this document. Record the
    design and validation appropriate to the change.

## Coding worker deployment

Gamma PC runs enabled user units `gamma-coding-autonomous.service` and
`gamma-coding-idle.service` from the existing gamma-main checkout with UMask 0077.
User lingering is enabled for session-independent operation. Both use the
repository `.venv` and source under `src/gamma`; candidate trials use the separate
`.venv-training` through the supervisor. Candidate files, checkpoints, job state,
backups and logs stay under ignored `data/coding`.

Dashboard port 8001 owns `/api/coding/work`, job controls, lesson history and
candidate start/status/stop. The public proxy route contracts and Shana port 8000
remain unchanged. Changes to coding/dashboard source restart the dashboard and
verify HTTP; integration activation still requires its specific runtime proof.
