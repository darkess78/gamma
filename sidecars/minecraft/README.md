# Minecraft Sidecar Runtime

This package defines Gamma's TypeScript Minecraft protocol v1 shapes, reusable
outbound WebSocket control client, and explicitly started sidecar runtime
shell. The runtime connects only to an exact literal-loopback Shana URL,
authenticates with an environment-supplied bearer token, performs the
`hello`/`welcome` handshake, reports bounded state, and sends periodic
heartbeats. It does not reconnect automatically or restore commands after a
new control session.

Mineflayer 4.37.1 is installed behind a narrow adapter. No pathfinder package
is installed. The runtime can join a literal-loopback Minecraft Java 1.21.11
server in offline development mode, but only after Shana sends an explicit
canonical `join` command. In addition to join, leave, status, stop, and
emergency-stop handling, it implements bounded `follow_owner`, `wait_here`,
`come_here`, and `look_at_owner` commands through direct steering. Default
tests use fake Minecraft adapters and do not open a Minecraft connection. No
real-server movement smoke test has been run. The runtime is inactive unless a
person explicitly runs it; it is not wired into Gamma's supervisor or
application startup.
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
SHANA_MINECRAFT_OWNER_USERNAME                 required for owner movement; 3-16 letters, digits, or underscores
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

Owner authorization is deliberately limited to private offline development.
Both the configured owner name and an observed player's name must match the
Minecraft username format; comparison is an exact match after converting both
names to lowercase. The owner remains absent unless exactly one observed
player matches. This case-insensitive username check is not UUID-strength
authorization and is unsuitable for an online or public deployment. Microsoft
authentication and online owner UUID authorization remain deferred. Minecraft
chat and other world text cannot configure the owner or issue commands.

Movement is intentionally limited to clear, flat, loaded, direct Overworld
terrain. On each Mineflayer physics tick, the adapter re-observes the owner and
checks the short forward step before enabling forward walking. Unknown or
unloaded blocks, obstacles, jumps, drops, liquids, hazards, portals, or a
dimension mismatch clear movement and cause a bounded retry or terminal
failure. The executor does not navigate around an obstruction. Jumping and
sprinting are disabled. It does not break or place blocks, equip items,
activate blocks, open doors or containers, attack, chat, intentionally swim,
enter portals, or travel between dimensions. Movement listeners and controls
are removed on every terminal, preemption, disconnect, death, and shutdown
path.

Dashboard controls and natural-language command integration are not part of
this phase. No real-server companion movement smoke has passed; the automated
end-to-end coverage uses a fake Gamma WebSocket server and fake Minecraft
adapter.

`node_modules/` and generated `dist-test/` output are ignored and must not be
tracked. `package-lock.json` is tracked to preserve exact dependency
resolution. No credentials, tokens, authentication caches, server addresses,
or other secrets belong in this package.
