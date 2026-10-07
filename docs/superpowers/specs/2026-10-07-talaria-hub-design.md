# Talaria — one bot for all apps on a host (hub design)

Date: 2026-10-07
Status: design approved in conversation by the owner (2026-10-07); spec under review
Supersedes: the "one bot for several installs" exclusion in
`2026-10-02-talaria-adapters-design.md` §2.4 and §6.

## 1. Goal

One Telegram bot per host manages every Talaria app install (today: Hermes and
Clawvisor). A new app needs one root paste and one setup run — no new bot. The bot
token lives in exactly one place.

## 2. Decisions (owner, 2026-10-07)

1. **Token only in the hub.** A dedicated service user, `talaria` (the hub), holds the
   only bot token and the pairing, and runs the bot and the daily check timer. App users
   (`hermes`, `clawvisor`) never hold the token and never talk to Telegram.
2. **Always a hub**, also with one app. One model, one code path. Existing installs
   migrate once (§7).
3. **App isolation stays.** Each app keeps its own service user, Talaria install, state,
   backups, lock, Quadlet and `changing` marker, exactly as in v0.4.
4. **The hub reaches an app only through `talaria op …`**, via one sudo rule per app.
5. **Self-update is driven from the hub**, approved with a button, hub first, apps next,
   bot restart last.

## 3. Users and files

| | hub (`talaria`) | app (`hermes`, `clawvisor`) |
|---|---|---|
| Talaria install | `~/.local/share/talaria` (release tag) | same, same tag |
| Config | `talaria.conf` with `apps = <app>:<user> …`, `check.time`, `talaria_repo`, `telegram_api` | as v0.4, minus bot/timer use |
| Secrets | `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID` (mode 600) | none for Telegram |
| Units | `talaria-telegram.service`, `talaria-check.service/.timer` | app Quadlet only |

The hub's name defaults to `talaria`; `setup --hub NAME` overrides it. An app may not
use the hub's account. App names in `apps` are the known app names; user names follow
the existing `NAME_RE`.

## 4. The `op` door

### 4.1 Sudo rule (one per app, written by the root paste)
```
talaria ALL=(<appuser>) NOPASSWD: /home/<appuser>/.local/bin/talaria op *
```
(`<appuser>`'s real home from `getpwnam`, not assumed under `/home`.)

### 4.2 Allowed operations
`talaria op` accepts only these, and parses its own arguments strictly (unknown → exit 2,
no side effects):

| op | does |
|---|---|
| `status` | the app's status block |
| `backups` | the app's backups list |
| `check [--timer]` | release check for this app (no Talaria-release check) |
| `deploy TAG` / `reject TAG` | as the CLI |
| `rollback describe` / `rollback confirm` | as `/rollback` and `/rollback CONFIRM` |
| `restore ID describe` / `restore ID confirm` | as `/restore` |
| `button DATA` | validate a tap against current state (§5.2) |
| `self-update TAG` | as the CLI; must stay accepted by every later version |
| `self-update TAG --dry-run` | renders units, reports whether the Quadlet would change |
| `hello` | prints protocol version and app name/title |

Never: `setup`, `set-token`, `login-link`, adopt, `bot`, or anything with a shell.

### 4.3 Output protocol
`op` writes JSON lines to stdout. Each line is an object with `"v": 1` and `"kind"`:
- `message` — a `notify.Message` (`text`, `blocks`, `buttons` with **unprefixed**
  callback data; the hub adds the prefix, §5.2)
- `reply` — the answer to a quick op (status, backups, describe, reject)
- `button` — `{toast, status, run}` where `run` is an op argv or null
- `hello` — `{protocol, app, title, version}`

Anything else on stdout is ignored and logged to the journal; stderr goes to the journal.
Inside `op`, `ctx.notify` is a notifier that emits `message` lines instead of calling
Telegram.

### 4.4 Version check
The hub calls `op hello` (cached per bot start, and after each update). A protocol
mismatch makes every command for that app answer "Talaria versions differ on this host;
run /update" — except `/update` itself, which uses `op self-update` (stable across
versions).

## 5. The hub

### 5.1 Commands
- `/status` — every app, one block each.
- App commands take the app name first; optional when only one app is registered:
  `/backups [app]`, `/check [app]`, `/approve [app] TAG`, `/reject [app] TAG`,
  `/rollback [app] [CONFIRM]`, `/restore [app] ID [CONFIRM]`.
- Several apps and no name: answer "Which app?" with one button per app that runs the
  command's describe/read form for that app (never a confirm).
- `/update vX.Y.Z` — §6.
- The chat menu (`setMyCommands`) is one list, worded app-neutrally.

### 5.2 Buttons
- Callback data is `<app>|<data>` (app names are short; total ≤ 64 bytes, checked when
  building the keyboard as today).
- On a tap the hub runs `op button <data>` for that app; the app validates against its
  state exactly as `Bot.on_button` does in v0.4 and returns `{toast, status, run}`; the
  hub answers the callback, edits the buttons, and starts `run` (§5.3) if set.
- Data without a known app prefix (buttons sent before the migration) → "⌛ Out of date".

### 5.3 Quick vs long operations
- Quick (`status`, `backups`, describe forms, `reject`, `button`): run synchronously with
  a timeout (60 s); send the `reply`.
- Long (`deploy`, `rollback confirm`, `restore … confirm`, `check`): start a transient
  unit in the hub user (`systemd-run --user --collect`), running
  `talaria relay <app> <op argv…>`. The relay runs
  `sudo -n -u <appuser> <apphome>/.local/bin/talaria op …`, sends each `message` line to
  Telegram as it arrives, and if `op` exits non-zero without having sent a message, sends
  "<Title>: <op> failed unexpectedly (exit N)".

### 5.4 Timer
One `talaria-check.timer` in the hub at the hub's `check.time`. Its service runs
`talaria check --timer` in the hub: for each registered app in order, `relay … op check
--timer`; then the single Talaria-release check (§6). An app's own `check.time` becomes
unused; setup warns once if it is set.

### 5.5 Messages
App messages already name the app and pass through unchanged. Hub-made messages (errors,
"Which app?", update reports) name the app's title where they concern one app.

## 6. Updating Talaria itself

- The hub's daily check looks up the latest Talaria release (as v0.4 does per install,
  now once per host). If newer than the hub, it first runs `op self-update vX --dry-run`
  per app and sends: "Talaria vX is available. Hermes would restart: no · Clawvisor would
  restart: yes" with an **Update Talaria to vX** button (`hub|up:vX`). `/update vX` does
  the same; the shell command `talaria self-update vX` (as `talaria`) runs the update
  directly.
- The update runs in a transient unit in the hub user, never inside the bot process:
  1. hub install: fetch, checkout tag, re-render hub units;
  2. each app: `relay … op self-update vX`;
  3. restart `talaria-telegram.service`;
  4. report "Talaria vX installed: Hermes ✓ · Clawvisor ✗ (reason)".
- An app whose update failed keeps its old version; its commands answer per §4.4 until a
  retry succeeds. Each install switches atomically by `git checkout` of the tag.
- Tags must be `vX.Y.Z` from each install's configured `talaria_repo`, as today.

## 7. Setup and migration

### 7.1 Root paste
Built on v0.4.2's one-paste block (`sudo bash -euo pipefail <<'TALARIA' … TALARIA`,
idempotent, sudoers validated with `visudo -cf` before `install`). It contains, as needed:
hub user creation + linger + `operator → talaria` rule (if no hub yet); app user creation
+ linger + `operator → app` rule (if missing); `talaria → app` op rule (if missing). One
paste per setup run, never two.

### 7.2 New host, first app
`bin/talaria setup --app hermes --user hermes` → root paste → second run installs Talaria
for hub and app from the same tag, registers `hermes:hermes` in the hub conf → ACTION
REQUIRED once: the person runs `set-token` as the hub user in their own terminal and sends
the pairing code → DONE.

### 7.3 Adding an app
Root paste (app user, linger, two rules) → setup installs the app, registers it in the hub
conf, restarts the hub bot so it sees the app → DONE. No bot step.

### 7.4 Migrating a v0.4 install
Setup run by the operator from a v0.5 checkout detects an app user with its own
`talaria-telegram.service` and no hub, and plans "move the bot to `talaria`". After the
root paste, in this order, each step idempotent:
1. stop and disable the app's `talaria-telegram.service` and `talaria-check.timer`;
   remove those unit files (only one bot ever polls);
2. move `TELEGRAM_BOT_TOKEN` and `TELEGRAM_USER_ID` from the app's env to the hub's env
   by piping (`sudo -u app` read → `sudo -u talaria` write, mode 600); never printed,
   never in argv; then remove them from the app's env;
3. install and start the hub's units.
Same bot, same owner: no new pairing. The app's Quadlet is not touched and the app is not
restarted (golden test). Re-running setup after an interruption resumes.

### 7.5 Transitional mode (self-update from 0.4.x)
`self-update` cannot create users. An app user on v0.5 code that still has its own token
and no hub runs its bot as today, with `op` executed locally (same user, no sudo) instead
of via `sudo` — the same hub code with a local executor, not separate logic — and sends
once a day: "Talaria v0.5 needs a one-time move of the bot to its own user: run
`bin/talaria setup --app <app> --user <user>` as the operator." Removed in a later release.

## 8. Errors

- `sudo -n` refused (rule missing) → "<Title>: Talaria cannot reach this app (sudo rule
  missing); run setup again".
- `op` timeout (quick ops) → "<Title> did not answer in time".
- Unknown app in a command → "Unknown app: x. Apps: hermes, clawvisor".
- Invalid JSON lines → ignored, logged.

## 9. Testing

- Unit: fake sudo executor and fake Telegram. Commands with/without app; "Which app?";
  prefixed and unprefixed buttons; relay forwarding, crash, timeout, protocol mismatch;
  `op` argument strictness (every disallowed subcommand); update order and partial
  failure; dry-run restart report; migration steps and re-runs; root paste with/without
  hub/app/rules; transitional mode.
- Golden: app Quadlet byte-identical across migration; hub units.
- CI e2e: hub + both test apps on one runner with the fake Telegram API: `/status`,
  approve-and-deploy via buttons for each app, one rollback, `/update`, migration from a
  v0.4-layout install.
- Mutation run before release (threshold 85 %).

## 10. Release and rollout

- v0.4.2 (one-paste root block) ships first, independently.
- v0.5.0: this design.
- This host: migrate Hermes (owner pastes root block; verify one bot polls,
  `hermes.container` identical, Hermes not restarted), then the Clawvisor rollout without
  a second bot, Tailscale networking as decided.

## 11. Out of scope

Several Telegram users or chats; groups; per-app bots; apps on other hosts; a web UI.
