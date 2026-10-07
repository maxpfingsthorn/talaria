# Talaria

Safe, approved updates for self-hosted [Hermes Agent](https://github.com/NousResearch/hermes-agent)
on rootless podman. Opinionated.

Setting this up with a coding agent? Point it at [`AGENT_SETUP.md`](AGENT_SETUP.md).

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

Scope: Talaria manages Hermes installs that run from the official container image. It
does not cover the git-based `hermes update` path.

Talaria is an independent project and is not affiliated with or endorsed by Nous
Research.

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
bin/talaria setup --plan --user hermes   # what would happen
bin/talaria setup --user hermes          # run again until it prints DONE
```

`--user` names the service account; setup creates it (through a block you run as root)
if it does not exist. Clone over https as shown: setup installs Talaria for the service
user from your checkout's origin, and that user has no SSH key.

`--app hermes|clawvisor` picks which app to manage (default `hermes`; the account
defaults to the app's own name, e.g. `clawvisor`, unless `--user` says otherwise). Once
`talaria.conf` exists, its `app` wins: re-running setup with a different `--app` stops
with an error instead of silently switching apps.

Setup never guesses. Each run does what it can and stops at the next thing only you
can do:

```
MISSING: podman >= 4.9 (found 4.3.1)
  hint: Debian/Ubuntu: apt install podman · Fedora/RHEL: dnf install podman · Arch: pacman -S podman
ACTION REQUIRED: run this block as root, then run setup again with --user hermes: …
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
| `/status` | Hermes version and state, free disk, data size, pending update, interrupted change |
| `/check` | look for a release now |
| `/approve <tag>` | deploy the pending update |
| `/reject <tag>` | never offer this release again |
| `/rollback` | describe what a rollback would restore, and how old the backup is |
| `/rollback CONFIRM` | roll back |
| `/backups` | list backups |
| `/restore <id>` / `/restore <id> CONFIRM` | describe / restore a backup |

The bot registers these commands with Telegram, so they appear under the **Menu**
button in your chat (and nowhere else). Update offers come with **Approve** and
**Reject** buttons; `/rollback` and `/restore <id>` answer with a confirm button, so
you never have to type `CONFIRM`. Each button names what it acts on and is checked
against the current state: an old button is refused instead of acting on something
else. Rollback and restore buttons expire after an hour. After a tap, the buttons are replaced by one status button (for example "✅ Approved — deploying vX"); the report above stays.

You get a message when an update is ready, after a deploy, rollback or restore, when
something fails, after an interrupted change, and once per new Talaria release.
Nothing else.

**A rollback replaces the data with the backup taken before the last deploy.** `/rollback`
shows that backup's age before you confirm. Talaria first backs up the current data, and
the rollback message names the `/restore <id> CONFIRM` that undoes it.

## Dashboard

The dashboard always requires a login: user `admin`, password in
`~/.config/talaria/hermes.env` of the service user.

- Default (`dashboard.bind = loopback`): `ssh -L 9119:127.0.0.1:9119 <host>`, then open
  `http://127.0.0.1:9119`.
- Tailscale: set `dashboard.bind = tailscale` in `talaria.conf` and run setup again.
  The dashboard is then reachable on the host's Tailscale address only.

Public IPv4 addresses are rejected, but a private address is only as private as its network:
use a "LAN address" only on a network you trust (home or office). On a cloud VM the
private/VPC address may be reachable from elsewhere (1:1 NAT, provider network), so prefer
Tailscale or an SSH tunnel there.

After editing `talaria.conf`, run setup again: it validates the file (a bad value would stop
the bot at its next start).

Clawvisor has no dashboard password: its first login is a one-time link. As the service
user, in your own terminal (never through an agent): `talaria login-link`.

## Configuration

`~/.config/talaria/talaria.conf` of the service user, `key = value`. Defaults below are
Hermes's; a Clawvisor install (`app = clawvisor`) gets its own defaults for `data_dir`,
`dashboard.port`, `repo`, `image`, `min_release` and `backup.exclude`.

| Key | Default |
|---|---|
| `data_dir` | `~/hermes-data` |
| `image` | `docker.io/nousresearch/hermes-agent` |
| `repo` (`hermes_repo` also accepted) | `https://github.com/NousResearch/hermes-agent` |
| `talaria_repo` | this repository |
| `dashboard.bind` | `loopback`; or a space-separated list of 1-3 of `loopback`, `tailscale` and private IPv4 addresses (never `0.0.0.0`, public or other `127.x` addresses). The first is the primary address (health checks); each is published |
| `dashboard.port` | `9119` |
| `backup.keep` | `5` |
| `backup.exclude` | `.cache .npm home/.cache home/.npm backups` (`backups/config` is always kept) |
| `disk.floor_gb` | `6` |
| `check.time` | `04:30` |
| `host_loopback` | `false` (`true`: the container may reach the host's loopback at `10.0.2.2`; needs `slirp4netns`) |
| `add_hosts` | (none; space-separated `name:ip` pairs, e.g. `clawvisor:10.254.254.1`) |

Run setup again after changing it.

## Clawvisor

Talaria can also manage [Clawvisor](https://github.com/clawvisor/clawvisor) the same
way it manages Hermes: release detection, a rehearsal on a copy, Telegram approval,
backup, deploy, verify, and rollback of image and data together.

One Talaria install manages one app. To run both Hermes and Clawvisor on the same
host, set Clawvisor up as its own service user, separate from Hermes's:

```bash
bin/talaria setup --plan --app clawvisor --user clawvisor
bin/talaria setup --app clawvisor --user clawvisor
```

Clawvisor's README warns that an agent sharing an environment with it can read its
database, so it never shares a user, a podman network or a data dir with Hermes.

**Clawvisor gets its own, second Telegram bot.** Pair it exactly like Hermes's bot
(`set-token` as the `clawvisor` service user, then `/pair CODE` in a chat with that
bot) — just with a different bot token. One bot managing several installs is a
later wish, not supported today.

Clawvisor has no dashboard password; its first login is a single-use, short-lived
link. As the `clawvisor` service user, **in your own terminal** (never through a
coding agent): `talaria login-link`. Treat its output like a password — **never
paste it into a chat, a ticket, an agent's context, or anywhere it could be
logged.** If an agent is driving setup, have it stop here and let the person at
the keyboard run this step and use the link themselves.

Hermes and Clawvisor run as two rootless podman users, so no podman network joins them.
To let Hermes reach Clawvisor (`http://clawvisor:25297`), pick one option. Each time,
re-run setup for the app whose `talaria.conf` you changed. Clawvisor's own dashboard,
on a dummy NIC or loopback, is reached from your laptop through an SSH tunnel
(`ssh -L 25297:<address>:25297 <host>`, then open the login link with the address replaced
by `127.0.0.1`; `login-link` prints that hint). To also reach it from your browser without a
tunnel, list a second address: `dashboard.bind = 10.254.254.1 tailscale` publishes it on the
dummy NIC (for Hermes) and on your Tailscale address (for your browser); `login-link` then
prints one link per address.

**1. Dummy NIC (recommended).** Only services bound to its address are exposed, but that
holds for the host only: a neighbour on your LAN that routes `10.254.254.1` via your server,
or rootful containers on the same host, can still reach it. As root,
once (the address is arbitrary private space; pick one unused on your networks):

```bash
cat >/etc/systemd/network/10-talaria0.netdev <<'EOF2'
[NetDev]
Name=talaria0
Kind=dummy
EOF2
cat >/etc/systemd/network/10-talaria0.network <<'EOF2'
[Match]
Name=talaria0

[Link]
RequiredForOnline=no

[Network]
Address=10.254.254.1/32
EOF2
networkctl reload    # needs systemd-networkd running (default on Ubuntu Server)
```

Optional: drop traffic to that address unless it arrives on `lo` (rootless podman and host
processes reach it through `lo`, so nothing intended breaks). Not persistent; persist it via
your distribution's `nftables.conf`:

```bash
nft add table inet talaria; nft add chain inet talaria in '{ type filter hook input priority 0; }'; nft add rule inet talaria in ip daddr 10.254.254.1 iifname != "lo" drop
```

- Clawvisor's `talaria.conf`: `dashboard.bind = 10.254.254.1`
- Hermes's `talaria.conf`: `add_hosts = clawvisor:10.254.254.1`

**2. Tailscale.** If you already use it (it also gives remote dashboard access).

- Clawvisor's: `dashboard.bind = tailscale` (setup records `tailscale_ip`)
- Hermes's: `add_hosts = clawvisor:<tailscale ip>`

**3. Host loopback (no root).** Clawvisor stays on `127.0.0.1`. Hermes's container joins
`slirp4netns:allow_host_loopback=true` and sees the host's loopback at `10.0.2.2`.
Needs the `slirp4netns` package. **Warning:** the Hermes container can then reach every
service listening on the host's loopback, not just Clawvisor.

- Clawvisor's: nothing (default `dashboard.bind = loopback`)
- Hermes's: `host_loopback = true` and `add_hosts = clawvisor:10.0.2.2`

In all three, the two service users never share a podman network.

Clawvisor keeps everything in SQLite (WAL mode, one connection); it publishes no
container image, so Talaria downloads each release's binary, checks its SHA-256
against the published checksums, and builds a local image from a pinned distroless
base. Its log tables (`audit_log`, `gateway_request_log`, `runtime_events`) are
never pruned upstream and grow without bound — `/status` reports the data
directory's size so you notice before disk space runs out; Talaria itself does
not prune them.

Google OAuth login (it needs an https redirect) is out of scope; use the one-time
login link instead. Adopting an existing Clawvisor install is also out of scope —
only a fresh install is supported.

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
  `CONFIRM` and say what they destroy. Config diffs mask values under secret-looking
  keys, after secret-looking flags, in URL credentials and parameters, and values that
  look like tokens. This is a heuristic; review what the bot shows you.
- Talaria never follows symlinks in the data dir: backups and rehearsal copies keep them
  as links, and restores extract into new directories.
- The adoption plan shows only variable names on `Environment=` lines and hides
  `PodmanArgs=`, `Exec=` and `Secret=` lines entirely.
- No root after setup.

Known limitations:

- Change Hermes images only through Talaria. Starting another image by hand on
  migrated data is unsupported.
- `.env` backup copies that Hermes itself writes stay in the data dir and in backups.
- A rollback while Hermes is blocked by an interrupted change takes no extra backup: the
  data it replaces is the half-changed state.
- While a change is interrupted, Talaria refuses to deploy or rehearse anything new until
  you recover with `/rollback CONFIRM`.

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

`/status` and a one-time message tell you about new releases. Then:

```bash
sudo -u <service user> -H ~<service user>/.local/bin/talaria self-update vX.Y.Z
```

## Uninstall

As the service user:

```bash
systemctl --user disable --now talaria-check.timer talaria-telegram.service
systemctl --user stop hermes.service
rm ~/.config/containers/systemd/hermes.container ~/.config/systemd/user/talaria-*
rm -r ~/.local/share/talaria ~/.local/bin/talaria ~/.config/talaria
systemctl --user daemon-reload
podman images --format '{{.Repository}}:{{.Tag}}' localhost/hermes-agent | xargs -r podman image rm
```

Then as root: `rm /etc/sudoers.d/talaria-<user>` and `loginctl disable-linger <user>`
(or `userdel -r <user>` if the account served nothing else; that also deletes the data).

Your data dir and `~/.local/state/talaria/backups` are left untouched.

## Architecture

Talaria is a small app-neutral core (state, backup, deploy, rollback, restore,
rehearsal, setup, notify) plus a thin per-app adapter under `talaria/apps/` that
supplies everything that differs between the apps Talaria can manage: release
discovery and image fetch, health and data-version checks, quadlet template
variables, secret setup and the texts shown to you. `ctx.app` is the adapter in
use; `talaria.apps.get(name)` looks one up by the `app` key in `talaria.conf`.
`talaria/apps/hermes.py` and `talaria/apps/clawvisor.py` are the adapters today.

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
