# Android review access

Status: prepared procedure, 2026-10-27. No Android-reachable candidate HTTPS URL
was configured or verified by this packet. The owner authorized temporary
activation for testing. This record does not claim that a route or cutover ran.
Use Brave on Android for the eventual review.

## Choose a reachable private address

The existing finite review launcher exposes loopback listeners and a Windows
loopback tunnel. Opening that Windows `127.0.0.1` URL on Android would address
the phone itself. It does not extend the tunnel to the phone.

The prepared temporary option now uses `/review/shana` on the existing HTTPS
hostname, with a separately authenticated candidate on loopback 18708. Its
opt-in wrapper scopes cookies, browser keys, assets and API calls to that prefix;
the phone page would be `/review/shana/dashboard/phone`. The
[proxy lease packet](shana_review_proxy_lease.md) is additive and finite, with
source/process/identity binding and exact-block cleanup. It remains unapplied:
the attempted read-only Nginx syntax check required interactive sudo
authentication. A fresh candidate receipt, successful privileged staged/live
syntax checks and actual HTTPS verification are required before presenting this
as a usable address. Android must also trust the existing certificate chain.

A separate option is a new HTTPS review hostname reachable from the phone's
network or existing private VPN. Its certificate must cover that actual hostname.
Android must also trust the certificate chain. A valid certificate in Windows
does not establish that trust on the phone; do not bypass certificate errors.
Keep the candidate backend on loopback and forward the complete dashboard route
family through the authenticated review dashboard. Do not assume a new hostname,
certificate, DNS entry or VPN membership already exists.

The other option is the existing HTTPS hostname after the bounded handover
rehearsal and rollback package are ready. That changes which owner receives the
normal routes and needs the full authority-transfer checks in
`shana_delivery_plan.md`, rather than forwarding only the phone page to a
different brain. Restore current compatible state when reversing a test cutover,
including experiences accepted during that test.

Gamma currently uses the fixed `gamma_dashboard_session` cookie with path `/`.
A second port on the same hostname does not isolate that cookie. Prefer a
separate review hostname for an unwrapped independent test login. The opt-in
prefix wrapper instead uses a different cookie alias scoped to `/review/shana`
and ignores the ordinary session cookie. Do not overwrite the normal dashboard
session merely to show the mobile page. Cookie port behavior
is defined in [RFC 6265, section 8.5](https://www.rfc-editor.org/rfc/rfc6265#section-8.5).

## Route and authentication checks

Keep the two application roles and public HTTPS concepts from
`LOCKED_GAMMA_NETWORK_DEPLOYMENT.md`. The phone browser uses these dashboard
paths on one origin. In the prepared prefix review, every path below has the
`/review/shana` prefix, including login, assets, APIs and worker scope:

| Path | Required behavior |
| --- | --- |
| `/dashboard/phone` | Authenticated mobile page and honest mock/private-review label |
| `/dashboard/phone-worker.js` | Authenticated fixed JavaScript, no-store, notification-only scope `/dashboard/phone` |
| `/static/phone.js`, `/static/phone.css`, `/static/phone_worker.js`, shared shell assets | Correct current content, MIME types and reachable same-origin URLs |
| `/api/coordination/phone/*` | Authenticated typed proxy to this candidate's `/v1/coordination/phone/*` |
| `/login`, `/logout` | Correct review session, secure cookie over HTTPS |

Verify unauthenticated private calls are denied, sign-in works from the actual
phone, and the backend identifies as the intended private candidate. Set runtime
public URL and secure-cookie values for that HTTPS endpoint. Preserve production
port ownership and ignored machine-local files. Keep ephemeral credentials in
the private review handoff, not in the URL, QR code, report or browser logs.
The phone never receives the backend bearer token or a private database copy.

Browser notifications require a secure context. Mobile browsers generally need
`ServiceWorkerRegistration.showNotification` rather than the desktop
`Notification` constructor. The page registers its narrow notification bridge
only after the owner's alert opt-in and permission. Registration, permission or
display failure leaves chat usable and explains that updates remain in the
page. See the [Notifications API](https://developer.mozilla.org/en-US/docs/Web/API/Notifications_API),
the [mobile constructor restriction](https://developer.mozilla.org/en-US/docs/Web/API/Notification/Notification)
and [worker scope rules](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerContainer/register).

The bridge has no push subscription, network handler, content cache, background
decision loop or offline conversation store. Alerts still depend on this page
being open. This is not closed-phone delivery or Web Push.

## Physical Android acceptance

1. Open the prepared HTTPS review URL in Brave. Check the correct private-review
   label. Sign in, confirm private output and Connect. Continue a desktop
   conversation, ask about a real bounded task, and inspect its result. Check
   the keyboard, scrolling, navigation and touch controls on the actual phone.
2. Allow considered reminders for a real unfinished commitment, with availability
   set appropriately. Enable discreet alerts through the button and handle the
   browser permission yourself. Inspect the actual alert on Android. Its visible
   content must remain generic. Read the private message in the page and select
   Got it. Verify the exchange stops while the commitment remains unfinished.
3. Try Busy, Quiet, sleep, a lost connection and reconnect. A hidden page clears
   private output and disconnects. An uncertain send is checked through its
   saved request ID instead of being resent. Use Manage browser connections to
   revoke a lost key, then connect again without deleting the shared history.

Record actual received behavior, comfort with the timing and wording, layout
problems and the exact endpoint/source version. HTTP test clients, viewport
emulation, Node mocks and permission stubs do not satisfy this physical check.

## End the review

Disable review contact authority and disconnect or revoke the review device.
Close generic alerts and the review page. Stop the owned finite host and remove
only the temporary route created for this review, or use the tested compatible
handover reversal. Verify normal HTTP routes and owner identity afterward.
The bridge contains no private history and has no background delivery source.
If retiring the separate review site, its worker can be removed through that
site's browser controls without clearing the normal production site's data.
