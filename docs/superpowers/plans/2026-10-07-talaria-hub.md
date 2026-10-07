# Talaria hub (v0.5.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Telegram bot per host, run by a dedicated hub account (`talaria`), manages every Talaria app install (Hermes, Clawvisor) through a strict `talaria op` door; Talaria updates itself host-wide from a bot button; v0.4 installs migrate without a new pairing.

**Architecture:** An install is a hub iff `~/.config/talaria/hub.conf` exists (`talaria/hubconf.py`). The hub's bot (`talaria/telegram.py`) and timer (`talaria/hubcheck.py`) reach each app only by running `talaria op …` as the app's account through an executor (`talaria/hubexec.py`: `SudoExecutor`, or `LocalExecutor` in transitional mode). `op` (`talaria/op.py`) runs as the app, parses strictly, and writes JSON lines; `talaria/relay.py` turns them into Telegram messages (button data prefixed `<app>|`). Long operations run in transient units as `talaria relay APP OP…`. Setup grows a hub phase (`setup --as-hub`), one root paste covering hub, app and op rule, app registration, and the v0.4 migration.

**Tech Stack:** Python ≥ 3.10 stdlib only, pytest, mutmut 3, rootless podman + Quadlet, systemd user units, sudo, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-07-talaria-hub-design.md` (binding). Brief rulings: `.superpowers/sdd/hub-plan-brief.md`.

**Rulings added by this plan** (the spec is silent; these are decisions, apply them):
- R1. `message` lines carry `text`, `blocks` (= `Message.untrusted`; a titled block is a 2-element list `[title, body]`), `commands`, `buttons` (rows of `[label, data]`). `reply` lines carry `text`, `buttons` (+ `restart` for a dry run). `button` lines carry `toast`, `status`, `run`.
- R2. The relay inserts the app name into each `commands` entry (`/approve v1` → `/approve hermes v1`); message text passes unchanged.
- R3. "Which app?" buttons carry `hub|w:<app>:<form>`, form ∈ `status`, `backups`, `check`, `rollback`, `restore:<id>`. `/approve` and `/reject` map to `status`; `/rollback [CONFIRM]` to `rollback` (describe); `/restore ID [CONFIRM]` to `restore:<id>` (describe); `/check` runs `check` (it changes nothing).
- R4. The hub keeps `~/.local/state/talaria/hub.json` (`{"talaria_notified": tag}`). The app's `check` no longer looks up Talaria releases; `status.py` stays unchanged.
- R5. `talaria update TAG` (hub) starts the transient unit `talaria-update-<ts>` running `talaria self-update TAG` (spec §6 steps). `talaria update TAG --offer` sends the per-app dry-run offer with the `hub|up:TAG` button. `/update TAG` runs `update TAG --offer` in a transient unit; the button runs `self-update TAG` in one.
- R6. In a hub, `self-update TAG` is the spec §6 flow. In an app (CLI or `op self-update`) it no longer restarts `talaria-telegram.service`, and refuses with exit 75 while the app's lock is held.
- R7. `SudoExecutor` runs `sudo -n -H -u <user> <home>/.local/bin/talaria op …` with cwd `/`; "sudo refused" = exit 1 and stderr starting with `sudo:`.
- R8. `talaria op` forces `XDG_RUNTIME_DIR=/run/user/<own uid>` and drops `DBUS_SESSION_BUS_ADDRESS` before anything else.
- R9. Whenever setup prints a root paste for accounts, it includes the op rule. Otherwise, after installing, setup probes `sudo -n -u <hub> sudo -n -H -u <app> <app bin> op hello`; on a `sudo:` refusal it prints a paste with only the op rule.
- R10. The op rule's app home comes from `getent passwd` when the paste runs.
- R11. `DONE` is printed only by the operator phase, after the app phase and the hub phase both returned 0.
- R12. App installs get only their Quadlet; the hub gets the three existing unit templates rendered with title `new` (`Description=Talaria: check for new releases`). `units.render_units` stays (it pins what v0.4 installs have on disk; its goldens stay).
- R13. `cli.main(argv, make=make_ctx, make_hub=None)`: role detection calls `hubexec.load_hub()` only when `make` is the default `make_ctx` (a test injecting an app ctx is an app install).
- R14. In a hub only `version, setup, set-token, bot, relay, check, status, self-update, update` run (others exit 2). In transitional mode `bot, relay, check, self-update, update` use the hub code; everything else stays app code.
- R15. Migration is needed when the app account has `~/.config/systemd/user/talaria-telegram.service` or `TALARIA_TELEGRAM_` keys in its `.env`. If the hub already holds a token, the app's token is dropped, not moved.
- R16. `op hello` with another protocol, or failing with a non-sudo error, marks the app "versions differ". The hub timer reports it per app and skips that app.
- R17. The hub phase restarts the bot only when hub units changed or an app was newly registered.
- R18. Setup prints `NOTE: …` (new label) when an app's `talaria.conf` sets `check.time`.
- R19. Hub command parsing: a first argument matching `^[a-z][a-z_-]{0,31}$` is an app name.
- R20. e2e runs in one CI job (`fetch-depth: 0` for the v0.4.2 tag). The test seeds `hub.conf` after the root paste. The adopt test uses its own hub `hubadopt`; the migration test uses hub `hub2`.

## Global Constraints

- Python stdlib only at runtime; `bin/talaria` runs `python3 -I`; CI unit matrix Python 3.10 and 3.13.
- Every test run is capped: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider <paths>` (full suite: `tests --ignore=tests/e2e --ignore=tests/contract`; mutmut: `MemoryMax=4G`). Never `prlimit`.
- Hermes and Clawvisor Quadlets stay byte-identical: `tests/golden/*` and the golden tests in `tests/test_units.py` pass unmodified.
- `FakeShell` rules: `check` is a bool; `timeout` is a positive number or `None`.
- The bot token is never printed, logged, put in an argv or sent anywhere but Telegram; `.env` files are written mode 0600 via `conf.write_env_value`; the migration moves it through an OS pipe between two sudo processes.
- One bot per host: app installs never install, enable or restart `talaria-telegram.service` or `talaria-check.timer`.
- `talaria op` accepts only the ops of spec §4.2 plus the hidden read-only `quadlet`; anything else exits 2 with no side effects. Every stdout line is a JSON object with `"v": 1` and `"kind"`.
- Telegram callback data ≤ 64 bytes including the `<app>|` / `hub|` prefix.
- Account names match `NAME_RE` (`^[A-Za-z_][A-Za-z0-9_.-]{0,31}$`); hub default `talaria`; app names are `talaria.apps.NAMES`; `hub` is reserved; an app may not use the hub's account; one account per app.
- Mutation score ≥ 85 % (spec §9); `rollback`, `restore`, `marker`, `deploy` keep 0 true survivors.
- Commit on branch `feat/hub`; every commit message ends with the attribution lines of your session. No merge, tag, push or host steps: the controller does those.

## Review Focus

1. **A Talaria update while an app is mid-operation** (deploy or rollback holds the app's lock): the app must report ✗ (busy) and keep its old checkout, never switch code under a running operation. → Task 6 (`test_self_update_refuses_while_an_operation_runs`).
2. **The longest real callback data after the app prefix** (Hermes rollback button with a `pre-v2026.12.31.99-2` backup id, Clawvisor restore, the "Which app?" restore button) must stay ≤ 64 bytes, or `keyboard()` silently drops the button. → Task 4 (`test_longest_real_callback_data_still_fits`) and Task 5 (`test_which_app_restore_button_fits`).
3. **An op that writes a lot to stderr** (podman/git chatter during a deploy) must not deadlock the relay that reads its stdout. → Task 3 (`test_stream_survives_lots_of_stderr`).
4. **The hub's environment leaking into the app's op** (sudo keeping `XDG_RUNTIME_DIR`/`DBUS_SESSION_BUS_ADDRESS` of the hub, or a cwd the app cannot enter): op must talk to its own user bus and run from `/`. → Task 2 (`test_cli_routes_op_with_this_users_runtime_dir`) and Task 3 (`test_executor_runs_from_root`).
5. **Registering a second app for an account that already serves one** (both would share one `talaria.conf`): refused with a clear message. → Task 1 (`test_register_refuses_an_account_already_serving_another_app`).

## File Structure

| File | Responsibility |
|---|---|
| `talaria/hubconf.py` (new) | `hub.conf`: role detection, `HubConf`, validation, `register_app` |
| `talaria/op.py` (new) | the app-side door: strict parser, JSON lines, button decision, self-update/dry-run/quadlet |
| `talaria/hubexec.py` (new) | running `op` as an app (`SudoExecutor`, `LocalExecutor`), `Hub` registry, `load_hub` |
| `talaria/relay.py` (new) | op output → Telegram (`to_message`, `quick`, `hello`, `relay`, `status_all`) |
| `talaria/hubcheck.py` (new) | the hub's daily check and the Talaria-release offer |
| `talaria/hubupdate.py` (new) | host-wide Talaria update (offer, transient unit, hub → apps → bot) |
| `talaria/telegram.py` | the one bot: commands with `[app]`, "Which app?", prefixed buttons, startup hello |
| `talaria/setup.py` | root paste (hub, app, op rule), operator phase, hub phase, app phase without a bot, migration |
| `talaria/units.py` | app installs: Quadlet only; hub: the three unit templates |
| `talaria/selfupdate.py` | app self-update (no bot restart, busy refusal) and `dry_run` |
| `talaria/cli.py` | `op` routing, `run_locked`, `relay`/`update`, role routing (hub, transitional, app) |
| `talaria/check.py` | the app's release check without the Talaria-release reminder |
| `talaria/conf.py`, `talaria/ctx.py` | `read_telegram_env`; `Paths.hub_conf`/`hub_state`, `make_hub_ctx` |
| `tests/hubfakes.py` (new) | `FakeExecutor`, `make_hub`, JSON-line builders |

---
### Task 1: HubConf and role detection

**Files:**
- Create: `talaria/hubconf.py`
- Modify: `talaria/conf.py` (add `read_telegram_env`, use it in `load_conf`), `talaria/ctx.py` (`Paths.hub_conf`, `Paths.hub_state`, `make_hub_ctx`), `talaria/setup.py` (import `NAME_RE` from `hubconf`)
- Test: `tests/test_hubconf.py`

**Interfaces:**
- Consumes: `talaria.conf.parse_kv(text) -> dict[str, str]`; `talaria.apps.NAMES = ("hermes", "clawvisor")`; `talaria.ctx.Ctx(paths, conf, sh, notify, …, app=None)`; `talaria.notify.TelegramNotifier(conf)`; `talaria.shell.Shell()`.
- Produces:
  - `talaria.hubconf.NAME_RE` (compiled `^[A-Za-z_][A-Za-z0-9_.-]{0,31}$`; `talaria.setup.NAME_RE` is the same object).
  - `talaria.hubconf.HubConf` dataclass: `apps: tuple[tuple[str, str], ...] = ()` (`(app, user)` in file order), `check_time: str = "04:30"`, `talaria_repo: str = "https://github.com/maxpfingsthorn/talaria"`, `telegram_api: str = "https://api.telegram.org"`, `telegram_token: str = ""` (not in repr), `telegram_user_id: int = 0`.
  - `talaria.hubconf.is_hub(paths) -> bool`; `load_hub_conf(paths) -> HubConf` (raises `ValueError`); `parse_apps(value: str, where) -> tuple`; `register_app(paths, app: str, user: str) -> bool` (True if added, False if already there; `ValueError` on conflict).
  - `talaria.conf.read_telegram_env(path: Path) -> tuple[str, int]`.
  - `Paths.hub_conf` = `conf_dir / "hub.conf"`; `Paths.hub_state` = `state_dir / "hub.json"`.
  - `talaria.ctx.make_hub_ctx(home: Path | None = None, sh=None) -> Ctx` (conf is a `HubConf`, `app` is `None`, notify a `TelegramNotifier(conf)`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hubconf.py
import pytest

from talaria import hubconf, setup
from talaria.conf import read_telegram_env
from talaria.ctx import Paths, make_hub_ctx
from talaria.hubconf import HubConf, is_hub, load_hub_conf, register_app


def home(tmp_path, conf=None, env=None):
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True, exist_ok=True)
    if conf is not None:
        p.hub_conf.write_text(conf)
    if env is not None:
        p.env_file.write_text(env)
    return p


def test_role_is_the_presence_of_hub_conf(tmp_path):
    p = home(tmp_path)
    assert not is_hub(p)
    p.hub_conf.write_text("")
    assert is_hub(p)
    assert p.hub_conf == tmp_path / ".config/talaria/hub.conf"
    assert p.hub_state == tmp_path / ".local/state/talaria/hub.json"


def test_defaults_without_a_file(tmp_path):
    c = load_hub_conf(Paths(tmp_path))
    assert c == HubConf()
    assert (c.apps, c.check_time, c.talaria_repo, c.telegram_api, c.telegram_token,
            c.telegram_user_id) == ((), "04:30", "https://github.com/maxpfingsthorn/talaria",
                                    "https://api.telegram.org", "", 0)


def test_keys_and_secrets(tmp_path):
    p = home(tmp_path, "# hub\napps = hermes:hermes clawvisor:cv\ncheck.time = 03:15\n"
                       "talaria_repo = /srv/t\ntelegram_api = http://127.0.0.1:8081\n",
             "TALARIA_TELEGRAM_TOKEN=1:abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    c = load_hub_conf(p)
    assert c.apps == (("hermes", "hermes"), ("clawvisor", "cv"))
    assert (c.check_time, c.talaria_repo, c.telegram_api) == ("03:15", "/srv/t",
                                                              "http://127.0.0.1:8081")
    assert (c.telegram_token, c.telegram_user_id) == ("1:abc", 42)
    assert "1:abc" not in repr(c)


@pytest.mark.parametrize("line,msg", [
    ("colour = red", "unknown key in"),
    ("apps = hermes", "'hermes' is not <app>:<user>"),
    ("apps = hub:talaria", "'hub:talaria' is not <app>:<user>"),
    ("apps = nope:x", "'nope:x' is not <app>:<user> with app one of hermes, clawvisor"),
    ("apps = hermes:bad;name", "not a valid account name: 'bad;name'"),
    ("apps = hermes:a hermes:b", "hermes is registered twice"),
    ("apps = hermes:a clawvisor:a", "the account a is registered twice"),
])
def test_invalid_hub_conf(tmp_path, line, msg):
    p = home(tmp_path, line + "\n")
    with pytest.raises(ValueError, match=msg.replace("(", r"\(")):
        load_hub_conf(p)


def test_register_into_an_empty_conf(tmp_path):
    p = home(tmp_path, "# Talaria hub settings; see README.\n")
    assert register_app(p, "hermes", "hermes") is True
    assert p.hub_conf.read_text() == ("# Talaria hub settings; see README.\n"
                                      "apps = hermes:hermes\n")


def test_register_keeps_comments_order_and_place(tmp_path):
    p = home(tmp_path, "# hub\napps = hermes:hermes\ncheck.time = 03:00\n")
    assert register_app(p, "clawvisor", "clawvisor") is True
    assert p.hub_conf.read_text() == ("# hub\napps = hermes:hermes clawvisor:clawvisor\n"
                                      "check.time = 03:00\n")
    assert register_app(p, "clawvisor", "clawvisor") is False
    assert p.hub_conf.read_text().count("clawvisor:clawvisor") == 1


def test_register_refuses_the_same_app_for_another_account(tmp_path):
    p = home(tmp_path, "apps = hermes:hermes\n")
    with pytest.raises(ValueError, match="already registers hermes for the account hermes"):
        register_app(p, "hermes", "hermes2")


def test_register_refuses_an_account_already_serving_another_app(tmp_path):
    p = home(tmp_path, "apps = hermes:agent\n")
    with pytest.raises(ValueError, match="the account agent is registered twice"):
        register_app(p, "clawvisor", "agent")
    assert p.hub_conf.read_text() == "apps = hermes:agent\n"


@pytest.mark.parametrize("app,user", [("hub", "x"), ("nope", "x"), ("hermes", "a b")])
def test_register_validates_the_entry(tmp_path, app, user):
    p = home(tmp_path, "")
    with pytest.raises(ValueError):
        register_app(p, app, user)


def test_read_telegram_env(tmp_path):
    assert read_telegram_env(tmp_path / "missing") == ("", 0)
    f = tmp_path / ".env"
    f.write_text("TALARIA_TELEGRAM_TOKEN=1:x\nTALARIA_TELEGRAM_USER_ID=\nOTHER=1\n")
    assert read_telegram_env(f) == ("1:x", 0)


def test_make_hub_ctx(tmp_path):
    home(tmp_path, "apps = hermes:hermes\n", "TALARIA_TELEGRAM_TOKEN=1:abc\n")
    ctx = make_hub_ctx(tmp_path)
    assert ctx.paths.home == tmp_path and ctx.app is None
    assert ctx.conf.apps == (("hermes", "hermes"),) and ctx.conf.telegram_token == "1:abc"
    assert ctx.notify.conf is ctx.conf


def test_setup_uses_the_same_name_rule():
    assert setup.NAME_RE is hubconf.NAME_RE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_hubconf.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'talaria.hubconf'`.

- [ ] **Step 3: Implement**

`talaria/conf.py` — add after `parse_kv`:

```python
def read_telegram_env(path: Path) -> tuple[str, int]:
    """(TALARIA_TELEGRAM_TOKEN, TALARIA_TELEGRAM_USER_ID) from an .env file; ("", 0) if absent."""
    if not path.exists():
        return "", 0
    env = parse_kv(path.read_text())
    return (env.get("TALARIA_TELEGRAM_TOKEN", ""),
            int(env.get("TALARIA_TELEGRAM_USER_ID", "0") or 0))
```

and in `load_conf` replace the last block

```python
    if paths.env_file.exists():
        env = parse_kv(paths.env_file.read_text())
        conf.telegram_token = env.get("TALARIA_TELEGRAM_TOKEN", "")
        conf.telegram_user_id = int(env.get("TALARIA_TELEGRAM_USER_ID", "0") or 0)
    return conf
```

with

```python
    conf.telegram_token, conf.telegram_user_id = read_telegram_env(paths.env_file)
    return conf
```

`talaria/hubconf.py`:

```python
"""The hub's settings (spec §3). An install is the hub iff ~/.config/talaria/hub.conf exists."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from talaria.conf import parse_kv, read_telegram_env

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")   # goes into a root shell block
RESERVED = "hub"                                          # button prefix of the hub itself


@dataclass
class HubConf:
    apps: tuple = ()                     # ((app, user), ...) in hub.conf order
    check_time: str = "04:30"
    talaria_repo: str = "https://github.com/maxpfingsthorn/talaria"
    telegram_api: str = "https://api.telegram.org"
    telegram_token: str = field(default="", repr=False)
    telegram_user_id: int = 0


_KEYS = {"apps": "apps", "check.time": "check_time", "talaria_repo": "talaria_repo",
         "telegram_api": "telegram_api"}


def is_hub(paths) -> bool:
    return paths.hub_conf.exists()


def parse_apps(value: str, where) -> tuple:
    from talaria import apps
    out, names, users = [], set(), set()
    for entry in value.split():
        app, sep, user = entry.partition(":")
        if not sep or app == RESERVED or app not in apps.NAMES:
            raise ValueError(f"apps: {entry!r} is not <app>:<user> with app one of "
                             f"{', '.join(apps.NAMES)} in {where}")
        if not NAME_RE.match(user):
            raise ValueError(f"apps: not a valid account name: {user!r} in {where}")
        if app in names:
            raise ValueError(f"apps: {app} is registered twice in {where}")
        if user in users:   # one account holds one talaria.conf, so one app
            raise ValueError(f"apps: the account {user} is registered twice in {where}")
        names.add(app)
        users.add(user)
        out.append((app, user))
    return tuple(out)


def load_hub_conf(paths) -> HubConf:
    conf = HubConf()
    if paths.hub_conf.exists():
        for k, v in parse_kv(paths.hub_conf.read_text()).items():
            if k not in _KEYS:
                raise ValueError(f"unknown key in {paths.hub_conf}: {k}")
            setattr(conf, _KEYS[k], parse_apps(v, paths.hub_conf) if k == "apps" else v)
    conf.telegram_token, conf.telegram_user_id = read_telegram_env(paths.env_file)
    return conf


def register_app(paths, app: str, user: str) -> bool:
    """Add app:user to the `apps` line, in place, keeping every other line. False if it is
    already registered; ValueError if the app is registered for another account or the
    entry is invalid."""
    current = load_hub_conf(paths).apps
    if (app, user) in current:
        return False
    for a, u in current:
        if a == app:
            raise ValueError(f"{paths.hub_conf} already registers {app} for the account {u}")
    value = " ".join(f"{a}:{u}" for a, u in (*current, (app, user)))
    parse_apps(value, paths.hub_conf)
    text = paths.hub_conf.read_text() if paths.hub_conf.exists() else ""
    lines, done = [], False
    for line in text.splitlines():
        if set(parse_kv(line)) == {"apps"}:
            lines.append(f"apps = {value}")
            done = True
        else:
            lines.append(line)
    if not done:
        lines.append(f"apps = {value}")
    paths.hub_conf.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.hub_conf.with_name(paths.hub_conf.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, paths.hub_conf)
    return True
```

`talaria/ctx.py` — in `Paths`, after `env_file`:

```python
    @property
    def hub_conf(self): return self.conf_dir / "hub.conf"
```

and after `state_file`:

```python
    @property
    def hub_state(self): return self.state_dir / "hub.json"
```

and append:

```python
def make_hub_ctx(home: Path | None = None, sh=None) -> Ctx:
    """The hub account's ctx: hub.conf instead of talaria.conf, no app."""
    from talaria.hubconf import load_hub_conf
    from talaria.notify import TelegramNotifier
    from talaria.shell import Shell

    paths = Paths(Path(home) if home else Path.home())
    conf = load_hub_conf(paths)
    return Ctx(paths=paths, conf=conf, sh=sh or Shell(), notify=TelegramNotifier(conf))
```

`talaria/setup.py` — replace the line
`NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")   # goes into a root shell block`
with `from talaria.hubconf import NAME_RE  # noqa: E402  (shared with hub.conf validation)` placed with the other `from talaria…` imports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS (existing `test_conf.py` covers the `load_conf` refactor).

- [ ] **Step 5: Commit**

```bash
git add talaria/hubconf.py talaria/conf.py talaria/ctx.py talaria/setup.py tests/test_hubconf.py
git commit -m "Hub role: hub.conf, HubConf, register_app, make_hub_ctx"
```

---

### Task 2: `talaria op`, JsonNotifier and the button decision

**Files:**
- Create: `talaria/op.py`, `tests/test_op.py`
- Modify: `talaria/cli.py` (route `op` before argparse; extract `run_locked`), `talaria/check.py` (drop the Talaria-release reminder), `tests/test_check.py` (drop its tests)
- Test: `tests/test_op.py`, `tests/test_check.py`

**Interfaces:**
- Consumes: `cli._release`, `cli._backup_id`, `cli._semver` (argparse types), `cli.reject(ctx, tag) -> str`, `cli.EXIT_BUSY = 75`; `status.status_text(ctx)`, `status.backups_text(ctx)`, `status.interrupted_text(ctx, st) -> str | None`; `rollback.describe/describe_buttons/describe_restore/describe_restore_buttons/needs_resume/target/fresh/stamp`; `talaria.ctx.make_ctx()`.
- Produces:
  - `op.PROTOCOL = 1`; `op.OPS: tuple[str, ...]`; `op.STALE = "Out of date — send /status"`.
  - `op.emit(out, kind: str, **fields) -> None` (one JSON line `{"v": 1, "kind": kind, **fields}`, flushed).
  - `op.JsonNotifier(out)` with `.send(m: Message)` → a `message` line `{text, blocks, commands, buttons}`.
  - `op.decide_button(ctx, data: str | None) -> tuple[str, str | None, list[str] | None]` = `(toast, status label, op argv to run or None)`; run argvs are `["deploy", tag]`, `["rollback", "confirm"]`, `["restore", id, "confirm"]`.
  - `op.build_parser() -> argparse.ArgumentParser`; `op.main(argv: list[str], make=None, out=None) -> int`.
  - `cli.run_locked(ctx, args: argparse.Namespace) -> int` (lock + `_locked` + Busy/unexpected-error handling, as `main` did).
  - Lines: `reply {text, buttons}`, `button {toast, status, run}`, `hello {protocol, app, title, version}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_op.py
import argparse
import io
import json
import os

import pytest

from talaria import __version__, backup, cli, lock, op, rollback, state
from talaria.notify import Message
from tests.fakes import make_test_ctx


def lines(buf):
    return [json.loads(l) for l in buf.getvalue().splitlines()]


@pytest.fixture
def opx(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl", "--user", "is-active", out="active\n")

    def run(*argv):
        out = io.StringIO()
        rc = op.main(list(argv), make=lambda: ctx, out=out)
        return rc, lines(out)
    return ctx, run


def pending(ctx, tag="v2026.9.24"):
    st = state.load(ctx.paths)
    st["pending"] = {"tag": tag}
    state.save(ctx.paths, st)


def test_hello(opx):
    ctx, run = opx
    assert run("hello") == (0, [{"v": 1, "kind": "hello", "protocol": 1, "app": "hermes",
                                 "title": "Hermes", "version": __version__}])


@pytest.mark.parametrize("argv", [
    [], ["setup"], ["set-token"], ["login-link"], ["bot"], ["relay", "hermes", "status"],
    ["adopt"], ["backup"], ["history"], ["rehearse", "v2026.9.24"], ["sh"], ["Status"],
    ["status", "extra"], ["status", "--timer"], ["status", "-h"], ["-h"], ["check", "--tim"],
    ["check", "--timer=1"], ["deploy"], ["deploy", "v2026.9.24;id"], ["deploy", "$(id)"],
    ["reject", "latest"], ["rollback"], ["rollback", "CONFIRM"], ["rollback", "confirm", "x"],
    ["restore", "../x", "describe"], ["restore", "20260927T043000Z-manual"],
    ["restore", "20260927T043000Z-manual", "CONFIRM"], ["button"], ["button", "a", "b"],
    ["hello", "x"], ["interrupted", "x"],
])
def test_anything_else_exits_2_without_side_effects(argv, capsys):
    out = io.StringIO()
    rc = op.main(argv, make=lambda: pytest.fail("no ctx for a refused op"), out=out)
    assert rc == 2 and out.getvalue() == ""


def test_other_apps_tag_scheme_is_refused(opx):
    ctx, run = opx
    assert run("deploy", "v0.9.10") == (2, [])
    assert run("reject", "v0.9.10") == (2, [])
    assert ctx.sh.calls == [] and state.load(ctx.paths)["rejected"] == []


def test_status_and_backups_reply(opx):
    ctx, run = opx
    rc, out = run("status")
    assert rc == 0 and out[0]["kind"] == "reply" and out[0]["text"].startswith("Hermes unknown")
    assert out[0]["buttons"] == []
    assert run("backups") == (0, [{"v": 1, "kind": "reply", "text": "No backups yet.",
                                   "buttons": []}])


def test_interrupted_is_silent_unless_interrupted(opx):
    ctx, run = opx
    assert run("interrupted") == (0, [])
    st = state.load(ctx.paths)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "started": ctx.now().isoformat()}
    state.save(ctx.paths, st)
    rc, out = run("interrupted")
    assert rc == 0 and out[0]["kind"] == "reply" and "Interrupted deploy v2026.9.24" in out[0]["text"]


def test_reject_replies_and_records(opx):
    ctx, run = opx
    assert run("reject", "v2026.9.24") == (0, [{
        "v": 1, "kind": "reply", "text": "Rejected v2026.9.24. It will not be offered again.",
        "buttons": []}])
    assert state.load(ctx.paths)["rejected"] == ["v2026.9.24"]


def test_rollback_describe_carries_its_button(opx, monkeypatch):
    ctx, run = opx
    monkeypatch.setattr(op.rollback, "describe", lambda c: "would restore")
    monkeypatch.setattr(op.rollback, "describe_buttons", lambda c: [[("Roll back", "rb:B:1")]])
    assert run("rollback", "describe") == (0, [{"v": 1, "kind": "reply", "text": "would restore",
                                                "buttons": [[["Roll back", "rb:B:1"]]]}])


def test_restore_describe(opx):
    ctx, run = opx
    assert run("restore", "20260927T043000Z-manual", "describe") == (0, [{
        "v": 1, "kind": "reply", "text": "No backup 20260927T043000Z-manual. /backups lists them.",
        "buttons": []}])


@pytest.mark.parametrize("argv,ns", [
    (["check"], dict(cmd="check", timer=False)),
    (["check", "--timer"], dict(cmd="check", timer=True)),
    (["deploy", "v2026.9.24"], dict(cmd="deploy", tag="v2026.9.24", timer=False)),
    (["rollback", "confirm"], dict(cmd="rollback", confirm=True, timer=False)),
    (["restore", "20260927T043000Z-manual", "confirm"],
     dict(cmd="restore", id="20260927T043000Z-manual", confirm=True, timer=False)),
])
def test_long_ops_run_under_the_lock_like_the_cli(opx, monkeypatch, argv, ns):
    ctx, run = opx
    seen = []
    monkeypatch.setattr(op.cli, "run_locked", lambda c, a: (seen.append((c is ctx, vars(a))), 3)[1])
    assert run(*argv) == (3, [])
    assert seen == [(True, ns)]


def test_messages_become_json_lines_and_stray_prints_go_to_stderr(opx, monkeypatch, capsys):
    ctx, run = opx

    def deploy(c, tag):
        print("noise from a library")
        c.notify.send(Message("Deployed Hermes v2026.9.24.", untrusted=[("Log", "body"), "raw"],
                              commands=["/rollback"], buttons=[[("Roll back", "rb:x:1")]]))
    monkeypatch.setattr(cli.deploy, "deploy", deploy)
    assert run("deploy", "v2026.9.24") == (0, [{
        "v": 1, "kind": "message", "text": "Deployed Hermes v2026.9.24.",
        "blocks": [["Log", "body"], "raw"], "commands": ["/rollback"],
        "buttons": [[["Roll back", "rb:x:1"]]]}])
    err = capsys.readouterr().err
    assert "noise from a library" in err and "[talaria] message: Deployed Hermes" in err


def test_busy_deploy_sends_busy_and_exits_75(opx):
    ctx, run = opx
    with lock.op_lock(ctx.paths):
        rc, out = run("deploy", "v2026.9.24")
    assert rc == 75 and [o["text"] for o in out] == [
        "Busy: another operation is running. Try again in a minute."]


def test_busy_timer_check_is_silent(opx):
    ctx, run = opx
    with lock.op_lock(ctx.paths):
        assert run("check", "--timer") == (0, [])


def test_button_op_emits_the_decision(opx):
    ctx, run = opx
    pending(ctx)
    assert run("button", "ap:v2026.9.24") == (0, [{
        "v": 1, "kind": "button", "toast": "Deploying",
        "status": "✅ Approved — deploying v2026.9.24", "run": ["deploy", "v2026.9.24"]}])


def test_cli_routes_op_with_this_users_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/99999")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/99999/bus")
    seen = []
    monkeypatch.setattr(op, "main", lambda argv: (seen.append(
        (argv, os.environ["XDG_RUNTIME_DIR"], "DBUS_SESSION_BUS_ADDRESS" in os.environ)), 7)[1])
    assert cli.main(["op", "hello"]) == 7
    assert seen == [(["hello"], f"/run/user/{os.getuid()}", False)]


# ---- the button decision (moved here from the v0.4 bot) ----

def test_approve_button(tmp_path):
    ctx = make_test_ctx(tmp_path)
    pending(ctx)
    assert op.decide_button(ctx, "ap:v2026.9.24") == (
        "Deploying", "✅ Approved — deploying v2026.9.24", ["deploy", "v2026.9.24"])


def test_stale_approve_button(tmp_path):
    ctx = make_test_ctx(tmp_path)
    pending(ctx, "v2026.10.1")
    assert op.decide_button(ctx, "ap:v2026.9.24") == (op.STALE, "⌛ Out of date", None)
    assert op.STALE == "Out of date — send /status"


def test_reject_button(tmp_path):
    ctx = make_test_ctx(tmp_path)
    pending(ctx)
    assert op.decide_button(ctx, "rj:v2026.9.24") == ("Rejected", "❌ Rejected v2026.9.24", None)
    assert state.load(ctx.paths)["rejected"] == ["v2026.9.24"]


def test_reject_while_busy_keeps_the_buttons(tmp_path):
    ctx = make_test_ctx(tmp_path)
    pending(ctx)
    with lock.op_lock(ctx.paths):
        assert op.decide_button(ctx, "rj:v2026.9.24") == ("Busy, try again in a minute", None, None)


def test_rollback_button_must_match_current_target(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: False)
    monkeypatch.setattr(op.rollback, "target", lambda c, st: ("20260927T043000Z-pre-v2", {"tag": "v1"}))
    now = rollback.stamp(ctx)
    assert op.decide_button(ctx, f"rb:20260927T043000Z-other:{now}") == (op.STALE, "⌛ Out of date", None)
    assert op.decide_button(ctx, f"rb:20260927T043000Z-pre-v2:{now}") == (
        "Rolling back", "↩️ Rolling back…", ["rollback", "confirm"])


def test_resume_button(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: True)
    assert op.decide_button(ctx, "rb:resume")[2] == ["rollback", "confirm"]
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: False)
    monkeypatch.setattr(op.rollback, "target", lambda c, st: None)
    assert op.decide_button(ctx, "rb:resume") == (op.STALE, "⌛ Out of date", None)


def test_restore_button(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert op.decide_button(ctx, "rs:20260927T043000Z-manual")[2] is None    # no such backup
    bk = backup.create(ctx, "manual", None)
    assert op.decide_button(ctx, f"rs:{bk.id}:{rollback.stamp(ctx)}") == (
        "Restoring", f"↩️ Restoring {bk.id}…", ["restore", bk.id, "confirm"])


@pytest.mark.parametrize("age, fresh", [(0, True), (60, True), (61, False), (-1, False)])
def test_rollback_and_restore_buttons_expire(tmp_path, monkeypatch, age, fresh):
    ctx = make_test_ctx(tmp_path)
    bk = backup.create(ctx, "manual", None)
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: False)
    monkeypatch.setattr(op.rollback, "target", lambda c, st: (bk.id, {"tag": "v1"}))
    sent = rollback.stamp(ctx) - age
    runs = [op.decide_button(ctx, f"{k}:{bk.id}:{sent}")[2] for k in ("rb", "rs")]
    assert (runs == [["rollback", "confirm"], ["restore", bk.id, "confirm"]]) is fresh
    if not fresh:
        assert runs == [None, None]


@pytest.mark.parametrize("data", ["rb:{id}", "rs:{id}", "rb:{id}:x", "rs:{id}:1:2"])
def test_buttons_without_a_valid_minute_are_stale(tmp_path, monkeypatch, data):
    ctx = make_test_ctx(tmp_path)
    bk = backup.create(ctx, "manual", None)
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: False)
    monkeypatch.setattr(op.rollback, "target", lambda c, st: (bk.id, {"tag": "v1"}))
    assert op.decide_button(ctx, data.format(id=bk.id)) == (op.STALE, "⌛ Out of date", None)


@pytest.mark.parametrize("data", ["xx:1", "ap:latest;rm", "rs:../x", "rb:", "", None])
def test_malformed_buttons(tmp_path, data):
    assert op.decide_button(make_test_ctx(tmp_path), data) == ("Unknown button", None, None)


def test_done_button(tmp_path):
    assert op.decide_button(make_test_ctx(tmp_path), "done") == ("Already handled", None, None)
```

In `tests/test_check.py`: delete `test_talaria_update_reminder_once`, `test_reminder_text_exact`, `test_reminder_offline_is_silent`, `test_reminder_no_release_or_same`; in the `cctx` fixture delete the line `ctx.talaria_latest = f"v{__version__}"` and the `monkeypatch.setattr(check, "latest_semver", …)` statement (two lines); add:

```python
def test_check_never_looks_up_talaria_releases(cctx):
    ctx, st = cctx
    check.check(ctx, st)         # FakeShell has no rules: any git call would fail the test
    assert ctx.sh.calls == [] and not hasattr(check, "latest_semver")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_op.py tests/test_check.py`
Expected: FAIL (`ImportError: cannot import name 'op'`; `test_check_never_looks_up_talaria_releases` fails on the `git ls-remote` call).

- [ ] **Step 3: Implement**

`talaria/check.py`: delete `_talaria_reminder` and its call in `check()`; change the imports to

```python
from talaria import history
from talaria.notify import Message
from talaria.rehearse import Permanent, Transient, rehearse
from talaria.rollback import interrupted
from talaria.shell import CommandError
from talaria.tags import pick_candidate, releases
```

`talaria/cli.py`: replace the tail of `main` (from `try:\n        with lock.op_lock(ctx.paths):` to the final `return 0`) with `return run_locked(ctx, args)` and add above `main`:

```python
def run_locked(ctx, args) -> int:
    """Run a state-changing command under the op lock; report Busy and crashes."""
    try:
        with lock.op_lock(ctx.paths):
            _locked(ctx, args)
    except lock.Busy:
        if args.cmd == "check" and args.timer:
            return 0
        ctx.notify.send(Message("Busy: another operation is running. Try again in a minute."))
        return EXIT_BUSY
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        ctx.notify.send(Message(f"talaria {args.cmd} failed unexpectedly: {e}"))
        return 1
    return 0
```

and make the start of `main`:

```python
def main(argv: list[str] | None = None, make=make_ctx) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["op"]:
        # sudo may keep the hub's environment: talk to this account's own user bus
        os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
        from talaria import op
        return op.main(argv[1:])
    args = build_parser().parse_args(argv)
```

`talaria/op.py`:

```python
"""`talaria op`: the only door from the hub into an app install (spec §4).

Runs as the app's account (through `sudo -n -u <app>` from the hub, or directly in
transitional mode). Parses its own arguments strictly; writes JSON lines to stdout and
everything else to stderr (the journal)."""
from __future__ import annotations

import argparse
import contextlib
import json
import sys

from talaria import __version__, cli, rollback, state, status
from talaria.backup import ID_RE
from talaria.notify import Message

PROTOCOL = 1
OPS = ("status", "backups", "check", "deploy", "reject", "rollback", "restore", "button",
       "hello", "interrupted")
STALE = "Out of date — send /status"


def emit(out, kind: str, **fields) -> None:
    out.write(json.dumps({"v": 1, "kind": kind, **fields}) + "\n")
    out.flush()


def _rows(buttons) -> list:
    return [[[str(label), str(data)] for label, data in row] for row in buttons or []]


class JsonNotifier:
    """ctx.notify inside op: each message becomes one `message` line; the hub sends it."""

    def __init__(self, out):
        self.out = out

    def send(self, m: Message) -> None:
        print(f"[talaria] message: {m.text}", file=sys.stderr)
        emit(self.out, "message", text=m.text,
             blocks=[list(b) if isinstance(b, tuple) else b for b in m.untrusted],
             commands=list(m.commands), buttons=_rows(m.buttons))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="talaria op", add_help=False, allow_abbrev=False)
    sub = p.add_subparsers(dest="op", required=True)

    def add(name):
        return sub.add_parser(name, add_help=False, allow_abbrev=False)

    for name in ("status", "backups", "hello", "interrupted"):
        add(name)
    add("check").add_argument("--timer", action="store_true")
    for name in ("deploy", "reject"):
        add(name).add_argument("tag", type=cli._release)
    add("rollback").add_argument("mode", choices=("describe", "confirm"))
    r = add("restore")
    r.add_argument("id", type=cli._backup_id)
    r.add_argument("mode", choices=("describe", "confirm"))
    add("button").add_argument("data")
    return p


def decide_button(ctx, data) -> tuple[str, str | None, list[str] | None]:
    """(toast, status label, op argv to run). The label replaces the buttons in place; None
    keeps them. Each button names what it acts on and is checked against the current
    state, so an old button never acts on a different target. Rollback and restore
    buttons carry the minute they were sent and expire after an hour."""
    kind, _, arg = (data or "").partition(":")
    if kind == "done":
        return "Already handled", None, None
    st = state.load(ctx.paths)
    if kind in ("ap", "rj") and ctx.app.is_release(arg):
        if kind == "rj":
            if cli.reject(ctx, arg).startswith("Busy"):
                return "Busy, try again in a minute", None, None
            return "Rejected", f"❌ Rejected {arg}", None
        if (st.get("pending") or {}).get("tag") != arg:
            return STALE, "⌛ Out of date", None
        return "Deploying", f"✅ Approved — deploying {arg}", ["deploy", arg]
    bid, _, minute = arg.partition(":")
    if kind == "rb" and (arg == "resume" or ID_RE.match(bid)):
        resume = rollback.needs_resume(ctx, st)
        t = None if resume else rollback.target(ctx, st)
        if (arg == "resume" and resume) or (t and t[0] == bid and rollback.fresh(ctx, minute)):
            return "Rolling back", "↩️ Rolling back…", ["rollback", "confirm"]
        return STALE, "⌛ Out of date", None
    if kind == "rs" and ID_RE.match(bid):
        if not rollback.describe_restore_buttons(ctx, bid) or not rollback.fresh(ctx, minute):
            return STALE, "⌛ Out of date", None
        return "Restoring", f"↩️ Restoring {bid}…", ["restore", bid, "confirm"]
    return "Unknown button", None, None


def _reply(out, text: str, buttons=()) -> int:
    emit(out, "reply", text=text, buttons=_rows(buttons))
    return 0


def _run(ctx, args, out) -> int:
    o = args.op
    if o in ("deploy", "reject") and not ctx.app.is_release(args.tag):
        print(f"talaria op: {args.tag} is not a {ctx.app.title} release tag", file=sys.stderr)
        return 2
    if o == "hello":
        emit(out, "hello", protocol=PROTOCOL, app=ctx.app.name, title=ctx.app.title,
             version=__version__)
        return 0
    if o == "status":
        return _reply(out, status.status_text(ctx))
    if o == "backups":
        return _reply(out, status.backups_text(ctx))
    if o == "interrupted":
        text = status.interrupted_text(ctx, state.load(ctx.paths))
        return _reply(out, text) if text else 0
    if o == "reject":
        return _reply(out, cli.reject(ctx, args.tag))
    if o == "button":
        toast, label, run = decide_button(ctx, args.data)
        emit(out, "button", toast=toast, status=label, run=run)
        return 0
    if o == "rollback" and args.mode == "describe":
        return _reply(out, rollback.describe(ctx), rollback.describe_buttons(ctx))
    if o == "restore" and args.mode == "describe":
        return _reply(out, rollback.describe_restore(ctx, args.id),
                      rollback.describe_restore_buttons(ctx, args.id))
    if o == "check":
        ns = argparse.Namespace(cmd="check", timer=args.timer)
    elif o == "deploy":
        ns = argparse.Namespace(cmd="deploy", tag=args.tag, timer=False)
    elif o == "rollback":
        ns = argparse.Namespace(cmd="rollback", confirm=True, timer=False)
    else:
        ns = argparse.Namespace(cmd="restore", id=args.id, confirm=True, timer=False)
    return cli.run_locked(ctx, ns)


def main(argv: list[str], make=None, out=None) -> int:
    out = out or sys.stdout          # the JSON channel; everything else goes to stderr
    if not argv or argv[0] not in OPS:
        print(f"talaria op: not allowed: {' '.join(argv)[:80]!r}", file=sys.stderr)
        return 2
    try:
        args = build_parser().parse_args(argv)
    except SystemExit:
        return 2
    with contextlib.redirect_stdout(sys.stderr):
        if make is None:
            from talaria.ctx import make_ctx as make
        ctx = make()
        ctx.notify = JsonNotifier(out)
        return _run(ctx, args, out)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS (`tests/test_telegram.py` still tests the v0.4 bot; it is replaced in Task 5).

- [ ] **Step 5: Commit**

```bash
git add talaria/op.py talaria/cli.py talaria/check.py tests/test_op.py tests/test_check.py
git commit -m "talaria op: strict door for the hub, JSON lines, button decision"
```

---
### Task 3: Executors and the hub registry

**Files:**
- Create: `talaria/hubexec.py`, `tests/hubfakes.py`, `tests/test_hubexec.py`
- Test: `tests/test_hubexec.py`

**Interfaces:**
- Consumes: `talaria.ctx.make_hub_ctx(home, sh=None)`, `Paths`, `Ctx`; `talaria.conf.load_conf(paths)`; `talaria.apps.get(name).title`; `talaria.notify.TelegramNotifier`.
- Produces:
  - `hubexec.Unreachable(Exception)` (sudo refused: op rule missing), `hubexec.NoAnswer(Exception)` (quick op timed out). Both take the app name as their argument.
  - `hubexec.parse_lines(text: str, app: str) -> list[dict]` (keeps objects with `"v": 1` and a string `"kind"`; logs every other non-blank line to stderr as `[talaria] <app>: ignored output: …`).
  - `hubexec.SudoExecutor(app: str, user: str, home: str, sudo: str = "sudo")` and `hubexec.LocalExecutor(app: str, bin_link)`; both have `.app`, `.argv(op_argv) -> list[str]`, `.call(op_argv, timeout: float = 60) -> list[dict]`, `.stream(op_argv) -> Iterator[dict]`, `.returncode: int | None` (set after `call`, and after `stream` is exhausted). `call` raises `NoAnswer` on timeout; both raise `Unreachable` on a sudo refusal (`stream`: after the last line).
  - `hubexec.AppEntry(name: str, title: str, executor)` (frozen dataclass).
  - `hubexec.Hub(ctx, apps: dict[str, AppEntry], transitional: bool = False)`.
  - `hubexec.load_hub(home: Path | None = None, *, getpwnam=pwd.getpwnam, sh=None) -> Hub | None`.
  - Test helpers in `tests/hubfakes.py`: `FakeExecutor(app="hermes", log=None)` with `.on(*prefix, lines=(), rc=0, exc=None)`, `.calls: list[tuple[str, list[str], float | None]]` (`("call"|"stream", argv, timeout)`), `.ops() -> list[list[str]]`; `line(kind, **f)`, `reply(text, buttons=())`, `message(text, blocks=(), commands=(), buttons=())`, `hello(protocol=1, app="hermes")`; `make_hub(tmp_path, names=("hermes",), transitional=False, log=None) -> Hub` (ctx from `make_test_ctx`, `telegram_user_id=42`, `telegram_token="t"`); `ex(hub, name) -> FakeExecutor`.

- [ ] **Step 1: Write the test helpers and the failing tests**

```python
# tests/hubfakes.py
"""Fakes for the hub side: an executor answering `op` calls from rules, and a Hub."""
from talaria import apps
from talaria.hubexec import AppEntry, Hub
from tests.fakes import make_test_ctx


def line(kind, **fields):
    return {"v": 1, "kind": kind, **fields}


def _rows(buttons):
    return [[list(b) for b in row] for row in buttons]


def reply(text, buttons=()):
    return line("reply", text=text, buttons=_rows(buttons))


def message(text, blocks=(), commands=(), buttons=()):
    return line("message", text=text, blocks=list(blocks), commands=list(commands),
                buttons=_rows(buttons))


def hello(protocol=1, app="hermes"):
    return line("hello", protocol=protocol, app=app, title=apps.get(app).title,
                version="0.5.0")


class FakeExecutor:
    """Stands in for SudoExecutor/LocalExecutor. Rules match an op argv prefix; the most
    recently added matching rule wins. `exc` is raised before any line by call() and
    after the lines by stream(), like the real executors."""

    def __init__(self, app="hermes", log=None):
        self.app, self.rules, self.calls, self.returncode, self.log = app, [], [], None, log

    def on(self, *prefix, lines=(), rc=0, exc=None):
        self.rules.insert(0, (tuple(prefix), list(lines), rc, exc))
        return self

    def _rule(self, argv):
        for prefix, lines, rc, exc in self.rules:
            if tuple(argv[:len(prefix)]) == prefix:
                return lines, rc, exc
        raise AssertionError(f"unexpected op for {self.app}: {argv}")

    def _note(self, how, argv, timeout):
        self.calls.append((how, list(argv), timeout))
        if self.log is not None:
            self.log.append((self.app, how, tuple(argv)))

    def call(self, argv, timeout=60):
        self._note("call", argv, timeout)
        lines, rc, exc = self._rule(list(argv))
        if exc:
            raise exc
        self.returncode = rc
        return list(lines)

    def stream(self, argv):
        self._note("stream", argv, None)
        lines, rc, exc = self._rule(list(argv))
        yield from lines
        self.returncode = rc
        if exc:
            raise exc

    def ops(self):
        return [argv for _, argv, _ in self.calls]


def make_hub(tmp_path, names=("hermes",), transitional=False, log=None):
    ctx = make_test_ctx(tmp_path, telegram_user_id=42, telegram_token="t")
    entries = {n: AppEntry(n, apps.get(n).title, FakeExecutor(n, log)) for n in names}
    return Hub(ctx=ctx, apps=entries, transitional=transitional)


def ex(hub, name):
    return hub.apps[name].executor
```

```python
# tests/test_hubexec.py
import os
import threading
from types import SimpleNamespace

import pytest

from talaria import hubexec
from talaria.ctx import Paths
from talaria.hubconf import HubConf
from talaria.hubexec import LocalExecutor, NoAnswer, SudoExecutor, Unreachable, load_hub


def script(tmp_path, body, name="talaria"):
    p = tmp_path / name
    p.write_text("#!/bin/bash\n" + body + "\n")
    p.chmod(0o755)
    return p


def test_parse_lines_keeps_protocol_objects_and_logs_the_rest(capsys):
    text = ('{"v": 1, "kind": "reply", "text": "hi"}\n\n{"v": 2, "kind": "reply"}\n'
            '[1]\nnot json\n{"v": 1}\n')
    assert hubexec.parse_lines(text, "hermes") == [{"v": 1, "kind": "reply", "text": "hi"}]
    err = capsys.readouterr().err.splitlines()
    assert err == ['[talaria] hermes: ignored output: {"v": 2, "kind": "reply"}',
                   "[talaria] hermes: ignored output: [1]",
                   "[talaria] hermes: ignored output: not json",
                   '[talaria] hermes: ignored output: {"v": 1}']


def test_call_returns_lines_and_exit_code(tmp_path, capsys):
    s = script(tmp_path, """echo '{"v": 1, "kind": "reply", "text": "hi"}'; echo noise
echo oops >&2; exit 3""")
    e = LocalExecutor("hermes", s)
    assert e.call(["status"]) == [{"v": 1, "kind": "reply", "text": "hi"}]
    assert e.returncode == 3
    err = capsys.readouterr().err
    assert "oops" in err and "[talaria] hermes: ignored output: noise" in err


def test_local_executor_argv_and_arguments(tmp_path):
    s = script(tmp_path, """printf '{"v": 1, "kind": "reply", "text": "%s"}\\n' "$*" """)
    e = LocalExecutor("clawvisor", s)
    assert e.argv(["button", "ap:v1"]) == [str(s), "op", "button", "ap:v1"]
    assert e.call(["button", "ap:v1"])[0]["text"] == "op button ap:v1"


def test_executor_runs_from_root(tmp_path, monkeypatch):
    s = script(tmp_path, """printf '{"v": 1, "kind": "reply", "text": "%s"}\\n' "$PWD" """)
    monkeypatch.chdir(tmp_path)
    assert LocalExecutor("hermes", s).call(["status"])[0]["text"] == "/"
    assert [d["text"] for d in LocalExecutor("hermes", s).stream(["status"])] == ["/"]


def test_call_timeout_is_no_answer(tmp_path):
    s = script(tmp_path, "sleep 5")
    with pytest.raises(NoAnswer):
        LocalExecutor("hermes", s).call(["status"], timeout=0.3)


def test_stream_yields_each_line_then_sets_the_exit_code(tmp_path):
    s = script(tmp_path, """echo '{"v": 1, "kind": "message", "text": "a"}'
echo '{"v": 1, "kind": "message", "text": "b"}'; exit 1""")
    e = LocalExecutor("hermes", s)
    got = []
    for d in e.stream(["deploy", "v1"]):
        got.append(d["text"])
        assert e.returncode is None or got == ["a", "b"]
    assert got == ["a", "b"] and e.returncode == 1


def test_stream_survives_lots_of_stderr(tmp_path):
    s = script(tmp_path, """head -c 2000000 /dev/zero | tr '\\0' x >&2
echo '{"v": 1, "kind": "message", "text": "done"}'""")
    got = []
    t = threading.Thread(target=lambda: got.extend(LocalExecutor("hermes", s).stream(["check"])))
    t.start()
    t.join(30)
    assert not t.is_alive(), "relay deadlocked on stderr"
    assert [d["text"] for d in got] == ["done"]


def test_sudo_executor_argv():
    e = SudoExecutor("hermes", "hermes", "/home/hermes")
    assert e.argv(["status"]) == ["sudo", "-n", "-H", "-u", "hermes",
                                  "/home/hermes/.local/bin/talaria", "op", "status"]


def test_sudo_refusal_is_unreachable(tmp_path):
    fake = script(tmp_path, 'echo "sudo: a password is required" >&2; exit 1', name="sudo")
    e = SudoExecutor("hermes", "hermes", "/home/hermes", sudo=str(fake))
    with pytest.raises(Unreachable):
        e.call(["status"])
    with pytest.raises(Unreachable):
        list(e.stream(["deploy", "v1"]))


def test_op_failure_is_not_a_sudo_refusal(tmp_path):
    fake = script(tmp_path, 'echo "Traceback (most recent call last):" >&2; exit 1', name="sudo")
    e = SudoExecutor("hermes", "hermes", "/home/hermes", sudo=str(fake))
    assert e.call(["status"]) == [] and e.returncode == 1
    assert list(e.stream(["status"])) == [] and e.returncode == 1


# ---- load_hub ----

def pw(home):
    return SimpleNamespace(pw_dir=home)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_no_conf_is_no_hub(tmp_path):
    assert load_hub(tmp_path) is None


def test_app_under_a_hub_is_no_hub(tmp_path):
    write(Paths(tmp_path).conf_file, "app = hermes\n")
    assert load_hub(tmp_path) is None


def test_hub(tmp_path):
    p = Paths(tmp_path)
    write(p.hub_conf, "apps = hermes:hermes clawvisor:cv\n")
    write(p.env_file, "TALARIA_TELEGRAM_TOKEN=1:abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    homes = {"hermes": "/home/hermes", "cv": "/srv/cv"}
    h = load_hub(tmp_path, getpwnam=lambda u: pw(homes[u]), sh="SH")
    assert h.transitional is False and isinstance(h.ctx.conf, HubConf)
    assert h.ctx.sh == "SH" and h.ctx.paths.home == tmp_path and h.ctx.app is None
    assert list(h.apps) == ["hermes", "clawvisor"]
    cv = h.apps["clawvisor"]
    assert (cv.name, cv.title) == ("clawvisor", "Clawvisor")
    assert cv.executor.argv(["hello"]) == ["sudo", "-n", "-H", "-u", "cv",
                                           "/srv/cv/.local/bin/talaria", "op", "hello"]


def test_hub_conf_wins_over_an_own_token(tmp_path):
    p = Paths(tmp_path)
    write(p.hub_conf, "")
    write(p.conf_file, "app = hermes\n")
    write(p.env_file, "TALARIA_TELEGRAM_TOKEN=1:abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    h = load_hub(tmp_path)
    assert h.transitional is False and h.apps == {}


def test_hub_with_a_vanished_account(tmp_path):
    write(Paths(tmp_path).hub_conf, "apps = hermes:gone\n")

    def getpwnam(u):
        raise KeyError(u)
    with pytest.raises(ValueError, match="registers hermes for gone, but that account does not exist"):
        load_hub(tmp_path, getpwnam=getpwnam)


def test_transitional_v04_install(tmp_path):
    p = Paths(tmp_path)
    write(p.conf_file, "app = clawvisor\n")
    write(p.env_file, "TALARIA_TELEGRAM_TOKEN=1:abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    h = load_hub(tmp_path)
    assert h.transitional is True
    assert h.ctx.app.name == "clawvisor" and h.ctx.paths.app == "clawvisor"
    assert h.ctx.conf.telegram_user_id == 42
    (e,) = h.apps.values()
    assert (e.name, e.title) == ("clawvisor", "Clawvisor")
    assert e.executor.argv(["hello"]) == [str(tmp_path / ".local/bin/talaria"), "op", "hello"]


def test_token_without_owner_is_no_hub(tmp_path):
    p = Paths(tmp_path)
    write(p.conf_file, "app = hermes\n")
    write(p.env_file, "TALARIA_TELEGRAM_TOKEN=1:abc\n")
    assert load_hub(tmp_path) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_hubexec.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'talaria.hubexec'`.

- [ ] **Step 3: Implement `talaria/hubexec.py`**

```python
"""How the hub reaches an app: `talaria op …` as the app's account (spec §4, §5.3)."""
from __future__ import annotations

import json
import pwd
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from talaria import apps


class Unreachable(Exception):
    """sudo refused: the hub's op rule for this app is missing."""


class NoAnswer(Exception):
    """A quick op did not finish in time."""


def parse_lines(text: str, app: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            d = None
        if isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("kind"), str):
            out.append(d)
        else:
            print(f"[talaria] {app}: ignored output: {line[:200]}", file=sys.stderr)
    return out


class _Executor:
    app = ""
    returncode: int | None = None

    def argv(self, op_argv) -> list[str]:
        raise NotImplementedError

    def _refused(self, rc, err: str) -> bool:
        return False

    def call(self, op_argv, timeout: float = 60) -> list[dict]:
        """A quick op: wait for it (up to `timeout` seconds), return its lines."""
        self.returncode = None
        try:
            p = subprocess.run(self.argv(op_argv), capture_output=True, text=True,
                               timeout=timeout, cwd="/")
        except subprocess.TimeoutExpired:
            raise NoAnswer(self.app) from None
        self.returncode = p.returncode
        if p.stderr:
            sys.stderr.write(p.stderr)
        if self._refused(p.returncode, p.stderr):
            raise Unreachable(self.app)
        return parse_lines(p.stdout, self.app)

    def stream(self, op_argv) -> Iterator[dict]:
        """A long op: yield each line as it arrives. stderr goes to a file, never a pipe,
        so a chatty op cannot block on it while we read stdout."""
        self.returncode = None
        with tempfile.TemporaryFile(mode="w+") as err:
            p = subprocess.Popen(self.argv(op_argv), stdout=subprocess.PIPE, stderr=err,
                                 text=True, cwd="/")
            try:
                for line in p.stdout:
                    yield from parse_lines(line, self.app)
            finally:
                p.stdout.close()
                self.returncode = p.wait()
                err.seek(0)
                text = err.read()
                if text:
                    sys.stderr.write(text)
        if self._refused(self.returncode, text):
            raise Unreachable(self.app)


class SudoExecutor(_Executor):
    """The hub's way in: one sudo rule per app allows exactly `<home>/.local/bin/talaria op *`."""

    def __init__(self, app: str, user: str, home, sudo: str = "sudo"):
        self.app, self.user, self.home, self.sudo = app, user, str(home), sudo

    def argv(self, op_argv) -> list[str]:
        return [self.sudo, "-n", "-H", "-u", self.user, f"{self.home}/.local/bin/talaria",
                "op", *op_argv]

    def _refused(self, rc, err: str) -> bool:
        return rc == 1 and err.lstrip().startswith("sudo:")


class LocalExecutor(_Executor):
    """Transitional mode (spec §7.5): a v0.4 app account still runs its own bot, so op
    runs as the same account, without sudo."""

    def __init__(self, app: str, bin_link):
        self.app, self.bin = app, str(bin_link)

    def argv(self, op_argv) -> list[str]:
        return [self.bin, "op", *op_argv]


@dataclass(frozen=True)
class AppEntry:
    name: str
    title: str
    executor: object


@dataclass
class Hub:
    ctx: object                       # this account's Ctx: telegram_*, check_time, talaria_repo
    apps: dict = field(default_factory=dict)   # name -> AppEntry, in hub.conf order
    transitional: bool = False


def load_hub(home: Path | None = None, *, getpwnam=pwd.getpwnam, sh=None) -> Hub | None:
    """This account as a hub: a real hub (hub.conf exists), a v0.4-layout app install that
    still holds its own bot token and owner (transitional), or None (an app under a hub)."""
    from talaria.conf import load_conf
    from talaria.ctx import Ctx, Paths, make_hub_ctx
    from talaria.notify import TelegramNotifier
    from talaria.shell import Shell

    home = Path(home) if home else Path.home()
    paths = Paths(home)
    if paths.hub_conf.exists():
        ctx = make_hub_ctx(home, sh=sh)
        reg = {}
        for app, user in ctx.conf.apps:
            try:
                pw = getpwnam(user)
            except KeyError:
                raise ValueError(f"{paths.hub_conf} registers {app} for {user}, but that "
                                 "account does not exist") from None
            reg[app] = AppEntry(app, apps.get(app).title, SudoExecutor(app, user, pw.pw_dir))
        return Hub(ctx, reg)
    if not paths.conf_file.exists():
        return None
    conf = load_conf(paths)
    if not conf.telegram_token or not conf.telegram_user_id:
        return None
    a = apps.get(conf.app)
    paths = Paths(home, conf.app)
    ctx = Ctx(paths=paths, conf=conf, sh=sh or Shell(), notify=TelegramNotifier(conf), app=a)
    return Hub(ctx, {a.name: AppEntry(a.name, a.title, LocalExecutor(a.name, paths.bin_link))},
               transitional=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_hubexec.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add talaria/hubexec.py tests/hubfakes.py tests/test_hubexec.py
git commit -m "Hub executors: sudo and local op runners, registry, transitional detection"
```

---

### Task 4: Relay — op output to Telegram

**Files:**
- Create: `talaria/relay.py`, `tests/test_relay.py`
- Test: `tests/test_relay.py`

**Interfaces:**
- Consumes: `hubexec.Unreachable`, `hubexec.NoAnswer`, `Hub`, `AppEntry` (`.name`, `.title`, `.executor` with `.call(argv, timeout=60)`, `.stream(argv)`, `.returncode`); `op.PROTOCOL`; `notify.Message`, `notify.keyboard`; test helpers `make_hub`, `ex`, `FakeExecutor.on`, `reply`, `message`, `hello` (Task 3).
- Produces:
  - `relay.VERSIONS = "Talaria versions differ on this host; run /update"`.
  - `relay.unreachable_text(title) -> str` = `"<Title>: Talaria cannot reach this app (sudo rule missing); run setup again"`; `relay.no_answer_text(title) -> str` = `"<Title> did not answer in time"`.
  - `relay.prefix(app, rows) -> list` (rows of `(label, "<app>|<data>")` tuples); `relay.with_app(app, commands) -> list[str]`; `relay.to_message(app, d: dict) -> Message`.
  - `relay.quick(entry, argv, timeout=60) -> tuple[str, list]` (reply text and prefixed buttons, or an error text and `[]`; `("", [])` when the op replied nothing and exited 0).
  - `relay.hello(entry) -> str | None` (None = same protocol; `VERSIONS`; or an unreachable/no-answer text).
  - `relay.status_all(hub, mismatch=frozenset()) -> str`.
  - `relay.relay(hub, app, argv) -> int` (streams, sends each `message` via `hub.ctx.notify`, sends `"<Title>: <argv joined> failed unexpectedly (exit N)"` when the op exits non-zero without a message; returns the exit code, 1 when unreachable).
  - `relay.main(hub, app: str, argv: list[str]) -> int` (2 for an unknown app or empty argv).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_relay.py
import pytest

from talaria import relay
from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import keyboard
from tests.hubfakes import ex, hello, make_hub, message, reply


def test_to_message_prefixes_buttons_and_names_the_app_in_commands():
    m = relay.to_message("clawvisor", message(
        "Clawvisor v0.9.10 is ready to deploy", blocks=[["Migrations", "055.sql"], "raw"],
        commands=["/approve v0.9.10", "/reject v0.9.10", "/rollback"],
        buttons=[[("Approve v0.9.10", "ap:v0.9.10"), ("Reject", "rj:v0.9.10")]]))
    assert m.text == "Clawvisor v0.9.10 is ready to deploy"
    assert m.untrusted == [("Migrations", "055.sql"), "raw"]
    assert m.commands == ["/approve clawvisor v0.9.10", "/reject clawvisor v0.9.10",
                          "/rollback clawvisor"]
    assert m.buttons == [[("Approve v0.9.10", "clawvisor|ap:v0.9.10"),
                          ("Reject", "clawvisor|rj:v0.9.10")]]


def test_to_message_tolerates_missing_fields():
    m = relay.to_message("hermes", {"v": 1, "kind": "message"})
    assert (m.text, m.untrusted, m.commands, m.buttons) == ("", [], [], [])


def test_with_app_leaves_non_commands_alone():
    assert relay.with_app("hermes", ["see README", "/status"]) == ["see README", "/status hermes"]


@pytest.mark.parametrize("app,data", [
    ("hermes", "rb:20261231T235959Z-pre-v2026.12.31.99-2:29999999"),
    ("hermes", "rs:20261231T235959Z-pre-v2026.12.31.99-2:29999999"),
    ("clawvisor", "rb:20261231T235959Z-pre-rollback-12:29999999"),
    ("clawvisor", "ap:v10.100.100"),
])
def test_longest_real_callback_data_still_fits(app, data):
    rows = relay.prefix(app, [[("x", data)]])
    assert len(rows[0][0][1].encode()) <= 64
    assert keyboard(rows) == {"inline_keyboard": [[{"text": "x", "callback_data": f"{app}|{data}"}]]}


@pytest.fixture
def h(tmp_path):
    return make_hub(tmp_path, ("hermes", "clawvisor"))


def test_relay_sends_each_message_as_it_arrives(h):
    ex(h, "hermes").on("deploy", lines=[message("Deploying"), reply("ignored"),
                                        message("Deployed Hermes v2026.9.24.", commands=["/rollback"])])
    assert relay.relay(h, "hermes", ["deploy", "v2026.9.24"]) == 0
    assert h.ctx.notify.texts() == ["Deploying", "Deployed Hermes v2026.9.24."]
    assert h.ctx.notify.sent[1].commands == ["/rollback hermes"]
    assert ex(h, "hermes").calls == [("stream", ["deploy", "v2026.9.24"], None)]


def test_crash_without_a_message_is_reported(h):
    ex(h, "hermes").on("deploy", rc=1)
    assert relay.relay(h, "hermes", ["deploy", "v2026.9.24"]) == 1
    assert h.ctx.notify.texts() == ["Hermes: deploy v2026.9.24 failed unexpectedly (exit 1)"]


def test_failure_after_a_message_adds_nothing(h):
    ex(h, "clawvisor").on("rollback", lines=[message("Busy: another operation is running.")], rc=75)
    assert relay.relay(h, "clawvisor", ["rollback", "confirm"]) == 75
    assert h.ctx.notify.texts() == ["Busy: another operation is running."]


def test_unreachable_app(h):
    ex(h, "clawvisor").on("check", exc=Unreachable("clawvisor"))
    assert relay.relay(h, "clawvisor", ["check"]) == 1
    assert h.ctx.notify.texts() == [
        "Clawvisor: Talaria cannot reach this app (sudo rule missing); run setup again"]


def test_quick(h):
    e = h.apps["hermes"]
    ex(h, "hermes").on("rollback", lines=[reply("would restore", [[("Roll back", "rb:B:1")]])])
    assert relay.quick(e, ["rollback", "describe"]) == ("would restore",
                                                        [[("Roll back", "hermes|rb:B:1")]])
    assert ex(h, "hermes").calls[-1] == ("call", ["rollback", "describe"], 60)
    ex(h, "hermes").on("interrupted")
    assert relay.quick(e, ["interrupted"]) == ("", [])
    ex(h, "hermes").on("status", rc=2)
    assert relay.quick(e, ["status"]) == ("Hermes: status failed unexpectedly (exit 2)", [])
    ex(h, "hermes").on("status", exc=Unreachable("hermes"))
    assert relay.quick(e, ["status"])[0] == relay.unreachable_text("Hermes")
    ex(h, "hermes").on("status", exc=NoAnswer("hermes"))
    assert relay.quick(e, ["status"]) == ("Hermes did not answer in time", [])


def test_hello(h):
    e = h.apps["clawvisor"]
    ex(h, "clawvisor").on("hello", lines=[hello(app="clawvisor")])
    assert relay.hello(e) is None
    ex(h, "clawvisor").on("hello", lines=[hello(protocol=2, app="clawvisor")])
    assert relay.hello(e) == relay.VERSIONS
    ex(h, "clawvisor").on("hello", rc=2)
    assert relay.hello(e) == "Talaria versions differ on this host; run /update"
    ex(h, "clawvisor").on("hello", exc=Unreachable("clawvisor"))
    assert relay.hello(e) == relay.unreachable_text("Clawvisor")
    ex(h, "clawvisor").on("hello", exc=NoAnswer("clawvisor"))
    assert relay.hello(e) == "Clawvisor did not answer in time"


def test_status_all(h, tmp_path):
    ex(h, "hermes").on("status", lines=[reply("Hermes v1, running.")])
    ex(h, "clawvisor").on("status", lines=[reply("Clawvisor v0.9.10, running.")])
    assert relay.status_all(h) == "Hermes v1, running.\n\nClawvisor v0.9.10, running."
    assert relay.status_all(h, {"clawvisor"}) == (
        "Hermes v1, running.\n\nClawvisor: Talaria versions differ on this host; run /update")
    h.apps.clear()
    assert relay.status_all(h) == "No apps registered."


def test_main_refuses_an_unknown_app_or_no_op(h, capsys):
    assert relay.main(h, "nope", ["status"]) == 2
    assert relay.main(h, "hermes", []) == 2
    assert capsys.readouterr().err == ("talaria relay: unknown app 'nope'; apps: hermes, clawvisor\n"
                                       "talaria relay: no operation given\n")
    ex(h, "hermes").on("check")
    assert relay.main(h, "hermes", ["check", "--timer"]) == 0
    assert ex(h, "hermes").ops() == [["check", "--timer"]]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_relay.py`
Expected: FAIL with `ImportError: cannot import name 'relay'`.

- [ ] **Step 3: Implement `talaria/relay.py`**

```python
"""Turns `talaria op` output into Telegram content for the hub (spec §4.3, §5.3)."""
from __future__ import annotations

import sys

from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import Message
from talaria.op import PROTOCOL

VERSIONS = "Talaria versions differ on this host; run /update"


def unreachable_text(title: str) -> str:
    return f"{title}: Talaria cannot reach this app (sudo rule missing); run setup again"


def no_answer_text(title: str) -> str:
    return f"{title} did not answer in time"


def prefix(app: str, rows) -> list:
    """Callback data names its app, so the hub knows where a tap goes (spec §5.2)."""
    return [[(str(label), f"{app}|{data}") for label, data in row] for row in rows or []]


def with_app(app: str, commands) -> list[str]:
    """`/approve v1` → `/approve hermes v1`: typed commands work with several apps."""
    out = []
    for c in commands or []:
        head, sep, rest = str(c).partition(" ")
        out.append(f"{head} {app}{sep}{rest}" if head.startswith("/") else str(c))
    return out


def _block(b):
    if isinstance(b, list) and len(b) == 2:
        return (str(b[0]), str(b[1]))
    return str(b)


def to_message(app: str, d: dict) -> Message:
    return Message(str(d.get("text") or ""), untrusted=[_block(b) for b in d.get("blocks") or []],
                   commands=with_app(app, d.get("commands")), buttons=prefix(app, d.get("buttons")))


def quick(entry, argv: list[str], timeout: float = 60) -> tuple[str, list]:
    try:
        lines = entry.executor.call(argv, timeout=timeout)
    except Unreachable:
        return unreachable_text(entry.title), []
    except NoAnswer:
        return no_answer_text(entry.title), []
    for d in lines:
        if d.get("kind") == "reply":
            return str(d.get("text") or ""), prefix(entry.name, d.get("buttons"))
    rc = entry.executor.returncode
    if rc:
        return f"{entry.title}: {argv[0]} failed unexpectedly (exit {rc})", []
    return "", []


def hello(entry) -> str | None:
    try:
        lines = entry.executor.call(["hello"])
    except Unreachable:
        return unreachable_text(entry.title)
    except NoAnswer:
        return no_answer_text(entry.title)
    if any(d.get("kind") == "hello" and d.get("protocol") == PROTOCOL for d in lines):
        return None
    return VERSIONS


def status_all(hub, mismatch=frozenset()) -> str:
    blocks = [f"{e.title}: {VERSIONS}" if name in mismatch else quick(e, ["status"])[0]
              for name, e in hub.apps.items()]
    return "\n\n".join(b for b in blocks if b) or "No apps registered."


def relay(hub, app: str, argv: list[str]) -> int:
    e = hub.apps[app]
    sent = False
    try:
        for d in e.executor.stream(argv):
            if d.get("kind") == "message":
                hub.ctx.notify.send(to_message(app, d))
                sent = True
    except Unreachable:
        hub.ctx.notify.send(Message(unreachable_text(e.title)))
        return 1
    rc = e.executor.returncode or 0
    if rc != 0 and not sent:
        hub.ctx.notify.send(Message(f"{e.title}: {' '.join(argv)} failed unexpectedly (exit {rc})"))
    return rc


def main(hub, app: str, argv: list[str]) -> int:
    if app not in hub.apps:
        print(f"talaria relay: unknown app {app!r}; apps: {', '.join(hub.apps)}", file=sys.stderr)
        return 2
    if not argv:
        print("talaria relay: no operation given", file=sys.stderr)
        return 2
    return relay(hub, app, argv)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_relay.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add talaria/relay.py tests/test_relay.py
git commit -m "Relay: op JSON lines to Telegram, app-prefixed buttons and commands"
```

---
### Task 5: The hub bot

**Files:**
- Modify: `talaria/telegram.py` (Bot over a registry; `run(hub)`)
- Test: `tests/test_telegram.py` (replaced)

**Interfaces:**
- Consumes: `Hub` (`.ctx` with `.conf.telegram_user_id`, `.conf.telegram_api`, `.conf.telegram_token`, `.paths.bin_link`, `.sh`, `.notify`, `.now`; `.apps: dict[str, AppEntry]`), `hubexec.Unreachable`, `hubexec.NoAnswer`; `relay.quick`, `relay.hello`, `relay.status_all`, `relay.unreachable_text`, `relay.no_answer_text`, `relay.VERSIONS`; `op.STALE`; `apps.get(name).is_release(tag)`; `backup.ID_RE`; `tags.SEMVER`, `tags.TAG_ARG`; test helpers `make_hub`, `ex`, `reply`, `hello` (Task 3).
- Produces:
  - `telegram.Bot(hub, api)` with `.hub`, `.ctx` (= `hub.ctx`), `.api`, `.offset`, `.mismatch: set[str]`, `.reply(text, buttons=None)`, `.spawn(name, *argv)` (runs `systemd-run --user --collect --quiet --unit=talaria-<name>-<int(time)> <bin_link> *argv`), `.dispatch(cmd, args) -> str | tuple[str, list] | None`, `.app_command(app, cmd, args)`, `.on_button(data) -> tuple[str, str | None]`, `.hub_button(rest)`, `.handle(update)`, `.handle_button(q)`, `.startup()`, `.poll_once()`.
  - `telegram.run(hub) -> int`; `telegram.pair(ctx, api, code, timeout_s=900, announce=…)` and `telegram.new_code()` unchanged.
  - Constants: `HELP`, `MENU`, `APP_WORD`, `APP_CMDS`, `LONG_OPS = ("check", "deploy", "rollback", "restore")`, `OUT_OF_DATE = "⌛ Out of date"`; `telegram.read_form(cmd, args) -> str | None`.
  - Spawned commands: `relay <app> check`, `relay <app> deploy <tag>`, `relay <app> rollback confirm`, `relay <app> restore <id> confirm` (unit names `talaria-<app>-<op>-<ts>`); `update <tag> --offer` and `self-update <tag>` (unit name `talaria-update-<ts>`).

- [ ] **Step 1: Replace `tests/test_telegram.py` with the hub bot's tests**

```python
# tests/test_telegram.py
import pytest

from talaria import relay, telegram
from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import ApiError
from tests.fakes import make_test_ctx
from tests.hubfakes import ex, hello, line, make_hub, reply

OWNER = 42
ID = "20260927T043000Z-manual"
NU = "Not understood. Commands: " + telegram.HELP


class FakeAPI:
    def __init__(self, ctx, batches=()):
        self.ctx, self.batches, self.calls = ctx, list(batches), []

    def call(self, method, **params):     # same signature as TelegramAPI.call
        self.calls.append((method, params))
        if method == "getUpdates":
            timeout = params.get("timeout", 0)
            if timeout > 0:
                self.ctx.clock.sleep(timeout)
            if len(self.calls) > 1000:
                raise AssertionError("runaway polling loop")
            return self.batches.pop(0) if self.batches else []
        return {"message_id": 1}

    def sent(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]


def upd(uid, text, user=OWNER, chat_type="private"):
    return {"update_id": uid, "message": {"text": text, "chat": {"id": user, "type": chat_type},
                                          "from": {"id": user, "first_name": "Ann",
                                                   "username": "ann"}}}


def cb(data, user=OWNER, chat_type="private", mid=55):
    return {"update_id": 9, "callback_query": {
        "id": "q1", "data": data, "from": {"id": user},
        "message": {"message_id": mid, "chat": {"id": user, "type": chat_type}}}}


def status_markup(label):
    return {"chat_id": OWNER, "message_id": 55,
            "reply_markup": {"inline_keyboard": [[{"text": label, "callback_data": "done"}]]}}


def calls(api, method):
    return [p for m, p in api.calls if m == method]


def spawned(ctx):
    return [c[5:] for c in ctx.sh.called("systemd-run")]


def _bot(tmp_path, names):
    hub = make_hub(tmp_path, names)
    hub.ctx.sh.on("systemd-run")
    for n in names:
        ex(hub, n).on("hello", lines=[hello(app=n)]).on("interrupted").on(
            "status", lines=[reply(f"{hub.apps[n].title} v1, running.")])
    api = FakeAPI(hub.ctx)
    b = telegram.Bot(hub, api)
    b.offset = 1
    return hub.ctx, api, b


@pytest.fixture
def bot(tmp_path):
    return _bot(tmp_path, ("hermes",))


@pytest.fixture
def bot2(tmp_path):
    return _bot(tmp_path, ("hermes", "clawvisor"))


def test_new_code():
    c = telegram.new_code()
    assert len(c) == 8 and c.isalnum() and not set(c) & set("0O1IL")


def test_only_owner_in_private_chat(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/status", user=7))
    b.handle(upd(2, "/status", chat_type="group"))
    assert api.sent() == []
    b.handle(upd(3, "/status"))
    assert api.sent() == ["Hermes v1, running."]


def test_status_lists_every_app(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/status"))
    assert api.sent() == ["Hermes v1, running.\n\nClawvisor v1, running."]


def test_status_of_one_app(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/status clawvisor"))
    assert api.sent() == ["Clawvisor v1, running."]


def test_app_name_is_optional_with_one_app(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/approve v2026.9.24"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "relay", "hermes", "deploy", "v2026.9.24"]]
    assert api.sent() == ["Deploying Hermes v2026.9.24. I will report the result."]


def test_spawn_exact(bot, monkeypatch):
    ctx, api, b = bot
    monkeypatch.setattr(telegram.time, "time", lambda: 1234.9)
    b.spawn("hermes-deploy", "relay", "hermes", "deploy", "v2026.9.24")
    assert ctx.sh.calls[-1] == ["systemd-run", "--user", "--collect", "--quiet",
                                "--unit=talaria-hermes-deploy-1234", str(ctx.paths.bin_link),
                                "relay", "hermes", "deploy", "v2026.9.24"]
    assert ctx.sh.timeouts[-1] is None and ctx.paths.bin_link.is_absolute()


@pytest.mark.parametrize("text,reply_text,run", [
    ("/check clawvisor", "Checking Clawvisor for releases.", ["relay", "clawvisor", "check"]),
    ("/approve clawvisor v0.9.10", "Deploying Clawvisor v0.9.10. I will report the result.",
     ["relay", "clawvisor", "deploy", "v0.9.10"]),
    ("/rollback hermes CONFIRM", "Rolling back Hermes. I will report the result.",
     ["relay", "hermes", "rollback", "confirm"]),
    (f"/restore hermes {ID} CONFIRM", f"Restoring Hermes {ID}. I will report the result.",
     ["relay", "hermes", "restore", ID, "confirm"]),
    ("/check@talaria_bot hermes", "Checking Hermes for releases.", ["relay", "hermes", "check"]),
])
def test_long_commands_spawn_the_relay(bot2, text, reply_text, run):
    ctx, api, b = bot2
    b.handle(upd(1, text))
    assert api.sent() == [reply_text]
    assert spawned(ctx) == [[str(ctx.paths.bin_link), *run]]
    unit = ctx.sh.called("systemd-run")[0][4]
    assert unit.startswith(f"--unit=talaria-{run[1]}-{run[2]}-")


def test_quick_commands_ask_the_app(bot2):
    ctx, api, b = bot2
    ex(b.hub, "hermes").on("reject", lines=[reply("Rejected v2026.9.24. It will not be offered again.")])
    ex(b.hub, "hermes").on("rollback", lines=[reply("would restore", [[("Roll back", "rb:B:1")]])])
    ex(b.hub, "clawvisor").on("backups", lines=[reply("No backups yet.")])
    ex(b.hub, "clawvisor").on("restore", lines=[reply(f"No backup {ID}. /backups lists them.")])
    for text in ("/reject hermes v2026.9.24", "/rollback hermes", "/backups clawvisor",
                 f"/restore clawvisor {ID}"):
        b.handle(upd(1, text))
    assert ex(b.hub, "hermes").ops()[-2:] == [["reject", "v2026.9.24"], ["rollback", "describe"]]
    assert ex(b.hub, "clawvisor").ops()[-2:] == [["backups"], ["restore", ID, "describe"]]
    rb = calls(api, "sendMessage")[1]
    assert rb["text"] == "would restore"
    assert rb["reply_markup"] == {"inline_keyboard": [[{"text": "Roll back",
                                                        "callback_data": "hermes|rb:B:1"}]]}
    assert spawned(ctx) == []


@pytest.mark.parametrize("text,form", [
    ("/backups", "backups"), ("/check", "check"), ("/approve v2026.9.24", "status"),
    ("/reject v0.9.10", "status"), ("/rollback", "rollback"), ("/rollback CONFIRM", "rollback"),
    (f"/restore {ID}", f"restore:{ID}"), (f"/restore {ID} CONFIRM", f"restore:{ID}"),
])
def test_which_app(bot2, text, form):
    ctx, api, b = bot2
    b.handle(upd(1, text))
    (params,) = calls(api, "sendMessage")
    assert params["text"] == "Which app?"
    assert params["reply_markup"] == {"inline_keyboard": [[
        {"text": "Hermes", "callback_data": f"hub|w:hermes:{form}"},
        {"text": "Clawvisor", "callback_data": f"hub|w:clawvisor:{form}"}]]}
    assert spawned(ctx) == []
    assert all(o in (["hello"], ["interrupted"]) for n in b.hub.apps for o in ex(b.hub, n).ops())


def test_which_app_restore_button_fits():
    assert len(f"hub|w:clawvisor:restore:20261231T235959Z-pre-v2026.12.31.99-2".encode()) <= 64
    assert telegram.read_form("/restore", ["20261231T235959Z-pre-v2026.12.31.99-2"]) == \
        "restore:20261231T235959Z-pre-v2026.12.31.99-2"


def test_which_app_button_runs_the_read_form_never_a_confirm(bot2):
    ctx, api, b = bot2
    ex(b.hub, "hermes").on("rollback", lines=[reply("would restore", [[("Roll back", "rb:B:1")]])])
    ex(b.hub, "clawvisor").on("restore", lines=[reply("Restore it?")])
    b.handle(cb("hub|w:hermes:rollback"))
    b.handle(cb(f"hub|w:clawvisor:restore:{ID}"))
    b.handle(cb("hub|w:hermes:check"))
    assert ex(b.hub, "hermes").ops()[-1] == ["rollback", "describe"]
    assert ex(b.hub, "clawvisor").ops()[-1] == ["restore", ID, "describe"]
    assert api.sent() == ["would restore", "Restore it?", "Checking Hermes for releases."]
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "relay", "hermes", "check"]]
    assert [p["text"] for p in calls(api, "answerCallbackQuery")] == ["Hermes", "Clawvisor", "Hermes"]
    assert calls(api, "editMessageReplyMarkup") == []


@pytest.mark.parametrize("data", ["hub|w:nope:status", "hub|w:hermes:deploy", "hub|w:hermes:restore:../x",
                                  "hub|w:hermes:status:x", "hub|x", "hub|up:latest"])
def test_bad_hub_buttons(bot2, data):
    ctx, api, b = bot2
    b.handle(cb(data))
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Unknown button"
    assert spawned(ctx) == [] and api.sent() == []


def test_unknown_app(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/backups nope"))
    assert api.sent() == ["Unknown app: nope. Apps: hermes, clawvisor"]


def test_no_apps_registered(tmp_path):
    ctx, api, b = _bot(tmp_path, ())
    b.handle(upd(1, "/backups"))
    b.handle(upd(2, "/status"))
    assert api.sent() == ["No apps registered.", "No apps registered."]


@pytest.mark.parametrize("text", [
    "/help", "/status hermes extra", "/backups hermes x", "/check hermes now", "/reject",
    "/reject v1", "/approve v0.9.10", "/rollback CONFIRM now", f"/restore {ID} confirm",
    "/restore", "/update", "/update latest", "/update v1.2", "/approve $(id)",
])
def test_not_understood(bot, text):
    ctx, api, b = bot
    b.handle(upd(1, text))
    assert api.sent() == [NU] and spawned(ctx) == []


def test_invalid_arguments_never_spawn(bot):
    ctx, api, b = bot
    for text in ["/approve v2026.9.24;rm -rf", "/approve", "/restore ../x CONFIRM",
                 "/rollback confirm", "/approve $(id)", "/update v1.2.3;id"]:
        b.handle(upd(1, text))
    assert spawned(ctx) == []


def test_help_text_exact():
    assert telegram.HELP == ("/status · /check [app] · /approve [app] <tag> · /reject [app] <tag> · "
                             "/rollback [app] [CONFIRM] · /backups [app] · "
                             "/restore [app] <id> [CONFIRM] · /update <version>")


def test_update_command_spawns_the_offer(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/update v0.6.0"))
    assert api.sent() == ["Checking what Talaria v0.6.0 would change. I will send the result."]
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "update", "v0.6.0", "--offer"]]
    assert ctx.sh.called("systemd-run")[0][4].startswith("--unit=talaria-update-")


def test_quick_errors_name_the_app(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("status", exc=Unreachable("hermes"))
    b.handle(upd(1, "/status"))
    ex(b.hub, "hermes").on("backups", exc=NoAnswer("hermes"))
    b.handle(upd(2, "/backups"))
    assert api.sent() == [relay.unreachable_text("Hermes"), "Hermes did not answer in time"]


def test_versions_differ(bot2):
    ctx, api, b = bot2
    b.mismatch = {"clawvisor"}
    for text in ("/backups clawvisor", "/approve clawvisor v0.9.10", "/status"):
        b.handle(upd(1, text))
    assert api.sent() == [relay.VERSIONS, relay.VERSIONS,
                          "Hermes v1, running.\n\nClawvisor: " + relay.VERSIONS]
    b.handle(cb("clawvisor|ap:v0.9.10"))
    assert calls(api, "answerCallbackQuery")[0]["text"] == relay.VERSIONS
    b.handle(upd(2, "/update v0.6.0"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "update", "v0.6.0", "--offer"]]
    assert ["button", "ap:v0.9.10"] not in ex(b.hub, "clawvisor").ops()


def test_dispatch_error_is_replied(bot, monkeypatch):
    ctx, api, b = bot
    monkeypatch.setattr(telegram.relay, "status_all", lambda h, m: 1 / 0)
    b.handle(upd(1, "/status"))
    assert api.sent() == ["Error: division by zero"]


def test_empty_answer_sends_nothing(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("backups")            # replied nothing, exit 0
    b.handle(upd(1, "/backups"))
    assert api.sent() == []


# ---- startup and polling ----

def test_startup_checks_versions_and_reports_interruptions(bot2):
    ctx, api, b = bot2
    ex(b.hub, "hermes").on("interrupted", lines=[reply("Interrupted deploy v2026.9.24 (3m ago).")])
    ex(b.hub, "clawvisor").on("hello", lines=[hello(protocol=2, app="clawvisor")])
    api.batches = [[upd(10, "/approve v2026.9.24")], []]
    b.startup()
    assert b.offset == 11 and spawned(ctx) == []
    assert b.mismatch == {"clawvisor"}
    assert ctx.notify.texts() == ["Interrupted deploy v2026.9.24 (3m ago)."]
    assert ["interrupted"] not in ex(b.hub, "clawvisor").ops()


def test_startup_marks_a_failing_hello_as_mismatch(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("hello", rc=2)
    b.startup()
    assert b.mismatch == {"hermes"}


def test_startup_reports_an_unreachable_app(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("hello", exc=Unreachable("hermes")).on(
        "interrupted", exc=Unreachable("hermes"))
    b.startup()
    assert b.mismatch == set() and ctx.notify.texts() == [relay.unreachable_text("Hermes")]


def test_startup_registers_the_app_neutral_menu_for_the_owner_only(bot):
    ctx, api, b = bot
    b.startup()
    (params,) = calls(api, "setMyCommands")
    assert params["scope"] == {"type": "chat", "chat_id": OWNER}
    assert [(c["command"], c["description"]) for c in params["commands"]] == telegram.MENU
    assert [c for c, _ in telegram.MENU] == ["status", "check", "approve", "reject", "rollback",
                                             "backups", "restore", "update"]
    assert all(0 < len(d) <= 256 and "Hermes" not in d for _, d in telegram.MENU)


def test_menu_failure_does_not_stop_startup(bot):
    ctx, api, b = bot
    real = api.call

    def call(method, **p):
        if method == "setMyCommands":
            raise ApiError(400, None)
        return real(method, **p)

    api.call = call
    b.startup()          # no exception


def test_startup_without_backlog(bot):
    ctx, api, b = bot
    api.batches = [[]]
    b.startup()
    assert b.offset is None and [c for c in api.calls if c[0] == "getUpdates"] == [
        ("getUpdates", {"offset": -1, "timeout": 0})]
    assert ctx.notify.sent == []


def test_startup_ack_exact(bot):
    ctx, api, b = bot
    api.batches = [[upd(4, "x")], []]
    b.startup()
    assert [c for c in api.calls if c[0] == "getUpdates"] == [
        ("getUpdates", {"offset": -1, "timeout": 0}), ("getUpdates", {"offset": 5, "timeout": 0})]


def test_poll_advances_offset(bot):
    ctx, api, b = bot
    api.batches = [[upd(5, "/check"), upd(6, "/status")]]
    b.poll_once()
    assert b.offset == 7


def test_poll_once_params_exact(bot):
    ctx, api, b = bot
    b.offset = 7
    b.poll_once()
    assert api.calls[-1] == ("getUpdates", {"offset": 7, "timeout": 25,
                                            "allowed_updates": ["message", "callback_query"]})


def test_reply_exact(bot):
    ctx, api, b = bot
    b.reply("x" * 5000)
    method, params = api.calls[-1]
    assert method == "sendMessage" and params == {"chat_id": OWNER, "text": "x" * 4096}


def test_empty_or_missing_text_is_ignored(bot, capsys):
    ctx, api, b = bot
    b.handle(upd(1, "   "))
    b.handle({"update_id": 2, "message": {"chat": {"type": "private"}, "from": {"id": OWNER}}})
    b.handle({"update_id": 3})
    assert api.sent() == []
    assert capsys.readouterr().err == ("[talaria] ignored update 1\n[talaria] ignored update 2\n"
                                       "[talaria] ignored update 3\n")


# ---- buttons ----

def test_prefixed_button_asks_the_app_and_runs_its_answer(bot2):
    ctx, api, b = bot2
    ex(b.hub, "clawvisor").on("button", lines=[line(
        "button", toast="Deploying", status="✅ Approved — deploying v0.9.10",
        run=["deploy", "v0.9.10"])])
    b.handle(cb("clawvisor|ap:v0.9.10"))
    assert ex(b.hub, "clawvisor").calls[-1] == ("call", ["button", "ap:v0.9.10"], 60)
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "relay", "clawvisor", "deploy", "v0.9.10"]]
    assert calls(api, "answerCallbackQuery") == [{"callback_query_id": "q1", "text": "Deploying"}]
    assert calls(api, "editMessageReplyMarkup") == [status_markup("✅ Approved — deploying v0.9.10")]
    assert api.sent() == []


def test_button_without_run_only_answers(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="Rejected",
                                                 status="❌ Rejected v2026.9.24", run=None)])
    b.handle(cb("hermes|rj:v2026.9.24"))
    assert spawned(ctx) == []
    assert calls(api, "editMessageReplyMarkup") == [status_markup("❌ Rejected v2026.9.24")]


@pytest.mark.parametrize("run", [["setup"], ["deploy", 1], [], "deploy", None])
def test_button_runs_only_long_ops(bot, run):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="x", status=None, run=run)])
    b.handle(cb("hermes|ap:v2026.9.24"))
    assert spawned(ctx) == []


@pytest.mark.parametrize("data", ["ap:v2026.9.24", "nope|ap:v1", "|ap:v1"])
def test_unprefixed_or_unknown_app_buttons_are_out_of_date(bot, data):
    ctx, api, b = bot
    b.handle(cb(data))
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Out of date — send /status"
    assert calls(api, "editMessageReplyMarkup") == [status_markup("⌛ Out of date")]
    assert ["button"] not in [o[:1] for o in ex(b.hub, "hermes").ops()]


def test_app_answer_without_a_button_line(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", rc=2)
    b.handle(cb("hermes|zz"))
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Unknown button"


def test_button_for_an_unreachable_app(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", exc=Unreachable("hermes"))
    b.handle(cb("hermes|ap:v2026.9.24"))
    assert calls(api, "answerCallbackQuery")[0]["text"] == relay.unreachable_text("Hermes")
    assert calls(api, "editMessageReplyMarkup") == []


def test_update_button(bot):
    ctx, api, b = bot
    b.handle(cb("hub|up:v0.6.0"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "self-update", "v0.6.0"]]
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Updating"
    assert calls(api, "editMessageReplyMarkup") == [status_markup("⬆️ Updating Talaria to v0.6.0…")]


def test_status_button_tap_does_nothing(bot):
    ctx, api, b = bot
    b.handle(cb("done"))
    assert calls(api, "answerCallbackQuery") == [{"callback_query_id": "q1", "text": "Already handled"}]
    assert calls(api, "editMessageReplyMarkup") == [] and spawned(ctx) == []


@pytest.mark.parametrize("update", [cb("hermes|ap:v2026.9.24", user=7),
                                    cb("hermes|ap:v2026.9.24", chat_type="group")])
def test_buttons_from_others_are_ignored(bot, update):
    ctx, api, b = bot
    b.handle(update)
    assert spawned(ctx) == [] and api.calls == []


@pytest.mark.parametrize("failing", ["answerCallbackQuery", "editMessageReplyMarkup"])
def test_button_handling_survives_api_errors(bot, failing, capsys):
    # a late tap makes Telegram answer 400 ("query is too old"); the rest must still happen
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="Deploying", status="✅",
                                                 run=["deploy", "v2026.9.24"])])
    real = api.call

    def call(method, **p):
        if method == failing:
            api.calls.append((method, p))
            raise ApiError(400, None)
        return real(method, **p)

    api.call = call
    b.handle(cb("hermes|ap:v2026.9.24"))
    assert spawned(ctx)[0][-2:] == ["deploy", "v2026.9.24"]
    assert [m for m, _ in api.calls if m == "editMessageReplyMarkup"]
    assert f"[talaria] telegram {failing}: telegram api status 400" in capsys.readouterr().err


def test_poll_asks_for_callback_queries(bot):
    ctx, api, b = bot
    b.poll_once()
    assert calls(api, "getUpdates")[-1]["allowed_updates"] == ["message", "callback_query"]


# ---- pairing (unchanged) ----

def test_pair_first_correct_sender_wins(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[upd(1, "old")], [], [upd(2, "/pair WRONG", user=9), upd(3, "/pair ABCD2345",
                                                                           user=77)]])
    who = telegram.pair(ctx, api, "ABCD2345")
    assert who["id"] == 77


def test_pair_ignores_groups_and_expires(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[], [upd(2, "/pair ABCD2345", chat_type="group")]])
    assert telegram.pair(ctx, api, "ABCD2345", timeout_s=60) is None


def test_pair_code_shown_only_after_backlog_is_dropped(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[upd(1, "old")]])
    announce = lambda: api.batches.append([upd(2, "/pair ABCD2345", user=77)])
    who = telegram.pair(ctx, api, "ABCD2345", announce=announce)
    assert who["id"] == 77


def test_pair_exact_calls(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[upd(1, "old")], [], [upd(2, "/pair ABCD2345", user=77)], []])
    announced = []
    who = telegram.pair(ctx, api, "ABCD2345", announce=lambda: announced.append(len(api.calls)))
    assert who == {"id": 77, "first_name": "Ann", "username": "ann"}
    assert announced == [2]
    assert api.calls == [("getUpdates", {"offset": -1, "timeout": 0}),
                         ("getUpdates", {"offset": 2, "timeout": 0}),
                         ("getUpdates", {"offset": 2, "timeout": 30}),
                         ("getUpdates", {"offset": 3, "timeout": 0}),
                         ("sendMessage", {"chat_id": 77,
                                          "text": "Paired. This chat now controls Talaria."})]


def test_pair_without_backlog_polls_from_start(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[], [upd(1, "/pair ABCD2345")], []])
    assert telegram.pair(ctx, api, "ABCD2345")["id"] == OWNER
    assert api.calls[1] == ("getUpdates", {"offset": None, "timeout": 30})


def test_pair_code_must_match_exactly(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[], [upd(1, "/pair ABCD2345 x"), upd(2, "/pair abcd2345"),
                             upd(3, "pair ABCD2345")]])
    assert telegram.pair(ctx, api, "ABCD2345", timeout_s=60) is None


def test_pair_default_timeout_is_15_minutes(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [])
    telegram.pair(ctx, api, "ABCD2345")
    assert ctx.clock.slept == 900


def test_new_code_alphabet():
    assert telegram.ALPHABET == "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    codes = {telegram.new_code() for _ in range(200)}
    assert len(codes) > 190 and all(set(c) <= set(telegram.ALPHABET) for c in codes)


# ---- run ----

class Stop(Exception):
    pass


def test_run_requires_token_and_user(tmp_path, capsys):
    hub = make_hub(tmp_path)
    hub.ctx.conf.telegram_user_id = 0
    assert telegram.run(hub) == 1
    assert capsys.readouterr().err == "talaria bot: token or user id missing; run talaria setup\n"
    hub.ctx.conf.telegram_user_id, hub.ctx.conf.telegram_token = 5, ""
    assert telegram.run(hub) == 1


def test_run_backs_off_and_resets(tmp_path, monkeypatch, capsys):
    hub = make_hub(tmp_path)
    ex(hub, "hermes").on("hello", lines=[hello()]).on("interrupted")
    events = [ApiError(0, None)] * 8 + [None, ApiError(502, None), Stop()]
    made = []

    class LoopAPI:
        def __init__(self, base, token):
            made.append((base, token))

        def call(self, method, **p):
            if method == "setMyCommands" or p.get("offset") == -1 or p.get("timeout") == 0:
                return []
            e = events.pop(0)
            if e:
                raise e
            return []

    slept = []
    monkeypatch.setattr(telegram, "TelegramAPI", LoopAPI)
    monkeypatch.setattr(telegram.time, "sleep", slept.append)
    with pytest.raises(Stop):
        telegram.run(hub)
    assert made == [(hub.ctx.conf.telegram_api, "t")]
    assert slept == [1, 2, 4, 8, 16, 32, 60]      # first failure retries at once
    assert "[talaria] telegram: telegram api status 0" in capsys.readouterr().err


def test_run_startup_once(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    starts = []
    monkeypatch.setattr(telegram.Bot, "startup", lambda self: starts.append(1))
    polls = [None, None, Stop()]

    def poll(self):
        p = polls.pop(0)
        if p:
            raise p

    monkeypatch.setattr(telegram.Bot, "poll_once", poll)
    monkeypatch.setattr(telegram, "TelegramAPI", lambda b, t: None)
    with pytest.raises(Stop):
        telegram.run(hub)
    assert starts == [1]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_telegram.py`
Expected: FAIL (e.g. `TypeError` from `Bot(hub, api)` reading `hub.conf`, `AttributeError: module 'talaria.telegram' has no attribute 'read_form'`).

- [ ] **Step 3: Rewrite `talaria/telegram.py`**

Keep `ALPHABET`, `new_code`, `_private_text`, `pair` exactly as they are. Replace the imports, the constants, the `Bot` class and `run` with:

```python
from __future__ import annotations

import re
import secrets
import sys
import time
from datetime import timedelta

from talaria import apps, hubexec, relay
from talaria.backup import ID_RE
from talaria.notify import ApiError, Message, TelegramAPI, keyboard
from talaria.op import STALE
from talaria.tags import SEMVER, TAG_ARG

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
HELP = ("/status · /check [app] · /approve [app] <tag> · /reject [app] <tag> · "
        "/rollback [app] [CONFIRM] · /backups [app] · /restore [app] <id> [CONFIRM] · "
        "/update <version>")
MENU = [("status", "Every app: version, state, pending update"),
        ("check", "Look for new releases now: /check [app]"),
        ("approve", "Deploy a pending update: /approve [app] <tag>"),
        ("reject", "Never offer a release: /reject [app] <tag>"),
        ("rollback", "Undo the last change: /rollback [app] (asks to confirm)"),
        ("backups", "List backups: /backups [app]"),
        ("restore", "Restore a backup: /restore [app] <id> (asks to confirm)"),
        ("update", "Update Talaria itself: /update <version>")]
APP_WORD = re.compile(r"^[a-z][a-z_-]{0,31}$")
APP_CMDS = ("/status", "/backups", "/check", "/approve", "/reject", "/rollback", "/restore")
LONG_OPS = ("check", "deploy", "rollback", "restore")
OUT_OF_DATE = "⌛ Out of date"
FORMS = {"status": "/status", "backups": "/backups", "check": "/check", "rollback": "/rollback"}
```

(then `new_code`, `_private_text`, `pair` unchanged), then:

```python
def read_form(cmd: str, args: list[str]) -> str | None:
    """What a "Which app?" button runs for this command: its read or describe form, never
    a confirm (spec §5.1)."""
    if cmd in ("/status", "/backups", "/check") and not args:
        return cmd[1:]
    if cmd in ("/approve", "/reject") and len(args) == 1 and TAG_ARG.match(args[0]):
        return "status"
    if cmd == "/rollback" and args in ([], ["CONFIRM"]):
        return "rollback"
    if cmd == "/restore" and args and ID_RE.match(args[0]) and args[1:] in ([], ["CONFIRM"]):
        return f"restore:{args[0]}"
    return None


class Bot:
    def __init__(self, hub, api):
        self.hub, self.ctx, self.api = hub, hub.ctx, api
        self.offset = None
        self.mismatch: set[str] = set()

    def reply(self, text: str, buttons=None) -> None:
        params = {"chat_id": self.ctx.conf.telegram_user_id, "text": text[:4096]}
        markup = keyboard(buttons or [])
        if markup:
            params["reply_markup"] = markup
        self.api.call("sendMessage", **params)

    def _send(self, answer) -> None:
        text, buttons = answer if isinstance(answer, tuple) else (answer, None)
        if text:
            self.reply(text, buttons)

    def spawn(self, name: str, *argv: str) -> None:
        """Long work runs in a transient unit of this account, never in the bot process."""
        unit = f"talaria-{name}-{int(time.time())}"
        self.ctx.sh.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
                         str(self.ctx.paths.bin_link), *argv])

    def dispatch(self, cmd: str, args: list[str]):
        nu = f"Not understood. Commands: {HELP}"
        if cmd == "/status" and not args:
            return relay.status_all(self.hub, self.mismatch)
        if cmd == "/update":
            if len(args) == 1 and SEMVER.match(args[0]):
                self.spawn("update", "update", args[0], "--offer")
                return f"Checking what Talaria {args[0]} would change. I will send the result."
            return nu
        if cmd not in APP_CMDS:
            return nu
        names = list(self.hub.apps)
        if args and args[0] in self.hub.apps:
            return self.app_command(args[0], cmd, args[1:])
        if args and APP_WORD.match(args[0]):
            return f"Unknown app: {args[0]}. Apps: {', '.join(names)}"
        if not names:
            return "No apps registered."
        if len(names) == 1:
            return self.app_command(names[0], cmd, args)
        form = read_form(cmd, args)
        if form is None:
            return nu
        return "Which app?", [[(e.title, f"hub|w:{n}:{form}") for n, e in self.hub.apps.items()]]

    def app_command(self, app: str, cmd: str, args: list[str]):
        if app in self.mismatch:
            return relay.VERSIONS
        e, a = self.hub.apps[app], apps.get(app)
        if cmd == "/status" and not args:
            return relay.quick(e, ["status"])
        if cmd == "/backups" and not args:
            return relay.quick(e, ["backups"])
        if cmd == "/check" and not args:
            self.spawn(f"{app}-check", "relay", app, "check")
            return f"Checking {e.title} for releases."
        if cmd in ("/approve", "/reject") and len(args) == 1 and a.is_release(args[0]):
            if cmd == "/reject":
                return relay.quick(e, ["reject", args[0]])
            self.spawn(f"{app}-deploy", "relay", app, "deploy", args[0])
            return f"Deploying {e.title} {args[0]}. I will report the result."
        if cmd == "/rollback" and not args:
            return relay.quick(e, ["rollback", "describe"])
        if cmd == "/rollback" and args == ["CONFIRM"]:
            self.spawn(f"{app}-rollback", "relay", app, "rollback", "confirm")
            return f"Rolling back {e.title}. I will report the result."
        if cmd == "/restore" and args and ID_RE.match(args[0]):
            if len(args) == 1:
                return relay.quick(e, ["restore", args[0], "describe"])
            if args[1:] == ["CONFIRM"]:
                self.spawn(f"{app}-restore", "relay", app, "restore", args[0], "confirm")
                return f"Restoring {e.title} {args[0]}. I will report the result."
        return f"Not understood. Commands: {HELP}"

    def hub_button(self, rest: str) -> tuple[str, str | None]:
        kind, _, arg = rest.partition(":")
        if kind == "up" and SEMVER.match(arg):
            self.spawn("update", "self-update", arg)
            return "Updating", f"⬆️ Updating Talaria to {arg}…"
        if kind == "w":
            app, _, form = arg.partition(":")
            what, _, rid = form.partition(":")
            if app in self.hub.apps:
                if what == "restore" and ID_RE.match(rid):
                    self._send(self.app_command(app, "/restore", [rid]))
                    return self.hub.apps[app].title, None
                if what in FORMS and not rid:
                    self._send(self.app_command(app, FORMS[what], []))
                    return self.hub.apps[app].title, None
        return "Unknown button", None

    def on_button(self, data) -> tuple[str, str | None]:
        """(toast, status). Data is `<app>|<data>`; the app checks the tap against its own
        state (`op button`) and names what to run. `hub|…` buttons are the hub's own."""
        data = data or ""
        if data == "done":
            return "Already handled", None
        app, sep, rest = data.partition("|")
        if sep and app == "hub":
            return self.hub_button(rest)
        if not sep or app not in self.hub.apps:
            return STALE, OUT_OF_DATE
        if app in self.mismatch:
            return relay.VERSIONS, None
        e = self.hub.apps[app]
        try:
            lines = e.executor.call(["button", rest])
        except hubexec.Unreachable:
            return relay.unreachable_text(e.title), None
        except hubexec.NoAnswer:
            return relay.no_answer_text(e.title), None
        b = next((d for d in lines if d.get("kind") == "button"), None)
        if b is None:
            return "Unknown button", None
        run = b.get("run")
        if isinstance(run, list) and run and run[0] in LONG_OPS \
                and all(isinstance(x, str) for x in run):
            self.spawn(f"{app}-{run[0]}", "relay", app, *run)
        return str(b.get("toast") or ""), b.get("status")

    def handle_button(self, q: dict) -> None:
        msg = q.get("message") or {}
        chat = msg.get("chat") or {}
        if (q.get("from") or {}).get("id") != self.ctx.conf.telegram_user_id \
                or chat.get("type") != "private":
            print(f"[talaria] ignored button {q.get('id')}", file=sys.stderr)
            return
        try:
            toast, status = self.on_button(q.get("data"))
        except Exception as e:
            toast, status = f"Error: {e}"[:200], None
        # quiet: a late tap gets 400 "query is too old"; that must not stop the rest
        self._try("answerCallbackQuery", callback_query_id=q.get("id"), text=toast[:200])
        if status is None:
            return
        self._try("editMessageReplyMarkup", chat_id=chat.get("id"),
                  message_id=msg.get("message_id"),
                  reply_markup={"inline_keyboard": [[{"text": status, "callback_data": "done"}]]})

    def _try(self, method: str, **params) -> None:
        try:
            self.api.call(method, **params)
        except ApiError as e:
            print(f"[talaria] telegram {method}: {e}", file=sys.stderr)

    def handle(self, u: dict) -> None:
        if "callback_query" in u:
            self.handle_button(u["callback_query"])
            return
        who, text = _private_text(u)
        if not who or who.get("id") != self.ctx.conf.telegram_user_id or not text:
            print(f"[talaria] ignored update {u.get('update_id')}", file=sys.stderr)
            return
        parts = text.split()
        cmd = parts[0].split("@", 1)[0]
        try:
            answer = self.dispatch(cmd, parts[1:])
        except Exception as e:
            answer = f"Error: {e}"
        self._send(answer)

    def startup(self) -> None:
        try:   # the "Menu" button in the chat; shown to the owner only
            self.api.call("setMyCommands",
                          commands=[{"command": c, "description": d} for c, d in MENU],
                          scope={"type": "chat", "chat_id": self.ctx.conf.telegram_user_id})
        except ApiError as e:
            print(f"[talaria] could not set the command menu: {e}", file=sys.stderr)
        backlog = self.api.call("getUpdates", offset=-1, timeout=0)
        self.offset = backlog[-1]["update_id"] + 1 if backlog else None
        if self.offset is not None:
            self.api.call("getUpdates", offset=self.offset, timeout=0)
        # spec §4.4: each app's protocol, once per bot start (an update restarts the bot)
        self.mismatch = {n for n, e in self.hub.apps.items() if relay.hello(e) == relay.VERSIONS}
        for n, e in self.hub.apps.items():
            if n not in self.mismatch:
                text, _ = relay.quick(e, ["interrupted"])
                if text:
                    self.ctx.notify.send(Message(text))

    def poll_once(self) -> None:
        for u in self.api.call("getUpdates", offset=self.offset, timeout=25,
                               allowed_updates=["message", "callback_query"]) or []:
            self.offset = u["update_id"] + 1
            self.handle(u)


def run(hub) -> int:
    conf = hub.ctx.conf
    if not conf.telegram_token or not conf.telegram_user_id:
        print("talaria bot: token or user id missing; run talaria setup", file=sys.stderr)
        return 1
    bot = Bot(hub, TelegramAPI(conf.telegram_api, conf.telegram_token))
    delay = 0      # the first failure retries at once: usually one dropped connection
    while True:
        try:
            if bot.offset is None:
                bot.startup()
                bot.offset = bot.offset or 0
            bot.poll_once()
            delay = 0
        except ApiError as e:
            print(f"[talaria] telegram: {e}", file=sys.stderr)
            if delay:
                time.sleep(delay)
            delay = min(max(delay * 2, 1), 60)
```

Also in `talaria/cli.py`, the `bot` branch must not call `telegram.run(ctx)` any more until Task 7 wires the hub; replace

```python
    if args.cmd == "bot":
        from talaria import telegram
        return telegram.run(ctx)
```

with

```python
    if args.cmd == "bot":
        print("talaria bot: this account has no bot of its own; the hub runs it",
              file=sys.stderr)
        return 1
```

and in `tests/test_cli_ops.py::test_delegations` remove the `telegram.run` monkeypatch and the `main("bot")` call, so it reads:

```python
def test_delegations(run, monkeypatch, capsys):
    ctx, main = run
    from talaria import selfupdate, setup
    seen = []
    monkeypatch.setattr(setup, "setup", lambda a: (seen.append(("setup", a.cmd)), 3)[1])
    monkeypatch.setattr(setup, "set_token", lambda c: (seen.append(("set-token", c is ctx)), 4)[1])
    monkeypatch.setattr(selfupdate, "self_update", lambda c, t: (seen.append(("su", t)), 6)[1])
    assert [main("setup"), main("set-token"), main("bot"), main("self-update", "v0.2.0")] == [3, 4, 1, 6]
    assert seen == [("setup", "setup"), ("set-token", True), ("su", "v0.2.0")]
    assert "the hub runs it" in capsys.readouterr().err
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add talaria/telegram.py talaria/cli.py tests/test_telegram.py tests/test_cli_ops.py
git commit -m "Hub bot: one bot over all apps, Which app?, prefixed buttons, version check"
```

---
### Task 6: App-side self-update: `op self-update`, `--dry-run`, `op quadlet`

**Files:**
- Modify: `talaria/selfupdate.py` (no bot restart; busy refusal; `dry_run`), `talaria/op.py` (add `self-update` and `quadlet`)
- Test: `tests/test_selfupdate.py`, `tests/test_op.py`

**Interfaces:**
- Consumes: `op.OPS`, `op.build_parser`, `op._run`, `op.emit`, `op._reply` (Task 2); `hubexec.parse_lines(text, app)` (Task 3); `units.render_quadlet(ctx)`; `lock.op_lock(paths)`, `lock.Busy`; `cli.EXIT_BUSY`, `cli._semver`.
- Produces:
  - `selfupdate.self_update(ctx, tag) -> int` (0 ok; 1 failure; 75 when the app's lock is held, before any git command; never restarts `talaria-telegram.service`).
  - `selfupdate.dry_run(ctx, tag) -> bool` (True if the app's Quadlet would change; raises `CommandError` or `ValueError`). Worktree path: `ctx.paths.state_dir / "dry-run"`.
  - `talaria op self-update TAG` → one `reply` (`"Talaria TAG installed."`, `"busy: an operation is running"`, or `"self-update failed (exit N); details in the journal"`), exit code of `self_update`.
  - `talaria op self-update TAG --dry-run` → `reply {text: "would restart: yes|no", buttons: [], restart: bool}` exit 0, or `reply {text: "dry run failed: …"}` exit 1.
  - `talaria op quadlet` → `reply {text: <rendered app Quadlet>}`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_selfupdate.py` with:

```python
import json

import pytest

from talaria import lock, selfupdate
from talaria.shell import CommandError
from tests.fakes import make_test_ctx


def test_self_update_checks_out_and_runs_the_new_setup(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link))
    assert selfupdate.self_update(ctx, "v0.2.0") == 0
    d = str(ctx.paths.install_dir)
    assert ctx.sh.calls == [["git", "-C", d, "fetch", "-q", "--tags", "origin"],
                            ["git", "-C", d, "checkout", "-q", "v0.2.0"],
                            [str(ctx.paths.bin_link), "setup", "--as-service"]]
    assert not ctx.sh.called("systemctl")       # an app install has no bot to restart


def test_self_update_unknown_tag(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", "-C", str(ctx.paths.install_dir), "fetch")
    ctx.sh.on("git", "-C", str(ctx.paths.install_dir), "checkout", rc=1, err="no such ref")
    assert selfupdate.self_update(ctx, "v9.9.9") == 1
    assert "no such ref" in capsys.readouterr().err


def test_self_update_exact(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link), out="OK: x\n")
    assert selfupdate.self_update(ctx, "v0.2.0") == 0
    assert capsys.readouterr().out == "OK: x\nTalaria v0.2.0 installed.\n"
    assert ctx.sh.timeouts == [600, None, 1800]


def test_self_update_setup_failure_is_reported(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link), rc=10, out="ACTION REQUIRED: x\n")
    assert selfupdate.self_update(ctx, "v0.2.0") == 1
    assert capsys.readouterr().out == "ACTION REQUIRED: x\n"


def test_self_update_fetch_failure(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", rc=128, err="offline")
    assert selfupdate.self_update(ctx, "v0.2.0") == 1
    assert len(ctx.sh.calls) == 1 and "offline" in capsys.readouterr().err


def test_self_update_refuses_while_an_operation_runs(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    with lock.op_lock(ctx.paths):
        assert selfupdate.self_update(ctx, "v0.2.0") == 75
    assert ctx.sh.calls == []            # nothing fetched, nothing checked out
    assert capsys.readouterr().err == ("self-update to v0.2.0 refused: an operation is "
                                       "running\n")


# ---- dry run ----

def quadlet_reply(text):
    return json.dumps({"v": 1, "kind": "reply", "text": text, "buttons": []}) + "\n"


@pytest.fixture
def dry(tmp_path):
    ctx = make_test_ctx(tmp_path)
    wt = ctx.paths.state_dir / "dry-run"
    ctx.sh.on("git")
    ctx.paths.quadlet.parent.mkdir(parents=True)
    ctx.paths.quadlet.write_text("OLD\n")
    return ctx, wt


def test_dry_run_commands_exact(dry):
    ctx, wt = dry
    ctx.sh.on(str(wt / "bin/talaria"), out="noise\n" + quadlet_reply("OLD\n"))
    assert selfupdate.dry_run(ctx, "v0.6.0") is False
    d = str(ctx.paths.install_dir)
    assert list(zip(ctx.sh.calls, ctx.sh.timeouts)) == [
        (["git", "-C", d, "fetch", "-q", "--tags", "origin"], 600),
        (["git", "-C", d, "worktree", "remove", "--force", str(wt)], None),
        (["git", "-C", d, "worktree", "prune"], None),
        (["git", "-C", d, "worktree", "add", "-q", "--detach", str(wt), "v0.6.0"], 120),
        ([str(wt / "bin/talaria"), "op", "quadlet"], 120),
        (["git", "-C", d, "worktree", "remove", "--force", str(wt)], None)]


def test_dry_run_reports_a_changed_quadlet(dry):
    ctx, wt = dry
    ctx.sh.on(str(wt / "bin/talaria"), out=quadlet_reply("NEW\n"))
    assert selfupdate.dry_run(ctx, "v0.6.0") is True


def test_dry_run_without_an_installed_quadlet_restarts(dry):
    ctx, wt = dry
    ctx.paths.quadlet.unlink()
    ctx.sh.on(str(wt / "bin/talaria"), out=quadlet_reply("NEW\n"))
    assert selfupdate.dry_run(ctx, "v0.6.0") is True


def test_dry_run_removes_the_worktree_when_the_new_version_fails(dry):
    ctx, wt = dry
    ctx.sh.on(str(wt / "bin/talaria"), rc=2, err="talaria op: not allowed")
    with pytest.raises(CommandError):
        selfupdate.dry_run(ctx, "v0.6.0")
    assert ctx.sh.calls[-1][-3:] == ["remove", "--force", str(wt)]


def test_dry_run_without_a_reply_is_an_error(dry):
    ctx, wt = dry
    ctx.sh.on(str(wt / "bin/talaria"), out="")
    with pytest.raises(ValueError, match="Talaria v0.6.0 did not render a Quadlet"):
        selfupdate.dry_run(ctx, "v0.6.0")


def test_dry_run_clears_a_leftover_worktree(dry):
    ctx, wt = dry
    (wt / "junk").mkdir(parents=True)
    ctx.sh.on(str(wt / "bin/talaria"), out=quadlet_reply("OLD\n"))
    selfupdate.dry_run(ctx, "v0.6.0")
    assert not (wt / "junk").exists()
```

Append to `tests/test_op.py`:

```python
# ---- self-update and quadlet (Task 6) ----

def test_quadlet_op_replies_with_the_rendered_quadlet(opx):
    ctx, run = opx
    from talaria import units
    assert run("quadlet") == (0, [{"v": 1, "kind": "reply", "text": units.render_quadlet(ctx),
                                   "buttons": []}])


@pytest.mark.parametrize("restart,word", [(True, "yes"), (False, "no")])
def test_self_update_dry_run(opx, monkeypatch, restart, word):
    ctx, run = opx
    monkeypatch.setattr(op.selfupdate, "dry_run", lambda c, t: (c is ctx and t == "v0.6.0") and restart)
    assert run("self-update", "v0.6.0", "--dry-run") == (0, [{
        "v": 1, "kind": "reply", "text": f"would restart: {word}", "buttons": [],
        "restart": restart}])


def test_self_update_dry_run_failure(opx, monkeypatch):
    ctx, run = opx

    def boom(c, t):
        raise ValueError("Talaria v0.6.0 did not render a Quadlet")
    monkeypatch.setattr(op.selfupdate, "dry_run", boom)
    assert run("self-update", "v0.6.0", "--dry-run") == (1, [{
        "v": 1, "kind": "reply", "text": "dry run failed: Talaria v0.6.0 did not render a Quadlet",
        "buttons": []}])


@pytest.mark.parametrize("rc,text", [
    (0, "Talaria v0.6.0 installed."), (75, "busy: an operation is running"),
    (1, "self-update failed (exit 1); details in the journal")])
def test_self_update(opx, monkeypatch, rc, text):
    ctx, run = opx
    monkeypatch.setattr(op.selfupdate, "self_update", lambda c, t: (c is ctx and t == "v0.6.0") and rc)
    assert run("self-update", "v0.6.0") == (rc, [{"v": 1, "kind": "reply", "text": text,
                                                  "buttons": []}])


@pytest.mark.parametrize("argv", [["self-update"], ["self-update", "main"],
                                  ["self-update", "v0.6.0", "--dry"], ["self-update", "v1.2"],
                                  ["quadlet", "x"], ["self-update", "v0.6.0", "--dry-run", "x"]])
def test_self_update_and_quadlet_are_strict(argv):
    out = io.StringIO()
    assert op.main(argv, make=lambda: pytest.fail("no ctx"), out=out) == 2 and out.getvalue() == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_selfupdate.py tests/test_op.py`
Expected: FAIL (`dry_run` missing; `test_self_update_checks_out_and_runs_the_new_setup` sees the `systemctl` restart; `quadlet`/`self-update` exit 2 in op).

- [ ] **Step 3: Implement**

`talaria/selfupdate.py`:

```python
from __future__ import annotations

import shutil
import sys

from talaria import lock
from talaria.cli import EXIT_BUSY
from talaria.hubexec import parse_lines
from talaria.shell import CommandError


def self_update(ctx, tag: str) -> int:
    """Switch this install to `tag` and let the new code re-render its units (it restarts
    the app only if its Quadlet changed). Never under a running operation: that one would
    continue on half-old, half-new code."""
    try:
        with lock.op_lock(ctx.paths):
            pass
    except lock.Busy:
        print(f"self-update to {tag} refused: an operation is running", file=sys.stderr)
        return EXIT_BUSY
    d = str(ctx.paths.install_dir)
    try:
        ctx.sh.run(["git", "-C", d, "fetch", "-q", "--tags", "origin"], timeout=600)
        ctx.sh.run(["git", "-C", d, "checkout", "-q", tag])
    except CommandError as e:
        print(f"self-update failed: {e}", file=sys.stderr)
        return 1
    r = ctx.sh.run([str(ctx.paths.bin_link), "setup", "--as-service"], check=False,
                   timeout=1800)
    print(r.stdout, end="")
    if r.returncode != 0:
        return 1
    print(f"Talaria {tag} installed.")
    return 0


def dry_run(ctx, tag: str) -> bool:
    """Would updating to `tag` change this app's Quadlet, and so restart the app? The new
    version renders it with its own code in a throwaway worktree; nothing else changes."""
    d = str(ctx.paths.install_dir)
    wt = ctx.paths.state_dir / "dry-run"
    ctx.sh.run(["git", "-C", d, "fetch", "-q", "--tags", "origin"], timeout=600)
    ctx.sh.run(["git", "-C", d, "worktree", "remove", "--force", str(wt)], check=False)
    shutil.rmtree(wt, ignore_errors=True)
    ctx.sh.run(["git", "-C", d, "worktree", "prune"], check=False)
    ctx.sh.run(["git", "-C", d, "worktree", "add", "-q", "--detach", str(wt), tag], timeout=120)
    try:
        r = ctx.sh.run([str(wt / "bin/talaria"), "op", "quadlet"], timeout=120)
    finally:
        ctx.sh.run(["git", "-C", d, "worktree", "remove", "--force", str(wt)], check=False)
    new = next((x["text"] for x in parse_lines(r.stdout, ctx.app.name)
                if x.get("kind") == "reply" and isinstance(x.get("text"), str)), None)
    if new is None:
        raise ValueError(f"Talaria {tag} did not render a Quadlet")
    old = ctx.paths.quadlet.read_text() if ctx.paths.quadlet.exists() else None
    return new != old
```

`talaria/op.py`:
- change the import line to `from talaria import __version__, cli, rollback, selfupdate, state, status, units` and add `from talaria.shell import CommandError`;
- change `OPS` to

```python
OPS = ("status", "backups", "check", "deploy", "reject", "rollback", "restore", "button",
       "hello", "interrupted", "self-update", "quadlet")
```

- in `build_parser`, before `return p`, add

```python
    s = add("self-update")              # must stay accepted by every later version (§4.2)
    s.add_argument("tag", type=cli._semver)
    s.add_argument("--dry-run", action="store_true")
    add("quadlet")                      # read-only; used by `self-update --dry-run`
```

- in `_run`, directly after the `hello` branch, add

```python
    if o == "quadlet":
        return _reply(out, units.render_quadlet(ctx))
    if o == "self-update":
        return _self_update(ctx, args, out)
```

- and add above `_run`:

```python
def _self_update(ctx, args, out) -> int:
    if args.dry_run:
        try:
            restart = selfupdate.dry_run(ctx, args.tag)
        except (CommandError, ValueError) as e:
            return _reply(out, f"dry run failed: {e}") or 1
        emit(out, "reply", text=f"would restart: {'yes' if restart else 'no'}", buttons=[],
             restart=restart)
        return 0
    rc = selfupdate.self_update(ctx, args.tag)
    texts = {0: f"Talaria {args.tag} installed.", cli.EXIT_BUSY: "busy: an operation is running"}
    _reply(out, texts.get(rc, f"self-update failed (exit {rc}); details in the journal"))
    return rc
```

(`_reply` returns 0, so `_reply(...) or 1` returns 1.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add talaria/selfupdate.py talaria/op.py tests/test_selfupdate.py tests/test_op.py
git commit -m "op self-update with --dry-run and quadlet; app self-update never restarts a bot"
```

---

### Task 7: Hub timer, Talaria-release offer, update flow, CLI routing

**Files:**
- Create: `talaria/hubcheck.py`, `talaria/hubupdate.py`, `tests/test_hubcheck.py`, `tests/test_hubupdate.py`, `tests/test_cli_hub.py`
- Modify: `talaria/cli.py` (parser: `relay`, `update`; role routing), `tests/test_cli_ops.py` (parser table)
- Test: the three new files and `tests/test_cli_ops.py`

**Interfaces:**
- Consumes: `Hub`, `AppEntry`, `hubexec.Unreachable`, `hubexec.NoAnswer`, `hubexec.load_hub` (Task 3); `relay.hello`, `relay.relay`, `relay.status_all`, `relay.main`, `relay.VERSIONS` (Task 4); `telegram.run(hub)` (Task 5); `op self-update TAG [--dry-run]` replies (Task 6); `upstream.latest_semver(sh, repo)`; `tags.semver_newer`; `state.write_json_atomic`; `setup.set_token(ctx)`; `Paths.hub_state`.
- Produces:
  - `hubcheck.TRANSITIONAL` (format string with `{app}`, `{user}`); `hubcheck.check(hub, timer: bool) -> int`; `hubcheck.talaria_release(hub) -> None`; `hubcheck.load_state(paths) -> dict`.
  - `hubupdate.would_restart(entry, tag) -> str` (`yes`, `no`, `unknown`, `unknown (sudo rule missing)`, `unknown (no answer)`); `hubupdate.offer(hub, tag) -> int`; `hubupdate.start_update(hub, tag) -> int`; `hubupdate.self_update(hub, tag) -> int`.
  - CLI: `talaria relay APP OP…`, `talaria update TAG [--offer]`; `cli.main(argv=None, make=make_ctx, make_hub=None)`; `cli.HUB_CMDS`, `cli.TRANSITIONAL_CMDS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hubcheck.py
import json

import pytest

from talaria import __version__, hubcheck
from talaria.hubexec import Unreachable
from talaria.shell import CommandError, Result
from tests.hubfakes import ex, hello, make_hub, message, reply


@pytest.fixture
def h2(tmp_path, monkeypatch):
    log = []
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=log)
    for n in h.apps:
        ex(h, n).on("hello", lines=[hello(app=n)]).on("check", lines=[message(f"{n} checked")])
    h.latest = f"v{__version__}"
    monkeypatch.setattr(hubcheck, "latest_semver",
                        lambda sh, repo: (sh is h.ctx.sh and repo == h.ctx.conf.talaria_repo)
                        and h.latest)
    h.log = log
    return h


def test_timer_checks_each_app_in_order(h2):
    assert hubcheck.check(h2, timer=True) == 0
    assert h2.log == [("hermes", "call", ("hello",)), ("hermes", "stream", ("check", "--timer")),
                      ("clawvisor", "call", ("hello",)),
                      ("clawvisor", "stream", ("check", "--timer"))]
    assert h2.ctx.notify.texts() == ["hermes checked", "clawvisor checked"]


def test_manual_check_has_no_timer_flag(h2):
    hubcheck.check(h2, timer=False)
    assert ("hermes", "stream", ("check",)) in h2.log


def test_app_with_another_protocol_is_reported_and_skipped(h2):
    ex(h2, "clawvisor").on("hello", lines=[hello(protocol=2, app="clawvisor")])
    hubcheck.check(h2, timer=True)
    assert ("clawvisor", "stream", ("check", "--timer")) not in h2.log
    assert h2.ctx.notify.texts() == [
        "hermes checked", "Clawvisor: Talaria versions differ on this host; run /update"]


def test_unreachable_app_is_reported(h2):
    ex(h2, "hermes").on("hello", exc=Unreachable("hermes"))
    hubcheck.check(h2, timer=True)
    assert h2.ctx.notify.texts()[0] == ("Hermes: Talaria cannot reach this app (sudo rule "
                                        "missing); run setup again")


def test_new_talaria_release_is_offered_once_with_dry_runs(h2):
    h2.latest = "v99.0.0"
    ex(h2, "hermes").on("self-update", lines=[dict(reply("would restart: no"), restart=False)])
    ex(h2, "clawvisor").on("self-update", lines=[dict(reply("would restart: yes"), restart=True)])
    hubcheck.check(h2, timer=True)
    hubcheck.check(h2, timer=True)
    offers = [m for m in h2.ctx.notify.sent if m.text.startswith("Talaria v99.0.0")]
    assert len(offers) == 1
    assert offers[0].text == (f"Talaria v99.0.0 is available (installed v{__version__}). "
                              "Hermes would restart: no · Clawvisor would restart: yes")
    assert offers[0].buttons == [[("Update Talaria to v99.0.0", "hub|up:v99.0.0")]]
    assert ("call", ["self-update", "v99.0.0", "--dry-run"], 900) in ex(h2, "hermes").calls
    assert json.loads(h2.ctx.paths.hub_state.read_text()) == {"talaria_notified": "v99.0.0"}


@pytest.mark.parametrize("latest", [None, f"v{__version__}", "v0.0.1"])
def test_no_offer_without_a_newer_release(h2, latest):
    h2.latest = latest
    hubcheck.check(h2, timer=True)
    assert not any(t.startswith("Talaria") for t in h2.ctx.notify.texts())
    assert not h2.ctx.paths.hub_state.exists()


def test_offline_release_lookup_is_silent(h2, monkeypatch):
    monkeypatch.setattr(hubcheck, "latest_semver",
                        lambda sh, repo: (_ for _ in ()).throw(CommandError(["git"], Result(1))))
    hubcheck.check(h2, timer=True)
    assert h2.ctx.notify.texts() == ["hermes checked", "clawvisor checked"]


def test_corrupt_hub_state_is_treated_as_empty(h2):
    h2.ctx.paths.hub_state.parent.mkdir(parents=True, exist_ok=True)
    h2.ctx.paths.hub_state.write_text("{nope")
    assert hubcheck.load_state(h2.ctx.paths) == {}


def test_transitional_mode_reminds_daily_on_the_timer_only(tmp_path, monkeypatch):
    h = make_hub(tmp_path, ("clawvisor",), transitional=True)
    ex(h, "clawvisor").on("hello", lines=[hello(app="clawvisor")]).on("check")
    monkeypatch.setattr(hubcheck, "latest_semver", lambda sh, repo: None)
    monkeypatch.setattr(hubcheck.getpass, "getuser", lambda: "clawvisor")
    hubcheck.check(h, timer=True)
    hubcheck.check(h, timer=False)
    assert h.ctx.notify.texts() == [
        "Talaria v0.5 needs a one-time move of the bot to its own user: run "
        "`bin/talaria setup --app clawvisor --user clawvisor` as the operator."]
```

```python
# tests/test_hubupdate.py
import pytest

from talaria import __version__, hubupdate
from talaria.hubexec import NoAnswer, Unreachable
from talaria.shell import Result
from tests.hubfakes import ex, make_hub, reply


@pytest.fixture
def h2(tmp_path):
    events = []
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=events)
    sh = h.ctx.sh
    sh.on("git", fn=lambda argv, input: (events.append(("git", argv[3])), Result(0))[1])
    sh.on(str(h.ctx.paths.bin_link), fn=lambda argv, input: (events.append(("setup",)),
                                                             Result(0, "OK: hub units\n"))[1])
    sh.on("systemctl", fn=lambda argv, input: (events.append(("restart",)), Result(0))[1])
    ex(h, "hermes").on("self-update", lines=[reply("Talaria v0.6.0 installed.")])
    ex(h, "clawvisor").on("self-update", lines=[reply("Talaria v0.6.0 installed.")])
    h.events = events
    return h


def test_hub_first_then_each_app_then_the_bot(h2, capsys):
    assert hubupdate.self_update(h2, "v0.6.0") == 0
    assert h2.events == [("git", "fetch"), ("git", "checkout"), ("setup",),
                         ("hermes", "stream", ("self-update", "v0.6.0")),
                         ("clawvisor", "stream", ("self-update", "v0.6.0")), ("restart",)]
    d = str(h2.ctx.paths.install_dir)
    assert list(zip(h2.ctx.sh.calls, h2.ctx.sh.timeouts)) == [
        (["git", "-C", d, "fetch", "-q", "--tags", "origin"], 600),
        (["git", "-C", d, "checkout", "-q", "v0.6.0"], None),
        ([str(h2.ctx.paths.bin_link), "setup", "--as-hub"], 600),
        (["systemctl", "--user", "restart", "talaria-telegram.service"], None)]
    assert h2.ctx.notify.texts() == ["Talaria v0.6.0 installed: Hermes ✓ · Clawvisor ✓"]
    assert capsys.readouterr().out == "OK: hub units\n"


def test_partial_failure_keeps_going_and_names_the_reason(h2):
    ex(h2, "clawvisor").on("self-update", lines=[
        reply("self-update failed (exit 1); details in the journal")], rc=1)
    ex(h2, "hermes").on("self-update", exc=Unreachable("hermes"))
    assert hubupdate.self_update(h2, "v0.6.0") == 1
    assert h2.ctx.notify.texts() == [
        "Talaria v0.6.0 installed: Hermes ✗ (sudo rule missing) · "
        "Clawvisor ✗ (self-update failed (exit 1); details in the journal)"]
    assert h2.events[-1] == ("restart",)


def test_app_failure_without_a_reply_names_the_exit_code(h2):
    ex(h2, "clawvisor").on("self-update", rc=75)
    hubupdate.self_update(h2, "v0.6.0")
    assert h2.ctx.notify.texts() == ["Talaria v0.6.0 installed: Hermes ✓ · Clawvisor ✗ (exit 75)"]


def test_hub_checkout_failure_changes_nothing(h2, capsys):
    h2.ctx.sh.on("git", "-C", str(h2.ctx.paths.install_dir), "checkout", rc=1, err="no such ref")
    assert hubupdate.self_update(h2, "v0.6.0") == 1
    (text,) = h2.ctx.notify.texts()
    assert text.startswith("Talaria v0.6.0 was not installed: the hub could not check it out (")
    assert text.endswith("). Nothing changed.") and "no such ref" in text
    assert not any(e[0] in ("hermes", "clawvisor", "restart") for e in h2.events)
    assert text in capsys.readouterr().err


def test_hub_setup_failure_stops_before_the_apps(h2):
    h2.ctx.sh.on(str(h2.ctx.paths.bin_link), rc=1, out="STOP: x\n")
    assert hubupdate.self_update(h2, "v0.6.0") == 1
    assert h2.ctx.notify.texts() == ["Talaria v0.6.0: the hub's setup failed (exit 1); apps were "
                                     "not updated. Details in the journal."]
    assert not any(e[0] in ("hermes", "clawvisor", "restart") for e in h2.events)


def test_transitional_update_skips_the_hub_install(tmp_path):
    events = []
    h = make_hub(tmp_path, ("hermes",), transitional=True, log=events)
    h.ctx.sh.on("systemctl", fn=lambda argv, input: (events.append(("restart",)), Result(0))[1])
    ex(h, "hermes").on("self-update", lines=[reply("Talaria v0.6.0 installed.")])
    assert hubupdate.self_update(h, "v0.6.0") == 0
    assert events == [("hermes", "stream", ("self-update", "v0.6.0")), ("restart",)]
    assert h.ctx.notify.texts() == ["Talaria v0.6.0 installed: Hermes ✓"]


def test_would_restart(tmp_path):
    h = make_hub(tmp_path)
    e = h.apps["hermes"]
    ex(h, "hermes").on("self-update", lines=[dict(reply("would restart: yes"), restart=True)])
    assert hubupdate.would_restart(e, "v0.6.0") == "yes"
    ex(h, "hermes").on("self-update", lines=[reply("dry run failed: x")], rc=1)
    assert hubupdate.would_restart(e, "v0.6.0") == "unknown"
    ex(h, "hermes").on("self-update", exc=Unreachable("hermes"))
    assert hubupdate.would_restart(e, "v0.6.0") == "unknown (sudo rule missing)"
    ex(h, "hermes").on("self-update", exc=NoAnswer("hermes"))
    assert hubupdate.would_restart(e, "v0.6.0") == "unknown (no answer)"


def test_offer_text_and_button(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("self-update", lines=[dict(reply("would restart: no"), restart=False)])
    assert hubupdate.offer(h, "v0.6.0") == 0
    (m,) = h.ctx.notify.sent
    assert m.text == f"Talaria v0.6.0 is available (installed v{__version__}). Hermes would restart: no"
    assert m.buttons == [[("Update Talaria to v0.6.0", "hub|up:v0.6.0")]]


def test_start_update_runs_in_a_transient_unit(tmp_path, monkeypatch, capsys):
    h = make_hub(tmp_path)
    h.ctx.sh.on("systemd-run")
    monkeypatch.setattr(hubupdate.time, "time", lambda: 1234.5)
    assert hubupdate.start_update(h, "v0.6.0") == 0
    assert h.ctx.sh.calls == [["systemd-run", "--user", "--collect", "--quiet",
                               "--unit=talaria-update-1234", str(h.ctx.paths.bin_link),
                               "self-update", "v0.6.0"]]
    assert capsys.readouterr().out == ("Updating Talaria to v0.6.0 in the background; the bot "
                                       "reports the result.\n")
```

```python
# tests/test_cli_hub.py
from types import SimpleNamespace

import pytest

from talaria import cli, hubexec
from tests.fakes import make_test_ctx
from tests.hubfakes import make_hub


@pytest.fixture
def hubmain(tmp_path, monkeypatch):
    hub = make_hub(tmp_path, ("hermes", "clawvisor"))
    seen = []
    from talaria import hubcheck, hubupdate, relay, setup, telegram
    monkeypatch.setattr(telegram, "run", lambda h: (seen.append(("bot", h is hub)), 11)[1])
    monkeypatch.setattr(relay, "main", lambda h, app, argv: (seen.append(("relay", app, argv)), 12)[1])
    monkeypatch.setattr(relay, "status_all", lambda h: "ALL")
    monkeypatch.setattr(hubcheck, "check", lambda h, timer: (seen.append(("check", timer)), 13)[1])
    monkeypatch.setattr(hubupdate, "self_update", lambda h, t: (seen.append(("su", t)), 14)[1])
    monkeypatch.setattr(hubupdate, "start_update", lambda h, t: (seen.append(("update", t)), 15)[1])
    monkeypatch.setattr(hubupdate, "offer", lambda h, t: (seen.append(("offer", t)), 16)[1])
    monkeypatch.setattr(setup, "set_token", lambda c: (seen.append(("token", c is hub.ctx)), 17)[1])
    run = lambda *argv: cli.main(list(argv), make=lambda: pytest.fail("app ctx in a hub"),
                                 make_hub=lambda: hub)
    return hub, run, seen


def test_hub_commands(hubmain, capsys):
    hub, run, seen = hubmain
    assert [run("bot"), run("relay", "hermes", "check", "--timer"), run("check", "--timer"),
            run("check"), run("self-update", "v0.6.0"), run("update", "v0.6.0"),
            run("update", "v0.6.0", "--offer"), run("set-token"), run("status")] == \
        [11, 12, 13, 13, 14, 15, 16, 17, 0]
    assert seen == [("bot", True), ("relay", "hermes", ["check", "--timer"]), ("check", True),
                    ("check", False), ("su", "v0.6.0"), ("update", "v0.6.0"), ("offer", "v0.6.0"),
                    ("token", True)]
    assert capsys.readouterr().out == "ALL\n"


@pytest.mark.parametrize("argv", [["deploy", "v2026.9.24"], ["backups"], ["rollback"],
                                  ["restore", "20260927T043000Z-manual"], ["backup"], ["history"],
                                  ["rehearse", "v2026.9.24"], ["login-link"], ["reject", "v2026.9.24"]])
def test_app_commands_are_refused_in_a_hub(hubmain, capsys, argv):
    hub, run, seen = hubmain
    assert run(*argv) == 2 and seen == []
    assert capsys.readouterr().err == (f"talaria {argv[0]}: this account is the Talaria hub; app "
                                       "commands run as the app's account or through the bot\n")


def test_transitional_routes_only_the_bot_side(hubmain):
    hub, run, seen = hubmain
    hub.transitional = True
    assert run("bot") == 11 and run("check", "--timer") == 13
    app_dir = hub.ctx.paths.home.parent / "app"
    app_dir.mkdir()
    app_ctx = make_test_ctx(app_dir)
    assert cli.main(["backups"], make=lambda: app_ctx, make_hub=lambda: hub) == 0


@pytest.mark.parametrize("argv", [["relay", "hermes", "status"], ["update", "v0.6.0"]])
def test_hub_only_commands_outside_a_hub(argv, tmp_path, capsys):
    assert cli.main(argv, make=lambda: pytest.fail("no ctx"), make_hub=lambda: None) == 2
    assert capsys.readouterr().err == f"talaria {argv[0]}: this account is not a Talaria hub\n"


def test_injected_app_ctx_never_looks_for_a_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hubexec, "load_hub", lambda *a, **k: pytest.fail("looked for a hub"))
    ctx = make_test_ctx(tmp_path)
    assert cli.main(["backups"], make=lambda: ctx) == 0


def test_default_make_looks_for_a_hub(monkeypatch):
    fake = SimpleNamespace(transitional=False)
    monkeypatch.setattr(hubexec, "load_hub", lambda *a, **k: fake)
    from talaria import telegram
    monkeypatch.setattr(telegram, "run", lambda h: 0 if h is fake else 1)
    assert cli.main(["bot"]) == 0
```

In `tests/test_cli_ops.py::test_parser_commands` add these rows to the parametrize list:

```python
    (["relay", "hermes", "check", "--timer"], {"cmd": "relay", "app": "hermes",
                                               "op": ["check", "--timer"]}),
    (["update", "v0.6.0"], {"cmd": "update", "tag": "v0.6.0", "offer": False}),
    (["update", "v0.6.0", "--offer"], {"cmd": "update", "tag": "v0.6.0", "offer": True}),
```

and to `test_parser_rejects`: `["update", "main"]`, `["update"]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_hubcheck.py tests/test_hubupdate.py tests/test_cli_hub.py tests/test_cli_ops.py`
Expected: FAIL (`ModuleNotFoundError: talaria.hubcheck`, `talaria.hubupdate`; `main()` has no `make_hub`).

- [ ] **Step 3: Implement**

`talaria/hubupdate.py`:

```python
"""Updating Talaria on the whole host: hub first, apps next, bot restart last (spec §6).
Everything this module uses is imported at load time: `self_update` checks out new code
underneath the running process."""
from __future__ import annotations

import sys
import time

from talaria import __version__
from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import Message
from talaria.shell import CommandError


def would_restart(entry, tag: str) -> str:
    try:
        lines = entry.executor.call(["self-update", tag, "--dry-run"], timeout=900)
    except Unreachable:
        return "unknown (sudo rule missing)"
    except NoAnswer:
        return "unknown (no answer)"
    for d in lines:
        if d.get("kind") == "reply" and isinstance(d.get("restart"), bool):
            return "yes" if d["restart"] else "no"
    return "unknown"


def offer(hub, tag: str) -> int:
    parts = [f"{e.title} would restart: {would_restart(e, tag)}" for e in hub.apps.values()]
    hub.ctx.notify.send(Message(
        f"Talaria {tag} is available (installed v{__version__}). " + " · ".join(parts),
        buttons=[[(f"Update Talaria to {tag}", f"hub|up:{tag}")]]))
    return 0


def start_update(hub, tag: str) -> int:
    ctx = hub.ctx
    ctx.sh.run(["systemd-run", "--user", "--collect", "--quiet",
                f"--unit=talaria-update-{int(time.time())}", str(ctx.paths.bin_link),
                "self-update", tag])
    print(f"Updating Talaria to {tag} in the background; the bot reports the result.")
    return 0


def _stop(ctx, text: str) -> int:
    print(text, file=sys.stderr)
    ctx.notify.send(Message(text))
    return 1


def _update_app(entry, tag: str) -> tuple[bool, str]:
    last = ""
    try:
        for d in entry.executor.stream(["self-update", tag]):
            if d.get("kind") == "reply":
                last = str(d.get("text") or "")
    except Unreachable:
        return False, f"{entry.title} ✗ (sudo rule missing)"
    rc = entry.executor.returncode
    if rc == 0:
        return True, f"{entry.title} ✓"
    return False, f"{entry.title} ✗ ({last or f'exit {rc}'})"


def self_update(hub, tag: str) -> int:
    ctx = hub.ctx
    if not hub.transitional:      # in transitional mode the hub's install is the app's
        d = str(ctx.paths.install_dir)
        try:
            ctx.sh.run(["git", "-C", d, "fetch", "-q", "--tags", "origin"], timeout=600)
            ctx.sh.run(["git", "-C", d, "checkout", "-q", tag])
        except CommandError as e:
            return _stop(ctx, f"Talaria {tag} was not installed: the hub could not check it "
                              f"out ({e}). Nothing changed.")
        r = ctx.sh.run([str(ctx.paths.bin_link), "setup", "--as-hub"], check=False, timeout=600)
        print(r.stdout, end="")
        if r.returncode != 0:
            return _stop(ctx, f"Talaria {tag}: the hub's setup failed (exit {r.returncode}); "
                              "apps were not updated. Details in the journal.")
    results = [_update_app(e, tag) for e in hub.apps.values()]
    ctx.sh.run(["systemctl", "--user", "restart", "talaria-telegram.service"], check=False)
    ctx.notify.send(Message(f"Talaria {tag} installed: " + " · ".join(t for _, t in results)))
    return 0 if all(ok for ok, _ in results) else 1
```

`talaria/hubcheck.py`:

```python
"""The hub's daily check (spec §5.4) and the one Talaria-release check per host (§6)."""
from __future__ import annotations

import getpass
import json

from talaria import __version__, hubupdate, relay
from talaria.notify import Message
from talaria.shell import CommandError
from talaria.state import write_json_atomic
from talaria.tags import semver_newer
from talaria.upstream import latest_semver

TRANSITIONAL = ("Talaria v0.5 needs a one-time move of the bot to its own user: run "
                "`bin/talaria setup --app {app} --user {user}` as the operator.")


def load_state(paths) -> dict:
    try:
        return json.loads(paths.hub_state.read_text())
    except (OSError, ValueError):
        return {}


def check(hub, timer: bool) -> int:
    for name, e in hub.apps.items():
        err = relay.hello(e)
        if err:
            hub.ctx.notify.send(Message(f"{e.title}: {err}" if err == relay.VERSIONS else err))
            continue
        relay.relay(hub, name, ["check", "--timer"] if timer else ["check"])
    if hub.transitional and timer:
        (app,) = hub.apps
        hub.ctx.notify.send(Message(TRANSITIONAL.format(app=app, user=getpass.getuser())))
    talaria_release(hub)
    return 0


def talaria_release(hub) -> None:
    ctx = hub.ctx
    try:
        latest = latest_semver(ctx.sh, ctx.conf.talaria_repo)
    except CommandError:
        return
    if not latest or not semver_newer(latest, f"v{__version__}"):
        return
    st = load_state(ctx.paths)
    if st.get("talaria_notified") == latest:
        return
    hubupdate.offer(hub, latest)
    st["talaria_notified"] = latest
    write_json_atomic(ctx.paths.hub_state, st)
```

`talaria/cli.py`:
- in `build_parser`, after the `self-update` line, add

```python
    r = sub.add_parser("relay")                  # hub only: run a long op, send its messages
    r.add_argument("app")
    r.add_argument("op", nargs=argparse.REMAINDER)
    u = sub.add_parser("update")                 # hub only
    u.add_argument("tag", type=_semver)
    u.add_argument("--offer", action="store_true")
```

- add module constants and `_hub_main`, and replace `main` up to `ctx = make()` with:

```python
HUB_CMDS = ("set-token", "bot", "relay", "check", "status", "self-update", "update")
TRANSITIONAL_CMDS = ("bot", "relay", "check", "self-update", "update")


def _hub_main(hub, args) -> int:
    cmd = args.cmd
    if cmd == "set-token":
        from talaria import setup
        return setup.set_token(hub.ctx)
    if cmd == "bot":
        from talaria import telegram
        return telegram.run(hub)
    if cmd in ("relay", "status"):
        from talaria import relay
        if cmd == "relay":
            return relay.main(hub, args.app, args.op)
        print(relay.status_all(hub))
        return 0
    if cmd == "check":
        from talaria import hubcheck
        return hubcheck.check(hub, timer=args.timer)
    from talaria import hubupdate
    if cmd == "self-update":
        return hubupdate.self_update(hub, args.tag)
    return hubupdate.offer(hub, args.tag) if args.offer else hubupdate.start_update(hub, args.tag)


def main(argv: list[str] | None = None, make=make_ctx, make_hub=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["op"]:
        # sudo may keep the hub's environment: talk to this account's own user bus
        os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
        from talaria import op
        return op.main(argv[1:])
    args = build_parser().parse_args(argv)
    # `sudo -u hermes talaria …` has no XDG_RUNTIME_DIR, which systemctl --user needs
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "setup":
        from talaria import setup
        return setup.setup(args)
    if make_hub is None:     # a caller that injects an app ctx (tests) is an app install
        from talaria import hubexec
        make_hub = hubexec.load_hub if make is make_ctx else (lambda: None)
    hub = make_hub()
    if hub is not None:
        if args.cmd in (TRANSITIONAL_CMDS if hub.transitional else HUB_CMDS):
            return _hub_main(hub, args)
        if not hub.transitional:
            print(f"talaria {args.cmd}: this account is the Talaria hub; app commands run as "
                  "the app's account or through the bot", file=sys.stderr)
            return 2
    if args.cmd in ("relay", "update"):
        print(f"talaria {args.cmd}: this account is not a Talaria hub", file=sys.stderr)
        return 2
    ctx = make()
```

(the rest of `main` — tag validation, `set-token`, `bot` refusal from Task 5, `self-update`, `status`, … `return run_locked(ctx, args)` — stays.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add talaria/hubcheck.py talaria/hubupdate.py talaria/cli.py tests/test_hubcheck.py \
        tests/test_hubupdate.py tests/test_cli_hub.py tests/test_cli_ops.py
git commit -m "Hub timer, Talaria update offer and host-wide self-update; CLI role routing"
```

---
### Task 8: Setup — one root paste, hub phase, app registration, app phase without a bot

**Files:**
- Modify: `talaria/setup.py` (root block builders, `operator_phase`, `service_phase`, new `hub_phase`, `_telegram_ready`, `setup()` dispatch), `talaria/units.py` (`install_units` writes only the Quadlet; `render_hub_units`, `install_hub_units`), `talaria/cli.py` (setup flags `--hub`, `--as-hub`, `--register`, `--import-telegram`)
- Create: `tests/test_setup_hub.py`
- Test: `tests/test_setup.py`, `tests/test_setup_golden.py`, `tests/test_setup_service_golden.py`, `tests/test_units.py`, `tests/test_cli_ops.py`, `tests/test_setup_hub.py`

**Interfaces:**
- Consumes: `hubconf.NAME_RE`, `hubconf.register_app(paths, app, user) -> bool`, `hubconf.load_hub_conf(paths)`, `ctx.make_hub_ctx()`, `Paths.hub_conf` (Task 1); `telegram.pair`, `telegram.new_code`, `telegram.TelegramAPI` (Task 5, unchanged); `units._tpl`, `units._write`, `units.TALARIA_UNITS`.
- Produces:
  - `setup.HUB = "talaria"`; `setup.PASTE` (the "paste this…" text); `setup.account_lines(user, operator, create) -> list[str]`; `setup.op_rule_lines(hub, user) -> list[str]`; `setup.root_block(lines: list[str]) -> str`; `setup.sudo_as(user, home) -> list[str]`.
  - `setup.operator_phase(sh, args, *, getpwnam, operator, call, linger_dir, app, explicit_app) -> int`: installs Talaria for app and hub, probes the op rule, hands over to `setup --as-service` (app) then `setup --as-hub --register <app>:<user>` (hub), prints `DONE`.
  - `setup.hub_phase(ctx, args, api=None) -> int` (`args.register: str | None`, `args.import_telegram: bool` — the latter is implemented in Task 9; here it is read with `getattr(args, "import_telegram", False)` and ignored).
  - `setup.HUB_CONF_HEAD = "# Talaria hub settings; see README.\n"`.
  - `setup.service_phase(ctx, args) -> int` (no `api` parameter any more; no token, pairing, bot units or `DONE`).
  - `units.install_units(ctx) -> bool` (Quadlet only), `units.render_hub_units(ctx) -> dict[str, str]`, `units.install_hub_units(ctx) -> bool`, `units.HUB_TITLE = "new"`.
  - Setup argparse: `--hub` (default `"talaria"`), `--as-hub`, `--register APP:USER`, `--import-telegram` (the last three hidden).

- [ ] **Step 1: Update the existing tests and write the new ones**

`tests/test_units.py` — replace `test_install_reports_quadlet_change` with:

```python
def test_install_reports_quadlet_change_and_writes_no_bot_units(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl")
    assert units.install_units(ctx) is True
    assert units.install_units(ctx) is False
    assert ctx.paths.quadlet.exists()
    assert not (ctx.paths.units_dir / "talaria-telegram.service").exists()
    assert not (ctx.paths.units_dir / "talaria-check.timer").exists()
    assert ctx.sh.called("systemctl", "--user", "daemon-reload")


def _hub_ctx(check_time="04:30"):
    from talaria.hubconf import HubConf
    return Ctx(paths=Paths(FIXED_HOME), conf=HubConf(check_time=check_time), sh=None,
               notify=None)


def test_hub_units_exact():
    r = units.render_hub_units(_hub_ctx("03:15"))
    assert r["talaria-check.service"] == (
        "[Unit]\nDescription=Talaria: check for new releases\n\n[Service]\nType=oneshot\n"
        "ExecStart=%h/.local/bin/talaria check --timer\n")
    assert r["talaria-check.timer"] == (
        "[Unit]\nDescription=Talaria: daily release check\n\n[Timer]\n"
        "OnCalendar=*-*-* 03:15\nRandomizedDelaySec=15m\nPersistent=true\n\n[Install]\n"
        "WantedBy=timers.target\n")
    assert r["talaria-telegram.service"] == (GOLDEN / "default.talaria-telegram.service").read_text()


def test_install_hub_units_reports_changes(tmp_path):
    from talaria.hubconf import HubConf
    from tests.fakes import FakeShell
    ctx = Ctx(paths=Paths(tmp_path), conf=HubConf(), sh=FakeShell().on("systemctl"), notify=None)
    assert units.install_hub_units(ctx) is True
    assert units.install_hub_units(ctx) is False
    assert sorted(p.name for p in ctx.paths.units_dir.iterdir()) == list(units.TALARIA_UNITS)
    ctx.conf.check_time = "05:00"
    assert units.install_hub_units(ctx) is True
    assert ctx.sh.calls == [["systemctl", "--user", "daemon-reload"]] * 3
```

`tests/test_cli_ops.py` — replace `test_parser_setup_flags` with:

```python
def test_parser_setup_flags():
    base = {"cmd": "setup", "plan": False, "user": None, "adopt": None, "dev": False,
            "app": None, "as_service": False, "hub": "talaria", "as_hub": False,
            "register": None, "import_telegram": False}
    assert parse("setup") == base
    assert parse("setup", "--plan", "--user", "h", "--adopt", "u.service", "--dev",
                 "--app", "clawvisor", "--as-service", "--hub", "hub2", "--as-hub",
                 "--register", "clawvisor:h", "--import-telegram") == {
        **base, "plan": True, "user": "h", "adopt": "u.service", "dev": True,
        "app": "clawvisor", "as_service": True, "hub": "hub2", "as_hub": True,
        "register": "clawvisor:h", "import_telegram": True}
```

`tests/test_setup.py`:
- replace `args`, `PW` and `op_env` with:

```python
def args(**kw):
    base = dict(plan=False, user=None, adopt=None, dev=False, app=None, as_service=False,
                hub="talaria", as_hub=False, register=None, import_telegram=False)
    base.update(kw)
    return argparse.Namespace(**base)


PW = SimpleNamespace(pw_name="hermes", pw_uid=1001, pw_gid=1001, pw_dir="/home/hermes")
HUBPW = SimpleNamespace(pw_name="talaria", pw_uid=1002, pw_gid=1002, pw_dir="/home/talaria")


def op_env(monkeypatch, tmp_path, *, user_exists=True, sudo_ok=True, linger=True,
           installed=False, tag="v0.1.0", hub_exists=True, hub_sudo_ok=True, hub_linger=True,
           hub_installed=True, probe_rc=0, probe_err=""):
    monkeypatch.setattr(setup, "which", lambda t: f"/usr/bin/{t}")
    sh = FakeShell()
    sh.on("sudo")   # catch-all first: in FakeShell the most recently added rule wins
    sh.on("podman", "--version", out="podman version 4.9.3\n")
    sh.on("getenforce", out="Permissive\n")
    sh.on("sudo", "-n", "-u", "hermes", "true", rc=0 if sudo_ok else 1)
    sh.on("sudo", "-n", "-u", "hermes", "test", rc=0 if installed else 1)
    sh.on("sudo", "-n", "-u", "talaria", "true", rc=0 if hub_sudo_ok else 1)
    sh.on("sudo", "-n", "-u", "talaria", "test", rc=0 if hub_installed else 1)
    sh.on("sudo", "-n", "-u", "talaria", "sudo", rc=probe_rc, err=probe_err)
    sh.on("git", "-C", str(setup.REPO), "describe", out=f"{tag}\n", rc=0 if tag else 128)
    sh.on("git", "-C", str(setup.REPO), "remote", out="https://github.com/o/talaria\n")
    ld = tmp_path / "linger"
    ld.mkdir()
    if linger:
        (ld / "hermes").touch()
    if hub_linger:
        (ld / "talaria").touch()
    accounts = {"hermes": PW if user_exists else None, "talaria": HUBPW if hub_exists else None}

    def getpwnam(name):
        if accounts.get(name) is None:
            raise KeyError(name)
        return accounts[name]

    calls = []
    run = lambda a: setup.operator_phase(sh, a, getpwnam=getpwnam, operator="admin",
                                         call=lambda argv: calls.append(argv) or 0,
                                         linger_dir=ld)
    return sh, run, calls
```

- in `test_new_user_gets_one_root_block` add `assert 'talaria ALL=(hermes) NOPASSWD: $home/.local/bin/talaria op *' in out`;
- in `test_installs_and_hands_over` add at the end:

```python
    assert calls[1][-4:] == ["setup", "--as-hub", "--register", "hermes:hermes"]
    assert calls[1][:5] == ["sudo", "-n", "-u", "talaria", "-H"]
    assert "XDG_RUNTIME_DIR=/run/user/1002" in calls[1]
```

- in `test_plan_names_the_app_passed_in` change the expected substring to
  `"then: detect Clawvisor (fresh or adopt), secrets, units, start Clawvisor, verify"`;
- in `test_clawvisor_default_account_name_and_plan_text` and `test_clawvisor_handoff_carries_the_app_flag` add `(ld / "talaria").touch()` after `(ld / "clawvisor").touch()`;
- replace `test_fresh_install_end_to_end` and delete `test_missing_token_asks_person` (it moves to `tests/test_setup_hub.py`):

```python
def test_fresh_install_end_to_end(svc, capsys):
    rc = setup.service_phase(svc, args(as_service=True))
    out = capsys.readouterr().out
    assert rc == 0 and "DONE" not in out and "/pair" not in out, out
    assert state.load(svc.paths)["current"]["tag"] == "v2026.9.24"
    assert not svc.paths.env_file.exists()            # no bot token in an app install
    pw = parse_kv(svc.paths.hermes_env.read_text())["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"]
    assert len(pw) >= 24 and pw not in out
    assert svc.paths.quadlet.exists()
    assert not [c for c in svc.sh.calls if "talaria-telegram.service" in c
                or "talaria-check.timer" in c]


def test_check_time_in_an_app_conf_is_noted(svc, capsys):
    svc.paths.conf_dir.mkdir(parents=True)
    svc.paths.conf_file.write_text("check.time = 03:00\n")
    assert setup.service_phase(svc, args(as_service=True)) == 0
    assert ("NOTE: check.time in talaria.conf is not used any more; set it in the hub's "
            "hub.conf\n") in capsys.readouterr().out


def test_operator_phase_refuses_the_hubs_account_for_an_app(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    assert run(args(user="talaria")) == 1 and sh.calls == []
    assert capsys.readouterr().out == ("STOP: talaria is the hub's account; the app needs an "
                                       "account of its own (--user)\n")


def test_operator_phase_validates_the_hub_name(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    assert run(args(user="hermes", hub="a b")) == 1
    assert capsys.readouterr().out == "STOP: not a valid account name: 'a b'\n"


def test_app_phase_failure_skips_the_hub(monkeypatch, tmp_path, capsys):
    sh, _, _ = op_env(monkeypatch, tmp_path)
    calls = []
    rc = setup.operator_phase(sh, args(user="hermes"),
                              getpwnam=lambda n: {"hermes": PW, "talaria": HUBPW}[n],
                              operator="admin", linger_dir=tmp_path / "linger",
                              call=lambda argv: calls.append(argv) or 10)
    assert rc == 10 and len(calls) == 1 and "DONE" not in capsys.readouterr().out


def test_hub_phase_failure_is_returned_without_done(monkeypatch, tmp_path, capsys):
    sh, _, _ = op_env(monkeypatch, tmp_path)
    rcs = [0, 10]
    rc = setup.operator_phase(sh, args(user="hermes"),
                              getpwnam=lambda n: {"hermes": PW, "talaria": HUBPW}[n],
                              operator="admin", linger_dir=tmp_path / "linger",
                              call=lambda argv: rcs.pop(0))
    assert rc == 10 and "DONE" not in capsys.readouterr().out


def test_probe_failure_that_is_not_sudo_stops(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, probe_rc=1,
                            probe_err="Traceback (most recent call last):\nKeyError: 'x'\n")
    assert run(args(user="hermes")) == 1 and calls == []
    assert capsys.readouterr().out.endswith(
        "STOP: Talaria for hermes does not answer: Traceback (most recent call last):\n"
        "KeyError: 'x'\n")
```

- replace the three root-block tests at the end (`_block`, `test_root_block_is_one_quoted_heredoc_paste`, `test_root_block_validates_sudoers_before_installing`, `test_e2e_extracts_exactly_what_setup_prints`) with:

```python
def _full_block():
    return setup.root_block(setup.account_lines("talaria", "admin", True)
                            + setup.account_lines("hermes", "admin", False)
                            + setup.op_rule_lines("talaria", "hermes"))


def test_root_block_is_one_quoted_heredoc_paste():
    b = _full_block()
    lines = b.splitlines()
    assert lines[0] == "sudo bash -euo pipefail <<'TALARIA'" and lines[-1] == "TALARIA"
    assert lines[-2] == "echo 'Talaria: root step done'"
    assert b.count("TALARIA") == 2   # only opener and closer
    assert "chmod" not in b


def test_every_sudoers_rule_is_validated_before_it_is_installed():
    lines = _full_block().splitlines()
    installs = [i for i, l in enumerate(lines) if l.startswith("install -m 440")]
    checks = [i for i, l in enumerate(lines) if l.startswith("visudo -cf")]
    assert len(installs) == len(checks) == 3
    assert all(c == i - 1 for c, i in zip(checks, installs))
    assert "> /etc/sudoers.d" not in _full_block()     # never written in place


def test_op_rule_lines_exact():
    assert setup.op_rule_lines("talaria", "hermes") == [
        "home=$(getent passwd hermes | cut -d: -f6)", 'test -n "$home"', "tmp=$(mktemp)",
        'echo "talaria ALL=(hermes) NOPASSWD: $home/.local/bin/talaria op *" > "$tmp"',
        'visudo -cf "$tmp"',
        'install -m 440 -o root -g root "$tmp" /etc/sudoers.d/talaria-talaria-hermes',
        'rm -f "$tmp"']


def test_op_rule_paste_writes_the_real_home_and_no_glob(tmp_path):
    """Run the op-rule lines in bash with root-only commands stubbed: the rule must carry
    the account's home from the passwd database, and `*` must stay a literal."""
    out = tmp_path / "rule"
    (tmp_path / "x").touch()          # a glob would expand to this
    stubs = ("getent() { echo 'hermes:x:1:1::/srv/hermes:/bin/bash'; }\n"
             "visudo() { :; }\n"
             f"install() {{ cp \"$7\" {out}; }}\n")
    import subprocess
    r = subprocess.run(["bash", "-euo", "pipefail", "-c",
                        stubs + "\n".join(setup.op_rule_lines("talaria", "hermes"))],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert out.read_text() == "talaria ALL=(hermes) NOPASSWD: /srv/hermes/.local/bin/talaria op *\n"


@pytest.mark.parametrize("kw,a", [(dict(user_exists=False), dict()),
                                  (dict(sudo_ok=False), dict(user="hermes")),
                                  (dict(hub_exists=False), dict(user="hermes"))])
def test_e2e_extracts_exactly_what_setup_prints(monkeypatch, tmp_path, capsys, kw, a):
    from tests.e2e.conftest import root_block as extract
    sh, run, _ = op_env(monkeypatch, tmp_path, **kw)
    assert run(args(**a)) == 10
    out = capsys.readouterr().out
    block = extract(out)
    assert block.startswith("sudo bash -euo pipefail <<'TALARIA'\n") and block.endswith("\nTALARIA")
    assert out.endswith(block + "\n")
```

`tests/test_setup_golden.py` — replace everything from the line `R = str(setup.REPO)` down to and including `test_operator_phase_exact` with:

```python
R = str(setup.REPO)
INSTALL = "/home/hermes/.local/share/talaria"
HUBINSTALL = "/home/talaria/.local/share/talaria"
ENVU = ["env", "-u", "XDG_CONFIG_HOME", "-u", "XDG_DATA_HOME", "-u", "XDG_STATE_HOME", "-u",
        "XDG_CACHE_HOME"]
SUDO = ["sudo", "-n", "-u", "hermes", "-H", *ENVU, "HOME=/home/hermes"]
HSUDO = ["sudo", "-n", "-u", "talaria", "-H", *ENVU, "HOME=/home/talaria"]
PRE = [(["podman", "--version"], None), (["getenforce"], None)]
CHECKS = [(["sudo", "-n", "-u", "hermes", "true"], None),
          (["sudo", "-n", "-u", "hermes", "test", "-e", INSTALL], None)]
HUB_CHECKS = [(["sudo", "-n", "-u", "talaria", "true"], None),
              (["sudo", "-n", "-u", "talaria", "test", "-e", HUBINSTALL], None)]
TAG = [(["git", "-C", R, "describe", "--tags", "--exact-match"], None)]
ORIGIN = [(["git", "-C", R, "remote", "get-url", "origin"], None)]


def install_cmds(sudo, user, install, clone):
    out = [(sudo + ["git", "clone", "-q", "https://github.com/o/talaria", install], 600)] if clone else []
    return out + [
        (sudo + ["git", "-C", install, "fetch", "-q", "--tags", "origin"], 600),
        (sudo + ["git", "-C", install, "checkout", "-q", "v0.1.0"], None),
        (sudo + ["mkdir", "-p", f"/home/{user}/.local/bin"], None),
        (sudo + ["ln", "-sfn", f"{install}/bin/talaria", f"/home/{user}/.local/bin/talaria"], None)]


CLONE = install_cmds(SUDO, "hermes", INSTALL, True)[:1]
UPDATE = install_cmds(SUDO, "hermes", INSTALL, False)
HUB_CLONE = install_cmds(HSUDO, "talaria", HUBINSTALL, True)[:1]
HUB_UPDATE = install_cmds(HSUDO, "talaria", HUBINSTALL, False)
PROBE = [(["sudo", "-n", "-u", "talaria", "sudo", "-n", "-H", "-u", "hermes",
           "/home/hermes/.local/bin/talaria", "op", "hello"], None)]
HANDOFF = SUDO + ["XDG_RUNTIME_DIR=/run/user/1001",
                  "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus",
                  "/home/hermes/.local/bin/talaria", "setup", "--as-service", "--app", "hermes"]
HUB_HANDOFF = HSUDO + ["XDG_RUNTIME_DIR=/run/user/1002",
                       "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1002/bus",
                       "/home/talaria/.local/bin/talaria", "setup", "--as-hub", "--register",
                       "hermes:hermes"]
INSTALLED = "OK: Talaria v0.1.0 installed for hermes\nOK: Talaria v0.1.0 installed for talaria\n"


def acct(user, create):
    head = (f"id {user} >/dev/null 2>&1 || useradd --create-home --shell /bin/bash {user}\n"
            f"grep -q '^{user}:' /etc/subuid || echo 'WARNING: {user} has no subuid range;"
            " see README'\n") if create else ""
    return head + (f"loginctl enable-linger {user}\n"
                   "tmp=$(mktemp)\n"
                   f"echo 'admin ALL=({user}) NOPASSWD: ALL' > \"$tmp\"\n"
                   "visudo -cf \"$tmp\"\n"
                   f"install -m 440 -o root -g root \"$tmp\" /etc/sudoers.d/talaria-{user}\n"
                   "rm -f \"$tmp\"\n")


OPRULE = ("home=$(getent passwd hermes | cut -d: -f6)\n"
          "test -n \"$home\"\n"
          "tmp=$(mktemp)\n"
          "echo \"talaria ALL=(hermes) NOPASSWD: $home/.local/bin/talaria op *\" > \"$tmp\"\n"
          "visudo -cf \"$tmp\"\n"
          "install -m 440 -o root -g root \"$tmp\" /etc/sudoers.d/talaria-talaria-hermes\n"
          "rm -f \"$tmp\"\n")


def root_cmd(body):
    return ("sudo bash -euo pipefail <<'TALARIA'\n" + body
            + "echo 'Talaria: root step done'\nTALARIA\n")


ASK = "ACTION REQUIRED: paste this into your terminal (sudo asks for your password), then run setup again"


def run_case(monkeypatch, tmp_path, capsys, kw, a):
    sh, run, calls = op_env(monkeypatch, tmp_path, **kw)
    rc = run(args(**a))
    return rc, capsys.readouterr().out, list(zip(sh.calls, sh.timeouts)), calls


CASES = {
    "install": (dict(), dict(user="hermes", adopt="hermes-gateway.service"), 0,
                INSTALLED + "DONE\n",
                PRE + CHECKS + HUB_CHECKS + TAG + ORIGIN + CLONE + UPDATE + HUB_UPDATE + PROBE,
                [HANDOFF + ["--adopt", "hermes-gateway.service"], HUB_HANDOFF]),
    "installed": (dict(installed=True), dict(), 0, INSTALLED + "DONE\n",
                  PRE + CHECKS + HUB_CHECKS + TAG + ORIGIN + UPDATE + HUB_UPDATE + PROBE,
                  [HANDOFF, HUB_HANDOFF]),
    "hubfresh": (dict(installed=True, hub_installed=False), dict(), 0, INSTALLED + "DONE\n",
                 PRE + CHECKS + HUB_CHECKS + TAG + ORIGIN + UPDATE + HUB_CLONE + HUB_UPDATE
                 + PROBE, [HANDOFF, HUB_HANDOFF]),
    "newuser": (dict(user_exists=False), dict(), 10,
                ASK + " with --user hermes:\n" + root_cmd(acct("hermes", True) + OPRULE),
                PRE + HUB_CHECKS, []),
    "newhost": (dict(user_exists=False, hub_exists=False), dict(), 10,
                ASK + " with --user hermes:\n"
                + root_cmd(acct("talaria", True) + acct("hermes", True) + OPRULE), PRE, []),
    "nohub": (dict(hub_exists=False), dict(user="hermes"), 10,
              ASK + ":\n" + root_cmd(acct("talaria", True) + OPRULE), PRE + CHECKS, []),
    "hubnolinger": (dict(hub_linger=False), dict(user="hermes"), 10,
                    ASK + ":\n" + root_cmd(acct("talaria", False) + OPRULE),
                    PRE + CHECKS + HUB_CHECKS, []),
    "hubnosudo": (dict(hub_sudo_ok=False), dict(user="hermes"), 10,
                  ASK + ":\n" + root_cmd(acct("talaria", False) + OPRULE),
                  PRE + CHECKS + HUB_CHECKS[:1], []),
    "confirm": (dict(), dict(), 1,
                "FOUND: account hermes exists but Talaria is not installed for it\n"
                "STOP: confirm with the person, then re-run with --user hermes\n",
                PRE + CHECKS, []),
    "sudo": (dict(sudo_ok=False), dict(user="hermes"), 10,
             ASK + ":\n" + root_cmd(acct("hermes", False) + OPRULE),
             PRE + CHECKS[:1] + HUB_CHECKS, []),
    "nolinger": (dict(linger=False), dict(user="hermes"), 10,
                 ASK + ":\n" + root_cmd(acct("hermes", False) + OPRULE),
                 PRE + CHECKS + HUB_CHECKS, []),
    "norule": (dict(probe_rc=1, probe_err="sudo: a password is required\n"), dict(user="hermes"),
               10, INSTALLED + ASK + ":\n" + root_cmd(OPRULE),
               PRE + CHECKS + HUB_CHECKS + TAG + ORIGIN + CLONE + UPDATE + HUB_UPDATE + PROBE, []),
    "plan": (dict(), dict(user="hermes", plan=True), 0,
             "PLAN: install Talaria v0.1.0 for hermes from https://github.com/o/talaria\n"
             "PLAN: then: detect Hermes (fresh or adopt), dashboard password, units, start "
             "Hermes, verify\n"
             "PLAN: then: install the same Talaria for the hub talaria and register hermes with "
             "it (Telegram bot token and pairing, once per host)\n",
             PRE + CHECKS + HUB_CHECKS + TAG + ORIGIN, []),
    "planroot": (dict(hub_exists=False), dict(user="hermes", plan=True), 0,
                 "PLAN: accounts, linger and sudo rules for talaria and hermes: setup prints a "
                 "block to run as root\n", PRE + CHECKS, []),
    "notag": (dict(tag=None), dict(user="hermes"), 1,
              "STOP: this checkout is not at a release tag; check out the latest tag "
              "(or pass --dev)\n", PRE + CHECKS + HUB_CHECKS + TAG, []),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_operator_phase_exact(name, monkeypatch, tmp_path, capsys):
    kw, a, rc, out, cmds, handoff = CASES[name]
    got_rc, got_out, got_cmds, got_handoff = run_case(monkeypatch, tmp_path, capsys, kw, a)
    assert got_out == out
    assert got_cmds == cmds
    assert got_handoff == handoff
    assert got_rc == rc
```

and in `test_dev_install_exact` change the output assertion to
`assert capsys.readouterr().out == ("OK: Talaria abc123 installed for hermes\nOK: Talaria abc123 installed for talaria\nDONE\n")`.

`tests/test_setup_service_golden.py`:
- delete the constants `PAIR` and `PAIRED`, and remove `+ PAIR + PAIRED` and `+ PAIR` from every expected output in the file;
- set `RUNNING` to end without `DONE`:

```python
RUNNING = (f"OK: Hermes is running. Dashboard: http://127.0.0.1:9119 (user admin, "
           f"password in {H}/.config/talaria/hermes.env)\n")
UNITS = [(["systemctl", "--user", "daemon-reload"], None),
         (["podman", "tag", "sha256:n", "localhost/hermes-agent:current"], None)]
```

- in `test_fresh_exact` replace `assert s.paths.env_file.read_text() == "TALARIA_TELEGRAM_USER_ID=42\n"` with `assert not s.paths.env_file.exists()`;
- delete `test_no_token_exact`, `test_pair_fail_exact`, `test_pair_uses_configured_api`, `test_paired_user_without_username`, `test_already_paired_skips_pairing` (their hub equivalents are in `tests/test_setup_hub.py`);
- in `test_plan_exact` expect `"PLAN: dashboard password, install units, start Hermes, verify\n"`; in `test_clawvisor_plan_text_uses_secrets_not_dashboard_password` expect `"PLAN: secrets, install units, start Clawvisor, verify\n"`;
- in `test_busy_lock_stops` delete the line `s.conf.telegram_user_id = 42`.

New `tests/test_setup_hub.py`:

```python
import argparse

import pytest

from talaria import setup, units
from talaria.conf import parse_kv
from talaria.ctx import Ctx, Paths
from talaria.hubconf import load_hub_conf
from tests.fakes import Clock, FakeNotifier, FakeShell

TOKEN = "1:" + "a" * 35
PAIR = ("ACTION REQUIRED: in a private chat with your bot, send within 15 minutes:\n"
        "  /pair CODE2345\n")
UNITS = [["systemctl", "--user", "daemon-reload"],
         ["systemctl", "--user", "enable", "--now", "talaria-check.timer",
          "talaria-telegram.service"]]
RESTART = [["systemctl", "--user", "restart", "talaria-telegram.service"]]


def hargs(**kw):
    return argparse.Namespace(**{"register": None, "import_telegram": False, **kw})


def hub_ctx(tmp_path, env=""):
    home = tmp_path / "hub"
    home.mkdir()
    p = Paths(home)
    if env:
        p.conf_dir.mkdir(parents=True)
        p.env_file.write_text(env)
    clock = Clock()
    ctx = Ctx(paths=p, conf=load_hub_conf(p), sh=FakeShell().on("systemctl"),
              notify=FakeNotifier(), sleep=clock.sleep, now=clock.now)
    return ctx


@pytest.fixture
def paired(monkeypatch):
    monkeypatch.setattr(setup.getpass, "getuser", lambda: "talaria")
    monkeypatch.setattr(setup.telegram, "new_code", lambda: "CODE2345")
    seen = []
    monkeypatch.setattr(setup.telegram, "TelegramAPI", lambda base, token: seen.append((base, token)))
    monkeypatch.setattr(setup.telegram, "pair",
                        lambda c, api, code, timeout_s=900, announce=lambda: None: (
                            announce(), {"id": 42, "first_name": "Ann", "username": "ann"})[1])
    return seen


def run(ctx, capsys, **a):
    rc = setup.hub_phase(ctx, hargs(**a))
    return rc, capsys.readouterr().out.replace(str(ctx.paths.home), "~H"), ctx.sh.calls


def test_first_run_registers_and_asks_for_the_token(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path)
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert rc == 10 and cmds == []
    assert out == ("ACTION REQUIRED: create a Telegram bot: open @BotFather, send /newbot, copy "
                   "the token. Then, in your own terminal (not through an agent), run:\n"
                   "  sudo -u talaria -H ~H/.local/bin/talaria set-token\n")
    assert ctx.paths.hub_conf.read_text() == ("# Talaria hub settings; see README.\n"
                                              "apps = hermes:hermes\n")
    assert (ctx.paths.conf_dir.stat().st_mode & 0o777) == 0o700
    assert (ctx.paths.state_dir.stat().st_mode & 0o777) == 0o700


def test_pairing_then_units_and_bot(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n")
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert rc == 0
    assert out == PAIR + "OK: paired with Ann (@ann)\nOK: Talaria hub ready; apps: hermes\n"
    assert cmds == UNITS + RESTART
    assert parse_kv(ctx.paths.env_file.read_text())["TALARIA_TELEGRAM_USER_ID"] == "42"
    assert paired == [(ctx.conf.telegram_api, TOKEN)]
    assert sorted(p.name for p in ctx.paths.units_dir.iterdir()) == list(units.TALARIA_UNITS)


def test_rerun_unchanged_does_not_restart_the_bot(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n")
    run(ctx, capsys, register="hermes:hermes")
    ctx.sh.calls.clear()
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert (rc, out, cmds) == (0, "OK: Talaria hub ready; apps: hermes\n", UNITS)


def test_registering_another_app_restarts_the_bot(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n")
    run(ctx, capsys, register="hermes:hermes")
    ctx.sh.calls.clear()
    rc, out, cmds = run(ctx, capsys, register="clawvisor:clawvisor")
    assert rc == 0 and out == "OK: Talaria hub ready; apps: hermes, clawvisor\n"
    assert cmds == UNITS + RESTART


def test_without_register_re_renders_only(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n")
    rc, out, cmds = run(ctx, capsys)
    assert rc == 0 and out == "OK: Talaria hub ready; apps: none yet\n"
    assert ctx.paths.hub_conf.read_text() == "# Talaria hub settings; see README.\n"


@pytest.mark.parametrize("reg,msg", [
    ("hermes:other", "already registers hermes for the account hermes"),
    ("clawvisor:hermes", "the account hermes is registered twice"),
    ("nope:x", "'nope:x' is not <app>:<user>"),
])
def test_conflicting_registration_stops(tmp_path, paired, capsys, reg, msg):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n")
    run(ctx, capsys, register="hermes:hermes")
    ctx.sh.calls.clear()
    rc, out, cmds = run(ctx, capsys, register=reg)
    assert rc == 1 and out.startswith("STOP: ") and msg in out and cmds == []


def test_no_pair_message_stops(tmp_path, paired, monkeypatch, capsys):
    monkeypatch.setattr(setup.telegram, "pair",
                        lambda c, api, code, timeout_s=900, announce=lambda: None: (announce(), None)[1])
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n")
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert rc == 10 and cmds == []
    assert out == PAIR + "STOP: no /pair message arrived; run setup again for a new code\n"


def test_paired_user_without_username(tmp_path, paired, monkeypatch, capsys):
    monkeypatch.setattr(setup.telegram, "pair",
                        lambda c, api, code, timeout_s=900, announce=lambda: None: {"id": 7})
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n")
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert "OK: paired with  (@-)\n" in out


def test_setup_dispatches_as_hub(monkeypatch):
    seen = []
    monkeypatch.setattr(setup, "hub_phase", lambda c, a: (seen.append(a.register), 0)[1])
    monkeypatch.setattr("talaria.ctx.make_hub_ctx", lambda *a, **k: "CTX")
    from tests.test_setup import args
    assert setup.setup(args(as_hub=True, register="hermes:hermes")) == 0
    assert seen == ["hermes:hermes"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_setup.py tests/test_setup_golden.py tests/test_setup_service_golden.py tests/test_setup_hub.py tests/test_units.py tests/test_cli_ops.py`
Expected: FAIL (`setup.account_lines` missing, `hub_phase` missing, `units.render_hub_units` missing, unknown setup flags, app phase still pairs).

- [ ] **Step 3: Implement**

`talaria/cli.py` — in `build_parser`, after `s.add_argument("--as-service", …)`:

```python
    s.add_argument("--hub", default="talaria", help="the hub's account (default talaria)")
    s.add_argument("--as-hub", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--register", metavar="APP:USER", help=argparse.SUPPRESS)
    s.add_argument("--import-telegram", action="store_true", help=argparse.SUPPRESS)
```

`talaria/units.py` — replace `render_units` and `install_units` with:

```python
HUB_TITLE = "new"     # "Talaria: check for new releases": the hub checks every app


def _render(ctx, title: str) -> dict[str, str]:
    return {name: _tpl(ctx, name).substitute(check_time=ctx.conf.check_time, title=title)
            for name in TALARIA_UNITS}


def render_units(ctx) -> dict[str, str]:
    """The units a v0.4 app install has on disk (pinned by the golden tests)."""
    return _render(ctx, ctx.app.title)


def render_hub_units(ctx) -> dict[str, str]:
    return _render(ctx, HUB_TITLE)
```

keep `_write`, then:

```python
def install_units(ctx) -> bool:
    """An app install gets only its Quadlet; the bot and the timer belong to the hub."""
    changed = _write(ctx.paths.quadlet, render_quadlet(ctx))
    ctx.sh.run(["systemctl", "--user", "daemon-reload"])
    return changed


def install_hub_units(ctx) -> bool:
    changed = False
    for name, text in render_hub_units(ctx).items():
        changed = _write(ctx.paths.units_dir / name, text) or changed
    ctx.sh.run(["systemctl", "--user", "daemon-reload"])
    return changed
```

`talaria/setup.py`:
- change the existing import lines to `from talaria.conf import check_bind, load_conf, parse_kv, write_env_value` and `from talaria.hubconf import NAME_RE, load_hub_conf, register_app` (the latter replaces Task 1's `NAME_RE`-only import);
- replace `root_block` with:

```python
HUB = "talaria"
HUB_CONF_HEAD = "# Talaria hub settings; see README.\n"
PASTE = "paste this into your terminal (sudo asks for your password), then run setup again"


def _sudoers(rule: str, name: str) -> list[str]:
    """Validate with visudo before it can break sudo; never written in place."""
    return ["tmp=$(mktemp)", f"echo {rule} > \"$tmp\"", "visudo -cf \"$tmp\"",
            f"install -m 440 -o root -g root \"$tmp\" /etc/sudoers.d/{name}", "rm -f \"$tmp\""]


def account_lines(user: str, operator: str, create: bool) -> list[str]:
    lines = []
    if create:
        lines += [f"id {user} >/dev/null 2>&1 || useradd --create-home --shell /bin/bash {user}",
                  f"grep -q '^{user}:' /etc/subuid || echo 'WARNING: {user} has no subuid range; see README'"]
    return lines + [f"loginctl enable-linger {user}",
                    *_sudoers(f"'{operator} ALL=({user}) NOPASSWD: ALL'", f"talaria-{user}")]


def op_rule_lines(hub: str, user: str) -> list[str]:
    """The hub may run `talaria op …` as the app and nothing else (spec §4.1). The app's
    home comes from the passwd database when the block runs."""
    return [f"home=$(getent passwd {user} | cut -d: -f6)", "test -n \"$home\"",
            *_sudoers(f"\"{hub} ALL=({user}) NOPASSWD: $home/.local/bin/talaria op *\"",
                      f"talaria-{hub}-{user}")]


def root_block(lines: list[str]) -> str:
    """One command the person pastes whole into their own terminal; sudo asks for the
    password. The quoted heredoc delimiter keeps their shell from expanding anything."""
    return "\n".join(["sudo bash -euo pipefail <<'TALARIA'", *lines,
                      "echo 'Talaria: root step done'", "TALARIA"])


def sudo_as(user: str, home: str) -> list[str]:
    # sudo may keep the caller's XDG_* dirs (e.g. on CI runners); podman and git must
    # use the service user's own
    return ["sudo", "-n", "-u", user, "-H", "env", "-u", "XDG_CONFIG_HOME", "-u",
            "XDG_DATA_HOME", "-u", "XDG_STATE_HOME", "-u", "XDG_CACHE_HOME", f"HOME={home}"]


def _bus(pw) -> list[str]:
    return [f"XDG_RUNTIME_DIR=/run/user/{pw.pw_uid}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{pw.pw_uid}/bus"]


def _pw(getpwnam, name):
    try:
        return getpwnam(name)
    except KeyError:
        return None


def _can_sudo(sh, user: str) -> bool:
    return sh.run(["sudo", "-n", "-u", user, "true"], check=False).returncode == 0


def _installed(sh, user: str, home: str) -> bool:
    return sh.run(["sudo", "-n", "-u", user, "test", "-e", f"{home}/.local/share/talaria"],
                  check=False).returncode == 0


def _install(sh, user: str, home: str, url: str, ref: str, installed: bool) -> None:
    install, sudo = f"{home}/.local/share/talaria", sudo_as(user, home)
    if not installed:
        sh.run(sudo + ["git", "clone", "-q", url, install], timeout=600)
    sh.run(sudo + ["git", "-C", install, "fetch", "-q", "--tags", "origin"], timeout=600)
    sh.run(sudo + ["git", "-C", install, "checkout", "-q", ref])
    sh.run(sudo + ["mkdir", "-p", f"{home}/.local/bin"])
    sh.run(sudo + ["ln", "-sfn", f"{install}/bin/talaria", f"{home}/.local/bin/talaria"])
    say("OK", f"Talaria {ref} installed for {user}")
```

- replace `operator_phase` with:

```python
def operator_phase(sh, args, *, getpwnam=pwd.getpwnam, operator=None, call=subprocess.call,
                   linger_dir=Path("/var/lib/systemd/linger"), app="hermes",
                   explicit_app=True) -> int:
    if app not in apps.NAMES:
        say("STOP", f"unknown app: {app!r}; choose one of {', '.join(apps.NAMES)}")
        return 1
    A = apps.get(app)
    operator = operator or getpass.getuser()
    user = args.user or app
    hub = getattr(args, "hub", None) or HUB
    for name in (user, operator, hub):
        if not NAME_RE.match(name):
            say("STOP", f"not a valid account name: {name!r}")
            return 1
    if user == hub:
        say("STOP", f"{hub} is the hub's account; the app needs an account of its own (--user)")
        return 1
    if prerequisites(sh):
        return 10
    if selinux_enforcing(sh):
        say("STOP", "SELinux is enforcing; Talaria v1 does not support that")
        return 1
    lingers = lambda name: (Path(linger_dir) / name).exists()
    pw = _pw(getpwnam, user)
    app_lines, installed = [], False
    if pw is None:
        if args.plan:
            say("PLAN", f"create the account {user}: setup prints a block to run as root")
            say("PLAN", f"then run setup again with --user {user} to install Talaria for it")
            return 0
        app_lines = account_lines(user, operator, create=True)
    else:
        sudo_ok = _can_sudo(sh, user)
        installed = sudo_ok and _installed(sh, user, pw.pw_dir)
        if not args.user and not installed:
            say("FOUND", f"account {user} exists but Talaria is not installed for it")
            say("STOP", f"confirm with the person, then re-run with --user {user}")
            return 1
        if not sudo_ok or not lingers(user):
            app_lines = account_lines(user, operator, create=False)
    hub_pw = _pw(getpwnam, hub)
    hub_lines, hub_installed = [], False
    if hub_pw is None:
        hub_lines = account_lines(hub, operator, create=True)
    else:
        hub_ok = _can_sudo(sh, hub)
        hub_installed = hub_ok and _installed(sh, hub, hub_pw.pw_dir)
        if not hub_ok or not lingers(hub):
            hub_lines = account_lines(hub, operator, create=False)
    if app_lines or hub_lines:
        if args.plan:
            say("PLAN", f"accounts, linger and sudo rules for {hub} and {user}: setup prints "
                        "a block to run as root")
            return 0
        again = f" with --user {user}" if pw is None else ""
        say("ACTION REQUIRED", f"{PASTE}{again}:\n"
            + root_block(hub_lines + app_lines + op_rule_lines(hub, user)))
        return 10
    tag = sh.run(["git", "-C", str(REPO), "describe", "--tags", "--exact-match"], check=False)
    if tag.returncode == 0:
        ref = tag.stdout.strip()
    elif args.dev:
        ref = sh.run(["git", "-C", str(REPO), "rev-parse", "HEAD"]).stdout.strip()
    else:
        say("STOP", "this checkout is not at a release tag; check out the latest tag "
                    "(or pass --dev)")
        return 1
    url = str(REPO) if args.dev else install_url(
        sh.run(["git", "-C", str(REPO), "remote", "get-url", "origin"],
               check=False).stdout.strip() or str(REPO))
    if url is None:
        say("STOP", "this checkout's origin needs SSH, but the service user has no key; "
                    "clone Talaria over https")
        return 1
    if args.plan:
        say("PLAN", f"install Talaria {ref} for {user} from {url}")
        say("PLAN", f"then: detect {A.title} (fresh or adopt), {A.prepare_summary}, units, "
                    f"start {A.title}, verify")
        say("PLAN", f"then: install the same Talaria for the hub {hub} and register {app} "
                    "with it (Telegram bot token and pairing, once per host)")
        return 0
    home, hub_home = pw.pw_dir, hub_pw.pw_dir
    _install(sh, user, home, url, ref, installed)
    _install(sh, hub, hub_home, url, ref, hub_installed)
    probe = sh.run(["sudo", "-n", "-u", hub, "sudo", "-n", "-H", "-u", user,
                    f"{home}/.local/bin/talaria", "op", "hello"], check=False)
    if probe.returncode != 0:
        if probe.stderr.lstrip().startswith("sudo:"):
            say("ACTION REQUIRED", f"{PASTE}:\n" + root_block(op_rule_lines(hub, user)))
            return 10
        say("STOP", f"Talaria for {user} does not answer: "
                    f"{(probe.stderr or probe.stdout).strip()[-300:]}")
        return 1
    rest = (["--app", app] if explicit_app else []) + \
        (["--adopt", args.adopt] if args.adopt else [])
    rc = call(sudo_as(user, home) + _bus(pw)
              + [f"{home}/.local/bin/talaria", "setup", "--as-service", *rest])
    if rc != 0:
        return rc
    rc = call(sudo_as(hub, hub_home) + _bus(hub_pw)
              + [f"{hub_home}/.local/bin/talaria", "setup", "--as-hub", "--register",
                 f"{app}:{user}"])
    if rc != 0:
        return rc
    print("DONE", flush=True)
    return 0
```

- extract the Telegram step of `service_phase` into a function and change `service_phase`:

```python
def _telegram_ready(ctx, api=None) -> int | None:
    """The bot token (stored by the person) and the pairing. None when both are there."""
    p = ctx.paths
    if not ctx.conf.telegram_token:
        say("ACTION REQUIRED",
            "create a Telegram bot: open @BotFather, send /newbot, copy the token. Then, in "
            "your own terminal (not through an agent), run:\n"
            f"  sudo -u {getpass.getuser()} -H {p.bin_link} set-token")
        return 10
    if not ctx.conf.telegram_user_id:
        api = api or telegram.TelegramAPI(ctx.conf.telegram_api, ctx.conf.telegram_token)
        code = telegram.new_code()
        who = telegram.pair(ctx, api, code, announce=lambda: say(
            "ACTION REQUIRED", f"in a private chat with your bot, send within 15 minutes:\n"
                               f"  /pair {code}"))
        if not who:
            say("STOP", "no /pair message arrived; run setup again for a new code")
            return 10
        write_env_value(p.env_file, "TALARIA_TELEGRAM_USER_ID", str(who["id"]))
        ctx.conf.telegram_user_id = who["id"]
        say("OK", f"paired with {who.get('first_name', '')} (@{who.get('username', '-')})")
    return None


def hub_phase(ctx, args, api=None) -> int:
    """The hub account's own setup (spec §7.2, §7.3): hub.conf, bot token and pairing,
    the bot and timer units. `--register app:user` adds an app."""
    p = ctx.paths
    ensure_dir(p.conf_dir)
    ensure_dir(p.state_dir)
    if not p.hub_conf.exists():
        p.hub_conf.write_text(HUB_CONF_HEAD)
    added = False
    try:
        if args.register:
            app, _, user = args.register.partition(":")
            added = register_app(p, app, user)
        ctx.conf = load_hub_conf(p)
    except ValueError as e:
        say("STOP", str(e))
        return 1
    rc = _telegram_ready(ctx, api)
    if rc is not None:
        return rc
    changed = units.install_hub_units(ctx)
    ctx.sh.run(["systemctl", "--user", "enable", "--now", "talaria-check.timer",
                "talaria-telegram.service"])
    if changed or added:      # a new app or new units: the bot must see them
        ctx.sh.run(["systemctl", "--user", "restart", "talaria-telegram.service"])
    names = ", ".join(a for a, _ in ctx.conf.apps) or "none yet"
    say("OK", f"Talaria hub ready; apps: {names}")
    return 0
```

  In `service_phase`: change the signature to `def service_phase(ctx, args) -> int:`; after the block that writes `initial_conf`, add

```python
    if "check.time" in parse_kv(p.conf_file.read_text()):
        say("NOTE", "check.time in talaria.conf is not used any more; set it in the hub's "
                    "hub.conf")
```

  change the plan line to `say("PLAN", f"{ctx.app.prepare_summary}, install units, start {ctx.app.title}, verify")`; delete the whole `if not ctx.conf.telegram_token: …` and `if not ctx.conf.telegram_user_id: …` blocks; inside the lock delete the two `ctx.sh.run([... "talaria-telegram.service" ...])` calls (the `enable --now` and the `restart`); delete the final `print("DONE", flush=True)`.

- in `setup()`, change the import line to `from talaria.ctx import make_ctx, make_hub_ctx` and add before `if args.as_service:`

```python
    if getattr(args, "as_hub", False):
        return hub_phase(make_hub_ctx(), args)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS, including the untouched Quadlet goldens in `tests/test_units.py`.

- [ ] **Step 5: Commit**

```bash
git add talaria/setup.py talaria/units.py talaria/cli.py tests/test_setup.py \
        tests/test_setup_golden.py tests/test_setup_service_golden.py tests/test_setup_hub.py \
        tests/test_units.py tests/test_cli_ops.py
git commit -m "Setup: one root paste for hub, app and op rule; hub phase; app phase without a bot"
```

---
### Task 9: Migration of v0.4 installs and transitional mode

**Files:**
- Modify: `talaria/setup.py` (`move_env`, `_v04_bot`, `_migrate`, `import_telegram`; `operator_phase` gets `move=None`; `hub_phase` handles `--import-telegram`)
- Create: `tests/test_transitional.py`
- Test: `tests/test_setup.py`, `tests/test_setup_golden.py`, `tests/test_setup_hub.py`, `tests/test_transitional.py`

**Interfaces:**
- Consumes: `setup.sudo_as`, `setup._bus`, `setup.operator_phase`, `setup.hub_phase`, `setup.say`, `setup.TOKEN_RE`, `conf.write_env_value`, `conf.parse_kv`, `hubconf.load_hub_conf` (Task 8); `hubexec.load_hub` (Task 3); `cli.main(argv, make, make_hub)` (Task 7); `units.TALARIA_UNITS`.
- Produces:
  - `setup.move_env(read_argv, write_argv, popen=subprocess.Popen) -> int` (0 when both processes exit 0).
  - `setup.OLD_UNITS = ("talaria-telegram.service", "talaria-check.timer", "talaria-check.service")`; `setup.TG_LINES = "^TALARIA_TELEGRAM_(TOKEN|USER_ID)="`.
  - `setup._v04_bot(sh, user, home) -> tuple[bool, bool]` (own bot unit file, own token in `.env`).
  - `setup._migrate(sh, user, pw, hub, hub_pw, found, move) -> int`.
  - `setup.import_telegram(ctx, stream) -> int` (run as the hub by `setup --as-hub --import-telegram`).
  - `operator_phase(..., move=None)`: migration runs after the app's setup and before the hub's.

- [ ] **Step 1: Write the failing tests**

`tests/test_setup.py` — extend `op_env` (signature and body; everything else stays):

```python
def op_env(monkeypatch, tmp_path, *, user_exists=True, sudo_ok=True, linger=True,
           installed=False, tag="v0.1.0", hub_exists=True, hub_sudo_ok=True, hub_linger=True,
           hub_installed=True, probe_rc=0, probe_err="", v04_units=False, v04_token=False,
           move_rc=0):
```

after the `probe` rule add

```python
    s = setup.sudo_as("hermes", "/home/hermes")
    sh.on(*s, "test", "-e", "/home/hermes/.config/systemd/user/talaria-telegram.service",
          rc=0 if v04_units else 1)
    sh.on(*s, "grep", "-qs", rc=0 if v04_token else 1)
    sh.moves, sh.marks = [], []
```

and replace the `calls = []` / `run = …` lines with

```python
    calls = []

    def call(argv):
        calls.append(argv)
        sh.marks.append(("call", len(sh.calls)))
        return 0

    def move(read, write):
        sh.moves.append((read, write))
        sh.marks.append(("move", len(sh.calls)))
        return move_rc

    run = lambda a: setup.operator_phase(sh, a, getpwnam=getpwnam, operator="admin",
                                         call=call, linger_dir=ld, move=move)
    return sh, run, calls
```

In `test_clawvisor_default_account_name_and_plan_text` and `test_clawvisor_handoff_carries_the_app_flag`, after the `sh.on("git", … "remote", …)` line, add (these tests have a catch-all `sudo` rule, which must not look like a v0.4 install):

```python
    sh.on(*setup.sudo_as("clawvisor", PW.pw_dir), "test", rc=1)
    sh.on(*setup.sudo_as("clawvisor", PW.pw_dir), "grep", rc=1)
```

Append to `tests/test_setup.py`:

```python
def test_move_env_pipes_from_one_process_into_the_other(tmp_path):
    import sys
    src, out = tmp_path / "src", tmp_path / "out"
    secret = "TALARIA_TELEGRAM_TOKEN=1:" + "s" * 35 + "\nTALARIA_TELEGRAM_USER_ID=42\n"
    src.write_text(secret)
    writer = [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write(sys.stdin.read())",
              str(out)]
    assert setup.move_env(["cat", str(src)], writer) == 0
    assert out.read_text() == secret


def test_move_env_fails_if_either_side_fails(tmp_path):
    import sys
    ok_writer = [sys.executable, "-c", "import sys; sys.stdin.read()"]
    assert setup.move_env(["false"], ok_writer) == 1
    assert setup.move_env(["echo", "x"], [sys.executable, "-c", "import sys; sys.exit(3)"]) == 1
```

`tests/test_setup_golden.py` — add after `PROBE`:

```python
MIGCHK = [(SUDO + ["test", "-e", "/home/hermes/.config/systemd/user/talaria-telegram.service"], None),
          (SUDO + ["grep", "-qs", "^TALARIA_TELEGRAM_", "/home/hermes/.config/talaria/.env"], None)]
BUS = ["XDG_RUNTIME_DIR=/run/user/1001", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus"]
UNITDIR = "/home/hermes/.config/systemd/user"
ENV = "/home/hermes/.config/talaria/.env"
STOP_BOT = [(SUDO + BUS + ["systemctl", "--user", "disable", "--now", "talaria-telegram.service",
                          "talaria-check.timer"], None),
            (SUDO + ["rm", "-f", f"{UNITDIR}/talaria-telegram.service",
                     f"{UNITDIR}/talaria-check.timer", f"{UNITDIR}/talaria-check.service"], None),
            (SUDO + BUS + ["systemctl", "--user", "daemon-reload"], None)]
DROP_TOKEN = [(SUDO + ["sed", "-i", "-E", "/^TALARIA_TELEGRAM_(TOKEN|USER_ID)=/d", ENV], None)]
MOVE = (SUDO + ["grep", "-E", "^TALARIA_TELEGRAM_(TOKEN|USER_ID)=", ENV],
        HSUDO + ["/home/talaria/.local/bin/talaria", "setup", "--as-hub", "--import-telegram"])
BEFORE_HANDOFF = PRE + CHECKS + HUB_CHECKS + TAG + ORIGIN + MIGCHK + UPDATE + HUB_UPDATE + PROBE
```

In `CASES`, change `TAG + ORIGIN +` to `TAG + ORIGIN + MIGCHK +` in exactly these five entries: `install`, `installed`, `hubfresh`, `norule`, `plan` (the `plan` entry's command list becomes `PRE + CHECKS + HUB_CHECKS + TAG + ORIGIN + MIGCHK`). Then append:

```python
def test_migration_moves_the_bot_between_the_app_and_the_hub_setup(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, installed=True, v04_units=True, v04_token=True)
    assert run(args(user="hermes")) == 0
    cmds = list(zip(sh.calls, sh.timeouts))
    assert cmds == BEFORE_HANDOFF + STOP_BOT + DROP_TOKEN
    assert sh.moves == [MOVE]
    n = len(BEFORE_HANDOFF)
    assert sh.marks == [("call", n), ("move", n + 3), ("call", n + 4)]
    assert calls == [HANDOFF, HUB_HANDOFF]
    assert capsys.readouterr().out == (
        INSTALLED + "OK: stopped hermes's own bot; the hub talaria runs the only one\n"
        "OK: hermes holds no bot token any more\nDONE\n")
    flat = " ".join(a for c, _ in cmds for a in c) + " ".join(a for m in sh.moves for p in m for a in p)
    assert "hermes.container" not in flat and "hermes.service" not in flat   # the app is untouched
    assert "TALARIA_TELEGRAM_TOKEN=" not in flat                             # no secret in an argv


def test_interrupted_migration_resumes_with_the_token(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, installed=True, v04_token=True)
    assert run(args(user="hermes")) == 0
    assert list(zip(sh.calls, sh.timeouts)) == BEFORE_HANDOFF + DROP_TOKEN
    assert sh.moves == [MOVE]
    assert capsys.readouterr().out == INSTALLED + "OK: hermes holds no bot token any more\nDONE\n"


def test_failed_move_keeps_the_token_and_stops_before_the_hub(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, installed=True, v04_token=True, move_rc=1)
    assert run(args(user="hermes")) == 1
    assert list(zip(sh.calls, sh.timeouts)) == BEFORE_HANDOFF
    assert calls == [HANDOFF]
    assert capsys.readouterr().out.endswith(
        "STOP: could not move the bot token from hermes to talaria; run setup again\n")


def test_plan_names_the_move(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, installed=True, v04_units=True, v04_token=True)
    assert run(args(user="hermes", plan=True)) == 0
    assert capsys.readouterr().out.endswith(
        "PLAN: move the bot from hermes to the hub talaria (same bot, same chat, no new "
        "pairing; Hermes is not restarted)\n")
    assert sh.moves == [] and calls == []
```

Append to `tests/test_setup_hub.py`:

```python
import io


def test_import_moves_token_and_owner(tmp_path, capsys, monkeypatch):
    ctx = hub_ctx(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n"))
    rc, out, cmds = run(ctx, capsys, import_telegram=True)
    assert rc == 0 and cmds == []
    assert out == "OK: bot token and owner moved to the hub (same bot, no new pairing)\n"
    assert parse_kv(ctx.paths.env_file.read_text()) == {"TALARIA_TELEGRAM_TOKEN": TOKEN,
                                                        "TALARIA_TELEGRAM_USER_ID": "42"}
    assert (ctx.paths.env_file.stat().st_mode & 0o777) == 0o600
    assert ctx.paths.hub_conf.read_text() == "# Talaria hub settings; see README.\n"


def test_import_without_an_owner_moves_the_token_only(tmp_path, capsys, monkeypatch):
    ctx = hub_ctx(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n"))
    rc, out, cmds = run(ctx, capsys, import_telegram=True)
    assert rc == 0 and out == "OK: bot token moved to the hub; pair it when setup asks\n"
    assert parse_kv(ctx.paths.env_file.read_text()) == {"TALARIA_TELEGRAM_TOKEN": TOKEN}


def test_import_keeps_the_hubs_own_bot(tmp_path, capsys, monkeypatch):
    ctx = hub_ctx(tmp_path, "TALARIA_TELEGRAM_TOKEN=9:" + "b" * 35 + "\nTALARIA_TELEGRAM_USER_ID=7\n")
    before = ctx.paths.env_file.read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO(f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n"))
    rc, out, cmds = run(ctx, capsys, import_telegram=True)
    assert rc == 0 and out == ("OK: the hub already has a bot; the app's own token is not "
                               "needed any more\n")
    assert ctx.paths.env_file.read_text() == before


@pytest.mark.parametrize("stdin", ["", "nonsense\n", "TALARIA_TELEGRAM_TOKEN=x\n"])
def test_import_refuses_anything_but_a_token(tmp_path, capsys, monkeypatch, stdin):
    ctx = hub_ctx(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    rc, out, cmds = run(ctx, capsys, import_telegram=True)
    assert rc == 1 and out == "STOP: no valid bot token on stdin; nothing changed\n"
    assert not ctx.paths.env_file.exists()
```

New `tests/test_transitional.py`:

```python
"""Spec §7.5: a v0.4 app install self-updated to v0.5 keeps its own bot until it is moved,
running the hub code with a local executor."""
from talaria import cli, hubexec, setup, units
from talaria.ctx import Paths
from tests.test_setup import args, svc  # noqa: F401  (fixture)

V04_ENV = "TALARIA_TELEGRAM_TOKEN=1:" + "a" * 35 + "\nTALARIA_TELEGRAM_USER_ID=42\n"


def v04_units(p):
    p.units_dir.mkdir(parents=True, exist_ok=True)
    for name in units.TALARIA_UNITS:
        (p.units_dir / name).write_text(f"v0.4 {name}\n")


def test_v04_bot_and_timer_run_the_hub_code_locally(tmp_path, monkeypatch):
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True)
    p.conf_file.write_text("data_dir = ~/hermes-data\n")
    p.env_file.write_text(V04_ENV)
    v04_units(p)
    seen = []
    from talaria import hubcheck, telegram
    monkeypatch.setattr(telegram, "run", lambda h: seen.append(
        ("bot", h.transitional, h.apps["hermes"].executor.argv(["hello"]))) or 0)
    monkeypatch.setattr(hubcheck, "check", lambda h, timer: seen.append(
        ("check", h.transitional, timer)) or 0)
    load = lambda: hubexec.load_hub(tmp_path)
    assert cli.main(["bot"], make_hub=load) == 0
    assert cli.main(["check", "--timer"], make_hub=load) == 0
    assert seen == [("bot", True, [str(tmp_path / ".local/bin/talaria"), "op", "hello"]),
                    ("check", True, True)]


def test_app_phase_leaves_the_v04_bot_alone(svc, capsys):  # noqa: F811
    p = svc.paths
    v04_units(p)
    p.conf_dir.mkdir(parents=True, exist_ok=True)
    p.env_file.write_text(V04_ENV)
    assert setup.service_phase(svc, args(as_service=True)) == 0
    assert all((p.units_dir / n).read_text() == f"v0.4 {n}\n" for n in units.TALARIA_UNITS)
    assert p.env_file.read_text() == V04_ENV
    assert not [c for c in svc.sh.calls if any("talaria-" in a for a in c)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_setup.py tests/test_setup_golden.py tests/test_setup_hub.py tests/test_transitional.py`
Expected: FAIL (`operator_phase() got an unexpected keyword argument 'move'`, `setup.move_env` missing, import cases fall through to registration).

- [ ] **Step 3: Implement in `talaria/setup.py`**

Add after `_install`:

```python
OLD_UNITS = ("talaria-telegram.service", "talaria-check.timer", "talaria-check.service")
TG_LINES = "^TALARIA_TELEGRAM_(TOKEN|USER_ID)="


def move_env(read_argv: list[str], write_argv: list[str], popen=subprocess.Popen) -> int:
    """Pipe one process's stdout straight into another's stdin. Used for the bot token: it
    never passes through this process, an argv or the terminal."""
    reader = popen(read_argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    writer = popen(write_argv, stdin=reader.stdout)
    reader.stdout.close()          # the writer holds the only read end now
    wrc, rrc = writer.wait(), reader.wait()
    return 0 if wrc == 0 and rrc == 0 else 1


def _v04_bot(sh, user: str, home: str) -> tuple[bool, bool]:
    """(own bot unit, own bot token): what a v0.4 install has and a v0.5 app must not."""
    s = sudo_as(user, home)
    has_units = sh.run(s + ["test", "-e", f"{home}/.config/systemd/user/talaria-telegram.service"],
                       check=False).returncode == 0
    has_token = sh.run(s + ["grep", "-qs", "^TALARIA_TELEGRAM_", f"{home}/.config/talaria/.env"],
                       check=False).returncode == 0
    return has_units, has_token


def _migrate(sh, user: str, pw, hub: str, hub_pw, found, move) -> int:
    """Spec §7.4, between the app's and the hub's setup: one bot per host. Each step is
    idempotent, so a re-run after an interruption resumes. The app's Quadlet and service
    are never touched."""
    has_units, has_token = found
    home, s = pw.pw_dir, sudo_as(user, pw.pw_dir)
    if has_units:
        sh.run(s + _bus(pw) + ["systemctl", "--user", "disable", "--now",
                               "talaria-telegram.service", "talaria-check.timer"], check=False)
        sh.run(s + ["rm", "-f", *(f"{home}/.config/systemd/user/{u}" for u in OLD_UNITS)])
        sh.run(s + _bus(pw) + ["systemctl", "--user", "daemon-reload"])
        say("OK", f"stopped {user}'s own bot; the hub {hub} runs the only one")
    if has_token:
        env = f"{home}/.config/talaria/.env"
        rc = move(s + ["grep", "-E", TG_LINES, env],
                  sudo_as(hub, hub_pw.pw_dir) + [f"{hub_pw.pw_dir}/.local/bin/talaria", "setup",
                                                 "--as-hub", "--import-telegram"])
        if rc != 0:
            say("STOP", f"could not move the bot token from {user} to {hub}; run setup again")
            return 1
        sh.run(s + ["sed", "-i", "-E", f"/{TG_LINES}/d", env])
        say("OK", f"{user} holds no bot token any more")
    return 0


def import_telegram(ctx, stream) -> int:
    """Spec §7.4 step 2, the hub's end of the pipe: the app's Telegram lines on stdin."""
    kv = parse_kv(stream.read())
    token, uid = kv.get("TALARIA_TELEGRAM_TOKEN", ""), kv.get("TALARIA_TELEGRAM_USER_ID", "")
    if not TOKEN_RE.match(token):
        say("STOP", "no valid bot token on stdin; nothing changed")
        return 1
    if load_hub_conf(ctx.paths).telegram_token:
        say("OK", "the hub already has a bot; the app's own token is not needed any more")
        return 0
    write_env_value(ctx.paths.env_file, "TALARIA_TELEGRAM_TOKEN", token)
    if uid.isdigit():
        write_env_value(ctx.paths.env_file, "TALARIA_TELEGRAM_USER_ID", uid)
        say("OK", "bot token and owner moved to the hub (same bot, no new pairing)")
    else:
        say("OK", "bot token moved to the hub; pair it when setup asks")
    return 0
```

In `operator_phase`:
- signature: add `move=None` after `explicit_app=True`;
- after the `if url is None: … return 1` block insert `found = _v04_bot(sh, user, pw.pw_dir)`;
- in the `if args.plan:` block that prints the three install PLAN lines, before its `return 0`, add

```python
        if any(found):
            say("PLAN", f"move the bot from {user} to the hub {hub} (same bot, same chat, no "
                        f"new pairing; {A.title} is not restarted)")
```

- after the app handoff's `if rc != 0: return rc`, insert

```python
    if any(found):
        rc = _migrate(sh, user, pw, hub, hub_pw, found, move or move_env)
        if rc != 0:
            return rc
```

In `hub_phase`, directly after the `if not p.hub_conf.exists(): …` lines, insert

```python
    if getattr(args, "import_telegram", False):
        return import_telegram(ctx, sys.stdin)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add talaria/setup.py tests/test_setup.py tests/test_setup_golden.py tests/test_setup_hub.py \
        tests/test_transitional.py
git commit -m "Migrate a v0.4 install's bot to the hub; transitional mode tests"
```

---
### Task 10: End-to-end in CI, docs, version 0.5.0, mutation run

**Files:**
- Modify: `tests/e2e/conftest.py`, `tests/e2e/test_e2e.py` (rewritten), `tests/e2e/test_e2e_clawvisor.py`, `.github/workflows/ci.yml`, `README.md`, `AGENT_SETUP.md`, `pyproject.toml`, `talaria/__init__.py`, `uv.lock`, `docs/mutation-report.md`, `tests/test_cli.py`
- Create: `tests/e2e/test_e2e_hub.py`
- Test: the e2e suite (CI only), `tests/test_cli.py`, the mutation run

**Interfaces:**
- Consumes: everything above as a running system: `bin/talaria setup --dev --app APP --user USER [--hub HUB]` (operator phase prints the root paste with `ACTION REQUIRED: paste this into your terminal…`, the hub's `set-token` hint as `sudo -u <hub> -H <bin> set-token`, a `/pair CODE` line, and `DONE`); bot commands `/status`, `/check <app>`, `/approve <app> <tag>`, `/rollback <app> [CONFIRM]`, `/restore <app> <id> CONFIRM`, `/update <tag>`; callback data `<app>|ap:<tag>`, `hub|up:<tag>`; messages `"<Title> <tag> is ready to deploy"`, `"Deploying <Title> <tag>. I will report the result."`, `"Deployed <Title> <tag>."`, `"Talaria <tag> is available …"`, `"Talaria <tag> installed: Hermes ✓ · Clawvisor ✓"`.
- Produces: e2e helpers `HUB = "talaria"`, `HUB_CONF`, `seed_hub_conf(hub=HUB)`, `AppEnv(user, app, src, telegram, hub=HUB)` (`hub=None` omits `--hub`, for v0.4.2's setup); version `0.5.0`.

- [ ] **Step 1: Version test (fails first)**

Append to `tests/test_cli.py`:

```python
def test_versions_agree():
    import re
    py = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)[1]
    lock = re.search(r'name = "talaria"\nversion = "([^"]+)"', (ROOT / "uv.lock").read_text())[1]
    assert py == lock == talaria.__version__ == "0.5.0"
```

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_cli.py::test_versions_agree`
Expected: FAIL (`'0.4.2' == …`).

- [ ] **Step 2: Bump the version**

Set `version = "0.5.0"` in `pyproject.toml`, `__version__ = "0.5.0"` in `talaria/__init__.py`, then run `uv lock` (updates the `talaria` entry in `uv.lock` to `0.5.0`). Re-run the test from Step 1: PASS.

- [ ] **Step 3: e2e helpers for the hub (`tests/e2e/conftest.py`)**

Add after `PAIR_CODE_RE`:

```python
HUB = "talaria"
HUB_CONF = """talaria_repo = /tmp/talaria-e2e/talaria-src
telegram_api = http://127.0.0.1:8081
"""
SET_TOKEN_RE = re.compile(r"sudo -u (\S+) -H (\S+) set-token")


def seed_hub_conf(hub=HUB):
    """The hub's settings must exist before its first setup run: the local Talaria repo
    for release checks and the fake Telegram API. Kept if already there."""
    as_user("mkdir", "-p", f"/home/{hub}/.config/talaria", user=hub)
    as_user("sh", "-c", f"test -e /home/{hub}/.config/talaria/hub.conf || "
                        f"cat > /home/{hub}/.config/talaria/hub.conf", user=hub, input=HUB_CONF)
    as_user("git", "config", "--global", "--add", "safe.directory", "*", user=hub)
```

Replace `class AppEnv` with:

```python
class AppEnv:
    """An app's service user, set up in --dev mode under a hub (or, with hub=None, by a
    v0.4 checkout that knows no hub). The account may not exist yet: the first root paste
    creates it."""

    def __init__(self, user: str, app: str, src: Path, telegram, hub=HUB):
        self.user, self.app, self.src, self.telegram, self.hub = user, app, src, telegram, hub

    def _conf_file(self) -> str:
        return f"/home/{self.user}/.config/talaria/talaria.conf"

    def conf(self, text: str) -> None:
        as_user("mkdir", "-p", f"/home/{self.user}/.config/talaria", user=self.user)
        as_user("sh", "-c", f"cat > {self._conf_file()}", user=self.user, input=text)

    def conf_add(self, text: str) -> None:
        as_user("sh", "-c", f"cat >> {self._conf_file()}", user=self.user, input=text)

    def setup_until_done(self, *extra, timeout=900) -> str:
        """Run `talaria setup --dev` for this user, handling whatever it asks for next (a
        root paste, set-token as whichever account it names, a /pair code) and re-running,
        until it exits 0 with output ending in DONE. Returns all output."""
        out = ""
        argv = [str(self.src / "bin/talaria"), "setup", "--dev", "--app", self.app,
                "--user", self.user, *(["--hub", self.hub] if self.hub else []), *extra]
        while True:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True)
            chunk = ""
            for line in p.stdout:
                chunk += line
                m = PAIR_CODE_RE.search(line)
                if m:
                    self.telegram.inject(f"/pair {m[1]}")
            rc = p.wait(timeout=timeout)
            out += chunk
            if rc == 0:
                assert chunk.rstrip().endswith("DONE"), out
                return out
            if rc == 10 and "ACTION REQUIRED: paste this into your terminal" in chunk:
                sh("bash", "-c", root_block(chunk))   # the block calls sudo itself
                wait_for(lambda: bus_ready(self.user))
                as_user("git", "config", "--global", "--add", "safe.directory", "*",
                        user=self.user)
                if self.hub:
                    wait_for(lambda: bus_ready(self.hub))
                    seed_hub_conf(self.hub)
                continue
            m = SET_TOKEN_RE.search(chunk)
            if rc == 10 and m:
                as_user(m[2], "set-token", user=m[1], input="123456:" + "a" * 35 + "\n")
                continue
            raise AssertionError(f"setup did not finish: {out}")
```

Replace the `cv_env` fixture with:

```python
@pytest.fixture(scope="session")
def cv_env(_base_env):
    """A second app user, cvtest, running Clawvisor under the same hub; built directly from
    the real upstream releases (no registry or dummy image, unlike Hermes's `env`)."""
    user = "cvtest"
    r = sh(_base_env["src"] / "bin/talaria", "setup", "--dev", "--app", "clawvisor",
           "--user", user, check=False)
    assert r.returncode == 10, r.stdout
    sh("bash", "-c", root_block(r.stdout))
    wait_for(lambda: bus_ready(user))
    wait_for(lambda: bus_ready(HUB))
    seed_hub_conf(HUB)
    as_user("git", "config", "--global", "--add", "safe.directory", "*", user=user)
    return AppEnv(user=user, app="clawvisor", src=_base_env["src"], telegram=_base_env["tg"])
```

- [ ] **Step 4: Rewrite `tests/e2e/test_e2e.py` for the hub**

```python
import re

import pytest

from tests.e2e.conftest import (HUB, USER, AppEnv, as_user, bus_ready, root_block, seed_conf,
                                seed_hub_conf, sh, talaria, wait_for)

pytestmark = pytest.mark.e2e


def hermes_active(user=USER):
    return as_user("systemctl", "--user", "is-active", "hermes.service", user=user,
                   check=False).stdout.strip() == "active"


def bot_active(hub=HUB):
    return as_user("systemctl", "--user", "is-active", "talaria-telegram.service", user=hub,
                   check=False).stdout.strip() == "active"


def cfg_version(user=USER):
    data = f"/home/{user}/hermes-data/config.yaml"
    out = as_user("cat", data, user=user, check=False).stdout
    m = re.search(r"_config_version: (\d+)", out)
    return int(m[1]) if m else None


def test_01_fresh_setup_creates_the_hub(env):
    r = sh(env["src"] / "bin/talaria", "setup", "--dev", check=False)
    assert r.returncode == 10 and "then run setup again with --user hermes" in r.stdout, r.stdout
    assert "talaria ALL=(hermes) NOPASSWD: $home/.local/bin/talaria op *" in r.stdout
    sh("bash", "-c", root_block(r.stdout))
    wait_for(lambda: bus_ready())
    wait_for(lambda: bus_ready(HUB))
    seed_conf()
    seed_hub_conf()
    out = AppEnv(user=USER, app="hermes", src=env["src"], telegram=env["tg"]).setup_until_done()
    assert "set-token" in out and "/pair" in out
    assert hermes_active() and cfg_version() == 1 and bot_active()
    assert as_user("test", "-e", f"/home/{USER}/.config/systemd/user/talaria-telegram.service",
                   check=False).returncode != 0          # the app has no bot of its own


def test_02_update_via_bot_survives_bot_restart(env):
    tg = env["tg"]
    env["up"].publish(2)
    talaria("backup")                                   # a manual backup for test_04
    tg.inject("/check hermes")
    tg.wait_sent("Hermes v2026.1.2 is ready to deploy")
    tg.inject("/approve hermes v2026.1.2")
    tg.wait_sent("Deploying Hermes v2026.1.2")
    as_user("systemctl", "--user", "restart", "talaria-telegram.service", user=HUB)
    tg.wait_sent("Deployed Hermes v2026.1.2")
    assert hermes_active() and cfg_version() == 2


def test_03_rollback(env):
    tg = env["tg"]
    tg.inject("/rollback hermes")
    tg.wait_sent("Send /rollback CONFIRM")
    tg.inject("/rollback hermes CONFIRM")
    tg.wait_sent("Rolled back to Hermes v2026.1.1")
    assert hermes_active() and cfg_version() == 1


def test_04_restore(env):
    bid = re.search(r"^(\S+-manual)\s", talaria("backups").stdout, re.M)[1]
    env["tg"].inject(f"/restore hermes {bid} CONFIRM")
    env["tg"].wait_sent(f"Restored backup {bid}")
    assert hermes_active()


def test_05_redeploy_by_tapping_the_button(env):
    tg = env["tg"]
    tg.inject("/check hermes")
    msg = tg.wait_sent("Hermes v2026.1.2 is ready to deploy")
    markup = tg.buttons[tg.sent.index(msg)]
    assert markup["inline_keyboard"][0][0]["callback_data"] == "hermes|ap:v2026.1.2"
    tg.tap("hermes|ap:v2026.1.2")
    tg.wait_sent("Deployed Hermes v2026.1.2")


def test_06_crashing_release_rolls_back_automatically(env):
    tg = env["tg"]
    env["up"].publish(3, "crash")
    tg.inject("/check hermes")
    tg.wait_sent("Hermes v2026.1.3 is ready to deploy")
    tg.inject("/approve hermes v2026.1.3")
    msg = tg.wait_sent("v2026.1.3 failed during deploy", timeout=400)
    assert "Rolled back to Hermes v2026.1.2" in msg
    assert hermes_active() and cfg_version() == 2


def test_07_failing_migration_stops_at_rehearsal(env):
    env["up"].publish(4, "failmigrate")
    env["tg"].inject("/check hermes")
    env["tg"].wait_sent("v2026.1.4 failed the rehearsal")
    assert hermes_active() and cfg_version() == 2


def test_08_crash_mid_deploy_blocks_start_until_rollback(env):
    tg = env["tg"]
    env["up"].publish(5)
    tg.inject("/check hermes")
    tg.wait_sent("Hermes v2026.1.5 is ready to deploy")
    r = talaria("deploy", "v2026.1.5", env=["TALARIA_TEST_CRASH_AT=after_marker"], check=False)
    assert r.returncode != 0
    assert as_user("test", "-e", f"/home/{USER}/.local/state/talaria/changing",
                   check=False).returncode == 0
    # Spike: restarting the user manager times out on GitHub runners, so simulate the
    # boot: systemd tries to start Hermes (the marker must block it) and the bot restarts.
    as_user("systemctl", "--user", "start", "hermes.service", check=False)
    assert not hermes_active()
    as_user("systemctl", "--user", "restart", "talaria-telegram.service", user=HUB)
    tg.wait_sent("Interrupted deploy")
    tg.inject("/rollback hermes CONFIRM")
    tg.wait_sent("Rolled back to Hermes v2026.1.2")
    assert hermes_active() and cfg_version() == 2


def test_09_adopt_existing_install_under_its_own_hub(env):
    tg, user, hub = env["tg"], "hermes2", "hubadopt"
    as_user("systemctl", "--user", "stop", "hermes.service", check=False)        # port 9119
    as_user("systemctl", "--user", "stop", "talaria-telegram.service", user=HUB)  # one poller
    sh("sudo", "useradd", "--create-home", "--shell", "/bin/bash", user)
    sh("sudo", "loginctl", "enable-linger", user)
    wait_for(lambda: bus_ready(user))
    as_user("mkdir", "-p", f"/home/{user}/data", f"/home/{user}/.config/containers/systemd",
            user=user)
    as_user("podman", "pull", "-q", "--tls-verify=false",
            "localhost:5000/hermes-agent:v2026.1.1", user=user)
    quadlet = (
        "[Container]\nContainerName=old-hermes\n"
        "Image=localhost:5000/hermes-agent:v2026.1.1\n"
        f"Volume=/home/{user}/data:/opt/data\nUserNS=keep-id:uid=10000,gid=10000\n"
        "Environment=HERMES_DASHBOARD_INSECURE=true TZ=UTC\n"
        "PublishPort=127.0.0.1:9119:9119\nExec=gateway run\n[Install]\nWantedBy=default.target\n")
    as_user("sh", "-c", f"cat > /home/{user}/.config/containers/systemd/old-hermes.container",
            user=user, input=quadlet)
    as_user("systemctl", "--user", "daemon-reload", user=user)
    as_user("systemctl", "--user", "start", "old-hermes.service", user=user)
    wait_for(lambda: hermes_active_unit(user, "old-hermes.service"))
    seed_conf(user)
    setup = [env["src"] / "bin/talaria", "setup", "--dev", "--user", user, "--hub", hub]
    r = sh(*setup, check=False)                 # creates the hub account (root paste)
    assert r.returncode == 10 and "useradd --create-home --shell /bin/bash hubadopt" in r.stdout
    sh("bash", "-c", root_block(r.stdout))
    wait_for(lambda: bus_ready(hub))
    seed_hub_conf(hub)
    r = sh(*setup, check=False)
    assert r.returncode == 10 and "--adopt old-hermes.service" in r.stdout, r.stdout
    assert "HERMES_DASHBOARD_INSECURE" in r.stdout            # listed as dropped
    out = AppEnv(user=user, app="hermes", src=env["src"], telegram=tg,
                 hub=hub).setup_until_done("--adopt", "old-hermes.service")
    assert "adopted old-hermes.service" in out
    assert hermes_active(user)
    assert as_user("test", "-e", f"/home/{user}/.config/containers/systemd/"
                   "old-hermes.container.talaria-orig", user=user, check=False).returncode == 0
    # hand the host back to the main hub and Hermes for the tests that follow
    as_user("systemctl", "--user", "disable", "--now", "talaria-telegram.service",
            "talaria-check.timer", user=hub)
    as_user("systemctl", "--user", "stop", "hermes.service", user=user)
    as_user("systemctl", "--user", "start", "hermes.service")
    as_user("systemctl", "--user", "start", "talaria-telegram.service", user=HUB)
    wait_for(hermes_active)
    wait_for(bot_active)


def hermes_active_unit(user, unit):
    return as_user("systemctl", "--user", "is-active", unit, user=user,
                   check=False).stdout.strip() == "active"
```

- [ ] **Step 5: Clawvisor e2e under the same hub (`tests/e2e/test_e2e_clawvisor.py`)**

Change the module docstring's first sentence to "e2e tests against the real Clawvisor releases, registered with the same hub as Hermes (GitHub runners only, TALARIA_E2E=1)." In `test_02_update_offer_lists_migrations_and_deploys` replace `talaria("check", user=USER)` with `cv_env.telegram.inject("/check clawvisor")` and `cv_env.telegram.tap("ap:v0.9.10")` with `cv_env.telegram.tap("clawvisor|ap:v0.9.10")`. In `test_03_rollback_restores_v099_and_data` replace `cv_env.telegram.inject("/rollback CONFIRM")` with `cv_env.telegram.inject("/rollback clawvisor CONFIRM")`. Remove `talaria` from the conftest import if unused.

- [ ] **Step 6: Hub e2e (`tests/e2e/test_e2e_hub.py`)**

```python
"""Hub e2e (spec §9): both apps under one bot, Talaria self-update through the bot, and
the move of a v0.4 install's bot to a new hub. Runs after test_e2e.py and
test_e2e_clawvisor.py in the same session."""
import pytest

from tests.e2e.conftest import (HUB, WORK, AppEnv, as_user, bus_ready, root_block, seed_conf,
                                sh, wait_for)

pytestmark = pytest.mark.e2e


def unit_active(unit, user):
    return as_user("systemctl", "--user", "is-active", unit, user=user,
                   check=False).stdout.strip() == "active"


def test_01_status_lists_both_apps(env, cv_env):
    tg = env["tg"]
    tg.inject("/status")
    msg = tg.wait_sent("Clawvisor")
    assert "Hermes" in msg


def test_02_update_talaria_through_the_bot(env, cv_env):
    src, tg = env["src"], env["tg"]
    sh("git", "-C", src, "-c", "user.email=e2e@example.invalid", "-c", "user.name=e2e",
       "commit", "-q", "--allow-empty", "-m", "e2e release")
    sh("git", "-C", src, "tag", "v9.0.0")
    sh("chmod", "-R", "a+rX", src)
    tg.inject("/update v9.0.0")
    offer = tg.wait_sent("Talaria v9.0.0 is available", timeout=600)
    assert "Hermes would restart: no" in offer and "Clawvisor would restart: no" in offer
    assert tg.buttons[tg.sent.index(offer)]["inline_keyboard"][0][0]["callback_data"] == \
        "hub|up:v9.0.0"
    tg.tap("hub|up:v9.0.0")
    tg.wait_sent("Talaria v9.0.0 installed: Hermes ✓ · Clawvisor ✓", timeout=900)
    for user in (HUB, "hermes", "cvtest"):
        tag = as_user("git", "-C", f"/home/{user}/.local/share/talaria", "describe", "--tags",
                      "--exact-match", user=user).stdout.strip()
        assert tag == "v9.0.0", user
    wait_for(lambda: unit_active("talaria-telegram.service", HUB))
    assert unit_active("hermes.service", "hermes")


def test_03_moves_the_bot_of_a_v04_install_to_a_new_hub(env):
    tg, user, hub = env["tg"], "hermes3", "hub2"
    as_user("systemctl", "--user", "stop", "talaria-telegram.service", user=HUB)  # one poller
    old = WORK / "talaria-v042"
    sh("git", "clone", "-q", env["src"], old)
    sh("git", "-C", old, "checkout", "-q", "v0.4.2")
    sh("chmod", "-R", "a+rX", old)
    r = sh(old / "bin/talaria", "setup", "--dev", "--user", user, check=False)
    assert r.returncode == 10, r.stdout
    sh("bash", "-c", root_block(r.stdout))
    wait_for(lambda: bus_ready(user))
    seed_conf(user)
    v04 = AppEnv(user=user, app="hermes", src=old, telegram=tg, hub=None)
    v04.conf_add("dashboard.port = 9121\n")            # Hermes (user hermes) holds 9119
    v04.setup_until_done()                             # v0.4.2: own bot, set-token, pairing
    assert unit_active("talaria-telegram.service", user)
    # v0.5 in --dev mode installs from the main checkout, not from the v0.4.2 clone
    as_user("git", "-C", f"/home/{user}/.local/share/talaria", "remote", "set-url", "origin",
            str(env["src"]), user=user)
    quadlet = f"/home/{user}/.config/containers/systemd/hermes.container"
    before = as_user("cat", quadlet, user=user).stdout
    started = as_user("systemctl", "--user", "show", "hermes.service", "-p",
                      "ActiveEnterTimestamp", user=user).stdout

    out = AppEnv(user=user, app="hermes", src=env["src"], telegram=tg,
                 hub=hub).setup_until_done()
    assert "/pair" not in out and "set-token" not in out      # same bot, same owner
    assert as_user("test", "-e", f"/home/{user}/.config/systemd/user/talaria-telegram.service",
                   user=user, check=False).returncode != 0
    assert "TALARIA_TELEGRAM" not in as_user("cat", f"/home/{user}/.config/talaria/.env",
                                             user=user).stdout
    assert as_user("grep", "-c", "^TALARIA_TELEGRAM_TOKEN=", f"/home/{hub}/.config/talaria/.env",
                   user=hub).stdout.strip() == "1"
    assert as_user("cat", quadlet, user=user).stdout == before          # Quadlet untouched
    assert as_user("systemctl", "--user", "show", "hermes.service", "-p",
                   "ActiveEnterTimestamp", user=user).stdout == started  # not restarted
    wait_for(lambda: unit_active("talaria-telegram.service", hub))
    tg.inject("/status")
    tg.wait_sent("Hermes")
```

- [ ] **Step 7: One CI job for all e2e (`.github/workflows/ci.yml`)**

Replace the `e2e` and `e2e-clawvisor` jobs with:

```yaml
  e2e:
    runs-on: ubuntu-24.04
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0      # the migration test installs v0.4.2 from its tag
      - uses: astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4 # v10.1.0
        with:
          python-version: "3.12"
      - run: uv sync --locked
      - run: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
      # runner images set XDG_CONFIG_HOME=/home/runner/.config system-wide; every user
      # manager would inherit it and look for Quadlets in the runner's home
      - run: grep -n XDG /etc/environment || true; sudo sed -i '/^XDG_/d' /etc/environment
      - run: >-
          TALARIA_E2E=1 uv run pytest -m e2e -v -x tests/e2e/test_e2e.py
          tests/e2e/test_e2e_clawvisor.py tests/e2e/test_e2e_hub.py
      - if: failure()
        run: |
          for u in talaria hermes cvtest hubadopt hermes2 hub2 hermes3; do
            id -u "$u" >/dev/null 2>&1 || continue
            echo "=== $u"; sudo journalctl --no-pager -n 200 _UID=$(id -u "$u") || true
            sudo -u "$u" -H ls -la "/home/$u/.local/state/talaria" || true
          done
          sudo -u talaria -H cat /home/talaria/.config/talaria/hub.conf || true
          sudo -u hermes -H cat /home/hermes/.config/containers/systemd/hermes.container || true
```

- [ ] **Step 8: Docs**

`README.md`:

1. In "What it does", replace "- **Waits for your approval** on Telegram." with "- **Waits for your approval** on Telegram: one bot per host for every app Talaria manages." In the "Opinionated:" sentence replace "one dedicated service user" with "one dedicated service user per app plus one hub account per host".

2. In "Setup by hand", replace the paragraph starting "`--user` names the service account" with:

```markdown
`--user` names the app's service account; setup creates it if it does not exist. Setup
also creates the hub account `talaria` (once per host; `--hub NAME` picks another name),
which runs the one Telegram bot and the daily check for every app on the host. Both happen
through one command you paste into your own terminal (sudo asks for your password). Clone
over https as shown: setup installs Talaria for both accounts from your checkout's origin,
and they have no SSH key.
```

   and replace the paragraph "The bot token is never typed…" with:

```markdown
The bot token is never typed into a chat or an agent. You store it yourself, once per
host, as the hub: `sudo -u talaria -H ~talaria/.local/bin/talaria set-token` (setup prints
the exact command). A second app on the same host uses the same bot: no new token, no
pairing.
```

   and add `NOTE: check.time in talaria.conf is not used any more; set it in the hub's hub.conf` as a line in the example output block.

3. Replace the body of "What setup changes" with:

```markdown
- The hub account (default `talaria`) and the app's service account (default `hermes`),
  linger for both, and three sudo rules, each checked with `visudo` before it is installed:
  - `/etc/sudoers.d/talaria-<user>` and `/etc/sudoers.d/talaria-talaria`: your login account
    may act as the app's account and as the hub;
  - `/etc/sudoers.d/talaria-talaria-<user>`: the hub may run
    `~<user>/.local/bin/talaria op …` as the app's account — nothing else.
- For the hub:
  - `~/.local/share/talaria` and `~/.local/bin/talaria` (the same release tag as the apps);
  - `~/.config/talaria/hub.conf` (registered apps, check time) and `.env` with the bot token;
  - `talaria-check.timer` and `talaria-telegram.service`.
- For the app's account:
  - `~/.local/share/talaria` (this repo at a release tag) and `~/.local/bin/talaria`;
  - `~/.config/talaria/` (`talaria.conf`, `hermes.env` with the dashboard password);
  - `~/.local/state/talaria/` (state, backups, history);
  - `~/.config/containers/systemd/hermes.container`.
- Adopting an existing install renames its Quadlet to `*.talaria-orig`, takes a full
  backup first, and keeps the data where it is.
```

4. In "Daily use", replace the table with:

```markdown
| Telegram command | Effect |
|---|---|
| `/status` | every app: version and state, free disk, data size, pending update, interrupted change |
| `/check [app]` | look for a release now |
| `/approve [app] <tag>` | deploy the pending update |
| `/reject [app] <tag>` | never offer this release again |
| `/rollback [app]` | describe what a rollback would restore, and how old the backup is |
| `/rollback [app] CONFIRM` | roll back |
| `/backups [app]` | list backups |
| `/restore [app] <id>` / `/restore [app] <id> CONFIRM` | describe / restore a backup |
| `/update <version>` | show which apps a Talaria update would restart, with an **Update** button |

With one app registered, the app name is optional. With several, a command without one
answers "Which app?" with a button per app; the button runs the read-only or describe
form (status, rollback description), never a confirm.
```

   and in the paragraph after it replace "and once per new Talaria release." with "and once per new Talaria release (with what it would restart, per app, and an **Update** button)."

5. In "Configuration", replace the `talaria_repo` row with `| \`talaria_repo\` | not used since v0.5 (see the hub's \`hub.conf\`) |` and the `check.time` row with `| \`check.time\` | not used since v0.5 (see the hub's \`hub.conf\`) |`, and add at the end of the section:

```markdown
### The hub's `hub.conf`

`~/.config/talaria/hub.conf` of the hub account, `key = value`:

| Key | Default |
|---|---|
| `apps` | written by setup: `<app>:<account>` pairs, e.g. `hermes:hermes clawvisor:clawvisor` |
| `check.time` | `04:30` (the daily check of every app and of Talaria itself) |
| `talaria_repo` | this repository |
| `telegram_api` | `https://api.telegram.org` |

Run setup again (for any app) after changing it.
```

6. In "Clawvisor", replace the paragraph starting "**Clawvisor gets its own, second Telegram bot.**" with:

```markdown
**Clawvisor uses the same bot as Hermes.** Its setup registers it with the hub; there is
no second bot, token or pairing. In the chat, name the app when you type a command:
`/approve clawvisor v0.9.10`.
```

7. In "Security", add the table row `| The hub account (\`talaria\`) | holds the bot token; may run only \`talaria op …\` as each app |` and the bullet "- The hub reaches an app only through `talaria op`, which accepts a fixed list of operations (status, check, deploy, rollback, restore, button, self-update, …) and nothing with a shell. App accounts hold no bot token."

8. Replace "Removing the sudo rule" with:

```markdown
## Removing the sudo rules

After setup you can remove your own rules: `sudo rm /etc/sudoers.d/talaria-<user>
/etc/sudoers.d/talaria-talaria`. Talaria keeps working; only `talaria setup` from your
login account needs them again. Keep `/etc/sudoers.d/talaria-talaria-<user>`: the bot needs
it to reach the app.
```

9. Replace the body of "Updating Talaria" with:

~~~markdown
The hub checks for new Talaria releases daily and sends which apps an update would
restart, with an **Update Talaria to vX.Y.Z** button; `/update vX.Y.Z` asks for the same.
The update runs hub first, then each app, then restarts the bot, and reports per app. From
a shell, as the hub:

```bash
sudo -u talaria -H ~talaria/.local/bin/talaria self-update vX.Y.Z
```

An app whose update failed keeps its version; its commands answer "Talaria versions differ
on this host; run /update" until an update succeeds.

### Moving from v0.4

v0.4 ran one bot per app. After `self-update v0.5.0`, an install keeps its own bot and
reminds you daily to move it: from a v0.5 checkout, as your login account, run
`bin/talaria setup --app <app> --user <user>`. Setup prints one root paste (the hub account
and its rules), stops the app's own bot, moves its token and pairing to the hub — the same
bot and chat, no new pairing — and starts the hub's bot. The app is not restarted. If the
hub already has a bot, the app's own token is dropped.
~~~

10. In "Uninstall", replace the first code block and the text around it with:

~~~markdown
As the hub (`talaria`):

```bash
systemctl --user disable --now talaria-check.timer talaria-telegram.service
rm ~/.config/systemd/user/talaria-*
rm -r ~/.local/share/talaria ~/.local/bin/talaria ~/.config/talaria
systemctl --user daemon-reload
```

As each app's service user:

```bash
systemctl --user stop hermes.service
rm ~/.config/containers/systemd/hermes.container
rm -r ~/.local/share/talaria ~/.local/bin/talaria ~/.config/talaria
systemctl --user daemon-reload
podman images --format '{{.Repository}}:{{.Tag}}' localhost/hermes-agent | xargs -r podman image rm
```

Then as root: `rm /etc/sudoers.d/talaria-*` and `loginctl disable-linger <user>` for each
account (or `userdel -r <user>` if the account served nothing else; that also deletes the
data).
~~~

11. In "Architecture", append:

```markdown
One hub account per host runs the Telegram bot and the daily timer (`talaria/telegram.py`,
`talaria/hubcheck.py`, `talaria/hubupdate.py`); `hub.conf` makes an account the hub
(`talaria/hubconf.py`). The hub reaches each app only through `talaria op`
(`talaria/op.py`), run as the app's account through one sudo rule (`talaria/hubexec.py`).
`op` writes JSON lines, which `talaria/relay.py` turns into Telegram messages, prefixing
button data with the app's name.
```

`AGENT_SETUP.md`: replace step 4 with

```markdown
4. Setup uses a dedicated service user per app, `hermes` by default (`clawvisor` by default
   for a Clawvisor install), and one hub account per host, `talaria`, which runs the
   Telegram bot for every app. **Confirm both names with the person first** (`--hub NAME`
   picks another hub name), then run `bin/talaria setup --plan --app APP --user NAME` and
   explain the plan in plain words. Pass the same flags on every later run. If setup reports
   `FOUND: account … exists`, ask whether that account should be used.
```

replace in step 6 the sentences from "The person runs `set-token`" to "never reuse Hermes's token)." with

```markdown
   The person runs `set-token` in their own terminal as the hub account, once per host
   (setup prints the exact command). A second app uses the same bot: no new token, no
   pairing.
```

and add after step 7:

```markdown
7a. If setup plans to "move the bot" from an app's account to the hub (an install from
   v0.4), explain it: the same bot and chat, no new pairing, the app is not restarted.
   `NOTE:` lines are informational; relay them.
```

- [ ] **Step 9: Full unit suite, then the mutation run**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS.

Run (long; up to several hours):
`timeout 21600 systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 uv run mutmut run`
then `uv run mutmut export-cicd-stats && uv run python tests/mutation_score.py`.
Expected: `score: ≥ 85.0%` and exit 0. List survivors per module with `uv run mutmut results`. For every survivor in `talaria.hubconf`, `talaria.op`, `talaria.hubexec`, `talaria.relay`, `talaria.telegram`, `talaria.hubcheck`, `talaria.hubupdate`, `talaria.selfupdate` and in the new functions of `talaria.setup` (`account_lines`, `op_rule_lines`, `root_block`, `operator_phase`, `hub_phase`, `_telegram_ready`, `move_env`, `_v04_bot`, `_migrate`, `import_telegram`): kill it with a focused test (exact text, exact argv, exact timeout) or, if it is equivalent, mark the line `# pragma: no mutate` with the reason next to it. `rollback`, `restore`, `marker`, `deploy` must keep 0 true survivors. Re-run `mutmut run` on the touched modules until stable.

- [ ] **Step 10: Mutation report**

In `docs/mutation-report.md`, replace the first two paragraphs (date line and score paragraph) with the new run's facts in the same form — the date of the run, the commit it ran on, `v0.5.0, hub`, tool versions, score, totals (killed, timeouts, survived, no tests, suspicious) exactly as `tests/mutation_score.py` printed them — and replace the module table with the new `mutmut results` counts per module (new rows for the eight hub modules). Add a section "## What changed in v0.5.0 (hub)" listing: the new modules and their survivor counts; every new `# pragma: no mutate` line with its reason; that the e2e suite now runs hub, Hermes and Clawvisor on one runner. Keep the older sections below it unchanged.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml talaria/__init__.py uv.lock tests/test_cli.py tests/e2e .github/workflows/ci.yml \
        README.md AGENT_SETUP.md docs/mutation-report.md talaria tests
git commit -m "v0.5.0: hub e2e on one runner, docs, mutation report"
```

(The controller pushes, watches CI — including the e2e job — merges and tags.)
