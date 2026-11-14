# Keeping the owner's information private

Shana talks to the owner privately and to everyone else in public (Twitch,
Discord, games). The rule: nothing about the owner reaches anyone else, and
nobody else can plant things about the owner. This page lists the guards and
the tests that hold them in place. The audit behind it was done on 2026-11-14;
see `tests/test_audit_*.py`.

## Who is the owner

The owner is recognised in only three ways:

- the dashboard's owner channel (`X-Gamma-Owner-Channel`);
- the local command-line tools run on Gamma PC;
- a platform id that came from Gamma's own Twitch or Discord worker.

Those workers send the ingress secret (`X-Gamma-Ingress`, kept in
`data/runtime/ingress.key`). A request body that only *claims* a platform id
counts as an unknown speaker. That includes the owner's Discord id, which is
public. A caller that names no speaker is unknown, never the owner.

Real names and platform ids live in `config/users.local.toml`, which git
ignores. `config/users.toml` in the repo is only a template.

## What reaches a shared prompt

Prompts for shared turns get:

- the character;
- that audience's own recent history, where speakers are shown by chat name
  or a role label, never a private profile name;
- the current speaker's own viewer notes and familiarity line.

They never get:

- facts or episodes;
- core memories;
- feelings notes;
- relationship state;
- host paths.

Checked by `gamma.evals.memory_privacy` (14 probes, 27 canaries) and the audit
tests.

## What can be written about the owner

- **Facts about the owner:** only the owner's own turns can save memories about
  the owner. A claim from anyone else about the owner is refused, not stored as
  "unverified".
- **Viewer notes:** only what a viewer says about themselves. A note that
  mentions the owner, "the owner/streamer", or someone else is dropped.
- **Feeling state:** this is shown in the owner's prompt, so only owner-private
  turns leave their words in it. Other turns move her mood without any text.
- **Known-people list:** only facts the owner reported add people to it.

## What leaves the system

- **Output with no target:** public only if the event itself is public;
  otherwise it goes to the dashboard Monitor. Monitor notes stay on the
  Monitor while live unless the owner sends them to the stream.
- **Live feeds:** a URL token (the OBS overlay) opens only the `stream_public`
  feed. The Monitor's private feed needs a 10-minute ticket from the logged-in
  dashboard (`/api/performer/ticket`).
- **Audio files:** `/v1/audio/artifacts/` serves only Shana's own TTS audio.
  File names are unique, so two replies can't overwrite each other.
- **Stream safety review:** the switch is server-side
  (`twitch_llm_safety_review_enabled`, plus the go-live safety toggle). Event
  metadata can't turn it off.

## The last line: private terms

List things that must never be said publicly in
`config/sensitive_terms.local.txt`, one per line. Git ignores the file, and the
terms never go into any prompt. For example:

```text
# real name, city, employer, family
Jonathan Example
Example
Springfield
```

Every public output (stream subtitles and voice, Discord, game lines) is
checked against the list before publication. A match replaces the whole line
with "filtered". The check ignores case, accents and zero-width characters,
`j0hn`-style swaps, and dots or spaces between letters. A multi-word entry
matches as a whole, so give a surname its own line if it must never be said
alone. Logs keep only a short hash of the term.

## Still open

- **Proxy access log:** the nginx access log records full URLs, including
  `?token=` and the games `?key=`. Fixing it changes the locked deployment
  config, so the owner has to approve it
  (`tests/test_audit_output_leaks.py::test_repo_nginx_access_log_does_not_record_query_secrets`).
- **Git history:** the repo is public, and its history still has the old
  `config/users.toml` values (from April 2026). Rewriting history needs the
  owner's go-ahead.
