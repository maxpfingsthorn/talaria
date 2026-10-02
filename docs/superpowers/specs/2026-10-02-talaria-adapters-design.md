# Talaria — app adapters and Clawvisor (design)

Date: 2026-10-02
Status: approved (decisions by the owner on 2026-10-01; spike done on 2026-10-02)

## 1. Goal

Talaria keeps updating Hermes exactly as today, and can also manage
[Clawvisor](https://github.com/clawvisor/clawvisor) on the same host: release
detection, a rehearsal on a copy, Telegram approval, backup, deploy, verify, and
rollback of image and data together.

## 2. Decisions

1. **One codebase, a core and exactly two adapters** (`hermes`, `clawvisor`). No plugin
   system, no third adapter, no adapter API promises.
2. **One app per Talaria install.** An install is one service user with its own state,
   bot, timer and Quadlet. The app is chosen by `app = hermes|clawvisor` in
   `talaria.conf`; a missing key means `hermes`, so existing installs keep working.
3. **Clawvisor runs as its own service user** (`clawvisor`). Clawvisor's README warns
   that an agent sharing an environment with it can read its database. Rootless podman
   gives each user its own network, so no podman network can span both users.
4. **A second Telegram bot** for the Clawvisor install. One bot for all apps is a later
   wish, not part of this design.
5. **Network:** Clawvisor publishes its port on the Tailscale IP only. Hermes reaches it
   through the host; its Quadlet gets `AddHost=clawvisor:<tailscale ip>` from a new
   generic setting `add_hosts`, so Hermes can use `http://clawvisor:25297`.
6. **SQLite**, not Postgres. Clawvisor opens it in WAL mode with one connection. Its log
   tables (`audit_log`, `gateway_request_log`, `runtime_events`) are never pruned
   upstream, so `/status` reports the data size.
7. **Releases only.** Clawvisor publishes no container image. Talaria downloads the
   release binary and `checksums.txt`, checks the SHA-256, and builds a local image
   `FROM` a distroless base pinned by digest. No `main` builds.
8. **Google OAuth (needs an https redirect) is out of scope.**

## 3. Spike findings (Clawvisor v0.9.9 → v0.9.10, rootless podman)

- The release binary runs in `gcr.io/distroless/static-debian12` as uid 65532
  (`--userns=keep-id:uid=65532,gid=65532`); ready within about 2 s.
- Required environment: `DATABASE_DRIVER=sqlite` (else it expects Postgres when
  `SERVER_HOST=0.0.0.0`), `JWT_SECRET`, a `vault.key` file, `SQLITE_PATH`,
  `CONFIG_FILE`, `VAULT_KEY_FILE`, the relay key file paths and `CLAWVISOR_DAEMON_DATA_DIR`
  (as in upstream `deploy/docker-compose.local.yml`), `CLAWVISOR_CONTAINER=1`,
  `MAX_USERS=1`.
- `clawvisor-server healthcheck` checks `/ready`; it works through `podman exec`.
  `/ready` answers `{"db":"ok","status":"ok","vault":"ok"}`.
- In a container Clawvisor is in "non-local" mode and prints no login link.
  `clawvisor-server dashboard --no-open` (via `podman exec`) prints a one-time link.
- Migrations are embedded, run at startup, and are recorded in
  `schema_migrations(name, applied_at)`. A rehearsal with `--network=none` on a copy
  works and shows exactly the new migration names.
- **An old version starts on a newer schema and reports healthy.** A health check
  cannot catch that; rollback must restore data and image together (Talaria already
  does).
- Database files are created mode 0644; the data dir must be 0700.
- Clawvisor has an auto-updater (`auto_update.enabled`, default off). Talaria sets
  `CLAWVISOR_AUTO_UPDATE_ENABLED=false` and runs the container `ReadOnly=true`.
- Cross-user reachability works with the default rootless network and with pasta.

## 4. The adapter seam

Everything that differs between the apps moves behind an `App` object; everything
else (state, backups, marker, rollback, restore, retention, bot, history, self-update,
lock) stays in the core and does not learn app names.

| Concern | Hermes | Clawvisor |
|---|---|---|
| Release tags | `vYYYY.M.D[.N]` from git | `vX.Y.Z` from git (no pre-releases) |
| Published? | tag also in the registry | release assets exist (checked when fetching) |
| Image | pull, check revision label | download, check SHA-256, `podman build` |
| Rehearsal | `migrate.py`, `dbopen.py`, doctor, config diff | start offline on the copy, wait ready, list new migrations |
| Deploy step before start | migrate in place, compare config version | none (migrates at start) |
| Health | dashboard `/api/status` requires login | `/ready` is ok |
| Check after deploy | none extra | live `schema_migrations` count equals the rehearsal's |
| Data version in backups | `_config_version` | newest migration name |
| Secrets | dashboard password in `hermes.env` | `JWT_SECRET` in `clawvisor.env`, `vault.key` in the data dir |
| Adopt an existing install | yes | no (fresh install only) |

## 5. Releases

- v0.3.0: the refactor. No behaviour change for Hermes: the rendered Quadlet, unit
  files, messages and commands are byte-for-byte the same.
- v0.4.0: the Clawvisor adapter.
- Then: rollout on this host (owner present for root, bot token, first login, Hermes
  restart).

## 6. Out of scope

One bot for several installs; Postgres; Google OAuth; adopting an existing Clawvisor;
building from source; signature checks beyond the release's own checksums.
