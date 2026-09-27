# Talaria

Safe, approved updates for self-hosted [Hermes Agent](https://github.com/NousResearch/hermes-agent)
on rootless podman. Opinionated.

Setting this up with a coding agent? Point it at [`AGENTS.md`](AGENTS.md).

## What it does

- Finds new Hermes releases and pulls them, pinned by digest and checked against the
  release's git commit.
- **Rehearses the migration on a throwaway copy** of your data and reports what would
  change: config version, database version, which migrations ran, a config diff.
- **Waits for your approval** on Telegram.
- Backs up, deploys and checks the result. On failure it **rolls back image and data
  together**. You can trigger the same rollback at any time.
- Messages you only for decisions and failures.

Opinionated: one Hermes per host, one dedicated service user, rootless podman with
Quadlet, Telegram for approvals.

## Requirements

- Linux with systemd user units and linger (tested on Ubuntu 24.04).
- Rootless podman ≥ 4.9 with Quadlet.
- `git`, `python3` ≥ 3.10, GNU `tar`, `gzip`, `sudo`.
- amd64 or arm64.
- Hermes ≥ v2026.6.5 (existing installs older than that are refused).
- SELinux in enforcing mode is not supported.

## Setup by hand

```bash
git clone https://github.com/maxpfingsthorn/talaria && cd talaria
git checkout "$(git tag -l 'v*' --sort=-v:refname | head -1)"
bin/talaria setup --plan     # what would happen
bin/talaria setup            # run again until it prints DONE
```

Setup never guesses. Each run does what it can and stops at the next thing only you
can do:

```
MISSING: podman >= 4.9 (found 4.3.1)
  hint: Debian/Ubuntu: apt install podman · Fedora/RHEL: dnf install podman · Arch: pacman -S podman
ACTION REQUIRED: run this block as root, then run setup again: …
ACTION REQUIRED: in a private chat with your bot, send within 15 minutes:
  /pair K7M2QX9P
FOUND: Hermes unit hermes-gateway.service (container hermes-gateway)
STOP: several Hermes installs; choose one with --adopt UNIT
DONE
```

Exit codes: 0 done · 10 a person must act · 1 stop or error.

The bot token is never typed into a chat or an agent. You store it yourself with
`sudo -u <service user> -H ~<service user>/.local/bin/talaria set-token`.

## What setup changes

- A service user (default `hermes`), linger for it, and the sudo rule
  `/etc/sudoers.d/talaria-<user>` that lets your login account act as it.
- For the service user:
  - `~/.local/share/talaria` (this repo at a release tag) and `~/.local/bin/talaria`;
  - `~/.config/talaria/` (`talaria.conf`, `.env` with the bot token, `hermes.env` with
    the dashboard password);
  - `~/.local/state/talaria/` (state, backups, history);
  - `~/.config/containers/systemd/hermes.container`;
  - `talaria-check.timer` and `talaria-telegram.service`.
- Adopting an existing install renames its Quadlet to `*.talaria-orig`, takes a full
  backup first, and keeps the data where it is.

## Daily use

| Telegram command | Effect |
|---|---|
| `/status` | Hermes version and state, free disk, pending update, interrupted change |
| `/check` | look for a release now |
| `/approve <tag>` | deploy the pending update |
| `/reject <tag>` | never offer this release again |
| `/rollback` | describe what a rollback would restore, and how old the backup is |
| `/rollback CONFIRM` | roll back |
| `/backups` | list backups |
| `/restore <id>` / `/restore <id> CONFIRM` | describe / restore a backup |

You get a message when an update is ready, after a deploy, rollback or restore, when
something fails, after an interrupted change, and once per new Talaria release.
Nothing else.

**A rollback loses everything Hermes wrote since its backup.** `/rollback` shows the
backup's age before you confirm.

## Dashboard

The dashboard always requires a login: user `admin`, password in
`~/.config/talaria/hermes.env` of the service user.

- Default (`dashboard.bind = loopback`): `ssh -L 9119:127.0.0.1:9119 <host>`, then open
  `http://127.0.0.1:9119`.
- Tailscale: set `dashboard.bind = tailscale` in `talaria.conf` and run setup again.
  The dashboard is then reachable on the host's Tailscale address only.

It is never published on a public interface.

## Configuration

`~/.config/talaria/talaria.conf` of the service user, `key = value`:

| Key | Default |
|---|---|
| `data_dir` | `~/hermes-data` |
| `image` | `docker.io/nousresearch/hermes-agent` |
| `hermes_repo` | `https://github.com/NousResearch/hermes-agent` |
| `talaria_repo` | this repository |
| `dashboard.bind` | `loopback` (or `tailscale`) |
| `dashboard.port` | `9119` |
| `backup.keep` | `5` |
| `backup.exclude` | `.cache .npm home/.cache home/.npm backups` (`backups/config` is always kept) |
| `disk.floor_gb` | `6` |
| `check.time` | `04:30` |

Run setup again after changing it.

## Security

| Party | Trust |
|---|---|
| The paired Telegram account | trusted |
| Your login account and coding agents running as it | fully trusted, through the sudo rule |
| The Hermes agent, its data dir and everything in it | untrusted |
| Upstream images and their output | untrusted beyond digest and revision checks |
| This repository | trusted as cloned; install from the canonical URL at a tag |

- The agent cannot reach the updater: Talaria's files are outside the container's only
  mount, and the container sees only its data and the dashboard password.
- Upstream code never runs on the host. Rehearsal helpers run in containers without a
  network.
- One commander, paired with a code only the person at the terminal sees. Everyone else
  is ignored.
- Text from the agent or upstream is shown inert in messages. Destructive commands need
  `CONFIRM` and say what they destroy. Secret-looking config values are masked.
- Restores extract into new directories; nothing follows symlinks from the data dir.
- No root after setup.

Known limitations:

- Change Hermes images only through Talaria. Starting another image by hand on
  migrated data is unsupported.
- `.env` backup copies that Hermes itself writes stay in the data dir and in backups.
- A rollback loses everything written since its backup.

## Manual recovery

If a rollback itself fails, Hermes stays stopped and the file
`~/.local/state/talaria/changing` exists (it keeps systemd from starting Hermes on
half-changed data). As the service user:

1. `talaria backups` and pick a backup.
2. `talaria restore <id> --confirm`.
3. If that fails too:
   ```bash
   systemctl --user stop hermes.service
   mv <data_dir> <data_dir>.broken && mkdir <data_dir>
   tar -xzf ~/.local/state/talaria/backups/<id>.tar.gz -C <data_dir>
   rm ~/.local/state/talaria/changing
   systemctl --user start hermes.service
   ```

## Removing the sudo rule

After setup you can remove it: `sudo rm /etc/sudoers.d/talaria-<user>`. Talaria keeps
working; only `talaria setup` from your login account needs it again.

## Updating Talaria

`/status` and a one-time message tell you about new releases. As the service user:
`talaria self-update vX.Y.Z`.

## Uninstall

As the service user:

```bash
systemctl --user disable --now talaria-check.timer talaria-telegram.service hermes.service
rm ~/.config/containers/systemd/hermes.container ~/.config/systemd/user/talaria-*
rm -r ~/.local/share/talaria ~/.local/bin/talaria ~/.config/talaria
systemctl --user daemon-reload
```

Your data dir and `~/.local/state/talaria/backups` are left untouched.

## Development

```bash
uv sync
uv run pytest                                          # unit tests
TALARIA_E2E=1 uv run pytest -m e2e tests/e2e          # disposable machine only (creates users)
TALARIA_CONTRACT=1 uv run pytest -m contract tests/contract   # pulls the newest Hermes image
uv run mutmut run && uv run mutmut export-cicd-stats && uv run python tests/mutation_score.py
```

CI runs unit and end-to-end tests on every push, and the contract test and mutation
testing weekly and on release. See [`docs/mutation-report.md`](docs/mutation-report.md).

Licence: MIT.
