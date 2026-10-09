# Talaria

Safe, approved updates for self-hosted [Hermes Agent](https://github.com/NousResearch/hermes-agent)
on rootless podman. Opinionated.

Setting this up with a coding agent? Point it at [`AGENT_SETUP.md`](AGENT_SETUP.md).

Talaria manages Hermes, [Clawvisor](#clawvisor) and [gbrain](#gbrain), each in its own account under one bot.

## What it does

- Finds new Hermes releases and pulls them, pinned by digest and checked against the
  release's git commit.
- **Rehearses the migration on a throwaway copy** of your data and reports what would
  change: config version, database version, which migrations ran, a config diff.
- **Waits for your approval** on Telegram: one bot per host for every app Talaria manages.
- Backs up, deploys and checks the result. On failure it **rolls back image and data
  together**. You can trigger the same rollback at any time.
- Messages you only for decisions and failures.

Opinionated: one Hermes per host, one dedicated service user per app plus one hub account per host, rootless podman with
Quadlet, Telegram for approvals.

Scope: Talaria manages Hermes installs that run from the official container image. It
does not cover the git-based `hermes update` path.

Talaria is an independent project and is not affiliated with or endorsed by Nous
Research.

## How it fits together

![One host: you (the operator) run setup; the hub account talaria runs the only bot and reaches each app account only through sudo and talaria op; each app account runs its own rootless podman container; Hermes reaches Clawvisor through a published host address.](docs/accounts.svg)

Each app runs in its own account with its own rootless podman, data and backups. The hub
account holds the only bot token and no app data; it can run nothing but `talaria op` as
each app. Podman networks cannot cross accounts, so containers reach each other only
through a published host address (see [Clawvisor](#clawvisor)).

## Requirements

- Linux with systemd user units and linger (tested on Ubuntu 24.04).
- Rootless podman ≥ 4.9 with Quadlet.
- `git`, `python3` ≥ 3.10, GNU `tar`, `gzip`, `sudo`.
- amd64 or arm64 (gbrain: amd64 only; it publishes no arm64 Linux build).
- Hermes ≥ v2026.6.5 (existing installs older than that are refused).
- SELinux in enforcing mode is not supported.

## Setup by hand

```bash
git clone https://github.com/maxpfingsthorn/talaria && cd talaria
git checkout "$(git tag -l 'v*' --sort=-v:refname | head -1)"
bin/talaria setup --plan --user hermes   # what would happen
bin/talaria setup --user hermes          # run again until it prints DONE
```

`--user` names the app's service account; setup creates it if it does not exist. Setup
also creates the hub account `talaria` (once per host; `--hub NAME` picks another name),
which runs the one Telegram bot and the daily check for every app on the host. Both happen
through one command you paste into your own terminal (sudo asks for your password). Clone
over https as shown: setup installs Talaria for both accounts from your checkout's origin,
and they have no SSH key.

`--app hermes|clawvisor` picks which app to manage (default `hermes`; the account
defaults to the app's own name, e.g. `clawvisor`, unless `--user` says otherwise). Once
`talaria.conf` exists, its `app` wins: re-running setup with a different `--app` stops
with an error instead of silently switching apps.

Setup never guesses. Each run does what it can and stops at the next thing only you
can do:

```
MISSING: podman >= 4.9 (found 4.3.1)
  hint: Debian/Ubuntu: apt install podman · Fedora/RHEL: dnf install podman · Arch: pacman -S podman
ACTION REQUIRED: paste this into your terminal (sudo asks for your password), then run setup again with --user hermes: …
ACTION REQUIRED: in a private chat with your bot, send within 15 minutes:
  /pair K7M2QX9P
FOUND: Hermes unit hermes-gateway.service (container hermes-gateway)
STOP: several Hermes installs; choose one with --adopt UNIT
NOTE: check.time in talaria.conf is not used any more; set it in the hub's hub.conf
DONE
```

Exit codes: 0 done · 10 a person must act · 1 stop or error.

The bot token is never typed into a chat or an agent. You store it yourself, once per
host, as the hub: `sudo -u talaria -H ~talaria/.local/bin/talaria set-token` (setup prints
the exact command). A second app on the same host uses the same bot: no new token, no
pairing.

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
  - `~/.config/talaria/` (`talaria.conf`, `hermes.env` with the dashboard password);
  - `~/.local/state/talaria/` (state, backups, history);
  - `~/.config/containers/systemd/hermes.container`.
- Adopting an existing install renames its Quadlet to `*.talaria-orig`, takes a full
  backup first, and keeps the data where it is.
- Setup never moves a Talaria install backwards: if the hub already runs a newer release
  than your checkout, it stops and asks you to check out the newer tag.

## Daily use

| Telegram command | Effect |
|---|---|
| `/status` | every app: version and state, free disk, data size, pending update, interrupted change |
| `/check` | look for new releases now, for every app and for Talaria itself; always ends with a summary, even when nothing is new |
| `/check <app>` | the same for one app |
| `/check talaria` | only the Talaria release: the update offer if there is a newer one, else "Talaria vX is current." |
| `/approve [app] <tag>` | deploy the pending update |
| `/reject [app] <tag>` | never offer this release again |
| `/rollback [app]` | describe what a rollback would restore, and how old the backup is |
| `/rollback [app] CONFIRM` | roll back |
| `/backups [app]` | list backups |
| `/restore [app] <id>` / `/restore [app] <id> CONFIRM` | describe / restore a backup |
| `/update <version>` | show which apps a Talaria update would restart, with an **Update** button |

With one app registered, the app name is optional. With several, a command without one
answers "Which app?" with a button per app (not `/check`, which checks everything); the button runs the read-only or describe
form (status, rollback description), never a confirm.

The bot registers these commands with Telegram, so they appear under the **Menu**
button in your chat (and nowhere else). Update offers come with **Approve** and
**Reject** buttons; `/rollback` and `/restore <id>` answer with a confirm button, so
you never have to type `CONFIRM`. Each button names what it acts on and is checked
against the current state: an old button is refused instead of acting on something
else. Rollback and restore buttons expire after an hour. After a tap, the buttons are replaced by one status button (for example "✅ Approved — deploying vX"); the report above stays.

You get a message when an update is ready, after a deploy, rollback or restore, when
something fails, after an interrupted change, and once per new Talaria release (with what it would restart, per app, and an **Update** button).
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
Hermes's; a Clawvisor or gbrain install (`app = clawvisor`, `app = gbrain`) gets its own defaults
for `data_dir`, `dashboard.port`, `repo`, `image`, `min_release`, `backup.exclude`,
`check.days` and `maintenance.time`.

| Key | Default |
|---|---|
| `data_dir` | `~/hermes-data` |
| `image` | `docker.io/nousresearch/hermes-agent` |
| `repo` (`hermes_repo` also accepted) | `https://github.com/NousResearch/hermes-agent` |
| `talaria_repo` | not used since v0.5 (see the hub's `hub.conf`) |
| `dashboard.public_url` | empty; `https://<host>` (or `http://127.0.0.1`/`http://localhost`), no path. `login-link` prints its link with this address first. Sets Clawvisor's `PUBLIC_URL`; changes nothing Talaria publishes or health-checks |
| `dashboard.bind` | `loopback`; or a space-separated list of 1-3 of `loopback`, `tailscale` and private IPv4 addresses (never `0.0.0.0`, public or other `127.x` addresses). The first is the primary address (health checks); each is published |
| `dashboard.port` | `9119` |
| `backup.keep` | `5` |
| `backup.exclude` | `.cache .npm home/.cache home/.npm backups` (`backups/config` is always kept) |
| `disk.floor_gb` | `6` |
| `check.time` | not used since v0.5 (see the hub's `hub.conf`) |
| `check.days` | every day (gbrain: `mon thu`); space-separated `mon` … `sun`: the days the hub's daily timer looks for this app's releases. `/check` always looks. Empty = every day |
| `maintenance.time` | none (gbrain: `01:30`); `HH:MM` local time: start of a two-hour window in which Talaria runs the app's maintenance once (gbrain: `gbrain dream`). Empty turns it off |
| `host_loopback` | `false` (`true`: the container may reach the host's loopback at `10.0.2.2`; needs `slirp4netns`) |
| `add_hosts` | (none; space-separated `name:ip` pairs, e.g. `clawvisor:10.254.254.1`) |

Run setup again after changing it. An unknown key stops setup (naming the key) but is only
warned about at runtime, so a key added for a newer Talaria does not cut an older install
off; still update first, then add new keys.

### The hub's `hub.conf`

`~/.config/talaria/hub.conf` of the hub account, `key = value`:

| Key | Default |
|---|---|
| `apps` | written by setup: `<app>:<account>` pairs, e.g. `hermes:hermes clawvisor:clawvisor` |
| `check.time` | `04:30` (the daily check of every app and of Talaria itself) |
| `talaria_repo` | this repository |
| `telegram_api` | `https://api.telegram.org` |

Run setup again (for any app) after changing it.

## Clawvisor

Talaria can also manage [Clawvisor](https://github.com/clawvisor/clawvisor) the same
way it manages Hermes: release detection, a rehearsal on a copy, Telegram approval,
backup, deploy, verify, and rollback of image and data together.

Each app has its own service account and its own Talaria install, under one hub. To run both Hermes and Clawvisor on the same
host, set Clawvisor up as its own service user, separate from Hermes's:

```bash
bin/talaria setup --plan --app clawvisor --user clawvisor
bin/talaria setup --app clawvisor --user clawvisor
```

Clawvisor's README warns that an agent sharing an environment with it can read its
database, so it never shares a user, a podman network or a data dir with Hermes.

**Clawvisor uses the same bot as Hermes.** Its setup registers it with the hub; there is
no second bot, token or pairing. In the chat, name the app when you type a command:
`/approve clawvisor v0.9.10`.

Clawvisor's `HOME` is its data dir, so custom adapters
(`~clawvisor/clawvisor-data/.clawvisor/adapters/`) persist and are backed up and rolled back
with the data; the login session file (`.clawvisor/.local-session`) is excluded from backups.

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

Clawvisor in a container always runs in "non-local" mode, so its login cookie is
`Secure`: browsers keep it only over HTTPS or on `http://localhost` / `http://127.0.0.1`.
Open the dashboard through an SSH tunnel at `http://127.0.0.1:<port>` (works on any bind),
or over HTTPS, e.g. `sudo tailscale serve --bg --https=443 http://<bind address>:<port>`
(tailnet-only; `funnel` would make it public), and set
`dashboard.public_url = https://<host>.<tailnet>.ts.net` so `login-link` prints that link
first. Plain `http://` to a Tailscale, LAN or dummy-NIC address logs you out right after
login ("Link expired or already used").

`dashboard.public_url` also becomes Clawvisor's `PUBLIC_URL` (a line in its Quadlet), which
Google OAuth needs: the redirect URI is `<public_url>/api/oauth/callback`, see Clawvisor's
[GOOGLE_OAUTH_SETUP.md](https://github.com/clawvisor/clawvisor/blob/main/docs/GOOGLE_OAUTH_SETUP.md).
`login-link` does not depend on it: it hands Clawvisor a throwaway session file
(`<data dir>/.login-link`, removed afterwards) that points at the container's own address.

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

## gbrain

Talaria can manage [gbrain](https://github.com/garrytan/gbrain), an agent memory "brain"
with an MCP server, the way it manages Hermes and Clawvisor. That covers release
detection, a rehearsal on a copy, Telegram approval, backup, deploy, verify, and
rollback of image and data together. gbrain runs in its own service account under the
same bot:

```bash
bin/talaria setup --plan --app gbrain --user gbrain
bin/talaria setup --app gbrain --user gbrain
```

- **`dashboard.public_url` is required.** It is the https address MCP connectors use,
  port included, for example `dashboard.public_url = https://<host>.<tailnet>.ts.net:8443`.
  It becomes gbrain's `--public-url`, which is its OAuth issuer. Setup stops without it.
- **Image.** gbrain publishes no container image. Talaria downloads the release's
  `gbrain-linux-x64` and checks its SHA-256 against the digest GitHub publishes for that
  asset. When `gh` is installed and logged in for the service account, Talaria also checks
  the build provenance (`gh attestation verify`). Otherwise setup prints a `NOTE` and only
  the digest is checked. Talaria then builds a local image from a pinned distroless base.
- **Storage.** gbrain keeps its data in PGLite (embedded Postgres) in `~gbrain/gbrain-data`,
  mode 0700, because some PGLite files inside it are world-readable. Only one process can
  open the brain at a time. Rehearsal copies, backups and maintenance therefore stop the
  server for a moment. The rehearsal never starts the new server: it runs
  `gbrain doctor --json` (schema version) and recalls a marker page that setup wrote, on
  the copy, offline and without your keys.
- **Admin token.** Setup generates `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` in
  `~gbrain/.config/talaria/gbrain.env` (mode 0600), and you log in to `/admin` with it.
  Treat it like a password: never paste it into a chat or an agent's context.
- **Provider keys** (embeddings, synthesis, the dream cycle) are yours to add, in your own
  terminal as the service user: put them in the app env file
  `~<user>/.config/talaria/gbrain.env`, for example `OPENROUTER_API_KEY=…` (or
  `OPENAI_API_KEY=…`, `ANTHROPIC_API_KEY=…`, any provider gbrain supports), then run
  `systemctl --user restart gbrain.service`. gbrain picks up a changed key only after a
  restart. Talaria never asks for provider keys and never prints them.
- **Model tiers** (utility, reasoning, deep, subagent) are gbrain's own configuration
  (`gbrain config`), not Talaria's. gbrain's defaults call Anthropic directly, so with
  another provider set the tiers to that provider's model ids. To choose embedding models
  for an existing brain, stop gbrain first, because the command needs the brain to itself.
  As the service user:

  ```bash
  systemctl --user stop gbrain.service
  podman run --rm --read-only --userns=keep-id:uid=65532,gid=65532 \
    -v ~/gbrain-data:/data:Z -e HOME=/data -e GBRAIN_HOME=/data \
    --env-file ~/.config/talaria/gbrain.env localhost/gbrain:current \
    embeddings enable --embedding-model <provider:model>
  systemctl --user start gbrain.service
  ```
- **Update cadence.** gbrain releases several times a day. By default
  (`check.days = mon thu`) the daily timer looks for a gbrain release twice a week.
  `/check gbrain` looks at any time.
- **Nightly maintenance.** Each night, from `maintenance.time` (default `01:30`, local
  time), Talaria stops gbrain, runs `gbrain dream` once in a one-off container, and starts
  gbrain again. The dream cycle has network access and your provider keys; without keys it
  runs only its file-based phases. Expect minutes of downtime. The run is skipped while
  another Talaria operation runs or a change is interrupted. Success is silent. A failure
  sends one message, and the next night retries. The hub's `talaria-maintain.timer` checks
  every 15 minutes whether a window is due. `maintenance.time =` (empty) turns
  maintenance off.
- Out of scope: Postgres, gbrain's autopilot daemon, adopting an existing gbrain install,
  and gbrain's own `self-upgrade` (Talaria turns its update checks off).

### Who reaches what

| Path | Who | Where |
|---|---|---|
| `/mcp`, `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource` (and `…/mcp`), `/authorize`, `/token`, `/register`, `/revoke` | cloud MCP connectors (claude.ai, ChatGPT) | public, port 8443 |
| everything, including `/admin` and `/metrics` | you, and local agents on the host or tailnet | tailnet only, port 10000 (or the published address) |

Cloud connectors use OAuth: they register themselves (dynamic client registration) and
you approve each one in `/admin`. **With registration open, anyone who reaches
`/register` can create a client.** It stays *pending*, and nothing is granted until you
approve it. Approve only clients you just added yourself. Local agents (Hermes, Claude
Code, Codex) get scoped tokens that you mint in `/admin`, and use the tailnet port or the
published address.

A connector's consent link points at the public port (`https://<host>.<tailnet>.ts.net:8443/admin/?oauth_request=…`),
which does not serve `/admin`. Open the same link with port `10000` instead of `8443` to
approve it over your tailnet.

### Public MCP endpoints with Tailscale Serve and Funnel

Talaria does not configure Tailscale. For any app with a public MCP endpoint, publish the
app on loopback (`dashboard.bind = loopback`, the default) or on its Tailscale address.
Then, as root, once (`<port>` is the app's `dashboard.port`, 3131 for gbrain):

```bash
# public: exactly the connector paths, on 8443
for p in /mcp /.well-known/oauth-authorization-server /.well-known/oauth-protected-resource \
         /authorize /token /register /revoke; do
  tailscale funnel --bg --https=8443 --set-path "$p" "http://127.0.0.1:<port>$p"
done
# tailnet only: the whole server, including its admin UI, on 10000
tailscale serve --bg --https=10000 http://127.0.0.1:<port>
tailscale funnel status
```

Funnel must be allowed for the node in your tailnet policy, and it only works on ports
443, 8443 and 10000. **Funnel applies per port: never funnel a port that also serves a
private app.** `tailscale funnel status` should list only the paths above as public.
Set `dashboard.public_url = https://<host>.<tailnet>.ts.net:8443` and run setup again.

## Security

| Party | Trust |
|---|---|
| The paired Telegram account | trusted |
| Your login account and coding agents running as it | fully trusted, through the sudo rule |
| The Hermes agent, its data dir and everything in it | untrusted |
| Upstream images and their output | untrusted beyond digest and revision checks |
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

## Removing the sudo rules

After setup you can remove your own rules: `sudo rm /etc/sudoers.d/talaria-<user>
/etc/sudoers.d/talaria-talaria`. Talaria keeps working; only `talaria setup` from your
login account needs them again. Keep `/etc/sudoers.d/talaria-talaria-<user>`: the bot needs
it to reach the app.

## Updating Talaria

The hub checks for new Talaria releases daily and sends which apps an update would
restart, with an **Update Talaria to vX.Y.Z** button; `/update vX.Y.Z` asks for the same.
The update runs hub first, then each app, then restarts the bot, and reports per app. From
a shell, as the hub:

```bash
sudo -u talaria -H ~talaria/.local/bin/talaria self-update vX.Y.Z
```

The bot refuses a version that is not newer than the one installed (no downgrades), and
the apps refuse releases older than v0.5.0 (the first with a hub interface). If an update fails on an account, that
account's previous version is restored; an app whose update failed keeps its version, and
its commands answer "Talaria versions differ on this host; run /update" until an update
succeeds.

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

## Architecture

Talaria is a small app-neutral core (state, backup, deploy, rollback, restore,
rehearsal, setup, notify) plus a thin per-app adapter under `talaria/apps/` that
supplies everything that differs between the apps Talaria can manage: release
discovery and image fetch, health and data-version checks, quadlet template
variables, secret setup and the texts shown to you. `ctx.app` is the adapter in
use; `talaria.apps.get(name)` looks one up by the `app` key in `talaria.conf`.
`talaria/apps/hermes.py`, `talaria/apps/clawvisor.py` and `talaria/apps/gbrain.py` are the adapters today. `talaria/maintain.py` (app side) and `talaria/hubmaintain.py` (hub timer) run maintenance windows.

One hub account per host runs the Telegram bot and the daily timer (`talaria/telegram.py`,
`talaria/hubcheck.py`, `talaria/hubupdate.py`); `hub.conf` makes an account the hub
(`talaria/hubconf.py`). The hub reaches each app only through `talaria op`
(`talaria/op.py`), run as the app's account through one sudo rule (`talaria/hubexec.py`).
`op` writes JSON lines, which `talaria/relay.py` turns into Telegram messages, prefixing
button data with the app's name.

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
