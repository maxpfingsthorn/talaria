# Talaria v0.6.1 hardening Implementation Plan

> Historical: written for Talaria v0.6.1; paths and test names may have changed since (e.g. helpers/ → tools/).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the confirmed findings of the 2026-10-10 adversarial review (security, stuck states, self-update, inputs) and release them as v0.6.1.

**Architecture:** Targeted fixes in the existing modules; one new helper module `talaria/safefs.py` for writes into agent-controlled directories. No new `talaria.conf` keys, no change to the hub↔app `op` protocol (apps on v0.6.0 must keep working with a v0.6.1 hub during the update).

**Tech Stack:** Python 3.10+ stdlib only, pytest, existing fakes in `tests/opsfakes.py`, `tests/fakes*.py`.

**Spec:** this plan's "Findings" section (the review reports themselves stay out of the repo; they contain host details).

## Global Constraints

- Stdlib only; Python ≥ 3.10 (CI runs 3.10 and 3.13).
- **No new `talaria.conf` or `hub.conf` keys.** New conf validation is **strict-only** (`load_conf(..., strict=True)`, i.e. setup); runtime loading (`strict=False`, used by `op`) must keep accepting every file v0.6.0 accepts, or the hub loses `op` access to that app.
- **No `op` protocol change:** the hub (new code) must still drive apps that run v0.6.0 during an update. Do not add arguments to `op self-update`, `op hello` etc.; only read fields v0.6.0 already emits (`hello` already emits `version`).
- The repo is public: no host names, IPs, account names of any installation in code, tests or docs; use placeholders (`<host>.<tailnet>.ts.net`, `100.64.0.1`, `example`).
- Match surrounding style: short functions, comments only where the why is non-obvious, existing message tone.
- Test command (capped, always use it):
  `cd /home/max/talaria && timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
  For one file add its path instead of `tests`, e.g. `... uv run pytest -q -p no:cacheprovider tests/test_safefs.py`.
- Commit at the end of each task on the current branch (`docs/gbrain-443-chat-model`), message ending with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01XnPKBAtHq1LTWDjRc2j2mg
  ```
- Never run anything against the live host (no `bin/talaria setup`, `sudo`, `systemctl`, `podman`).

## Findings (the spec)

- **F1 (security, medium):** `cli.login_link` creates `/data/.login-link/.clawvisor/.local-session` with `mkdir(exist_ok=True)`, `os.chmod` and `os.open(O_CREAT|O_TRUNC)` — all follow symlinks the container can plant, so the container can overwrite any file of the app account (talaria.conf, backups, the op binary).
- **F2 (security, medium):** `gbrain.write_schema` (`Path.write_text`, every deploy) and `INIT_FILE` handling (`.exists()` + `write_text`) in `apps/gbrain.py` follow symlinks in the data dir.
- **F3 (security, low):** `rehearse.py` `copytree(symlinks=True)` copies with `copy2`, which follows a symlink swapped in after scandir (Hermes copies while running). `restore._move_missing` follows a symlinked parent inside the data dir.
- **F4 (security, medium):** the daily check's `hubcheck.talaria_release` → `hubupdate.offer` → `would_restart` → app `op self-update <tag> --dry-run` executes the new tag's `bin/talaria` as every app user before the owner taps anything.
- **F5 (state, high):** after a failed deploy, the automatic rollback (`ours=False`, `st["op"]` stays `deploy`) clears `last_deploy`/`previous` in `_swap`; if it is killed after the swap (or its post-start check fails and the owner then recovers by hand per README), `st["op"]` stays a changed `deploy` with no marker and no target: `/rollback` says "Nothing to roll back" while deploy/check/maintenance are refused forever. Also `deploy.py` saves `changed=True` before writing the marker.
- **F6 (state, medium):** `maintain.run`: if the app does not come back, the message says "tries again next night", but `timer_day` requires the app running — nothing retries or restarts it, and there is no recovery path in the bot. Same class: the gbrain rehearsal's stop/start (`copy_stopped`) leaves the app down silently if the restart fails; `talaria backup` runs during an interrupted change; archives without a JSON sidecar are never pruned.
- **F7 (self-update, medium-high):** `selfupdate.self_update` on setup failure only does `git checkout prev` (new Quadlet stays, app may stay down); `subprocess.TimeoutExpired` from setup is not caught (no restore at all). Same in `hubupdate._self_update`. `git fetch --tags` fails (exit 1) forever on an install that has a tag which was re-pointed upstream.
- **F8 (self-update, medium):** an app whose self-update failed stays on the old version silently: `relay.hello` compares only `protocol`, the daily check calls it "current", `/update <hub version>` and the button are refused as "not newer". Also the bot computes `mismatch` once at start; a single failed `op hello` (e.g. a bad conf edit) blocks that app until a manual bot restart.
- **F9 (inputs):** setup appends `tailscale_ip = …` without a preceding newline (corrupts the last line); `tailscale_ip` is never validated (bypasses the 0.0.0.0 guard, reaches an `ExecStartPre` shell string); hub.conf `check.time` is never validated; conf parsing: non-boolean values for boolean keys read as False, duplicate keys silently last-wins, port out of range accepted, relative `data_dir` accepted, a UTF-8 BOM hides the first key; `Shell.run` crashes on non-UTF-8 output of upstream tools; a `talaria.conf` without an `app` line is read as hermes even when setup was called with `--app gbrain`; setup accepts an operator account that is the hub or an app account.
- **F10 (docs):** README "Manual recovery" leads into F5's dead end and uses `systemctl --user` without `XDG_RUNTIME_DIR`; README promises "versions differ" behaviour that F8 describes as broken; AGENT_SETUP 11/11a invite running setup from an app account and writing `public_url` before the first setup; "verified" wording overstates integrity checks against the same source.

## Review Focus

- A v0.6.1 hub driving a v0.6.0 app during `/update v0.6.1` (no new op args, no new required reply fields) — Task 6 test `test_update_drives_old_app_protocol`.
- A `talaria.conf` that v0.6.0 accepted (e.g. `host_loopback = yes`, duplicate key) must still load with `strict=False` — Task 7 test `test_runtime_load_accepts_v060_quirks`.
- Container racing the safe write (symlink appears between check and open) — Task 1 test `test_write_new_refuses_symlink_planted_at_final_name`.
- Self-update failing on an app that was already stopped (marker present) must not leave a new Quadlet behind — Task 5 test `test_failed_update_rerenders_with_old_code`.
- Maintenance killed mid-hook (process dies) must leave a bot-recoverable state — Task 4 test `test_maintenance_crash_leaves_resumable_op`.

---

### Task 1: Writes into agent-controlled directories never follow symlinks (F1, F2, F3)

**Files:**
- Create: `talaria/safefs.py`
- Modify: `talaria/cli.py` (`login_link`, ~lines 100-125), `talaria/apps/gbrain.py` (`write_schema` ~line 125; `initialize` INIT_FILE check/write ~lines 297, 312), `talaria/rehearse.py` (~line 48 `copytree`), `talaria/restore.py` (`_move_missing` ~lines 158-171)
- Test: `tests/test_safefs.py` (new), additions to `tests/test_gbrain.py`, `tests/test_cli_ops.py` (or wherever `login_link` is tested — `grep -rn login_link tests`), `tests/test_rehearse.py`, `tests/test_rollback.py`/restore tests

**Interfaces:**
- Produces in `talaria/safefs.py`:
  - `write_text(path: Path, text: str, mode: int = 0o600) -> None` — atomically replace `path` with a regular file: write a fresh tmp file next to it opened `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW`, fsync, `os.replace(tmp, path)`. `os.replace` replaces a symlink at `path` with the file (never writes through it). Raises `ValueError` if `path.parent` is a symlink.
  - `is_regular(path: Path) -> bool` — `os.lstat` + `S_ISREG`; False if missing or not regular (symlinks are not regular).
  - `copy_regular(src, dst, *, follow_symlinks=True) -> str` — a `copy_function` for `shutil.copytree`: opens `src` with `O_RDONLY|O_NOFOLLOW`, `fstat` must be `S_ISREG` (else raise `OSError`), copies bytes to `dst` opened `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` with the source mode, `shutil.copystat` on the fds/paths without following; returns `dst`. Look at `talaria/history.py` `_copy_regular` and reuse its approach (move it into safefs and import it from history if that is a clean move).

- [ ] **Step 1: Write failing tests for safefs** in `tests/test_safefs.py`:

```python
import os
from pathlib import Path
import pytest
from talaria import safefs

def test_write_text_replaces_symlink_instead_of_following(tmp_path):
    victim = tmp_path / "victim"; victim.write_text("secret\n")
    link = tmp_path / "data" / ".talaria-schema"; link.parent.mkdir()
    link.symlink_to(victim)
    safefs.write_text(link, "42\n")
    assert victim.read_text() == "secret\n"
    assert not link.is_symlink() and link.read_text() == "42\n"

def test_write_new_refuses_symlink_planted_at_final_name(tmp_path, monkeypatch):
    # a symlink appearing where the tmp file goes must not be followed
    victim = tmp_path / "victim"; victim.write_text("x")
    d = tmp_path / "d"; d.mkdir()
    real_open = os.open
    def racing_open(p, flags, *a, **k):
        if str(p).endswith(".tmp") and not os.path.lexists(p):
            os.symlink(victim, p)
        return real_open(p, flags, *a, **k)
    monkeypatch.setattr(safefs.os, "open", racing_open)
    with pytest.raises(OSError):
        safefs.write_text(d / "f", "y")
    assert victim.read_text() == "x"

def test_write_text_refuses_symlinked_parent(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "data").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError):
        safefs.write_text(tmp_path / "data" / "f", "y")

def test_is_regular(tmp_path):
    f = tmp_path / "f"; f.write_text("1")
    (tmp_path / "l").symlink_to(f)
    assert safefs.is_regular(f) and not safefs.is_regular(tmp_path / "l")
    assert not safefs.is_regular(tmp_path / "missing")

def test_copy_regular_refuses_symlink(tmp_path):
    (tmp_path / "t").write_text("host secret")
    (tmp_path / "s").symlink_to(tmp_path / "t")
    with pytest.raises(OSError):
        safefs.copy_regular(tmp_path / "s", tmp_path / "out")
    assert not (tmp_path / "out").exists()
```

- [ ] **Step 2: Run** `... uv run pytest -q -p no:cacheprovider tests/test_safefs.py` — expect FAIL (no module).
- [ ] **Step 3: Implement `talaria/safefs.py`** per the interface. The tmp name must be unique (`f".{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"`) and end in `.tmp`; on any error unlink the tmp (with `os.unlink`, which does not follow) and re-raise.
- [ ] **Step 4: gbrain.** `write_schema` → `safefs.write_text(Path(data_dir) / SCHEMA_FILE, f"{v}\n", mode=0o644)` (keep the current mode semantics — check what mode the file has today; if unspecified, 0o644 matches `write_text`'s default umask behaviour). In `initialize`: replace `(data / INIT_FILE).exists()` with `safefs.is_regular(data / INIT_FILE)`, and the final write with `safefs.write_text(data / INIT_FILE, "1\n", mode=0o644)`. Tests in `tests/test_gbrain.py`: a `.talaria-schema` symlink to a file outside → after `write_schema` the outside file is unchanged; a dangling `INIT_FILE` symlink → `initialize` runs init and replaces the link with a regular file, outside target not created.
- [ ] **Step 5: login-link.** Rewrite the throwaway-HOME setup in `cli.login_link` so nothing follows a planted link:
  1. `tmp = data_dir / ".login-link"`; if `os.path.lexists(tmp)`: if it is a symlink or not a directory → `os.unlink(tmp)`; else `shutil.rmtree(tmp)` (rmtree refuses a symlinked top dir; it does not follow links inside).
  2. `os.mkdir(tmp, 0o700)` (no `exist_ok`: if it reappeared, `FileExistsError` → print `STOP: could not prepare the login session` and return 1).
  3. `dfd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)`; `os.mkdir(".clawvisor", 0o700, dir_fd=dfd)`; `sfd = os.open(".clawvisor", os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=dfd)`; `fd = os.open(".local-session", os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600, dir_fd=sfd)`; write the same JSON as today; close all fds in `finally`.
  4. No `os.chmod` calls on paths (modes are set at creation).
  5. Cleanup stays `shutil.rmtree(tmp, ignore_errors=True)` in `finally` (safe for a symlinked top).
  Test (in the existing login-link test file): plant `data/.login-link/.clawvisor/.local-session` → symlink to an outside file, and separately `data/.login-link` → symlink to an outside dir; run `login_link` with the existing fake shell/tty setup; assert the outside file/dir are untouched and the podman exec was still issued (or STOP was printed for the reappear case).
- [ ] **Step 6: rehearse copy.** Pass `copy_function=safefs.copy_regular` to the `copytree` call in `rehearse.py`. A file that is a symlink at copy time is already copied as a link by `symlinks=True`; a file swapped to a symlink after scandir now raises `OSError` inside copytree → copytree collects it in `shutil.Error`. Make sure the existing error path turns `shutil.Error` into a Transient (check how rehearse handles copy errors today; if it doesn't catch `shutil.Error`, catch it and raise the module's `Transient` with "the data changed during the copy; trying again next check"). Test: monkeypatch so a regular file becomes a symlink between scandir and copy (simplest: call `safefs.copy_regular` directly on a symlink is already covered; here assert `copytree` is called with `copy_function=safefs.copy_regular`, and that a `shutil.Error` from copytree becomes Transient).
- [ ] **Step 7: restore `_move_missing`.** Before `dst.parent.mkdir(...)`/`os.rename`, walk from the data dir to `dst.parent` with `os.lstat` per component; if any existing component is a symlink or not a directory, skip that move (and say nothing — the excluded path just isn't carried over). Test: `data/home` is a symlink to an outside dir; after restore, the outside dir did not receive `.cache`.
- [ ] **Step 8: Run the full suite** (capped command) — all pass.
- [ ] **Step 9: Commit** `Never write through symlinks in agent-controlled dirs (login-link, gbrain schema/init, rehearsal copy, restore)`.

---

### Task 2: A Talaria release offer runs no new code before the owner taps (F4)

**Files:**
- Modify: `talaria/hubupdate.py` (`offer`, add `announce`), `talaria/hubcheck.py` (`talaria_release`, ~line 106), `talaria/telegram.py` (`hub_button`)
- Test: `tests/test_hub*.py` where `offer`/`talaria_release`/`hub_button` are tested (`grep -rn "offer\|talaria_release\|hub_button" tests`)

**Interfaces:**
- Produces: `hubupdate.announce(hub, tag: str) -> int` — sends `Message(f"Talaria {tag} is available (installed v{__version__}).", buttons=[[(f"What would {tag} change?", f"hub|ofr:{tag}")]])`, returns 0. Calls no executor.
- `hubupdate.offer` unchanged (still used by `/update <tag>` → `update <tag> --offer`, which the owner initiated).
- Bot: button `hub|ofr:<tag>` with `SEMVER.match(tag)`: if not newer → `NOT_NEWER` (as for `up:`, but see Task 6 which relaxes "not newer" for lagging apps — keep the check in one helper so Task 6 can change it in one place); else `self.spawn("update", "update", tag, "--offer")` and return `("Checking", f"🔎 Checking what Talaria {tag} would change…")`.

- [ ] **Step 1: Failing tests:**
  - `test_timer_offer_runs_no_app_code`: drive `hubcheck.talaria_release(hub, force=False)` with a newer tag available (reuse the existing test setup for talaria_release); give each app a fake executor that records calls; assert no executor received `self-update`, and the sent message has exactly one button with data `hub|ofr:<tag>`.
  - `test_ofr_button_spawns_offer`: `bot.on_button("hub|ofr:v9.9.9")` → one spawn `["update", "v9.9.9", "--offer"]`; toast `"Checking"`.
  - `test_ofr_button_refuses_not_newer`: tag equal to `__version__` → NOT_NEWER text, no spawn.
- [ ] **Step 2: Run** them — FAIL.
- [ ] **Step 3: Implement** `announce`; in `talaria_release` call `hubupdate.announce(hub, latest)` instead of `offer`; add the `ofr` branch in `hub_button`.
- [ ] **Step 4: Docs:** README section on Talaria updates / bot buttons: the daily notice now says a release is available with a "What would vX change?" button; the check of what would restart happens only after that tap (or `/update vX`). Update the command/button table if it lists the offer.
- [ ] **Step 5: Full suite** — pass. **Commit** `Talaria release notice runs no new code before the owner taps`.

---

### Task 3: An interrupted deploy/rollback always stays recoverable from the bot (F5, F10 manual recovery)

**Files:**
- Modify: `talaria/rollback.py` (`_swap`, `rollback`, `needs_resume`), `talaria/deploy.py` (~lines 68-70), `talaria/cli.py` (`_manual_backup` ~lines 144-151), `README.md` ("Manual recovery")
- Test: `tests/test_rollback.py`, `tests/test_deploy.py`, `tests/test_cli_ops.py` (manual backup)

**Interfaces:**
- Consumes: `tests/opsfakes.ops_ctx(...)` (see how existing rollback tests simulate a failing `post_start_check` and a crash via `BaseException`).
- Produces: no new public names.

- [ ] **Step 1: Failing tests** (`tests/test_rollback.py`):
  - `test_killed_auto_rollback_is_resumable`: deploy whose post-start check fails, then the rollback's `post_start_check` raises a `BaseException` subclass (simulated kill). Assert afterwards: `st["op"]["op"] == "rollback"`, `marker.read` is None, `rollback.needs_resume(ctx, st)` is True, and `rollback_cmd(ctx)` starts the app and clears `st["op"]` (message contains "running again").
  - `test_leftover_op_without_target_resumes`: state with `st["op"] = {"op": "deploy", "changed": True, ...}`, no marker, `last_deploy=None` (the manual-recovery dead end). `describe(ctx)` must not say "Nothing to roll back"; it offers `/rollback CONFIRM` to start the app; `rollback_cmd` starts it and clears `st["op"]`.
  - `test_deploy_writes_marker_before_changed` (`tests/test_deploy.py`): crash right after the state save that sets `changed=True` must find the marker already written (use the existing `_crash_point` mechanism or monkeypatch `state.save` to raise on the save that contains `changed=True`, then assert `marker.read(ctx.paths)` is not None).
  - `test_manual_backup_refused_while_interrupted` (`tests/test_cli_ops.py`): marker present → `talaria backup` prints a refusal naming `/rollback CONFIRM`, creates no backup, exit code non-zero.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:**
  - In `_swap`, in the same `state.save` that sets `current/previous/last_deploy`, also set `st["op"] = {"op": "rollback" if op != "restore" else "restore", "backup": b.id, "changed": True, "started": <existing started or ctx.now().isoformat()>}`. (`op` here is the marker op name; a restore keeps "restore". Check callers: `restore_cmd` also uses `_swap`.) After the swap a crash now resumes via `needs_resume` (op in rollback/restore, no marker).
  - `needs_resume`: also True when `st.get("op")` and no marker and `target(ctx, st) is None` — nothing is left to restore, so the only sensible recovery is to start what is there and check it.
  - `deploy.py`: write the marker **before** `st["op"].update(backup=..., changed=True); state.save(...)`.
  - `_manual_backup`: `why = rollback.interrupted(ctx, st)`; if set, print `f"Backup refused: {why}."` to stderr and return 1.
- [ ] **Step 4: README "Manual recovery":** after the manual restore steps, the last step is "send `/rollback CONFIRM` in the bot (it finds nothing left to restore, starts the app, checks it and clears the interrupted state)". Every `systemctl --user`/`podman` line run "as the service user" gets the form `sudo -u <user> XDG_RUNTIME_DIR=/run/user/$(id -u <user>) systemctl --user …` (also in the gbrain "Provider keys" and embeddings snippets).
- [ ] **Step 5: Full suite** — pass. **Commit** `Interrupted deploy/rollback always recoverable via /rollback CONFIRM; marker before changed`.

---

### Task 4: Maintenance and rehearsal never leave the app silently stopped; prune orphan archives (F6)

**Files:**
- Modify: `talaria/maintain.py` (`run`), `talaria/rehearse.py` (the `copy_stopped` stop/start, ~lines 108-120), `talaria/backup.py` (`prune`, ~lines 114-123)
- Test: `tests/test_maintain.py`, `tests/test_rehearse.py`, `tests/test_backup*.py`

**Interfaces:**
- Consumes: `rollback.needs_resume` (from Task 3: True for an op with `changed: False` and no marker), `rollback.interrupted`.

- [ ] **Step 1: Failing tests:**
  - `test_maintenance_down_gives_recovery_path` (`tests/test_maintain.py`): timer run, hook ok, `service.start` ok but `post_start_check` returns "not active". Assert the message text contains "is not running" and "/rollback CONFIRM", does **not** contain "next night"; `st["op"] == {"op": "maintain", "changed": False, ...}`; `rollback.needs_resume(ctx, st)` is True.
  - `test_maintenance_crash_leaves_resumable_op`: hook raises a `BaseException` subclass (process death) → `st["op"]["op"] == "maintain"` persisted with `changed: False` (so the bot start notice and `/rollback CONFIRM` resume it).
  - `test_maintenance_success_clears_op`: normal run → `st["op"] is None` afterwards.
  - `test_maintenance_hook_fail_but_running`: hook returns a reason, app comes back → message keeps "tries again next night", `st["op"] is None`.
  - `test_rehearsal_restart_failure_flags_app` (`tests/test_rehearse.py`): gbrain-style adapter with `copy_stopped=True`; `service.start` in the restart raises `CommandError` → the error reported to the owner says the app is not running and names `/rollback CONFIRM`; `st["op"] == {"op": "rehearse", "changed": False, ...}`.
  - `test_prune_removes_orphan_archives` (`tests/test_backup*.py`): a `<id>.tar.gz` without `<id>.json` and a `<id>.json.tmp`, both with mtime older than 10 minutes → removed by `prune`; same files with a fresh mtime → kept.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:**
  - `maintain.run`: when `was_active`, before `service.stop`, set `st["op"] = {"op": "maintain", "changed": False, "started": ctx.now().isoformat()}` and save (together with the existing `st["maintenance"]` save). After the restart: if `down` is falsy → `st["op"] = None` and save. If `down` → keep the op and send `Message(f"{app.title} maintenance failed and {app.title} is not running. Send /rollback CONFIRM to start it again; nothing is restored.", untrusted=[("Last error", reason)])`. Otherwise keep today's messages. (State must be re-loaded or the same dict saved consistently — follow how `run` already saves `st`.)
  - `rehearse.py`: wrap the restart in the `finally` of the `copy_stopped` path: on exception, `st = state.load(...)`, set `st["op"] = {"op": "rehearse", "changed": False, "started": ...}`, save, and raise (or report through the module's existing error type) with text `f"{title} is not running after the rehearsal copy ({e}). Send /rollback CONFIRM to start it again."`. Do not mask an earlier exception's information: include both if both happened.
  - `backup.prune`: additionally unlink `*.tar.gz` whose `<id>.json` is missing and `*.json.tmp`, only if `mtime` is older than 600 s (a concurrent backup may be mid-write; the op lock normally prevents that, the age check is belt and braces).
- [ ] **Step 4: Docs:** README "Nightly maintenance" — on failure with the app down, the message says so and `/rollback CONFIRM` starts it (nothing restored); otherwise next night retries.
- [ ] **Step 5: Full suite** — pass. **Commit** `Maintenance/rehearsal restart failures are recoverable from the bot; prune orphan archives`.

---

### Task 5: A failed self-update really restores the previous version; tag fetch survives re-pointed tags (F7)

**Files:**
- Modify: `talaria/selfupdate.py` (`self_update`, `dry_run`), `talaria/hubupdate.py` (`_self_update`)
- Test: `tests/test_selfupdate*.py` / `tests/test_hub*.py` (`grep -rln "self_update\|dry_run" tests`)

**Interfaces:**
- Produces: `selfupdate.fetch_tag(sh, install_dir: str, tag: str) -> None` — `sh.run(["git", "-C", d, "fetch", "-q", "origin", f"+refs/tags/{tag}:refs/tags/{tag}"], timeout=600)`. Used by `self_update`, `dry_run` and `hubupdate._self_update` instead of `fetch -q --tags origin`. (`tag` is already validated by the strict SEMVER/TAG regexes at every entry point; keep it that way.)

- [ ] **Step 1: Failing tests:**
  - `test_failed_update_rerenders_with_old_code`: fake shell where `git rev-parse` → `"abc"`, fetch/checkout ok, the new `setup --as-service --lock-held` returns rc 1. Assert the call sequence after it is `git checkout -q abc` **then** `bin_link setup --as-service --lock-held` again (the old code re-renders units and restarts the app), and the printed text says the previous version was restored and whether the re-run setup succeeded ("previous version restored" vs "previous version restored, but its setup failed too: the app may be stopped").
  - `test_setup_timeout_restores`: the new setup raises `subprocess.TimeoutExpired` → same restore sequence, return 1.
  - `test_fetch_uses_forced_single_tag`: `self_update(ctx, "v0.6.1")` issues `git fetch -q origin +refs/tags/v0.6.1:refs/tags/v0.6.1`, never `--tags`.
  - `test_hub_failed_setup_rerenders` (hub side): `hubupdate._self_update` with the hub's `setup --as-hub --no-restart` returning 1 → checkout prev, then `setup --as-hub --no-restart` again with the old code, then the existing STOP message; `TimeoutExpired` handled the same way.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:**
  - `self_update`: wrap the setup call: `try: r = ctx.sh.run([...], check=False, timeout=1800); ok = r.returncode == 0; out = r.stdout` `except subprocess.TimeoutExpired: ok, out = False, ""`. If not ok: `git checkout -q prev` (check=False); then `r2 = ctx.sh.run([bin_link, "setup", "--as-service", "--lock-held"], check=False, timeout=1800)` (catch TimeoutExpired → treat as failed); print `f"self-update to {tag} failed; previous version restored"` + (`""` if r2 ok else `"; its setup failed too, so {ctx.app.title} may be stopped: see /status"`) to stderr; return 1. Note: `bin_link` points into the install dir, so after `checkout prev` it runs the old code — that is the point.
  - `hubupdate._self_update`: same pattern with `setup --as-hub --no-restart` and timeout 600; keep the existing STOP message, appending "its setup failed too" when the re-run fails.
  - Replace the three `fetch --tags` calls with `fetch_tag`.
- [ ] **Step 4: Docs:** README "Updating Talaria": a failed install re-runs the previous version's setup (units re-rendered, app restarted if needed).
- [ ] **Step 5: Full suite** — pass. **Commit** `Failed self-update re-runs the old setup; fetch only the wanted tag (forced)`.

---

### Task 6: Version skew is visible and can be retried; a failed hello heals (F8)

**Files:**
- Modify: `talaria/relay.py` (`hello`), `talaria/telegram.py` (`startup`, `dispatch` `/update`, `hub_button`, `app_command`, `on_button`), `talaria/hubupdate.py` (`_self_update`), `talaria/hubcheck.py` (`talaria_release`, optional), `README.md`
- Test: `tests/test_telegram.py`, `tests/test_relay*.py`, `tests/test_hub*.py`

**Interfaces:**
- Produces:
  - `relay.hello(entry) -> str | None` unchanged signature; now also returns `VERSIONS` when the hello line carries a `version` string different from the hub's `__version__` (a hello without `version` keeps today's behaviour: protocol only).
  - In the bot: `self.mismatch` re-probed lazily: in `app_command` and `on_button`, if `app in self.mismatch`, call `relay.hello(entry)` again; if it now returns None, drop the app from `mismatch` and continue; else return `relay.VERSIONS` as today.
  - `TelegramBot._update_allowed(tag) -> str | None` (one helper used by `/update`, `hub|up:` and `hub|ofr:` from Task 2): returns `None` if `semver_newer(tag, hub)` **or** (`tag == f"v{__version__}"` and `self.mismatch` is non-empty); otherwise the `NOT_NEWER` text.
  - `hubupdate._self_update(hub, tag)`: when `tag == f"v{__version__}"` (a retry for lagging apps), skip the hub checkout/setup (it is already there) and update only apps whose `relay.hello` is not None; report as today.

- [ ] **Step 1: Failing tests:**
  - `test_hello_flags_version_skew`: executor returns `{"kind":"hello","protocol":PROTOCOL,"version":"0.6.0"}` while hub `__version__` is `"0.6.1"` (monkeypatch `relay.__version__` or the module where it is read) → `relay.hello` returns `VERSIONS`. Same with `version` equal → None. Without a `version` field → None.
  - `test_mismatch_heals_on_next_command`: bot with app in `mismatch`; executor's hello now succeeds → `/status app` returns the status, and the app is no longer in `mismatch`.
  - `test_update_same_version_allowed_when_lagging`: `mismatch = {"gbrain"}`, `/update v<hub version>` → spawns `update v<hub> --offer`; with empty `mismatch` → NOT_NEWER.
  - `test_retry_updates_only_lagging_apps`: `_self_update(hub, f"v{__version__}")` → no git calls on the hub, `self-update` streamed only to the app whose hello reports an old version.
  - `test_update_drives_old_app_protocol` (Review Focus): `_update_app` against a fake executor that behaves like v0.6.0 (`op self-update <tag>` with no extra args, replies with `kind: reply` text only) → ✓; assert the argv sent is exactly `["self-update", tag]`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** per the interface. Keep `VERSIONS` text unchanged. In `hubcheck.talaria_release`, if `latest == v{__version__}` and any app's hello is not None, send (once per day at most — reuse `talaria_notified`-style state key in hub state, e.g. `st["skew_notified"] = <date>`; hub state is not talaria.conf, so this is not a conf key) a message `"Talaria versions differ on this host: <titles> still run an older version. Send /update v<ver> to retry."`.
- [ ] **Step 4: README** "Updating Talaria": make the "versions differ" paragraph true: a lagging app answers "versions differ; run /update", the daily check reminds you, `/update v<current>` retries only the lagging apps.
- [ ] **Step 5: Full suite** — pass. **Commit** `Detect and retry Talaria version skew; failed hello heals without a bot restart`.

---

### Task 7: Input validation and setup fixes; docs; version 0.6.1 (F9, F10)

**Files:**
- Modify: `talaria/setup.py` (tailscale_ip append ~line 462; operator checks ~lines 235-244; `--app` seeding), `talaria/conf.py` (parsing/validation), `talaria/hubconf.py` (`check.time`), `talaria/ctx.py` (~lines 225-228, app default), `talaria/units.py` (render guard), `talaria/shell.py` (`run`), `AGENT_SETUP.md`, `README.md`, `talaria/__init__.py`
- Test: `tests/test_conf*.py`, `tests/test_setup.py`, `tests/test_hubconf*.py`, `tests/test_shell*.py` (create if missing), `tests/test_units*.py`

**Interfaces:** none new beyond validation errors (`ValueError` with the key name, as existing validators do).

- [ ] **Step 1: Failing tests:**
  - `test_tailscale_ip_append_keeps_last_line`: conf file whose last line `dashboard.bind = tailscale` has no trailing newline → after the setup step, the file has `dashboard.bind = tailscale\ntailscale_ip = 100.64.0.1\n` (fake `tailscale ip -4` → `100.64.0.1`).
  - `test_tailscale_ip_validated`: `strict=True` load with `tailscale_ip = 0.0.0.0`, `tailscale_ip = 8.8.8.8`, `tailscale_ip = 100.64.0.1"x` → `ValueError` naming `tailscale_ip`; `100.100.1.2` ok. Unit render: a Quadlet render with an invalid `tailscale_ip` raises instead of emitting it (guard in `units.py` where the address is substituted, independent of strictness).
  - `test_check_time_validated`: hub.conf `check.time = 25:00` / `4.30` → `ValueError` naming `check.time` (use `conf.check_time_of_day`).
  - `test_strict_conf_rejects`: strict load rejects `host_loopback = maybe`, a duplicate key, `dashboard.port = 99999`, `data_dir = relative/dir`; a leading UTF-8 BOM is stripped (the first key is recognised).
  - `test_runtime_load_accepts_v060_quirks` (Review Focus): the same files load with `strict=False` exactly as v0.6.0 did (non-bool → False, duplicate last-wins, port/relative path accepted) — at most a warning to stderr.
  - `test_shell_decodes_invalid_utf8`: `Shell().run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"])` returns a string containing `�`, no exception.
  - `test_app_seeded_from_flag_when_conf_has_no_app`: existing talaria.conf with only `dashboard.public_url = …`, setup `--app gbrain --user gbrain` → loaded conf has `app == "gbrain"` and setup writes an `app = gbrain` line into the file (prepend or append with newline handling as above); no "already selects app = hermes" STOP.
  - `test_operator_cannot_be_hub_or_app`: setup run where the operator (`getpass.getuser()` or however setup determines it — check) equals the hub account name or an account listed in hub.conf apps → STOP naming the reason ("run setup from your own login account").
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:**
  - setup tailscale_ip: before appending, if the file is non-empty and does not end with `\n`, write `\n` first.
  - conf: a `tailscale_ip` check (IPv4 in `100.64.0.0/10`) under strict; strict-only checks for bool values (`true/false/yes/no/1/0`, case-insensitive), duplicate keys, port `1..65535`, `data_dir` absolute or `~/…`; strip a leading `﻿` in both modes (harmless). Runtime path unchanged otherwise.
  - units: refuse to render a `tailscale_ip` that is not a valid IPv4 address in `100.64.0.0/10` (raise `ValueError`).
  - hubconf: validate `check.time` with `check_time_of_day`.
  - shell: `subprocess.run(..., text=True, errors="replace")`.
  - ctx/setup: when the existing talaria.conf has no `app` key and setup got `--app X`, use X and add `app = X` to the file.
  - setup operator: STOP if operator == hub account or operator is in hub.conf apps (only when hub.conf exists).
- [ ] **Step 4: Docs:**
  - AGENT_SETUP 11: "tell the person to run setup from their own login account with `--app X --user X`" (never from an app or the hub account). 11a: "run setup first; it writes talaria.conf and STOPs asking for `dashboard.public_url`; then add the line and run setup again".
  - README: where integrity checks are described ("verified", "checksum-verified"), say they check the downloaded artifact against what the same release publishes (digest/checksums/revision label), i.e. integrity, not an independent signature; gbrain additionally checks build provenance when `gh` is logged in.
  - README conf section: comments must be on their own line; booleans are `true`/`false`.
- [ ] **Step 5: Version:** `talaria/__init__.py` → `__version__ = "0.6.1"`; check `pyproject.toml` / release workflow for other version strings (`grep -rn "0\.6\.0" --include=*.py --include=*.toml --include=*.yml .`) and update those that must match.
- [ ] **Step 6: Full suite** — pass. **Commit** `Validate tailscale_ip, check.time and strict conf input; seed app from --app; docs; v0.6.1`.

---

## Self-review notes

- Coverage: F1–F3 → Task 1; F4 → Task 2; F5 + manual-recovery docs → Task 3; F6 → Task 4; F7 → Task 5; F8 → Task 6; F9, F10 rest → Task 7.
- Deferred (not in this release): signed-tag verification / authenticity beyond same-source integrity; attestation fail-open state tracking; Hermes registry tag listing past 1000; passing the expected commit through `op self-update` (needs a protocol change).
- Task ordering dependencies: Task 2's `ofr` branch uses the not-newer check that Task 6 moves into `_update_allowed`; Task 4 relies on Task 3's `needs_resume` semantics (op with `changed: False`, no marker → resume — already true before Task 3).
