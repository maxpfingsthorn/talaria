# Configuration

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

Comments must be on their own line (`# …`); a trailing `# …` becomes part of the value.
Booleans are `true` or `false`. Setup also rejects a key given twice, a port outside
1-65535, a relative `data_dir` and a `tailscale_ip` outside `100.64.0.0/10`.

Run setup again after changing it. An unknown key stops setup (naming the key) but is only
warned about at runtime, so a key added for a newer Talaria does not cut an older install
off; still update first, then add new keys.

## The hub's `hub.conf`

`~/.config/talaria/hub.conf` of the hub account, `key = value`:

| Key | Default |
|---|---|
| `apps` | written by setup: `<app>:<account>` pairs, e.g. `hermes:hermes clawvisor:clawvisor` |
| `check.time` | `04:30` (the daily check of every app and of Talaria itself) |
| `talaria_repo` | this repository; notices read its latest published stable Release. Custom HTTP repositories can serve the same metadata at `<repo>/api/releases/latest`; a local git path still works for explicit version updates but has no automatic release discovery |
| `telegram_api` | `https://api.telegram.org` |

Run setup again (for any app) after changing it.
