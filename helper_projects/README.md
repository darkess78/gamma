# Helper Projects

`helper_projects/` is for support applications and workflows that are useful for building or maintaining Gamma but are not part of the core assistant runtime.

Current helper projects:

- `GammaTTSDataPrep/` - desktop GUI, packaging files, and scripts for preparing Shana TTS training data.
- `imagegen/` - local ComfyUI/image generation support workspace. This is helper tooling, not core Gamma runtime code.

Keep runtime services, dashboard code, API routes, stream logic, memory, and voice runtime modules in `src/gamma/`.
