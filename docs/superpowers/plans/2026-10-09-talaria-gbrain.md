# Talaria gbrain (v0.6.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Talaria manages gbrain (agent memory with an MCP server) as a third app, like Hermes and Clawvisor (release detection, rehearsal on a copy, approval, backup, deploy, verify, rollback), and adds two generic features: a per-app release-check cadence (`check.days`) and a nightly maintenance window (`maintenance.time`) that gbrain uses for `gbrain dream`.

**Architecture:** A new adapter `talaria/apps/gbrain.py` builds a local image from the verified release binary (API sha256 digest, plus `gh attestation verify` when available) on a pinned distroless/cc base. It rehearses offline with `gbrain doctor --json` and `recall` on a copy, and checks the schema in `before_start`. Generic core hooks are small and default to no-ops: `App.initialize` and `App.setup_notes` for setup, `App.copy_stopped` for the rehearsal copy, and `App.maintenance` for the window. A new hub timer `talaria-maintain.timer` runs every 15 minutes and relays `op maintain --timer` to each app with a hook. The app decides whether its window is due (`talaria/maintain.py`). `check.days` is enforced on the app side, in `cli._locked`.

**Tech Stack:** Python ≥ 3.10 stdlib only, pytest, mutmut 3, rootless podman + Quadlet, systemd user units, GitHub Actions; gcc only for the e2e fake release (C, static).

**Spec:** `docs/superpowers/specs/2026-10-09-talaria-gbrain-design.md` (binding). Evidence: `.superpowers/sdd/2026-10-09-talaria-gbrain/gbrain-spike.md`. Brief: `.superpowers/sdd/2026-10-09-talaria-gbrain/plan-brief.md`.

**Rulings added by this plan.** The spec is silent or ambiguous on these points. They are decisions; apply them as written.
- R1. **Fake release is new.** Clawvisor's e2e uses the real upstream releases; there is no Clawvisor fake to copy. The gbrain fake is a small C program (`tests/e2e/fake_gbrain/fake_gbrain.c`), compiled `-static` once per release. A local static web server (`tests/e2e/fake_release.py`, port 8092) serves three things: the bare git repo over git's dumb HTTP protocol, the asset under `releases/download/<tag>/`, and the release JSON under `api/releases/tags/<tag>`.
- R2. **Release lookup.** If `repo` is `https://github.com/<o>/<r>`, the JSON comes from `https://api.github.com/repos/<o>/<r>/releases/tags/<tag>`. Any other repo uses `<repo>/api/releases/tags/<tag>`, the fake's layout. The asset is always `<repo>/releases/download/<tag>/gbrain-linux-x64`. The JSON's `assets[].digest` (`sha256:<hex>`) is the reference. A missing or malformed digest is Permanent ("failed verification"). A missing asset, a non-200 answer (including a 403 rate limit) or no answer is Transient.
- R3. **Attestation** runs only when all three hold: the repo is on GitHub, `gh` is on PATH, and `gh auth status` exits 0. Any non-zero `gh attestation verify` is Permanent. Setup prints the "digest only" NOTE only for GitHub repos.
- R4. **The "schema live" check runs in `before_start`**, not after the start. It runs `doctor --json` with the *new* image on the stopped production data, offline, and compares the result with the rehearsal's. Reason: the CLI cannot open the brain while the server holds it (spike §8), and `/health` does not report the schema. `after_start` stays the default (`None`); health is `/health`.
- R5. **Data version** is the integer in `<data_dir>/.talaria-schema`. Talaria writes it after every `doctor` run on production data (initialize, before_start). The rehearsal's "before" reads the copy's file instead of running the old image; when the file is missing, "before" shows as "unknown".
- R6. **Rehearsal** runs `doctor --json`, then `recall --query "talaria rehearsal marker"`, both on the copy. It never starts the server, always runs with `--network=none` and never mounts the env file. Only the `schema_version` check's `status == "ok"` counts. Doctor's exit code is ignored, because keyless installs warn about embeddings. A timeout is Permanent.
- R7. **The rehearsal copy is taken with gbrain stopped** (`App.copy_stopped = True`): stop, copy, start. The checks run after the start. That start gets no health check; `/status` reports a service that is not running.
- R8. **First run.** Setup calls the new hook `App.initialize(ctx)` after `current` is tagged and before the first start. For gbrain it runs, in order: `init --pglite` (only if `.gbrain/config.json` is missing), `config set self_upgrade.mode off`, `remember "<marker text>"`, `doctor --json`, then writes `.talaria-schema` and `.talaria-init` (done marker). A failure raises `ValueError`, which becomes `STOP:`. A second hook, `App.setup_notes(ctx)`, provides the `NOTE:` lines.
- R9. **Maintenance timer.** The hub gets `talaria-maintain.service` and `talaria-maintain.timer` (`OnCalendar=*:0/15`). They run `talaria maintain --timer`, which relays `op maintain --timer` only to apps whose adapter has `has_maintenance`. The app decides whether the run is due:
  - The window is `[maintenance.time, +2 h)` in local time. A window that starts before midnight still counts after midnight.
  - The run happens once per window. The state key `maintenance = {"date", "ok"}` is saved *before* the hook runs.
  - On the timer, the run is skipped silently when the time is outside the window, the window is already done, the service is not active, a change is interrupted, or the op lock is busy.
  - A manual `talaria maintain` (app or hub) runs immediately and also reports success.
- R10. **"Skipped while a release offer is pending deployment"** means while a deploy, rollback or restore runs (the op lock) or is interrupted (`st["op"]` or the `changing` marker). A pending offer (`st["pending"]`) does **not** block maintenance, because gbrain has one pending almost all the time.
- R11. **Hub maintenance ticks never send a message about failing to reach an app.** Failures go to the journal only; the daily check already reports unreachable apps and version mismatches. Messages the app itself sends are forwarded.
- R12. **The `check.days` gate is on the app side.** In `cli._locked`, a timer check on a day outside `check.days` only does the daily history commit. Local time is `ctx.now().astimezone()`.
- R13. **New conf keys** are validated at load, like `dashboard.bind`: a bad value raises at runtime too, and unknown-key tolerance is unchanged. `check.days` entries are case-insensitive. An empty `check.days =` means every day; an empty `maintenance.time =` means no window. When a key is absent, the adapter default applies.
- R14. **gbrain Quadlet.** `Exec=serve --http --bind 0.0.0.0 --port 3131 --public-url <dashboard.public_url> --enable-dcr --fail-fast --surface full`. `--surface full` comes from the spike's flag list and spec §2 ("full feature set"). There is no `PUBLIC_URL` env line. `dashboard.public_url` is required: `prepare` and `quadlet_vars` raise without it.
- R15. **Tailscale docs** use one `tailscale funnel --bg --https=8443 --set-path <p> http://127.0.0.1:<port><p>` per public path, instead of spec §5.3's "`serve …` + `funnel --bg 8443`". In the current CLI a bare port argument is a proxy *target*, not "turn funnel on for this port". The tailnet-only port is `tailscale serve --bg --https=10000 http://127.0.0.1:<port>`.
- R16. **e2e order**: `test_e2e.py`, `test_e2e_clawvisor.py`, `test_e2e_gbrain.py`, `test_e2e_hub.py`. The hub's test_03 stops the main hub's bot, so gbrain must run before it.
- R17. **One-off containers** are named `talaria-gbrain-oneoff` and are removed (`rm -f`) before and after each run. Timeouts: dream 3600 s, everything else 300 s.
- R18. **gbrain defaults**: title `gbrain`, user `gbrain`, data `~/gbrain-data`, port 3131 (container 3131), repo `https://github.com/garrytan/gbrain`, `min_release = v0.60.116.0`, `check.days = mon thu`, `maintenance.time = 03:30`, `backup_exclude = ()`, `can_adopt = False`, amd64 only.
- R19. `op` protocol stays 1: `maintain` is an additive op. An older app answers `maintain` with exit 2, which R11 keeps quiet.

## Global Constraints

- The repository stays generic. No host names, IPs, tailnet names, accounts or emails of any installation may appear in code, templates, tests or docs. Use placeholders (`<host>.<tailnet>.ts.net`, `<tailscale ip>`, `example.invalid`). CI greps for `100\.x.x.x`, `openclaw` and `@gmail.com`.
- The Hermes and Clawvisor Quadlets stay byte-identical. `tests/golden/*` (including the new `default.clawvisor.container`, Task 1) and the golden tests in `tests/test_units.py` must pass unmodified after Task 1.
- Python stdlib only at runtime; Python ≥ 3.10 (no `match`; every module starts with `from __future__ import annotations`). `bin/talaria` runs `python3 -I`.
- Every test run is capped: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract` (single files: the same with the file path instead of `tests …`). mutmut runs with `MemoryMax=4G`. Never `prlimit`.
- Secrets (the admin token, provider keys, the bot token) are never printed, logged, put in an argv or sent to Telegram. Generated secrets go through `talaria.conf.write_env_value` (file mode 0600). Text from the app env file that could appear in a message is redacted.
- Conf loading: tolerant at runtime (an unknown key warns), strict in setup (an unknown key stops). New keys: `check.days`, `maintenance.time`.
- `FakeShell` rules: `check` is a bool; `timeout` is a positive number or `None`.
- Tests patch adapter attributes on the **instance** (`monkeypatch.setattr(apps.get("x"), ...)` or `ctx.app`), never on the class. `monkeypatch` leaves an instance attribute behind on undo, and that would shadow a later class patch.
- One bot per host: app installs never install, enable or restart hub units.
- Mutation score ≥ 85 %; `rollback`, `restore`, `marker` and `deploy` keep 0 true survivors.
- Commit on branch `feat/gbrain`. Every commit message ends with your session's attribution lines. No merge, tag, push or host steps.

## Review Focus

1. **A brain with provider keys is rehearsed.** The rehearsal must stay offline and must not mount the env file. Otherwise a provider outage could fail a release, and keys could reach a rehearsal log. → Task 6 (`test_rehearsal_runs_offline_without_the_env_file`).
2. **A dream that hangs or crashes.** gbrain must be started again, one message sent, and the night recorded as done, so the run is not repeated. → Task 3 (`test_hook_exception_still_starts_the_app_and_reports`).
3. **`maintenance.time` near midnight (23:30).** A tick at 00:15 belongs to the previous evening's window and runs once. → Task 3 (`test_window_crossing_midnight_runs_once`).
4. **A provider key echoed in dream's error output** must not reach Telegram. → Task 6 (`test_maintenance_failure_line_redacts_env_values`).
5. **GitHub's API refuses an unauthenticated lookup (403 rate limit) or the network blips.** The release must not be marked failed for good: the outcome is Transient. → Task 5 (`test_rate_limited_release_api_is_transient`).

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `talaria/conf.py` | `DAYS`, `Conf.check_days`, `Conf.maintenance_time`, validation, adapter defaults | 1 |
| `talaria/ctx.py` | `local_now(ctx)` | 1 |
| `talaria/apps/base.py` | new hooks/attributes with no-op defaults | 1 |
| `talaria/check.py` | `due_today(ctx)` | 1 |
| `talaria/cli.py` | check.days gate (1); `maintain` command (3); hub `maintain` (4); `--app` help (5) | 1, 3, 4, 5 |
| `tests/golden/default.clawvisor.container` | pins Clawvisor's Quadlet | 1 |
| `talaria/setup.py` | `setup_notes`/`initialize` in the service phase (2); enable `talaria-maintain.timer` (4) | 2, 4 |
| `talaria/rehearse.py` | stop the app while copying when `copy_stopped` | 2 |
| `talaria/maintain.py` | app side of the window: `window_start`, `run` | 3 |
| `talaria/op.py` | `op maintain [--timer]` | 3 |
| `talaria/hubmaintain.py` | hub tick: relay `maintain` to apps with a hook | 4 |
| `talaria/units.py`, `templates/talaria-maintain.{service,timer}` | hub units | 4 |
| `talaria/apps/__init__.py`, `talaria/apps/gbrain.py`, `templates/gbrain.Containerfile` | registry; releases, fetch/verify, health | 5 |
| `talaria/apps/gbrain.py`, `templates/gbrain.container` | Quadlet, prepare, initialize, rehearsal, before_start, data version, maintenance hook | 6 |
| `tests/e2e/fake_gbrain/fake_gbrain.c`, `tests/e2e/fake_release.py`, `tests/e2e/conftest.py`, `tests/e2e/test_e2e_gbrain.py`, `tests/test_fake_gbrain.py`, `tests/contract/test_contract_gbrain.py`, `.github/workflows/ci.yml`, `README.md`, `AGENT_SETUP.md`, version files, `docs/mutation-report.md` | e2e, docs, release | 7 |

---

### Task 1: Per-app check cadence (`check.days`), maintenance-time key, adapter hook defaults

**Files:**
- Create: `tests/test_cadence.py`, `tests/golden/default.clawvisor.container`
- Modify: `talaria/conf.py`, `talaria/ctx.py`, `talaria/apps/base.py`, `talaria/check.py`, `talaria/cli.py` (`_locked`), `tests/fakes.py` (`local_tz`), `tests/test_apps.py` (`test_base_class_defaults`), `tests/test_units.py` (Clawvisor golden test)

**Interfaces:**
- Consumes: `talaria.conf.load_conf(paths, strict=False) -> Conf`; `talaria.apps.get(name) -> App`; `ctx.now() -> datetime` (aware, UTC in tests: `Clock` starts at Sunday 2026-09-27 04:30 UTC); `talaria.history.commit(ctx, st, msg)`; `talaria.check.check(ctx, st)`.
- Produces:
  - `talaria.conf.DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")`.
  - `Conf.check_days: tuple = ()` (empty: every day), `Conf.maintenance_time: str = ""` (empty: none). Keys `check.days` (list) and `maintenance.time` (str).
  - `talaria.conf.check_days(value: tuple, where) -> tuple` (lower-cases, raises `ValueError`); `talaria.conf.check_time_of_day(value: str, key: str, where) -> None` (raises `ValueError`).
  - `talaria.ctx.local_now(ctx) -> datetime` (= `ctx.now().astimezone()`).
  - `talaria.check.due_today(ctx) -> bool`.
  - `App` attributes and methods: `copy_stopped = False`, `has_maintenance = False`, `default_check_days: tuple = ()`, `default_maintenance_time = ""`, `maintenance(ctx) -> str | None` (raises `NotImplementedError`), `initialize(ctx) -> list[str]` (returns `[]`), `setup_notes(ctx) -> list[str]` (returns `[]`).
  - `tests.fakes.local_tz(name="UTC")`: a context manager that sets `TZ` and calls `time.tzset()`.

- [ ] **Step 1: Pin Clawvisor's Quadlet before anything changes**

```bash
uv run python -c "
from tests.test_units import _fixed_ctx
from talaria import units
open('tests/golden/default.clawvisor.container', 'w').write(units.render_quadlet(_fixed_ctx('clawvisor')))"
```

Append to `tests/test_units.py`:

```python
def test_clawvisor_quadlet_golden():
    assert units.render_quadlet(_fixed_ctx("clawvisor")) == \
        (GOLDEN / "default.clawvisor.container").read_text()
```

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_units.py`
Expected: PASS.

- [ ] **Step 2: Add `local_tz` to `tests/fakes.py`**

Add at the top of `tests/fakes.py` (with the other imports) and below `Clock`:

```python
import os
import time
from contextlib import contextmanager


@contextmanager
def local_tz(name: str = "UTC"):
    """Run with the process's local time zone set to `name` (talaria.ctx.local_now)."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
```

- [ ] **Step 3: Write the failing tests**

```python
# tests/test_cadence.py
import io

import pytest

from talaria import apps, check, cli, op
from talaria.conf import DAYS, load_conf
from talaria.ctx import Paths, local_now
from tests.fakes import local_tz, make_test_ctx


@pytest.fixture(autouse=True)
def utc():
    with local_tz():
        yield


def conf_with(tmp_path, text, app="hermes"):
    p = Paths(tmp_path, app)
    p.conf_dir.mkdir(parents=True, exist_ok=True)
    p.conf_file.write_text(text)
    return load_conf(p)


def test_days_constant():
    assert DAYS == ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def test_defaults_are_every_day_and_no_window(tmp_path):
    c = load_conf(Paths(tmp_path))
    assert (c.check_days, c.maintenance_time) == ((), "")


def test_days_and_time_parse(tmp_path):
    c = conf_with(tmp_path, "check.days = Mon thu\nmaintenance.time = 03:30\n")
    assert (c.check_days, c.maintenance_time) == (("mon", "thu"), "03:30")


@pytest.mark.parametrize("line,msg", [
    ("check.days = mon funday", "check.days: 'funday' is not one of mon tue wed thu fri sat sun"),
    ("maintenance.time = 3:30", "maintenance.time must be HH:MM"),
    ("maintenance.time = 24:00", "maintenance.time must be HH:MM"),
    ("maintenance.time = 03:60", "maintenance.time must be HH:MM"),
    ("maintenance.time = 03:30 ", None),
])
def test_bad_values_are_errors_even_at_runtime(tmp_path, line, msg):
    if msg is None:     # parse_kv trims; a trailing space is fine
        assert conf_with(tmp_path, line + "\n").maintenance_time == "03:30"
        return
    with pytest.raises(ValueError, match=msg):
        conf_with(tmp_path, line + "\n")


def test_adapter_defaults_apply_unless_set(tmp_path, monkeypatch):
    cv = apps.get("clawvisor")
    monkeypatch.setattr(cv, "default_check_days", ("mon", "thu"))
    monkeypatch.setattr(cv, "default_maintenance_time", "03:30")
    c = conf_with(tmp_path, "app = clawvisor\n", app="clawvisor")
    assert (c.check_days, c.maintenance_time) == (("mon", "thu"), "03:30")
    c = conf_with(tmp_path, "app = clawvisor\ncheck.days =\nmaintenance.time =\n", app="clawvisor")
    assert (c.check_days, c.maintenance_time) == ((), "")


@pytest.mark.parametrize("days,due", [((), True), (("sun",), True), (("mon", "thu"), False)])
def test_due_today_uses_the_local_weekday(tmp_path, days, due):
    ctx = make_test_ctx(tmp_path, check_days=days)     # the test clock: Sunday 2026-09-27
    assert check.due_today(ctx) is due


def test_local_now_is_aware_local_time(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert local_now(ctx).hour == 4 and local_now(ctx).tzinfo is not None
    with local_tz("Europe/Berlin"):
        assert local_now(ctx).hour == 6       # 04:30 UTC is 06:30 CEST


@pytest.fixture
def gate(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, check_days=("mon",))     # Sunday: not a check day
    seen = []
    monkeypatch.setattr(cli.check, "check", lambda c, st: seen.append("check"))
    monkeypatch.setattr(cli.history, "commit", lambda c, st, msg: seen.append(("history", msg)))
    return ctx, seen


def test_timer_check_outside_check_days_only_commits_history(gate):
    ctx, seen = gate
    assert cli.main(["check", "--timer"], make=lambda: ctx) == 0
    assert seen == [("history", "daily")]


def test_on_demand_check_ignores_check_days(gate):
    ctx, seen = gate
    assert cli.main(["check"], make=lambda: ctx) == 0
    assert seen == ["check"]


def test_timer_check_on_a_check_day_runs(gate):
    ctx, seen = gate
    ctx.conf.check_days = ("sun",)
    assert cli.main(["check", "--timer"], make=lambda: ctx) == 0
    assert seen == ["check"]


def test_op_check_timer_is_gated_the_same_way(gate):
    ctx, seen = gate
    assert op.main(["check", "--timer"], make=lambda: ctx, out=io.StringIO()) == 0
    assert seen == [("history", "daily")]
```

Extend `test_base_class_defaults` in `tests/test_apps.py` (inside the function, after the existing asserts):

```python
    assert (app.copy_stopped, app.has_maintenance, app.default_check_days,
            app.default_maintenance_time) == (False, False, (), "")
    assert app.initialize(None) == [] and app.setup_notes(None) == []
    with pytest.raises(NotImplementedError):
        app.maintenance(None)
```

- [ ] **Step 4: Run them to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_cadence.py tests/test_apps.py`
Expected: FAIL (`ImportError: cannot import name 'DAYS'`).

- [ ] **Step 5: Implement**

`talaria/conf.py`: add `import re` to the imports, and add the following after `_bool`:

```python
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def check_days(value: tuple, where) -> tuple:
    """check.days: weekday names, any case; () means every day."""
    out = tuple(d.lower() for d in value)
    for d in out:
        if d not in DAYS:
            raise ValueError(f"check.days: {d!r} is not one of {' '.join(DAYS)} in {where}")
    return out


def check_time_of_day(value: str, key: str, where) -> None:
    if value and not _TIME.match(value):
        raise ValueError(f"{key} must be HH:MM (24-hour) or empty, got {value!r} in {where}")
```

In `Conf`, add after `check_time: str = "04:30"`:

```python
    check_days: tuple = ()            # app: days the timer looks for releases; () = every day
    maintenance_time: str = ""        # app: start of the maintenance window; "" = none
```

In `_KEYS`, add:

```python
    "check.days": ("check_days", "list"), "maintenance.time": ("maintenance_time", str),
```

In `load_conf`, after `if "backup_exclude" not in seen: …`:

```python
    if "check_days" not in seen:
        conf.check_days = app.default_check_days
    if "maintenance_time" not in seen:
        conf.maintenance_time = app.default_maintenance_time
    conf.check_days = check_days(conf.check_days, paths.conf_file)
    check_time_of_day(conf.maintenance_time, "maintenance.time", paths.conf_file)
```

`talaria/ctx.py`: add after `_utcnow`:

```python
def local_now(ctx) -> datetime:
    """ctx.now() in the host's local time zone (systemd timers fire in local time)."""
    return ctx.now().astimezone()
```

`talaria/apps/base.py`: add class attributes after `prepare_summary`:

```python
    copy_stopped = False                    # stop the service while the rehearsal copies its data
    has_maintenance = False                 # the app has a maintenance() hook (spec 2026-10-09 §5.2)
    default_check_days: tuple = ()          # talaria.conf check.days default; () = every day
    default_maintenance_time = ""           # talaria.conf maintenance.time default; "" = none
```

and methods at the end of the class:

```python
    def maintenance(self, ctx) -> str | None:
        """Runs with the service stopped, under the op lock (talaria.maintain). Returns a
        failure line (shown as untrusted text) or None."""
        raise NotImplementedError

    def initialize(self, ctx) -> list[str]:
        """Setup, after the current image is tagged and before the start: create the app's
        data if missing. Idempotent. Returns OK-line texts; raises ValueError to stop."""
        return []

    def setup_notes(self, ctx) -> list[str]:
        """NOTE-line texts for setup (printed after the prepare lines)."""
        return []
```

`talaria/check.py`: add imports `from talaria.conf import DAYS` and `from talaria.ctx import local_now`, and add:

```python
def due_today(ctx) -> bool:
    """The timer's release check runs only on the app's check.days (empty: every day).
    /check (no --timer) always checks."""
    days = ctx.conf.check_days
    return not days or DAYS[local_now(ctx).weekday()] in days
```

`talaria/cli.py`, in `_locked`, replace

```python
            if cmd == "check":
                check.check(ctx, st)
```

with

```python
            if cmd == "check":
                if getattr(args, "timer", False) and not check.due_today(ctx):
                    history.commit(ctx, st, "daily")      # the snapshot stays daily
                else:
                    check.check(ctx, st)
```

- [ ] **Step 6: Run the full suite**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS (including `tests/golden/*` and the Clawvisor golden).

- [ ] **Step 7: Commit**

```bash
git add talaria/conf.py talaria/ctx.py talaria/apps/base.py talaria/check.py talaria/cli.py \
        tests/fakes.py tests/test_cadence.py tests/test_apps.py tests/test_units.py \
        tests/golden/default.clawvisor.container
git commit -m "Per-app check.days and maintenance.time; adapter hook defaults; Clawvisor Quadlet golden"
```

---

### Task 2: Core hooks in setup and the rehearsal (`initialize`, `setup_notes`, `copy_stopped`)

**Files:**
- Create: `tests/test_app_hooks.py`
- Modify: `talaria/setup.py` (`service_phase`), `talaria/rehearse.py` (`rehearse`)

**Interfaces:**
- Consumes (Task 1): `App.initialize(ctx) -> list[str]` (may raise `ValueError`), `App.setup_notes(ctx) -> list[str]`, `App.copy_stopped: bool`. Existing: `talaria.service.is_active(ctx) -> bool`, `service.stop(ctx)`, `service.start(ctx)`; `talaria.setup.say(kind, text)`; `rehearse.copy_data(src, dst, excludes)`; `rehearse.Transient`.
- Produces: setup prints `NOTE: <text>` for each `setup_notes` entry right after the `prepare` OK lines (not in `--plan`). Setup calls `initialize` inside the lock, after `images.retag(ctx, "current", …)` and before the start check, prints each returned line as `OK: <text>`, and turns a `ValueError` into `STOP: <text>` with exit 1 and no start. `rehearse.rehearse` stops an active app with `copy_stopped` before `copy_data` and starts it right after, even when the copy fails.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_app_hooks.py
import pytest

from talaria import rehearse
from tests.fakes import make_test_ctx
from tests.test_rehearse import IMG, st_with_current
from tests.test_setup import svc  # noqa: F401  (fixture)
from tests.test_setup_service_golden import (NEW, PULLED, PW, RESTART, RUNNING,  # noqa: F401
                                             UNITS, run, s)


def test_initialize_runs_after_the_image_is_tagged_and_before_the_start(s, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(s.app, "initialize",
                        lambda c: (seen.append((c is s, len(c.sh.calls))), ["brain initialized"])[1])
    rc, out, cmds = run(s, capsys)
    assert rc == 0
    assert out == NEW + PW + PULLED + "OK: brain initialized\n" + RUNNING
    assert seen == [(True, len(UNITS))]      # after daemon-reload and podman tag
    assert cmds == UNITS + RESTART


def test_initialize_failure_stops_before_the_start(s, monkeypatch, capsys):
    def boom(c):
        raise ValueError("gbrain init failed: disk full")
    monkeypatch.setattr(s.app, "initialize", boom)
    rc, out, cmds = run(s, capsys)
    assert rc == 1
    assert out == NEW + PW + PULLED + "STOP: gbrain init failed: disk full\n"
    assert cmds == UNITS


def test_setup_notes_follow_the_prepared_lines(s, monkeypatch, capsys):
    monkeypatch.setattr(s.app, "setup_notes", lambda c: ["digest only"] if c is s else [])
    rc, out, _ = run(s, capsys)
    assert rc == 0 and out == NEW + PW + "NOTE: digest only\n" + PULLED + RUNNING


def test_plan_prints_no_notes(s, monkeypatch, capsys):
    monkeypatch.setattr(s.app, "setup_notes", lambda c: ["digest only"])
    rc, out, _ = run(s, capsys, plan=True)
    assert "NOTE" not in out


SVC = "hermes.service"


def rctx(tmp_path, monkeypatch, active="active\n", stopped=True):
    ctx = make_test_ctx(tmp_path)
    (ctx.conf.data_dir / "f").write_text("x")
    ctx.sh.on("systemctl").on("systemctl", "--user", "is-active", out=active)
    monkeypatch.setattr(ctx.app, "copy_stopped", stopped)
    monkeypatch.setattr(ctx.app, "fetch", lambda c, t, commit: dict(IMG))
    seen = []

    def reh(c, st, image, copy, stage):
        seen.append(((copy / "f").read_text(), list(c.sh.calls)))
        return {"tag": image["tag"]}
    monkeypatch.setattr(ctx.app, "rehearse", reh)
    monkeypatch.setattr(ctx.app, "report_lines", lambda c, r: ([], []))
    monkeypatch.setattr(ctx.app, "pending_extra", lambda r: {})
    return ctx, seen


def sc(*a):
    return ["systemctl", "--user", *a, SVC]


def test_copy_stopped_app_is_stopped_only_while_copying(tmp_path, monkeypatch):
    ctx, seen = rctx(tmp_path, monkeypatch)
    rehearse.rehearse(ctx, st_with_current(), "v2026.9.24", "c")
    assert seen == [("x", [sc("is-active"), sc("stop"), sc("reset-failed"), sc("start")])]


def test_an_inactive_app_is_neither_stopped_nor_started(tmp_path, monkeypatch):
    ctx, seen = rctx(tmp_path, monkeypatch, active="inactive\n")
    rehearse.rehearse(ctx, st_with_current(), "v2026.9.24", "c")
    assert seen[0][1] == [sc("is-active")]


def test_apps_without_copy_stopped_copy_while_running(tmp_path, monkeypatch):
    ctx, seen = rctx(tmp_path, monkeypatch, stopped=False)
    rehearse.rehearse(ctx, st_with_current(), "v2026.9.24", "c")
    assert seen[0][1] == []


def test_the_app_is_started_again_when_the_copy_fails(tmp_path, monkeypatch):
    ctx, seen = rctx(tmp_path, monkeypatch)

    def broken(src, dst, excludes):
        raise OSError("disk full")
    monkeypatch.setattr(rehearse, "copy_data", broken)
    with pytest.raises(rehearse.Transient, match="could not copy the data dir: disk full"):
        rehearse.rehearse(ctx, st_with_current(), "v2026.9.24", "c")
    assert ctx.sh.calls == [sc("is-active"), sc("stop"), sc("reset-failed"), sc("start")]
    assert seen == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_app_hooks.py`
Expected: FAIL (no `OK: brain initialized`, no `NOTE`, no systemctl calls).

- [ ] **Step 3: Implement**

`talaria/setup.py`, in `service_phase`, directly after

```python
    for line in prepared:
        say("OK", line)
```

add

```python
    for note in ctx.app.setup_notes(ctx):
        say("NOTE", note)
```

and replace

```python
            changed = units.install_units(ctx)
            images.retag(ctx, "current", state.load(p)["current"])
```

with

```python
            changed = units.install_units(ctx)
            images.retag(ctx, "current", state.load(p)["current"])
            try:
                for line in ctx.app.initialize(ctx):
                    say("OK", line)
            except ValueError as e:
                say("STOP", str(e))
                return 1
```

`talaria/rehearse.py`: change `from talaria import disk` to `from talaria import disk, service`, and replace

```python
    try:
        try:
            copy_data(data, copy, ctx.conf.backup_exclude)
        except (OSError, shutil.Error, sqlite3.Error) as e:
            raise Transient(f"could not copy the data dir: {str(e)[:300]}") from None
        report = ctx.app.rehearse(ctx, st, image, copy, stage)
```

with

```python
    try:
        # an app whose files are only consistent at rest (gbrain's PGLite) is stopped
        # for the copy; seconds of downtime
        stopped = ctx.app.copy_stopped and service.is_active(ctx)
        if stopped:
            service.stop(ctx)
        try:
            copy_data(data, copy, ctx.conf.backup_exclude)
        except (OSError, shutil.Error, sqlite3.Error) as e:
            raise Transient(f"could not copy the data dir: {str(e)[:300]}") from None
        finally:
            if stopped:
                service.start(ctx)
        report = ctx.app.rehearse(ctx, st, image, copy, stage)
```

- [ ] **Step 4: Run the full suite**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS (the Hermes/Clawvisor setup goldens are unchanged, because the defaults print nothing).

- [ ] **Step 5: Commit**

```bash
git add talaria/setup.py talaria/rehearse.py tests/test_app_hooks.py
git commit -m "Setup runs App.initialize/setup_notes; rehearsal stops copy_stopped apps while copying"
```

---

### Task 3: Maintenance window, app side (`talaria maintain [--timer]`, `op maintain`)

**Files:**
- Create: `talaria/maintain.py`, `tests/test_maintain.py`
- Modify: `talaria/cli.py` (parser, `_locked`, `run_locked`, import), `talaria/op.py` (`OPS`, parser, `_run`)

**Interfaces:**
- Consumes (Task 1): `talaria.ctx.local_now(ctx) -> datetime`; `ctx.conf.maintenance_time: str` (`"HH:MM"` or `""`); `App.has_maintenance: bool`; `App.maintenance(ctx) -> str | None`. Existing: `talaria.rollback.interrupted(ctx, st) -> str | None`; `talaria.service.is_active/stop/start/post_start_check(ctx)` (`post_start_check` returns a reason or `None`); `talaria.state.save(paths, st)`; `talaria.notify.Message(text, untrusted=[(title, body)])`; `cli.run_locked(ctx, args) -> int`; `cli.EXIT_BUSY == 75`.
- Produces:
  - `talaria.maintain.WINDOW = timedelta(hours=2)`.
  - `talaria.maintain.window_start(ctx) -> datetime | None`.
  - `talaria.maintain.run(ctx, st: dict, timer: bool) -> None`. It sets `st["maintenance"] = {"date": "YYYY-MM-DD", "ok": bool}`. Messages: failure `"<Title> maintenance failed. Talaria tries again next night."` with untrusted `[("Last error", reason)]`; manual success `"<Title> maintenance finished."`; manual with no hook `"<Title> has no maintenance."`; manual while interrupted `"<Title> maintenance not run: <why>."`.
  - CLI: `talaria maintain [--timer]` (app account) runs under the op lock. A busy lock with `--timer` exits 0 silently.
  - `talaria op maintain [--timer]` → `cli.run_locked(ctx, Namespace(cmd="maintain", timer=…))`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_maintain.py
import copy
import io
import subprocess
from datetime import datetime, timezone

import pytest

from talaria import cli, lock, maintain, marker, op, state
from tests.fakes import local_tz, make_test_ctx


@pytest.fixture(autouse=True)
def utc():
    with local_tz():
        yield


def at(ctx, h, m, day=8):
    ctx.clock.t = datetime(2026, 10, day, h, m, tzinfo=timezone.utc)
    ctx.clock.slept = 0.0


def verbs(ctx):
    return [c[2] for c in ctx.sh.calls if c[:2] == ["systemctl", "--user"]]


def st0():
    return copy.deepcopy(state.DEFAULT)


@pytest.fixture
def mctx(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, maintenance_time="03:30")
    at(ctx, 3, 40)                               # Thursday 03:40, inside 03:30-05:30
    ctx.sh.on("systemctl").on("systemctl", "--user", "is-active", out="active\n")
    monkeypatch.setattr(ctx.app, "has_maintenance", True)
    ctx.hook = {"reason": None, "exc": None, "down": None, "seen": []}

    def hook(c):
        assert c is ctx
        ctx.hook["seen"].append(verbs(c))
        if ctx.hook["exc"]:
            raise ctx.hook["exc"]
        return ctx.hook["reason"]
    monkeypatch.setattr(ctx.app, "maintenance", hook)
    monkeypatch.setattr(maintain.service, "post_start_check", lambda c: ctx.hook["down"])
    return ctx


def test_timer_run_stops_runs_the_hook_starts_and_stays_silent(mctx):
    st = st0()
    maintain.run(mctx, st, timer=True)
    assert mctx.hook["seen"] == [["is-active", "stop"]]
    assert verbs(mctx) == ["is-active", "stop", "reset-failed", "start"]
    assert mctx.notify.sent == []
    assert st["maintenance"] == {"date": "2026-10-08", "ok": True}
    # saved before the hook ran: a crash mid-way does not run it again tonight
    assert state.load(mctx.paths)["maintenance"] == {"date": "2026-10-08", "ok": False}


def test_timer_runs_once_per_window(mctx):
    st = st0()
    maintain.run(mctx, st, timer=True)
    at(mctx, 5, 0)
    maintain.run(mctx, st, timer=True)
    assert len(mctx.hook["seen"]) == 1


@pytest.mark.parametrize("h,m,runs", [(3, 29, False), (3, 30, True), (5, 29, True),
                                      (5, 30, False), (12, 0, False)])
def test_window_is_two_hours_from_maintenance_time(mctx, h, m, runs):
    at(mctx, h, m)
    maintain.run(mctx, st0(), timer=True)
    assert bool(mctx.hook["seen"]) is runs


def test_window_crossing_midnight_runs_once(mctx):
    mctx.conf.maintenance_time = "23:30"
    st = st0()
    at(mctx, 0, 15, day=9)
    maintain.run(mctx, st, timer=True)
    assert st["maintenance"]["date"] == "2026-10-08"
    at(mctx, 1, 0, day=9)
    maintain.run(mctx, st, timer=True)
    assert len(mctx.hook["seen"]) == 1
    at(mctx, 23, 40, day=9)
    maintain.run(mctx, st, timer=True)
    assert st["maintenance"]["date"] == "2026-10-09" and len(mctx.hook["seen"]) == 2


def test_window_start_values(mctx):
    assert maintain.window_start(mctx) == datetime(2026, 10, 8, 3, 30, tzinfo=timezone.utc)
    mctx.conf.maintenance_time = ""
    assert maintain.window_start(mctx) is None


def test_no_window_means_no_timer_runs(mctx):
    mctx.conf.maintenance_time = ""
    maintain.run(mctx, st0(), timer=True)
    assert mctx.sh.calls == [] and mctx.hook["seen"] == []


def test_hook_failure_is_reported_once_and_the_app_started(mctx):
    mctx.hook["reason"] = "dream: phase synthesize failed"
    st = st0()
    maintain.run(mctx, st, timer=True)
    assert verbs(mctx)[-2:] == ["reset-failed", "start"]
    (m,) = mctx.notify.sent
    assert m.text == "Hermes maintenance failed. Talaria tries again next night."
    assert m.untrusted == [("Last error", "dream: phase synthesize failed")]
    assert st["maintenance"] == {"date": "2026-10-08", "ok": False}


def test_hook_exception_still_starts_the_app_and_reports(mctx):
    mctx.hook["exc"] = subprocess.TimeoutExpired(["podman", "run"], 3600)
    st = st0()
    maintain.run(mctx, st, timer=True)
    assert verbs(mctx)[-1] == "start"
    (m,) = mctx.notify.sent
    assert m.untrusted[0][1].startswith("TimeoutExpired: ")
    assert st["maintenance"] == {"date": "2026-10-08", "ok": False}
    maintain.run(mctx, st, timer=True)                   # same night: not again
    assert len(mctx.hook["seen"]) == 1


def test_unhealthy_after_the_start_is_reported(mctx):
    mctx.hook["down"] = "hermes.service is not active"
    maintain.run(mctx, st0(), timer=True)
    assert mctx.notify.sent[0].untrusted == [("Last error", "hermes.service is not active")]


def test_hook_failure_and_unhealthy_name_both(mctx):
    mctx.hook["reason"], mctx.hook["down"] = "boom", "hermes.service restarted"
    maintain.run(mctx, st0(), timer=True)
    assert mctx.notify.sent[0].untrusted == [("Last error", "boom; then hermes.service restarted")]


def test_start_failure_is_reported(mctx):
    mctx.sh.on("systemctl", "--user", "start", rc=1, err="Job failed")
    maintain.run(mctx, st0(), timer=True)
    assert "exited 1" in mctx.notify.sent[0].untrusted[0][1]


def test_timer_skips_an_app_that_is_not_running(mctx):
    mctx.sh.on("systemctl", "--user", "is-active", out="inactive\n")
    st = st0()
    maintain.run(mctx, st, timer=True)
    assert verbs(mctx) == ["is-active"] and st.get("maintenance") is None


@pytest.mark.parametrize("how", ["op", "marker"])
def test_interrupted_change_blocks_maintenance(mctx, how):
    st = st0()
    if how == "op":
        st["op"] = {"op": "deploy", "tag": "v2026.9.24", "started": mctx.now().isoformat()}
    else:
        marker.write(mctx.paths, "deploy", "b", {}, mctx.now())
    maintain.run(mctx, st, timer=True)
    assert verbs(mctx) == ["is-active"] and mctx.notify.sent == []
    maintain.run(mctx, st, timer=False)
    assert mctx.notify.texts() == ["Hermes maintenance not run: an interrupted deploy must be "
                                   "recovered first: send /rollback CONFIRM."]
    assert mctx.hook["seen"] == []


def test_pending_offer_does_not_block_maintenance(mctx):
    st = st0()
    st["pending"] = {"tag": "v2026.9.24"}
    maintain.run(mctx, st, timer=True)
    assert len(mctx.hook["seen"]) == 1


def test_manual_run_ignores_the_window_and_reports_success(mctx):
    mctx.conf.maintenance_time = ""
    at(mctx, 14, 0)
    st = st0()
    maintain.run(mctx, st, timer=False)
    assert mctx.hook["seen"] == [["stop"]]
    assert mctx.notify.texts() == ["Hermes maintenance finished."]
    assert st["maintenance"] == {"date": "2026-10-08", "ok": True}


def test_app_without_maintenance(mctx, monkeypatch):
    monkeypatch.setattr(mctx.app, "has_maintenance", False)
    maintain.run(mctx, st0(), timer=True)
    assert mctx.sh.calls == [] and mctx.notify.sent == []
    maintain.run(mctx, st0(), timer=False)
    assert mctx.notify.texts() == ["Hermes has no maintenance."]


@pytest.fixture
def routed(mctx, monkeypatch):
    seen = []

    def fake(c, st, timer):
        seen.append((c is mctx, timer))
        st["maintenance"] = {"date": "x", "ok": True}
    monkeypatch.setattr(cli.maintain, "run", fake)
    return seen


def test_parser():
    assert vars(cli.build_parser().parse_args(["maintain", "--timer"])) == \
        {"cmd": "maintain", "timer": True}


def test_cli_maintain_runs_under_the_lock_and_saves_state(mctx, routed):
    assert cli.main(["maintain", "--timer"], make=lambda: mctx) == 0
    assert cli.main(["maintain"], make=lambda: mctx) == 0
    assert routed == [(True, True), (True, False)]
    assert state.load(mctx.paths)["maintenance"] == {"date": "x", "ok": True}


def test_busy_timer_maintain_is_silent_manual_is_not(mctx, routed):
    with lock.op_lock(mctx.paths):
        assert cli.main(["maintain", "--timer"], make=lambda: mctx) == 0
        assert mctx.notify.sent == []
        assert cli.main(["maintain"], make=lambda: mctx) == cli.EXIT_BUSY
    assert routed == [] and "Busy" in mctx.notify.sent[-1].text


def test_op_maintain_routes_to_the_locked_command(mctx, routed):
    assert op.main(["maintain", "--timer"], make=lambda: mctx, out=io.StringIO()) == 0
    assert op.main(["maintain"], make=lambda: mctx, out=io.StringIO()) == 0
    assert routed == [(True, True), (True, False)]


@pytest.mark.parametrize("argv", [["maintain", "x"], ["maintain", "--tim"],
                                  ["maintain", "--timer=1"]])
def test_op_refuses_other_maintain_forms(argv):
    out = io.StringIO()
    assert op.main(argv, make=lambda: pytest.fail("no ctx"), out=out) == 2
    assert out.getvalue() == ""
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_maintain.py`
Expected: FAIL (`ImportError: cannot import name 'maintain'`).

- [ ] **Step 3: Implement `talaria/maintain.py`**

```python
"""The nightly maintenance window, app side (spec 2026-10-09 §5.2): `talaria maintain
[--timer]`. The hub's talaria-maintain.timer asks every 15 minutes; this module decides
whether the window is due, then stops the app, runs its hook and starts it again."""
from __future__ import annotations

from datetime import datetime, timedelta

from talaria import service, state
from talaria.ctx import local_now
from talaria.notify import Message
from talaria.rollback import interrupted

WINDOW = timedelta(hours=2)


def window_start(ctx) -> datetime | None:
    """Start of the maintenance window the local time is in now; None without a window or
    outside it. A window that started before midnight still counts after it."""
    t = ctx.conf.maintenance_time
    if not t:
        return None
    now = local_now(ctx)
    h, m = (int(x) for x in t.split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if start > now:
        start -= timedelta(days=1)
    return start if now < start + WINDOW else None


def run(ctx, st: dict, timer: bool) -> None:
    app = ctx.app
    if not app.has_maintenance:
        if not timer:
            ctx.notify.send(Message(f"{app.title} has no maintenance."))
        return
    if timer:
        start = window_start(ctx)
        if start is None:
            return
        day = start.date().isoformat()
        if (st.get("maintenance") or {}).get("date") == day:
            return
        if not service.is_active(ctx):      # stopped on purpose or broken: /status says so
            return
    else:
        day = local_now(ctx).date().isoformat()
    why = interrupted(ctx, st)
    if why:
        if not timer:
            ctx.notify.send(Message(f"{app.title} maintenance not run: {why}."))
        return
    st["maintenance"] = {"date": day, "ok": False}
    state.save(ctx.paths, st)               # a crash below must not repeat the run tonight
    service.stop(ctx)
    try:
        reason = app.maintenance(ctx)
    except Exception as e:                  # the app must come back whatever the hook did
        reason = f"{type(e).__name__}: {e}"
    try:
        service.start(ctx)
        down = service.post_start_check(ctx)
    except Exception as e:
        down = str(e)
    if down:
        reason = f"{reason}; then {down}" if reason else down
    st["maintenance"]["ok"] = reason is None
    if reason:
        ctx.notify.send(Message(f"{app.title} maintenance failed. Talaria tries again next "
                                "night.", untrusted=[("Last error", reason)]))
    elif not timer:
        ctx.notify.send(Message(f"{app.title} maintenance finished."))
```

- [ ] **Step 4: Wire the CLI and op**

`talaria/cli.py`:
- Import: add `maintain` to `from talaria import (__version__, backup, check, deploy, history, lock, maintain, rollback, service, state, status)`.
- Parser, after the `check` subparser:

```python
    m = sub.add_parser("maintain")
    m.add_argument("--timer", action="store_true")
```

- In `_locked`, change `if cmd == "check" or cmd == "rehearse" or cmd == "history":` to `if cmd in ("check", "rehearse", "history", "maintain"):`, and in its inner chain replace

```python
            elif cmd == "rehearse":
                check.rehearse_tag(ctx, st, args.tag)
```

with

```python
            elif cmd == "rehearse":
                check.rehearse_tag(ctx, st, args.tag)
            elif cmd == "maintain":
                maintain.run(ctx, st, args.timer)
```

- In `run_locked`, replace `if args.cmd == "check" and args.timer:` with `if args.cmd in ("check", "maintain") and getattr(args, "timer", False):`.

`talaria/op.py`:
- `OPS`: add `"maintain"` after `"check"`.
- `build_parser`: after `add("check").add_argument("--timer", action="store_true")` add `add("maintain").add_argument("--timer", action="store_true")`.
- `_run`: before the final `else:` of the `if o == "check": … elif …` chain add

```python
    elif o == "maintain":
        ns = argparse.Namespace(cmd="maintain", timer=args.timer)
```

- [ ] **Step 5: Run the full suite**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add talaria/maintain.py talaria/cli.py talaria/op.py tests/test_maintain.py
git commit -m "Maintenance window, app side: talaria maintain [--timer] and op maintain"
```

---

### Task 4: Maintenance window, hub side (timer units, `hubmaintain`, CLI routing)

**Files:**
- Create: `talaria/hubmaintain.py`, `templates/talaria-maintain.service`, `templates/talaria-maintain.timer`, `tests/test_hubmaintain.py`
- Modify: `talaria/units.py`, `talaria/setup.py` (`hub_phase` enable line), `talaria/cli.py` (`HUB_CMDS`, `_hub_main`), `tests/test_units.py` (line 82), `tests/test_setup_hub.py` (`UNITS`, line 76)

**Interfaces:**
- Consumes (Task 1): `App.has_maintenance`. Consumes (Task 3): `op maintain [--timer]` and its `message` lines (`"<Title> maintenance failed. …"` with block `["Last error", …]`, `"<Title> maintenance finished."`). Existing: `hub.apps: dict[name, AppEntry(name, title, executor)]`; `executor.stream(argv)` yields JSON-line dicts and sets `executor.returncode` and `executor.stderr_line`; it raises `talaria.hubexec.Unreachable`. `talaria.relay.to_message(app, d) -> Message`; `talaria.apps.get(name)`.
- Produces:
  - `talaria.hubmaintain.maintain(hub, timer: bool) -> int` (always 0).
  - `talaria.units.HUB_UNITS = ("talaria-check.service", "talaria-check.timer", "talaria-maintain.service", "talaria-maintain.timer", "talaria-telegram.service")`. `render_hub_units` renders these. `TALARIA_UNITS` (the v0.4 app units) is unchanged.
  - The hub phase enables `talaria-check.timer talaria-maintain.timer talaria-telegram.service`.
  - Hub CLI: `talaria maintain [--timer]` → `hubmaintain.maintain(hub, timer=args.timer)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hubmaintain.py
import pytest

from talaria import apps, cli, hubmaintain, units
from talaria.hubexec import Unreachable
from tests.hubfakes import ex, make_hub, message
from tests.test_units import _fixed_ctx, _hub_ctx


@pytest.fixture
def h2(tmp_path, monkeypatch):
    log = []
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=log)
    monkeypatch.setattr(apps.get("clawvisor"), "has_maintenance", True)
    h.log = log
    return h


def test_timer_asks_only_apps_with_maintenance(h2):
    ex(h2, "clawvisor").on("maintain", lines=[])
    assert hubmaintain.maintain(h2, timer=True) == 0
    assert h2.log == [("clawvisor", "stream", ("maintain", "--timer"))]
    assert h2.ctx.notify.texts() == []


def test_manual_run_has_no_timer_flag_and_forwards_messages(h2):
    ex(h2, "clawvisor").on("maintain", lines=[message("Clawvisor maintenance finished.")])
    hubmaintain.maintain(h2, timer=False)
    assert h2.log == [("clawvisor", "stream", ("maintain",))]
    assert h2.ctx.notify.texts() == ["Clawvisor maintenance finished."]


def test_failure_message_from_the_app_keeps_its_block(h2):
    ex(h2, "clawvisor").on("maintain", lines=[message(
        "Clawvisor maintenance failed. Talaria tries again next night.",
        blocks=[["Last error", "boom"]])])
    hubmaintain.maintain(h2, timer=True)
    (m,) = h2.ctx.notify.sent
    assert m.untrusted == [("Last error", "boom")]


def test_unreachable_or_old_app_goes_to_the_journal_only(h2, capsys):
    ex(h2, "clawvisor").on("maintain", exc=Unreachable("clawvisor"))
    assert hubmaintain.maintain(h2, timer=True) == 0
    assert h2.ctx.notify.sent == []
    assert "[talaria] clawvisor: maintain failed: Unreachable('clawvisor')" in capsys.readouterr().err
    ex(h2, "clawvisor").on("maintain", rc=2, err="talaria op: not allowed: 'maintain --timer'")
    hubmaintain.maintain(h2, timer=True)
    assert h2.ctx.notify.sent == []
    assert capsys.readouterr().err == ("[talaria] clawvisor: maintain exited 2: talaria op: "
                                       "not allowed: 'maintain --timer'\n")


def test_one_failing_app_does_not_stop_the_next(tmp_path, monkeypatch):
    log = []
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=log)
    monkeypatch.setattr(apps.get("hermes"), "has_maintenance", True)
    monkeypatch.setattr(apps.get("clawvisor"), "has_maintenance", True)
    ex(h, "hermes").on("maintain", exc=RuntimeError("boom"))
    ex(h, "clawvisor").on("maintain", lines=[])
    hubmaintain.maintain(h, timer=True)
    assert [a for a, _, _ in log] == ["hermes", "clawvisor"]


def test_cli_routes_maintain_in_a_hub(tmp_path, monkeypatch):
    hub = make_hub(tmp_path, ("hermes",))
    seen = []
    monkeypatch.setattr(hubmaintain, "maintain",
                        lambda h, timer: (seen.append((h is hub, timer)), 0)[1])

    def run(*argv):
        return cli.main(list(argv), make=lambda: pytest.fail("app ctx in a hub"),
                        make_hub=lambda: hub)
    assert run("maintain", "--timer") == 0 and run("maintain") == 0
    assert seen == [(True, True), (True, False)]


def test_hub_units_include_the_maintenance_timer():
    r = units.render_hub_units(_hub_ctx())
    assert sorted(r) == list(units.HUB_UNITS)
    assert r["talaria-maintain.service"] == (
        "[Unit]\nDescription=Talaria: nightly app maintenance\n\n[Service]\nType=oneshot\n"
        "ExecStart=%h/.local/bin/talaria maintain --timer\n")
    assert r["talaria-maintain.timer"] == (
        "[Unit]\nDescription=Talaria: maintenance window check\n\n[Timer]\n"
        "OnCalendar=*:0/15\nPersistent=false\n\n[Install]\nWantedBy=timers.target\n")


def test_v04_app_units_stay_three():
    assert sorted(units.render_units(_fixed_ctx())) == list(units.TALARIA_UNITS)
```

Update existing tests:
- `tests/test_units.py` line 82 (`test_install_hub_units_reports_changes`): `== list(units.TALARIA_UNITS)` → `== list(units.HUB_UNITS)`.
- `tests/test_setup_hub.py` line 76: `== list(units.TALARIA_UNITS)` → `== list(units.HUB_UNITS)`; `UNITS` (line 15) becomes:

```python
UNITS = [["systemctl", "--user", "daemon-reload"],
         ["systemctl", "--user", "enable", "--now", "talaria-check.timer",
          "talaria-maintain.timer", "talaria-telegram.service"]]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_hubmaintain.py tests/test_units.py tests/test_setup_hub.py`
Expected: FAIL (`ImportError: cannot import name 'hubmaintain'`).

- [ ] **Step 3: Implement**

`templates/talaria-maintain.service` (ends with one newline):

```
[Unit]
Description=Talaria: nightly app maintenance

[Service]
Type=oneshot
ExecStart=%h/.local/bin/talaria maintain --timer
```

`templates/talaria-maintain.timer` (ends with one newline):

```
[Unit]
Description=Talaria: maintenance window check

[Timer]
OnCalendar=*:0/15
Persistent=false

[Install]
WantedBy=timers.target
```

`talaria/units.py`:

```python
TALARIA_UNITS = ("talaria-check.service", "talaria-check.timer", "talaria-telegram.service")
HUB_UNITS = ("talaria-check.service", "talaria-check.timer", "talaria-maintain.service",
             "talaria-maintain.timer", "talaria-telegram.service")
```

and replace `_render` and `render_hub_units`:

```python
def _render(ctx, title: str, names=TALARIA_UNITS) -> dict[str, str]:
    return {name: _tpl(ctx, name).substitute(check_time=ctx.conf.check_time, title=title)
            for name in names}


def render_hub_units(ctx) -> dict[str, str]:
    return _render(ctx, HUB_TITLE, HUB_UNITS)
```

`talaria/setup.py`, in `hub_phase`, replace the enable call with:

```python
    ctx.sh.run(["systemctl", "--user", "enable", "--now", "talaria-check.timer",
                "talaria-maintain.timer", "talaria-telegram.service"])
```

`talaria/hubmaintain.py`:

```python
"""The hub's maintenance tick (spec 2026-10-09 §5.2): `talaria maintain [--timer]`, run by
talaria-maintain.timer every 15 minutes. Each app with a maintenance hook decides itself
whether its window is due; the hub forwards what it says. Not reaching an app goes to the
journal only: the daily check reports that."""
from __future__ import annotations

import sys
import traceback

from talaria import apps, relay


def maintain(hub, timer: bool) -> int:
    argv = ["maintain", "--timer"] if timer else ["maintain"]
    for name, e in hub.apps.items():
        if not apps.get(name).has_maintenance:
            continue
        try:
            for d in e.executor.stream(argv):
                if d.get("kind") == "message":
                    hub.ctx.notify.send(relay.to_message(name, d))
        except Exception as x:    # one app must not stop the others
            traceback.print_exc(file=sys.stderr)  # pragma: no mutate  (stderr is the default)
            print(f"[talaria] {name}: maintain failed: {x!r}", file=sys.stderr)
            continue
        rc = e.executor.returncode
        if rc:
            print(f"[talaria] {name}: maintain exited {rc}: {e.executor.stderr_line}",
                  file=sys.stderr)
    return 0
```

`talaria/cli.py`: `HUB_CMDS = ("set-token", "bot", "relay", "check", "status", "self-update", "update", "maintain")`. In `_hub_main`, before `from talaria import hubupdate`:

```python
    if cmd == "maintain":
        from talaria import hubmaintain
        return hubmaintain.maintain(hub, timer=args.timer)
```

- [ ] **Step 4: Run the full suite**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS (`tests/golden/*` untouched).

- [ ] **Step 5: Commit**

```bash
git add talaria/hubmaintain.py talaria/units.py talaria/setup.py talaria/cli.py \
        templates/talaria-maintain.service templates/talaria-maintain.timer \
        tests/test_hubmaintain.py tests/test_units.py tests/test_setup_hub.py
git commit -m "Hub maintenance timer: talaria-maintain.timer relays op maintain to apps with a hook"
```

---

### Task 5: gbrain adapter, part 1: registry, releases, verified fetch, health

**Files:**
- Create: `talaria/apps/gbrain.py`, `templates/gbrain.Containerfile`, `tests/test_gbrain.py`
- Modify: `talaria/apps/__init__.py`, `talaria/cli.py` (`--app` help), `tests/test_setup.py` (lines 170, 176, 182), `tests/test_hubconf.py` (line 52)

**Interfaces:**
- Consumes: `talaria.apps.base.App` (Task 1 attributes); `talaria.upstream._ls_remote(sh, repo) -> dict[tag, sha]`; `ctx.http_get(url, timeout) -> (status, bytes)` (status 0 = no answer); `ctx.download(url, dest, max_bytes) -> status` (raises `talaria.ctx.TooLarge`); `talaria.images.RevisionMismatch` (becomes Permanent in `rehearse.rehearse` and STOP in setup); `talaria.rehearse.Transient`; `talaria.state.ensure_dir`; `ctx.paths.staging`, `ctx.paths.templates_dir`.
- Produces (`talaria/apps/gbrain.py`):
  - Constants: `TAG` (regex `^v(\d+)\.(\d+)\.(\d+)\.(\d+)$`), `BASE` (`gcr.io/distroless/cc-debian12@sha256:<64 hex>`), `ASSET = "gbrain-linux-x64"`, `MAX_BINARY = 512 * 1024 * 1024`; module-level `which = shutil.which` (patched in tests).
  - `github_repo(repo: str) -> str | None` (`"owner/name"`); `release_api(repo: str, tag: str) -> str`; `attestation_unavailable(ctx) -> str | None` (reason, or `None` when `gh attestation verify` can run).
  - `class Gbrain(App)` with R18 attributes, `has_maintenance = True`, `copy_stopped = True`, `prepare_summary = "admin token"`, `before_start_error = "the brain check before start failed"`; methods `is_release`, `tag_key -> tuple[int, int, int, int]`, `releases`, `published`, `image_refs -> ["localhost/gbrain"]`, `fetch(ctx, tag, commit) -> {"tag", "id", "digest": "sha256:<hex>", "ref": "build:<tag>", "commit"}`, `reacquire`, `health(ctx) -> str | None`. `APP = Gbrain()`.
  - `talaria.apps.NAMES == ("hermes", "clawvisor", "gbrain")`; `apps.get("gbrain")`.

- [ ] **Step 1: Pin the base image digest**

Resolve the current digest of `gcr.io/distroless/cc-debian12:latest`, either with
`skopeo inspect --format '{{.Digest}}' docker://gcr.io/distroless/cc-debian12:latest`
or with `podman pull -q gcr.io/distroless/cc-debian12:latest && podman image inspect --format '{{index .RepoDigests 0}}' gcr.io/distroless/cc-debian12:latest`.
Use the `sha256:<64 hex>` value as `BASE` in Step 4.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_gbrain.py
import hashlib
import json
from pathlib import Path

import pytest

from talaria import apps
from talaria.apps import gbrain as gb
from talaria.conf import load_conf
from talaria.ctx import Paths, TooLarge
from talaria.images import RevisionMismatch
from talaria.rehearse import Transient
from talaria.shell import Result
from tests.fakes import make_test_ctx

BIN = b"\x7fELF fake gbrain"
SUM = hashlib.sha256(BIN).hexdigest()
TAG = "v0.60.116.0"
GH = "https://github.com/garrytan/gbrain"


def release_json(digest=f"sha256:{SUM}", name="gbrain-linux-x64"):
    return json.dumps({"tag_name": TAG, "assets": [
        {"name": "gbrain-darwin-arm64", "digest": "sha256:" + "0" * 64},
        {"name": name, "digest": digest}]}).encode()


def gctx(tmp_path, monkeypatch, api=None, files=None, gh=None,
         version="gbrain 0.60.116.0\n"):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    seen = {"get": [], "download": [], "containerfile": []}
    status, body = api or (200, release_json())

    def http_get(url, timeout=5.0):
        seen["get"].append((url, timeout))
        return status, body

    def download(url, dest, max_bytes):
        seen["download"].append((url, max_bytes))
        data = (files if files is not None else {"gbrain-linux-x64": BIN}).get(url.rsplit("/", 1)[1])
        if data is None:
            return 404
        dest.write_bytes(data)
        return 200

    def build(argv, input):
        seen["containerfile"].append((Path(argv[-1]) / "Containerfile").read_text())
        return Result(0, "sha256:built\n")
    ctx.http_get, ctx.download, ctx.seen = http_get, download, seen
    monkeypatch.setattr(gb, "which", lambda t: gh)
    ctx.sh.on("uname", "-m", out="x86_64\n")
    ctx.sh.on("podman", "build", fn=build)
    ctx.sh.on("podman", "run", out=version)
    return ctx


def test_registry_and_attributes():
    assert apps.NAMES == ("hermes", "clawvisor", "gbrain")
    a = apps.get("gbrain")
    assert (a.name, a.title, a.unit, a.container, a.quadlet_file, a.env_file, a.local_image) == (
        "gbrain", "gbrain", "gbrain.service", "gbrain", "gbrain.container", "gbrain.env",
        "localhost/gbrain")
    assert (a.default_data_dir, a.default_port, a.container_port, a.default_repo,
            a.min_release, a.backup_exclude, a.can_adopt) == (
        "~/gbrain-data", 3131, 3131, GH, "v0.60.116.0", (), False)
    assert (a.has_maintenance, a.copy_stopped, a.default_check_days,
            a.default_maintenance_time, a.prepare_summary, a.before_start_error) == (
        True, True, ("mon", "thu"), "03:30", "admin token", "the brain check before start failed")


def test_conf_defaults(tmp_path):
    c = load_conf(Paths(tmp_path, "gbrain"))
    assert (c.data_dir, c.dashboard_port, c.repo, c.min_release, c.check_days,
            c.maintenance_time, c.backup_exclude) == (
        tmp_path / "gbrain-data", 3131, GH, "v0.60.116.0", ("mon", "thu"), "03:30", ())


def test_release_tags_have_four_numbers():
    a = apps.get("gbrain")
    assert a.is_release("v0.60.116.0")
    assert not any(a.is_release(t) for t in ("v0.60.116", "v0.60.116.0-rc1", "latest", "0.60.1.0"))
    assert a.tag_key("v0.60.116.0") == (0, 60, 116, 0)
    assert a.tag_key("v0.60.9.0") < a.tag_key("v0.60.116.0") < a.tag_key("v0.61.1.0")
    with pytest.raises(ValueError, match="not a release tag: 'latest'"):
        a.tag_key("latest")


def test_releases_keep_only_release_tags(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    seen = {}

    def ls_remote(sh, repo):
        seen["args"] = (sh, repo)
        return {"v0.60.116.0": "a", "v0.60.117.0-rc1": "b", "latest": "c", "v0.60.9.0": "d"}
    monkeypatch.setattr(gb, "_ls_remote", ls_remote)
    assert ctx.app.releases(ctx) == {"v0.60.116.0": "a", "v0.60.9.0": "d"}
    assert seen["args"] == (ctx.sh, GH)
    assert ctx.app.published(ctx, ["v1", "v2"]) == {"v1", "v2"}
    assert ctx.app.image_refs(ctx) == ["localhost/gbrain"]


def test_github_repo_and_release_api():
    assert gb.github_repo(GH) == "garrytan/gbrain"
    assert gb.github_repo(GH + ".git") == "garrytan/gbrain"
    assert gb.github_repo(GH + "/") == "garrytan/gbrain"
    assert gb.github_repo("http://127.0.0.1:8092/gbrain") is None
    assert gb.release_api(GH, TAG) == f"https://api.github.com/repos/garrytan/gbrain/releases/tags/{TAG}"
    assert gb.release_api("http://127.0.0.1:8092/gbrain", TAG) == \
        f"http://127.0.0.1:8092/gbrain/api/releases/tags/{TAG}"


def test_base_is_pinned_by_digest():
    assert __import__("re").fullmatch(r"gcr\.io/distroless/cc-debian12@sha256:[0-9a-f]{64}", gb.BASE)


def test_fetch_verifies_the_digest_and_builds(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    rec = ctx.app.fetch(ctx, TAG, "abc")
    assert rec == {"tag": TAG, "id": "sha256:built", "digest": f"sha256:{SUM}",
                   "ref": f"build:{TAG}", "commit": "abc"}
    assert ctx.seen["get"] == [(f"https://api.github.com/repos/garrytan/gbrain/releases/tags/{TAG}", 30.0)]
    assert ctx.seen["download"] == [(f"{GH}/releases/download/{TAG}/gbrain-linux-x64", gb.MAX_BINARY)]
    (build,) = ctx.sh.called("podman", "build")
    assert build[:-1] == ["podman", "build", "-q", "--pull=missing", "--timestamp", "0",
                          "--label", "org.opencontainers.image.revision=abc",
                          "--label", f"org.opencontainers.image.version={TAG}",
                          "-t", f"localhost/gbrain:{TAG}"]
    (cf,) = ctx.seen["containerfile"]
    assert cf == (f"FROM {gb.BASE}\nCOPY --chmod=0755 gbrain /usr/local/bin/gbrain\n"
                  "EXPOSE 3131\nUSER 65532:65532\nENTRYPOINT [\"/usr/local/bin/gbrain\"]\n")
    assert ctx.sh.called("podman", "run") == [["podman", "run", "--rm", "--network=none",
                                               "sha256:built", "--version"]]
    assert not (ctx.paths.staging / f"build-{TAG}").exists()


def test_digest_mismatch_fails_verification(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, files={"gbrain-linux-x64": b"tampered"})
    with pytest.raises(RevisionMismatch, match=rf"{TAG}: gbrain-linux-x64 failed verification \(sha256"):
        ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.sh.called("podman", "build") == []
    assert not (ctx.paths.staging / f"build-{TAG}").exists()


@pytest.mark.parametrize("digest", ["", "md5:abc", "sha256:XYZ"])
def test_a_missing_digest_fails_verification(tmp_path, monkeypatch, digest):
    ctx = gctx(tmp_path, monkeypatch, api=(200, release_json(digest=digest)))
    with pytest.raises(RevisionMismatch, match="publishes no sha256 digest"):
        ctx.app.fetch(ctx, TAG, "abc")


@pytest.mark.parametrize("api,files,msg", [
    ((404, b""), None, "not published yet \\(release API answered 404\\)"),
    ((0, b""), None, "not published yet \\(release API answered nothing\\)"),
    ((200, b"<html>"), None, "answered no JSON"),
    ((200, release_json(name="gbrain-linux-arm64")), None, "release assets of v0.60.116.0 are not published yet"),
    (None, {}, "release assets of v0.60.116.0 are not published yet"),
])
def test_unpublished_or_unreachable_release_is_transient(tmp_path, monkeypatch, api, files, msg):
    ctx = gctx(tmp_path, monkeypatch, api=api, files=files)
    with pytest.raises(Transient, match=msg):
        ctx.app.fetch(ctx, TAG, "abc")


def test_rate_limited_release_api_is_transient(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, api=(403, b'{"message": "API rate limit exceeded"}'))
    with pytest.raises(Transient, match="answered 403"):
        ctx.app.fetch(ctx, TAG, "abc")


def test_oversized_asset_is_permanent(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)

    def big(url, dest, max_bytes):
        raise TooLarge("too big")
    ctx.download = big
    with pytest.raises(RevisionMismatch, match="download limit"):
        ctx.app.fetch(ctx, TAG, "abc")


def test_unsupported_architecture(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.sh.on("uname", "-m", out="aarch64\n")
    with pytest.raises(RevisionMismatch, match="unsupported architecture: aarch64"):
        ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.seen["get"] == []


@pytest.mark.parametrize("out,ok", [
    ("gbrain 0.60.116.0\n", True),
    ("UPGRADE_AVAILABLE 0.60.116.0 0.60.120.0\nRun: gbrain self-upgrade\ngbrain 0.60.116.0\n", True),
    ("gbrain 0.60.115.0\n", False),
    ("", False),
])
def test_the_binary_must_report_the_tag(tmp_path, monkeypatch, out, ok):
    ctx = gctx(tmp_path, monkeypatch, version=out)
    if ok:
        assert ctx.app.fetch(ctx, TAG, "abc")["tag"] == TAG
    else:
        with pytest.raises(RevisionMismatch, match="the binary reports version"):
            ctx.app.fetch(ctx, TAG, "abc")


def test_attestation_runs_when_gh_is_logged_in(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status").on("gh", "attestation", "verify")
    ctx.app.fetch(ctx, TAG, "abc")
    (v,) = ctx.sh.called("gh", "attestation")
    assert v[:3] == ["gh", "attestation", "verify"] and v[4:] == ["-R", "garrytan/gbrain"]
    assert v[3].endswith(f"build-{TAG}/gbrain")
    assert ctx.sh.timeouts[ctx.sh.calls.index(v)] == 300


def test_attestation_failure_is_permanent(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status")
    ctx.sh.on("gh", "attestation", rc=1, err="Verification failed\nno matching attestations\n")
    with pytest.raises(RevisionMismatch, match=r"failed verification \(attestation: no matching attestations\)"):
        ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.sh.called("podman", "build") == []


def test_attestation_skipped_without_a_gh_login(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status", rc=1)
    ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.sh.called("gh", "attestation") == []


def test_attestation_unavailable_reasons(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh=None)
    assert gb.attestation_unavailable(ctx) == "gh is not installed"
    monkeypatch.setattr(gb, "which", lambda t: "/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status", rc=1)
    assert gb.attestation_unavailable(ctx) == "gh is not logged in for this account"
    ctx.sh.on("gh", "auth", "status", rc=0)
    assert gb.attestation_unavailable(ctx) is None
    ctx.conf.repo = "http://127.0.0.1:8092/gbrain"
    assert gb.attestation_unavailable(ctx) == "the repository is not on GitHub"


def test_reacquire_fetches_again(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.app.reacquire(ctx, {"tag": TAG, "id": "sha256:old", "commit": "abc"})
    assert len(ctx.sh.called("podman", "build")) == 1


@pytest.mark.parametrize("answer,reason", [
    ((200, b'{"status":"ok","version":"0.60.116.0","engine":"pglite"}'), None),
    ((503, b'{"status":"unavailable"}'), "/health answered 503"),
    ((0, b""), "/health answered nothing"),
    ((200, b"<html>"), "/health did not answer JSON"),
    ((200, b'{"status":"degraded"}'), "/health reports status degraded"),
    ((200, b"[]"), "/health reports status ?"),
])
def test_health(tmp_path, answer, reason):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    urls = []
    ctx.http_get = lambda url, timeout=5.0: (urls.append((url, timeout)), answer)[1]
    assert ctx.app.health(ctx) == reason
    assert urls == [("http://127.0.0.1:3131/health", 5.0)]
```

Update existing tests: in `tests/test_setup.py` and `tests/test_hubconf.py`, replace every `one of hermes, clawvisor` with `one of hermes, clawvisor, gbrain`.

- [ ] **Step 3: Run them to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_gbrain.py`
Expected: FAIL (`ImportError: cannot import name 'gbrain'`).

- [ ] **Step 4: Implement**

`templates/gbrain.Containerfile` (ends with one newline):

```
FROM ${base}
COPY --chmod=0755 gbrain /usr/local/bin/gbrain
EXPOSE 3131
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/gbrain"]
```

`talaria/apps/__init__.py`:

```python
NAMES = ("hermes", "clawvisor", "gbrain")
```

and in `get`, before the `raise`:

```python
    if name == "gbrain":
        from talaria.apps.gbrain import APP
        return APP
```

`talaria/cli.py`: `s.add_argument("--app", help="hermes (default), clawvisor or gbrain")`.

`talaria/apps/gbrain.py` (replace `<digest from Step 1>` with the 64 hex characters):

```python
"""gbrain (github.com/garrytan/gbrain) as a Talaria app (spec 2026-10-09). No container
image is published: Talaria downloads the release binary, verifies it (the release API's
sha256 digest; `gh attestation verify` when gh is logged in) and builds a local image."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from string import Template

from talaria.apps.base import App
from talaria.ctx import TooLarge
from talaria.images import RevisionMismatch
from talaria.state import ensure_dir
from talaria.upstream import _ls_remote

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)$")
BASE = ("gcr.io/distroless/cc-debian12@sha256:"
        "<digest from Step 1>")
ASSET = "gbrain-linux-x64"
MAX_BINARY = 512 * 1024 * 1024
which = shutil.which


def github_repo(repo: str) -> str | None:
    """"owner/name" of an https://github.com/owner/name URL, else None."""
    m = re.match(r"^https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$", repo)
    return m[1] if m else None


def release_api(repo: str, tag: str) -> str:
    """Where a release's JSON (assets with their sha256 digest) lives: GitHub's REST API,
    or `<repo>/api/releases/tags/<tag>` elsewhere (the e2e fake release's layout)."""
    gh = github_repo(repo)
    if gh:
        return f"https://api.github.com/repos/{gh}/releases/tags/{tag}"
    return f"{repo}/api/releases/tags/{tag}"


def attestation_unavailable(ctx) -> str | None:
    """Why `gh attestation verify` cannot run for this account, or None if it can."""
    if not github_repo(ctx.conf.repo):
        return "the repository is not on GitHub"
    if which("gh") is None:
        return "gh is not installed"
    if ctx.sh.run(["gh", "auth", "status"], check=False, timeout=60).returncode != 0:
        return "gh is not logged in for this account"
    return None


class Gbrain(App):
    name = "gbrain"
    title = "gbrain"
    unit = "gbrain.service"
    container = "gbrain"
    quadlet_file = "gbrain.container"
    env_file = "gbrain.env"
    local_image = "localhost/gbrain"
    default_data_dir = "~/gbrain-data"
    default_port = 3131
    container_port = 3131
    default_image = ""
    default_repo = "https://github.com/garrytan/gbrain"
    min_release = "v0.60.116.0"
    backup_exclude = ()
    can_adopt = False
    prepare_summary = "admin token"
    before_start_error = "the brain check before start failed"
    copy_stopped = True               # PGLite's files are consistent only at rest (spike §8)
    has_maintenance = True            # gbrain dream
    default_check_days = ("mon", "thu")   # several releases a day (spec §5.1)
    default_maintenance_time = "03:30"

    def is_release(self, tag: str) -> bool:
        return bool(TAG.match(tag))

    def tag_key(self, tag: str) -> tuple:
        m = TAG.match(tag)
        if not m:
            raise ValueError(f"not a release tag: {tag!r}")
        return tuple(int(x) for x in m.groups())

    def releases(self, ctx) -> dict[str, str]:
        return {t: c for t, c in _ls_remote(ctx.sh, ctx.conf.repo).items()
                if self.is_release(t)}

    def published(self, ctx, tags) -> set[str]:
        return set(tags)          # assets are checked in fetch(); missing ones are Transient

    def image_refs(self, ctx) -> list[str]:
        return [self.local_image]

    def _digest(self, ctx, tag: str) -> str:
        """The sha256 hex the release publishes for ASSET."""
        from talaria.rehearse import Transient
        code, body = ctx.http_get(release_api(ctx.conf.repo, tag), 30.0)
        if code != 200:
            raise Transient(f"the release {tag} is not published yet (release API answered "
                            f"{code or 'nothing'})")
        try:
            rel = json.loads(body)
        except ValueError:
            raise Transient(f"the release API answered no JSON for {tag}") from None
        assets = rel.get("assets") if isinstance(rel, dict) else None
        asset = next((a for a in assets or [] if isinstance(a, dict) and a.get("name") == ASSET),
                     None)
        if asset is None:
            raise Transient(f"release assets of {tag} are not published yet")
        digest = str(asset.get("digest") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise RevisionMismatch(f"{tag}: {ASSET} failed verification (the release publishes "
                                   "no sha256 digest)")
        return digest[len("sha256:"):]

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        from talaria.rehearse import Transient
        machine = ctx.sh.run(["uname", "-m"]).stdout.strip()
        if machine != "x86_64":
            raise RevisionMismatch(f"unsupported architecture: {machine}")
        want = self._digest(ctx, tag)
        work = ctx.paths.staging / f"build-{tag}"
        shutil.rmtree(work, ignore_errors=True)
        ensure_dir(ctx.paths.staging)
        work.mkdir(mode=0o700)
        try:
            try:
                code = ctx.download(f"{ctx.conf.repo}/releases/download/{tag}/{ASSET}",
                                    work / "gbrain", MAX_BINARY)
            except TooLarge as e:
                raise RevisionMismatch(f"{tag}: {ASSET} exceeds the {MAX_BINARY}-byte download "
                                       f"limit ({e})") from None
            if code != 200:
                raise Transient(f"release assets of {tag} are not published yet")
            got = hashlib.sha256((work / "gbrain").read_bytes()).hexdigest()
            if got != want:
                raise RevisionMismatch(f"{tag}: {ASSET} failed verification (sha256 {got[:12]}… "
                                       f"is not the published {want[:12]}…)")
            if attestation_unavailable(ctx) is None:
                r = ctx.sh.run(["gh", "attestation", "verify", str(work / "gbrain"), "-R",
                                github_repo(ctx.conf.repo)], check=False, timeout=300)
                if r.returncode != 0:
                    lines = (r.stderr or r.stdout).strip().splitlines()
                    raise RevisionMismatch(f"{tag}: {ASSET} failed verification (attestation: "
                                           f"{lines[-1] if lines else f'exit {r.returncode}'})")
            (work / "Containerfile").write_text(Template(
                (ctx.paths.templates_dir / "gbrain.Containerfile").read_text()
            ).substitute(base=BASE))
            iid = ctx.sh.run(["podman", "build", "-q", "--pull=missing", "--timestamp", "0",
                              "--label", f"org.opencontainers.image.revision={commit}",
                              "--label", f"org.opencontainers.image.version={tag}",
                              "-t", f"{self.local_image}:{tag}", str(work)],
                             timeout=1800).stdout.strip().splitlines()[-1]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out = ctx.sh.run(["podman", "run", "--rm", "--network=none", iid, "--version"],
                         timeout=120).stdout
        m = re.search(r"\bgbrain (\d+(?:\.\d+){3})\b", out)
        if not m or m[1] != tag[1:]:
            raise RevisionMismatch(f"{tag}: the binary reports version {m[1] if m else '?'}")
        return {"tag": tag, "id": iid, "digest": f"sha256:{got}", "ref": f"build:{tag}",
                "commit": commit}

    def reacquire(self, ctx, rec: dict) -> None:
        self.fetch(ctx, rec["tag"], rec.get("commit") or "")

    def health(self, ctx) -> str | None:
        code, body = ctx.http_get(f"http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port}/health",
                                  5.0)
        if code != 200:
            return f"/health answered {code or 'nothing'}"
        try:
            r = json.loads(body)
        except ValueError:
            return "/health did not answer JSON"
        status = r.get("status") if isinstance(r, dict) else "?"
        return None if status == "ok" else f"/health reports status {status}"


APP = Gbrain()
```

- [ ] **Step 5: Run the full suite**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add talaria/apps/__init__.py talaria/apps/gbrain.py talaria/cli.py templates/gbrain.Containerfile \
        tests/test_gbrain.py tests/test_setup.py tests/test_hubconf.py
git commit -m "gbrain adapter: releases, digest- and attestation-verified local image, health"
```

---

### Task 6: gbrain adapter, part 2: Quadlet, prepare, initialize, rehearsal, before_start, data version, dream

**Files:**
- Create: `templates/gbrain.container`
- Modify: `talaria/apps/gbrain.py`, `tests/test_gbrain.py` (append)

**Interfaces:**
- Consumes (Task 5): `talaria.apps.gbrain` module (`ASSET`, `github_repo`, `attestation_unavailable`, `Gbrain`, `APP`, `which`). Consumes (Task 1/2): `App.initialize`, `App.setup_notes`, `App.maintenance` contracts; setup prints initialize lines as `OK:` and turns `ValueError` into `STOP:`. Existing: `talaria.conf.parse_kv`, `write_env_value`; `units.render_quadlet(ctx)` substitutes `data_dir, app_env, publish_ports, marker, wait_addr, host_net, add_hosts, public_env, title` and `**app.quadlet_vars(ctx)`; `talaria.rehearse.Permanent(msg, details)`; `deploy` calls `before_start(ctx, pending) -> (reason | None, details)` with `pending = {"tag", "image": {"id", …}, "report", **pending_extra}`.
- Produces (`talaria/apps/gbrain.py`):
  - Constants: `UNS = "--userns=keep-id:uid=65532,gid=65532"`, `ONEOFF = "talaria-gbrain-oneoff"`, `MARKER = "talaria rehearsal marker"`, `MARKER_TEXT` (starts with `MARKER`), `SCHEMA_FILE = ".talaria-schema"`, `INIT_FILE = ".talaria-init"`, `TOKEN_KEY = "GBRAIN_ADMIN_BOOTSTRAP_TOKEN"`, `NEEDS_URL` (STOP text).
  - `oneoff(ctx, image: str, data, args: list[str], *, network: bool = False, env: bool = False, timeout: float = 300) -> Result`; `tail(r, n=20) -> str`; `last_line(r) -> str`; `parse_schema(out: str) -> int` (raises `ValueError`); `write_schema(data_dir, v: int) -> None`; `_redact(ctx, text) -> str`.
  - Methods: `quadlet_vars`, `prepare`, `setup_notes`, `initialize`, `data_version` (static), `rehearse -> {"tag", "digest", "before": int | None, "after": int}`, `report_lines`, `pending_extra -> {"schema_after": int}`, `before_start`, `maintenance`, `ready_text`, `initial_conf`.

- [ ] **Step 0: Check the real CLI shapes (when network and podman are available)**

The plan assumes these shapes:
- `gbrain doctor --json` prints a JSON object with `checks: [{name, status, message}]`, where `schema_version`'s message is `Version N (latest: N)`.
- `gbrain remember "<text>"` and `gbrain recall --query "<text>"` work keylessly.

Check them on a scratch dir with the image built by Task 5's `fetch`:

```bash
podman run --rm --read-only --userns=keep-id:uid=65532,gid=65532 --network=none -v /tmp/gb:/data:Z -e HOME=/data -e GBRAIN_HOME=/data localhost/gbrain:<tag> init --pglite
podman run … doctor --json | head -c 2000
```

If the shapes differ, adapt `parse_schema`, the `remember`/`recall` argv and the test fixtures `doctor()` below, keeping each function's contract. Note the difference in the commit message. The contract test (Task 7) pins the real shape.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_gbrain.py`)

```python
import os
import subprocess

from talaria import rehearse, setup, units
from talaria.conf import parse_kv


def doctor(v, status="ok"):
    msg = (f"Version {v} (latest: {v})" if status == "ok"
           else f"Version {v} is AHEAD of this client's latest known version (219).")
    return json.dumps({"status": status, "checks": [
        {"name": "embeddings", "status": "warn", "message": "disabled"},
        {"name": "schema_version", "status": status, "message": msg}]}) + "\n"


def split_run(argv):
    """(image, gbrain args) of a oneoff `podman run`."""
    i = argv.index("GBRAIN_HOME=/data") + 1
    if argv[i] == "--env-file":
        i += 2
    return argv[i], argv[i + 1:]


URL = "https://brain.example.ts.net:8443"


def brain(tmp_path, answers=None):
    ctx = make_test_ctx(tmp_path, app="gbrain", dashboard_public_url=URL)
    runs = []
    answers = {"doctor": Result(0, doctor(221)), "recall": Result(0, f"{gb.MARKER_TEXT}\n"),
               **(answers or {})}

    def fn(argv, input):
        image, args = split_run(argv)
        runs.append((image, args, argv))
        a = answers.get(args[0], Result(0, ""))
        return a(argv) if callable(a) else a
    ctx.sh.on("podman", "rm")
    ctx.sh.on("podman", "run", fn=fn)
    ctx.runs = runs
    return ctx


def raises(exc):
    def f(argv):
        raise exc
    return f


# --- parse_schema, oneoff -------------------------------------------------------

def test_parse_schema_reads_the_schema_check():
    assert gb.parse_schema(doctor(221)) == 221
    assert gb.parse_schema("UPGRADE_AVAILABLE 1 2\n" + doctor(7)) == 7


@pytest.mark.parametrize("out,msg", [
    (doctor(221, "warn"), "schema_version is warn: Version 221 is AHEAD"),
    ("", "doctor printed no JSON"),
    ("{nope}", "doctor printed no valid JSON"),
    (json.dumps({"checks": []}), "doctor reports no schema_version check"),
    (json.dumps({"checks": [{"name": "schema_version", "status": "ok", "message": "fine"}]}),
     "schema_version names no version"),
    ("[1]", "doctor reports no schema_version check"),
])
def test_parse_schema_refuses(out, msg):
    with pytest.raises(ValueError, match=msg):
        gb.parse_schema(out)


def test_oneoff_is_offline_without_keys_by_default(tmp_path):
    ctx = brain(tmp_path)
    gb.oneoff(ctx, "img", tmp_path / "d", ["doctor", "--json"])
    rm = ["podman", "rm", "-f", "talaria-gbrain-oneoff"]
    assert ctx.sh.calls == [rm, ["podman", "run", "--rm", "--name", "talaria-gbrain-oneoff",
                                 "--read-only", "--userns=keep-id:uid=65532,gid=65532",
                                 "--network=none", "-v", f"{tmp_path / 'd'}:/data:Z",
                                 "-e", "HOME=/data", "-e", "GBRAIN_HOME=/data", "img",
                                 "doctor", "--json"], rm]
    assert ctx.sh.timeouts == [120, 300, 120]


def test_oneoff_with_network_and_env(tmp_path):
    ctx = brain(tmp_path)
    gb.oneoff(ctx, "img", tmp_path, ["dream"], network=True, env=True, timeout=3600)
    run = ctx.runs[0][2]
    assert "--network=none" not in run
    assert run[run.index("--env-file") + 1] == str(ctx.paths.app_env)
    assert ctx.sh.timeouts[1] == 3600


def test_oneoff_removes_its_container_after_a_timeout(tmp_path):
    ctx = brain(tmp_path, {"dream": raises(subprocess.TimeoutExpired(["podman"], 3600))})
    with pytest.raises(subprocess.TimeoutExpired):
        gb.oneoff(ctx, "img", tmp_path, ["dream"])
    assert ctx.sh.calls[-1] == ["podman", "rm", "-f", "talaria-gbrain-oneoff"]


# --- Quadlet ---------------------------------------------------------------------

def test_quadlet(tmp_path):
    ctx = make_test_ctx(tmp_path, app="gbrain", dashboard_public_url=URL)
    q = units.render_quadlet(ctx)
    assert (f"\nExec=serve --http --bind 0.0.0.0 --port 3131 --public-url {URL} "
            "--enable-dcr --fail-fast --surface full\n") in q
    for line in ("ContainerName=gbrain", "Image=localhost/gbrain:current", "Pull=never",
                 "ReadOnly=true", "UserNS=keep-id:uid=65532,gid=65532",
                 f"Volume={ctx.conf.data_dir}:/data:Z", "Environment=HOME=/data GBRAIN_HOME=/data",
                 f"EnvironmentFile={ctx.paths.app_env}", "PublishPort=127.0.0.1:3131:3131",
                 f"ExecCondition=/bin/sh -c 'test ! -e \"{ctx.paths.marker}\"'"):
        assert f"\n{line}\n" in q, line
    assert q.startswith("# Managed by Talaria.") and "PUBLIC_URL" not in q and "${" not in q


def test_quadlet_needs_the_public_url(tmp_path):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    with pytest.raises(ValueError, match="gbrain needs dashboard.public_url"):
        units.render_quadlet(ctx)


# --- prepare, notes, initial conf, ready text --------------------------------------

def test_prepare_generates_the_admin_token_once(tmp_path):
    ctx = brain(tmp_path)
    lines = ctx.app.prepare(ctx)
    token = parse_kv(ctx.paths.app_env.read_text())["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"]
    assert len(token) >= 40 and token not in "".join(lines)
    assert lines == [f"admin token generated in {ctx.paths.app_env} (GBRAIN_ADMIN_BOOTSTRAP_TOKEN)"]
    assert (ctx.paths.app_env.stat().st_mode & 0o777) == 0o600
    assert (ctx.conf.data_dir.stat().st_mode & 0o777) == 0o700
    assert ctx.app.prepare(ctx) == []
    assert parse_kv(ctx.paths.app_env.read_text())["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"] == token


def test_prepare_keeps_provider_keys(tmp_path):
    ctx = brain(tmp_path)
    ctx.paths.app_env.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.app_env.write_text("OPENAI_API_KEY=sk-x\n")
    ctx.app.prepare(ctx)
    env = parse_kv(ctx.paths.app_env.read_text())
    assert env["OPENAI_API_KEY"] == "sk-x" and env["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"]


def test_prepare_stops_without_the_public_url(tmp_path):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    with pytest.raises(ValueError, match="gbrain needs dashboard.public_url"):
        ctx.app.prepare(ctx)
    assert not ctx.paths.app_env.exists()


def test_setup_notes(tmp_path, monkeypatch):
    ctx = brain(tmp_path)
    monkeypatch.setattr(gb, "which", lambda t: None)
    assert ctx.app.setup_notes(ctx) == [
        "gbrain downloads are checked against the release's sha256 digest only (gh is not "
        "installed); install gh and run `gh auth login` as this account to also verify their "
        "build provenance"]
    ctx.conf.repo = "http://127.0.0.1:8092/gbrain"
    assert ctx.app.setup_notes(ctx) == []
    ctx.conf.repo = GH
    monkeypatch.setattr(gb, "which", lambda t: "/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status")
    assert ctx.app.setup_notes(ctx) == []


def test_initial_conf_and_ready_text(tmp_path):
    ctx = brain(tmp_path)
    assert ctx.app.initial_conf(ctx) == (
        "# Talaria settings; see README.\napp = gbrain\ndata_dir = ~/gbrain-data\n"
        "# required: the https address MCP connectors use, e.g.\n"
        "# dashboard.public_url = https://<host>.<tailnet>.ts.net:8443\n")
    assert ctx.app.ready_text(ctx) == (
        f"gbrain is running at http://127.0.0.1:3131 (admin UI /admin; its token is "
        f"GBRAIN_ADMIN_BOOTSTRAP_TOKEN in {ctx.paths.app_env}). MCP connectors use {URL}/mcp "
        "once you publish it (README: gbrain)")


# --- initialize, data_version ------------------------------------------------------

def test_initialize_a_fresh_brain(tmp_path):
    ctx = brain(tmp_path)
    data = ctx.conf.data_dir
    assert ctx.app.initialize(ctx) == [f"gbrain brain initialized in {data} (PGLite, schema 221)"]
    assert [args for _, args, _ in ctx.runs] == [
        ["init", "--pglite"], ["config", "set", "self_upgrade.mode", "off"],
        ["remember", gb.MARKER_TEXT], ["doctor", "--json"]]
    assert {image for image, _, _ in ctx.runs} == {"localhost/gbrain:current"}
    assert all("--network=none" in argv and "--env-file" not in argv for _, _, argv in ctx.runs)
    assert (data / ".talaria-schema").read_text() == "221\n"
    assert (data / ".talaria-init").exists()
    assert gb.MARKER_TEXT.startswith(gb.MARKER)
    ctx.runs.clear()
    assert ctx.app.initialize(ctx) == [] and ctx.runs == []


def test_initialize_skips_init_for_an_existing_brain(tmp_path):
    ctx = brain(tmp_path)
    (ctx.conf.data_dir / ".gbrain").mkdir()
    (ctx.conf.data_dir / ".gbrain/config.json").write_text("{}")
    ctx.app.initialize(ctx)
    assert [args[0] for _, args, _ in ctx.runs] == ["config", "remember", "doctor"]


def test_initialize_failure_stops_and_retries_next_time(tmp_path):
    ctx = brain(tmp_path, {"init": Result(1, "", "Error: cannot create /data/.gbrain\n")})
    with pytest.raises(ValueError, match="gbrain init failed: Error: cannot create /data/.gbrain"):
        ctx.app.initialize(ctx)
    assert not (ctx.conf.data_dir / ".talaria-init").exists()
    ctx = brain(tmp_path, {"doctor": Result(0, doctor(221, "warn"))})
    with pytest.raises(ValueError, match="gbrain doctor failed after init: schema_version is warn"):
        ctx.app.initialize(ctx)
    assert not (ctx.conf.data_dir / ".talaria-init").exists()


def test_data_version(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    assert gb.APP.data_version(d) is None
    (d / ".talaria-schema").write_text("221\n")
    assert gb.APP.data_version(d) == 221
    (d / ".talaria-schema").write_text("x\n")
    assert gb.APP.data_version(d) is None
    (d / ".talaria-schema").unlink()
    (tmp_path / "elsewhere").write_text("5\n")
    os.symlink(tmp_path / "elsewhere", d / ".talaria-schema")
    assert gb.APP.data_version(d) is None


# --- rehearsal ---------------------------------------------------------------------

IMG = {"tag": "v0.60.117.0", "id": "sha256:new", "digest": "sha256:dd"}


def copy_dir(tmp_path, schema="219\n"):
    c = tmp_path / "copy"
    c.mkdir()
    if schema:
        (c / ".talaria-schema").write_text(schema)
    return c


def test_rehearsal_reports_the_schema_change(tmp_path):
    ctx = brain(tmp_path)
    c = copy_dir(tmp_path)
    report = ctx.app.rehearse(ctx, {}, IMG, c, tmp_path)
    assert report == {"tag": "v0.60.117.0", "digest": "sha256:dd", "before": 219, "after": 221}
    assert [(image, args) for image, args, _ in ctx.runs] == [
        ("sha256:new", ["doctor", "--json"]), ("sha256:new", ["recall", "--query", gb.MARKER])]
    assert ctx.app.report_lines(ctx, report) == (["Brain schema: 219 → 221"], [])
    assert ctx.app.pending_extra(report) == {"schema_after": 221}


def test_rehearsal_runs_offline_without_the_env_file(tmp_path):
    ctx = brain(tmp_path)
    ctx.paths.app_env.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.app_env.write_text("OPENAI_API_KEY=sk-secret\n")
    ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path), tmp_path)
    for _, _, argv in ctx.runs:
        assert "--network=none" in argv and "--env-file" not in argv
        assert f"{tmp_path / 'copy'}:/data:Z" in argv


def test_rehearsal_without_a_schema_file_says_unknown(tmp_path):
    ctx = brain(tmp_path)
    report = ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path, schema=None), tmp_path)
    assert report["before"] is None
    assert ctx.app.report_lines(ctx, report) == (["Brain schema: unknown → 221"], [])


def test_rehearsal_doctor_not_ok_is_permanent(tmp_path):
    ctx = brain(tmp_path, {"doctor": Result(0, doctor(221, "warn"), "")})
    with pytest.raises(rehearse.Permanent, match="gbrain doctor on the copy is not ok: schema_version is warn") as e:
        ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path), tmp_path)
    assert e.value.details[0][0] == "gbrain doctor (last lines)"


@pytest.mark.parametrize("answer", [Result(0, "nothing found\n"), Result(1, "", "pglite_busy\n")])
def test_rehearsal_needs_the_marker_recalled(tmp_path, answer):
    ctx = brain(tmp_path, {"recall": answer})
    with pytest.raises(rehearse.Permanent, match="could not recall Talaria's marker page on the copy") as e:
        ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path), tmp_path)
    assert e.value.details[0][0] == "gbrain recall (last lines)"


def test_rehearsal_timeout_is_permanent(tmp_path):
    ctx = brain(tmp_path, {"doctor": raises(subprocess.TimeoutExpired(["podman"], 300))})
    with pytest.raises(rehearse.Permanent, match="did not finish its checks on the copy within 300 s"):
        ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path), tmp_path)


# --- deploy: before_start ------------------------------------------------------------

PENDING = {"tag": "v0.60.117.0", "image": {"id": "sha256:new"}, "schema_after": 221}


def test_before_start_reads_the_schema_with_the_new_image_on_stopped_data(tmp_path):
    ctx = brain(tmp_path)
    assert ctx.app.before_start(ctx, PENDING) == (None, [])
    ((image, args, argv),) = ctx.runs
    assert (image, args) == ("sha256:new", ["doctor", "--json"])
    assert f"{ctx.conf.data_dir}:/data:Z" in argv and "--network=none" in argv
    assert ctx.app.data_version(ctx.conf.data_dir) == 221


def test_before_start_schema_mismatch_fails_the_deploy(tmp_path):
    ctx = brain(tmp_path, {"doctor": Result(0, doctor(222))})
    assert ctx.app.before_start(ctx, PENDING) == (
        "the brain schema is 222, the rehearsal expected 221", [])


def test_before_start_doctor_not_ok(tmp_path):
    ctx = brain(tmp_path, {"doctor": Result(0, "", "Error [pglite_busy]\n")})
    reason, details = ctx.app.before_start(ctx, PENDING)
    assert reason == "gbrain doctor is not ok: doctor printed no JSON"
    assert details == [("gbrain doctor (last lines)", "Error [pglite_busy]")]


# --- maintenance (dream) --------------------------------------------------------------

def test_maintenance_runs_dream_with_network_and_keys(tmp_path):
    ctx = brain(tmp_path)
    assert ctx.app.maintenance(ctx) is None
    ((image, args, argv),) = ctx.runs
    assert (image, args) == ("localhost/gbrain:current", ["dream"])
    assert "--network=none" not in argv and "--env-file" in argv
    assert f"{ctx.conf.data_dir}:/data:Z" in argv
    assert ctx.sh.timeouts[ctx.sh.calls.index(argv)] == 3600


def test_maintenance_failure_line_redacts_env_values(tmp_path):
    ctx = brain(tmp_path, {"dream": Result(1, "phase embed\n",
                                           "401 Unauthorized for key sk-secret123\n")})
    ctx.paths.app_env.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.app_env.write_text("OPENAI_API_KEY=sk-secret123\nFAKE=1\n")
    assert ctx.app.maintenance(ctx) == "401 Unauthorized for key ***"


def test_maintenance_failure_without_output(tmp_path):
    ctx = brain(tmp_path, {"dream": Result(3, "", "")})
    assert ctx.app.maintenance(ctx) == "exit 3"


# --- setup, end to end with fakes ------------------------------------------------------

def gsetup(tmp_path, monkeypatch):
    from tests.test_setup import args
    home = tmp_path / "home"
    p = Paths(home, "gbrain")
    p.conf_dir.mkdir(parents=True)
    p.conf_file.write_text(f"app = gbrain\ndashboard.public_url = {URL}\n")
    ctx = brain(tmp_path)
    ctx.sh.on("systemctl").on("systemctl", "--user", "is-active", out="inactive\n")
    ctx.sh.on("podman", "tag")
    monkeypatch.setattr(setup, "which", lambda t: None)
    monkeypatch.setattr(gb, "which", lambda t: None)
    monkeypatch.setattr(ctx.app, "releases", lambda c: {TAG: "c"})
    monkeypatch.setattr(ctx.app, "published", lambda c, tags: {TAG})
    monkeypatch.setattr(ctx.app, "fetch", lambda c, t, commit: {
        "tag": t, "id": "sha256:n", "ref": f"build:{t}", "digest": "d", "commit": commit})
    monkeypatch.setattr(setup.service, "post_start_check", lambda c: None)
    return ctx, home, args


def test_setup_fresh_install_initializes_the_brain(tmp_path, monkeypatch, capsys):
    ctx, home, args = gsetup(tmp_path, monkeypatch)
    rc = setup.service_phase(ctx, args(as_service=True))
    out = capsys.readouterr().out.replace(str(home), "~H")
    token = parse_kv(ctx.paths.app_env.read_text())["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"]
    assert rc == 0 and token not in out
    assert out == (
        "OK: no existing gbrain found: fresh install\n"
        "OK: admin token generated in ~H/.config/talaria/gbrain.env (GBRAIN_ADMIN_BOOTSTRAP_TOKEN)\n"
        "NOTE: gbrain downloads are checked against the release's sha256 digest only (gh is "
        "not installed); install gh and run `gh auth login` as this account to also verify "
        "their build provenance\n"
        "OK: gbrain v0.60.116.0 pulled and verified\n"
        "OK: gbrain brain initialized in ~H/gbrain-data (PGLite, schema 221)\n"
        "OK: gbrain is running at http://127.0.0.1:3131 (admin UI /admin; its token is "
        "GBRAIN_ADMIN_BOOTSTRAP_TOKEN in ~H/.config/talaria/gbrain.env). MCP connectors use "
        f"{URL}/mcp once you publish it (README: gbrain)\n")
    assert ctx.paths.quadlet.exists()


def test_setup_stops_without_the_public_url(tmp_path, capsys):
    from tests.test_setup import args
    ctx = make_test_ctx(tmp_path, app="gbrain")
    rc = setup.service_phase(ctx, args(as_service=True))
    assert rc == 1
    assert capsys.readouterr().out == ("OK: no existing gbrain found: fresh install\n"
                                       f"STOP: {gb.NEEDS_URL}\n")
    assert ctx.paths.conf_file.read_text().startswith("# Talaria settings; see README.\napp = gbrain\n")
    assert ctx.sh.calls == []


def test_setup_plan_names_the_admin_token(tmp_path, capsys):
    from tests.test_setup import args
    ctx = make_test_ctx(tmp_path, app="gbrain")
    assert setup.service_phase(ctx, args(as_service=True, plan=True)) == 0
    assert capsys.readouterr().out == ("OK: no existing gbrain found: fresh install\n"
                                       "PLAN: admin token, install units, start gbrain, verify\n")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_gbrain.py`
Expected: FAIL (`AttributeError: module 'talaria.apps.gbrain' has no attribute 'MARKER_TEXT'`, then `NotImplementedError`).

- [ ] **Step 3: Implement**

`templates/gbrain.container` (ends with one newline):

```
# Managed by Talaria. Re-run `talaria setup` after changing talaria.conf.
[Unit]
Description=gbrain (managed by Talaria)
Wants=network-online.target
After=network-online.target

[Container]
ContainerName=gbrain
Image=localhost/gbrain:current
Pull=never
ReadOnly=true
UserNS=keep-id:uid=65532,gid=65532
Volume=${data_dir}:/data:Z
Environment=HOME=/data GBRAIN_HOME=/data
EnvironmentFile=${app_env}
Exec=serve --http --bind 0.0.0.0 --port ${container_port} --public-url ${public_url} --enable-dcr --fail-fast --surface full
${host_net}${publish_ports}${add_hosts}
[Service]
ExecCondition=/bin/sh -c 'test ! -e "${marker}"'
${wait_addr}
Restart=on-failure
RestartSec=30
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

`talaria/apps/gbrain.py`: add imports

```python
import os
import secrets as _secrets
import subprocess
from pathlib import Path

from talaria.conf import parse_kv, write_env_value
```

add after `MAX_BINARY`:

```python
UNS = "--userns=keep-id:uid=65532,gid=65532"
ONEOFF = "talaria-gbrain-oneoff"
MARKER = "talaria rehearsal marker"
MARKER_TEXT = (f"{MARKER}: Talaria recalls this page to check that a new gbrain release "
               "can read the brain.")
SCHEMA_FILE = ".talaria-schema"
INIT_FILE = ".talaria-init"
TOKEN_KEY = "GBRAIN_ADMIN_BOOTSTRAP_TOKEN"
NEEDS_URL = ("gbrain needs dashboard.public_url in talaria.conf: the https address MCP "
             "connectors reach it at, e.g. https://<host>.<tailnet>.ts.net:8443 (README: gbrain)")
```

add module functions after `attestation_unavailable`:

```python
def oneoff(ctx, image: str, data, args: list[str], *, network: bool = False,
           env: bool = False, timeout: float = 300):
    """`gbrain <args>` once, in a throwaway container on `data`. Offline unless `network`;
    the app env file (provider keys) only with `env`. Never raises on a non-zero exit."""
    ctx.sh.run(["podman", "rm", "-f", ONEOFF], check=False, timeout=120)
    try:
        return ctx.sh.run(["podman", "run", "--rm", "--name", ONEOFF, "--read-only", UNS,
                           *([] if network else ["--network=none"]),
                           "-v", f"{data}:/data:Z", "-e", "HOME=/data", "-e", "GBRAIN_HOME=/data",
                           *(["--env-file", str(ctx.paths.app_env)] if env else []),
                           image, *args], check=False, timeout=timeout)
    finally:
        ctx.sh.run(["podman", "rm", "-f", ONEOFF], check=False, timeout=120)


def tail(r, n: int = 20) -> str:
    return "\n".join((r.stdout + r.stderr).strip().splitlines()[-n:])[-2000:]


def last_line(r) -> str:
    lines = [l.strip() for l in (r.stderr or r.stdout or "").splitlines() if l.strip()]
    return lines[-1][:300] if lines else f"exit {r.returncode}"


def parse_schema(out: str) -> int:
    """The brain's schema version from `gbrain doctor --json`; ValueError (the reason)
    unless the schema_version check is ok. Other checks (keyless warnings) do not count."""
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end < start:
        raise ValueError("doctor printed no JSON")
    try:
        doc = json.loads(out[start:end + 1])
    except ValueError:
        raise ValueError("doctor printed no valid JSON") from None
    checks = doc.get("checks") if isinstance(doc, dict) else None
    for c in checks if isinstance(checks, list) else []:
        if isinstance(c, dict) and c.get("name") == "schema_version":
            if c.get("status") != "ok":
                raise ValueError(f"schema_version is {c.get('status')}: {c.get('message')}")
            m = re.search(r"Version (\d+)", str(c.get("message") or ""))
            if not m:
                raise ValueError(f"schema_version names no version: {c.get('message')}")
            return int(m[1])
    raise ValueError("doctor reports no schema_version check")


def write_schema(data_dir, v: int) -> None:
    (Path(data_dir) / SCHEMA_FILE).write_text(f"{v}\n")


def _redact(ctx, text: str) -> str:
    """Values from the app env file (provider keys, the admin token) never reach a message."""
    try:
        env = parse_kv(ctx.paths.app_env.read_text())
    except (OSError, UnicodeDecodeError):
        env = {}
    for v in sorted((v for v in env.values() if len(v) >= 4), key=len, reverse=True):
        text = text.replace(v, "***")
    return text
```

add methods to `Gbrain` (after `health`):

```python
    def quadlet_vars(self, ctx) -> dict:
        if not ctx.conf.dashboard_public_url:
            raise ValueError(NEEDS_URL)
        return {"public_url": ctx.conf.dashboard_public_url,
                "container_port": self.container_port}

    def prepare(self, ctx) -> list[str]:
        if not ctx.conf.dashboard_public_url:
            raise ValueError(NEEDS_URL)
        data = ctx.conf.data_dir
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(data, 0o700)              # some PGLite files are 0644 (spike §2)
        env = parse_kv(ctx.paths.app_env.read_text()) if ctx.paths.app_env.exists() else {}
        if env.get(TOKEN_KEY):
            return []
        write_env_value(ctx.paths.app_env, TOKEN_KEY, _secrets.token_urlsafe(32))
        return [f"admin token generated in {ctx.paths.app_env} ({TOKEN_KEY})"]

    def setup_notes(self, ctx) -> list[str]:
        why = attestation_unavailable(ctx)
        if why is None or not github_repo(ctx.conf.repo):
            return []
        return [f"gbrain downloads are checked against the release's sha256 digest only "
                f"({why}); install gh and run `gh auth login` as this account to also verify "
                "their build provenance"]

    def initialize(self, ctx) -> list[str]:
        """First run: a keyless PGLite brain, gbrain's own update checks off, Talaria's
        marker page for the rehearsal's recall, the schema version."""
        data = ctx.conf.data_dir
        if (data / INIT_FILE).exists():
            return []
        image = f"{self.local_image}:current"
        steps = [] if (data / ".gbrain/config.json").exists() else [["init", "--pglite"]]
        steps += [["config", "set", "self_upgrade.mode", "off"], ["remember", MARKER_TEXT]]
        for args in steps:
            r = oneoff(ctx, image, data, args)
            if r.returncode != 0:
                raise ValueError(f"gbrain {args[0]} failed: {last_line(r)}")
        try:
            v = parse_schema(oneoff(ctx, image, data, ["doctor", "--json"]).stdout)
        except ValueError as e:
            raise ValueError(f"gbrain doctor failed after init: {e}") from None
        write_schema(data, v)
        (data / INIT_FILE).write_text("1\n")
        return [f"gbrain brain initialized in {data} (PGLite, schema {v})"]

    @staticmethod
    def data_version(data_dir):
        f = Path(data_dir) / SCHEMA_FILE
        if f.is_symlink() or not f.is_file():
            return None
        try:
            return int(f.read_text().strip())
        except (OSError, ValueError, UnicodeDecodeError):
            return None

    def rehearse(self, ctx, st, image, copy, stage) -> dict:
        """Offline, without the env file: doctor (applies the migrations, reports the
        schema) and a recall of the marker page, both on the copy."""
        from talaria.rehearse import Permanent
        tag = image["tag"]
        before = self.data_version(copy)
        try:
            doc = oneoff(ctx, image["id"], copy, ["doctor", "--json"])
            try:
                after = parse_schema(doc.stdout)
            except ValueError as e:
                raise Permanent(f"gbrain doctor on the copy is not ok: {e}",
                                [("gbrain doctor (last lines)", tail(doc))]) from None
            rec = oneoff(ctx, image["id"], copy, ["recall", "--query", MARKER])
        except subprocess.TimeoutExpired as e:
            raise Permanent(f"{tag} did not finish its checks on the copy within "
                            f"{e.timeout:.0f} s") from None
        if rec.returncode != 0 or MARKER not in rec.stdout.lower():
            raise Permanent(f"{tag} could not recall Talaria's marker page on the copy",
                            [("gbrain recall (last lines)", tail(rec))])
        return {"tag": tag, "digest": image.get("digest"), "before": before, "after": after}

    def report_lines(self, ctx, report: dict) -> tuple[list[str], list]:
        before = report["before"]
        return [f"Brain schema: {before if before is not None else 'unknown'} → "
                f"{report['after']}"], []

    def pending_extra(self, report: dict) -> dict:
        return {"schema_after": report["after"]}

    def before_start(self, ctx, pending: dict) -> tuple[str | None, list]:
        """The new release opens (and migrates) the stopped production brain once, offline,
        so its schema can be compared with the rehearsal's; the CLI cannot open the brain
        while the server holds it (spike §8)."""
        r = oneoff(ctx, pending["image"]["id"], ctx.conf.data_dir, ["doctor", "--json"])
        try:
            v = parse_schema(r.stdout)
        except ValueError as e:
            return f"gbrain doctor is not ok: {e}", [("gbrain doctor (last lines)", tail(r))]
        write_schema(ctx.conf.data_dir, v)
        want = pending.get("schema_after")
        if want is not None and v != want:
            return f"the brain schema is {v}, the rehearsal expected {want}", []
        return None, []

    def maintenance(self, ctx) -> str | None:
        """`gbrain dream`: one maintenance cycle, with network and the provider keys."""
        r = oneoff(ctx, f"{self.local_image}:current", ctx.conf.data_dir, ["dream"],
                   network=True, env=True, timeout=3600)
        return None if r.returncode == 0 else _redact(ctx, last_line(r))

    def ready_text(self, ctx) -> str:
        return (f"gbrain is running at http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port} "
                f"(admin UI /admin; its token is {TOKEN_KEY} in {ctx.paths.app_env}). "
                f"MCP connectors use {ctx.conf.dashboard_public_url}/mcp once you publish it "
                "(README: gbrain)")

    def initial_conf(self, ctx) -> str:
        return ("# Talaria settings; see README.\napp = gbrain\n"
                f"data_dir = {self.default_data_dir}\n"
                "# required: the https address MCP connectors use, e.g.\n"
                "# dashboard.public_url = https://<host>.<tailnet>.ts.net:8443\n")
```

- [ ] **Step 4: Run the full suite**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: all PASS. `tests/golden/*`, the Clawvisor golden and `tests/test_units.py` are unchanged.

- [ ] **Step 5: Commit**

```bash
git add templates/gbrain.container talaria/apps/gbrain.py tests/test_gbrain.py
git commit -m "gbrain adapter: Quadlet, admin token, first-run init, offline rehearsal, schema check, dream"
```

---

### Task 7: e2e with a fake gbrain release, contract test, docs, v0.6.0, mutation run

**Files:**
- Create: `tests/e2e/fake_gbrain/fake_gbrain.c`, `tests/e2e/fake_release.py`, `tests/e2e/test_e2e_gbrain.py`, `tests/test_fake_gbrain.py`, `tests/contract/test_contract_gbrain.py`
- Modify: `tests/e2e/conftest.py` (`gb_env`), `.github/workflows/ci.yml`, `README.md`, `AGENT_SETUP.md`, `pyproject.toml`, `uv.lock`, `talaria/__init__.py`, `tests/test_cli.py` (`test_versions_agree`), `docs/mutation-report.md`

**Interfaces:**
- Consumes (Tasks 5/6): `talaria.apps.gbrain.MARKER`, `MARKER_TEXT`, `parse_schema(out) -> int`; release layout from R1/R2 (`<repo>/api/releases/tags/<tag>` JSON with `assets[{name, digest}]`, `<repo>/releases/download/<tag>/gbrain-linux-x64`). Setup prints `brain initialized`, `gbrain is running`. Offer text: `gbrain <tag> is ready to deploy` with `Brain schema: A → B`. Approve button `gbrain|ap:<tag>`. Deploy text `Deployed gbrain <tag>.`. Maintenance messages from Task 3. The hub CLI `talaria maintain` (Task 4). Rollback text `Rolled back to gbrain <tag>.`. Existing e2e helpers in `tests/e2e/conftest.py`: `WORK`, `HUB`, `sh`, `as_user`, `talaria`, `root_block`, `wait_for`, `bus_ready`, `seed_hub_conf`, `AppEnv(user, app, src, telegram)` (`.conf`, `.conf_add`, `.setup_until_done`, `.telegram`), `_base_env` fixture (`{"src", "tg"}`); fake Telegram state `tg.inject`, `tg.tap`, `tg.wait_sent(needle, timeout=240)`, `tg.wait_polling()`, `tg.sent`.
- Produces: `FakeReleases(root, port=8092)` with `.url`, `.publish(version: str, schema: int) -> tag`, `.shutdown()`; fixture `gb_env -> {"app": AppEnv, "rel": FakeReleases}`; version `0.6.0`.

- [ ] **Step 1: The fake gbrain program and its unit test**

```c
/* tests/e2e/fake_gbrain/fake_gbrain.c
   Stand-in for the gbrain release binary in Talaria's e2e test (test_e2e_gbrain.py). It
   speaks just enough of the real CLI: --version, init --pglite, config, remember,
   recall --query, doctor --json, dream and serve --http (GET /health). The "brain" is
   files under $GBRAIN_HOME/.gbrain: config.json, schema (an integer; opening the brain
   raises it to SCHEMA, like a migration), facts and dreams.
   Build: gcc -O2 -static -DVERSION='"0.60.1.0"' -DSCHEMA=1 -o gbrain-linux-x64 fake_gbrain.c
   (static: it runs in distroless/cc, whose glibc is older than the build host's). */
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef VERSION
#define VERSION "0.0.0.0"
#endif
#ifndef SCHEMA
#define SCHEMA 1
#endif

static char home[512];

static const char *path(const char *name) {
    static char buf[1024];
    snprintf(buf, sizeof buf, "%s/.gbrain/%s", home, name);
    return buf;
}

static int read_schema(void) {
    int v = 0;
    FILE *f = fopen(path("schema"), "r");
    if (f) {
        if (fscanf(f, "%d", &v) != 1) v = 0;
        fclose(f);
    }
    return v;
}

static int write_text(const char *name, const char *mode, const char *text) {
    FILE *f = fopen(path(name), mode);
    if (!f) {
        perror(name);
        return 1;
    }
    fputs(text, f);
    return fclose(f) != 0;
}

static int open_brain(void) {
    char v[32];
    if (access(path("config.json"), F_OK) != 0) {
        fprintf(stderr, "Error: no brain at %s; run gbrain init\n", home);
        return 1;
    }
    if (read_schema() >= SCHEMA) return 0;
    snprintf(v, sizeof v, "%d\n", SCHEMA);
    return write_text("schema", "w", v);
}

static void on_term(int sig) {
    (void)sig;
    _exit(0);
}

static int serve(int port) {
    int s = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = htons((unsigned short)port);
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    if (s < 0 || setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one) != 0 ||
        bind(s, (struct sockaddr *)&a, sizeof a) != 0 || listen(s, 16) != 0) {
        perror("serve");
        return 1;
    }
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    printf("MCP Server v%s listening on 0.0.0.0:%d\n", VERSION, port);
    fflush(stdout);
    for (;;) {
        char req[2048], body[256], out[512];
        int c = accept(s, NULL, NULL), n, ok;
        if (c < 0) continue;
        n = (int)read(c, req, sizeof req - 1);
        req[n > 0 ? n : 0] = '\0';
        ok = strncmp(req, "GET /health ", 12) == 0;
        if (ok)
            snprintf(body, sizeof body,
                     "{\"status\":\"ok\",\"version\":\"%s\",\"engine\":\"pglite\"}", VERSION);
        else
            snprintf(body, sizeof body, "{\"error\":\"not found\"}");
        n = snprintf(out, sizeof out,
                     "HTTP/1.1 %s\r\nContent-Type: application/json\r\nContent-Length: %zu\r\n"
                     "Connection: close\r\n\r\n%s",
                     ok ? "200 OK" : "404 Not Found", strlen(body), body);
        if (write(c, out, (size_t)n) < 0) perror("write");
        close(c);
    }
}

int main(int argc, char **argv) {
    const char *h = getenv("GBRAIN_HOME");
    const char *cmd = argc > 1 ? argv[1] : "";
    snprintf(home, sizeof home, "%s", h && *h ? h : "/data");
    if (!strcmp(cmd, "--version") || !strcmp(cmd, "version")) {
        printf("gbrain %s\n", VERSION);
        return 0;
    }
    if (!strcmp(cmd, "init")) {
        char dir[1024];
        snprintf(dir, sizeof dir, "%s/.gbrain", home);
        if (mkdir(dir, 0700) != 0 && access(dir, F_OK) != 0) {
            perror(dir);
            return 1;
        }
        return write_text("config.json", "w", "{\"engine\":\"pglite\"}\n") || open_brain();
    }
    if (open_brain() != 0) return 1;
    if (!strcmp(cmd, "config")) return 0;
    if (!strcmp(cmd, "remember") && argc > 2)
        return write_text("facts", "a", argv[2]) || write_text("facts", "a", "\n");
    if (!strcmp(cmd, "recall") && argc > 3 && !strcmp(argv[2], "--query")) {
        char line[1024];
        FILE *f = fopen(path("facts"), "r");
        if (!f) return 0;
        while (fgets(line, sizeof line, f))
            if (strstr(line, argv[3])) fputs(line, stdout);
        fclose(f);
        return 0;
    }
    if (!strcmp(cmd, "doctor")) {
        int v = read_schema();
        if (v > SCHEMA)
            printf("{\"status\":\"warn\",\"checks\":[{\"name\":\"schema_version\",\"status\":"
                   "\"warn\",\"message\":\"Version %d is AHEAD of this client's latest known "
                   "version (%d).\"}]}\n", v, SCHEMA);
        else
            printf("{\"status\":\"ok\",\"checks\":[{\"name\":\"schema_version\",\"status\":"
                   "\"ok\",\"message\":\"Version %d (latest: %d)\"}]}\n", v, SCHEMA);
        return 0;
    }
    if (!strcmp(cmd, "dream")) {
        if (getenv("FAKE_DREAM_FAIL")) {
            fprintf(stderr, "dream: phase synthesize failed\n");
            return 1;
        }
        return write_text("dreams", "a", "dream\n");
    }
    if (!strcmp(cmd, "serve")) {
        int port = 3131;
        for (int i = 2; i + 1 < argc; i++)
            if (!strcmp(argv[i], "--port")) port = atoi(argv[i + 1]);
        return serve(port);
    }
    fprintf(stderr, "fake gbrain: unknown command %s\n", cmd);
    return 2;
}
```

```python
# tests/test_fake_gbrain.py
"""The e2e fake gbrain (tests/e2e/fake_gbrain/fake_gbrain.c) speaks the CLI the adapter
uses. Built natively here (no -static); skipped without gcc."""
import json
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from talaria.apps.gbrain import MARKER, MARKER_TEXT, parse_schema

SRC = Path(__file__).parent / "e2e/fake_gbrain/fake_gbrain.c"


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not installed")
    out = tmp_path_factory.mktemp("fake")
    bins = {}
    for version, schema in (("0.60.1.0", 1), ("0.60.2.0", 2)):
        b = out / f"gbrain-{schema}"
        subprocess.run(["gcc", "-O2", f'-DVERSION="{version}"', f"-DSCHEMA={schema}", "-o",
                        str(b), str(SRC)], check=True, timeout=120)
        bins[schema] = b
    return bins


def gb(binary, home, *args, env=()):
    return subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin:/bin", "GBRAIN_HOME": str(home), **dict(env)})


def test_cli(build, tmp_path):
    old, new = build[1], build[2]
    assert gb(old, tmp_path, "--version").stdout == "gbrain 0.60.1.0\n"
    assert gb(old, tmp_path, "doctor", "--json").returncode == 1        # no brain yet
    assert gb(old, tmp_path, "init", "--pglite").returncode == 0
    assert gb(old, tmp_path, "config", "set", "self_upgrade.mode", "off").returncode == 0
    assert gb(old, tmp_path, "remember", MARKER_TEXT).returncode == 0
    assert parse_schema(gb(old, tmp_path, "doctor", "--json").stdout) == 1
    assert parse_schema(gb(new, tmp_path, "doctor", "--json").stdout) == 2     # migrates
    with pytest.raises(ValueError, match="AHEAD"):
        parse_schema(gb(old, tmp_path, "doctor", "--json").stdout)
    assert MARKER in gb(old, tmp_path, "recall", "--query", MARKER).stdout
    assert gb(old, tmp_path, "recall", "--query", "nothing like it").stdout == ""
    assert gb(old, tmp_path, "dream").returncode == 0
    r = gb(old, tmp_path, "dream", env={"FAKE_DREAM_FAIL": "1"})
    assert r.returncode == 1 and r.stderr == "dream: phase synthesize failed\n"
    assert (tmp_path / ".gbrain/dreams").read_text() == "dream\n"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_answers_health_and_stops_on_sigterm(build, tmp_path):
    gb(build[1], tmp_path, "init", "--pglite")
    port = free_port()
    p = subprocess.Popen([str(build[1]), "serve", "--http", "--bind", "0.0.0.0", "--port",
                          str(port)], env={"GBRAIN_HOME": str(tmp_path)},
                         stdout=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                body = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("the fake server did not answer")
        assert json.loads(body) == {"status": "ok", "version": "0.60.1.0", "engine": "pglite"}
    finally:
        p.terminate()
        assert p.wait(timeout=10) == 0
```

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests/test_fake_gbrain.py`
Expected: PASS (or SKIP without gcc).

- [ ] **Step 2: The fake release server, fixture and e2e test**

```python
# tests/e2e/fake_release.py
"""A static web server that looks like a GitHub repository with gbrain releases, for the
gbrain e2e test (plan ruling R1): the bare git repository over git's dumb HTTP protocol
(`git ls-remote`), each release's asset under releases/download/<tag>/, and its JSON with
the asset's sha256 digest (as GitHub's API has it) under api/releases/tags/<tag>."""
import functools
import hashlib
import json
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SRC = Path(__file__).parent / "fake_gbrain" / "fake_gbrain.c"
ASSET = "gbrain-linux-x64"


def run(*argv):
    subprocess.run([str(a) for a in argv], check=True, capture_output=True, text=True,
                   timeout=300)


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class FakeReleases:
    def __init__(self, root: Path, port: int = 8092):
        self.root, self.port = Path(root), port
        self.repo = self.root / "gbrain"
        self.work = self.root.parent / "gbrain-work"
        self.url = f"http://127.0.0.1:{port}/gbrain"
        self.root.mkdir(parents=True, exist_ok=True)
        run("git", "init", "-q", "--bare", self.repo)
        run("git", "clone", "-q", self.repo, self.work)
        run("git", "-C", self.work, "config", "user.email", "e2e@example.invalid")
        run("git", "-C", self.work, "config", "user.name", "e2e")
        handler = functools.partial(_Quiet, directory=str(self.root))
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, version: str, schema: int) -> str:
        tag = f"v{version}"
        dl = self.repo / "releases/download" / tag
        dl.mkdir(parents=True)
        binary = dl / ASSET
        run("gcc", "-O2", "-static", f'-DVERSION="{version}"', f"-DSCHEMA={schema}", "-o",
            binary, SRC)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        api = self.repo / "api/releases/tags"
        api.mkdir(parents=True, exist_ok=True)
        (api / tag).write_text(json.dumps({"tag_name": tag, "assets": [
            {"name": ASSET, "digest": f"sha256:{digest}"}]}))
        run("git", "-C", self.work, "commit", "-q", "--allow-empty", "-m", tag)
        run("git", "-C", self.work, "tag", tag)
        run("git", "-C", self.work, "push", "-q", "origin", "HEAD", tag)
        run("git", "-C", self.repo, "update-server-info")
        run("chmod", "-R", "a+rX", self.root)
        return tag

    def shutdown(self):
        self.server.shutdown()
```

Append to `tests/e2e/conftest.py`:

```python
@pytest.fixture(scope="session")
def gb_env(_base_env):
    """A third app user, gbtest, running gbrain under the same hub, from fake releases
    (tests/e2e/fake_release.py) instead of GitHub."""
    from tests.e2e.fake_release import FakeReleases
    rel = FakeReleases(WORK / "www")
    rel.publish("0.60.1.0", 1)
    user = "gbtest"
    r = sh(_base_env["src"] / "bin/talaria", "setup", "--dev", "--app", "gbrain",
           "--user", user, check=False)
    assert r.returncode == 10, r.stdout
    sh("bash", "-c", root_block(r.stdout))
    wait_for(lambda: bus_ready(user))
    wait_for(lambda: bus_ready(HUB))
    seed_hub_conf(HUB)
    as_user("git", "config", "--global", "--add", "safe.directory", "*", user=user)
    yield {"app": AppEnv(user=user, app="gbrain", src=_base_env["src"],
                         telegram=_base_env["tg"]), "rel": rel}
    rel.shutdown()
```

```python
# tests/e2e/test_e2e_gbrain.py
"""gbrain e2e (spec 2026-10-09 §8): a third app under the same hub, from fake releases
(tests/e2e/fake_release.py, one static C program per release). Runs after
test_e2e_clawvisor.py and before test_e2e_hub.py (plan ruling R16)."""
import json
from datetime import datetime

import pytest

from tests.e2e.conftest import HUB, as_user, talaria

pytestmark = pytest.mark.e2e
USER = "gbtest"
DATA = f"/home/{USER}/gbrain-data"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

CONF = """app = gbrain
repo = http://127.0.0.1:8092/gbrain
dashboard.bind = loopback
dashboard.public_url = https://brain.example.invalid
talaria_repo = /tmp/talaria-e2e/talaria-src
settle_seconds = 10
telegram_api = http://127.0.0.1:8081
disk.floor_gb = 0.2
min_release = v0.60.1.0
release_allow = v0.60.1.0
"""


def read(path):
    return as_user("cat", path, user=USER).stdout


def active():
    return as_user("systemctl", "--user", "is-active", "gbrain.service", user=USER,
                   check=False).stdout.strip() == "active"


def test_01_fresh_setup(gb_env):
    app = gb_env["app"]
    app.conf(CONF)
    out = app.setup_until_done()
    assert "brain initialized" in out and "gbrain is running" in out
    token = read(f"/home/{USER}/.config/talaria/gbrain.env").split(
        "GBRAIN_ADMIN_BOOTSTRAP_TOKEN=", 1)[1].split("\n", 1)[0]
    assert token and token not in out
    st = json.loads(read(f"/home/{USER}/.local/state/talaria/state.json"))
    assert st["current"]["tag"] == "v0.60.1.0"
    assert read(f"{DATA}/.talaria-schema").strip() == "1"
    assert "talaria rehearsal marker" in read(f"{DATA}/.gbrain/facts")
    health = as_user("python3", "-c", "import urllib.request; print(urllib.request.urlopen("
                     "'http://127.0.0.1:3131/health').read().decode())", user=USER).stdout
    assert json.loads(health)["status"] == "ok"


def test_02_offer_shows_the_schema_change_and_deploys(gb_env):
    app, rel = gb_env["app"], gb_env["rel"]
    rel.publish("0.60.2.0", 2)
    app.conf_add("release_allow = v0.60.1.0 v0.60.2.0\n")
    app.telegram.wait_polling()     # registering the app restarted the bot
    app.telegram.inject("/check gbrain")
    offer = app.telegram.wait_sent("gbrain v0.60.2.0 is ready to deploy", timeout=900)
    assert "Brain schema: 1 → 2" in offer
    app.telegram.tap("gbrain|ap:v0.60.2.0")
    app.telegram.wait_sent("Deployed gbrain v0.60.2.0.", timeout=900)
    assert read(f"{DATA}/.talaria-schema").strip() == "2"
    assert active()


def test_03_maintenance_runs_dream_and_restarts(gb_env):
    tg = gb_env["app"].telegram
    talaria("maintain", user=HUB)
    tg.wait_sent("gbrain maintenance finished.")
    assert read(f"{DATA}/.gbrain/dreams").count("dream") == 1
    assert active()


def test_04_failed_maintenance_reports_and_restarts(gb_env):
    tg = gb_env["app"].telegram
    env = f"/home/{USER}/.config/talaria/gbrain.env"
    as_user("sh", "-c", f"echo FAKE_DREAM_FAIL=1 >> {env}", user=USER)
    try:
        talaria("maintain", user=HUB)
        msg = tg.wait_sent("gbrain maintenance failed")
        assert "dream: phase synthesize failed" in msg
        assert active()
    finally:
        as_user("sed", "-i", "/^FAKE_DREAM_FAIL=/d", env, user=USER)


def test_05_the_timer_skips_gbrain_on_other_days_but_check_does_not(gb_env):
    app, rel = gb_env["app"], gb_env["rel"]
    rel.publish("0.60.3.0", 3)
    other = DAYS[(datetime.now().weekday() + 3) % 7]
    app.conf_add(f"check.days = {other}\nrelease_allow = v0.60.1.0 v0.60.2.0 v0.60.3.0\n")
    talaria("check", "--timer", user=HUB, timeout=1800)
    assert not any("gbrain v0.60.3.0 is ready" in t for t in app.telegram.sent)
    app.telegram.inject("/check gbrain")
    app.telegram.wait_sent("gbrain v0.60.3.0 is ready to deploy", timeout=900)


def test_06_rollback_restores_image_and_schema(gb_env):
    tg = gb_env["app"].telegram
    tg.inject("/rollback gbrain CONFIRM")
    tg.wait_sent("Rolled back to gbrain v0.60.1.0.", timeout=900)
    assert read(f"{DATA}/.talaria-schema").strip() == "1"
    assert active()
```

- [ ] **Step 3: CI**

In `.github/workflows/ci.yml`, job `e2e`, replace the test run with:

```yaml
      - run: >-
          TALARIA_E2E=1 uv run pytest -m e2e -v -x tests/e2e/test_e2e.py
          tests/e2e/test_e2e_clawvisor.py tests/e2e/test_e2e_gbrain.py tests/e2e/test_e2e_hub.py
```

and in the `if: failure()` step change the user list to `for u in talaria hermes cvtest gbtest hubadopt hermes2 hub2 hermes3; do`. The runner image ships `gcc` and static glibc (`libc6-dev`); nothing to install.

- [ ] **Step 4: Contract test (real binary; release and weekly workflows run `tests/contract`)**

```python
# tests/contract/test_contract_gbrain.py
"""Contract test against the newest real gbrain release (TALARIA_CONTRACT=1; network,
podman, ~200 MB). Pins what the adapter assumes about the real CLI: the release API's
digest, `--version`, `init --pglite`, `config set`, `remember`/`recall` and the shape of
`doctor --json` (parse_schema)."""
import os

import pytest

from talaria import apps, images, rehearse, tags
from talaria.conf import Conf
from talaria.ctx import Ctx, Paths
from talaria.shell import Shell

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if os.environ.get("TALARIA_CONTRACT") != "1":
        pytest.skip("set TALARIA_CONTRACT=1")
    home = tmp_path_factory.mktemp("home")
    app = apps.get("gbrain")
    conf = Conf(data_dir=home / "gbrain-data", app="gbrain", repo=app.default_repo,
                min_release=app.min_release, dashboard_public_url="https://brain.example.invalid")
    ctx = Ctx(paths=Paths(home, "gbrain"), conf=conf, sh=Shell(), notify=None, app=app)
    git = tags.releases(ctx)
    tag = max(git, key=app.tag_key)
    return ctx, app.fetch(ctx, tag, git[tag])


def test_a_fresh_brain_initializes_and_rehearses(built):
    ctx, rec = built
    ctx.app.prepare(ctx)
    images.retag(ctx, "current", rec)
    assert ctx.app.initialize(ctx)
    v = ctx.app.data_version(ctx.conf.data_dir)
    assert isinstance(v, int) and v > 0
    copy = ctx.paths.home / "copy"
    rehearse.copy_data(ctx.conf.data_dir, copy, ())
    report = ctx.app.rehearse(ctx, {}, rec, copy, None)
    assert report["after"] == v and report["before"] == v
```

- [ ] **Step 5: README**

Make these edits in `README.md`:

1. After the paragraph "Setting this up with a coding agent? …" add:

```markdown
Talaria manages Hermes, [Clawvisor](#clawvisor) and [gbrain](#gbrain), each in its own account under one bot.
```

2. In "Requirements", replace `- amd64 or arm64.` with `- amd64 or arm64 (gbrain: amd64 only; it publishes no arm64 Linux build).`

3. In "What setup changes", "For the hub", replace `` - `talaria-check.timer` and `talaria-telegram.service`. `` with `` - `talaria-check.timer`, `talaria-maintain.timer` and `talaria-telegram.service`. ``

4. In "Configuration": change the sentence "a Clawvisor install (`app = clawvisor`) gets its own defaults for …" to "a Clawvisor or gbrain install (`app = clawvisor`, `app = gbrain`) gets its own defaults for `data_dir`, `dashboard.port`, `repo`, `image`, `min_release`, `backup.exclude`, `check.days` and `maintenance.time`." Add two rows after `check.time`:

```markdown
| `check.days` | every day (gbrain: `mon thu`); space-separated `mon` … `sun`: the days the hub's daily timer looks for this app's releases. `/check` always looks. Empty = every day |
| `maintenance.time` | none (gbrain: `03:30`); `HH:MM` local time: start of a two-hour window in which Talaria runs the app's maintenance once (gbrain: `gbrain dream`). Empty turns it off |
```

5. In "Uninstall", hub block: `systemctl --user disable --now talaria-check.timer talaria-maintain.timer talaria-telegram.service`.

6. In "Architecture", replace "`talaria/apps/hermes.py` and `talaria/apps/clawvisor.py` are the adapters today." with "`talaria/apps/hermes.py`, `talaria/apps/clawvisor.py` and `talaria/apps/gbrain.py` are the adapters today. `talaria/maintain.py` (app side) and `talaria/hubmaintain.py` (hub timer) run maintenance windows."

7. In "Security", add a table row: `| gbrain's public connector paths (Tailscale Funnel) | untrusted; DCR clients stay pending until you approve them in /admin |`.

8. Insert this section between "## Clawvisor" and "## Security":

````markdown
## gbrain

Talaria can manage [gbrain](https://github.com/garrytan/gbrain), an agent memory "brain"
with an MCP server, the way it manages Hermes and Clawvisor. That covers release
detection, a rehearsal on a copy, Telegram approval, backup, deploy, verify, and
rollback of image and data together. gbrain runs in its own service account under the
same bot:

```bash
bin/talaria setup --plan --app gbrain --user gbrain
bin/talaria setup --app gbrain --user gbrain
```

- **`dashboard.public_url` is required.** It is the https address MCP connectors use,
  port included, for example `dashboard.public_url = https://<host>.<tailnet>.ts.net:8443`.
  It becomes gbrain's `--public-url`, which is its OAuth issuer. Setup stops without it.
- **Image.** gbrain publishes no container image. Talaria downloads the release's
  `gbrain-linux-x64` and checks its SHA-256 against the digest GitHub publishes for that
  asset. When `gh` is installed and logged in for the service account, Talaria also checks
  the build provenance (`gh attestation verify`). Otherwise setup prints a `NOTE` and only
  the digest is checked. Talaria then builds a local image from a pinned distroless base.
- **Storage.** gbrain keeps its data in PGLite (embedded Postgres) in `~gbrain/gbrain-data`,
  mode 0700, because some PGLite files inside it are world-readable. Only one process can
  open the brain at a time. Rehearsal copies, backups and maintenance therefore stop the
  server for a moment. The rehearsal never starts the new server: it runs
  `gbrain doctor --json` (schema version) and recalls a marker page that setup wrote, on
  the copy, offline and without your keys.
- **Admin token.** Setup generates `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` in
  `~gbrain/.config/talaria/gbrain.env` (mode 0600), and you log in to `/admin` with it.
  Treat it like a password: never paste it into a chat or an agent's context.
- **Provider keys** (embeddings, synthesis, the dream cycle) are yours to add. Put them
  in the same file with your own editor, as the service user: `OPENAI_API_KEY=…`,
  `ANTHROPIC_API_KEY=…`, or any provider gbrain supports, including an OpenAI-compatible
  router. Then run `systemctl --user restart gbrain.service`. Talaria never asks for
  provider keys and never prints them. To choose models for an existing brain, stop
  gbrain first, because the command needs the brain to itself. As the service user:

  ```bash
  systemctl --user stop gbrain.service
  podman run --rm --read-only --userns=keep-id:uid=65532,gid=65532 \
    -v ~/gbrain-data:/data:Z -e HOME=/data -e GBRAIN_HOME=/data \
    --env-file ~/.config/talaria/gbrain.env localhost/gbrain:current \
    embeddings enable --embedding-model <provider:model>
  systemctl --user start gbrain.service
  ```
- **Update cadence.** gbrain releases several times a day. By default
  (`check.days = mon thu`) the daily timer looks for a gbrain release twice a week.
  `/check gbrain` looks at any time.
- **Nightly maintenance.** Each night, from `maintenance.time` (default `03:30`, local
  time), Talaria stops gbrain, runs `gbrain dream` once in a one-off container, and starts
  gbrain again. The dream cycle has network access and your provider keys; without keys it
  runs only its file-based phases. Expect minutes of downtime. The run is skipped while
  another Talaria operation runs or a change is interrupted. Success is silent. A failure
  sends one message, and the next night retries. The hub's `talaria-maintain.timer` checks
  every 15 minutes whether a window is due. `maintenance.time =` (empty) turns
  maintenance off.
- Out of scope: Postgres, gbrain's autopilot daemon, adopting an existing gbrain install,
  and gbrain's own `self-upgrade` (Talaria turns its update checks off).

### Who reaches what

| Path | Who | Where |
|---|---|---|
| `/mcp`, `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource` (and `…/mcp`), `/authorize`, `/token`, `/register`, `/revoke` | cloud MCP connectors (claude.ai, ChatGPT) | public, port 8443 |
| everything, including `/admin` and `/metrics` | you, and local agents on the host or tailnet | tailnet only, port 10000 (or the published address) |

Cloud connectors use OAuth: they register themselves (dynamic client registration) and
you approve each one in `/admin`. **With registration open, anyone who reaches
`/register` can create a client.** It stays *pending*, and nothing is granted until you
approve it. Approve only clients you just added yourself. Local agents (Hermes, Claude
Code, Codex) get scoped tokens that you mint in `/admin`, and use the tailnet port or the
published address.

A connector's consent link points at the public port (`https://<host>.<tailnet>.ts.net:8443/admin/?oauth_request=…`),
which does not serve `/admin`. Open the same link with port `10000` instead of `8443` to
approve it over your tailnet.

### Public MCP endpoints with Tailscale Serve and Funnel

Talaria does not configure Tailscale. For any app with a public MCP endpoint, publish the
app on loopback (`dashboard.bind = loopback`, the default) or on its Tailscale address.
Then, as root, once (`<port>` is the app's `dashboard.port`, 3131 for gbrain):

```bash
# public: exactly the connector paths, on 8443
for p in /mcp /.well-known/oauth-authorization-server /.well-known/oauth-protected-resource \
         /authorize /token /register /revoke; do
  tailscale funnel --bg --https=8443 --set-path "$p" "http://127.0.0.1:<port>$p"
done
# tailnet only: the whole server, including its admin UI, on 10000
tailscale serve --bg --https=10000 http://127.0.0.1:<port>
tailscale funnel status
```

Funnel must be allowed for the node in your tailnet policy, and it only works on ports
443, 8443 and 10000. **Funnel applies per port: never funnel a port that also serves a
private app.** `tailscale funnel status` should list only the paths above as public.
Set `dashboard.public_url = https://<host>.<tailnet>.ts.net:8443` and run setup again.
````

- [ ] **Step 6: AGENT_SETUP.md**

1. Step 3: replace "which app this install manages — Hermes or Clawvisor —" with "which app this install manages — Hermes, Clawvisor or gbrain —", and "Pass `--app clawvisor` for a Clawvisor install;" with "Pass `--app clawvisor` or `--app gbrain` for those installs;".
2. Step 4: replace "(`clawvisor` by default for a Clawvisor install)" with "(`clawvisor` or `gbrain` by default for those installs)".
3. Insert after step 11:

```markdown
11a. For a gbrain install:
    - Before the first setup, ask the person for the https address MCP connectors will use
      and write it as `dashboard.public_url` (e.g. `https://<host>.<tailnet>.ts.net:8443`)
      into `talaria.conf`; setup stops without it.
    - **Provider keys are the person's to add, never yours.** Tell the person to add them
      (e.g. `OPENAI_API_KEY=…`) to `~gbrain/.config/talaria/gbrain.env` themselves, in
      their own editor as the service user, then restart `gbrain.service`. Never read,
      print, `cat`, `grep` or copy that file, and never ask for a key in the chat. The same
      file holds the admin token (`GBRAIN_ADMIN_BOOTSTRAP_TOKEN`); the same rules apply.
    - Public access is Tailscale Serve and Funnel, configured by the person as root (README:
      "Public MCP endpoints with Tailscale Serve and Funnel"). Show the commands; never run
      them. Explain that only the connector paths become public and that `/register` lets
      anyone create a pending client the person must approve.
    - The person logs in to `/admin`, adds the connectors in claude.ai or ChatGPT and
      approves them there; local agents get scoped tokens from `/admin`.
```

4. Step 12: replace "**Never run `deploy`, `rollback` or `restore`.**" with "**Never run `deploy`, `rollback`, `restore` or `maintain`.**"

- [ ] **Step 7: Version 0.6.0**

Set `version = "0.6.0"` in `pyproject.toml`, `__version__ = "0.6.0"` in `talaria/__init__.py`, `version = "0.6.0"` under `name = "talaria"` in `uv.lock`. In `tests/test_cli.py::test_versions_agree`, change `"0.5.4"` to `"0.6.0"`. Then run `uv lock --check` (expected: no change needed).

- [ ] **Step 8: Full suite and the generic-values check**

```bash
timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract
uv run shellcheck bin/talaria
! grep -rnE '100\.[0-9]+\.[0-9]+\.[0-9]+|openclaw|@gmail\.com' --include='*.md' --include='*.py' \
    --include='*.container' --include='*.service' --include='*.timer' \
    README.md AGENT_SETUP.md talaria helpers templates docs 2>/dev/null | grep -v 'docs/superpowers/'
```

Expected: all PASS; the grep prints nothing (exit 0 because of `!`).

- [ ] **Step 9: Mutation run**

```bash
timeout 7200 systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 uv run mutmut run
uv run mutmut export-cicd-stats && uv run python tests/mutation_score.py
```

Expected: `score: ≥ 85.0%` and exit 0. List the survivors per module with `uv run mutmut results`. Handle every survivor in `talaria.apps.gbrain`, `talaria.maintain`, `talaria.hubmaintain` and in the changed functions:
- `conf.check_days`, `conf.check_time_of_day`, `check.due_today`, `ctx.local_now`
- `cli._locked`, `cli.run_locked`
- `op` maintain routing
- `rehearse.rehearse` (copy_stopped)
- `setup.service_phase` (notes, initialize)
- `units._render`, `units.render_hub_units`

Kill each one with a focused test (exact text, exact argv, exact timeout). If a survivor is equivalent, mark the line `# pragma: no mutate` with the reason next to it. `rollback`, `restore`, `marker` and `deploy` must keep 0 true survivors. Re-run `mutmut run` on the touched modules until the result is stable.

Then update `docs/mutation-report.md`:
- Replace the date line and the score paragraph with the new run's facts, in the same form: date, commit, `v0.6.0, gbrain`, tool version, score, and the totals exactly as `tests/mutation_score.py` printed them.
- Replace the module table with the new per-module counts. Add rows for `talaria.apps.gbrain`, `talaria.maintain` and `talaria.hubmaintain`.
- Add a section "## What changed in v0.6.0 (gbrain)" that lists the new modules and their survivor counts, every new `# pragma: no mutate` line with its reason, and the fact that the e2e job now also runs gbrain (fake releases, a static C program). Keep the older sections below it unchanged.

- [ ] **Step 10: Commit**

```bash
git add tests/e2e/fake_gbrain/fake_gbrain.c tests/e2e/fake_release.py tests/e2e/conftest.py \
        tests/e2e/test_e2e_gbrain.py tests/test_fake_gbrain.py tests/contract/test_contract_gbrain.py \
        .github/workflows/ci.yml README.md AGENT_SETUP.md pyproject.toml uv.lock \
        talaria/__init__.py tests/test_cli.py docs/mutation-report.md
git add -u talaria tests        # pragmas and survivor-killing tests from Step 9
git commit -m "gbrain e2e with fake releases, contract test, docs, v0.6.0, mutation report"
```

No merge, tag, push or host steps: the controller does those.
