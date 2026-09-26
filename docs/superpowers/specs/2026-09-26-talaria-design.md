# Talaria — Design

Date: 2026-09-26
Status: draft, pending review
Supersedes: a host-specific design for one deployment, kept privately; its test
findings are carried over here in generic form.

> **Talaria** — safe, approved updates for self-hosted
> [Hermes Agent](https://github.com/NousResearch/hermes-agent) on rootless podman.
> Opinionated.

## 1. Purpose

Hermes Agent ships frequent releases. Updating a self-hosted instance is
`pull && restart`, and the container migrates its config on boot. What goes
wrong is rarely that the new container fails to start. It is that it starts,
**migrates the data**, and then misbehaves — leaving data the previous version
cannot safely read, with nothing in the running system that notices.

Talaria makes updates boring:

- It finds new releases, pulls them pinned by digest, and **rehearses the
  migration on a throwaway copy** of the data before production is touched.
- It reports what the update changes and **waits for a human to approve**.
- It deploys, verifies, and on failure **rolls back image and data together**.
- Approval, rollback and restore run from a **dedicated Telegram bot** that the
  agent itself cannot reach.
- Setup is one idempotent command a **coding agent** can drive end to end; the
  person only does what needs a person (create a bot, paste a root block).

### 1.1 Goals

- Adopt an existing Hermes-on-podman install, or install Hermes fresh.
- Track upstream release tags. Deploy only on explicit approval.
- Never leave an old binary running on a newer config, or the reverse.
- Low noise: messages only when something needs a decision or has failed.
- Everything host-specific is configuration; nothing personal in code or docs.

### 1.2 Non-goals

- Multiple hosts, multiple Hermes instances per host, orchestration.
- Building Hermes images (v1 pulls official images; §6.4).
- Configuring Hermes itself (providers, models, personality).
- Tracking `main`. Release tags only.
- Automatic deploys, automatic self-updates.
- Other approval channels in v1. The connector is named `telegram.py` so others
  can be added beside it later; no connector framework is built now.

## 2. Assumptions and requirements

- Linux with systemd (user instance, linger) and **rootless podman ≥ 4.9**.
- `git`, `curl`, `python3` ≥ 3.10 (stdlib only), `gzip`, `tar`, `shred`, `flock`.
- One Hermes per host, run by a **dedicated service user** (default `hermes`).
- amd64 or arm64 (the architectures upstream publishes).
- Runtime code: bash + Python stdlib. **No runtime dependencies beyond the
  above.** Development tools (bats, shellcheck, mutmut) come from a `pixi.toml`
  in the repo and are never needed on a deployed host.

## 3. Facts about upstream this design relies on

Verified against Hermes `v2026.8.3` (package `0.20.0`) and later tags on
2026-09-26.

| Fact | Consequence |
|---|---|
| Official images `docker.io/nousresearch/hermes-agent:<git-tag>`, one per release tag, amd64+arm64, with BuildKit attestations | pull, don't build (§6) |
| Image label `org.opencontainers.image.revision` = commit of the git tag | verify every pull against git |
| `podman search --list-tags` and `podman manifest inspect` work unauthenticated on podman 4.9 | tag list and digests without curl or extra tools |
| On boot, `docker/stage2-hook.sh` runs `scripts/docker_config_migrate.py` and **swallows its failure** (`\|\| echo "...continuing"`) | a failed migration still yields a running container |
| `docker_config_migrate.py` self-restores `config.yaml`/`.env` on error or if the version does not advance | a failed boot migration = **new binary on old config** |
| `HERMES_SKIP_CONFIG_MIGRATION=1` makes the script exit 0 immediately | Talaria disables boot migrations and runs them itself (§7.3) |
| The boot migration runs as the in-container user `hermes` (`s6-setuidgid hermes`) | Talaria's own migration run must use the same user |
| Migrations are a forward-only ladder; `check_config_version()` has no `current > latest` guard; `doctor` reports `✓` for a config newer than the binary | **an old binary on a newer config reports healthy** — rollback must restore data too |
| Every migration writes `config.yaml.bak-<ts>` and a full plaintext `.env.bak-<ts>` into the data root | secret copies accumulate where the agent can read them (§9) |
| Migration rewrites the whole YAML file (one real change produced 124 changed lines) | diff configs **semantically**, never textually |
| `HERMES_DASHBOARD_INSECURE` is ignored since v2026.7.1; a non-loopback dashboard with no auth provider **fails closed** (does not start) | every install needs a dashboard login (§10) |
| Bundled basic-auth provider: `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` + `_PASSWORD` | zero-infrastructure login |
| `hermes doctor` exits 0 even when it reports problems | parse its `✓/⚠/✗` lines; never trust its exit code |
| Telegram `getUpdates` without offset can return nothing while an update is pending; `offset=-1` returns it | the bot resyncs with `offset=-1` on start (§8.2) |

Each fact is re-checked by a test against a real image before a Talaria release
(§14.4), because upstream can change any of them.

## 4. Architecture

```
                    ┌────────────────────────────┐
  talaria-updater   │ timer (daily)              │── talaria history; talaria check
                    └──────────────┬─────────────┘
                                   ▼
                    ┌────────────────────────────┐
                    │  bin/talaria  (bash)       │  the only thing that changes anything
                    └──────────────▲─────────────┘
                                   │ fixed argv, never a shell
                    ┌──────────────┴─────────────┐
  talaria-telegram  │ telegram.py (stdlib)       │  long-poll; one pinned user
                    └────────────────────────────┘
        ┌───────────────────────────────────────────────┐
        │ hermes.container (Quadlet)                    │  never sees Talaria's files
        │ mounts only <data_dir> → /opt/data            │
        └───────────────────────────────────────────────┘
```

### 4.1 Repository layout

```
README.md            second paragraph points coding agents to AGENTS.md
AGENTS.md            the agent runbook (§5.3)
LICENSE              MIT
bin/talaria          entry point, argument parsing, dispatch
lib/*.sh             one module per concern: conf, podman, images, backup,
                     rehearse, deploy, history, setup, notify
lib/*.py             confdiff.py (semantic YAML diff), doctordiff.py,
                     migrations.py (per-tag migration extractor)
telegram.py          the Telegram connector
templates/           hermes.container, talaria-updater.{service,timer},
                     talaria-telegram.service, hermes.env
tests/               bats (bash), unittest (python), fixtures, mutation tooling
docs/                threat-model.md, buildkit.md (§6.4), mutation-report.md
pixi.toml            dev tools only
```

Each `lib/` module has one purpose and a small function interface, so it can be
tested with its collaborators stubbed. Python helpers are invoked as
`python3 lib/x.py <args>` with JSON on stdout; bash never parses YAML itself.

YAML parsing: host python has no `pyyaml` guaranteed. `confdiff.py` and the
config-version reads therefore run **inside a Hermes image** (which has
`pyyaml` in its venv), with the helper bind-mounted read-only and
`--network=none`. This also keeps rule §12.4 (upstream code never runs on the
host) simple.

### 4.2 On-host layout (as the service user)

| Path | Content |
|---|---|
| `~/.local/share/talaria/` | clone of this repo at a release tag |
| `~/.local/bin/talaria` | symlink to `bin/talaria` |
| `~/.config/talaria/talaria.conf` | settings (§11) |
| `~/.config/talaria/.env` | Talaria secrets, mode 600 |
| `~/.config/talaria/hermes.env` | dashboard credentials only, mode 600, passed to the container |
| `~/.config/containers/systemd/hermes.container` | Quadlet |
| `~/.config/systemd/user/talaria-*.{service,timer}` | Talaria units |
| `~/.local/state/talaria/` | `backups/`, `history/` (git), `staging/`, `logs/`, `state.json`, `lock` |
| `<data_dir>` (default `~/hermes-data`) | Hermes data, mounted at `/opt/data` |

`state.json`: deployed tag, digest, revision, config version; previous
deployment; pending candidate and its rehearsal result; rejected tags; last
Talaria release reported.

## 5. Setup

### 5.1 `talaria setup`

Non-interactive and idempotent: it walks a fixed list of steps, and for each
one checks whether it is already done, does it if it can, or stops with an
action line. Re-running continues where it stopped.

```
talaria setup [--plan] [--user NAME] [--adopt UNIT|CONTAINER]
              [--import-data DIR] [--apply-adopt]
```

`--plan` performs the checks and prints the remaining steps; it changes nothing.

Output lines an agent (or person) acts on:

```
OK: <step>
MISSING: <tool> [>= version]
  hint: Debian/Ubuntu: <pkg> · Fedora/RHEL/Rocky/Alma: <pkg> · Arch: <pkg>
ACTION REQUIRED: <what the person must do, exactly>
FOUND: <description of an existing Hermes install>
STOP: <why setup cannot continue>
DONE
```

Exit codes: 0 done, 10 action required, 11 missing prerequisite, 12 ambiguous
situation, 1 error.

Steps:

1. **Prerequisites.** Report each missing tool with package hints for the
   three most common server distributions. Setup never installs packages.
2. **Root block.** If the service user, its subuid/subgid ranges, linger or the
   operator sudo rule (`<operator> ALL=(<user>) NOPASSWD: ALL`) is missing, print
   **one** block of commands to run as root, as an `ACTION REQUIRED`. If a user
   of that name exists but has no Hermes/Talaria footprint, `STOP`: setup never
   takes over an unrelated account. Everything after this runs as the service
   user.
3. **Detect.** Find Hermes installs: podman containers and Quadlets of the
   service user whose image is `hermes-agent`, Docker/Compose containers,
   non-container installs (`~/.hermes` of any user it can read).
   - none → **fresh**;
   - exactly one rootless podman install under the service user → **adopt**;
   - anything else → `FOUND:` lines for each (name, image, data path, ports,
     manager), then `STOP` with the two ways forward:
     `--adopt <unit|container>` or `--import-data <dir>` (§5.2).
4. **Adopt only:** full backup (container stopped, §9.1), read the
   old unit's image, mounts, ports and environment, render the Talaria template
   with them, and **print the diff**. Nothing is switched until the person runs
   `setup --apply-adopt`, because that restarts their live agent.
5. **Image.** Fresh: newest release (§7.1), pinned and verified. Adopt: keep the
   running image; if it is not an official image, record it as
   `local:<revision>` — it stays until the first Talaria update.
6. **Dashboard login.** Generate a password into `hermes.env`; report where it
   is, never the password itself.
7. **Telegram.** `ACTION REQUIRED` for creating the bot via @BotFather and
   putting its token into `.env`. Then ask the person to send `/start`, read
   the sender's numeric ID with `offset=-1`, and `ACTION REQUIRED` to confirm
   that ID (prints ID and username).
8. **Units.** Render and install units, `daemon-reload`, start, run the
   post-start checks (§7.4), print a summary and `DONE`.

### 5.2 Existing installs Talaria cannot adopt

`--import-data <dir>` covers Docker Compose, rootful Docker, and non-container
installs: a fresh Talaria install whose data dir starts as a copy of `<dir>`.
The source is never modified and remains the fallback.

For `--adopt` and `--import-data` alike, **the old instance must be stopped by
the person** before Talaria starts Hermes: two gateways with the same Telegram
token consume each other's messages. Setup checks that nothing else is polling
that token (a `getUpdates` probe returning `409 Conflict`) and stops if
something is.

### 5.3 `AGENTS.md`

Short, imperative, written for coding agents:

1. Read this file fully before running anything.
2. Run `talaria setup --plan` (from a checkout, as the operator) and explain the
   plan to the user in plain words.
3. **Confirm the service user name with the user before handing over the
   root block.**
4. For `MISSING` lines: work out the install command for this distribution and
   ask before installing.
5. For `ACTION REQUIRED` lines: relay them to the user in plain words; never
   ask the user to paste secrets into the chat — they go into the files named.
6. Re-run `talaria setup` until `DONE`.
7. For `STOP` with `FOUND` lines: explain what was found and let the user choose
   `--adopt` or `--import-data`.
8. Before `--apply-adopt`: show the diff, say that it restarts the agent, and
   get an explicit yes.
9. Never edit Talaria's state, backups or history by hand.

## 6. Images

### 6.1 Source

`image` (default `docker.io/nousresearch/hermes-agent`) at release tags. Never
`latest`, `main`, `stable` or `*-desktop`.

### 6.2 Pinning and verification

For a tag: `podman pull <image>:<tag>`, then record the **digest** and from then
on refer to the image only by digest. Assert the image's
`org.opencontainers.image.revision` equals `git rev-list -n1 <tag>` in
Talaria's clone of the Hermes repo; on mismatch the candidate is refused.

Local names: `localhost/hermes-agent:<tag>` for each pulled release, plus the
pointers `:current` (what the Quadlet runs) and `:previous`.

Retention: `:current`, `:previous`, and a pending candidate. Everything else is
removed after a successful deploy.

### 6.3 Git clone

Talaria keeps a clone of the Hermes repo at `~/.local/state/talaria/hermes.git`
(bare, tags only fetched). It is used for tag listing, revision checks, and the
update report. It is never built or executed.

### 6.4 Building (not in v1)

Building from source was proven to work on podman 4.9 with BuildKit inside the
service user's rootless podman (4 added capabilities, pinned builder image).
`docs/buildkit.md` records the recipe so `image.source = build` can be added
later without redesign.

## 7. Update flow

### 7.1 `talaria check` (daily)

1. Fetch git tags; list image tags with `podman search --list-tags`.
2. Candidates: release tags present in both, newer than deployed, not rejected.
   Pick the newest. Tag order is by semver-like comparison of `vYYYY.M.D[.N]`.
3. Talaria self-update: `git ls-remote --tags` of Talaria's own repo; if a newer
   release exists and was not reported yet, notify once.
4. With a new candidate: run `prepare <tag>`.

A specific older release can be prepared on request: `talaria prepare <tag>`
(shell) or `/prepare <tag>` (bot).

### 7.2 `talaria prepare <tag>`

Production is not touched.

1. **Disk floor.** Refuse below `disk.floor_gb`.
2. **Pull and verify** (§6.2).
3. **Downgrade guard.** If the candidate's latest schema version is lower than
   the on-disk `_config_version`, refuse.
4. **Staging copy** in `~/.local/state/talaria/staging/` (mode 700): the data
   dir minus `backup.exclude`; SQLite databases copied with Python's stdlib
   backup API (production is running; a file copy of a WAL database can tear).
5. **Baseline doctor**: the current image's `hermes doctor` against the copy,
   `--network=none`.
6. **Migrate the copy**: one-shot candidate container, `--network=none`, the
   in-container `hermes` user, `scripts/docker_config_migrate.py`,
   `HERMES_SKIP_CONFIG_MIGRATION` unset. Assert exit 0, no
   `docker_config_migrate.py failed`, and `_config_version` equal to the
   candidate's latest.
7. **Load check**: `load_config()` under the candidate succeeds.
8. **Candidate doctor** against the migrated copy; compare with the baseline
   (§7.5).
9. **Semantic config diff** old → migrated (§7.6).
10. `shred -u` every `.env*` in the copy; delete the copy.
11. **Report** (one message, §8.3) and record the candidate as pending with the
    predicted `_config_version`.

Any failure: candidate rejected with the reason; production untouched.

### 7.3 `talaria deploy <tag>`

Only for the pending candidate, after `/approve <tag>` or from the shell.

1. Lock. Stop Hermes. **Backup** labelled `pre-<tag>` (§9.1). History commit.
2. **Migrate for real**: the same one-shot command as §7.2 step 6, on the real
   data dir. Assert the predicted `_config_version`. On failure: restore the
   backup, start the old image, report. Only the stop was visible.
3. `:previous` ← `:current`; `:current` ← candidate digest.
4. Start. The Quadlet always sets `HERMES_SKIP_CONFIG_MIGRATION=1`, so **a
   container start never migrates**.
5. Post-start checks (§7.4).
6. Pass: sweep secret copies (§9.3), history commit, remove superseded images,
   one short "deployed" message. Fail: rollback (§7.7).

### 7.4 Post-start checks

All must pass:

1. Container `running` for 60 s with no restart-count increase.
2. `gateway.pid` present and the process alive in the container.
3. If the agent uses Telegram: `getMe` with the agent's token returns `ok`.
4. Dashboard `/api/status` answers 200 and reports `auth_required: true`.
5. On-disk `_config_version` equals the expected value.
6. Optional (`smoke.completion = on`): one minimal model completion through the
   gateway. Off by default: it costs credits.

### 7.5 Doctor comparison

Parse `✓ / ⚠ / ✗` lines per check name. A **regression** (`✓` → `⚠`/`✗`, or a
`✓` check disappearing) fails the candidate. Checks that were already `⚠`/`✗`
in the baseline are ignored. Baseline and candidate both run on the same copy
with `--network=none`, so network-dependent checks fail the same way on both.

### 7.6 Semantic config diff

Parse both YAML files; compare as nested structures. Report:

- `_config_version` from → to;
- added keys with values;
- removed keys, marked **inert** when the removed value equals the candidate's
  default for that key;
- **changed values** — these are the behaviour changes a person must see (e.g. a
  migration resetting the personality or rewriting a concurrency limit). They
  are found generically from the diff; there is no hand-maintained list of
  "dangerous" migrations.

Formatting-only changes (quoting, escapes, indentation, key order) produce no
diff.

### 7.7 Rollback

`talaria rollback` (also `/rollback`), and automatically after a failed deploy:
stop → restore the `pre-<tag>` backup → `:current` ← `:previous` → start →
assert `_config_version` equals the value recorded before the deploy → checks.

Image and data are **always** restored together. A failure between the data
restore and the image switch leaves Hermes **stopped**, never running
mismatched. If the version assertion fails, Hermes is left stopped and a loud
message is sent. Rollback never retries in a loop.

### 7.8 Reject

`/reject <tag>` records the tag in `state.json`; it is never offered again. A
newer release is offered as usual.

### 7.9 Migration extractor (report only)

`lib/migrations.py` finds the migration registry per tag in the Hermes clone
(its location moved between releases). It **fails loudly** when it cannot find
it; it never reports "no migrations" by default. It is informational: the
authority on what happened is the rehearsal (§7.2).

### 7.10 Optional summary

`summary.url` (any OpenAI-compatible endpoint), `summary.model`, and
`TALARIA_SUMMARY_API_KEY` in `.env`. Empty `summary.url` (default) disables it.
Input: the deterministic report plus release notes and `git log` between the
tags. Output is labelled as model-generated from untrusted upstream text and
**never gates** anything. The docs advise against pointing it at Hermes itself:
the agent has tools and memory, and release notes are untrusted text.

## 8. Telegram connector

### 8.1 Rules

- Python stdlib only (`urllib.request`, `json`, `subprocess`).
- Runs `talaria <command>` with a fixed argument vector; never a shell; never
  interpolates message text beyond validated arguments (tags must match
  `^v\d{4}\.\d{1,2}\.\d{1,2}(\.\d+)?$`, backup IDs their own pattern).
- Accepts messages only from `TALARIA_TELEGRAM_USER_ID` (numeric), only in a
  private chat. Everything else is logged and dropped without reply.
- `Restart=always`; retries with backoff when Telegram is unreachable.

### 8.2 Startup resync

On start it calls `getUpdates?offset=-1`, **does not execute** what it gets,
and continues from `update_id + 1`. A command queued while the bot was down is
never run.

### 8.3 Commands

| Command | Effect |
|---|---|
| `/status` | version, image age, container state, free disk, pending candidate, Talaria update available |
| `/check` | run `check` now |
| `/prepare <tag>` | prepare a specific release |
| `/approve <tag>` | deploy; must match the pending candidate |
| `/reject <tag>` | never offer this release again |
| `/rollback` | atomic rollback (§7.7) |
| `/backups` | list backups |
| `/restore <id> CONFIRM` | restore (§9.2) |
| `/logs [n]` | last gateway log lines (default 50, max 200) |

### 8.4 When Talaria sends a message

Only: a candidate is ready (the report); deploy, rollback or restore finished
or failed; a failure (fetch, pull, verify, rehearsal, disk floor); a new
Talaria release (once). Everything else is silent.

The candidate report: tag, image digest, schema from → to, migration steps
that fire (from the per-tag extractor, §7.9), the semantic diff with changed
values first, doctor result, optional summary (§7.10), and the `/approve` and
`/reject` commands to send.

## 9. Data safety

### 9.1 Backups

- `~/.local/state/talaria/backups/<UTC-timestamp>-<label>.tar.gz`, plus a
  sidecar `.json`: image tag, digest, revision, `_config_version`, size.
- IDs are timestamps, so several backups can share a label (`manual`).
- Labels: `pre-<tag>`, `pre-restore`, `manual`, `daily`.
- **Hermes is stopped** for the tar; SQLite files and their `-wal`/`-shm`
  sidecars are included together.
- `backup.exclude`: regenerable caches (default `.cache`, `.npm`,
  `home/.cache`, `home/.npm`).
- Retention: `backup.keep` newest (default 5), counted across labels. The
  backup the current deployment would roll back to is never pruned.
- `backup.daily = on` adds a daily backup (a short stop); off by default.

### 9.2 Restore

`talaria restore <id>`: backup the current state as `pre-restore`, then restore
data **and** the image recorded in the sidecar. If that image is gone, pull it
again **by digest** and verify its revision. If that is impossible (a local,
non-official image), refuse. Then start and run the post-start checks.

### 9.3 Secret sweep

After every production start (deploy, rollback, restore, adopt), move
`config.yaml.bak-*` and `.env.bak-*` out of the data root into
`~/.local/state/talaria/migrate-bak/` (mode 700), keep the newest pair, and
`shred -u` the rest.

### 9.4 History

A git repo at `~/.local/state/talaria/history/` versioning `config.yaml` and
`memories/*.md` (not `*.lock`). `talaria history` commits if anything changed:
daily from the timer (message `daily <date>`) and before/after prepare, deploy,
rollback and restore (tag and `_config_version` in the message). **Silent**; no
remote. Never under git: the data dir as a whole, `.env`, caches, databases.

## 10. Dashboard

Always behind the bundled basic-auth provider:
`HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin` and a generated password, via
`hermes.env`. The container sees these two variables, not Talaria's `.env`.
(The agent can read its own environment; that is acceptable — the login guards
the network path, not the agent.)

`dashboard.bind`:

- `loopback` (default): `PublishPort=127.0.0.1:<port>:9119`. Reach it through
  an SSH tunnel.
- `tailscale`: publish on the Tailscale IPv4 address (read at setup via
  `tailscale ip -4`). Offered by setup only when Tailscale is running.

Never a public interface.

## 11. Configuration

`talaria.conf`: `key = value` lines, `#` comments, no sections, no quoting
rules beyond trimming. Read by `lib/conf.sh` (`conf_get key`) and by Python.

| Key | Default |
|---|---|
| `service_user` | `hermes` |
| `data_dir` | `~/hermes-data` |
| `image` | `docker.io/nousresearch/hermes-agent` |
| `hermes_repo` | `https://github.com/NousResearch/hermes-agent` |
| `talaria_repo` | this project's GitHub URL |
| `dashboard.bind` | `loopback` |
| `dashboard.port` | `9119` |
| `backup.keep` | `5` |
| `backup.daily` | `off` |
| `backup.exclude` | `.cache .npm home/.cache home/.npm` |
| `disk.floor_gb` | `6` |
| `check.time` | `04:30` |
| `smoke.completion` | `off` |
| `summary.url` / `summary.model` | empty |

`.env` (mode 600): `TALARIA_TELEGRAM_TOKEN`, `TALARIA_TELEGRAM_USER_ID`,
optional `TALARIA_SUMMARY_API_KEY`. Setup never generates or prints bot tokens
or API keys; it only tells the person where to put them.

## 12. Security model

Also published as `docs/threat-model.md`.

1. **The agent cannot reach the updater.** All Talaria files live outside the
   data mount. A prompt-injected agent can read neither the bot token nor its
   backups, and cannot rewrite its history.
2. **One commander.** Numeric user ID, private chat only.
3. **No root after setup.** One root block at install time; nothing later.
4. **Upstream code never runs on the host.** Rehearsal, migration, doctor and
   YAML parsing run in containers; rehearsal and migration without network.
5. **No secret copies in the mount** (§9.3).
6. **Pinned, verified images** (§6.2).
7. **Untrusted text never gates.** The summary is advisory; approval is a
   person.
8. **Bot input is validated** against strict patterns and passed as argv.
9. **One operation at a time** (`flock`); a second caller is refused.

## 13. Failure modes

| Failure | Handling |
|---|---|
| git fetch / registry unreachable | message, no state change |
| Disk below floor | refuse pull, message |
| Revision mismatch | candidate refused, message |
| Downgrade (candidate schema < on-disk) | refused |
| Rehearsal: migration fails, partial, or doctor regression | candidate refused, production untouched |
| Real migration fails in deploy | restore backup, start old image, message |
| Post-start check fails | rollback (§7.7) |
| Version wrong after rollback | leave stopped, loud message |
| Rollback fails | leave stopped, loud message, no retry loop |
| Restore image unavailable | refuse restore |
| Telegram down | shell still works; bot retries with backoff |
| Concurrent operation | refused via `flock` |
| Another gateway polls the agent token | setup stops (§5.2) |

## 14. Testing

### 14.1 Unit and component tests

- **bats** for `bin/talaria` and `lib/*.sh`, with `podman`, `systemctl`, `git`,
  `curl` and `sleep` replaced by stubs on `PATH` that record calls and replay
  fixtures. Covered at least:
  - candidate selection: git ∩ registry, rejected tags, `-desktop`/`latest`/`main`
    ignored, tag ordering incl. `.N` suffixes;
  - revision verification pass/fail; downgrade guard;
  - disk floor; lock contention;
  - backup: excludes, sidecar, retention across labels, protected rollback
    backup;
  - restore: `pre-restore` first; re-pull by digest; refusal for local images;
  - deploy: real-migration failure restores and restarts the old image;
  - **rollback atomicity**: a failure injected between data restore and image
    switch leaves Hermes stopped;
  - secret sweep; history no-op when unchanged, `.lock` never staged;
  - setup: every step idempotent (second run changes nothing); `--plan`
    changes nothing; root block content; foreign same-name user → `STOP`;
    ambiguous installs → `FOUND` + `STOP` + exit 12; token conflict → `STOP`.
- **unittest** for `confdiff.py` (formatting-only → no diff; inert removals;
  changed values), `doctordiff.py` (regression, pre-existing warning ignored,
  vanished check, malformed input), `migrations.py` (both registry layouts;
  missing registry fails loudly), `telegram.py` (foreign ID, group chat, tag
  validation, stale `/approve`, `/restore` without `CONFIRM`, startup resync
  not executed, backoff).
- **shellcheck** on all shell code.
- Every test suite runs in CI on every push and pull request (§15.2). No Hermes
  image is built or pulled in that workflow.

### 14.2 Mutation testing

After the code and tests are complete, **mutation testing proves the tests
detect real faults**:

- **Python**: `mutmut` over `lib/*.py` and `telegram.py`, run against the
  unittest suite.
- **Bash**: no mature bash mutation tool exists, so the repo ships
  `tests/mutate.sh`: it applies one mutation at a time to a copy of the
  sources and runs bats. Operators: swap `-eq`/`-ne`, `-lt`/`-ge`,
  `-gt`/`-le`, `==`/`!=`; swap `&&`/`||`; delete a `!`; replace `return 1`
  with `return 0` and `exit N` with `exit 0`; delete a single command line
  inside a function body; change numeric literals by ±1. Each mutant runs with
  a timeout; a timeout counts as killed.
- **Threshold**: ≥ 90 % of mutants killed in each language. Every surviving
  mutant is either killed by a new test or listed in `docs/mutation-report.md`
  with the reason it is equivalent (behaviour-identical). The report records
  counts per module and the date and commit it was run on.
- Mutation runs are a release gate, run by the release workflow (§15.3), not
  on every push (they are slow).

### 14.3 Integration test

Before each Talaria release, on a disposable VM: fresh setup to `DONE`; a
deploy of an older release, then an update to a newer one via the bot;
a deliberate `/rollback`; a `/restore`. Adoption is exercised on a real
existing install.

### 14.4 Upstream-fact checks

A script re-verifies the facts in §3 against a real image and the Hermes repo
(revision label, `HERMES_SKIP_CONFIG_MIGRATION` behaviour, the in-container
user of the boot migration, dashboard auth variables, registry tag format). It runs
weekly and before each Talaria release (§15.4); a changed fact blocks the
release.

## 15. Publishing, CI/CD, dependencies

### 15.1 Repository

- Public GitHub repo, MIT licence.
- Commits use the GitHub no-reply address
  (`<id>+<login>@users.noreply.github.com`), never a personal email.
- `main` is protected: changes land through pull requests with CI green.
- README: what it is and that it is opinionated; second paragraph: *"Setting
  this up with a coding agent? Point it at `AGENTS.md`."*; requirements; what
  it changes on the system (user, units, files); threat model link.
- Docs contain no deployment-specific values.

### 15.2 CI (`.github/workflows/ci.yml`)

On every push and pull request, on `ubuntu-latest`:

- `shellcheck` on all shell code;
- bats suite;
- unittest suite on Python 3.10 and 3.12 (the supported floor and a current
  version);
- a lint check that the docs and templates contain no values from a real
  deployment (IPs, Telegram IDs, tokens) — a simple pattern scan.

Workflow hygiene, applied to every workflow:

- third-party actions pinned by **commit SHA**, not tag;
- `permissions: contents: read` at the top level; any job that needs more
  declares exactly that;
- tools come from `pixi.toml`/`pixi.lock`, the same versions as local
  development.

### 15.3 Releases (`.github/workflows/release.yml`)

Triggered by pushing a tag `vMAJOR.MINOR.PATCH` (semver; `0.x` until the first
stable release). The workflow:

1. runs everything from §15.2;
2. runs the **mutation gate** (§14.2) and fails below the threshold;
3. runs the upstream-fact checks (§14.4);
4. creates the GitHub release with notes generated from the commits since the
   previous tag.

There are no build artefacts: a release is the tagged source.
`talaria self-update <tag>` checks out a tag and re-runs `setup`
(idempotent); `check` finds new releases with `git ls-remote --tags` (§7.1).
The integration test on a disposable VM (§14.3) is a manual checklist item in
the release pull request, not automated.

### 15.4 Scheduled (`.github/workflows/upstream.yml`)

Weekly: the upstream-fact checks (§14.4) against the newest Hermes release.
This pulls one Hermes image on the runner. A failure means upstream changed
something Talaria relies on, and shows as a failed workflow run.

### 15.5 Dependencies

- **Dependabot** (`.github/dependabot.yml`) for the `github-actions`
  ecosystem, weekly, grouped into one pull request. It keeps the SHA-pinned
  actions current.
- **pixi dev tools**: a monthly workflow runs `pixi update` and opens a pull
  request with the new lock file, so bats, shellcheck and mutmut stay current
  through the same review-and-CI path. Only this job gets `contents: write`
  and `pull-requests: write`.
- Runtime has no dependencies to track (§2). The Hermes image is tracked by
  Talaria itself, not by Dependabot.

## 16. Open questions

None blocking. Decided during design: dedicated service user; loopback
dashboard with login by default, Tailscale optional; pull-only images in v1;
adopt via one Talaria template with an explicit `--apply-adopt`; non-adoptable
installs via `--import-data`; silent history; summary off by default and not
pointed at Hermes.
