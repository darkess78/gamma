# Temporary review proxy lease controller

`scripts/review_proxy_lease.py` is prepared for a later explicit root-run test.
No privileged configuration, reload, sudo authentication, DNS, port or service
change was performed while building it. The current sudo authentication gate
remains an operational requirement. The controller does not acquire privileges.

The current reviewed packet is the additive `/review/shana` route to
`127.0.0.1:18708`, in exactly these two files:

- `/etc/nginx/conf.d/gamma-proxy.conf`
- `/etc/nginx/conf.d/gamma-npm-upstream.conf`

Default operation, `--check`, and `--prepare` only read, validate and print a plan.
They never create a lease directory or invoke Nginx/systemctl. For example:

```bash
/usr/bin/python3 -I scripts/review_proxy_lease.py --check --packet \
  data/coordination/remaining-20261027/proxy-review-2c36176b02a4440789fc75a83565055a/packet.json
```

This fresh packet requires exactly `client_max_body_size 4m`, preserving the
existing roughly 3 MB base64 envelope of a stereo 48 kHz, 12-second PCM input.
The former `proxy-review-f3d78ddf1c5f4663ab643610e774785a` archive remains unchanged;
its 2 MB packet is superseded and rejected by the controller. No broader body-size
parser or changed runtime audio contract is introduced.

Packet validation checks exact before/after/block hashes, fixed live paths,
regular non-symlink files and the precise permitted additive block. Changing an
after-file hash cannot authorize an extra directive. Both current live originals
must match before either installation starts. On 2026-10-27, read-only checking
of this actual staged packet confirmed both originals still matched.

## Activation and candidate binding

Mutation requires `--apply` from an already authorized root process, the packet,
a fresh candidate receipt and credentials supplied as a bounded JSON object on
stdin: `{"username":"...","password":"..."}`. Credentials are never placed in
argv, the journal or output. Parent orchestration must wire a secure stdin pipe;
the controller does not solicit or bypass privileged authentication.
Use `/usr/bin/python3 -I` for the eventual root operator command, rather than a
user-owned virtual environment and its site startup. The detached production
guardian also starts through `/usr/bin/python3 -I`. The controller itself remains
user-reviewed source, not a cryptographically attested privilege boundary.

The candidate receipt is a private trusted-launcher record, not a new API:

```json
{
  "scope": "isolated_owner_private",
  "prefix": "/review/shana",
  "port": 18708,
  "pid": 12345,
  "created_at": 1790000000,
  "expires_at": 1790001800,
  "cwd": "/absolute/path/to/the/isolated/candidate",
  "resolved_source": "/absolute/path/to/the/isolated/candidate/src/gamma/__init__.py",
  "start_ticks": "actual_linux_proc_stat_starttime",
  "argv_sha256": "sha256_of_exact_proc_cmdline_bytes",
  "identity_id": "32_lowercase_hex_digits",
  "generation": 1,
  "source_manifest": {"src/gamma/__init__.py": "actual_file_sha256"}
}
```

The abbreviated manifest above must actually include every `.py`, `.html`, `.js`
and `.css` file under `src/gamma`, excluding bytecode caches. Missing/new/changed
source files reject readiness. Both timestamps must be finite numbers, with a
positive receipt lifetime of at most 1800 seconds. Initial admission rejects a
future creation time, an expired receipt or creation more than 60 seconds ago.
Repeated checks enforce the same fixed expiry without refreshing receipt age.
Its source import path must be the path observed by the trusted candidate
launcher. This is process/source binding under that launcher, not cryptographic
attestation of an adversarial Python interpreter.

Before installation and again before the initial reload, the controller checks
`/proc` PID start time, exact cmdline hash, cwd, source inventory/hashes and socket
ownership. The candidate must own the sole IPv4 loopback listener on 18708; an
unexpected IPv6 listener is rejected. It performs a fresh login without following
redirects or using environment proxies, verifies the scoped Secure/HttpOnly review
cookie with SameSite Lax or Strict, and requests the existing authenticated status and personal APIs.
`isolated_owner_private`, generation and identity must match the receipt. A login
page, ordinary 401, open port or nonce by itself is insufficient. These checks
continue during the lease and candidate loss triggers cleanup.

## Finite guardian and recovery

`--lease-seconds` accepts 1 through 1800. The monotonic deadline begins before
installation; there is no reconnect/heartbeat renewal. Explicit apply launches a
separate session guardian with closed inherited descriptors and a one-time
credentials pipe. The launching SSH process may exit without stopping the
guardian. A root-owned single-controller file lock prevents concurrent leases.
The guardian handles SIGTERM, SIGINT and SIGHUP by requesting cleanup.

The initial stdout message means only that the guardian started. Activation must
be confirmed from its root-owned `/run/shana-review-proxy/<id>/journal.json`,
phase `active`, followed by separate observed HTTPS checks. It does not claim
Android certificate trust or phone reachability. A failed preflight retains a
failure record and does not install the blocks.

Before any live configuration write, the production guardian constructs a
complete staged master from the actual `/etc/nginx/nginx.conf`. Only its single
literal `/etc/nginx/conf.d/*.conf` directive is replaced: all current matching
files stay in their original sorted include order, with only the two exact
review files substituted by private staged AFTER copies. Master settings and
module/site includes remain intact. The helper reads and pins the complete
literal include graph, including empty wildcard inventories and symlink targets.
Relative, dynamic or escaped include forms block this narrow staging path.

The required staged callback runs `/usr/sbin/nginx -t -c <complete-staged-master>`
and rechecks every master/include/staged hash and wildcard membership before
returning a drift guard. That guard runs before each live install and before
activation, accounting only for the controller's own installed AFTER bytes.
Failed staged syntax, an incomplete graph, or a changed master/include/stage
blocks installation. There is no switch to skip the production staged callback.
The complete stage and its proof live inside the guardian's private root-owned
lease directory. Read-only CLI modes still make no stage files or Nginx calls.

Every reload, including rollback, also follows a fresh successful live `nginx -t`.
Partial installation, syntax failure, reload failure, candidate loss, signals
and normal expiry all enter cleanup. Cleanup reads the current configurations
and removes only the exact verified owned block. Unrelated edits remain. An
altered, partial or duplicated owned block is a conflict, never permission to
restore the original whole file. Compare checks before atomic replacement catch
observed concurrent changes; they do not provide a kernel atomic compare-and-swap
against an uncooperative external editor in the final rename interval.

Cleanup has three finite attempts with one- and two-second backoffs. It records
per-path or syntax/reload failure types without credentials. It never kills
Nginx, stops unrelated processes or replaces files blindly. After a dead guardian,
an explicit root recovery command is available:

```bash
/usr/bin/python3 -I scripts/review_proxy_lease.py --apply --cleanup \
  /run/shana-review-proxy/<actual-id>/journal.json
```

Recovery uses the root-owned journal's exact block and verifies its hash. It also
requires the single-controller lock, so it cannot race a still-running guardian.
The packet's user-owned staging files are not needed for recovery.

The 1800-second value bounds the requested active wait; syntax checks and cleanup
take additional bounded command time. It is not a guaranteed hard network expiry.
SIGKILL, power loss, blocked filesystem I/O, edited owned blocks, or unrelated
invalid Nginx configuration can prevent a cleanup reload. A `cleanup_failed`
journal means the route may remain active in the running Nginx process even if
the owned blocks were removed on disk. Root inspection and explicit recovery are
then required. The controller makes no successful-expiry claim in that state.

## Offline evidence

The focused Gamma lease suite passed 54 tests using disposable configs and fake command
callbacks. It exercises partial installation, syntax/reload rollback, finite TTL,
candidate loss, source/PID/identity changes, compare conflicts, exact-block
cleanup, preserved unrelated edits and read-only default modes. A real detached
fake guardian survived its launcher exiting, retained its own session, and then
handled SIGHUP by restoring only its disposable owned additions. No test invoked
real Nginx, systemctl, sudo or live configuration writes. The scoped local worker
helper passed three protected tests; its result/proposal/source hashes were
reviewed before integration. Added regressions reject superseded 2 MB packets,
nonfinite/incorrect receipt times, receipt renewal and weakened SameSite cookies.

The separate real mock-host launcher receipt test passed with the updated
controller, bringing that combined run to 55 cases. It observed the actual Linux
process, socket owner, source manifest, fresh login cookie, status and identity
APIs, then verified the candidate stopped. It used no model or Nginx command.
Windows helper evidence also records an actual hidden Python child with no
console handle and a successful nested SSH launch through CREATE_NO_WINDOW and
SW_HIDE. The existing worker receipt's loaded hash matched the current worker
source. This does not claim every unrelated console flash is fixed.

Twenty additional complete-stage tests bring the affected run to 75 cases.
They preserve master/module/site/non-owned config scope, require staged syntax
before the first writer, prove zero live writes/reloads on staged failure or
pre-install drift, and test master/include/glob/symlink/staged-byte changes,
including changes during the last candidate readiness or live syntax check.
Mid-install drift cancels activation and removes only the owned additions while
preserving the unrelated edit. These syntax callbacks are controlled fakes.

An actual read-only include-tree preview is retained under
`data/coordination/p7/proxy-complete-stage/`, with the complete config and
`syntax_validated: false`. It pins the current master, MIME file and two review
targets, retains the empty module/site glob inventories, and confirms the fresh
4 MB packet and live originals stayed unchanged. No real Nginx syntax command was
run. A root apply must rebuild and validate its own complete stage; this preview
is not an activation receipt.

The full root-run path, actual privileged Nginx activation, HTTPS verification
and Android connection remain unverified. This is a
prepared controller, not an activated or operationally accepted proxy lease.
