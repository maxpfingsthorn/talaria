# Talaria — Design

Date: 2026-09-26 (fourth revision, after three adversarial reviews)
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

- It finds new releases, pulls them pinned by digest, and **rehearses the
  migration on a throwaway copy** of the data before production is touched.
- It reports what the update changes and **waits for a person to approve**.
- It deploys, verifies, and on failure **rolls back image and data together**.
- If a crash or reboot interrupts a change, it **returns to the state before
  that change**, by one simple rule (§7.10).
- Approval, rollback and restore run from a **dedicated Telegram bot** that the
  agent itself cannot reach or steer.
- Setup is one idempotent command a **coding agent** can drive end to end; the
  person only does what needs a person.

### 1.1 Goals

- Adopt an existing rootless-podman Hermes install, or install Hermes fresh.
- Track upstream release tags. Deploy only on explicit approval.
- Low noise: messages only when something needs a decision or has failed.
- Everything host-specific is configuration; nothing personal in code or docs.

### 1.2 What is guaranteed, and what is not

Guaranteed:

- **The root `config.yaml` and the root `state.db`** are migrated by Talaria in
  a controlled step, rehearsed on a copy first, and checked before every start:
  Hermes never starts with an image that cannot handle their versions (§7.10).
- **The whole data dir** is backed up before every change; image and data are
  always rolled back together.

Not individually guaranteed: other SQLite stores (`kanban.db` and others) and
per-profile configs and databases migrate when Hermes opens them (F17). They are
covered by the full backup and the joint rollback, not by version checks.

### 1.3 Non-goals (v1)

- Multiple hosts, multiple Hermes instances per host.
- Building Hermes images (§6.4).
- Configuring Hermes itself (providers, models, personality).
- Tracking `main`, release candidates or canaries.
- Automatic deploys, automatic self-updates.
- Completing an interrupted change automatically: it is reverted and can be
  approved again (§7.10).
- Importing non-podman installs (Docker, Compose, pip/uv `~/.hermes`): planned
  for v2 as `--import-data`.
- Hosts with **SELinux in enforcing mode** (setup refuses).
- A model-written release summary; gating on `hermes doctor`.
- Other approval channels. The connector is named `telegram.py` and messages go
  through a connector-neutral outbox (§8.5).

## 2. Assumptions and requirements

- Linux with **systemd ≥ 255** (user instance, linger, `ExecCondition=`; 255 is
  the version the systemd integration tests run on — Ubuntu 24.04, Debian 13,
  Fedora 40 and later) and **rootless podman ≥ 4.9** (`UserNS=keep-id:uid=…`).
- `git` ≥ 2.34 (SSH tag verification), `python3` ≥ 3.10 (stdlib only), `gzip`,
  GNU `tar`, `flock`, `systemd-run`.
- SELinux absent, disabled or permissive.
- One Hermes per host, run by a **dedicated service user** (default `hermes`).
- amd64 or arm64.
- Hermes **≥ v2026.6.5** for the running version at adoption and for every
  candidate (first release with `HERMES_SKIP_CONFIG_MIGRATION` and the bundled
  basic-auth dashboard provider).
- Runtime code: bash + Python stdlib; all HTTP through `urllib`. Anything that
  parses YAML or imports Hermes code runs **inside a Hermes image**, never on the
  host.
- Development tools only (never on a deployed host): `pytest`, `mutmut`, `pyyaml`,
  `shellcheck-py` via `pyproject.toml` + `uv.lock`; `bats-core`, `bats-support`,
  `bats-assert` as git submodules under `tests/lib/`.

## 3. Facts about upstream this design relies on

Checked against the Hermes repository at `v2026.6.5`, `v2026.8.3` and
`v2026.9.24` on 2026-09-26. The contract tests (§14.4) re-verify every row
against **both the oldest supported (v2026.6.5) and the newest** release, weekly
and before each Talaria release.

| # | Fact | Where (tag:file) | Consequence |
|---|---|---|---|
| F1 | Official images `docker.io/nousresearch/hermes-agent:<git-tag>` per release, amd64+arm64 | registry | pull, don't build |
| F2 | Official images carry `org.opencontainers.image.revision` = commit of the git tag; locally built images may carry nothing (no label, no `/etc/hermes/image-provenance.json`, no `.hermes_build_sha`) | image config; 9.24:`Dockerfile:368-384` | verify official pulls; identify local images by image ID and `hermes --version` (F18) |
| F3 | Release tags look like `v2026.9.24`, `v2026.7.7.2`; the repo also carries `rc.N-v0.21.5`, `abandoned-rc.*`, `v0.21.4+canary.<ts>` (about daily) and ad-hoc tags | git tags | one tag pattern; classify every new tag (§7.1) |
| F4 | Hermes runs as uid/gid 10000; `stage2-hook.sh` remaps to `HERMES_UID`/`HERMES_GID` and chowns `/opt/data` | 9.24:`Dockerfile:168`, `stage2-hook.sh:40-105` | `keep-id:uid=10000,gid=10000` everywhere (§4.3) |
| F5 | On start, `stage2-hook.sh` runs `docker_config_migrate.py` and **swallows its failure** | 8.3:`stage2-hook.sh:453-454`; 9.24:`:654-656` | a failed start migration still yields a running container |
| F6 | `HERMES_SKIP_CONFIG_MIGRATION=1` makes that script exit 0 at once | 6.5:`docker_config_migrate.py:43-45`; 8.3:`:58-60` | Talaria disables start-time config migration and runs it itself |
| F7 | From v2026.8.3 the migration self-restores on error or if the version does not advance; v2026.6.5 does not | 8.3:`docker_config_migrate.py` | Talaria asserts the result itself |
| F8 | Config migrations are a forward-only ladder; no `current > latest` guard; a support floor in later releases | `hermes_cli/config*.py` | old binary on newer config reports healthy |
| F9 | `state.db` has its own schema ladder, run whenever a `SessionDB` is opened (not gated by F6). `SCHEMA_VERSION`: 14 at v2026.6.5 (`hermes_state.py:36`), 25 at v2026.8.3 (`hermes_state_common.py:155`), 30 at v2026.9.24 (`hermes_state_common.py:239`). The stored version may be **held back** below `SCHEMA_VERSION`, depending on runtime conditions (FTS migrations incomplete, FTS5 unavailable, a rebuild deferred to another process), and advance on a later open | 9.24:`hermes_state_schema.py:398-402,992,1101,1239-1250` | versions are checked against a **range**, never an exact value (§7.10) |
| F10 | Migration writes plaintext `.env` copies: `.env.bak-<ts>` in the data root (v2026.8.3); `backups/config/.env.pre-docker-migrate.<ts>` (v2026.9.24). Upstream's legacy sweep does not move `.env.bak-*` | 8.3:`docker_config_migrate.py`; 9.24:`hermes_cli/config_backups.py:22,29,55-62` | Talaria sweeps `.env` copies (§9.3) |
| F11 | `backups/config/config.yaml.good.<ts>` is Hermes's last-known-good config; `load_config()` falls back to it (or defaults) instead of failing on broken YAML | 9.24:`config_backups.py:72-79`, `config.py:2173-2193` | never delete config copies; archive `backups/config/` with the data (§9.1); parse YAML with `yaml.safe_load` |
| F12 | `hermes doctor`: labels embed values and differ between pass and fail (`check_bool(cond, ok, bad)`), checks come and go between releases, exit code differs by version, and it loads plugins from the data dir | 9.24:`doctor_report.py:23-28`, `doctor_config.py:327-328`, `doctor.py:188`, `doctor_tools.py:236` | report-only, untrusted text |
| F13 | The dashboard only starts if `HERMES_DASHBOARD` is truthy; a non-loopback bind fails closed without an auth provider; `HERMES_DASHBOARD_INSECURE` is ignored since v2026.7.1 | 9.24:`s6-rc.d/dashboard/run:9-19` | template sets `HERMES_DASHBOARD=1` + basic auth |
| F14 | Basic auth: `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` + `_PASSWORD`; first in v2026.6.5 | `plugins/dashboard_auth/basic` | zero-infrastructure login |
| F15 | Image Python is 3.13 | 9.24:`Dockerfile:43` | helpers target 3.13 |
| F16 | s6 services are `dashboard`, `main-hermes` (`exec sleep infinity`) and `user`; **the gateway is the container's main command** | 9.24:`docker/s6-rc.d/` | health = container main process |
| F17 | Other SQLite stores alter their schema on open; profiles are reconciled at container start (`cont-init.d/02-reconcile-profiles`), not by `docker_config_migrate.py` | 9.24 | §1.2: covered by backup + joint rollback |
| F18 | `hermes --version` prints `Hermes Agent v<pkg> (<release date>)`, e.g. `v0.16.0 (2026.6.5)` | deployed image | version floor and ordering for local images |
| F19 | `/api/status` reports `auth_required` | 6.5:`web_server.py` (~765); 9.24:`web_routers/status.py:119,364` | post-start check 3 |

Observed, not documented: a Telegram `getUpdates` without an offset returned
nothing while one update was pending; `offset=-1` returned it (2026-08-26).

## 4. Architecture

```
 boot ──► talaria-boot.service (oneshot, WantedBy=default.target)
            └─ talaria start --boot     only thing that starts Hermes at boot;
                                        reverts an interrupted change first

 hermes.service  (Quadlet hermes.container, no [Install]: never started by
                  systemd on its own; Restart=on-failure after a crash)
   ExecCondition = talaria guard          skips the start on unsafe versions/halt
   ExecStartPre  = talaria wait-tailscale (only for dashboard.bind = tailscale)
   mounts only <data_dir> → /opt/data

 talaria-updater.timer → talaria-updater.service → talaria history; talaria check

 talaria-telegram.service (telegram.py)   long-poll; only process talking to Telegram
   └─ systemd-run --user --collect --unit=talaria-op-<op>-<id> ~/.local/bin/talaria <op>

 bin/talaria (bash): the only thing that changes anything. Every operation runs
 under the op lock (§7.10) and writes messages into outbox/.
```

### 4.1 Repository layout

```
README.md            second paragraph points coding agents to AGENTS.md
AGENTS.md            the agent runbook (§5.5)
LICENSE              MIT
bin/talaria          entry point, argument parsing, dispatch
lib/*.sh             conf, state, podman, images, backup, restore_data, restore,
                     rehearse, deploy, rollback, adopt, start, guard, history,
                     sweep, outbox, setup, install_release, reconfigure
lib/py/              host-side stdlib helpers (importable, main()): state.py
                     (locked atomic state), oplock.py (op lock holder),
                     outbox.py, sqlite_copy.py, sqlite_version.py, safefs.py
helpers/             run INSIDE a Hermes image, bind-mounted read-only:
                     migrate.py, dbversion.py, confdiff.py, doctor.py,
                     imageinfo.py; _upstream.py is the only module that imports
                     Hermes code (a thin adapter over both upstream layouts)
telegram.py          the Telegram connector
templates/           hermes.container, talaria-boot.service,
                     talaria-updater.{service,timer}, talaria-telegram.service,
                     hermes.env
tests/               bats, pytest, fixtures, contract/, systemd/, mutation
                     tooling; tests/lib/ holds the bats submodules
docs/                threat-model.md, buildkit.md, mutation-report.md
pyproject.toml       dev tools only (uv); uv.lock committed
```

### 4.2 On-host layout (as the service user)

| Path | Content |
|---|---|
| `~/.local/share/talaria/releases/<tag>/` | one checkout per installed Talaria release |
| `~/.local/share/talaria/current` | symlink to the active release |
| `~/.local/bin/talaria` | symlink to `current/bin/talaria`; units always call this absolute path |
| `~/.config/talaria/talaria.conf` | settings (§11) |
| `~/.config/talaria/.env` | Talaria secrets, mode 600 |
| `~/.config/talaria/hermes.env` | `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD` only, mode 600 |
| `~/.config/containers/systemd/hermes.container` | Quadlet |
| `~/.config/systemd/user/talaria-*` | Talaria units |
| `~/.local/state/talaria/` | `state.json`, `state.lock`, `op.lock`, `history.lock`, `backups/`, `images/`, `history/` (git), `staging/`, `migrate-bak/`, `outbox/`, `outbox/failed/`, `logs/` |
| `~/.local/state/talaria/hermes.git` | bare clone of the Hermes repo (tags only); never built or executed |
| `<data_dir>` (default `~/hermes-data`) | Hermes data, mounted at `/opt/data`; not a mount point itself, parent writable by the service user |
| `/etc/talaria/owner` | written by the root block: the service user's name |

All directories under `~/.local/state/talaria/` are mode 700.

### 4.3 User namespace

The Quadlet and every one-shot container use `keep-id:uid=10000,gid=10000`
(`--userns=keep-id:uid=10000,gid=10000 --user 10000:10000` for one-shots) and
`HERMES_UID=10000 HERMES_GID=10000`. The host service user is uid 10000 inside,
so all files in `<data_dir>` belong to the service user on the host and Talaria's
host-side operations need neither `podman unshare` nor root.

### 4.4 Filesystem safety

The data dir is writable by the agent; every host-side operation treats its
contents as hostile:

- act on **regular files only** (`lstat`, `O_NOFOLLOW`, resolved path must stay
  inside the data dir);
- copies never dereference symlinks (skipped in history; kept as symlinks in
  staging and tar);
- restores extract with GNU tar into a **new empty directory** (§9.2);
- secret deletion through `safefs.py` (overwrite, then unlink, on a regular file
  opened `O_NOFOLLOW`); best effort on copy-on-write or journaling filesystems;
- SQLite files from the data dir are opened on the host only by
  `sqlite_version.py` and `sqlite_copy.py`: URI `mode=ro` for reading (never
  `immutable=1`, which would ignore the WAL), `PRAGMA trusted_schema=OFF`, and
  the defensive flag where the Python version offers it;
- a `config.yaml` whose top-level `_config_version` key appears more than once is
  treated as invalid.

### 4.5 One-shot containers

Every one-shot container gets `--name talaria-<op>-<id>-<step>` and
`--label talaria.op=<id>`. Rootless podman runs containers in their own scope,
so killing an operation does not kill its containers: `talaria start` and every
preemption first run `podman rm -f` on every container labelled `talaria.op`.

## 5. Setup

### 5.1 Ownership and the root block

`setup` runs as the operator (the admin's login account) and checks `getent
passwd <user>` and `/etc/talaria/owner`:

| User exists | Listed in owner file | Result |
|---|---|---|
| no | — | root block: create a **regular** user (`useradd -m`, uid ≥ 1000, so it gets its own journal), subuid/subgid, linger, operator sudo rule, owner file |
| yes | yes | proceed as the service user |
| yes | no | `FOUND` + `STOP` (exit 13). The person picks another `--user`, or runs the root block for **adoption** (sudo rule + owner file only). AGENTS.md makes the agent ask. |

The root block is **one** `ACTION REQUIRED` block to run as root; nothing after
it needs root. The operator rule `<operator> ALL=(<user>) NOPASSWD: ALL` makes the
operator account, and anything running as it, fully trusted (§12.1); the final
summary prints the command that removes it.

### 5.2 `talaria setup`

Non-interactive and idempotent; each step is checked, done if possible, or ends
the run with an action line; re-running continues.

```
talaria setup [--plan] [--user NAME] [--adopt UNIT] [--apply-adopt] [--dev]
```

Output lines: `OK:`, `MISSING: <tool> [>= version]` with
`hint: Debian/Ubuntu: … · Fedora/RHEL/Rocky/Alma: … · Arch: …`,
`ACTION REQUIRED:`, `FOUND:`, `STOP:`, `DONE`.

Exit codes: 0 done; 10 action required; 11 missing prerequisite; 12 ambiguous
installs; 13 refused (foreign account, unsupported version, unsafe mounts,
unsupported install type, SELinux enforcing, data dir unsuitable); 1 internal
error.

`setup` (operator) orchestrates; everything that runs as the service user is a
separate subcommand, `install-release`, also used by self-update (§15.3).

Steps:

1. **Prerequisites.** Report missing tools with hints; never install.
   `getenforce` = `Enforcing` → `STOP` (13).
2. **Ownership** (§5.1).
3. **Install Talaria**: the operator's checkout must be at a release tag (else
   `STOP`, unless `--dev`). `install-release <tag>` (as the service user): clone
   into `releases/<tag>/`, verify the tag signature (§15.3) and that its commit
   equals the operator's checkout, switch `current`. Setup prints the signing key
   fingerprint for the person to compare (§12.1).
4. **State.** Create `state.json` (`mode: fresh` or `mode: adopting`) and the
   state directories. Install and enable **`talaria-boot.service`** now, before
   anything touches Hermes, so every later step is covered by boot-time recovery.
5. **Detect** Hermes containers and Quadlets of the service user: none →
   **fresh**; exactly one → **adopt**; several → `FOUND` each, `STOP` (12),
   choose with `--adopt <unit>`. Docker/Compose/non-container → `STOP` (13).
6. **Data dir**: exists or can be created; not a mount point (same `st_dev` as
   its parent); parent writable by the service user (restores rename next to
   it). Else `STOP` (13).
7. **Adopt checks** (read-only; §5.3).
8. **`--apply-adopt`** (§5.3).
9. **Image.** Fresh: newest release (§7.1), pulled and verified. Adopt: the
   running image, recorded during the adopt checks.
10. **Dashboard password** generated into `hermes.env`; the file is named, the
    password never printed.
11. **Telegram pairing**:
    1. `ACTION REQUIRED`: create a bot via @BotFather; put the token into
       `~/.config/talaria/.env` as `TALARIA_TELEGRAM_TOKEN`.
    2. Setup prints a one-time 8-character code **in the terminal**.
       `ACTION REQUIRED`: send `/pair <code>` to the bot from your Telegram
       account.
    3. The first sender with the right code, in a private chat, becomes
       `TALARIA_TELEGRAM_USER_ID`; setup prints that account's name and
       @username. Messages without the code are ignored; the code expires after
       15 minutes.
    Only the person at the terminal knows the code. Setup talks to Telegram via
    `telegram.py --pair`.
12. **Units.** Render and install `hermes.container` (no `[Install]`) and the
    Talaria units; `daemon-reload`; enable the Talaria units (Quadlet units
    cannot be enabled); `talaria start` (§7.10); on a fresh install, record the
    versions the first start produced and set `mode: normal`. Summary, `DONE`.

`talaria reconfigure` re-renders `hermes.container` from `talaria.conf` (e.g.
after changing `dashboard.bind`) and restarts Hermes under the op lock.

### 5.3 Adoption

**Checks** (read-only, during plain `setup`). Refuse (13) if:

- the running image is older than v2026.6.5 (by revision ancestry for official
  images, by the `hermes --version` release date for local ones, F18);
- there is more than one mount, the mount is not a directory at `/opt/data`, or
  anything else (sockets, `$HOME`) is mounted.

Environment carries over from an allow-list of Hermes variables;
`HERMES_DASHBOARD_INSECURE` and unknown variables are dropped and listed.
`imageinfo.py` (in the running image) records the image's supported config and
database versions, and the on-disk versions are recorded as the baseline. Setup
renders the Talaria Quadlet and prints the diff. Nothing changes.

**`--apply-adopt`** (explicit; it restarts the live agent) is a production-
changing operation like deploy (§7.10):

1. Take the op lock. Stop the old unit. **Move its Quadlet file aside** to
   `<name>.talaria-orig`, so a reboot cannot start it on half-changed data.
   `daemon-reload`.
2. Backup labelled `adopt`, taken with `podman unshare tar --numeric-owner`, so
   the original ownership is preserved exactly. Record it as the revert point
   (§7.10) together with the old unit.
3. If files are not owned by the service user: `podman unshare chown -R 0:0
   <data_dir>`.
4. Install `hermes.container`; `talaria start` (post-start checks included).
5. Pass: clear the journal, set `mode: normal`. Fail: revert (§7.10), which for
   adoption means restoring the `adopt` backup with `podman unshare tar
   --numeric-owner`, moving the old Quadlet back, and starting it.

### 5.4 Other gateways on the same token

Two gateways with the same agent bot token consume each other's messages. For
adoption Talaria stops the adopted unit itself and refuses if any other Hermes
container of the service user runs. For anything outside its view, `AGENTS.md`
makes the agent ask the person to confirm the old instance is stopped.

### 5.5 `AGENTS.md`

1. Read this file fully before running anything.
2. Clone the repo at the latest release tag; run `bin/talaria setup --plan` as the
   operator; explain the plan to the user in plain words.
3. **Confirm the service user name with the user before handing over the root
   block.** If setup reports an existing account, ask whether to adopt it.
4. `MISSING`: work out the install command for this distribution; ask first.
5. `ACTION REQUIRED`: relay in plain words. Never ask for secrets in the chat.
   Show the pairing code to the user; they send it to the bot themselves.
6. Tell the user to compare the printed signing key fingerprint (§12.1).
7. Re-run `talaria setup` until `DONE`.
8. `FOUND` + `STOP`: explain; let the user choose.
9. Before `--apply-adopt`: show the diff, say it restarts the agent, get an
   explicit yes.
10. **Never run `deploy`, `rollback` or `restore`**; those are the person's
    decisions, made in Telegram.
11. Never edit Talaria's state, backups or history.

## 6. Images

### 6.1 Source

`image` (default `docker.io/nousresearch/hermes-agent`), release tags only
(§7.1), never below v2026.6.5.

### 6.2 Identification, pinning, verification

- **Official images**: "digest" is the platform image digest podman reports after
  the pull. After every pull: revision label = `git rev-list -n1 <tag>`; in-image
  `hermes` uid = 10000. Mismatch refuses the candidate. Re-pulls use
  `<image>@<digest>` and must yield the same digest.
- **Local images** (adopted, non-official): recorded as `local:<image-id>` with
  the `hermes --version` output. They can never be re-pulled.

For every image Talaria records in `images/<ref>.json`: reference, digest or image
ID, revision (if any), release date, and — read once by `imageinfo.py` inside the
image — its latest config version (`cfg_max`) and `SCHEMA_VERSION` (`db_max`).

**Pointers live in `state.json`** (`current`, `previous`). Podman tags
`localhost/hermes-agent:current`/`:previous` are derived from state and re-applied
by every operation and by `talaria start`; a pointer switch is one state write.

### 6.3 Retention

Keep the images of `current`, `previous`, the pending candidate, and every image
referenced by a retained backup. At the end of every operation that succeeded,
remove other Talaria-pulled images (after deploys, rejections, supersessions and
permanent failures alike).

### 6.4 Building (not in v1)

Building from source was proven on podman 4.9 with BuildKit inside the service
user's rootless podman; `docs/buildkit.md` keeps the recipe.

## 7. Update flow

### 7.1 Release tags and `talaria check`

```
RELEASE_TAG = ^v(20\d\d)\.(\d{1,2})\.(\d{1,2})(\.(\d+))?$
```

ordered by (year, month, day, suffix or 0); used for candidates and bot argument
validation alike. A local image's position in that order is its `hermes
--version` release date (F18).

`check` (daily, from the timer):

1. `git fetch --tags --force`. A tag Talaria already pulled that now points
   elsewhere → one `failed` message; that tag is never used again.
2. `podman search --list-tags --limit 1000 <image>`; exactly 1000 results → one
   warning that the list may be truncated.
3. **Every tag not seen before** is classified: release (`RELEASE_TAG`), known
   non-release (`rc.*`, `abandoned-rc.*`, `*+canary.*`), or other. A version-like
   other tag (`^v\d+\.\d+\.\d+$`, e.g. `v0.21.5`) → one `failed` message
   ("upstream tag format may have changed"); ad-hoc tags are ignored. Seen tags
   are recorded; the first `check` after setup only records, without messages.
4. Candidates: release tags in both lists, ≥ v2026.6.5, newer than `current`, not
   `rejected` or `failed` (§7.8). **Only the newest is prepared.**
5. Talaria self-update: `git ls-remote --tags <talaria_repo>`; a newer release not
   yet reported → one `talaria_release` message.
6. A new candidate → `prepare` it.

`talaria prepare <tag>` / `/prepare <tag>` prepares a specific release.

### 7.2 `talaria prepare <tag>`

Production is not touched. Runs under the op lock; **preemptible** by `rollback`
and `restore` (§7.10).

1. **Space**: free ≥ `disk.floor_gb` + image size + staging size, else a
   transient failure.
2. **Pull and verify** (§6.2); `imageinfo.py` records `cfg_max`, `db_max`.
3. **Plausibility**: the on-disk `_config_version` above both `current`'s and the
   candidate's `cfg_max` → `failed` message ("config claims schema N, no known
   image supports it"); above only the candidate's → refused as a downgrade.
4. **Staging copy** `staging/<id>/` (700): data dir minus `backup.exclude` (with
   `backups/config/` included, §9.1), no symlink following, SQLite databases
   through `sqlite_copy.py` (stdlib backup API; production is running).
5. **Migrate the copy**: the one-shot migration (§7.4) with the candidate.
6. **Migrate the copy's `state.db`**: `dbversion.py --migrate` opens it with the
   candidate's `SessionDB`; result `{version, db_max}`. An exception → permanent
   failure. `version < db_max` is **held back** (F9): reported, not a failure.
7. **Doctor** (report-only, F12): current image on the unmigrated copy, then the
   candidate on the migrated copy, both `--network=none`.
8. **Semantic config diff**, original → migrated (§7.7).
9. Delete the copy (secrets through `safefs.py`).
10. **Pending**: record the candidate with the predicted config version, the
    semantic diff and its fingerprint (§7.7), digest; write the `candidate`
    message (§8.4).

Failures are transient or permanent (§7.8). Production is untouched either way.

### 7.3 `talaria deploy <tag>`

Pending candidate only. A production-changing operation (§7.10).

1. Take the op lock. **Space**: floor + backup archive + one uncompressed copy of
   the data (the revert path, §9.2); short → refused, nothing stopped.
2. Stop Hermes.
3. Backup `pre-<tag>` (§9.1); history commit. **Record the revert point**:
   `{backup: pre-<tag>, pointer: current}`.
4. **Real migration**: the one-shot migration (§7.4) with the candidate on the
   data dir, then `dbversion.py --migrate` on the real `state.db`.
5. **Outcome check**: the real config version must equal the predicted one; the
   `state.db` version must lie in [recorded before, candidate `db_max`]; the
   semantic diff between the backed-up and the migrated config must have the same
   fingerprint as the approved one. Otherwise: revert (§7.10), the candidate goes
   back to `pending` with a fresh report ("the config changed since you approved;
   here is what would happen now"), and deploy ends.
6. Pointer switch: `previous` ← `current`, `current` ← candidate (one state
   write).
7. `talaria start` (guard, post-start checks, §7.5).
8. Pass: clear the journal; secret sweep (§9.3), history commit, retention (§6.3,
   §9.1), `deployed` message. Fail: rollback (§7.9); the tag becomes `failed`.

Comparing the real outcome instead of the files keeps approvals honest without
letting a harmless config change (a `/model` switch) block updates.

### 7.4 The one-shot migration

```
podman run --rm --network=none --name talaria-<op>-<id>-migrate \
  --label talaria.op=<id> \
  --userns=keep-id:uid=10000,gid=10000 --user 10000:10000 \
  -v <dir>:/opt/data -v <talaria>/helpers:/opt/talaria:ro \
  -v <result-dir>:/opt/talaria-out \
  -e HERMES_HOME=/opt/data -e HOME=/opt/data \
  -w /opt/hermes --entrypoint /opt/hermes/.venv/bin/python \
  <image ref> /opt/talaria/migrate.py
```

`HERMES_SKIP_CONFIG_MIGRATION` is unset. `migrate.py` runs upstream's
`docker_config_migrate.py` `main()` in-process, captures its step messages (e.g.
`✓ Turned off verify-on-stop`), parses the result with `yaml.safe_load`, and
writes one JSON object to `/opt/talaria-out/result.json` (not stdout, which
upstream code or plugins could pollute): exit code, `from`, `to`, `cfg_max`, the
support floor where the image has one, the captured step messages, error text.
Talaria asserts on the JSON only. Below the support floor → permanent failure.

All helpers use this invocation shape and result-file convention; `<result-dir>`
is a fresh directory under `staging/`. The captured step messages are how the
report says **which migrations ran**.

### 7.5 Post-start checks

Run by `talaria start`; all must pass:

1. `hermes.service` active for 60 s; its systemd `NRestarts` and the container's
   `StartedAt` unchanged across that time.
2. The container's main process (`podman top`: `hermes gateway run`) keeps the
   same PID in every sample (every 5 s).
3. `/api/status` answers 200 and reports `auth_required: true` (F19).
4. The on-disk versions are in range (§7.10).

### 7.6 Doctor

Report-only (F12). Output is untrusted text, shown inside `<pre>` (§8.5).

### 7.7 Semantic config diff

`helpers/confdiff.py` (in the candidate image) compares parsed structures:
`_config_version` from → to, **changed values first**, added keys, removed keys
(**inert** if equal to the candidate's default). Formatting-only changes produce
no diff. Its **fingerprint** is a hash of the canonicalised diff, used in §7.3.

### 7.8 Candidate states

```
            ┌──────────── transient failure (retried next check) ─────┐
  new ──► preparing ──► pending ◄─┐ ──► deploying ──► deployed        │
              │            │  │   └─ outcome changed / interrupted    │
              │            │  │         │                             │
              │            │  │         └──► failed (post-start checks)
              │            │  └──► superseded (a newer tag prepared)  │
              │            └──► rejected (/reject)                    │
              └──► failed (permanent) ◄───────────────────────────────┘
```

- **Transient** (network, registry, space, lock busy): eligible again at the next
  check; the same reason is reported once.
- **Permanent** (revision/uid mismatch, migration failure, `state.db` error,
  downgrade, support floor): `failed`; retried only by `/prepare <tag>`.
- `deploying → pending`: the outcome check (§7.3 step 5) differed, or the deploy
  was interrupted and reverted (§7.10). The person approves again.
- Post-start checks failed → rolled back → `failed`.
- `/reject` → `rejected`.
- **One pending candidate**, reported once; a newer successful prepare replaces it
  (`superseded`) and its report says so.

### 7.9 Rollback

Undoes **the last deploy** only: available while the last production-changing
operation was a deploy whose `pre-<tag>` backup and `previous` image exist;
otherwise refused, pointing to `/restore`. A production-changing operation:

1. Take the op lock (preempting a `prepare`). **Space**: floor + one
   uncompressed copy.
2. Stop Hermes. Record the revert point `{backup: pre-<tag>, pointer:
   previous}` — rollback's revert point is its own goal, so an interrupted
   rollback is completed, not undone.
3. `restore_data(pre-<tag>)` (§9.2).
4. Pointer switch: `current` ← `previous`.
5. `talaria start`. Pass: clear the journal, `rolled_back` message. Fail: `halt`.

A rollback **discards everything the agent wrote since the backup**; the bot
requires `/rollback <tag> CONFIRM` and states the backup's age first (§8.3).

### 7.10 Locking, starting, and the one recovery rule

**State.** `state.json` is changed only through `lib/py/state.py`, which takes
`state.lock` (`flock`) for every read-modify-write and writes atomically (temp
file, `fsync`, rename, directory `fsync`). The outbox `seq` counter is allocated
under the same lock.

**Op lock.** Every operation runs as a child of `oplock.py`, which opens
`op.lock` close-on-exec, takes `flock` (non-blocking; busy → refused, or for the
timer, silently skipped), records its PID and process start time (from
`/proc/<pid>/stat`) in `state.json`, and runs the operation. Because the lock file descriptor is not inherited, podman, conmon or
any other child can never keep the lock alive after the holder dies. Preemption
of a `prepare`: send SIGTERM to the recorded PID, wait for the lock (30 s), remove
labelled containers, delete its staging dir.

**Journal.** A production-changing operation (deploy, rollback, restore,
apply-adopt) records `{op, id, started}` when it starts and its **revert point**
`{backup, pointer, unit?}` as soon as the backup it needs exists — always before
it changes any data. It clears the journal when it finishes. Operations that do
not change production (check, prepare, history, self-update) are never journalled
and never block anything.

**Only Talaria starts Hermes.** `hermes.container` has no `[Install]` section.
At boot `talaria-boot.service` runs `talaria start --boot`; operations and
`/resume` run `talaria start`. systemd's own `Restart=on-failure` still restarts
Hermes after a crash, through the guard.

**`talaria start`** (takes the op lock, unless called from inside an operation
that already holds it):

1. Remove labelled one-shot containers (§4.5); delete stale `staging/` dirs;
   remove a stale `history/.git/index.lock` under `history.lock`.
2. **The one recovery rule**: if the journal holds an operation — it was
   interrupted, because nothing else can hold the lock — **restore its revert
   point**: `restore_data(revert.backup)` (§9.2) with the original operation ID,
   set the pointer, and for adoption move the old Quadlet back. With no revert
   point recorded yet, nothing was changed; the old state simply starts again.
   Candidate `deploying` → `pending`. Clear the journal, send a message ("an
   interrupted <op> was reverted").
3. Re-apply the podman pointer tags from state; `systemctl --user reset-failed
   hermes.service`; `systemctl --user start hermes.service`.
4. Post-start checks (§7.5). At boot or on `/resume`, a failure sends a `failed`
   message and sets `halt`; inside an operation it is that operation's failure.

**Guard** (`ExecCondition=`; exit 1–254 skips the start without marking the unit
failed, exit 0 allows it). It runs on the host, parses no YAML, traps every error
into exit 1 (never 255, never a signal death), and blocks when:

- `state.json` is missing or unreadable;
- the journal holds an operation whose recorded holder is not alive (PID and
  process start time no longer match; the guard never touches the lock itself);
- `halt` is set;
- `config.yaml` is missing, unless `mode` is `fresh` (the first start seeds it);
- the versions are out of range. With `cfg`, `db` the on-disk values
  (`_config_version` by line match on the unique top-level key; `state.db` via
  `sqlite_version.py`), `cfg_min`, `db_min` the values recorded for `current` at
  its last successful start, and `cfg_max`, `db_max` the `current` image's
  limits: allowed is `cfg_min ≤ cfg ≤ cfg_max` and `db_min ≤ db ≤ db_max`.
  A value above the recorded minimum but within the image's limits (a held-back
  database advancing, `hermes doctor --fix` inside the container) is accepted,
  and the new value becomes the minimum. Nothing is recorded yet (fresh install)
  → only the upper limits apply.

Every block writes a `failed` message (at most one per reason per hour), so
Hermes is never silently down. A version mismatch is resolved only by
`/restore`; `halt` and interrupted operations by `/resume`.

**Unit settings** (`hermes.container` `[Service]`, always): `Restart=on-failure`,
`RestartSec=30`, `TimeoutStartSec=300` (covers `wait-tailscale`'s 120 s).
`wait-tailscale` (`ExecStartPre=`, only for `dashboard.bind = tailscale`) waits
for the Tailscale address; a timeout fails the start and systemd retries.

## 8. Telegram connector

### 8.1 Rules

- Python stdlib only.
- Operations are started with `systemd-run --user --collect
  --unit=talaria-op-<op>-<id> <home>/.local/bin/talaria <op> …` (absolute path).
  A bot restart never kills an operation. The bot replies `ack` at once and keeps
  polling; results come through the outbox.
- Fixed argument vectors, never a shell; arguments validated (`RELEASE_TAG`,
  backup IDs `^\d{8}T\d{6}Z-[a-z0-9-]{1,40}$`, `CONFIRM` literally).
- Only `TALARIA_TELEGRAM_USER_ID`, only private chats; everything else is logged
  and dropped without reply.
- `Restart=always`; backoff when Telegram is unreachable. The bot runs no
  recovery itself.

### 8.2 Startup resync

The bot persists the last processed `update_id` in `state.json`. On start it
fetches everything newer, **executes none of it**, confirms it, and — if anything
was skipped — sends one `reply`: "N commands sent while I was offline were
ignored; send them again if still wanted." Without a stored offset (first start)
it uses `offset=-1`.

### 8.3 Commands

| Command | Lock | Effect |
|---|---|---|
| `/status` | none | version, image age, container state, free disk, pending candidate, halt flag, Talaria update available |
| `/check` | op | run `check` |
| `/prepare <tag>` | op | prepare a specific release |
| `/approve <tag>` | op | deploy; must match the pending candidate |
| `/reject <tag>` | state | mark `rejected` |
| `/rollback` | none | describe what would be rolled back, the backup's age, the confirm command |
| `/rollback <tag> CONFIRM` | op | rollback; `<tag>` must be the last deployed tag |
| `/backups` | none | ID, label, age, size, image |
| `/restore <id>` | none | describe what would be restored and lost; the confirm command |
| `/restore <id> CONFIRM` | op | restore |
| `/resume` | op | clear `halt`; `talaria start` (which reverts an interrupted change first) |
| `/logs [n]` | none | last gateway log lines (default 50, max 200), known token patterns redacted |

`rollback` and `restore` preempt a running `prepare`; otherwise a busy op lock is
answered `refused`.

### 8.4 What goes into the outbox

Kinds: `candidate`; `deployed`, `rolled_back`, `restored`; `failed`;
`talaria_release` (once); `ack`, `refused`, `reply`; `delivery_failed` (§8.5).
Everything else is silent.

The candidate report: tag, digest, `_config_version` and `state.db` from → to
(held back noted), the migration step messages captured in the rehearsal (§7.4),
the semantic diff with changed values first, doctor status changes, and the
`/approve <tag>` / `/reject <tag>` commands.

### 8.5 The outbox

`talaria` never talks to Telegram. Each message is one JSON file in `outbox/`,
written under a temporary name and renamed: `id`, `seq`, `created`, `kind`,
`text` (written by Talaria), `untrusted` (optional text blocks from upstream or
agent-writable data: config values, migration step messages, doctor lines, log
lines), `commands`.

`telegram.py` checks the outbox between long-polls (≤ 30 s) and sends in `seq`
order:

- **Rendering**: HTML parse mode, every dynamic string through `html.escape`;
  `untrusted` blocks only inside `<pre>` (no command links, no URLs); `commands`
  as `<code>`.
- **Size**: split at line boundaries into numbered parts of ≤ 4096 characters;
  the file is deleted only after all parts are confirmed.
- **Errors**: `429` → wait `retry_after`; `5xx`/network → backoff. A permanent
  `4xx` moves the file to `outbox/failed/` and queues one `delivery_failed`
  notice; a `delivery_failed` notice that itself fails permanently is only logged.

## 9. Data safety

### 9.1 Backups

- `backups/<UTC-timestamp>-<label>.tar.gz` plus sidecar `.json`: `seq`, image
  reference, `_config_version`, `state.db` version, size, sha256. Ordering by
  `seq`, never wall-clock. Operations refer to backups by ID, never by label.
- Labels `pre-<tag>`, `pre-restore`, `adopt`, `manual`; labels may repeat.
- Hermes is stopped; SQLite files are archived with their `-wal`/`-shm`.
- `backup.exclude` (default `.cache .npm home/.cache home/.npm backups`):
  regenerable caches and Hermes's own `backups/` — **except `backups/config/`,
  which is always archived** (it holds the last-known-good config that belongs to
  this data, F11).
- Written to a temp name, verified (`gzip -t`, `tar -tzf`), `fsync`ed, renamed;
  sidecar last. No valid sidecar → not a backup.
- Each operation checks space in its first step, before Hermes is stopped.
- Retention: `backup.keep` newest (default 5) across labels, applied at the end
  of every operation that succeeded. Never pruned: the current rollback target,
  the `adopt` backup until the first successful deploy, and a journalled revert
  point.

### 9.2 Restore

`restore_data(<backup>, <op-id>)` — internal, used by rollback, revert and
restore. Takes no backup, prunes nothing. Working names carry both IDs:
`R = <data_dir>.restore-<op-id>-<backup-id>`, `O = <data_dir>.old-<op-id>-<backup-id>`.
It decides from what exists, so it is idempotent at every point:

| Found | Action |
|---|---|
| `R` without its complete marker | delete `R`; extract again |
| nothing of `R`/`O` | extract into `R` (GNU tar, new empty directory), verify against the sidecar sha256, write the marker |
| `R` with marker, `<data_dir>` present, no `O` | rename `<data_dir>` → `O`; then as next row |
| `R` with marker, `O` present, no `<data_dir>` | rename `R` → `<data_dir>`; remove the marker |
| no `R`, `O` present, `<data_dir>` present | swap done: move each `backup.exclude` path (not `backups/config/`) that is missing in `<data_dir>` over from `O`; done |

`O` directories of an operation are deleted when the operation finishes
successfully (or its revert does).

`talaria restore <id>` (`/restore <id> CONFIRM`), production-changing:

1. Take the op lock (preempting a `prepare`). Verify the archive; ensure the
   sidecar's image (re-pull `<image>@<digest>` and verify, or refuse for a missing
   local image). **Space**: floor + the `pre-restore` archive + two uncompressed
   copies (the restore, and a possible revert).
2. Stop Hermes. Backup `pre-restore`; record the revert point `{backup:
   pre-restore, pointer: current}`.
3. `restore_data(<id>)`.
4. Pointer: `current` ← the sidecar's image; the sidecar's versions become the
   recorded minimums.
5. `talaria start`. Pass: clear the journal, `restored` message. Fail: revert.

A restore makes the last deploy non-rollbackable; it is undone by restoring its
`pre-restore` backup.

### 9.3 Secret sweep

After every successful production start (deploy, rollback, restore, adopt) and in
`talaria start`: move `.env` copies — `.env.bak-*` in the data root and `.env.*`
in `backups/config/` (F10) — into `migrate-bak/` (700), regular files only; keep
the newest; delete the rest through `safefs.py`. **Config copies are never
touched**: `config.yaml.good.<ts>` is Hermes's fallback (F11), and hand-named
copies belong to the user.

### 9.4 History

A git repo at `history/` versioning `config.yaml` and `memories/*.md` (regular
files only; `*.lock` excluded). `talaria history` commits if anything changed:
daily from the timer, and before/after deploy, rollback and restore. Takes
`history.lock`. **Silent** on success; a failure is reported once per distinct
error. No remote. Never under git: the whole data dir, `.env`, caches, databases.

## 10. Dashboard

Always behind basic auth: `HERMES_DASHBOARD=1`,
`HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin` in the Quadlet, password from
`hermes.env`. The container sees these, not Talaria's `.env`.

- `loopback` (default): `PublishPort=127.0.0.1:<port>:9119`; reached via SSH
  tunnel.
- `tailscale`: `PublishPort=<tailscale-ip>:<port>:9119` (address from `tailscale
  ip -4` at setup or `reconfigure`); `wait-tailscale` (§7.10) covers the boot race
  with the system `tailscaled`. Offered only when Tailscale runs.

Never a public interface.

## 11. Configuration

`talaria.conf`: `key = value`, `#` comments, no sections or quoting, values
trimmed, a leading `~/` expands to the service user's home and nothing else
expands. Read by `lib/conf.sh` and Python. Changes that affect the Quadlet take
effect with `talaria reconfigure`.

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

`.env` (600): `TALARIA_TELEGRAM_TOKEN`, `TALARIA_TELEGRAM_USER_ID`. Setup never
generates or prints tokens.

## 12. Security model

Published as `docs/threat-model.md`.

### 12.1 Trust

| Party | Trust |
|---|---|
| The person holding the paired Telegram account | trusted |
| The operator account and anything running as it (incl. coding agents) | **fully trusted** (sudo rule); AGENTS.md forbids agents to deploy, but that is guidance. The rule can be removed after setup. |
| The Hermes agent, the data dir, anything it writes (incl. plugins, SQLite files) | untrusted |
| Upstream content: images, tags, migration output, doctor output | untrusted beyond digest and revision checks |
| Other Telegram senders | ignored |
| The Talaria repository at first install | **trust on first use**. The printed key fingerprint is compared with the README; this protects against a tampered mirror or a wrong clone URL, **not** against a takeover of the GitHub repository itself, which controls both the tag and the README. Publishing the fingerprint on a second, independent channel is optional and left to the maintainer. |

### 12.2 Rules

1. The agent cannot reach the updater: Talaria files live outside the mount; the
   container sees only the data dir and the dashboard password.
2. Hostile files: no symlink following, regular files only, restores into new
   directories, SQLite read-only with defensive settings (§4.4).
3. One commander, paired with a code only the person at the terminal knows.
4. No root after setup.
5. Upstream code never runs on the host; helpers run in containers with
   `--network=none` and report through a result file.
6. What is approved is what is applied: the real outcome is compared with the
   approved one (§7.3).
7. Untrusted text is inert in messages (§8.5); destructive commands need `CONFIRM`
   and name what they destroy.
8. Pinned, verified images; local images identified by image ID.
9. No `.env` copies in the mount (best effort on modern filesystems).
10. Signed Talaria releases (§15.3), trust on first use (§12.1).
11. Crash safety: only Talaria starts Hermes; an interrupted change is reverted
    (§7.10).

## 13. Failure modes

| Failure | Handling |
|---|---|
| git fetch / registry unreachable | transient; one message per distinct reason |
| Tag moved upstream | message once; tag never used |
| Version-like tag outside the release pattern | message once (§7.1) |
| Space short | refused before anything stops |
| Revision or uid mismatch | `failed` |
| Implausible `_config_version` | message |
| Rehearsal migration or `state.db` error | `failed`; production untouched |
| `state.db` held back | reported; accepted by the range rule |
| Real outcome differs from the approved one | revert; back to `pending` with a new report |
| Post-start check fails after deploy | rollback; tag `failed` |
| Crash, power loss, OOM mid-change | at next start: revert to the change's starting state; message; approve again |
| Crash or kill of a non-production operation | nothing to revert; lock released with the process |
| Hermes crashes on its own | systemd restarts it through the guard |
| Guard blocks a start | start skipped, no restart loop; message |
| Rollback fails | `halt`; stays stopped across reboots; message |
| Restore image unavailable | refused |
| Truncated backup | never counted |
| Telegram down | outbox queues; shell works |
| Telegram rejects a message permanently | `outbox/failed/`; one notice |
| Concurrent operations | op lock; rollback/restore preempt prepare |
| Orphaned one-shot container | removed by label before data is touched |
| Tailscale address late at boot | `wait-tailscale` up to 120 s; systemd retries every 30 s |
| History commit fails | reported once; stale `index.lock` removed at next start |

## 14. Testing

### 14.1 Unit and component tests

**bats** for `bin/talaria` and `lib/*.sh`, with `podman`, `systemctl`,
`systemd-run`, `git`, `sleep` and the Python helpers stubbed on `PATH`. At least:

- tags: `RELEASE_TAG` edge cases, ordering incl. local images by release date,
  git ∩ registry, the v2026.6.5 floor, moved tags, classification of every
  unseen tag, the silent first run, only the newest candidate prepared;
- candidate states: every transition in §7.8; transient reasons reported once;
- verification of official and local images; pointer derivation from state;
- space checks happen before any stop, with the per-operation formulas;
- the op lock: refused for bot commands, silent for the timer, preemption of
  prepare, a stale holder PID;
- backups: excludes with `backups/config/` kept, sidecar, `seq`, truncated
  archives, retention with protected backups;
- `restore_data`: interrupted after each row of its table, a re-run reaches the
  same end state; excluded paths survive; two different backups with the same
  operation ID do not collide;
- **the recovery rule**: an interruption injected at every step of deploy,
  rollback, restore and apply-adopt; `talaria start` restores the revert point
  (or, before one exists, just starts); candidate `deploying → pending`;
- deploy outcome check: same fingerprint continues; different fingerprint
  reverts and re-asks;
- guard: every blocking condition, the version range incl. held-back and
  in-place increases, missing `state.json`, the fresh first start, a duplicated
  `_config_version` key, errors trapped into exit 1;
- sweep: only `.env` copies, both locations, never symlinks, never
  `config.yaml.good.*`;
- history: no-op when unchanged; symlinks and `.lock` never staged; failure
  reported once;
- outbox: atomic files, `seq`, kinds, nothing for silent operations;
- setup: idempotence, `--plan`, the ownership table incl. the re-run after the
  root block, `talaria-boot.service` installed before any Hermes change, SELinux
  and data-dir refusals, adopt checks, the allow-list, every exit code;
  `reconfigure`.

**pytest** (unittest-style, driven by pytest for mutmut):

- `confdiff.py`: formatting-only → nothing; inert removals; changed values first;
  fingerprint stable under key order;
- `doctor.py` parsing and the status-change report;
- `migrate.py`, `dbversion.py`, `imageinfo.py`: the result-file contract, held
  back, below the floor — with **`_upstream.py` replaced by a fake** in unit
  tests (host Python cannot import Hermes); the real adapter is exercised only by
  the contract tests (§14.4);
- `safefs.py`: planted symlinks at every touched path;
- `state.py`: concurrent writers under the lock; a kill mid-write;
- `oplock.py`: the lock is released when the holder dies even while a child it
  started (a sleeping process standing in for conmon) lives on;
- `sqlite_copy.py` under a concurrent writer; `sqlite_version.py` with WAL,
  read-only and defensive settings;
- `outbox.py`;
- `telegram.py`: pinned ID; group chats; argument validation; describe-only
  without `CONFIRM`; startup resync executes nothing and counts correctly;
  `systemd-run` with the absolute path; delivery order, splitting, `429`,
  permanent `4xx`, no notice loop; escaping and `<pre>`; pairing (wrong code
  ignored, expiry, first correct sender wins).

**shellcheck** on all shell code. Python on 3.10, 3.12, 3.13.

### 14.2 Mutation testing

After code and tests are complete:

- **Python**: `mutmut` over `lib/py/`, `helpers/` (except `_upstream.py`, which is
  only a thin adapter covered by the contract tests) and `telegram.py`, via pytest.
- **Bash**: `tests/mutate.sh`, one mutation at a time on a copy of the sources.
  Operators: swap `-eq`/`-ne`, `-lt`/`-ge`, `-gt`/`-le`, `==`/`!=`; swap
  `&&`/`||`; delete a `!`; `return 1` → `return 0`, `exit N` → `exit 0`; delete
  one command line in a function body (log/echo lines excluded); numeric literal
  ±1. Each mutant runs only the bats files mapped to its module
  (`tests/mutate.map`); mutants run in parallel; results are cached by source and
  test hash.
- **Honest scoring**: mutants failing `bash -n` or Python compilation are invalid
  (excluded, counted); timeouts count as killed but are listed.
- **Threshold**: ≥ 90 % of valid mutants killed per language; every survivor is
  killed by a new test or justified as equivalent in `docs/mutation-report.md`
  (counts per module and operator, invalid and timeout counts, date, commit).
- Release gate (§15.4), not per push.

### 14.3 systemd integration tests

`tests/systemd/` runs on a real systemd user instance with rootless podman and a
tiny dummy image standing in for Hermes (a main process, an `/api/status`
endpoint, a `config.yaml` and a SQLite file with a version table). It exercises:

- **the boot path**: linger enabled, the user manager restarted with
  `talaria-boot.service` and the Talaria units enabled; Hermes comes up only
  through `talaria start --boot`;
- the guard as `ExecCondition` (a blocked start is skipped, no restart loop, no
  start-limit hit), `Restart=on-failure` after killing the main process,
  `reset-failed`;
- `systemd-run` operations surviving a bot restart; the absolute path;
- a SIGKILL of an operation at each step of deploy, followed by a boot-path start
  that reverts it; labelled container cleanup;
- a guard failure that is not exit 1–254 (to prove the trap).

**Feasibility first**: before this becomes a pull-request gate, a spike checks
that GitHub's Ubuntu 24.04 runners support linger, a restartable user manager and
rootless podman with `keep-id:uid=10000` (AppArmor user-namespace restrictions,
subuid ranges). If they do not, the suite runs on the release VM (§14.5) instead
and is a release gate only.

### 14.4 Upstream contract tests

`tests/contract/` runs against real Hermes images — **the oldest supported
(v2026.6.5) and the newest release** — and the Hermes repository, covering every
row F1–F19 through the real `_upstream.py` adapter: revision label and
local-image identification, uid, the skip variable, the migration result
contract, the `state.db` ladder in both source layouts incl. held-back results,
backup locations and names, doctor output shape, dashboard variables and
fail-closed behaviour, `auth_required`, s6 service names, tag shapes, image
Python, `hermes --version` format. Helpers run exactly as in production. A
changed fact fails the run. Weekly and before each release.

### 14.5 Integration test on a VM

Before each release, on a disposable VM: fresh setup to `DONE` (including
pairing); deploy an older release, then update via the bot; `/rollback <tag>
CONFIRM`; `/restore <id> CONFIRM`; a **hard power-off mid-deploy** and reboot —
the boot path must revert it and report. Also confirm that `/rollback` inside a
`<pre>` block is not tappable in the Telegram clients. Adoption is exercised on a
real install.

## 15. Publishing, CI/CD, dependencies

### 15.1 Repository

- Public GitHub repo, MIT licence; commits use the GitHub no-reply address.
- `main` protected: pull requests with CI green.
- README: what it is, opinionated; second paragraph: *"Setting this up with a
  coding agent? Point it at `AGENTS.md`."*; requirements (incl. systemd ≥ 255,
  SELinux); what it changes; the signing key fingerprint; threat-model link.
- Docs contain no deployment-specific values.

### 15.2 CI (`.github/workflows/ci.yml`)

Every push and pull request, `ubuntu-latest`: shellcheck, bats, pytest (3.10,
3.12, 3.13), a scan for real deployment values in docs and templates; on pull
requests also the systemd integration tests if the spike (§14.3) succeeded.
Actions pinned by commit SHA; top-level `permissions: contents: read`; tools from
`uv.lock` and the bats submodules.

### 15.3 Releases and self-update

Releases are git tags `vMAJOR.MINOR.PATCH` (semver, `0.x` until stable), signed
with an SSH key; `allowed_signers` ships in the repo, its fingerprint in the
README (and optionally a second channel, §12.1).

`talaria self-update <tag>` (shell, as the service user; a non-production
operation under the op lock): `install-release <tag>` — clone into
`releases/<tag>/` (a partial clone from an earlier interrupted attempt is deleted
first), `git verify-tag` against the **installed** release's `allowed_signers`
(key changes need a release signed by the old key), render units from the new
release — then switch `current` atomically and restart `talaria-telegram` so the
bot runs the new code. Running operations keep their own release directory.
Kept: `current` and the previous release.

### 15.4 Release workflow (`.github/workflows/release.yml`)

On a pushed tag: §15.2, the systemd tests (or the VM checklist item), the
mutation gate, the contract tests, the tag-signature check; then a GitHub release
with notes from the commits since the previous tag. No artefacts. The VM test
(§14.5) is a checklist item in the release pull request.

### 15.5 Scheduled (`.github/workflows/upstream.yml`)

Weekly: the contract tests against v2026.6.5 and the newest Hermes release. A
failure means upstream changed something Talaria relies on.

### 15.6 Dependencies

**Dependabot** is the only update mechanism: `github-actions` (the SHA-pinned
actions), `uv` (`pytest`, `mutmut`, `pyyaml`, `shellcheck-py`), `gitsubmodule`
(`bats-core`, `bats-support`, `bats-assert`). Weekly, one grouped pull request per
ecosystem, `cooldown: default-days: 14`.

The bats submodules track their upstream default branches; Dependabot follows
commits, not tags, and none of the three repos has a release branch. The cooldown
filters short-lived commits; CI runs the full suite on every update.

pixi is not used: Dependabot cannot read `pixi.toml`/`pixi.lock` (checked
2026-09-26), while it supports uv and submodules.

## 16. Decisions and deferred items

Decided: dedicated service user; loopback dashboard with login, Tailscale
optional; pull-only images; adoption through one template with a way back;
silent history repo; doctor report-only; migrations identified from the
rehearsal's own output; narrow, explicit database guarantee (§1.2); version
checks as ranges; only Talaria starts Hermes; one recovery rule (revert an
interrupted change); SELinux enforcing unsupported; systemd ≥ 255; mutation
testing as a release gate at 90 %; Dependabot for all development dependencies.

Deferred to v2: `--import-data`; building images; SELinux enforcing support;
completing interrupted changes instead of reverting them.

Cut: model-written summary; daily backups; completion smoke check; migration
extractor; doctor as a gate.
