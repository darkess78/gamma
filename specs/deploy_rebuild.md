# Deploying the rebuild to Gamma PC

How to switch Gamma PC from the old production code to
`rebuild/shana-foundation`, and how to switch back.

**Deployed 2026-11-14** with the owner's go-ahead, following the steps below:
- **Backups:**
  - database: `data/memory/backups/gamma-pre-rebuild-20261114.db`, integrity ok;
  - config: `~/gamma-app.local.toml.pre-rebuild` and `~/gamma-env.pre-rebuild`.
- **Old code:** kept on the local branch `local/pre-rebuild-20261114`.
- **`persona.yaml`:** the production edits were already in the rebuild; only
  whitespace differed.
- **Test suite** in a full copy of the checkout: 1601 passed, 0 failed.
- **Dashboard:** restarted on the rebuild. Health is ok, the login works and
  the log shows no errors.
- **Verification endpoints:**
  - `/dashboard` 303 and `/api/status` 401: the login is enforced. The locked
    doc's checks predate it.
  - `/v1/*` 502: the Shana API was already stopped before the deploy and was
    left stopped.
- **GPU:** no Gamma processes on it.
- **Memories:** the live database held no facts or episodes before the deploy
  (nor did any older backup), so there was nothing to embed.

This follows [LOCKED_GAMMA_NETWORK_DEPLOYMENT.md](LOCKED_GAMMA_NETWORK_DEPLOYMENT.md):
the rebuild runs in the same `~/Documents/gamma-main` checkout, from `src/`,
with the same `.venv`, ports, services and proxy. Only the checked-out branch
changes.

## What is live (checked 2026-11-14, read-only)

- `~/Documents/gamma-main`: branch `local/pre-publication-history-20260827`
  at `38e3fbf`, with 54 modified and 99 untracked files (Codex-era source,
  tests and specs; no secrets found among them). Modified files include
  `config/persona.yaml` and `config/app.example.toml`.
- User services `gamma-shana` (API, port 8000; currently stopped) and
  `gamma-dashboard` (port 8001; running), with two drop-ins:
  - `60-manga-gpu-guard.conf` loads `~/.config/gamma/manga-gpu-guard.env`, which
    keeps Shana off the GPU while the manga job uses it. The rebuild still reads
    every variable in that file, and environment values override config files.
  - `99-shana-private-release-*.conf` stops them when a private release is active.
- `gamma-coding-autonomous` and `gamma-coding-idle` are installed but disabled.
- `config/app.local.toml` is ignored by git and stays in place. Every key in it
  is still read by the rebuild.
- The live venv already has every package the rebuild needs.
- `data/` is 954 MB. The database (`data/memory/gamma.db`) is 5 MB.
- Disk: 18 GB free.

## What changes when the rebuild runs

- The database gains columns and a table on first start. They are added
  automatically and nothing is removed: memory `visibility`, fact history
  (`superseded_at`, `superseded_by`) and the `memoryvector` cache. Old rows
  count as owner-only.
- A caller that names no speaker is no longer the owner. The dashboard always
  sends the owner header, so dashboard turns are unaffected.
- Twitch chat goes through the chat queue (`twitch_chat_window_enabled`,
  default on).
- New and off unless set: games (`games_enabled`), the 3D avatar (`[avatar3d]`),
  meaning-based memory search (`memory_embeddings_dir`), and the model memory
  judge (`memory_triage_judge = "model"`).

## Steps

All commands run in `~/Documents/gamma-main` on Gamma PC.

**1. Back up.** This doesn't affect anything running. The API is stopped, so the
database is quiet; SQLite's backup folds in the WAL.

```bash
cd ~/Documents/gamma-main
sqlite3 data/memory/gamma.db ".backup data/memory/backups/gamma-pre-rebuild-$(date +%Y%m%d).db"
cp config/app.local.toml ~/gamma-app.local.toml.pre-rebuild
```

**2. Keep the current code on a local branch.** Nothing is pushed.

```bash
git switch -c local/pre-rebuild-$(date +%Y%m%d)
git add -A
git commit -m "Production state before the rebuild"
```

`git add -A` respects `.gitignore`, so `data/`, `.env` and `app.local.toml`
stay out. Check `git show --stat HEAD` before going on.

**3. Switch to the rebuild.**

```bash
git fetch origin rebuild/shana-foundation
git switch -c rebuild/shana-foundation --track origin/rebuild/shana-foundation
diff <(git show local/pre-rebuild-$(date +%Y%m%d):config/persona.yaml) config/persona.yaml
```

The persona diff shows the production edits. Copy across any you want to keep
before starting Shana.

**4. Turn on meaning-based memory search.** Add to `config/app.local.toml`:

```toml
memory_embeddings_dir = "/home/neety/Documents/gamma-models/bge-small-en-v1.5"
```

**5. Check without starting services.** Uses the CPU only, at low priority.

```bash
.venv/bin/python -c 'import gamma; print(gamma.__file__)'   # must end in gamma-main/src/gamma/__init__.py
export PYTHONPATH=$PWD/src CUDA_VISIBLE_DEVICES=
nice -n 19 .venv/bin/python -m pytest -q -p no:cacheprovider
nice -n 19 .venv/bin/python -c "from gamma.memory.service import MemoryService; MemoryService(); print('schema ok')"
nice -n 19 .venv/bin/python -m gamma.memory.embeddings --backfill
```

What each command does:
- **Import check:** confirms the import path the locked doc requires.
- **Test suite:** should pass with no failures.
- **`MemoryService()`:** applies the schema additions.
- **`--backfill`:** embeds the stored memories on the CPU, which takes a minute or two.

**6. Restart.** This is the live change.

```bash
systemctl --user restart gamma-dashboard
systemctl --user start gamma-shana     # only when you want Shana running
```

The manga GPU guard drop-in still applies.

**7. Check.** Use the verification endpoints in the locked doc, plus:

- **Dashboard:** log in and check that:
  - Home loads;
  - a Talk message gets a reply that remembers something about you;
  - Shana → Memory lists your facts;
  - the Coding page loads.
- **GPU:** `nvidia-smi` shows no new Gamma processes while the guard is on.
- **Logs:** `journalctl --user -u gamma-shana -u gamma-dashboard --since "10 min ago"`
  has no tracebacks.

## Rolling back

```bash
cd ~/Documents/gamma-main
systemctl --user stop gamma-shana gamma-dashboard   # both have the database open
git switch local/pre-rebuild-YYYYMMDD
sqlite3 data/memory/gamma.db ".restore data/memory/backups/gamma-pre-rebuild-YYYYMMDD.db"
systemctl --user start gamma-dashboard
```

This puts back the exact production code and database. Anything Shana
remembered while the rebuild ran is lost unless you copy the newer
`gamma.db` aside first.

The old code would also run on the newer database, because the new columns
are additive. But replaced facts would show up again as current, so
restoring the backup is cleaner.

## Later

- The coding worker units run from `gamma-main`, so they pick up the rebuild
  automatically. Both stay disabled until enabled on purpose.
- When the rebuild has run well for a while, the folders left by removed
  systems can be archived: `data/improvement` (528 MB), `data/coding`
  (159 MB) and `data/local-git-cleanup-*`. The disk is 91% full.
