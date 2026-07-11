# Minecraft Control Client

This package defines Gamma's TypeScript Minecraft protocol v1 shapes and a
reusable outbound WebSocket control client. The client connects only to an
explicit literal-loopback Shana URL, authenticates with a bearer token supplied
directly by its caller, validates every inbound frame, and performs the
`hello`/`welcome` handshake. It has no automatic reconnect or background
heartbeat timer and is not an executable sidecar entry point.

Mineflayer and mineflayer-pathfinder are not installed. A hello must report
their versions as `not-installed` until later implementation phases add them.
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

`node_modules/` and generated `dist-test/` output are ignored and must not be
tracked. `package-lock.json` is tracked to preserve exact dependency
resolution. No credentials, tokens, authentication caches, server addresses,
or other secrets belong in this package.
