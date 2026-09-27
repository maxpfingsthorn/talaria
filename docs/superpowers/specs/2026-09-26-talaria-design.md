# Talaria — Design (v1)

Date: 2026-09-27 (fifth revision: cut to a v1)
Status: draft, pending review

> **Talaria** — safe, approved updates for self-hosted
> [Hermes Agent](https://github.com/NousResearch/hermes-agent) on rootless podman.
> Opinionated.

## 1. Purpose

Hermes Agent ships frequent releases. Updating a self-hosted instance means
`pull && restart`, and the container migrates its data on start. The container
rarely fails to start. The usual failure is worse: it starts, **migrates the
data**, and then misbehaves. The previous version can no longer safely read that
data, and nothing notices.

Talaria makes updates boring:

- It finds new releases and pulls them, pinned by digest. It **rehearses the
  migration on a throwaway copy** of the data and reports what would change.
- It **waits for a person to approve** on Telegram.
- It backs up, deploys and checks the result. On failure it **rolls back image
  and data together**. The person can trigger the same rollback at any time.
- Setup is one idempotent command that a **coding agent** can drive. The person
  only does the steps that need a person.

**Design stance: 80/20.** v1 covers the normal path and fails loudly on the rest.
Rare cases are documented, not engineered away. §15 lists what v1 leaves out.

### 1.1 Goals

- Adopt an existing rootless-podman Hermes install, or install Hermes fresh.
- Deploy only on explicit approval.
- Low noise: messages only for decisions and failures.
- Everything host-specific is configuration.
- As much verification as possible runs in CI, so there is little manual work.

### 1.2 Non-goals (v1)

- Multiple hosts, or more than one Hermes per host.
- Building images.
- Configuring Hermes itself.
- `main`, release candidates and canaries.
- Automatic deploys and automatic self-updates.
- Non-podman installs.
- SELinux in enforcing mode.

## 2. Requirements

- Linux with systemd user units and linger. Tested on Ubuntu 24.04 (systemd 255).
- Rootless podman ≥ 4.9 and Quadlet.
- `git`, `python3` ≥ 3.10 (stdlib only), GNU `tar`, `gzip`, `flock`.
- amd64 or arm64.
- Hermes ≥ v2026.6.5, the first release with `HERMES_SKIP_CONFIG_MIGRATION` and
  dashboard basic auth. Setup refuses older installs.
- A dedicated service user (default `hermes`).

**Code.** Talaria is a stdlib Python package behind a thin `bin/talaria`
wrapper. Nothing that parses Hermes's YAML or imports Hermes code runs on the
host. That code runs inside a Hermes image.

Python was chosen over bash and Rust:
- The helpers must be Python anyway, because they import Hermes inside its
  image. One language keeps one test and mutation setup.
- Installing is a `git clone` with no build step and no per-architecture
  binaries. Stdlib only means no dependencies on the host.
- Rust would add a release build pipeline and a toolchain for contributors,
  and would still need Python for the helpers. Its benefits, speed and a single
  static binary, don't matter for a tool that runs once a day.

## 3. Facts about upstream

Checked on 2026-09-26 against v2026.6.5, v2026.8.3 and v2026.9.24. The weekly
contract test (§13.3) re-checks the ones marked ✓.

| # | Fact | Consequence |
|---|---|---|
| F1 ✓ | Official images are published as `docker.io/nousresearch/hermes-agent:<git-tag>`. `podman search --list-tags` lists them unauthenticated. | Pull, don't build. No Docker Hub API. |
| F2 ✓ | Official images carry `org.opencontainers.image.revision`, which equals the tag's commit. | Verify after pulling. The commit comes from `git ls-remote`. |
| F3 | Release tags look like `v2026.9.24` or `v2026.7.7.2`. The repo also has rc, canary and ad-hoc tags. | One tag pattern (§6.1). |
| F4 ✓ | Hermes runs as uid 10000. | `UserNS=keep-id:uid=10000,gid=10000`, so data files belong to the service user on the host. |
| F5 | At start, `stage2-hook.sh` runs the config migration and **swallows its failure**. | Talaria disables that step and runs the migration itself (F6). |
| F6 ✓ | `HERMES_SKIP_CONFIG_MIGRATION=1` skips the start-time config migration. | Set in the Quadlet. |
| F7 | `state.db` migrates whenever it is opened, independently of F6. | The rehearsal opens it once. Production migrates it at start. |
| F8 | `load_config()` falls back to `config.yaml.good.*` or defaults instead of failing. | A healthy-looking container can have lost its config, which is why the migration result is checked explicitly. |
| F9 | `hermes doctor` output varies by version. | Report-only, untrusted text. |
| F10 ✓ | The dashboard needs `HERMES_DASHBOARD=1`. On a non-loopback bind it refuses to start without auth. `HERMES_DASHBOARD_INSECURE` has been ignored since v2026.7.1. Basic-auth variables exist since v2026.6.5. | The dashboard always uses basic auth. |
| F11 ✓ | `/api/status` reports `auth_required`. | Used by the post-start check. |

## 4. Architecture

```
hermes.service        Quadlet hermes.container, WantedBy=default.target
  ExecCondition = test ! -e ~/.local/state/talaria/changing
  ExecStartPre  = talaria wait-tailscale        (only for dashboard.bind = tailscale)
  mounts only <data_dir> → /opt/data

talaria-check.timer → talaria-check.service → talaria check (daily)

talaria-telegram.service   telegram.py, long-poll; the only process that reads Telegram
  └─ systemd-run --user --collect ~/.local/bin/talaria <op>   (a bot restart never kills an op)
```

All operations that change anything run under a single `flock -n` on
`~/.local/state/talaria/lock`. When the lock is busy, the operation is refused
with "busy, try again". A timer run that finds the lock busy is skipped
silently.

### 4.1 Repository layout

```
README.md          what it is; second paragraph points coding agents to AGENTS.md
AGENTS.md          the agent runbook (§5.3)
bin/talaria        thin wrapper → python3 -m talaria
talaria/           cli, conf, state, tags, images, backup, rehearse, deploy,
                   rollback, setup, adopt, history, notify, telegram
helpers/           run INSIDE a Hermes image (read-only mount): migrate.py,
                   confdiff.py, dbopen.py, doctor.py; _upstream.py is the only
                   module that imports Hermes code
templates/         hermes.container, talaria-check.{service,timer},
                   talaria-telegram.service
tests/             pytest, e2e/ (systemd + podman, §13.2), contract/ (§13.3)
pyproject.toml     dev tools only (uv): pytest, mutmut, pyyaml; uv.lock committed
```

### 4.2 On-host layout (service user)

| Path | Content |
|---|---|
| `~/.local/share/talaria/` | git checkout of Talaria at a release tag |
| `~/.local/bin/talaria` | symlink to its `bin/talaria` |
| `~/.config/talaria/talaria.conf` | settings (§10) |
| `~/.config/talaria/.env` | Telegram token and user ID, mode 600 |
| `~/.config/talaria/hermes.env` | dashboard password, mode 600 |
| `~/.config/containers/systemd/hermes.container` | Quadlet |
| `~/.local/state/talaria/` | `state.json`, `lock`, `changing`, `backups/`, `staging/`, `history/`, mode 700 |
| `<data_dir>` (default `~/hermes-data`) | Hermes data, mounted at `/opt/data` |

`state.json` is written atomically (temp file, `fsync`, rename) and only by the
lock holder. The one exception is the bot, which appends to the `rejected` list
under the same lock.

## 5. Setup

### 5.1 `talaria setup`

```
talaria setup [--plan] [--user NAME] [--adopt UNIT]
```

Setup is run by the admin's login account (the **operator**). It is idempotent:
each step is checked, done if possible, or ends the run with an action line.
Re-running continues where it stopped.

Output lines are `OK:`, `MISSING:`, `ACTION REQUIRED:`, `FOUND:`, `STOP:` and
`DONE`. Exit codes: 0 means done, 10 means a person must act, and 1 means stop
or error.

1. **Prerequisites.** Report missing tools, with package names for
   Debian/Ubuntu, Fedora and Arch. Never install anything. SELinux enforcing →
   `STOP`.
2. **Service user.** If the user is missing, print **one** root block: create a
   regular user, add subuid/subgid, enable linger, and add the rule
   `<operator> ALL=(<user>) NOPASSWD: ALL`. If the user already exists, report
   `FOUND`; AGENTS.md makes the agent confirm it with the person. Nothing after
   the root block needs root.
3. **Install Talaria** as the service user: clone the repo into
   `~/.local/share/talaria` at the same tag as the operator's checkout, and
   create the symlink.
4. **Detect Hermes.** None → **fresh**. Exactly one container or Quadlet of the
   service user → **adopt**. Several → `STOP`; the person chooses with
   `--adopt UNIT`.
5. **Image.** Fresh: pull the newest release (§6). Adopt: record the running
   image. Refuse anything older than v2026.6.5.
6. **Dashboard password.** Generate it into `hermes.env`; name the file, never
   print the password. If Tailscale is running, report that `dashboard.bind =
   tailscale` is available.
7. **Telegram pairing.**
   - `ACTION REQUIRED`: create a bot with @BotFather and put its token into
     `~/.config/talaria/.env` as `TALARIA_TELEGRAM_TOKEN`.
   - Setup prints an 8-character code in the terminal. `ACTION REQUIRED`: send
     `/pair <code>` to the bot.
   - The first private-chat sender with the right code, within 15 minutes,
     becomes `TALARIA_TELEGRAM_USER_ID`. Setup prints that account's name so the
     person can confirm it. Only the person at the terminal knows the code.
8. **Adopt** (§5.2), only when adopting and only with an explicit `--adopt
   UNIT`.
9. **Units.** Render the templates, `daemon-reload`, enable the timer and the
   bot, start Hermes, and run the post-start check (§7.4). Then `DONE`.

Changing `talaria.conf` (for example `dashboard.bind`) takes effect when setup
is run again. That re-renders the Quadlet and restarts Hermes if it changed.

### 5.2 Adoption

Checks come first and change nothing. Setup refuses when:
- the install has anything other than a single directory mounted at
  `/opt/data`;
- the image is older than v2026.6.5.

It then prints the old Quadlet next to the new one as a diff. Environment
variables carry over from an allow-list; `HERMES_DASHBOARD_INSECURE` and unknown
variables are dropped and listed.

Applying the adoption restarts the agent:
1. Stop the old unit.
2. Back up the data with `podman unshare tar --numeric-owner`, labelled
   `adopt`, so the original ownership is kept exactly.
3. If the files are not owned by the service user, run `podman unshare chown -R
   0:0 <data_dir>`.
4. Move the old Quadlet aside to `<name>.talaria-orig`.
5. Install the new one and start it with the post-start check.

On failure, Talaria stops and prints the manual way back: restore the `adopt`
backup and move the old Quadlet back. This is a one-time, attended step, so
printed instructions are enough.

**Same bot token.** Two gateways that use the same agent bot token steal each
other's messages. Setup refuses when another Hermes container of the service
user is running. For anything else, AGENTS.md makes the agent ask.

### 5.3 `AGENTS.md`

1. Read this file fully first.
2. Clone the repo at the latest release tag and run `bin/talaria setup --plan`.
   Explain the plan in plain words.
3. Confirm the service user name with the person before handing over the root
   block.
4. `MISSING`: work out the install command for this distribution and ask before
   running it.
5. `ACTION REQUIRED`: relay it in plain words. Never ask for secrets in the chat.
   Show the pairing code; the person sends it to the bot.
6. Re-run `talaria setup` until `DONE`.
7. `FOUND` / `STOP`: explain and let the person choose.
8. Before adopting, show the diff, say that it restarts the agent, and get an
   explicit yes.
9. Never run `deploy`, `rollback` or `restore`. Those are the person's
   decisions, made in Telegram.

## 6. Images and releases

### 6.1 Release tags

```
RELEASE_TAG = ^v(20\d\d)\.(\d{1,2})\.(\d{1,2})(\.(\d+))?$
```

Tags are ordered by (year, month, day, suffix or 0). The same pattern validates
bot arguments.

### 6.2 Pinning and verification

- Pull `<image>:<tag>` and record the digest podman reports. The revision label
  must equal the tag's commit from `git ls-remote --tags`. A mismatch fails the
  candidate.
- Podman tags `localhost/hermes-agent:current` and `:previous` point at the
  deployed and the prior image. The Quadlet uses `Image=localhost/hermes-agent:current`
  and `Pull=never`. `state.json` records the digest behind each tag.
- An adopted local image is recorded by its image ID and cannot be re-pulled.
- **Retention**: keep `current`, `previous`, the pending candidate and the
  images of retained backups. Remove any other image Talaria pulled.

## 7. Update flow

### 7.1 `talaria check` (daily)

1. Get release tags from `git ls-remote --tags <hermes_repo>` and from
   `podman search --list-tags <image>`, and take the tags present in both.
2. The candidate is the newest such tag that is newer than `current` and not in
   `rejected` or `failed`.
3. If it is new, **rehearse** it (§7.2).
4. **Talaria reminder**: `git ls-remote --tags <talaria_repo>`. The first time a
   newer Talaria release appears, send one message. `/status` keeps showing it.

The first `check` after setup only records what exists, without messages. An
unreachable network or registry fails silently; if it fails 3 days in a row,
send one message.

### 7.2 Rehearsal (`talaria rehearse <tag>`)

This does not touch production.

1. **Space.** Free space must be at least `disk.floor_gb` + image size + the
   size of the data dir.
2. **Pull and verify** (§6.2).
3. **Copy** the data dir to `staging/<id>/`, preserving symlinks. SQLite
   databases are copied with Python's `sqlite3` backup API, because production
   is running.
4. **Migrate the copy**: run the one-shot migration (§7.3) with the candidate.
5. **Open `state.db`**: run `dbopen.py` in the candidate on the copy. It opens
   the database the way Hermes does, which migrates it, and reports the version
   before and after. An exception fails the candidate.
6. **Doctor** (report-only): run the candidate's `hermes doctor` on the copy
   with `--network=none`, and keep its output.
7. **Config diff**: `confdiff.py` compares the original and the migrated config
   as parsed structures and lists changed values first, then added and removed
   keys. Formatting-only changes produce no diff.
8. Delete the copy.
9. The candidate becomes **pending** and one message goes out (§8.3).

A failure in steps 2–7 marks the tag `failed` and sends one message. A space
or network failure is retried at the next check and reported once.

A newer successful rehearsal replaces the pending candidate. Its message says
which candidate it replaced.

### 7.3 The one-shot migration

```
podman run --rm --network=none \
  --userns=keep-id:uid=10000,gid=10000 --user 10000:10000 \
  -v <dir>:/opt/data -v <talaria>/helpers:/opt/talaria:ro -v <out>:/opt/talaria-out \
  -e HERMES_HOME=/opt/data -e HOME=/opt/data \
  -w /opt/hermes --entrypoint /opt/hermes/.venv/bin/python \
  <image> /opt/talaria/migrate.py
```

`migrate.py` runs upstream's config migration in-process and captures its step
messages, which is how the report says **which migrations ran**. It parses the
result with `yaml.safe_load` and writes one JSON object to
`/opt/talaria-out/result.json`. The object holds the exit status, the config
version before and after, the step messages and any error text. Talaria only
ever reads that JSON, never stdout. Deploy runs the same migration on the real
data.

### 7.4 Post-start check

Run after every start that Talaria does:

1. `hermes.service` stays active for 60 s, with `NRestarts` unchanged.
2. `/api/status` answers 200 with `auth_required: true`.

### 7.5 `talaria deploy <tag>` (from `/approve <tag>`)

This works on the pending candidate only.

1. **Space.** Free space must be at least floor + backup + one uncompressed copy
   of the data dir. If not, refuse before stopping anything.
2. Stop Hermes, then back up as `pre-<tag>` (§9.1) and commit the history
   (§9.3).
3. Write the **`changing` marker**: operation, backup ID, and the previous image
   digest. From now on, Hermes will not start on its own (§7.7).
4. **Real migration**: the one-shot migration (§7.3) on the data dir. A failure,
   or a config version other than the rehearsal predicted, triggers an
   automatic rollback (§7.6).
5. Retag: `previous` ← `current`, `current` ← the candidate.
6. Start Hermes and run the post-start check (§7.4).
7. **Pass**: remove the marker, apply retention, and send "deployed vX".
   **Fail**: roll back automatically, mark the tag `failed`, and send one
   message.

### 7.6 Rollback (`/rollback CONFIRM`)

Rollback puts back the data and the image from before the last change.

- **Target**: if the `changing` marker exists, the backup and image recorded in
  it. Otherwise the last deploy's `pre-<tag>` backup and the `previous` image.
  If neither exists, the rollback is refused.
- **Steps**:
  1. Check space for one uncompressed copy.
  2. Stop Hermes.
  3. Write or keep the marker.
  4. Restore (§9.2).
  5. Retag `current`.
  6. Start and run the post-start check.
  7. Pass: remove the marker and send "rolled back".
  8. Fail: leave the marker, so Hermes stays down, and send one message with the
     manual steps.
- **Idempotent**: every step can be run again, so after a crash the answer is
  always to send `/rollback CONFIRM` again.
- **Warning**: plain `/rollback` explains what would be restored and **how old
  the backup is**. Everything the agent wrote since then is lost.

### 7.7 Interrupted changes

The `changing` marker exists only while production is being changed. While it
exists, `ExecCondition` stops systemd from starting Hermes, at boot or
otherwise, so a half-migrated data dir never runs.

When the bot starts, and on `/status`, it reports any marker it finds:
"interrupted <op> (<age>); send `/rollback CONFIRM` to restore the state before
it". Talaria never completes an interrupted deploy. The person rolls back and
approves again.

A crash before the marker is written (during the backup) leaves the data
untouched, and Hermes starts normally. The unfinished archive has no sidecar,
so it is not a backup (§9.1).

### 7.8 Candidates

- **pending**: at most one, reported once.
- **rejected**: set by `/reject <tag>`. Never offered again.
- **failed**: rehearsal failure, or a deploy that was rolled back. Retry with
  `talaria rehearse <tag>` on the command line.

## 8. Telegram

### 8.1 Rules

- Stdlib only.
- Only `TALARIA_TELEGRAM_USER_ID`, only in private chats. Everything else is
  dropped without a reply.
- Arguments are validated (`RELEASE_TAG`, backup IDs, the literal `CONFIRM`)
  and passed as fixed argument vectors, never through a shell.
- Operations start through `systemd-run`, the bot replies "started", and the
  operation sends its result itself.
- Commands sent while the bot was offline are dropped at startup (`offset=-1`),
  not executed.

### 8.2 Commands

| Command | Effect |
|---|---|
| `/status` | Hermes version and state, free disk, pending candidate, interrupted change, Talaria update available |
| `/check` | run `check` now |
| `/approve <tag>` | deploy the pending candidate |
| `/reject <tag>` | mark it `rejected` |
| `/rollback` | describe what would be rolled back, and the backup's age |
| `/rollback CONFIRM` | roll back (§7.6) |
| `/backups` | ID, label, age, size, image |
| `/restore <id>` | describe what would be restored and lost |
| `/restore <id> CONFIRM` | restore (§9.2) |

### 8.3 Messages

`talaria/notify.py` sends messages directly with `urllib`. It retries 3 times
with backoff, honours `429 retry_after`, and otherwise logs to the journal.
Messages longer than 4096 characters are truncated, and the full text goes to
the journal.

Messages use HTML parse mode, with every dynamic string passed through
`html.escape`. Untrusted text (config values, migration step messages, doctor
output) appears only inside `<pre>`.

Talaria sends a message for:
- a candidate report;
- deployed, rolled back, restored;
- failures;
- an interrupted change;
- a new Talaria release, once.

It sends nothing else.

**The candidate report** contains:
- tag, release notes link, digest;
- config version before → after;
- `state.db` version before → after;
- the migration step messages;
- the config diff;
- doctor lines that differ from the current image's run;
- `/approve <tag>` · `/reject <tag>`.

## 9. Data safety

### 9.1 Backups

- A backup is `backups/<UTC-timestamp>-<label>.tar.gz` plus a sidecar `.json`
  with the image digest, config version and sha256. Labels are `pre-<tag>`,
  `pre-restore`, `adopt` and `manual`. Labels can repeat; the ID is the file
  name.
- Hermes is stopped during a backup.
- `backup.exclude` (default `.cache .npm home/.cache home/.npm backups`) skips
  regenerable caches and Hermes's own `backups/`, except `backups/config/`,
  which is always archived because it holds the last-known-good config (F8).
- A backup is written under a temporary name, verified with `tar -tzf`, then
  renamed; the sidecar is written last. An archive without a sidecar is not a
  backup.
- Retention keeps the `backup.keep` newest backups (default 5). It never prunes
  the current rollback target, a backup named in the marker, or the `adopt`
  backup before the first successful deploy.
- `talaria backup` makes a `manual` backup from the command line.

### 9.2 Restore

The shared restore step, used by rollback and restore:
1. Extract the archive into a new directory `<data_dir>.restore-<id>` and verify
   it against the sidecar.
2. Rename `<data_dir>` to `<data_dir>.old-<id>`.
3. Rename the restored directory to `<data_dir>`.
4. Move the excluded paths back from `.old-<id>`.

Each step checks what already exists first, so a re-run finishes the job.
`.old-<id>` is deleted after a successful start.

`/restore <id> CONFIRM`:
1. Re-pull the backup's image by digest if it is missing. A missing local image
   refuses the restore.
2. Take a `pre-restore` backup and write the marker.
3. Restore, and set `current` to the backup's image.
4. Start and run the post-start check.

A restore can be undone by restoring its `pre-restore` backup.

### 9.3 History

`history/` is a git repo that versions `config.yaml` and `memories/*.md`
(regular files only). `talaria history` commits if anything changed. It runs
daily from the timer and before every deploy. It is silent: a failure is
reported once. It has no remote.

## 10. Configuration

`talaria.conf` uses `key = value` lines and `#` comments. A leading `~/`
expands; nothing else does.

| Key | Default |
|---|---|
| `data_dir` | `~/hermes-data` |
| `image` | `docker.io/nousresearch/hermes-agent` |
| `hermes_repo` | `https://github.com/NousResearch/hermes-agent` |
| `talaria_repo` | this project's GitHub URL |
| `dashboard.bind` | `loopback` or `tailscale` |
| `dashboard.port` | `9119` |
| `backup.keep` | `5` |
| `backup.exclude` | `.cache .npm home/.cache home/.npm backups` |
| `disk.floor_gb` | `6` |
| `check.time` | `04:30` |

`.env` holds `TALARIA_TELEGRAM_TOKEN` and `TALARIA_TELEGRAM_USER_ID`. Setup
never generates or prints tokens.

## 11. Dashboard

The dashboard always requires a login: `HERMES_DASHBOARD=1`, the username
`admin` set in the Quadlet, and the password from `hermes.env`. The container
sees only these values, never Talaria's `.env`.

- `loopback` (default): `PublishPort=127.0.0.1:<port>:9119`, reached through an
  SSH tunnel.
- `tailscale`: `PublishPort=<tailscale-ip>:<port>:9119`. The address comes from
  `tailscale ip -4` when setup runs. `wait-tailscale` (`ExecStartPre`) waits up
  to 120 s for that address at boot. If it times out, `Restart=on-failure`
  retries.

The dashboard is never on a public interface.

## 12. Security

This section is published as a README section.

| Party | Trust |
|---|---|
| The paired Telegram account | trusted |
| The operator account and its coding agents | fully trusted, through the sudo rule. The README shows how to remove the rule after setup. |
| The Hermes agent, the data dir, and everything in it | untrusted |
| Upstream images and their output | untrusted beyond digest and revision checks |
| Talaria's own repo | trusted as cloned. Install from the canonical URL at a tag. |

Rules:

1. The agent cannot reach the updater. Talaria's files are outside the mount;
   the container sees only the data dir and the dashboard password.
2. Upstream code never runs on the host. Helpers run in containers with
   `--network=none`.
3. There is one commander, paired with a code that only the person at the
   terminal sees.
4. Untrusted text is inert in messages. Destructive commands need `CONFIRM` and
   say what they destroy.
5. Images are pinned by digest and their revision is verified.
6. Restores extract into new directories; host code never follows symlinks from
   the data dir.
7. No root after setup.

**Documented limitations:**
- Starting another image by hand on migrated data is unsupported; change images
  only through Talaria.
- `.env` backup copies that Hermes writes stay in the data dir and in backups.
- A rollback loses everything written since its backup.

## 13. Testing (all automated in CI)

### 13.1 Unit tests

**pytest**, with `podman`, `systemctl`, `git` and the Telegram API faked. The
tests cover:
- tags: pattern, ordering, git ∩ registry, the floor;
- candidate states;
- space checks before any stop;
- backups: excludes, sidecar, retention and its protections;
- restore: interrupted after each step, then re-run;
- deploy and rollback: every failure path triggers the right rollback, and
  marker handling is correct;
- `check`: silent first run, the reminder sent once;
- setup: idempotence, `--plan`, every exit code, the adopt checks and the
  allow-list;
- the bot: pinned ID, group chats, argument validation, describe-only without
  `CONFIRM`, the offline backlog dropped, pairing (wrong code, expiry, first
  correct sender wins), escaping;
- helpers (`migrate.py`, `confdiff.py`, `dbopen.py`, `doctor.py`), with
  `_upstream.py` replaced by a fake.

**shellcheck** on `bin/talaria` and `wait-tailscale`. Python 3.10 and 3.13.

### 13.2 End-to-end tests (`tests/e2e/`)

These run on a GitHub runner with a real systemd user instance and rootless
podman. A small dummy image stands in for Hermes: a main process, `/api/status`,
a config file and a SQLite file, with tags `v2026.1.1` and `v2026.1.2` in a
local registry. They exercise:
- fresh setup through to `DONE`, with the Telegram API faked;
- check, rehearsal, and approve through to deployed;
- a deploy whose post-start check fails, which rolls back automatically;
- `/rollback CONFIRM`;
- `/restore <id> CONFIRM`;
- a SIGKILL of deploy after the marker is written: Hermes stays down after a
  user-manager restart, and `/rollback CONFIRM` recovers it;
- adoption of a pre-existing dummy Quadlet;
- `systemd-run` operations surviving a bot restart.

**The first implementation task is a spike** to confirm that GitHub's Ubuntu
24.04 runners support this: linger, a restartable user manager, and rootless
podman with `keep-id:uid=10000`. If they don't, the e2e suite runs in a VM
started inside CI (for example with `vagrant` or `lima`), not by hand.

### 13.3 Contract test (weekly)

This runs the real helpers (`migrate.py`, `dbopen.py`, `confdiff.py`,
`doctor.py`) through the real `_upstream.py` against the **newest** official
image. It also checks F1, F2, F4, F6, F10 and F11. A failure means upstream
changed something Talaria relies on, and GitHub opens an issue.

### 13.4 Mutation testing

`mutmut` over `talaria/`, `helpers/` (except `_upstream.py`) and `telegram.py`.
Surviving mutants are either killed by a new test or listed as equivalent in
`docs/mutation-report.md`. The threshold is **≥ 85 %** of mutants killed. It
runs in the release workflow and weekly, not on every push. The bash wrappers
are too thin to be worth mutating; the e2e tests cover them.

## 14. CI/CD and dependencies

- Public GitHub repo, MIT licence, commits from the GitHub no-reply address.
  `main` is protected: pull requests only, and CI must be green.
- **`ci.yml`** (every push and pull request): shellcheck, pytest (3.10, 3.13),
  e2e, and a scan for deployment-specific values in docs and templates.
  Actions are pinned by SHA with `permissions: contents: read`.
- **`release.yml`** (on a tag `vX.Y.Z`): CI, mutation testing, the contract test,
  then a GitHub release with notes generated from the commits.
- **`upstream.yml`** (weekly): the contract test and mutation testing.
- **Dependabot**: `github-actions` and `uv`. Weekly, grouped,
  `cooldown: default-days: 14`.
- **Self-update** (manual, prompted by the reminder): `talaria self-update
  <tag>` fetches and checks out the tag, re-renders the units and restarts the
  bot.

## 15. Deferred to later versions

Each of these was considered and left out of v1 on purpose:

- A guard with version ranges and "only Talaria starts Hermes". The marker is
  enough.
- Automatic recovery at boot. The person sends `/rollback CONFIRM`.
- Checking that the deploy outcome matches the approved one. The config version
  check is enough.
- An outbox with guaranteed delivery. Direct send plus the journal is enough.
- Signed releases and trust on first use.
- A secret sweep of `.env` backup copies.
- A tag-format change detector and moved-tag detection.
- `/prepare`, `/logs`, `/resume`.
- Bash mutation testing, and a contract matrix over the oldest supported release.
- `--import-data` from non-podman installs.
- Building images; the recipe is in `docs/buildkit.md`.
- SELinux enforcing.

## 16. Related projects

Checked on 2026-09-27.

- **[NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell)** (0.1.x) is an agent
  sandbox runtime. A gateway (the control plane) runs sandboxes through a
  Docker, rootless Podman, Kubernetes or microVM driver. Policies control
  filesystem, process and network access, and provider credentials are injected
  outside the sandbox. Enforcement uses a proxy, OPA, Landlock and seccomp. It
  can be used standalone, without NemoClaw.
- **[NVIDIA NemoClaw](https://github.com/NVIDIA/NemoClaw)** (alpha, Apache 2.0)
  is a full stack on top of OpenShell for Hermes, OpenClaw and others. It covers
  onboarding, inference routing, messaging channels, snapshots and `rebuild`.
  Docker is its main path; Hermes on rootless Podman is an experimental profile.
  Its update path is still settling: issue #11248 describes updates stranding
  existing Hermes sandboxes.

**How they relate to Talaria.** NemoClaw replaces the whole deployment rather
than adding to it. It has no approval step and does not rehearse migrations.
OpenShell's network and credential policies are stronger isolation than plain
podman offers. Running Hermes under OpenShell, instead of plain podman, is a
candidate for v2 once its rootless Podman support matures. Re-evaluate both in
early 2027.
