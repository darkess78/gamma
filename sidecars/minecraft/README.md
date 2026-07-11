# Minecraft Sidecar Runtime

This package defines Gamma's TypeScript Minecraft protocol v1 shapes, reusable
outbound WebSocket control client, and explicitly started sidecar runtime
shell. The runtime connects only to an exact literal-loopback Shana URL,
authenticates with an environment-supplied bearer token, performs the
`hello`/`welcome` handshake, reports bounded state, and sends periodic
heartbeats. It does not reconnect automatically or restore commands after a
new control session.

Mineflayer 4.37.1 is installed behind a narrow adapter. The runtime can join a
literal-loopback Minecraft Java 1.21.11 server in offline development mode,
but only after Shana sends an explicit canonical `join` command. It implements
`join`, `leave`, `report_status`, `stop`, and `emergency_stop`; companion
movement remains rejected until the pathfinder phase. No default test opens a
Minecraft connection, and no real-server smoke test has been run. The runtime
is inactive unless a person explicitly runs it; it is not wired into Gamma's
supervisor or application startup.
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
SHANA_MINECRAFT_SERVER_HOST                    optional; literal loopback, default 127.0.0.1
SHANA_MINECRAFT_SERVER_PORT                    optional; integer 1-65535, default 25565
SHANA_MINECRAFT_VERSION                        optional; must be exactly 1.21.11
SHANA_MINECRAFT_ACCOUNT_MODE                   optional; must be offline
SHANA_MINECRAFT_BOT_USERNAME                   optional; 3-16 safe characters, default Shana
```

Start it explicitly from the repository root only after Shana's disabled-by-
default Minecraft control feature has been locally configured and enabled:

```bash
npm --prefix sidecars/minecraft start
```

Startup connects only to Shana and never joins Minecraft automatically. A
successful `join` must spawn in the Overworld with the exact configured
version. Failed or cancelled joins clear controls and disconnect without
retrying. `leave` is idempotent, and control-channel loss, SIGINT, SIGTERM, or
Gamma shutdown clears all controls and disconnects Minecraft before cleanup.
Within a running sidecar process, control cleanup does not clear the emergency
latch; recovery requires a completed leave followed by a fresh successful
join.

This phase is offline-development-only. It does not load Microsoft profiles,
credentials, or authentication caches. Mineflayer pathfinding, following,
coming, waiting, looking, digging, placing, combat, chat commands, containers,
and dimension travel are not implemented.

`node_modules/` and generated `dist-test/` output are ignored and must not be
tracked. `package-lock.json` is tracked to preserve exact dependency
resolution. No credentials, tokens, authentication caches, server addresses,
or other secrets belong in this package.
