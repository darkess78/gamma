# Candidate dashboard review path

`ReviewIngress(existing_dashboard_app, '/review/shana')` is an opt-in ASGI wrapper
for the existing authenticated isolated dashboard. It does not start a server,
change the normal dashboard, create a coordination owner, or expose a backend
proxy. The app factory must choose it explicitly. Proxy activation is a separate
operation with a concrete configuration, rollback, and observed HTTPS checks.

The only accepted prefix shape is `/review/[a-z][a-z0-9-]{0,40}`. Requests outside
that prefix return 404. The inner path may belong to `/dashboard`, `/api`,
`/static`, `/login`, `/logout`, `/monitor`, or `/overlay`, or be `/`. Backend,
health, docs, traversal and ambiguous encoded paths are unavailable. WebSockets
are rejected with code 1008; current review flows use authenticated HTTP.

The wrapper delegates the existing lifespan and app state. Existing dashboard
authentication is unchanged. It forwards only its own review cookie under the
inner dashboard's expected cookie name; the normal `gamma_dashboard_session`
cookie is ignored. For `/review/shana`, the outer cookie is
`gamma_review_shana_session`, has Path `/review/shana`, always has Secure, and
retains the inner cookie's HttpOnly, SameSite, expiry and deletion properties.
No new CORS origin, login bypass or bearer-token forwarding is added.

Review HTML receives a separate Content-Security-Policy containing only
`connect-src <validated-current-origin>/review/shana/` for this example prefix,
without a `'self'` exception. Existing inner CSP headers remain as additional
intersecting policies. This confines browser requests, including dormant legacy
Monitor functions, to the review API path. Host/scheme validation rejects
ambiguous origins before emitting that policy.

Only the served phone, Monitor and presence JavaScript assets replace their exact
owned browser-storage key constants with a review-name namespace. This keeps an
existing phone tab's device secret and pending-message ID out of the candidate
flow and keeps review preferences separate. The normal assets and normal saved
values are not edited. There is no global storage monkeypatch.

Code-owned root-relative URL literals in HTML, static JavaScript and CSS acquire
the prefix. Login redirects and the phone worker's registration URL, scope,
click target and `Service-Worker-Allowed` header stay inside it. Rewrites are
idempotent. There is no global fetch monkeypatch. Relative and external URLs are
not converted into proxies. Response redirects outside the same origin or
dashboard-owned path fail closed.

Private API text and binary audio are not scanned. Only named URL locations in
known response schemas may change: `output.audio_url` from interaction poll/input
and top-level `audio_url` from channel-audio render. Other JSON fields, including
private text and arbitrary metadata with an `audio_url` key, remain unchanged.
When no approved URL changes, the original JSON bytes are retained. Transformed
HTML/JS/CSS/JSON are buffered up to 2,000,000 bytes; compressed or oversized
rewrite sources return 502. Transformed responses receive a fresh Content-Length,
no stale ETag or digest, and `Cache-Control: no-store`. Binary bodies retain their
exact bytes and streaming behavior. Duplicate unrelated response headers remain
separate. Byte ranges and conditional validators are stripped on routes whose
responses may be rewritten, so a partial unprefixed asset cannot be served as a
transformed review asset. Binary routes retain their incoming range/validator
headers and response validators.

On 2026-10-27, focused Gamma verification passed 52 ingress cases covering real private
Core text, actual checked-channel synthetic PCM generation and HTTP fetch,
ordinary-cookie rejection, review login/logout, existing unprefixed behavior,
current pages and scripts, phone notification worker scope, trusted JSON URL
locations, exact private JSON strings, duplicate headers, bounded failure paths,
WebSocket rejection, strict CSP and malformed-origin rejection, separate browser
storage, range/validator handling, and normal lifespan shutdown. Controlled Node
execution of the actual HTTP-served rewritten phone asset proved that normal
device and pending-message keys were never read or written through connect,
uncertain send, explicit status lookup and reset. The combined ingress/dashboard,
phone-route and checked-channel audio suites passed 61 tests. These are application tests,
not proof that Android trusts the existing certificate or can reach the path.

No live proxy, Nginx, DNS, port, service, certificate or machine-local configuration
was changed. Android certificate trust and actual phone access remain to verify
through the existing HTTPS origin without bypassing certificate checks.
