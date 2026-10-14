# Changelog

What changes for you in each release. Update from the bot ("Update Talaria" or
`/update vX.Y.Z`); before you confirm, it tells you which apps would restart.
Releases before v0.7.3 are listed on the
[releases page](https://github.com/maxpfingsthorn/talaria/releases).

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
