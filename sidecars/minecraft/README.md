# Minecraft Sidecar Runtime Shell

This package defines Gamma's TypeScript Minecraft protocol v1 shapes, reusable
outbound WebSocket control client, and explicitly started sidecar runtime
shell. The runtime connects only to an exact literal-loopback Shana URL,
authenticates with an environment-supplied bearer token, performs the
`hello`/`welcome` handshake, reports honest disconnected state, and sends
periodic heartbeats. It does not reconnect automatically or replay commands.

Mineflayer and mineflayer-pathfinder are not installed. The runtime cannot
connect to Minecraft or execute movement, and its hello reports both library
versions as `not-installed`. Unsupported commands are rejected rather than
claimed as executed. The runtime is inactive unless a person explicitly runs
it; it is not wired into Gamma's supervisor or application startup.
The canonical behavioral contract remains owned
by `specs/minecraft_companion.md` and
`src/gamma/integrations/minecraft/protocol.py`; tests load the shared fixtures
directly from `tests/fixtures/minecraft_protocol/v1/`.

## Requirements

- Node 22
- npm

Install the pinned local dependencies from the repository root:

```bash
npm --prefix sidecars/minecraft install
```

Run the strict no-emission type check:

```bash
npm --prefix sidecars/minecraft run typecheck
```

Run the protocol and control-client tests with Node's built-in test runner:

```bash
npm --prefix sidecars/minecraft test
```

## Local configuration and explicit start

The sidecar reads process environment only when the executable is started. It
does not load `.env` files. Set the dedicated control token locally without
printing or committing it:

```text
SHANA_MINECRAFT_CONTROL_TOKEN                 required
SHANA_MINECRAFT_CONTROL_WEBSOCKET_URL         optional; defaults to ws://127.0.0.1:8000/v1/minecraft/control
SHANA_MINECRAFT_SIDECAR_INSTANCE_ID           optional; generated when omitted
SHANA_MINECRAFT_HEARTBEAT_SECONDS              optional; integer 1-60, default 5
```

Start it explicitly from the repository root only after Shana's disabled-by-
default Minecraft control feature has been locally configured and enabled:

```bash
npm --prefix sidecars/minecraft start
```

SIGINT, SIGTERM, Gamma shutdown, and control-channel loss all clear the
heartbeat timer and close the client. No Minecraft server connection is made.

`node_modules/` and generated `dist-test/` output are ignored and must not be
tracked. `package-lock.json` is tracked to preserve exact dependency
resolution. No credentials, tokens, authentication caches, server addresses,
or other secrets belong in this package.
