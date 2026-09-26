# Talaria — Design

Date: 2026-09-26 (third revision, after two adversarial reviews)
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
- It **survives crashes and reboots** mid-operation.
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
  a controlled step, rehearsed on a copy first, and their versions are checked
  before every start. Hermes never starts with an image whose recorded versions
  do not match these two files (§7.10).
- **The whole data dir** is backed up before every change, and image and data
  are always rolled back together.

Not individually guaranteed: other SQLite stores (`kanban.db` and others) and
per-profile configs and databases migrate when Hermes opens them (F17). They are
covered by the full backup and the joint rollback, not by version checks.

### 1.3 Non-goals (v1)

- Multiple hosts, multiple Hermes instances per host.
- Building Hermes images (§6.4).
- Configuring Hermes itself (providers, models, personality).
- Tracking `main`, release candidates or canaries.
- Automatic deploys, automatic self-updates.
- Importing non-podman installs (Docker, Compose, pip/uv `~/.hermes`):
  planned for v2 as `--import-data`.
- Hosts with **SELinux in enforcing mode** (setup refuses; §5.2).
- A model-written release summary; gating on `hermes doctor`.
- Other approval channels. The connector is named `telegram.py` and messages go
  through a connector-neutral outbox (§8.5).

## 2. Assumptions and requirements

- Linux with **systemd ≥ 243** (user instance, linger, `ExecCondition=`) and
  **rootless podman ≥ 4.9** (`UserNS=keep-id:uid=…,gid=…` in Quadlet).
- `git` ≥ 2.34 (SSH tag verification), `python3` ≥ 3.10 (stdlib only),
  `gzip`, GNU `tar`, `flock`, `systemd-run`.
- SELinux absent, disabled or permissive.
- One Hermes per host, run by a **dedicated service user** (default `hermes`).
- amd64 or arm64.
- Hermes **≥ v2026.6.5** for the running version at adoption and for every
  candidate (first release with `HERMES_SKIP_CONFIG_MIGRATION` and the bundled
  basic-auth dashboard provider).
- Runtime code: bash + Python stdlib; all HTTP through `urllib`. Anything that
  parses YAML or imports Hermes code runs **inside a Hermes image**, never on the
  host.
- Development tools only (never on a deployed host): `pytest`, `mutmut`,
  `pyyaml`, `shellcheck-py` via `pyproject.toml` + `uv.lock`; `bats-core`,
  `bats-support`, `bats-assert` as git submodules under `tests/lib/`.

## 3. Facts about upstream this design relies on

Checked against the Hermes repository at `v2026.6.5`, `v2026.8.3` and
`v2026.9.24` on 2026-09-26. The contract tests (§14.4) re-verify every row
against **both the oldest supported (v2026.6.5) and the newest** release, weekly
and before each Talaria release.

| # | Fact | Where (tag:file) | Consequence |
|---|---|---|---|
| F1 | Official images `docker.io/nousresearch/hermes-agent:<git-tag>` per release, amd64+arm64 | registry | pull, don't build |
| F2 | Official images carry `org.opencontainers.image.revision` = commit of the git tag; locally built images may carry nothing (no label, no `/etc/hermes/image-provenance.json`, no `.hermes_build_sha`) | image config; 9.24:`Dockerfile:368-384` | verify official pulls; identify local images by image ID and `hermes --version` |
| F3 | Release tags look like `v2026.9.24`, `v2026.7.7.2`; the repo also carries `rc.N-v0.21.5`, `abandoned-rc.*`, `v0.21.4+canary.<ts>` (about daily) and ad-hoc tags | git tags | one tag pattern; examine every new tag (§7.1) |
| F4 | Hermes runs as uid/gid 10000; `stage2-hook.sh` remaps to `HERMES_UID`/`HERMES_GID` and chowns `/opt/data` | 9.24:`Dockerfile:168`, `stage2-hook.sh:40-105` | `keep-id:uid=10000,gid=10000` everywhere (§4.3) |
| F5 | On start, `stage2-hook.sh` runs `docker_config_migrate.py` and **swallows its failure** | 8.3:`stage2-hook.sh:453-454` | a failed start migration still yields a running container |
| F6 | `HERMES_SKIP_CONFIG_MIGRATION=1` makes that script exit 0 at once | 6.5:`docker_config_migrate.py:43-45`; 8.3:`:58-60` | Talaria disables start-time config migration and runs it itself |
| F7 | From v2026.8.3 the migration self-restores on error or if the version does not advance; v2026.6.5 does not | 8.3:`docker_config_migrate.py` | Talaria asserts the result itself, never relies on self-restore |
| F8 | Config migrations are a forward-only ladder; no `current > latest` guard; from v2026.8.x a support floor | `hermes_cli/config*.py` | old binary on newer config reports healthy |
| F9 | `state.db` has its own schema ladder, run whenever a `SessionDB` is opened (not gated by F6). `SCHEMA_VERSION` is 25 at v2026.8.3, 30 at v2026.9.24. The stored version may be **held back** below `SCHEMA_VERSION` (FTS migrations incomplete or FTS5 unavailable) and advance on a later open | 8.3:`hermes_state_common.py:155`; 9.24:`hermes_state_common.py:239`, `hermes_state_schema.py:992,1101` | rehearse; accept "held back" as a result (§7.2) |
| F10 | Migration writes plaintext `.env` copies: `.env.bak-<ts>` in the data root (v2026.8.3); `backups/config/.env.pre-docker-migrate.<ts>` (v2026.9.24). Upstream's legacy sweep does not move `.env.bak-*` | 8.3:`docker_config_migrate.py`; 9.24:`config_backups.py:22,29,55-62` | Talaria sweeps `.env` copies (§9.3) |
| F11 | `backups/config/config.yaml.good.<ts>` is Hermes's last-known-good config; `load_config()` falls back to it (or defaults) instead of failing on broken YAML | 9.24:`config_backups.py:72-79`, `config.py:2173ff` | never delete config copies; parse YAML with `yaml.safe_load`, never trust `load_config()` as a check |
| F12 | `hermes doctor`: labels embed values and differ between pass and fail (`check_bool(cond, ok, bad)`), checks come and go between releases, exit code differs by version, and it loads plugins from the data dir | 9.24:`doctor_report.py:23-28`, `doctor_config.py:327-328`, `doctor.py:188`, `doctor_tools.py:236` | doctor output is **report-only**, untrusted text |
| F13 | The dashboard only starts if `HERMES_DASHBOARD` is truthy; on a non-loopback bind it fails closed without an auth provider; `HERMES_DASHBOARD_INSECURE` is ignored since v2026.7.1 | 9.24:`s6-rc.d/dashboard/run:9-19` | template sets `HERMES_DASHBOARD=1` + basic auth |
| F14 | Basic auth: `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` + `_PASSWORD`; first in v2026.6.5 | `plugins/dashboard_auth/basic` | zero-infrastructure login |
| F15 | Image Python is 3.13 | 9.24:`Dockerfile:43` | helpers target 3.13 |
| F16 | s6 services are `dashboard`, `main-hermes` (`exec sleep infinity`) and `user`; **the gateway is the container's main command**, not an s6 service | 9.24:`docker/s6-rc.d/` | health = container main process, not s6 |
| F17 | Other SQLite stores (`kanban.db`, `response_store.db`, …) alter their schema on open; profiles (`profiles/<name>/config.yaml`, their `state.db`) are reconciled at container start (`cont-init.d/02-reconcile-profiles`), not by `docker_config_migrate.py` | 9.24 | §1.2: covered by backup + joint rollback |
| F18 | `hermes --version` prints `Hermes Agent v<pkg> (<release date>)`, e.g. `v0.16.0 (2026.6.5)` | deployed image | version floor for local images |

Observed, not documented: a Telegram `getUpdates` without an offset returned
nothing while one update was pending; `offset=-1` returned it (2026-08-26).

## 4. Architecture

```
 talaria-recover.service  (oneshot, Before=hermes.service, at every boot)
          │ talaria recover --boot
          ▼
 hermes.service  (Quadlet hermes.container)
   ExecCondition = talaria guard          skip start (not fail) on unsafe state
   ExecStartPre  = talaria wait-tailscale (only for dashboard.bind = tailscale)
   mounts only <data_dir> → /opt/data

 talaria-updater.timer → talaria-updater.service → talaria history; talaria check

 talaria-telegram.service (telegram.py)   long-poll; only process talking to Telegram
   └─ systemd-run --user --unit=talaria-op-<op>-<id> <abs path>/talaria <op>

 bin/talaria (bash): the only thing that changes anything. Every operation holds
 the op lock for its whole run, journals each phase in state.json, and writes
 messages into outbox/.
```

### 4.1 Repository layout

```
README.md            second paragraph points coding agents to AGENTS.md
AGENTS.md            the agent runbook (§5.4)
LICENSE              MIT
bin/talaria          entry point, argument parsing, dispatch
lib/*.sh             conf, state, lock, podman, images, backup, restore_data,
                     restore, rehearse, deploy, rollback, adopt, recover, guard,
                     history, sweep, outbox, setup, install_release
lib/py/              host-side stdlib helpers (importable, main()): state.py
                     (locked atomic journal), outbox.py, sqlite_copy.py,
                     sqlite_version.py (read-only), safefs.py
helpers/             run INSIDE a Hermes image, bind-mounted read-only:
                     migrate.py, dbversion.py, confdiff.py, doctor.py
telegram.py          the Telegram connector
templates/           hermes.container, talaria-recover.service,
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
| `~/.local/bin/talaria` | symlink to `current/bin/talaria`; units always use this absolute path |
| `~/.config/talaria/talaria.conf` | settings (§11) |
| `~/.config/talaria/.env` | Talaria secrets, mode 600 |
| `~/.config/talaria/hermes.env` | `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD` only, mode 600 |
| `~/.config/containers/systemd/hermes.container` | Quadlet |
| `~/.config/systemd/user/talaria-*` | Talaria units |
| `~/.local/state/talaria/` | `state.json`, `state.lock`, `op.lock`, `history.lock`, `backups/`, `images/`, `history/` (git), `staging/`, `migrate-bak/`, `outbox/`, `outbox/failed/`, `logs/` |
| `~/.local/state/talaria/hermes.git` | bare clone of the Hermes repo (tags only); never built or executed |
| `<data_dir>` (default `~/hermes-data`) | Hermes data, mounted at `/opt/data`; must not be a mount point itself (§9.2) |
| `/etc/talaria/owner` | written by the root block: the service user's name |

All directories under `~/.local/state/talaria/` are mode 700.

### 4.3 User namespace

The Quadlet and every one-shot container use `keep-id:uid=10000,gid=10000`
(`--userns=keep-id:uid=10000,gid=10000 --user 10000:10000` for one-shots) and
`HERMES_UID=10000 HERMES_GID=10000`. The host service user is uid 10000 inside,
so all files in `<data_dir>` belong to the service user on the host and Talaria's
host-side operations need neither `podman unshare` nor root. The in-image uid is
checked at pull time (§6.2).

### 4.4 Filesystem safety

The data dir is writable by the agent; every host-side operation treats its
contents as hostile:

- act on **regular files only** (`lstat`, `O_NOFOLLOW`, resolved path must stay
  inside the data dir);
- copies never dereference symlinks (skipped in history; kept as symlinks in
  staging and tar);
- restores extract with GNU tar into a **new empty directory** (§9.2);
- secret deletion through `safefs.py` (overwrite, then unlink, on a regular file
  opened `O_NOFOLLOW`); best effort on copy-on-write or journaling filesystems,
  and the threat model says so.

### 4.5 One-shot containers

Every one-shot container gets `--name talaria-<op>-<id>-<step>` and
`--label talaria.op=<id>`. Rootless podman runs containers in their own scope,
so killing an operation does not kill its containers: **preemption and
`recover` first run `podman rm -f` on every container labelled `talaria.op`**
before touching data.

## 5. Setup

### 5.1 Ownership and the root block

`setup` runs as the operator (the admin's login account) and checks `getent
passwd <user>` and `/etc/talaria/owner`:

| User exists | Listed in owner file | Result |
|---|---|---|
| no | — | root block: create user, subuid/subgid, linger, operator sudo rule, owner file |
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
unsupported install type, SELinux enforcing, data dir is a mount point);
1 internal error.

`setup` (operator) orchestrates; everything that runs as the service user is a
separate subcommand, `install-release`, also used by self-update (§15.3).

Steps:

1. **Prerequisites.** Report missing tools with hints; never install.
   `getenforce` reporting `Enforcing` → `STOP` (13).
2. **Ownership** (§5.1).
3. **Install Talaria**: the operator's checkout must be at a release tag (else
   `STOP`, unless `--dev`). `install-release <tag>` (as the service user):
   clone into `releases/<tag>/`, verify the tag signature (§15.3) and that its
   commit equals the operator's checkout, switch `current`. Setup prints the
   signing key fingerprint and tells the person to compare it with the one
   published on the project's GitHub page (trust on first use).
4. **Detect** Hermes containers and Quadlets of the service user: none →
   **fresh**; exactly one → **adopt**; several → `FOUND` each, `STOP` (12),
   choose with `--adopt <unit>`. Docker/Compose/non-container → `STOP` (13).
5. **Data dir**: must exist or be creatable, must not be a mount point (its
   `st_dev` equals its parent's), else `STOP` (13).
6. **Adopt checks** (read-only; §5.3).
7. **`--apply-adopt`** (§5.3).
8. **Image.** Fresh: newest release (§7.1), pulled and verified. Adopt: the
   running image is recorded (§6.2).
9. **Dashboard password** generated into `hermes.env`; the file is named, the
   password never printed.
10. **Telegram pairing**:
    1. `ACTION REQUIRED`: create a bot via @BotFather; put the token into
       `~/.config/talaria/.env` as `TALARIA_TELEGRAM_TOKEN`.
    2. Setup prints a one-time 8-character code **in the terminal**.
       `ACTION REQUIRED`: send `/pair <code>` to the bot from your Telegram
       account.
    3. The first sender with the right code, in a private chat, becomes
       `TALARIA_TELEGRAM_USER_ID`; setup prints that account's name and
       @username. Messages without the code are ignored. The code expires
       after 15 minutes.
    Only the person at the terminal knows the code; someone who merely finds the
    bot cannot pair. Setup talks to Telegram via `telegram.py --pair`.
11. **State and units.** Write `state.json` with `mode: fresh` (or the adopted
    versions), render and install units, `daemon-reload`, enable, start, run
    the post-start checks (§7.5); on a fresh install record the versions the
    first start produced. Summary, `DONE`.

### 5.3 Adoption

**Checks** (read-only, during plain `setup`). Refuse (13) if:

- the running image is older than v2026.6.5 (by revision ancestry for official
  images, by the `hermes --version` release date for local ones, F18);
- there is more than one mount, or the mount is not a directory at `/opt/data`,
  or anything else (sockets, `$HOME`) is mounted.

Environment carries over from an allow-list of Hermes variables;
`HERMES_DASHBOARD_INSECURE` and unknown variables are dropped and listed. Setup
renders the Talaria Quadlet and prints the diff. Nothing changes.

**`--apply-adopt`** (explicit; it restarts the live agent). An operation like any
other: it holds the op lock and journals its phases (§7.10).

| Phase | Step |
|---|---|
| A1 | stop the old unit |
| A2 | backup labelled `adopt`, taken with `podman unshare tar --numeric-owner`, so the original ownership (whatever the old unit used) is preserved exactly |
| A3 | if files are not owned by the service user: `podman unshare chown -R 0:0 <data_dir>` |
| A4 | keep the old unit as `<name>.talaria-orig` (disabled); install `hermes.container` and the Talaria units |
| A5 | start; post-start checks |
| A6 | record versions; done |

On failure after A1: stop, restore the `adopt` backup with `podman unshare tar
--numeric-owner` (original ownership back), reinstate and start the old unit,
report. Adoption never leaves the person without their old setup.

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
6. Tell the user to compare the printed signing key fingerprint with the one on
   the project's GitHub page.
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

- **Official images**: "digest" is the platform image digest podman reports
  after the pull. `images/<tag>.json` records digest, revision, in-image uid,
  the image's latest config version and `SCHEMA_VERSION` (read once by a
  helper), pull time. After every pull: revision label = `git rev-list -n1
  <tag>`; uid = 10000. Mismatch refuses the candidate. Re-pulls use
  `<image>@<digest>` and must yield the same digest.
- **Local images** (adopted, non-official): recorded as `local:<image-id>` with
  `hermes --version` output and the same version fields. They can never be
  re-pulled.

**Pointers live in `state.json`** (`current`, `previous`: image references with
digest or image ID). Podman tags `localhost/hermes-agent:current`/`:previous`
are derived from state and re-applied by any operation or `recover`; a pointer
switch is one state write.

### 6.3 Retention

Keep the images of `current`, `previous`, the pending candidate, and every image
referenced by a retained backup. Remove other Talaria-pulled images after a
deploy, a rejection, a supersession and a permanent failure.

### 6.4 Building (not in v1)

Building from source was proven on podman 4.9 with BuildKit inside the service
user's rootless podman; `docs/buildkit.md` keeps the recipe.

## 7. Update flow

### 7.1 Release tags and `talaria check`

```
RELEASE_TAG = ^v(20\d\d)\.(\d{1,2})\.(\d{1,2})(\.(\d+))?$
```

ordered by (year, month, day, suffix or 0); used for candidates and bot argument
validation alike.

`check` (daily, from the timer):

1. `git fetch --tags --force`. A tag Talaria already pulled that now points
   elsewhere → one `failed` message; that tag is never used again.
2. `podman search --list-tags --limit 1000 <image>`; exactly 1000 results → one
   warning that the list may be truncated.
3. **Every tag created since the last check** is classified: release
   (`RELEASE_TAG`), known non-release (`rc.*`, `abandoned-rc.*`, `*+canary.*`),
   or other. A *version-like* other tag (`^v\d+\.\d+\.\d+$`, e.g. `v0.21.5`) →
   one `failed` message: "upstream tag format may have changed". Non-version
   ad-hoc tags are ignored.
4. Candidates: release tags in both lists, ≥ v2026.6.5, newer than deployed, not
   `rejected` or `failed` (§7.8).
5. Talaria self-update: `git ls-remote --tags <talaria_repo>`; a newer release not
   yet reported → one `talaria_release` message.
6. A new candidate → `prepare` it.

`talaria prepare <tag>` / `/prepare <tag>` prepares a specific release on request.

### 7.2 `talaria prepare <tag>`

Production is not touched. Holds the op lock; **preemptible** by `rollback` and
`restore` (they stop its unit, remove its containers (§4.5), delete its staging).

1. **Space**: free ≥ `disk.floor_gb` + image size + staging size. Else transient
   failure.
2. **Pull and verify** (§6.2).
3. **Plausibility**: parse the on-disk `_config_version` (helper in the current
   image). Greater than both the current image's and the candidate's latest →
   `failed` message ("config claims schema N, no known image supports it"). Greater
   than only the candidate's → refuse as a downgrade.
4. **Staging copy** `staging/<id>/` (700): data dir minus `backup.exclude`, no
   symlink following, SQLite databases through `sqlite_copy.py` (stdlib backup
   API; production is running). Stale staging dirs are deleted first.
5. **Migrate the copy**: one-shot migration (§7.4) with the candidate.
6. **Migrate the copy's `state.db`**: `helpers/dbversion.py --migrate` opens it
   with the candidate's `SessionDB` and reports `{version, target}`:
   - `version == target` → ok;
   - `version < target` but the open succeeded → **held back** (F9): recorded as
     the expected value, shown in the report, not a failure;
   - an exception → permanent failure.
7. **Doctor** (report-only, F12): current image on the unmigrated copy, then the
   candidate on the migrated copy, both `--network=none`; the report lists
   checks whose status changed.
8. **Semantic config diff**, original → migrated (§7.7).
9. Delete the copy (secrets through `safefs.py`).
10. **Pending**: record the candidate with predicted versions, the semantic
    diff, fingerprints (§7.3), digest; write the `candidate` message (§8.4).

Failures are transient or permanent (§7.8). Production is untouched either way.

### 7.3 `talaria deploy <tag>`

Pending candidate only; holds the op lock; journals every phase before it starts
(§7.10).

| Phase | Step |
|---|---|
| D0 | **space check** for the backup (§9.1); short → refused, nothing stopped |
| D1 | stop Hermes |
| D2 | **fingerprint check**: `confdiff.py` fingerprints the parsed `config.yaml` and the key set + values of `.env`. Changed since prepare → start Hermes, re-run the rehearsal steps of prepare; if the new semantic diff and predicted versions are identical to the approved ones, continue at D3; if not, the candidate goes back to `pending` with a new report and deploy ends |
| D3 | backup `pre-<tag>` (§9.1); history commit |
| D4 | real migration: one-shot migration (§7.4) on the data dir, then `dbversion.py --migrate`; results must equal the prediction (held back included). Failure → restore (§9.2, internal) and start the old image |
| D5 | pointer switch: `previous` ← `current`, `current` ← candidate (one state write) |
| D6 | start; post-start checks (§7.5) |
| D7 | finalize: secret sweep (§9.3), history commit, image retention (§6.3), `deployed` message |

Post-start failure → rollback (§7.9); the tag becomes `failed`.

The fingerprint step makes the person approve what is applied, without letting a
config that changes for harmless reasons (a `/model` switch) block updates: only
a different *outcome* asks again.

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
`docker_config_migrate.py` `main()` in-process, captures its step messages
(e.g. `✓ Turned off verify-on-stop`), parses the result with `yaml.safe_load`,
and writes one JSON object to a result file in a Talaria-owned directory mounted
at `/opt/talaria-out` (not stdout, which plugins or upstream code could
pollute): exit code, `from`, `to`, the image's latest, the support floor where
the image has one, the captured step messages, error text. Talaria asserts on the
JSON only. Below the support floor → permanent failure with that explanation.

All helpers (`migrate.py`, `dbversion.py`, `confdiff.py`, `doctor.py`) use this
same invocation shape and result-file convention; `<result-dir>` is a fresh
directory under `staging/` owned by the service user.

The captured step messages are how the report says **which migrations ran**:
they come from the actual run, not from parsing upstream source.

### 7.5 Post-start checks

All must pass:

1. `hermes.service` active for 60 s; its systemd `NRestarts` and the container's
   `StartedAt` unchanged across that time.
2. The container's main process (`podman top`: `hermes gateway run`) has the same
   PID in every sample (every 5 s).
3. The dashboard `/api/status` answers 200 and reports `auth_required: true`.
4. On-disk `_config_version` and `state.db` version equal the expected values
   (`sqlite_version.py`, read-only).

### 7.6 Doctor

Report-only (F12). Output is untrusted text, shown inside `<pre>` (§8.5).

### 7.7 Semantic config diff

`helpers/confdiff.py` (in the candidate image) compares parsed structures:
`_config_version` from → to, **changed values first**, added keys, removed keys
(**inert** if equal to the candidate's default). Formatting-only changes produce
no diff. It also computes the fingerprints used in §7.3.

### 7.8 Candidate states

```
            ┌──────────── transient failure (retried next check) ─────┐
  new ──► preparing ──► pending ──► deploying ──► deployed            │
              │            │  │         │                             │
              │            │  │         └──► failed (auto-rolled back)│
              │            │  └──► superseded (a newer tag prepared)  │
              │            └──► rejected (/reject)                    │
              └──► failed (permanent) ◄───────────────────────────────┘
```

- **Transient** (network, registry, space, lock busy): eligible again at the next
  check; the same reason is reported once.
- **Permanent** (revision/uid mismatch, migration failure, `state.db` error,
  downgrade, support floor): `failed`; retried only by `/prepare <tag>`.
- A rolled-back deploy → `failed`.
- `/reject` → `rejected`.
- **One pending candidate**, reported once; a newer successful prepare replaces it
  (`superseded`) and its report says so.

### 7.9 Rollback

Undoes **the last deploy** only: available while the last production-changing
operation was a deploy whose `pre-<tag>` backup and `previous` image exist;
otherwise refused, pointing to `/restore`.

| Phase | Step |
|---|---|
| R1 | stop Hermes |
| R2 | `restore_data(pre-<tag>)` (§9.2): no new backup, no pruning |
| R3 | pointer switch: `current` ← `previous` |
| R4 | start; post-start checks against the recorded pre-deploy versions |
| R5 | `rolled_back` message |

A rollback **discards everything the agent wrote since the backup**; the bot
requires `/rollback <tag> CONFIRM` and states the backup's age first (§8.3).

### 7.10 Journal, guard and recovery

**State.** `state.json` is changed only through `lib/py/state.py`, which takes
`state.lock` (`flock`) for every read-modify-write and writes atomically (temp
file, `fsync`, rename, directory `fsync`). The outbox `seq` counter is allocated
under the same lock.

**Liveness.** An operation holds `op.lock` for its whole run, whether started
from the bot, the timer or a shell. "An operation is in progress" means: the
journal names one **and** `op.lock` is held (`flock -n` fails). A journal entry
without a held lock is an **interrupted** operation.

**Journal entry**: `{op, id, phase, start_allowed, started}`. An operation sets
`start_allowed` only immediately before it starts Hermes itself.

**Guard** (`ExecCondition=`; exit 1–254 **skips** the start without marking the
unit failed, so there is no restart loop; exit 0 allows it). It runs on the
host, reads no YAML, and blocks when:

- `state.json` is missing or unreadable (message; setup always writes it first);
- an operation is interrupted (journal entry, lock free);
- an operation is in progress and `start_allowed` is false;
- `halt` is set;
- `config.yaml` is missing, unless `mode` is `fresh` (the first start seeds it);
- the on-disk `_config_version` (line match on the top-level key) or `state.db`
  version (`sqlite_version.py`) differs from the values recorded for `current`,
  with one exception: a higher value that the `current` image itself supports
  (e.g. `hermes doctor --fix` inside the container) is accepted, recorded, and
  reported once.

Every block writes a `failed` message (at most one per reason per hour), so
Hermes is never silently down.

**`wait-tailscale`** (`ExecStartPre=`, only with `dashboard.bind = tailscale`):
waits up to 120 s for the Tailscale address; the template sets
`TimeoutStartSec=300` so the wait is not cut short, `Restart=on-failure`,
`RestartSec=30`.

**Recovery.** `talaria recover` runs at every boot from `talaria-recover.service`
(`Type=oneshot`, `Before=hermes.service`, `WantedBy=default.target`), when
`talaria-telegram` starts, and on demand. It takes `op.lock`, removes labelled
one-shot containers (§4.5), deletes stale staging dirs, re-journals itself as the
owner of the interrupted operation, and acts by phase:

| Operation | Interrupted in / after | Action |
|---|---|---|
| deploy | D0–D1 | start (nothing changed) |
| deploy | D2 (re-rehearsal) | start |
| deploy | D3 (backup incomplete) | delete the temp backup; start |
| deploy | D4 (migrating) | `restore_data(pre-<tag>)`; start the old image |
| deploy | D5 | the pointer write is atomic: if `current` is still old → as D4; if new → as D6 |
| deploy | D6 | start if stopped; post-start checks; pass → D7; fail → rollback |
| deploy | D7 | re-run finalize (idempotent) |
| rollback | R1–R2 | re-run `restore_data` (idempotent, §9.2); continue R3–R5 |
| rollback | R3–R4 | start; checks; fail → `halt` |
| restore | before its data swap | discard the partial extraction; start (nothing changed) |
| restore | after its data swap | continue: pointer, start, checks; fail → `halt` |
| apply-adopt | A1–A3 | restore the `adopt` backup (numeric owners), reinstate the old unit, start it |
| apply-adopt | A4–A5 | checks; pass → A6; fail → as A1–A3 |

Before starting Hermes, `recover` sets `start_allowed` and runs `systemctl --user
reset-failed hermes.service`. Every recovery ends with a message. If recovery
fails, `halt` is set and Hermes stays stopped until `/resume`.

## 8. Telegram connector

### 8.1 Rules

- Python stdlib only.
- Operations are started with `systemd-run --user --collect
  --unit=talaria-op-<op>-<id> <home>/.local/bin/talaria <op> …` (absolute path;
  transient units do not read the shell's `PATH`). A bot restart never kills an
  operation. The bot replies `ack` at once and keeps polling; results come through
  the outbox.
- Fixed argument vectors, never a shell; arguments validated (`RELEASE_TAG`,
  backup IDs `^\d{8}T\d{6}Z-[a-z0-9-]{1,40}$`, `CONFIRM` literally).
- Only `TALARIA_TELEGRAM_USER_ID`, only private chats; everything else is logged
  and dropped without reply.
- `Restart=always`; backoff when Telegram is unreachable.

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
| `/rollback` | none | describe: what would be rolled back, the backup's age, the confirm command |
| `/rollback <tag> CONFIRM` | op | rollback; `<tag>` must be the last deployed tag |
| `/backups` | none | ID, label, age, size, image |
| `/restore <id>` | none | describe what would be restored and lost; the confirm command |
| `/restore <id> CONFIRM` | op | restore |
| `/resume` | op | clear `halt`, `reset-failed`, start if the guard passes |
| `/logs [n]` | none | last gateway log lines (default 50, max 200), known token patterns redacted |

`rollback` and `restore` preempt a running `prepare`; otherwise a busy `op.lock`
is answered `refused`. The timer's `check` hitting a busy lock is silent (logged).

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
  notice; a `delivery_failed` notice that itself fails permanently is only logged,
  never re-notified.

## 9. Data safety

### 9.1 Backups

- `backups/<UTC-timestamp>-<label>.tar.gz` plus sidecar `.json`: `seq`, image
  reference, `_config_version`, `state.db` version, size, sha256. Ordering by
  `seq`, never wall-clock.
- Labels `pre-<tag>`, `pre-restore`, `adopt`, `manual`; labels may repeat.
- Hermes is stopped; SQLite files are archived with their `-wal`/`-shm`.
- `backup.exclude` (default `.cache .npm home/.cache home/.npm backups`):
  regenerable caches and Hermes's own `backups/`.
- Written to a temp name, verified (`gzip -t`, `tar -tzf`), `fsync`ed, renamed;
  sidecar last. No valid sidecar → not a backup.
- **Space check before Hermes is stopped** (each operation's first phase): free ≥
  `disk.floor_gb` + uncompressed size to archive.
- Retention: `backup.keep` newest (default 5) across labels, applied only when no
  operation is running. Never pruned: the current rollback target, the `adopt`
  backup until the first successful deploy, and any backup an operation is using.

### 9.2 Restore

`restore_data(<backup>)` — internal, used by rollback, deploy failure, recovery
and restore. Takes no backup, prunes nothing, and is idempotent in every state of
its directories (names carry the operation ID):

1. No `<data_dir>.restore-<id>/` with a complete marker → extract (GNU tar) into
   it, verify against the sidecar sha256, write the marker.
2. Marker present and `<data_dir>` present → rename `<data_dir>` to
   `<data_dir>.old-<id>`.
3. Rename `<data_dir>.restore-<id>` to `<data_dir>`; remove the marker.
4. Move the `backup.exclude` paths from `<data_dir>.old-<id>` into `<data_dir>`
   (caches and Hermes's own backups survive a restore).
5. `<data_dir>.old-<id>` is deleted only after the operation's post-start checks
   pass.

`talaria restore <id>` (`/restore <id> CONFIRM`):

| Phase | Step |
|---|---|
| S0 | verify the archive; space check (one uncompressed copy + floor); ensure the sidecar's image (re-pull `<image>@<digest>` and verify, or refuse for a missing local image) |
| S1 | stop Hermes |
| S2 | backup `pre-restore` |
| S3 | `restore_data` |
| S4 | pointer: `current` ← the sidecar's image |
| S5 | start; post-start checks against the sidecar's versions |
| S6 | `restored` message |

A restore makes the last deploy non-rollbackable; it is undone by restoring its
`pre-restore` backup.

### 9.3 Secret sweep

After every production start (deploy, rollback, restore, adopt) and in `recover`:
move `.env` copies — `.env.bak-*` in the data root and `.env.*` in
`backups/config/` (F10) — into `migrate-bak/` (700), regular files only; keep the
newest; delete the rest through `safefs.py`. **Config copies are never touched**:
`backups/config/config.yaml.good.<ts>` is Hermes's last-known-good fallback (F11),
and hand-named copies belong to the user.

### 9.4 History

A git repo at `history/` versioning `config.yaml` and `memories/*.md` (regular
files only; `*.lock` excluded). `talaria history` commits if anything changed:
daily from the timer, and before/after deploy, rollback and restore. Takes
`history.lock`. **Silent**; no remote. Never under git: the whole data dir,
`.env`, caches, databases.

## 10. Dashboard

Always behind basic auth: `HERMES_DASHBOARD=1`,
`HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin` in the Quadlet, password from
`hermes.env`. The container sees these, not Talaria's `.env`.

- `loopback` (default): `PublishPort=127.0.0.1:<port>:9119`; reached via SSH
  tunnel.
- `tailscale`: `PublishPort=<tailscale-ip>:<port>:9119` (address from `tailscale
  ip -4` at setup); `wait-tailscale` (§7.10) covers the boot race with the system
  `tailscaled`. Offered only when Tailscale runs at setup.

Never a public interface.

## 11. Configuration

`talaria.conf`: `key = value`, `#` comments, no sections or quoting, values
trimmed, a leading `~/` expands to the service user's home and nothing else
expands. Read by `lib/conf.sh` and Python.

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
| The Hermes agent, the data dir, anything it writes (incl. plugins) | untrusted |
| Upstream content: images, tags, migration output, doctor output | untrusted beyond digest and revision checks |
| Other Telegram senders | ignored |
| The Talaria repo at first install | trust on first use; the person compares the key fingerprint with the GitHub page |

### 12.2 Rules

1. The agent cannot reach the updater: Talaria files live outside the mount; the
   container sees only the data dir and the dashboard password.
2. Hostile files: no symlink following, regular files only, restores into new
   directories (§4.4).
3. One commander, paired with a code only the person at the terminal knows.
4. No root after setup.
5. Upstream code never runs on the host; helpers run in containers with
   `--network=none` and report through a result file, not stdout.
6. What is approved is what is applied (§7.3).
7. Untrusted text is inert in messages (§8.5); destructive commands need
   `CONFIRM` and name what they destroy.
8. Pinned, verified images; local images identified by image ID.
9. No `.env` copies in the mount (best effort on modern filesystems).
10. Signed Talaria releases (§15.3).
11. Crash safety: locked journal, guard, boot-time recovery (§7.10).

## 13. Failure modes

| Failure | Handling |
|---|---|
| git fetch / registry unreachable | transient; one message per distinct reason |
| Tag moved upstream | message once; tag never used |
| Version-like tag outside the release pattern | message once (§7.1) |
| Space short (prepare, deploy, restore) | refused before anything stops |
| Revision or uid mismatch | `failed` |
| Implausible `_config_version` | message |
| Rehearsal migration or `state.db` error | `failed`; production untouched |
| `state.db` held back | recorded, reported, not a failure |
| Config changed between prepare and approve | re-rehearse; ask again only if the outcome differs |
| Real migration fails | `restore_data`, old image started, message |
| Post-start check fails | rollback; tag `failed` |
| Crash, power loss, bot restart mid-operation | guard blocks start; boot-time `recover` finishes or reverts |
| Guard blocks a start | start skipped (no restart loop); message |
| Rollback or recovery fails | `halt`; stays stopped across reboots; message |
| Restore image unavailable | refused |
| Truncated backup | never counted |
| Telegram down | outbox queues; shell works |
| Telegram rejects a message permanently | `outbox/failed/`; one notice |
| Concurrent operations | `op.lock`; rollback/restore preempt prepare |
| Orphaned one-shot container | removed by label before data is touched |
| Tailscale address late at boot | `wait-tailscale` up to 120 s, then systemd retries every 30 s |

## 14. Testing

### 14.1 Unit and component tests

**bats** for `bin/talaria` and `lib/*.sh`, with `podman`, `systemctl`,
`systemd-run`, `git`, `sleep` and the Python helpers stubbed on `PATH`. At least:

- tags: `RELEASE_TAG` edge cases, ordering, git ∩ registry, the v2026.6.5 floor,
  moved tags, classification of every new tag (canary/rc ignored, version-like
  alert, ad-hoc ignored);
- candidate states: every transition in §7.8; transient reasons reported once;
- verification of official and local images; pointer derivation from state;
- space checks happen before any stop;
- `op.lock`: contention from bot, timer and shell; preemption; silent timer miss;
- backups: excludes, sidecar, `seq`, truncated archives, retention with protected
  backups and running operations;
- `restore_data`: interrupted after each of its steps, re-run reaches the same end
  state; excluded paths survive;
- deploy D2: unchanged outcome continues, changed outcome re-asks;
- **recovery**: a fault injected after **every** phase in §7.3, §7.9, §9.2 and
  §5.3; the guard blocks; `recover` yields the table's outcome; shell-started
  operations behave like unit-started ones;
- guard: every blocking condition, the accepted in-place upgrade, missing
  `state.json`, the fresh first start;
- sweep: only `.env` copies, both locations, never symlinks, never
  `config.yaml.good.*`;
- history: no-op when unchanged; symlinks and `.lock` never staged;
- outbox: atomic files, `seq`, kinds, nothing for silent operations;
- setup: idempotence, `--plan`, the ownership table incl. the re-run after the
  root block, SELinux and mount-point refusals, adopt checks, the allow-list,
  every exit code.

**pytest** (unittest-style, driven by pytest for mutmut): `confdiff.py`
(formatting-only → nothing; inert removals; changed values first; fingerprints),
`doctor.py` (parsing; status-change report), `migrate.py` and `dbversion.py`
(result-file contract; held back; below floor), `safefs.py` (planted symlinks at
every touched path), `state.py` (concurrent writers under the lock; kill
mid-write), `sqlite_copy.py` (copy under a concurrent writer),
`sqlite_version.py`, `outbox.py`, `telegram.py` (pinned ID; group chats; argument
validation; describe-only without `CONFIRM`; startup resync executes nothing and
counts correctly; `systemd-run` with absolute path; delivery order, splitting,
`429`, permanent `4xx`, no notice loop; escaping and `<pre>`; pairing: wrong code
ignored, expiry, first correct sender wins).

**shellcheck** on all shell code. Python on 3.10, 3.12, 3.13.

### 14.2 Mutation testing

After code and tests are complete:

- **Python**: `mutmut` over `lib/py/`, `helpers/`, `telegram.py`, via pytest.
- **Bash**: `tests/mutate.sh`, one mutation at a time on a copy of the sources.
  Operators: swap `-eq`/`-ne`, `-lt`/`-ge`, `-gt`/`-le`, `==`/`!=`; swap
  `&&`/`||`; delete a `!`; `return 1` → `return 0`, `exit N` → `exit 0`; delete
  one command line in a function body (log/echo lines excluded); numeric literal
  ±1. Each mutant runs only the bats files mapped to its module
  (`tests/mutate.map`), mutants run in parallel, and results are cached by source
  and test hash.
- **Honest scoring**: mutants failing `bash -n` or Python compilation are invalid
  (excluded, counted); timeouts count as killed but are listed.
- **Threshold**: ≥ 90 % of valid mutants killed per language; every survivor is
  killed by a new test or justified as equivalent in `docs/mutation-report.md`
  (counts per module and operator, invalid and timeout counts, date, commit).
- Release gate (§15.4), not per push.

### 14.3 systemd integration tests

`tests/systemd/` runs on a real systemd user instance (GitHub runners provide
systemd and podman) with a tiny dummy image standing in for Hermes (a main
process, an `/api/status` endpoint, a `config.yaml` and a SQLite file with a
version table). It exercises: the Quadlet with `ExecCondition` guard (a blocked
start is skipped, no restart loop, no start-limit hit), `reset-failed`,
`talaria-recover.service` ordering before `hermes.service`, `systemd-run`
operations surviving a bot restart, the absolute-path invocation, labelled
container cleanup, and a crash (SIGKILL of the operation) at each deploy phase
followed by `recover`. Runs on every pull request.

### 14.4 Upstream contract tests

`tests/contract/` runs against real Hermes images — **the oldest supported
(v2026.6.5) and the newest release** — and the Hermes repository, covering every
row F1–F18: revision label and local-image identification, uid, the skip
variable, the migration result contract, the `state.db` ladder including
held-back results, backup locations and names, doctor output shape, dashboard
variables and fail-closed behaviour, s6 service names, tag shapes, image Python,
`hermes --version` format. Helpers run exactly as in production. A changed fact
fails the run. Weekly and before each release.

### 14.5 Integration test on a VM

Before each release, on a disposable VM: fresh setup to `DONE` (including
pairing); deploy an older release, then update via the bot; `/rollback <tag>
CONFIRM`; `/restore <id> CONFIRM`; a **hard power-off mid-deploy** and reboot —
the guard blocks, boot recovery reverts. Also: confirm that `/rollback` inside a
`<pre>` block is not tappable in the Telegram clients. Adoption is exercised on a
real install.

## 15. Publishing, CI/CD, dependencies

### 15.1 Repository

- Public GitHub repo, MIT licence; commits use the GitHub no-reply address.
- `main` protected: pull requests with CI green.
- README: what it is, opinionated; second paragraph: *"Setting this up with a
  coding agent? Point it at `AGENTS.md`."*; requirements (incl. SELinux);
  what it changes; the signing key fingerprint; threat-model link.
- Docs contain no deployment-specific values.

### 15.2 CI (`.github/workflows/ci.yml`)

Every push and pull request, `ubuntu-latest`: shellcheck, bats, pytest (3.10,
3.12, 3.13), a scan for real deployment values in docs and templates; on pull
requests also the systemd integration tests (§14.3). Actions pinned by commit
SHA; top-level `permissions: contents: read`; tools from `uv.lock` and the bats
submodules.

### 15.3 Releases and self-update

Releases are git tags `vMAJOR.MINOR.PATCH` (semver, `0.x` until stable), signed
with an SSH key; `allowed_signers` ships in the repo, its fingerprint in the
README.

`talaria self-update <tag>` (shell, as the service user): take `op.lock`;
`install-release <tag>` (clone into `releases/<tag>/`, `git verify-tag` against
the **installed** release's `allowed_signers` — key changes need a release signed
by the old key — then render units from the new release); switch `current`
atomically; restart `talaria-telegram` so the bot runs the new code. Running
operations keep their own release directory. Kept: `current` and the previous
release.

### 15.4 Release workflow (`.github/workflows/release.yml`)

On a pushed tag: §15.2, the systemd tests, the mutation gate, the contract tests,
the tag-signature check; then a GitHub release with notes from the commits since
the previous tag. No artefacts. The VM test (§14.5) is a checklist item in the
release pull request.

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
rehearsal's own output; narrow, explicit database guarantee (§1.2); SELinux
enforcing unsupported in v1; mutation testing as a release gate at 90 %;
Dependabot for all development dependencies.

Deferred to v2: `--import-data`; building images; SELinux enforcing support.

Cut: model-written summary; daily backups; completion smoke check; migration
extractor; doctor as a gate.
