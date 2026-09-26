# Talaria — Design

Date: 2026-09-26 (revised the same day after an adversarial review)
Status: draft, pending review

> **Talaria** — safe, approved updates for self-hosted
> [Hermes Agent](https://github.com/NousResearch/hermes-agent) on rootless podman.
> Opinionated.

## 1. Purpose

Hermes Agent ships frequent releases. Updating a self-hosted instance is
`pull && restart`, and the container migrates its data on start. What goes wrong
is rarely that the new container fails to start. It is that it starts,
**migrates the data**, and then misbehaves — leaving data the previous version
cannot safely read, with nothing in the running system that notices.

Talaria makes updates boring:

- It finds new releases, pulls them pinned by digest, and **rehearses every
  migration on a throwaway copy** of the data before production is touched.
- It reports what the update changes and **waits for a person to approve**.
- It deploys, verifies, and on failure **rolls back image and data together**.
- It **survives crashes and reboots** mid-operation without ever running an
  image on data it does not match.
- Approval, rollback and restore run from a **dedicated Telegram bot** that the
  agent itself cannot reach or steer.
- Setup is one idempotent command a **coding agent** can drive end to end; the
  person only does what needs a person.

### 1.1 Goals

- Adopt an existing rootless-podman Hermes install, or install Hermes fresh.
- Track upstream release tags. Deploy only on explicit approval.
- Never run an image on data whose config or database schema it does not match.
- Low noise: messages only when something needs a decision or has failed.
- Everything host-specific is configuration; nothing personal in code or docs.

### 1.2 Non-goals (v1)

- Multiple hosts, multiple Hermes instances per host.
- Building Hermes images (§6.4).
- Configuring Hermes itself (providers, models, personality).
- Tracking `main`, release candidates or canaries.
- Automatic deploys, automatic self-updates.
- Importing non-podman installs (Docker, Compose, pip/uv `~/.hermes`):
  planned for v2 as `--import-data`.
- A model-written release summary.
- Other approval channels. The connector is named `telegram.py` and messages go
  through a connector-neutral outbox (§8.5), so others can be added later.

## 2. Assumptions and requirements

- Linux with systemd (user instance, linger) and **rootless podman ≥ 4.9**
  (`UserNS=keep-id:uid=…,gid=…` in Quadlet).
- `git` ≥ 2.34 (SSH tag verification), `python3` ≥ 3.10 (stdlib only),
  `gzip`, `tar` (GNU), `flock`, `systemd-run`.
- One Hermes per host, run by a **dedicated service user** (default `hermes`).
- amd64 or arm64 (the architectures upstream publishes).
- Hermes **≥ v2026.6.5**, for both the running version at adoption and every
  candidate: it is the first release with `HERMES_SKIP_CONFIG_MIGRATION` and the
  bundled basic-auth dashboard provider (§3).
- Runtime code: bash + Python stdlib; all HTTP through Python's `urllib`.
  Anything that must parse YAML or import Hermes code runs **inside a Hermes
  image** (which ships PyYAML), never on the host.
- Development tools are never needed on a deployed host: `pytest`, `mutmut`,
  `pyyaml` and `shellcheck-py` from `pyproject.toml` + `uv.lock`; `bats-core`,
  `bats-support`, `bats-assert` as git submodules under `tests/lib/`.

## 3. Facts about upstream this design relies on

Checked against the Hermes repository at `v2026.8.3` and `v2026.9.24` on
2026-09-26. Every row is re-verified by the contract tests (§14.4), weekly and
before each Talaria release, because upstream can change any of them.

| # | Fact | Where (tag:file) | Consequence |
|---|---|---|---|
| F1 | Official images `docker.io/nousresearch/hermes-agent:<git-tag>` per release, amd64+arm64 | registry | pull, don't build |
| F2 | Image label `org.opencontainers.image.revision` = commit of the git tag | image config | verify each pull |
| F3 | Release tags look like `v2026.9.24`, `v2026.7.7.2`; the repo also carries `rc.N-v0.21.5`, `abandoned-rc.*`, `v0.21.4+canary.<ts>` | git tags | one tag pattern, §7.1 |
| F4 | Hermes runs as uid/gid **10000** in the container; `stage2-hook.sh` remaps it to `HERMES_UID`/`HERMES_GID` and chowns `/opt/data` | 9.24:`Dockerfile:168`, `stage2-hook.sh:40-105` | Quadlet and every one-shot use `keep-id:uid=10000,gid=10000` (§4.3) |
| F5 | On start, `stage2-hook.sh` runs `docker_config_migrate.py` and **swallows its failure** | 8.3:`stage2-hook.sh:453-454` | a failed start migration still yields a running container |
| F6 | `HERMES_SKIP_CONFIG_MIGRATION=1` makes that script exit 0 at once; first in v2026.6.5 | 8.3:`docker_config_migrate.py:58-60` | Talaria disables start-time config migration and runs it itself |
| F7 | Config migration self-restores on error or if the version does not advance | 8.3:`docker_config_migrate.py` | failure = new binary on old config |
| F8 | Config migrations are a forward-only ladder with a support floor; no `current > latest` guard | `hermes_cli/config_migrations.py` | old binary on newer config reports healthy |
| F9 | **`state.db` has its own schema ladder**, run whenever a `SessionDB` is opened (not gated by F6): `SCHEMA_VERSION` 25 at v2026.8.3, 30 at v2026.9.24 | 9.24:`hermes_state_schema.py:992`, `hermes_state.py:547` | rehearse and version-check the database too |
| F10 | Config backups written by migration: `config.yaml.bak-<ts>` / `.env.bak-<ts>` in the data root (v2026.8.3); `backups/config/<name>.pre-docker-migrate.<ts>` (v2026.9.24), which also sweeps old root-level ones there | 8.3:`docker_config_migrate.py`; 9.24:`config_backups.py:22,55-62` | secret sweep covers both locations (§9.3) |
| F11 | `load_config()` does **not** fail on unparseable YAML; it falls back to the last good config or defaults | 9.24:`config.py:2173ff` | Talaria parses YAML itself (`yaml.safe_load`), never trusts `load_config()` as a check |
| F12 | `hermes doctor` labels embed values (`Config version up to date (v33)`); checks are added, renamed and removed between releases; its exit code differs by version (non-zero on issues at v2026.9.24, 0 on older builds) | 9.24:`doctor_config.py:327-328`, `doctor.py:188` | normalised comparison, exit code ignored (§7.6) |
| F13 | The dashboard only starts if `HERMES_DASHBOARD` is truthy; on a non-loopback bind it **fails closed** without an auth provider; `HERMES_DASHBOARD_INSECURE` is ignored (since v2026.7.1) | 9.24:`s6-rc.d/dashboard/run:9-19`, 8.3:`…/run:33-50` | template sets `HERMES_DASHBOARD=1` + basic auth |
| F14 | Bundled basic auth: `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` + `_PASSWORD`; first in v2026.6.5 | `plugins/dashboard_auth/basic` | zero-infrastructure login |
| F15 | Image Python is 3.13 | 9.24:`Dockerfile:43` | helpers target 3.13; CI tests it |

Observed, not documented upstream: a Telegram `getUpdates` without an offset
returned nothing while `getWebhookInfo` reported one pending update; `offset=-1`
returned it (2026-08-26). The startup resync (§8.2) does not depend on this.

## 4. Architecture

```
  talaria-updater.timer ──► talaria-updater.service ──► talaria check / history / recover
                                                              │
  talaria-telegram.service (telegram.py)                      │
     │  long-poll; the only process talking to Telegram       │
     │  starts operations as transient units:                 │
     └──► systemd-run --user --unit=talaria-op-… talaria <op> ┤
                                                              ▼
                                        bin/talaria (bash) — the only thing that
                                        changes anything; journals every phase
                                        in state.json; writes messages to outbox/
                                                              │
  hermes.service (Quadlet hermes.container)                   │
     ExecStartPre = talaria guard  ◄──────────────────────────┘ refuses to start on
     mounts only <data_dir> → /opt/data                          an unfinished or
                                                                 mismatched state
```

### 4.1 Repository layout

```
README.md            second paragraph points coding agents to AGENTS.md
AGENTS.md            the agent runbook (§5.4)
LICENSE              MIT
bin/talaria          entry point, argument parsing, dispatch
lib/*.sh             one module per concern: conf, state, lock, podman, images,
                     backup, restore, rehearse, deploy, rollback, recover,
                     guard, history, sweep, outbox, setup
lib/py/              host-side stdlib helpers, importable modules with main():
                     state.py (atomic journal), outbox.py, sqlite_copy.py,
                     safefs.py (no-follow file operations)
helpers/             run INSIDE a Hermes image, bind-mounted read-only:
                     migrate.py, dbversion.py, confdiff.py, doctor.py,
                     migrations.py
telegram.py          the Telegram connector
templates/           hermes.container, talaria-updater.{service,timer},
                     talaria-telegram.service, hermes.env
tests/               bats, pytest, fixtures, contract tests, mutation tooling;
                     tests/lib/ holds the bats submodules
docs/                threat-model.md, buildkit.md, mutation-report.md
pyproject.toml       dev tools only (uv); uv.lock committed
```

Each module has one purpose and a small interface so it can be tested with its
collaborators stubbed. Bash never parses YAML or JSON beyond calling a helper.

### 4.2 On-host layout (as the service user)

| Path | Content |
|---|---|
| `~/.local/share/talaria/releases/<tag>/` | one checkout per installed Talaria release |
| `~/.local/share/talaria/current` | symlink to the active release (§15.3) |
| `~/.local/bin/talaria` | symlink to `current/bin/talaria` |
| `~/.config/talaria/talaria.conf` | settings (§11) |
| `~/.config/talaria/.env` | Talaria secrets, mode 600 |
| `~/.config/talaria/hermes.env` | `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD` only, mode 600, passed to the container |
| `~/.config/containers/systemd/hermes.container` | Quadlet |
| `~/.config/systemd/user/talaria-*.{service,timer}` | Talaria units |
| `~/.local/state/talaria/` | `state.json`, `lock`, `history.lock`, `backups/`, `images/` (digest records), `history/` (git), `staging/`, `migrate-bak/`, `outbox/`, `outbox/failed/`, `logs/` |
| `~/.local/state/talaria/hermes.git` | bare clone of the Hermes repo (tags only); never built or executed |
| `<data_dir>` (default `~/hermes-data`) | Hermes data, mounted at `/opt/data` |
| `/etc/talaria/owner` | written by the root block: the service user's name (§5.1) |

All directories under `~/.local/state/talaria/` are mode 700.

### 4.3 User namespace

The Quadlet and **every one-shot container** Talaria runs use
`UserNS=keep-id:uid=10000,gid=10000` (`--userns=keep-id:uid=10000,gid=10000
--user 10000:10000` for one-shots) and `HERMES_UID=10000 HERMES_GID=10000`. The
host service user is then uid 10000 in the container, so every file in
`<data_dir>` is owned by the service user on the host, and Talaria's host-side
operations (backup, copy, sweep, history) need neither `podman unshare` nor
root. The uid 10000 is read from the image (`id -u hermes`) at pull time and
must match; a different uid refuses the candidate.

### 4.4 Filesystem safety

The data dir is writable by the agent, so every host-side operation on it
treats its contents as hostile:

- Act on **regular files only**; check with `lstat`, open with `O_NOFOLLOW`,
  and require the resolved path to stay inside the data dir.
- Copies (staging, history) never dereference symlinks; a symlink is either
  skipped (history) or copied as a symlink (staging, tar).
- Restores extract with GNU tar into a **new empty directory** (§9.2), never over
  existing data.
- Deleting secrets uses `safefs.py` (unlink after overwrite on a regular file
  opened with `O_NOFOLLOW`). Overwriting is best effort: on copy-on-write or
  journaling filesystems old blocks may survive; the threat model says so.

## 5. Setup

### 5.1 Ownership and the root block

The first `setup` run, as the operator (the admin's login account), checks
`getent passwd <user>` and `/etc/talaria/owner`:

| User exists | Listed in `/etc/talaria/owner` | Result |
|---|---|---|
| no | — | root block: create user, subuid/subgid, linger, operator sudo rule, write owner file |
| yes | yes | proceed as the service user |
| yes | no | `FOUND: user <u> exists and is not Talaria's` → `STOP` (exit 13). The person either picks another `--user`, or runs the root block for **adoption**, which takes this existing user over (sudo rule + owner file only). AGENTS.md makes the agent ask. |

The root block is printed as **one** `ACTION REQUIRED` block of shell commands to
run as root. A coding agent usually cannot enter a sudo password; the person
pastes it once. Nothing after it needs root.

The operator sudo rule is `<operator> ALL=(<user>) NOPASSWD: ALL`. It makes the
operator account — and anything running as it, including coding agents — fully
trusted (§12.1). The final setup summary prints the one command that removes
the rule, for people who want to drop it after setup.

### 5.2 `talaria setup`

Non-interactive and idempotent: a fixed list of steps; each is checked, done if
possible, or ends the run with an action line. Re-running continues where it
stopped.

```
talaria setup [--plan] [--user NAME] [--adopt UNIT] [--apply-adopt] [--dev]
```

Output lines:

```
OK: <step>
MISSING: <tool> [>= version]
  hint: Debian/Ubuntu: <pkg> · Fedora/RHEL/Rocky/Alma: <pkg> · Arch: <pkg>
ACTION REQUIRED: <what the person must do, exactly>
FOUND: <an existing install or account>
STOP: <why setup cannot continue>
DONE
```

Exit codes: 0 done; 10 action required; 11 missing prerequisite; 12 ambiguous
installs; 13 refused (foreign account, unsupported version, unsafe mounts,
unsupported install type); 1 internal error.

Steps:

1. **Prerequisites.** Report missing tools with package hints. Never install.
2. **Ownership** (§5.1).
3. **Install Talaria for the service user.** The operator's checkout must be at
   a release tag (else `STOP`, unless `--dev`). Setup clones that tag from
   `talaria_repo` into `releases/<tag>/` as the service user, verifies the tag
   signature (§15.3) and that its commit equals the operator's checkout, and
   points `current` at it. From here on setup runs from the installed copy.
4. **Detect.** Hermes containers and Quadlets of the service user (image name
   contains `hermes-agent`): none → **fresh**; exactly one → **adopt** that one;
   several → `FOUND` per install and `STOP` (exit 12), choose with
   `--adopt <unit>`. Docker/Compose or non-container installs → `FOUND` +
   `STOP` (exit 13): not supported in v1.
5. **Adopt checks** (read-only). Read the unit's image, mounts, ports and
   environment. Refuse (exit 13) if the running image is older than v2026.6.5
   (by revision ancestry), if there is more than one mount, if the one mount is
   not a directory at `/opt/data`, or if it mounts sockets, `$HOME` or anything
   else. Environment carries over from an allow-list of Hermes variables;
   `HERMES_DASHBOARD_INSECURE` and anything unknown are dropped and listed.
   Render the Talaria Quadlet and print the diff. Nothing changes.
6. **`--apply-adopt`** (explicit; it restarts the live agent): stop the old
   unit; full backup labelled `adopt`; if the files are not owned by the service
   user (the old unit lacked `keep-id`), `podman unshare chown -R 0:0
   <data_dir>` makes them so; keep the old unit as `<name>.talaria-orig`
   (disabled) and install `hermes.container`; start and run the post-start
   checks. On failure: restore the `adopt` backup (§9.2), reinstate and start
   the old unit, report. Adoption never leaves the person without their old
   setup.
7. **Image.** Fresh: newest release (§7.1), pulled and verified. Adopt: the
   running image is recorded; a non-official image is recorded as
   `local:<revision>` and retained while any backup references it (§6.3).
8. **Dashboard password.** Generated into `hermes.env`; setup reports the file,
   never the password.
9. **Telegram.**
   1. `ACTION REQUIRED`: create a bot via @BotFather and put the token into
      `~/.config/talaria/.env` as `TALARIA_TELEGRAM_TOKEN`.
   2. `ACTION REQUIRED`: send `/start` to the bot. Setup reads the sender's
      numeric ID.
   3. The bot sends that chat a 6-digit code; `ACTION REQUIRED`: send the code
      back to the bot. On a match the ID is written as
      `TALARIA_TELEGRAM_USER_ID`. The code only ever appears in Telegram, so a
      coding agent cannot confirm the ID on the person's behalf.
   Setup talks to Telegram through `telegram.py --setup`, keeping all Telegram
   code in one module.
10. **Units.** Render and install units, `daemon-reload`, enable and start,
    post-start checks (§7.5), summary, `DONE`.

### 5.3 Other gateways on the same token

Two gateways with the same agent bot token consume each other's messages.
Setup cannot reliably detect a poller elsewhere, so: for adoption it stops the
adopted unit itself and refuses if any other Hermes container of the service
user is running; for anything outside its view, `AGENTS.md` makes the agent ask
the person to confirm the old instance is stopped.

### 5.4 `AGENTS.md`

Short, imperative, for coding agents:

1. Read this file fully before running anything.
2. Clone the repo at the latest release tag and run `bin/talaria setup --plan`
   as the operator. Explain the plan to the user in plain words.
3. **Confirm the service user name with the user before handing over the root
   block.** If setup reports an existing account, ask whether to adopt it.
4. `MISSING`: work out the install command for this distribution; ask before
   installing.
5. `ACTION REQUIRED`: relay in plain words. Never ask for secrets in the chat —
   they go into the files named. The Telegram code is sent to the bot, not to
   you.
6. Re-run `talaria setup` until `DONE`.
7. `FOUND` + `STOP`: explain what was found; let the user choose.
8. Before `--apply-adopt`: show the diff, say it restarts the agent, get an
   explicit yes.
9. **Never run `deploy`, `rollback` or `restore` yourself**; those are the
   person's decisions, made in Telegram.
10. Never edit Talaria's state, backups or history.

## 6. Images

### 6.1 Source

`image` (default `docker.io/nousresearch/hermes-agent`), release tags only
(§7.1). Candidates below v2026.6.5 are never offered, including via `/prepare`.

### 6.2 Pinning and verification

"Digest" means the **platform image digest** podman reports after pulling
(`podman image inspect --format '{{.Digest}}'`) for this host's architecture.
For each pulled tag Talaria records in `images/<tag>.json`: digest, revision,
in-image uid, pull time. Checks after every pull:

- `org.opencontainers.image.revision` equals `git rev-list -n1 <tag>`;
- the in-image `hermes` uid is 10000 (§4.3).

Any mismatch refuses the candidate. Re-pulls (restore, §9.2) use
`<image>@<digest>` and must yield the same digest.

Local names: `localhost/hermes-agent:<tag>` per pulled release, and the pointer
tags `:current` (what the Quadlet runs) and `:previous`. Pointers are always set
to a recorded digest, never to a tag that could move.

### 6.3 Retention

Keep `:current`, `:previous`, a pending candidate, and **every image
referenced by a retained backup's sidecar**. Remove the rest after a successful
deploy. Local, non-official images can never be re-pulled, so they are kept for
as long as any backup references them.

### 6.4 Building (not in v1)

Building from source was proven on podman 4.9 with BuildKit inside the service
user's rootless podman (four added capabilities, pinned builder image).
`docs/buildkit.md` records the recipe so `image.source = build` can be added
later.

## 7. Update flow

### 7.1 Release tags and `talaria check`

One definition, used everywhere (candidate selection, bot validation):

```
RELEASE_TAG = ^v(20\d\d)\.(\d{1,2})\.(\d{1,2})(\.(\d+))?$
```

ordered by the numeric tuple (year, month, day, suffix or 0).

`check` (daily, from the timer):

1. `git fetch --tags --force` into `hermes.git`. If a tag that Talaria has
   already pulled now points at a different commit, send a `failed` message
   once and never use that tag again.
2. `podman search --list-tags --limit 1000 <image>`. If exactly 1000 tags come
   back, warn once that the list may be truncated.
3. Candidates: tags matching `RELEASE_TAG`, present in both lists, ≥ v2026.6.5,
   newer than deployed, not `rejected` or `failed` (§7.8).
4. If the newest upstream tag in git is created after the deployed release and
   matches neither `RELEASE_TAG` nor the known non-release shapes (`rc.*`,
   `abandoned-rc.*`, `*+canary.*`), send one `failed` message ("upstream tag
   format changed"), so Talaria does not go stale silently.
5. Talaria self-update: `git ls-remote --tags <talaria_repo>`; a newer release
   not yet reported → one `talaria_release` message.
6. A new candidate → `prepare` it (§7.2).

A specific release can be prepared on request: `talaria prepare <tag>` or
`/prepare <tag>`.

### 7.2 `talaria prepare <tag>`

Production is not touched. Runs in its own unit; **preemptible** by `rollback`
and `restore`, which stop it and clean up its staging dir.

1. **Disk floor.** Free space ≥ `disk.floor_gb` + image size (from the
   manifest) + staging size (data dir minus exclusions). Otherwise a transient
   failure (§7.8).
2. **Pull and verify** (§6.2).
3. **Record production fingerprints**: sha256 of `config.yaml` and `.env`.
4. **Plausibility.** Parse the on-disk `_config_version` (helper, in the
   *current* image). If it exceeds what both the current image and the
   candidate support, send a `failed` message ("config claims schema N, no known
   image supports it") instead of silently refusing. If it exceeds only the
   candidate's latest, refuse as a downgrade.
5. **Staging copy** in `staging/<id>/` (700): the data dir minus
   `backup.exclude`, never following symlinks (§4.4); SQLite databases through
   `sqlite_copy.py` (stdlib backup API — production is running and a file copy
   of a WAL database can tear). Stale staging dirs are deleted first.
6. **Baseline doctor**: current image, `helpers/doctor.py` against the copy,
   `--network=none`.
7. **Migrate the copy**: the one-shot migration (§7.4) with the candidate.
8. **Database migration**: `helpers/dbversion.py --migrate` opens `state.db`
   with the candidate's `SessionDB`, which runs its schema ladder (F9), and
   reports `schema_version`; it must equal the candidate's `SCHEMA_VERSION`.
9. **Candidate doctor** against the migrated copy; compare (§7.6).
10. **Semantic config diff**, original → migrated (§7.7).
11. Delete the copy: secret files through `safefs.py`, then the tree.
12. **Pending**: record the candidate with its predicted `_config_version`,
    predicted `state.db` schema, fingerprints, digest and report; write the
    `candidate` message (§8.4).

Failures classify as transient or permanent (§7.8). Production is untouched
either way.

### 7.3 `talaria deploy <tag>`

Only for the pending candidate. Runs in its own transient unit. Every phase is
journalled in `state.json` **before** it starts (§7.10).

1. Take the lock. Stop Hermes (`hermes.service`).
2. **Fingerprint check.** If `config.yaml` or `.env` changed since prepare,
   start Hermes again, re-run `prepare` for the same tag, and stop here: the
   person approves what will actually be applied.
3. **Backup**, labelled `pre-<tag>` (§9.1). History commit.
4. **Migrate for real**: the one-shot migration (§7.4) with the candidate on the
   real data dir, then `dbversion.py --migrate` on the real `state.db`. Both
   versions must equal the prediction. On failure: restore the backup, start the
   old image, report `failed`. Only the stop was visible.
5. `:previous` ← `:current`; `:current` ← candidate digest.
6. Start. The Quadlet sets `HERMES_SKIP_CONFIG_MIGRATION=1` and `state.db` is
   already at the candidate's schema, so the start migrates nothing.
7. **Post-start checks** (§7.5).
8. Pass: secret sweep (§9.3), history commit, image retention (§6.3), message
   `deployed`. Fail: rollback (§7.9), then the tag is marked `failed`.

### 7.4 The one-shot migration

Exact invocation, identical in prepare and deploy:

```
podman run --rm --network=none \
  --userns=keep-id:uid=10000,gid=10000 --user 10000:10000 \
  -v <dir>:/opt/data -v <talaria>/helpers:/opt/talaria:ro \
  -e HERMES_HOME=/opt/data -e HOME=/opt/data \
  -w /opt/hermes --entrypoint /opt/hermes/.venv/bin/python \
  <image@digest> /opt/talaria/migrate.py
```

`HERMES_SKIP_CONFIG_MIGRATION` is not set. `migrate.py` runs upstream's
`docker_config_migrate.py` `main()` in-process, then parses the resulting
`config.yaml` with `yaml.safe_load` (F11) and prints one JSON object: exit code,
`from`, `to`, the image's latest schema and support floor, and error text.
Talaria asserts on that JSON, never on log strings. A config below the support
floor is a permanent failure with that explanation.

### 7.5 Post-start checks

All must pass:

1. The container is `running` throughout 60 s, with no restart-count increase.
2. Inside, `s6-svstat` reports the gateway service **up** with its uptime
   growing across the 60 s (sampled every 5 s) — catching a crash loop that s6
   restarts within the container.
3. The dashboard `/api/status` answers 200 and reports `auth_required: true`.
4. On-disk `_config_version` and `state.db` `schema_version` equal the expected
   values (`dbversion.py` without `--migrate`, read-only).

### 7.6 Doctor comparison

`helpers/doctor.py` runs `hermes doctor` in-process and emits JSON: one entry
per check line with status (`ok`/`warn`/`error`) and a **normalised key** — the
label with parenthesised segments, version numbers, dates and digits removed,
whitespace collapsed. The exit code is ignored (F12).

- **Regression** (fails the candidate): a key present in both with `ok` →
  `error`.
- **Reported, not gating**: `ok` → `warn`, keys that disappeared, new keys.

Baseline and candidate both run on the same copy with `--network=none`, so
network-dependent checks fail the same way on both.

### 7.7 Semantic config diff

`helpers/confdiff.py` (in the candidate image) parses both files and compares
nested structures. It reports `_config_version` from → to, added keys, removed
keys (**inert** if the removed value equals the candidate's default), and
**changed values**, listed first — the behaviour changes a person must see.
Formatting-only changes produce no diff.

### 7.8 Candidate states

Per tag, in `state.json`:

```
            ┌──────────── transient failure (retried next check) ─────┐
  new ──► preparing ──► pending ──► deploying ──► deployed            │
              │            │  │         │                             │
              │            │  │         └──► failed (auto-rolled back)│
              │            │  └──► superseded (a newer tag prepared)  │
              │            └──► rejected (/reject)                    │
              └──► failed (permanent) ◄───────────────────────────────┘
```

- **Transient** (network, registry, disk floor, lock busy): the tag stays
  eligible; the next `check` retries it. The same transient reason is reported
  once, not daily.
- **Permanent** (revision or uid mismatch, migration failure, database schema
  mismatch, doctor regression, downgrade, support floor): `failed`. Never
  offered again automatically; `/prepare <tag>` retries it on purpose.
- A deploy that rolled back → `failed`, with the reason.
- `/reject` → `rejected`; never offered again.
- **One pending candidate at a time.** A pending candidate is reported once. If
  a newer release is prepared successfully, it replaces the pending one
  (`superseded`), and its report says so.

### 7.9 Rollback

`talaria rollback` undoes **the last deploy**, and only that: it is available
only while the last production-changing operation was a deploy and its
`pre-<tag>` backup and `:previous` image exist; otherwise it is refused,
pointing to `/restore`.

Sequence: stop → restore the `pre-<tag>` backup (§9.2) → `:current` ←
`:previous` → start → assert the recorded pre-deploy `_config_version` and
`state.db` schema → post-start checks → `rolled_back` message.

Image and data always move together. If anything fails between the data restore
and the image switch, the journal and the guard (§7.10) keep Hermes **stopped**,
across reboots, and a loud `failed` message is sent. Rollback never retries in a
loop.

A rollback **discards everything the agent wrote since the backup**; the bot
therefore requires `/rollback CONFIRM <tag>` and states the backup's age first
(§8.3).

### 7.10 Journal, guard and recovery

**Journal.** `state.json` is written atomically (temp file, `fsync`, rename,
`fsync` of the directory) by `lib/py/state.py`. Before each phase of `deploy`,
`rollback`, `restore` and `--apply-adopt`, the operation, its phase and its unit
name are recorded.

**Guard.** The Quadlet has `ExecStartPre=%h/.local/bin/talaria guard`. It is
fast, runs on the host, reads no YAML, and fails (Hermes does not start) when:

- an operation is recorded as in progress but its unit is not running (a crash
  or reboot interrupted it), unless the phase says Hermes may run;
- the `halt` flag is set (Talaria deliberately left Hermes stopped);
- the on-disk `_config_version` (read by a line match on the top-level key,
  e.g. `_config_version: 33`) differs from the value recorded for `:current`;
- `dashboard.bind = tailscale` and the Tailscale address is not up after
  waiting up to 120 s (§10).

**Recovery.** `talaria recover` runs at boot (from `talaria-updater.service`,
whose timer has `Persistent=true`, and when `talaria-telegram` starts) and on
demand. For an interrupted operation it decides by phase:

| Interrupted after | Action |
|---|---|
| stop, before the backup finished | discard the partial backup, start the old image |
| backup, before migration | start the old image |
| migration, before the pointer switch | restore the backup, start the old image |
| pointer switch, before checks passed | run the post-start checks; pass → keep, fail → rollback |
| rollback or restore, any phase | re-run it from the start (both are idempotent) |

Every recovery ends with a message. If recovery itself fails, `halt` is set and
Hermes stays stopped until the person acts (`/resume`). `recover` also deletes
stale `staging/` dirs.

### 7.11 Migration extractor (report only)

`helpers/migrations.py` lists the config migration steps that will fire, from
the candidate's registry (its location moved between releases). If it cannot
find the registry, the report says "migration list unavailable" — it never
claims "no migrations" — and the contract tests (§14.4) fail, flagging the
drift. It never gates: the rehearsal is the authority.

## 8. Telegram connector

### 8.1 Rules

- Python stdlib only.
- **Operations run outside the bot's cgroup**: `telegram.py` starts them with
  `systemd-run --user --unit=talaria-op-<op>-<id> --collect talaria <op> …`, so a
  bot restart never kills a running deploy. The bot acknowledges at once
  (`ack`) and keeps polling; results arrive through the outbox.
- Fixed argument vectors; never a shell. Arguments are validated: tags against
  `RELEASE_TAG`, backup IDs against `^\d{8}T\d{6}Z-[a-z0-9-]{1,40}$`, `CONFIRM`
  literally.
- Accepts messages only from `TALARIA_TELEGRAM_USER_ID` (numeric), only in a
  private chat. Everything else is logged and dropped without reply.
- `Restart=always`; backoff when Telegram is unreachable.

### 8.2 Startup resync

On start the bot calls `getUpdates?offset=-1`, does **not** execute what it
gets, and continues from `update_id + 1`. If anything was skipped it sends one
`reply`: "N commands sent while I was offline were ignored; send them again if
still wanted."

### 8.3 Commands

| Command | Lock | Effect |
|---|---|---|
| `/status` | none | version, image age, container state, free disk, pending candidate, halt flag, Talaria update available |
| `/check` | op | run `check` now |
| `/prepare <tag>` | op | prepare a specific release |
| `/approve <tag>` | op | deploy; must match the pending candidate |
| `/reject <tag>` | none | mark the tag `rejected` |
| `/rollback` | none | reply with what would be rolled back, the backup's age, and the exact confirm command |
| `/rollback CONFIRM <tag>` | op | rollback (§7.9); `<tag>` must be the last deployed tag |
| `/backups` | none | list backups: ID, label, age, size, image |
| `/restore <id>` | none | reply with what would be restored and lost, and the confirm command |
| `/restore <id> CONFIRM` | op | restore (§9.2) |
| `/resume` | op | clear `halt` after the person has looked; start Hermes if the guard passes |
| `/logs [n]` | none | last gateway log lines (default 50, max 200), known token patterns redacted |

`op` = the operation lock (`flock`). `rollback` and `restore` **preempt** a
running `prepare` (§7.2); otherwise a busy lock is refused with a `refused`
message. The timer's own `check` hitting a busy lock is silent (logged only).
Read-only commands take no lock.

### 8.4 What goes into the outbox

Kinds: `candidate` (a candidate is ready); `deployed`, `rolled_back`,
`restored`; `failed` (fetch, pull, verify, rehearsal, disk floor, tag format
change, recovery); `talaria_release` (once); `ack`, `refused`, `reply` (answers
to commands). Everything else is silent.

The candidate report: tag, image digest, `_config_version` and `state.db`
schema from → to, the migration steps that fire (§7.11), the semantic diff with
changed values first, the doctor result, and the `/approve <tag>` and
`/reject <tag>` commands.

### 8.5 The outbox

`talaria` never talks to Telegram. It writes each message as one JSON file into
`outbox/`: written under a temporary name, then renamed. Fields: `id`, `seq`
(monotonic counter from `state.json`), `created`, `kind`, `text` (trusted,
written by Talaria), `untrusted` (optional list of text blocks derived from
upstream or agent-writable data: config values, doctor lines, log lines), and
optional `commands`.

`telegram.py` checks the outbox between long-polls (every ≤ 30 s) and sends
files in `seq` order:

- **Rendering**: HTML parse mode; every dynamic string passes through
  `html.escape`; `untrusted` blocks only inside `<pre>`, where Telegram creates
  no command links or URLs — so the agent cannot plant a tappable `/rollback` in
  a report. `commands` are rendered as `<code>` for copying.
- **Size**: messages over 4096 characters are split at line boundaries into
  numbered parts. A file is deleted only after Telegram confirms delivery of all
  its parts.
- **Errors**: `429` → wait `retry_after`; `5xx`/network → backoff and retry; a
  **permanent `4xx`** → the file moves to `outbox/failed/` and a short `failed`
  notice is sent, so one bad message never blocks the queue.
- Messages older than 7 days are still sent, with their age noted.

## 9. Data safety

### 9.1 Backups

- `backups/<UTC-timestamp>-<label>.tar.gz` plus a sidecar `.json`: `seq`, image
  tag, digest, revision, `_config_version`, `state.db` schema, size, sha256.
  Ordering uses `seq`, never wall-clock time.
- Labels: `pre-<tag>`, `pre-restore`, `adopt`, `manual`; several backups may
  share a label.
- **Hermes is stopped**; SQLite files are included together with their
  `-wal`/`-shm` files.
- `backup.exclude`: regenerable caches plus Hermes's own `backups/` (default
  `.cache .npm home/.cache home/.npm backups`).
- Written to a temporary name, verified (`gzip -t`, `tar -tzf`), `fsync`ed,
  then renamed; the sidecar is written last. A backup without a valid sidecar
  does not exist for retention or restore.
- **Space check first**: free space ≥ `disk.floor_gb` + the uncompressed size
  of what will be archived; otherwise the operation stops before stopping
  Hermes.
- Retention: `backup.keep` newest (default 5), counted across labels. The backup
  the current deployment would roll back to, and the `adopt` backup until the
  first successful deploy, are never pruned.

### 9.2 Restore

`talaria restore <id>`:

1. Verify the archive against its sidecar sha256.
2. Space check (one uncompressed copy + floor).
3. Ensure the sidecar's image is present; if not, pull `<image>@<digest>` and
   verify it (§6.2). If that is impossible (a local, non-official image that is
   gone), refuse.
4. Stop Hermes. Back up the current state as `pre-restore`.
5. Extract into `<data_dir>.restore-<id>` (same filesystem), then swap: the
   current data dir is renamed to `<data_dir>.old-<id>`, the restored one takes
   its name; the old one is deleted after the post-start checks pass.
6. Point `:current` at the sidecar's image; start; post-start checks against the
   sidecar's versions.

A restore makes the last deploy non-rollbackable (§7.9); a restore is undone by
restoring its `pre-restore` backup.

### 9.3 Secret sweep

After every production start (deploy, rollback, restore, adopt) and in
`recover`: move `config.yaml.bak-*` and `.env.bak-*` from the data root, and
`config.yaml.*` / `.env.*` from `backups/config/` (F10), into `migrate-bak/`
(700). Regular files only, never symlinks (§4.4). Keep the newest config and env
copy; remove the rest through `safefs.py`.

### 9.4 History

A git repo at `history/` versioning `config.yaml` and `memories/*.md` (regular
files only; `*.lock` excluded). `talaria history` commits if anything changed:
daily from the timer (`daily <date>`), and before/after deploy, rollback and
restore (tag and versions in the message). It takes `history.lock`, not the
operation lock. **Silent**; no remote. Never under git: the data dir as a whole,
`.env`, caches, databases.

## 10. Dashboard

Always behind the bundled basic-auth provider: `HERMES_DASHBOARD=1` and
`HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin` in the Quadlet, the password from
`hermes.env`. The container sees these, not Talaria's `.env`; the agent can read
its own environment, and the login guards the network path, not the agent.

`dashboard.bind`:

- `loopback` (default): `PublishPort=127.0.0.1:<port>:9119`. Reached through an
  SSH tunnel.
- `tailscale`: `PublishPort=<tailscale-ip>:<port>:9119`, the address read at
  setup via `tailscale ip -4`. A user unit cannot order itself after the system
  `tailscaled.service`, so the guard (§7.10) waits up to 120 s for that address
  before Hermes starts. Offered only when Tailscale is running at setup.

Never a public interface.

## 11. Configuration

`talaria.conf`: `key = value` lines, `#` comments, no sections, no quoting;
values are trimmed; a leading `~/` expands to the service user's home, nothing
else expands. Read by `lib/conf.sh` (`conf_get key`) and by Python.

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
| `backup.exclude` | `.cache .npm home/.cache home/.npm backups` |
| `disk.floor_gb` | `6` |
| `check.time` | `04:30` |

`.env` (mode 600): `TALARIA_TELEGRAM_TOKEN`, `TALARIA_TELEGRAM_USER_ID`. Setup
never generates or prints tokens; it only says where they go.

## 12. Security model

Also published as `docs/threat-model.md`.

### 12.1 Trust

| Party | Trust |
|---|---|
| The person holding the pinned Telegram account | trusted: approves, rolls back, restores |
| The operator account (and anything running as it, including coding agents) | **fully trusted** — its sudo rule gives it the service user. AGENTS.md tells agents not to deploy; that is guidance, not enforcement. The rule can be removed after setup (§5.1). |
| The Hermes agent, the data dir, anything it writes | **untrusted** |
| Upstream release content: images, release text, config values produced by migrations | **untrusted** beyond the digest and revision checks |
| Telegram senders other than the pinned ID | untrusted, ignored |

### 12.2 Rules

1. **The agent cannot reach the updater.** All Talaria files live outside the
   data mount; the container sees only the data dir and the dashboard password.
2. **Hostile files.** No symlink following, regular files only, restores into
   empty directories (§4.4). The agent cannot redirect a Talaria write, copy or
   delete to a file outside the data dir.
3. **One commander.** Numeric user ID, private chat only.
4. **No root after setup.**
5. **Upstream code never runs on the host.** Migrations, database opening,
   doctor and YAML parsing run in containers, all but the real start with
   `--network=none`.
6. **What is approved is what is applied** (fingerprint check, §7.3).
7. **Untrusted text is inert in messages**: escaped, inside `<pre>` (§8.5).
   Destructive commands need `CONFIRM` and name what they destroy.
8. **Pinned, verified images** (§6.2).
9. **No secret copies in the mount** (§9.3), within the limits of overwriting on
   modern filesystems.
10. **Verified Talaria releases** (§15.3).
11. **Crash safety**: journal, guard and recovery (§7.10).

## 13. Failure modes

| Failure | Handling |
|---|---|
| git fetch / registry unreachable | transient; one message per distinct reason |
| Tag moved upstream | message once; tag never used |
| Tag format changed upstream | message once (§7.1) |
| Disk below floor (prepare, backup, restore) | refused before anything stops |
| Revision or uid mismatch | `failed` |
| Implausible `_config_version` | message; nothing refused silently |
| Rehearsal: config or database migration fails, doctor regression | `failed`; production untouched |
| Config changed between prepare and approve | re-prepare; new report |
| Real migration fails during deploy | restore, start old image, message |
| Post-start check fails | rollback; tag `failed` |
| Crash / power loss mid-operation | guard blocks start; `recover` finishes or reverts (§7.10) |
| Rollback or recovery fails | `halt`; Hermes stays stopped across reboots; loud message |
| Restore image unavailable | refused |
| Corrupt or truncated backup | never counted (verified before rename; sha256 on restore) |
| Telegram down | outbox queues; shell still works |
| A message Telegram rejects permanently | moved to `outbox/failed/`, notice sent |
| Concurrent operations | lock; rollback/restore preempt prepare |
| Bot restarts during an operation | operation continues in its own unit |
| Tailscale address not up at boot | guard waits 120 s, then systemd retries |

## 14. Testing

### 14.1 Unit and component tests

**bats** for `bin/talaria` and `lib/*.sh`, with `podman`, `systemctl`,
`systemd-run`, `git`, `sleep` and the Python helpers replaced by stubs on
`PATH` that record calls and replay fixtures. At least:

- tags: `RELEASE_TAG` edge cases, ordering with `.N` suffixes, git ∩ registry,
  ≥ v2026.6.5 floor, rejected/failed/superseded, a moved tag, the tag-format
  alert;
- candidate states: every transition in §7.8; transient reasons reported once;
- revision and uid verification; digest pinning of pointers;
- disk floor for prepare, backup and restore (nothing stopped when short);
- lock contention; preemption of prepare; silent timer lock miss;
- backup: excludes, sidecar and `seq`, a truncated archive never counted,
  retention incl. protected `pre-<tag>` and `adopt` backups, image retention of
  referenced images;
- restore: `pre-restore` first; extract into a new dir and swap; re-pull by
  digest; refusal for missing local images;
- deploy: fingerprint change → re-prepare; failed real migration → restore and
  old image;
- **crash recovery**: a fault injected after **each** journalled phase of
  deploy, rollback, restore and apply-adopt; the guard refuses to start; `recover`
  produces the outcome in §7.10's table;
- rollback atomicity: failure between data restore and image switch leaves
  `halt` set;
- secret sweep: both locations, never touches symlinks;
- history: no-op when unchanged, symlinks and `.lock` never staged;
- outbox: atomic files, `seq` order, correct `kind` per outcome, nothing for
  silent operations;
- setup: every step idempotent; `--plan` changes nothing; the ownership table in
  §5.1 including the re-run after the root block; adopt refusals (version,
  mounts); environment allow-list; `--apply-adopt` failure path restores the old
  unit; every exit code.

**pytest** (unittest-style tests, run by pytest so mutmut can drive them) for:
`confdiff.py` (formatting-only → no diff; inert removals; changed values
first), `doctor.py` normalisation and comparison (version/date variants map to
one key; regression vs reported-only; malformed input), `migrate.py` and
`dbversion.py` JSON contracts (against fixture modules mimicking both upstream
layouts), `safefs.py` (a symlink planted at every path the sweep, history and
staging copy touch is never followed), `state.py` (atomic writes; a kill
mid-write leaves the old state readable), `sqlite_copy.py` (copy while another
process writes), `outbox.py`, and `telegram.py`: pinned ID only, group chats
ignored, argument validation for every command, `/rollback` and `/restore`
without `CONFIRM` only describe, startup resync executes nothing and sends one
notice, operations started via `systemd-run` while the poll loop continues,
outbox delivery (order, 4096 split, `429` `retry_after`, permanent `4xx` →
`failed/`, delete only after full delivery), untrusted text rendered escaped
inside `<pre>`, and the setup code round trip.

**shellcheck** on all shell code. Python tests run on 3.10, 3.12 and 3.13 (host
floor, current, image).

### 14.2 Mutation testing

After the code and tests are complete, mutation testing proves the tests detect
real faults:

- **Python**: `mutmut` over `lib/py/`, `helpers/` and `telegram.py`, driven by
  pytest.
- **Bash**: `tests/mutate.sh` applies one mutation at a time to a copy of the
  sources and runs bats. Operators: swap `-eq`/`-ne`, `-lt`/`-ge`, `-gt`/`-le`,
  `==`/`!=`; swap `&&`/`||`; delete a `!`; `return 1` → `return 0`, `exit N` →
  `exit 0`; delete one command line inside a function body (log/echo lines
  excluded); change a numeric literal by ±1.
- **Honest scoring**: a mutant that fails `bash -n` (or Python compilation) is
  **invalid** — excluded from the score and counted separately. Timeouts count
  as killed but are listed separately.
- **Threshold**: ≥ 90 % of valid mutants killed in each language. Every
  surviving mutant is either killed by a new test or listed in
  `docs/mutation-report.md` with the reason it is equivalent. The report records
  counts per module and operator, invalid and timeout counts, date and commit.
- Mutation runs are a release gate (§15.4), not a per-push step.

### 14.3 Integration test

Before each release, on a disposable VM: fresh setup to `DONE`; deploy an older
release, then update via the bot; `/rollback CONFIRM`; `/restore … CONFIRM`; a
**hard power-off in the middle of a deploy**, then reboot — the guard must block
the start and recovery must revert. Adoption is exercised on a real existing
install.

### 14.4 Upstream contract tests

`tests/contract/` runs against a real Hermes image and the Hermes repository and
covers every row F1–F15 of §3: revision label, uid, `HERMES_SKIP_CONFIG_MIGRATION`,
the migration JSON contract, the `state.db` ladder via `dbversion.py`, backup
locations, doctor output shape and normalisation, dashboard variables and
fail-closed behaviour, tag shapes, the migration extractor, image Python. The
helpers run exactly as in production (same `podman run` argument vector) on a
fixture data dir. A changed fact fails the run. Runs weekly and before each
release (§15.4, §15.5).

## 15. Publishing, CI/CD, dependencies

### 15.1 Repository

- Public GitHub repo, MIT licence.
- Commits use the GitHub no-reply address
  (`<id>+<login>@users.noreply.github.com`).
- `main` is protected: pull requests with CI green.
- README: what it is and that it is opinionated; second paragraph: *"Setting
  this up with a coding agent? Point it at `AGENTS.md`."*; requirements; what it
  changes on the system; threat-model link.
- Docs contain no deployment-specific values.

### 15.2 CI (`.github/workflows/ci.yml`)

On every push and pull request, on `ubuntu-latest`: shellcheck; bats; pytest on
Python 3.10, 3.12 and 3.13; a pattern scan so docs and templates contain no real
deployment values (IPs, Telegram IDs, tokens).

For every workflow: actions pinned by commit SHA; top-level
`permissions: contents: read`, jobs declare exactly what more they need; tools
from `uv.lock` (`uv sync --frozen`) and the bats submodules (`actions/checkout`
with `submodules: true`).

### 15.3 Releases and self-update

Releases are **signed** git tags `vMAJOR.MINOR.PATCH` (semver, `0.x` until
stable), signed with an SSH key; `allowed_signers` ships in the repo.

`talaria self-update <tag>`: take the op lock; clone the tag into
`releases/<tag>/`; `git verify-tag` it against the `allowed_signers` of the
**currently installed** release (trust anchored at first install; changing keys
requires a release signed by the old key); run the new release's `setup`
(idempotent); swap the `current` symlink atomically. Running processes keep
executing their own release directory, so no script is replaced underneath a
running bash. The two newest releases are kept.

### 15.4 Release workflow (`.github/workflows/release.yml`)

On a pushed tag: everything from §15.2; the mutation gate (§14.2); the upstream
contract tests (§14.4); a check that the tag is signed by a key in
`allowed_signers`; then a GitHub release with notes from the commits since the
previous tag. No build artefacts. The VM integration test (§14.3) is a checklist
item in the release pull request.

### 15.5 Scheduled (`.github/workflows/upstream.yml`)

Weekly: the contract tests (§14.4) against the newest Hermes release. A failure
means upstream changed something Talaria relies on, and shows as a failed run.

### 15.6 Dependencies

**Dependabot** (`.github/dependabot.yml`) is the only update mechanism:

| Ecosystem | What it updates |
|---|---|
| `github-actions` | the SHA-pinned actions |
| `uv` | `pytest`, `mutmut`, `pyyaml`, `shellcheck-py` |
| `gitsubmodule` | `bats-core`, `bats-support`, `bats-assert` |

All three: weekly, grouped into one pull request, `cooldown: default-days: 14`.

The bats submodules track their upstream default branches. Dependabot follows
commits there, not release tags, and none of the three repos has a release
branch; the cooldown filters out short-lived commits and CI runs the full suite
on every update.

pixi is not used: Dependabot cannot read `pixi.toml`/`pixi.lock` (its `conda`
ecosystem reads only `environment.yml`; checked 2026-09-26), while it supports
uv and submodules. The runtime has no dependencies to track; the Hermes image is
tracked by Talaria itself.

## 16. Decisions and deferred items

Decided: dedicated service user; loopback dashboard with login by default,
Tailscale optional; pull-only images; adoption through one template with an
explicit `--apply-adopt` and a way back to the old unit; silent history repo;
mutation testing as a release gate at 90 %; Dependabot for all development
dependencies.

Deferred to v2: `--import-data` (Docker, Compose, non-container installs);
building images from source.

Cut: the model-written summary; daily backups; the optional completion smoke
check.
