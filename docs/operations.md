# Operations

## Updating Talaria

Daily update notices use published stable GitHub Releases. `bin/talaria latest-release`
prints the same selection for the default repository without requiring an installation; it
does not read a `talaria_repo` set in `hub.conf`. API failures are reported
as lookup failures; Talaria does not fall back to unpublished tags.

An explicit `/update <tag>` or `talaria self-update <tag>` remains an operator-selected
version, including for development. Use the published release unless you intend to test
another tag.

The hub checks for new Talaria releases daily and sends a notice with a **What would
vX.Y.Z change?** button; tapping it (or `/update vX.Y.Z`) lists which apps the update would
restart, with an **Update Talaria to vX.Y.Z** button.
The update runs hub first, then each app, then restarts the bot, and reports per app. From
a shell, as the hub:

```bash
sudo -u talaria -H ~talaria/.local/bin/talaria self-update vX.Y.Z
```

The bot refuses a version that is not newer than the one installed (no downgrades), and
the apps refuse releases older than v0.5.0 (the first with a hub interface). If an update fails on an account, that
account's previous version is restored and its setup re-run (units re-rendered, the app
restarted if needed); an app whose update failed keeps its version, and
keeps working: `/status` adds a note that it is not at the hub's version, and the daily
check still runs for it. Only a different protocol (or an app that does not answer `hello`)
blocks its commands with "Talaria versions differ on this host; run /update" until an
update succeeds (the bot re-checks the app on each command, so it needs no restart
afterwards). `/update vX.Y.Z` with the version the hub already runs is accepted while an app
lags, and updates only the lagging apps; with none lagging it says so.

### Refused updates

From v0.8.0 on, an update installs a tag only if it is signed by a key in
`keys/allowed_signers` of the install doing the check. A refusal changes nothing; the
reason comes last, in parentheses:

- app: `self-update refused: <tag> is not signed by a trusted key (<reason>)`;
- hub: `Talaria <tag> was not installed: it is not signed by a trusted key (<reason>).
  Nothing changed.`; the update offer then says `<App> would restart: unknown (not signed by
  a trusted key)`.

Do not update to that tag. A tag that is not signed, signed by another key, or one your
install cannot fetch is refused; so is any install without `keys/allowed_signers`
(`this install has no trusted release keys`). Check the release page and the fingerprints in
the README; if the tag is genuine and signed by a key you trust, update by hand (re-clone
and setup, below). v0.7.x installs have no check, so the update to v0.8.0 is trusted on
GitHub alone.

**Lost signing keys.** If every signing key is lost, installs refuse all future releases.
Recovery is manual: clone the repository again, check out a tag you have verified yourself,
and run `bin/talaria setup` (add `--dev` to skip the signature check). Setup keeps your
configuration, data and backups.

**Key rotation.** Add the new key to `keys/allowed_signers` in a release signed by an
existing key. Installs pick the new list up with that release, and trust it for the release
after it.

**v0.6.1.** The step from v0.6.0 to v0.6.1 is still run by v0.6.0's self-update code, so
the new behaviour of a failed update (previous version restored, its setup re-run, only
the wanted tag fetched) applies from the update after that. Self-update itself keeps
accepting every `talaria.conf` and `hub.conf` that v0.6.0 accepted; the stricter value
checks (duplicate keys, port range, booleans, `tailscale_ip`, `check.time`) apply only
when a person runs `talaria setup`.

### Moving from v0.4

v0.4 ran one bot per app. After `self-update v0.5.0`, an install keeps its own bot and
reminds you daily to move it: from a v0.5 checkout, as your login account, run
`bin/talaria setup --app <app> --user <user>`. Setup prints one root paste (the hub account
and its rules), stops the app's own bot, moves its token and pairing to the hub — the same
bot and chat, no new pairing — and starts the hub's bot. The app is not restarted. If the
hub already has a bot, the app's own token is dropped. If an app still runs its own bot,
setup of another app stops ("set <app> up first") until that app has been moved, so its
bot is not left polling alongside the hub's; and it stops if the old bot is still running.

## Uninstall

As the hub (`talaria`):

```bash
systemctl --user disable --now talaria-check.timer talaria-maintain.timer talaria-telegram.service
rm ~/.config/systemd/user/talaria-*
rm -r ~/.local/share/talaria ~/.local/bin/talaria ~/.config/talaria
systemctl --user daemon-reload
```

As each app's service user:

```bash
systemctl --user stop hermes.service
rm ~/.config/containers/systemd/hermes.container
rm -r ~/.local/share/talaria ~/.local/bin/talaria ~/.config/talaria
systemctl --user daemon-reload
podman images --format '{{.Repository}}:{{.Tag}}' localhost/hermes-agent | xargs -r podman image rm
```

Then as root: `rm /etc/sudoers.d/talaria-*` and `loginctl disable-linger <user>` for each
account (or `userdel -r <user>` if the account served nothing else; that also deletes the
data).

Your data dir and `~/.local/state/talaria/backups` are left untouched.
