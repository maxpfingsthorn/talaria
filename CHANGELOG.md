# Changelog

What changes for you in each release. Update from the bot ("Update Talaria" or
`/update vX.Y.Z`); before you confirm, it tells you which apps would restart.

## Unreleased

- **Signed releases.** Release tags are signed with SSH keys (fingerprints in the README).
  Setup and updates install a tag only if it is signed by a key in `keys/allowed_signers`
  of the install doing the check. An unsigned tag changes nothing and says so:
  `self-update refused: <tag> is not signed by a trusted key (<reason>)` from an app,
  `Talaria <tag> was not installed: it is not signed by a trusted key (<reason>). Nothing
  changed.` from the hub, and in the update offer `<App> would restart: unknown (not signed
  by a trusted key)`. Setup stops with `STOP: this checkout's tag is not signed by a trusted
  key` and lists the trusted keys; `setup --dev` skips the check and says so. Installs on
  v0.7.x have no such check, so the update to the release that introduces it is trusted on
  GitHub alone; every update after it is verified. If every signing key is ever lost,
  installs refuse all later releases (see [Operations](docs/operations.md)).
- **New prerequisites.** `git` 2.34 or newer and `ssh-keygen` (package `openssh-client`).
  Setup reports them as `MISSING` before it changes anything.
- **Your sudo rule asks for a password.** New rules for your login account no longer
  skip the password. Pass `--operator-nopasswd` to keep the old behaviour. Existing rules
  are never changed: setup prints a `NOTE` with a root paste that tightens them (optional).
  If you have passwordless sudo from somewhere else (a blanket `NOPASSWD: ALL`), setup
  notices, keeps its rules passwordless (a password rule would override that and lock you
  out), and prints one NOTE and no tightening paste. Setup asks for the
  password once (`sudo -v`) when it has a terminal, and stops with `STOP: setup needs your
  sudo password; run it in your own terminal` when it has none.

- **Restore isolation.** Carrying excluded files across a restore no longer follows
  symlinked source parents. An app cannot use `home/.cache` to move files from the
  service account's home into its container-visible data.
  Extraction completion is now recorded outside the app's data, so archived files and
  symlinks cannot forge that marker or redirect its writes.
- **Interrupted restores.** `/rollback CONFIRM` can recover an explicit restore killed
  between directory renames, including if recovery itself is interrupted.
- **gbrain rehearsals.** Recovery state is recorded before stopping the server to copy
  its brain. If the rehearsal process is killed, `/rollback CONFIRM` starts the unchanged
  app again. The README now calls out this downtime before approval.
- **Published Talaria releases.** Daily notices and the setup instructions select
  published stable GitHub Releases, with no fallback to tags on an API failure.
  `bin/talaria latest-release` prints the selected version. This discovery behavior
  takes effect once this Talaria version is installed.

## v0.7.4 (2026-10-20)

- **`/restart [app]`.** Stops and starts an app's container: same image, same data, no
  backup. Use it, for example, after logging an app in to a new provider. Talaria checks
  that the app came back and tells you. If it did not, `/rollback CONFIRM` starts it
  again (nothing is restored). The command is refused while an interrupted change waits
  for `/rollback CONFIRM`. With several apps, name the app: `/restart hermes`. It works
  once the app runs v0.7.4, so update Talaria first. As the app's account, it is
  `talaria restart`.
- **Hermes dashboard in `/status`.** While Hermes runs, its status shows
  `Dashboard: <address> (user admin)`. That is `dashboard.public_url` if you set one,
  otherwise the first address the dashboard is published on.

## v0.7.3 (2026-10-14)

- **Pinned Talaria updates.** The hub tells every app which commit it installed, and an app
  refuses an update whose tag points somewhere else. This applies from the update after
  this one, because v0.7.2 still runs the update to v0.7.3.
- **No accidental downgrades.** An app that already runs a newer Talaria than the hub is
  skipped by `/update` instead of being moved back, and the hint tells you to update the
  hub.
- **`/status` stays fast.** It answers within about 10 seconds even if one app hangs.
- **Login links.** If Telegram can't confirm that a link was delivered, you still get the
  "expired" notice five minutes later.
- **gbrain.** An update offer says "Build provenance not verified: …" when the release's
  attestation could not be checked (for example, `gh` is not logged in for that account).
- **Hermes.** New releases are found among all registry tags, not just the first 1000.
- **Rehearsals.** Copying the data for a rehearsal can no longer be tricked into following
  a symlink planted in the data folder.
- **Admin redirect tool.** It no longer claims "nothing was applied" when it can't be sure.

## v0.7.2 (2026-10-11)

- **Hub:** a reminder when an app lags behind the hub's version, `/status` re-probes an app
  before reporting it as lagging, and the login button shows only for running apps.
- **Stricter `tailscale_ip`:** the Quadlet is not rendered for `0.0.0.0`, multicast or
  public addresses.
- **Clearer messages** in setup, the rollback offer and the CLI. The `/admin` redirect tool
  keeps its backup unless the configuration is unchanged.

## v0.7.1 (2026-10-11)

- **v0.7.0 contents.** The changes of the withdrawn v0.7.0 (below) are part of this release.
- Housekeeping: the root `/admin` redirect tool lives in `tools/`.

## v0.7.0 (withdrawn)

Its release run failed and it was never published; v0.7.1 contains the same changes.

- **`/login [app]`.** The bot sends a one-time login link for Clawvisor or gbrain; the link
  is replaced with an expiry notice after five minutes. It needs the app to run this
  version or newer. Without the bot, `talaria login-link` as the app's account does the same.
- **gbrain `/admin` on 443.** A helper (`tools/tailscale-admin-redirect.py`, run by you as
  root) adds or undoes a redirect from `/admin` on the public port to the private one.

## v0.6.1 (2026-10-11)

- **Failed updates are undone.** A failed Talaria update restores the previous version and
  re-runs its setup; only the wanted tag is fetched.
- **Version skew.** Talaria detects an app on another version than the hub, retries, and a
  failed `hello` heals without a bot restart.
- **Interrupted changes** during deploy, rollback or maintenance can always be recovered
  with `/rollback CONFIRM`.
- **Release notice runs no new code** until you tap the button.
- **Stricter input in setup:** duplicate keys, port range, booleans, `tailscale_ip` and
  `check.time` are checked. Running installs keep working with the files they have.
- **Symlinks.** Login links, gbrain setup files, rehearsal copies and restores never write
  through symlinks in the app's data.
- **gbrain docs:** public access on port 443, and the `chat_model` / `expansion_model` pins.

## v0.6.0 (2026-10-09)

- **gbrain** is the third supported app (`--app gbrain`): a local image built from the
  digest- and attestation-verified release binary, first-run setup, offline rehearsal and
  schema check. Public access is up to you (Tailscale, see docs/apps/gbrain.md).
- **Per-app schedule.** `check.days` sets which days an app is checked; `maintenance.time`
  sets a nightly window in which the hub stops the app, runs its maintenance (gbrain:
  `dream`) and starts it again (`talaria-maintain.timer`, default 01:30 for gbrain).
- Rehearsals stop apps that must be quiet while their data is copied.

## v0.5.4 (2026-10-08)

- **Clawvisor** runs with `HOME=/data`, so custom adapters in `.clawvisor/adapters/` survive
  restarts and are backed up and rolled back with the data. The login session file is
  excluded from backups and rehearsal copies.

## v0.5.3 (2026-10-08)

- Unknown keys in `talaria.conf` and `hub.conf` are ignored with a warning at runtime
  (setup still refuses them), so a newer config does not break an older install.
- The hub's update report and relay failures include the app's last error line.

## v0.5.2 (2026-10-08)

- **`/check`** runs the full check for every app and Talaria itself and always ends with a
  summary; `/check talaria` checks only the Talaria release. The daily timer stays silent
  when nothing is new.
- **Clawvisor:** `dashboard.public_url` sets its public address (Google sign-in, approval
  links) and `talaria login-link` uses it.

## v0.5.1 (2026-10-08)

- Fixes `talaria login-link` for Clawvisor (podman started from `/`, writable `HOME`).

## v0.5.0 (2026-10-08)

- **One bot for all apps (the hub).** A hub account (`talaria`) runs the only Telegram bot
  and the daily check; it reaches each app only through `sudo -u <app> talaria op …`. Buttons
  and commands name the app (`/status hermes`).
- **Setup is one root paste** for the hub, the app account and the sudo rules.
- **Updating Talaria** is host-wide: hub first, then each app, then the bot restart. It
  refuses downgrades and apps older than v0.5.0.
- **Moving from v0.4.** An install with its own bot moves it to the hub: same bot and chat,
  no new pairing (see [Operations](docs/operations.md)).
