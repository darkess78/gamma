# Review the isolated P1 foundation

This starts a temporary private candidate on Gamma PC and a hidden SSH tunnel
from this Windows worktree. It does not activate live Shana, change services,
copy production memories, or install a scheduled task. Each launch gets fresh
private state under the candidate's ignored `data/coordination/` directory.

## Start the review

Run this PowerShell command from the managed Windows worktree. It uses the
existing SSH identity; the controller itself uses only Python's standard library.
All Gamma application code and inference run on Gamma PC.

```powershell
$reviewArguments = @(
  'C:/Users/darke/.codex/worktrees/shana-coordination-core/gamma/scripts/review_coordination_windows.pyw',
  '--host', 'neety@10.78.78.13',
  '--identity-file', 'C:/Users/darke/.ssh/codex_gamma_ed25519',
  '--remote-project', '/home/neety/Documents/gamma-coordination-core-20261023',
  '--duration', '600'
)
Start-Process -FilePath 'C:/Users/darke/.codex/workers/.venv/Scripts/pythonw.exe' -ArgumentList $reviewArguments -WindowStyle Hidden
```

Open `data/coordination/review_sessions/latest.json`. When its phase is `ready`,
open its `url` in the browser and sign in using the generated username/password
in its `credentials_file`. These credentials are local temporary files, never
command-line arguments or log output. The private API token stays in process
memory and the stdin pipe. The controller removes the login file when it closes.
The page starts with the private audience unconfirmed. Confirm that the owner is
present privately before sending text or using controls.

The default local and remote dashboard port is 18701. Both ends bind only to
loopback. Add `--port 18703` to use another unused port. Occupied listeners are
rejected. The private API uses a separate ephemeral loopback port; the dashboard
cannot route to the production applications.

The real model is Qwen3.8 27B on Gamma's existing local Ollama endpoint. Startup
refuses an occupied model endpoint. Admission remains cooperative with other
Gamma callers. The review does not reserve the GPU against unrelated clients.
Observed fresh replies take roughly 11-16 seconds. Model residency is not kept
between calls. Add `--mock` only for launcher/UI verification; its page labels
synthetic responses and disables work-start buttons. A provider trap test proves
that this mode sends no model requests.

## Check the experience

The same isolated launcher now includes P2 personal state. Follow the
"Personal state and corrections" link in the private Monitor. Confirm the
private owner audience on that page before inspecting records or saving edits.
It supports authored character edits, sourced report corrections, source
removal, explicit person/account links and raw-text retention. These edits stay
inside that review session's candidate state. They do not activate production.

P2 implementation and evidence are described in `shana_personal_state.md`.

- Chat normally, establish your attention and a current direction, then allow
  one selected quiet-time exercise. Waiting is a valid choice. Check whether
  Shana responds appropriately when addressed while work is running.
- Reserve a second allowed exercise conversationally, ask to switch, pause and
  return to saved work. For buttons, explicitly select the activity to control.
  Polling preserves your selection and does not replace control feedback.
- Sleep and wake Shana, then ask what actually happened to a specific activity.
  Compare her receipt with the activity state. Offline practice success does
  not establish general coding skill or learned personality.

Changing the audience back to unconfirmed hides the private draft and previous
reply, clears activity details, and withholds responses already in flight. The
draft and uncertain request identity remain available when privacy is confirmed
again. Conversation and control retries retain their own request identities.

## Stop or inspect the session

The session closes automatically after 600 seconds. Without `--awake-test`, the
allowed duration is 5-1800 seconds. To stop it earlier:

```powershell
Start-Process -FilePath 'C:/Users/darke/.codex/workers/.venv/Scripts/pythonw.exe' -ArgumentList @('C:/Users/darke/.codex/worktrees/shana-coordination-core/gamma/scripts/review_coordination_windows.pyw', '--stop') -WindowStyle Hidden -Wait
```

Shutdown sleeps the candidate and requests pauses for unfinished activities,
then waits for their safe boundaries. A current model call may take up to its
90-second bound. `closed` is saved only after the remote host confirms shutdown.
The transport log and nonsecret lifecycle record remain in the session directory.

Use the same command with `--status` instead of `--stop` to refresh
`data/coordination/review_sessions/status.json`. It checks the controller's actual
process creation time and liveness. A missing or replaced controller is
`unverified`; elapsed sessions are `stopping`, rather than stale `ready`.
No stop command kills a process by a PID from a saved record.

The [acceptance record](shana_foundation_acceptance.md) contains the connected
model/browser proof, failure cases, regression results and source package.
The [delivery plan](shana_delivery_plan.md) retains the later milestones and the
separate live-activation boundary.

## Hours-long awake-at-computer test

The owner authorized a private test with production unchanged. Add `--awake-test` to both launcher paths to permit a finite duration up to 57,600 seconds, or sixteen hours. The prepared Windows Start shortcut selects 43,200 seconds, or twelve hours, on loopback port 18711 with local private voice and selected-window analysis available. It passes the verified installed Git SSH executable and the existing pinned known-hosts file; it does not change the system SSH installation or trust settings. Ordinary reviews retain their thirty-minute limit.

Open [Home](http://127.0.0.1:18711/dashboard/home) manually in Brave. Read the temporary username and password from the current session's `credentials_file` in `data/coordination/review_sessions/latest.json`. The login file is removed at shutdown. Night + Signal is the default only in this isolated dashboard.

For local voice, open Voice and presence, confirm the private audience and owner microphone source, enable speech, then start listening. The session starts with these capabilities off and no presentation consumer connected. Browser microphone permission remains an explicit owner action. Hiding or minimizing the page does not put Shana to sleep.

Use I'm focusing while busy, I can chat now when available, and Sleep before leaving. Wake resumes the same session. From Activities, select only a chosen window, such as OSRS, and explicitly start a finite observation/watch. A longer host session does not renew microphone ownership, selected-window consent, practice permits or work budgets. The existing five-, fifteen- and thirty-minute watch choices remain bounded. Discord and public streaming stay disconnected. No production memories are imported.

The prepared Start and Stop shortcuts are under `data/coordination/awake-test-ready-20261030/`. They target pythonw and the same controller used in the connected checks, with hidden SSH children. Stop requests bounded remote closure and removes the login file. Closing a dashboard tab alone does not end the host session. Each new Start uses fresh separate test state; a reconnect during one host session keeps that session's state.

This tests private conversation, listening, interruption, focus transitions, selected-window moments and permitted work. Awake by itself does not generate autonomous boredom chatter. Natural initiative, comfort, voice quality and actual room/OSRS behavior remain owner acceptance checks. The authored audio readiness run produced first audio in roughly 18-24 seconds, with a full multi-chunk reply taking longer.
