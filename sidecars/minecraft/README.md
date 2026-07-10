# Minecraft Protocol Scaffold

This package is only a TypeScript protocol-validation scaffold for Gamma's
future Minecraft companion. It defines and tests protocol v1 data shapes. It
does not connect to Gamma or Minecraft, open a WebSocket, execute commands, or
implement companion behavior.

Mineflayer is not installed. The canonical behavioral contract remains owned
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

Run the protocol contract tests with Node's built-in test runner:

```bash
npm --prefix sidecars/minecraft test
```

`node_modules/` and generated `dist-test/` output are ignored and must not be
tracked. `package-lock.json` is tracked to preserve exact dependency
resolution. No credentials, tokens, authentication caches, server addresses,
or other secrets belong in this package.
