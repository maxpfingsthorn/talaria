# Clawvisor

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
coding agent): `talaria login-link`, or send `/login clawvisor` to the bot: it sends the link in your
private chat with previews off and replaces it with an "expired" notice after 5 minutes.
Treat the link like a password — **never paste it into an agent's context, a ticket, or
anywhere it could be logged; the bot's private chat is the exception, because the link
is single-use and short-lived.** If an agent is driving setup, have it stop here and let the person at
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
against the checksums the same release publishes (an integrity check, not an
independent signature), and builds a local image from a pinned distroless
base. Its log tables (`audit_log`, `gateway_request_log`, `runtime_events`) are
never pruned upstream and grow without bound — `/status` reports the data
directory's size so you notice before disk space runs out; Talaria itself does
not prune them.

Google OAuth login (it needs an https redirect) is out of scope; use the one-time
login link instead. Adopting an existing Clawvisor install is also out of scope —
only a fresh install is supported.
