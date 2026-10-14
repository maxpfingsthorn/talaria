# Hermes

Hermes is the default app (`--app hermes`, account `hermes`). Talaria runs the official
image (`docker.io/nousresearch/hermes-agent`) as `hermes gateway run`, with the dashboard
inside the same container, and refuses releases older than v2026.6.5. An existing Hermes
install is adopted: setup renames its Quadlet to `*.talaria-orig`, takes a full backup
first and keeps the data where it is (`--adopt UNIT` picks one when there are several).

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
