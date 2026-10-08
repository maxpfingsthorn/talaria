# Talaria — gbrain as a third app (design)

Date: 2026-10-09
Status: design approved in conversation by the owner (2026-10-09); spec under review
Amends: `2026-10-02-talaria-adapters-design.md` §2.1 ("exactly two adapters") — this adds a
third adapter, still without a plugin system.

## 1. Goal

Talaria manages [gbrain](https://github.com/garrytan/gbrain) (MIT; an agent memory
"brain" with an MCP server) like Hermes and Clawvisor: release detection, rehearsal on a
copy, approval in the hub bot, backup, deploy, verify, rollback of image and data
together. In addition gbrain gets a nightly maintenance window for its "dream cycle".
The brain is reachable by cloud agents (claude.ai, chatgpt.com custom connectors) through
a public HTTPS endpoint, and by local agents (e.g. Hermes, Claude Code, Codex) on the
host or tailnet.

## 2. Decisions (owner, 2026-10-09)

1. **Talaria adapter**, own service user (default `gbrain`), container built locally from
   the release binary — same model as Clawvisor.
2. **Full feature set**: embeddings, synthesis and the dream cycle. Provider keys come from
   the person (env file), never from Talaria. Talaria is provider-neutral; any provider
   gbrain supports works (e.g. an OpenAI-compatible router such as OpenRouter).
3. **Storage: PGLite** in the data dir (single writer). No Postgres sidecar.
4. **Dream cycle in a nightly window**: stop the server, run `gbrain dream`, start the
   server. Downtime of minutes is accepted.
5. **Update cadence twice a week** for gbrain (it releases several times a day); other
   apps keep checking daily. Cadence becomes a per-app setting.
6. **Public exposure only for what connectors need** (see §6). The admin UI stays private.
7. **The repository stays generic**: no host names, addresses or accounts of any one
   installation in code, templates, tests or docs. Examples use placeholders
   (`<host>.<tailnet>.ts.net`, `<tailscale ip>`).

## 3. Spike findings (gbrain v0.60.106 → v0.60.116, rootless podman 4.9.3)

- `gbrain-linux-x64` runs unchanged in `gcr.io/distroless/cc-debian12` as uid 65532
  (`UserNS=keep-id:uid=65532,gid=65532`), `ReadOnly=true`, with `HOME=/data` and
  `GBRAIN_HOME=/data`. Needs glibc only; no writable `/tmp`.
- `gbrain init --pglite` is keyless (~5 s, ~46 MB). Data under `/data/.gbrain/`.
  `config.json` is 0600 but PGLite files are 0644 → the data dir must stay 0700.
- Every CLI call checks for upgrades and prints a notice; the adapter disables
  self-upgrade checks (config/env), as Clawvisor's auto-updater is disabled.
- `gbrain serve --http` flags used: `--bind 0.0.0.0` (required in the container),
  `--port`, `--public-url`, `--enable-dcr`, `--fail-fast`, `--surface full`.
  `--public-url` with a non-443 port is reflected verbatim in the OAuth discovery
  documents.
- The admin bootstrap token is not persisted on a non-TTY start; Talaria generates it
  and passes `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` through the app env file (0600).
- `GET /health` → 200 `{"status":"ok","version":…,"engine":"pglite"}`; 503 if the brain
  cannot open (with `--fail-fast`). Ready in ~2 s. Distroless has no shell, so health is
  probed from the host over the published port.
- Migrations apply automatically on first open. `gbrain doctor --json` reports
  `schema_version` (219 → 221 across the two releases). An older binary on a newer schema
  warns ("AHEAD of client") but runs → rollback must restore data with the image (Talaria
  already does).
- Background work does not run inside the server. `gbrain dream` is one maintenance
  cycle ("designed for cron"); with the server running it cannot open the DB and skips
  DB phases → needs the server stopped.
- While the server holds the DB, most CLI commands fail (`pglite_busy`); maintenance and
  backups run with the server stopped (Talaria's model).
- Integrity: the GitHub release API gives a `sha256:` digest per asset that matches the
  file; `gh attestation verify <file> -R garrytan/gbrain` passes (SLSA provenance from the
  release workflow). There is no checksums asset.
- Resources: ~435 MB RSS idle, ~2 % CPU idle.

## 4. The adapter (`talaria/apps/gbrain.py`)

| Concern | gbrain |
|---|---|
| Release tags | `vX.Y.Z.W` from git (no pre-releases) |
| Published? | release asset `gbrain-linux-x64` (arm64: not published → Permanent "unsupported architecture") |
| Image | download asset; verify the API `digest` (sha256) **and** `gh attestation verify` when `gh` is available (else digest only, with a NOTE at setup); `podman build` `FROM` distroless/cc pinned by digest, `--timestamp 0` |
| Rehearsal | offline (`--network=none`) on a copy: `gbrain doctor --json` before (old image) and after (new image) → schema version change; one `recall` of a marker page written at setup |
| Deploy step before start | none (migrates at start) |
| Health | `GET /health` → 200 and `status == ok` |
| Check after deploy | `schema_version` live equals the rehearsal's |
| Data version in backups | `schema_version` |
| Secrets | `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` (generated, 0600 env file); provider keys added by the person to the same env file |
| Adopt | no (fresh install only) |
| First run | `gbrain init --pglite` in a one-off container before the first start; self-upgrade checks off |

Quadlet: like Clawvisor's (keep-id 65532, `ReadOnly=true`, data volume at `/data`,
`HOME=/data`, `GBRAIN_HOME=/data`, `EnvironmentFile=` the app env), `Exec=serve --http
--bind 0.0.0.0 --port <container port> --public-url <dashboard.public_url> --enable-dcr
--fail-fast`, published on the app's `dashboard.bind` addresses. `dashboard.public_url`
is required for gbrain (OAuth issuer).

## 5. Generic Talaria changes

### 5.1 Per-app check cadence
New `talaria.conf` key `check.days` (e.g. `mon thu`; default: every day). The hub's
daily timer still runs daily; for an app whose `check.days` excludes today, the hub skips
that app's release check (`/check <app>` and bare `/check` still check on demand). The
adapter may supply a default (gbrain: `mon thu`).

### 5.2 Maintenance window
New optional adapter hook `maintenance(ctx)` and `talaria.conf` key
`maintenance.time` (gbrain default `03:30`; empty disables). The hub's timer runs
`op maintain` for apps with a hook at their time (a second hub timer, or the existing
timer firing more often — the plan decides). `op maintain` runs under the app's op lock
(never concurrent with deploy/rollback/backup): stop the service, run the hook (gbrain:
`gbrain dream` in a one-off container from the current image, with network, the app env
file and the data volume), start the service, health check. Silent on success; on
failure (hook non-zero or service unhealthy) it sends a message with the last error line.
If the service does not come back, the normal "not running" status/recovery applies.
Skipped while a release offer is pending deployment or the `changing` marker exists.

### 5.3 Funnel-friendly docs
Talaria does not configure Tailscale. The README documents, for any app with a public
MCP endpoint: publish the app on the Tailscale IP (or loopback), then (as root):
- a **public** port (e.g. 8443) via `tailscale serve --https=8443 --set-path <p> …` for
  exactly the connector paths, plus `tailscale funnel --bg 8443`;
- a **tailnet-only** port (e.g. 10000) for the full server including the admin UI.
Funnel applies per port: never funnel a port that also serves a private app.

## 6. Exposure (gbrain)

Public (funnel) paths: `/mcp`, `/.well-known/oauth-authorization-server`,
`/.well-known/oauth-protected-resource` (and `/mcp` suffix), `/authorize`, `/token`,
`/register`, `/revoke`. Private (tailnet): everything, including `/admin`, `/metrics`.
DCR lets anyone who reaches `/register` create a *pending* client; nothing is granted
until the owner approves it in `/admin`. The README says so.

Clients: cloud connectors use native OAuth (DCR + owner consent). Local agents use
scoped tokens/clients minted by the owner (`/admin` or `gbrain agent register`) and the
same URL or the tailnet port.

## 7. Errors

- Asset digest mismatch or attestation failure → Permanent ("failed verification").
- `gh` missing → digest-only verification, NOTE at setup.
- Rehearsal: doctor not ok, recall of the marker fails, or server not healthy within the
  deadline → Permanent with details.
- Maintenance failure → one message; next night retries.
- Missing `dashboard.public_url` for gbrain → setup STOP.

## 8. Testing

Unit: adapter (releases, fetch/verify incl. digest mismatch and attestation failure,
Quadlet render, rehearsal, health, data version), `check.days` gating (timer vs. on
demand), maintenance (lock, skip conditions, stop/hook/start order, failure message,
service restored on hook failure). Goldens: Hermes and Clawvisor Quadlets unchanged.
CI e2e: a fake gbrain release (a small static binary serving `/health`, `doctor --json`,
`recall`, `dream`) like the existing Clawvisor fake; one real-binary contract test if
practical. Mutation run before release.

## 9. Release and rollout

v0.6.0. Rollout on a host: root paste (user, linger, sudo rules), setup with
`--app gbrain`, provider keys into the env file by the person, `dashboard.public_url`,
Tailscale serve/funnel by the person (root), owner login to `/admin`, connectors added in
claude.ai / ChatGPT and approved, local agents given scoped tokens.

## 10. Out of scope

Postgres; autopilot daemon; adopting an existing gbrain install; running gbrain outside a
container; Talaria managing Tailscale.
