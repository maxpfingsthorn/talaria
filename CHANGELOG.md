# Changelog

What changes for you in each release. Update from the bot ("Update Talaria" or
`/update vX.Y.Z`); before you confirm, it tells you which apps would restart.
Releases before v0.7.3 are listed on the
[releases page](https://github.com/maxpfingsthorn/talaria/releases).

## Unreleased

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
- **Release process.** Maintainers now run the release workflow on `main` with a version
  input. It runs every gate before creating the tag, protecting older clients that
  discover tags directly. Do not push release tags by hand.

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
