# Security

| Party | Trust |
|---|---|
| The paired Telegram account | trusted |
| Your login account and coding agents running as it | fully trusted, through the sudo rule |
| The Hermes agent, its data dir and everything in it | untrusted |
| Upstream images and their output | untrusted beyond digest and revision checks (integrity against what the same release publishes, not an independent signature) |
| This repository | trusted as cloned; install from the canonical URL at a tag |
| The hub account (`talaria`) | holds the bot token; may run only `talaria op …` as each app |
| gbrain's public connector paths (Tailscale Funnel) | untrusted; DCR clients stay pending until you approve them in /admin |

- The agent cannot reach the updater: Talaria's files are outside the container's only
  mount, and the container sees only its data and the dashboard password.
- Upstream code never runs on the host. Rehearsal helpers run in containers without a
  network.
- One commander, paired with a code only the person at the terminal sees. Everyone else
  is ignored.
- Text from the agent or upstream is shown inert in messages. Destructive commands need
  `CONFIRM` and say what they destroy. Config diffs mask values under secret-looking
  keys, after secret-looking flags, in URL credentials and parameters, and values that
  look like tokens. This is a heuristic; review what the bot shows you.
- Talaria never follows symlinks in the data dir: backups and rehearsal copies keep them
  as links, and restores extract into new directories.
- The adoption plan shows only variable names on `Environment=` lines and hides
  `PodmanArgs=`, `Exec=` and `Secret=` lines entirely.
- The hub reaches an app only through `talaria op`, which accepts a fixed list of operations (status, check, deploy, rollback, restore, button, self-update, …) and nothing with a shell. App accounts hold no bot token.
- No root after setup.

Known limitations:

- Change Hermes images only through Talaria. Starting another image by hand on
  migrated data is unsupported.
- `.env` backup copies that Hermes itself writes stay in the data dir and in backups.
- A rollback while Hermes is blocked by an interrupted change takes no extra backup: the
  data it replaces is the half-changed state.
- While a change is interrupted, Talaria refuses to deploy or rehearse anything new until
  you recover with `/rollback CONFIRM`.

## What setup changes

- The hub account (default `talaria`) and the app's service account (default `hermes`),
  linger for both, and three sudo rules, each checked with `visudo` before it is installed:
  - `/etc/sudoers.d/talaria-<user>` and `/etc/sudoers.d/talaria-talaria`: your login account
    may act as the app's account and as the hub;
  - `/etc/sudoers.d/talaria-talaria-<user>`: the hub may run
    `~<user>/.local/bin/talaria op …` as the app's account — nothing else.
- For the hub:
  - `~/.local/share/talaria` and `~/.local/bin/talaria` (the same release tag as the apps);
  - `~/.config/talaria/hub.conf` (registered apps, check time) and `.env` with the bot token;
  - `talaria-check.timer`, `talaria-maintain.timer` and `talaria-telegram.service`.
- For the app's account:
  - `~/.local/share/talaria` (this repo at a release tag) and `~/.local/bin/talaria`;
  - `~/.config/talaria/` (`talaria.conf`, and `<app>.env` with the app's secrets, e.g.
    `hermes.env` with the dashboard password);
  - `~/.local/state/talaria/` (state, backups, history);
  - `~/.config/containers/systemd/<app>.container`.
- Adopting an existing install renames its Quadlet to `*.talaria-orig`, takes a full
  backup first, and keeps the data where it is.
- Setup never moves a Talaria install backwards: if the hub already runs a newer release
  than your checkout, it stops and asks you to check out the newer tag.

## Removing the sudo rules

After setup you can remove your own rules: `sudo rm /etc/sudoers.d/talaria-<user>
/etc/sudoers.d/talaria-talaria`. Talaria keeps working; only `talaria setup` from your
login account needs them again. Keep `/etc/sudoers.d/talaria-talaria-<user>`: the bot needs
it to reach the app.
