# Talaria app adapters and Clawvisor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move everything Hermes-specific in Talaria behind an `App` adapter without changing Hermes behaviour (v0.3.0), add a Clawvisor adapter (v0.4.0), then roll Clawvisor out on this host and connect Hermes to it.

**Architecture:** A new package `talaria/apps/` holds `base.py` (the `App` class with the shared defaults), `hermes.py` and `clawvisor.py`. `ctx.app` is the adapter for this install, chosen by `app =` in `talaria.conf` (default `hermes`). Core modules (deploy, rollback, restore, backup, retention, telegram, status, setup) call `ctx.app.<hook>` where they used Hermes code before; `talaria/hermes.py` becomes the app-neutral `talaria/service.py`.

**Tech Stack:** Python ≥ 3.10 stdlib only, pytest, mutmut 3, rootless podman + Quadlet, systemd user units, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-02-talaria-adapters-design.md`

## Global Constraints

- Python stdlib only at runtime; `python3 -I` via `bin/talaria`; supports Python 3.10 and 3.13 (CI matrix).
- Every test run is capped: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest …` (mutmut: `MemoryMax=4G`). Never `prlimit --as`.
- No behaviour change for Hermes in Part A: rendered `hermes.container`, unit files, every message text and every command line stay byte-for-byte the same. Existing golden tests must pass unmodified, except for the import paths named in each task.
- `FakeShell` rules: `check` is a bool, `timeout` is positive or `None`.
- Secrets never printed, logged or sent to Telegram; generated secrets are written mode 0600 through `write_env_value`.
- Mutation score stays ≥ 95 % overall; `rollback`, `restore`, `marker`, `deploy` keep 0 survivors (crash-hook "segfault" mutants excepted).
- Clawvisor constants (spec §3): container uid/gid 65532, container port 25297, data mounted at `/data`, base image `gcr.io/distroless/static-debian12@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab`, release assets `clawvisor-server-linux-{amd64,arm64}` and `checksums.txt`, minimum release `v0.9.9`.
- Commit messages end with the two attribution lines from the session; work on a branch, merge with `--ff-only`.

## Review Focus

1. **An existing Hermes install upgrading to v0.3.0 with no `app =` line** must behave as before, including a pending update and an interrupted change recorded by v0.2.5 (state written by the old code). → Task 1 and Task 10 tests.
2. **Clawvisor release whose assets are not uploaded yet** (tag exists, `checksums.txt` 404) must be retried at the next check, not marked failed forever. → Task 11.
3. **Setup run twice for Clawvisor** must never regenerate `vault.key` or `JWT_SECRET` (that would make every stored credential unreadable). → Task 13.
4. **Rehearsal container left behind** after a crash (same name next time) must not make the next rehearsal fail. → Task 12.
5. **A tag argument from Telegram for the other app's scheme** (`/approve v2026.9.24` on a Clawvisor install) must be refused by the app's own pattern, not crash. → Task 2.

---

# Part A — Refactor (release v0.3.0, no behaviour change)

### Task 1: App registry, `app` setting, app-aware paths

**Files:**
- Create: `talaria/apps/__init__.py`, `talaria/apps/base.py`, `talaria/apps/hermes.py`
- Modify: `talaria/conf.py` (add `app` key), `talaria/ctx.py` (`Paths.app`, `Ctx.app`, `make_ctx`), `tests/fakes.py` (`make_test_ctx(tmp_path, app="hermes")`)
- Test: `tests/test_apps.py`

**Interfaces:**
- Produces: `talaria.apps.get(name: str) -> App` (raises `ValueError` for unknown names); `App` attributes `name, title, unit, container, quadlet_file, env_file, local_image, default_data_dir, default_port, container_port, default_image, default_repo, min_release, backup_exclude`; `Paths(home, app="hermes")` with `quadlet` → `quadlet_dir / app.quadlet_file` and `app_env` → `conf_dir / app.env_file` (`hermes_env` stays as an alias for `app_env`); `Ctx.app: App`; `Conf.app: str = "hermes"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_apps.py
import pytest

from talaria import apps
from talaria.conf import load_conf
from talaria.ctx import Paths


def test_registry():
    h = apps.get("hermes")
    assert (h.name, h.title, h.unit, h.container) == ("hermes", "Hermes", "hermes.service", "hermes")
    assert (h.quadlet_file, h.env_file, h.local_image) == ("hermes.container", "hermes.env",
                                                          "localhost/hermes-agent")
    assert (h.default_port, h.min_release) == (9119, "v2026.6.5")
    with pytest.raises(ValueError, match="unknown app: 'nope'"):
        apps.get("nope")


def test_conf_defaults_to_hermes(tmp_path):
    assert load_conf(Paths(tmp_path)).app == "hermes"


def test_conf_app_key_is_validated(tmp_path):
    p = Paths(tmp_path)
    p.conf_file.parent.mkdir(parents=True)
    p.conf_file.write_text("app = nope\n")
    with pytest.raises(ValueError, match="unknown app: 'nope'"):
        load_conf(p)


def test_paths_follow_the_app(tmp_path):
    p = Paths(tmp_path)
    assert p.quadlet == tmp_path / ".config/containers/systemd/hermes.container"
    assert p.app_env == p.hermes_env == tmp_path / ".config/talaria/hermes.env"
```

- [ ] **Step 2: Run to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q tests/test_apps.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'talaria.apps'`.

- [ ] **Step 3: Implement**

```python
# talaria/apps/base.py
from __future__ import annotations


class App:
    """What differs between the apps Talaria manages. One instance per app, stateless."""
    name = ""
    title = ""
    unit = ""
    container = ""
    quadlet_file = ""
    env_file = ""
    local_image = ""
    default_data_dir = ""
    default_port = 0
    container_port = 0
    default_image = ""
    default_repo = ""
    min_release = ""
    backup_exclude: tuple = ()
```

```python
# talaria/apps/hermes.py
from __future__ import annotations

from talaria.apps.base import App


class Hermes(App):
    name = "hermes"
    title = "Hermes"
    unit = "hermes.service"
    container = "hermes"
    quadlet_file = "hermes.container"
    env_file = "hermes.env"
    local_image = "localhost/hermes-agent"
    default_data_dir = "~/hermes-data"
    default_port = 9119
    container_port = 9119
    default_image = "docker.io/nousresearch/hermes-agent"
    default_repo = "https://github.com/NousResearch/hermes-agent"
    min_release = "v2026.6.5"
    backup_exclude = (".cache", ".npm", "home/.cache", "home/.npm", "backups")


APP = Hermes()
```

```python
# talaria/apps/__init__.py
from __future__ import annotations

from talaria.apps.base import App

NAMES = ("hermes",)


def get(name: str) -> App:
    if name == "hermes":
        from talaria.apps.hermes import APP
        return APP
    raise ValueError(f"unknown app: {name!r}")
```

In `talaria/conf.py`: add `app: str = "hermes"` as the first field after `data_dir` in `Conf`, add `"app": ("app", str)` to `_KEYS`, and after the key loop call `apps.get(conf.app)` (import inside the function) so an unknown name raises. Keep every existing default value literally as it is (they equal the Hermes adapter's; Part B switches defaults per app in Task 13).

In `talaria/ctx.py`: make `Paths` take `app: str = "hermes"` as a second field; replace the `quadlet` property body with `return self.quadlet_dir / apps.get(self.app).quadlet_file`; add `app_env` (`self.conf_dir / apps.get(self.app).env_file`) and make `hermes_env` return `self.app_env`. Add `app: object = None` to `Ctx` and fill it in `make_ctx`:

```python
def make_ctx() -> Ctx:
    from talaria import apps
    from talaria.conf import load_conf
    from talaria.notify import TelegramNotifier
    from talaria.shell import Shell

    conf = load_conf(Paths(Path.home()))
    paths = Paths(Path.home(), conf.app)
    return Ctx(paths=paths, conf=conf, sh=Shell(), notify=TelegramNotifier(conf),
               app=apps.get(conf.app))
```

In `tests/fakes.py`, give `make_test_ctx` an `app="hermes"` keyword and pass `app=apps.get(app)` and `Paths(home, app)`.

- [ ] **Step 4: Run all unit tests**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
Expected: PASS (all old tests plus 4 new).

- [ ] **Step 5: Commit**

```bash
git add talaria/apps talaria/conf.py talaria/ctx.py tests/fakes.py tests/test_apps.py
git commit -m "App registry and app-aware paths (Hermes only, no behaviour change)"
```

### Task 2: Release tags per app

**Files:**
- Modify: `talaria/tags.py`, `talaria/apps/base.py`, `talaria/apps/hermes.py`, `talaria/telegram.py:85-114`, `talaria/cli.py:17-22`, `talaria/adopt.py:129`, `talaria/check.py`
- Test: `tests/test_tags.py`, `tests/test_telegram.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `ctx.app` (Task 1).
- Produces: `tags.TAG_ARG` (regex `^v\d{1,4}(\.\d{1,4}){2,3}$`, the shape check for command arguments); `App.is_release(tag) -> bool`, `App.tag_key(tag) -> tuple` (raises `ValueError` for a non-release); `tags.pick_candidate(app, git_tags, published, current, excluded, floor) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tags.py (append)
from talaria import apps


def test_tag_arg_shape():
    for t in ("v2026.9.24", "v2026.9.24.1", "v0.9.10"):
        assert tags.TAG_ARG.match(t)
    for t in ("latest", "v1.2", "v1.2.3-rc1", "v1.2.3;rm", "v" + "1" * 5 + ".1.1"):
        assert not tags.TAG_ARG.match(t)


def test_hermes_tags_via_app():
    h = apps.get("hermes")
    assert h.is_release("v2026.9.24") and not h.is_release("v0.9.10")
    assert h.tag_key("v2026.9.24.2") == (2026, 9, 24, 2)


def test_pick_candidate_uses_the_apps_order():
    h = apps.get("hermes")
    git = {"v2026.9.7": "a", "v2026.9.24": "b", "v0.9.10": "c"}
    assert tags.pick_candidate(h, git, {"v2026.9.7", "v2026.9.24", "v0.9.10"},
                               "v2026.9.7", set(), "v2026.6.5") == "v2026.9.24"
```

```python
# tests/test_telegram.py (append)
def test_approve_refuses_the_other_apps_tag_scheme(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/approve v0.9.10"))
    assert ctx.sh.called("systemd-run") == []
    assert "Not understood" in api.sent()[-1]["text"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q tests/test_tags.py tests/test_telegram.py`
Expected: FAIL (`TAG_ARG` missing, `pick_candidate` signature).

- [ ] **Step 3: Implement**

In `talaria/tags.py` add `TAG_ARG = re.compile(r"^v\d{1,4}(\.\d{1,4}){2,3}$")` and change `pick_candidate`:

```python
def pick_candidate(app, git_tags, published: set[str], current: str | None,
                   excluded: set[str], floor: str) -> str | None:
    ok = [t for t in git_tags
          if app.is_release(t) and t in published and t not in excluded
          and app.tag_key(t) >= app.tag_key(floor)
          and (current is None or not app.is_release(current)
               or app.tag_key(t) > app.tag_key(current))]
    return max(ok, key=app.tag_key) if ok else None
```

In `talaria/apps/base.py` add abstract methods raising `NotImplementedError`; in `Hermes` implement them with the existing functions: `is_release = staticmethod(tags.is_release)`, `tag_key = staticmethod(tags.key)`.

Callers:
- `telegram.py`: `/approve`, `/reject` and the `ap:`/`rj:` buttons check `self.ctx.app.is_release(arg)` instead of `RELEASE_TAG.match(arg)`; a non-matching argument falls through to "Not understood".
- `cli.py`: `_release` checks `TAG_ARG` (argparse runs before ctx exists); `deploy`, `rehearse`, `reject` then refuse with exit 2 and `"{tag} is not a {app.title} release tag"` when `ctx.app.is_release(tag)` is false.
- `adopt.py:129` and `check.py`: use `ctx.app.is_release` / `ctx.app.tag_key` and the new `pick_candidate(ctx.app, …)`.
- `setup._fresh_image`: `pick_candidate(ctx.app, …)`.

- [ ] **Step 4: Run all unit tests** (same command as Task 1). Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -am "Release tags are checked by the app"`

### Task 3: Release discovery and image acquisition in the Hermes adapter

**Files:**
- Modify: `talaria/apps/base.py`, `talaria/apps/hermes.py`, `talaria/images.py`, `talaria/check.py`, `talaria/setup.py:_fresh_image`, `talaria/rehearse.py:rehearse` (pull step only)
- Test: `tests/test_images.py`, `tests/test_check.py`, `tests/test_apps.py`

**Interfaces:**
- Produces: `App.releases(ctx) -> dict[str, str]` (tag → commit), `App.published(ctx, tags) -> set[str]`, `App.fetch(ctx, tag, commit) -> dict` (image record `{"tag","id","digest","ref"}` plus optional `"commit"`), `App.reacquire(ctx, rec) -> None`; `images.ensure(ctx, rec)` calls `ctx.app.reacquire` when the image is missing; `images.retag/prune` use `ctx.app.local_image`.
- Raises from `fetch`: `images.RevisionMismatch` (permanent), `shell.CommandError` (transient) — unchanged meaning.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_apps.py (append)
def test_hermes_releases_published_fetch(tmp_path, monkeypatch):
    from talaria.apps import hermes as h
    from tests.fakes import make_test_ctx
    ctx = make_test_ctx(tmp_path)
    monkeypatch.setattr(h, "git_release_tags", lambda sh, repo: {"v2026.9.24": "c"})
    monkeypatch.setattr(h, "registry_tags", lambda sh, image, tls_verify=True: {"v2026.9.24"})
    monkeypatch.setattr(h, "pull_verify", lambda c, tag, commit: {"tag": tag, "id": commit})
    app = ctx.app
    assert app.releases(ctx) == {"v2026.9.24": "c"}
    assert app.published(ctx, ["v2026.9.24"]) == {"v2026.9.24"}
    assert app.fetch(ctx, "v2026.9.24", "c") == {"tag": "v2026.9.24", "id": "c"}
```

- [ ] **Step 2: Run to verify it fails** — expected `AttributeError: 'Hermes' object has no attribute 'releases'`.

- [ ] **Step 3: Implement**

```python
# talaria/apps/hermes.py (add)
from talaria.images import ImageMissing, pull_verify
from talaria.upstream import git_release_tags, registry_tags


class Hermes(App):
    ...
    def releases(self, ctx) -> dict[str, str]:
        return git_release_tags(ctx.sh, ctx.conf.hermes_repo)

    def published(self, ctx, tags) -> set[str]:
        return registry_tags(ctx.sh, ctx.conf.image, ctx.conf.registry_tls_verify)

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        return pull_verify(ctx, tag, commit)

    def reacquire(self, ctx, rec: dict) -> None:
        if not rec.get("ref"):
            raise ImageMissing(f"local image {rec['id'][:19]} is gone and cannot be pulled again")
        tls = [] if ctx.conf.registry_tls_verify else ["--tls-verify=false"]
        ctx.sh.run(["podman", "pull", "-q", *tls, rec["ref"]], timeout=3600)
```

`images.ensure` becomes `if not exists(ctx, rec): ctx.app.reacquire(ctx, rec)`. `images.retag` and `images.prune` replace `LOCAL` with `ctx.app.local_image`; keep `LOCAL` as a module constant only if a test imports it, otherwise delete it. `check.check` and `check.rehearse_tag` use `ctx.app.releases(ctx)` and `ctx.app.published(ctx, git)`; `rehearse.rehearse` calls `ctx.app.fetch(ctx, tag, commit)` in place of `pull_verify`. Update the monkeypatch targets in `tests/test_check.py` (`check.git_release_tags` → patch `ctx.app.releases`/`published` with `monkeypatch.setattr(type(ctx.app), "releases", …)` or a small fake app object) so the assertions stay the same.

- [ ] **Step 4: Run all unit tests.** Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "Hermes release discovery and image fetch move into its adapter"`

### Task 4: App-neutral service control

**Files:**
- Create: `talaria/service.py` (from `talaria/hermes.py`: `stop`, `start`, `is_active`, `nrestarts`, `post_start_check`)
- Modify: `talaria/apps/hermes.py` (gets `api_status` as `health`, and `config_version` as `data_version`), every importer of `talaria.hermes` (`deploy`, `rollback`, `status`, `setup`, `backup`, `adopt`, `cli`)
- Delete: `talaria/hermes.py`
- Test: rename `tests/test_hermes.py` → `tests/test_service.py`, adjust imports only

**Interfaces:**
- Produces: `service.stop(ctx)`, `service.start(ctx)`, `service.is_active(ctx) -> bool`, `service.post_start_check(ctx) -> str | None` (uses `ctx.app.unit` and `ctx.app.health(ctx)`); `App.health(ctx) -> str | None`; `App.data_version(data_dir) -> int | str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service.py (append)
def test_post_start_check_uses_the_apps_unit_and_health(tmp_path):
    from talaria import service
    from tests.fakes import make_test_ctx
    ctx = make_test_ctx(tmp_path)
    ctx.conf.settle_seconds = 5
    ctx.sh.on("systemctl", "--user", "is-active", out="active\n")
    ctx.sh.on("systemctl", "--user", "show", out="0\n")

    class FakeApp:
        unit = "x.service"
        def health(self, c):
            return "not yet"
    ctx.app = FakeApp()
    assert service.post_start_check(ctx) == "not yet"
    assert ctx.sh.called("systemctl", "--user", "is-active", "x.service")
```

- [ ] **Step 2: Run to verify it fails** — `ModuleNotFoundError: talaria.service`.
- [ ] **Step 3: Implement** — move the functions verbatim; replace the literal `UNIT` with `ctx.app.unit`, `api_status(ctx)` with `ctx.app.health(ctx)` and the messages `"hermes.service is not active"` / `"hermes.service restarted"` with `f"{ctx.app.unit} is not active"` / `f"{ctx.app.unit} restarted"` (same text for Hermes). Move `api_status` into `Hermes.health` and `config_version` into `Hermes.data_version` (a `staticmethod`). `backup.create` stores `"cfg_version": ctx.app.data_version(data)` (key name unchanged so old sidecars stay readable).
- [ ] **Step 4: Run all unit tests.** Expected: PASS, golden tests untouched.
- [ ] **Step 5: Commit** — `git commit -am "service.py: app-neutral start/stop/health; Hermes health and data version in its adapter"`

### Task 5: Rehearsal steps behind the adapter

**Files:**
- Modify: `talaria/rehearse.py` (keep `copy_data`, `_sqlite_copy`, staging, `Transient`/`Permanent`, the generic `rehearse()` frame and `candidate_message` header/buttons), `talaria/apps/hermes.py` (gets the helper/doctor steps, `_doctor_changes`, `_fmt_diff`, `_diff_title` and the Hermes report lines)
- Test: `tests/test_rehearse.py` (imports only), `tests/test_apps.py`

**Interfaces:**
- Produces: `App.rehearse(ctx, st, image, copy: Path, stage: Path) -> dict` (the report; raises `rehearse.Permanent`), `App.report_lines(ctx, report) -> tuple[list[str], list]` (plain lines, untrusted titled blocks), `App.pending_extra(report) -> dict` (fields stored in `st["pending"]`; Hermes: `{"cfg_after": report["cfg_after"]}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_apps.py (append)
def test_generic_rehearse_delegates_to_the_app(tmp_path, monkeypatch):
    from talaria import rehearse, state
    from tests.fakes import make_test_ctx
    ctx = make_test_ctx(tmp_path)
    (ctx.conf.data_dir / "f").write_text("x")
    seen = {}

    class FakeApp(type(ctx.app)):
        def fetch(self, c, tag, commit):
            return {"tag": tag, "id": "sha256:i", "digest": "d"}
        def rehearse(self, c, st, image, copy, stage):
            seen["copy"] = (copy / "f").read_text()
            return {"tag": image["tag"], "digest": "d", "x": 1}
        def report_lines(self, c, report):
            return ["Line"], [("Block", "body")]
        def pending_extra(self, report):
            return {"x": report["x"]}
    ctx.app = FakeApp()
    st = state.load(ctx.paths)
    rehearse.rehearse(ctx, st, "v2026.9.24", "c")
    assert seen["copy"] == "x" and st["pending"]["x"] == 1
    m = ctx.notify.sent[-1]
    assert "Line" in m.text and m.untrusted == [("Block", "body")]
    assert m.buttons == [[("Approve v2026.9.24", "ap:v2026.9.24"), ("Reject", "rj:v2026.9.24")]]
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — the generic frame keeps: fetch (mapping `RevisionMismatch` → `Permanent`, `CommandError` → `Transient`), the space check, the staging wipe, `copy_data`, `try/finally rmtree(stage)`, then `report = ctx.app.rehearse(ctx, st, image, copy, stage)`, `st["pending"] = {"tag", "image", "report", **ctx.app.pending_extra(report)}` and the message. `candidate_message(ctx, st, report, replaced)` builds the first line (`f"{ctx.app.title} {tag} is ready to deploy (current {cur}). The rehearsal on a copy passed."`), the release-notes line for `https://github.com/` repos, then `ctx.app.report_lines`, then `Replaces the pending …`, commands and buttons. The Hermes adapter's `rehearse` is today's body from the symlink-safe `config.yaml` copy through the `confdiff.py` call, returning today's report dict; `report_lines` returns today's config/db lines and titled blocks in the same order. The existing exact-text tests in `tests/test_rehearse.py` must pass unchanged.
- [ ] **Step 4: Run all unit tests.** Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "Rehearsal: generic frame, Hermes steps in the adapter"`

### Task 6: Deploy hooks

**Files:**
- Modify: `talaria/deploy.py:73-84`, `talaria/apps/base.py`, `talaria/apps/hermes.py`
- Test: `tests/test_deploy.py`

**Interfaces:**
- Produces: `App.before_start(ctx, pending) -> tuple[str | None, list]` (failure reason, untrusted details; runs while the marker is set; Hermes: run `migrate.py` in place and compare with `pending["cfg_after"]`), `App.after_start(ctx, pending) -> str | None` (extra check after `post_start_check`; Hermes: `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deploy.py (append)
def test_deploy_runs_the_apps_hooks_in_order(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    order = []
    real_before = type(ctx.app).before_start
    monkeypatch.setattr(type(ctx.app), "before_start",
                        lambda self, c, p: (order.append(("before", marker.read(c.paths) is not None)),
                                            real_before(self, c, p))[1])
    monkeypatch.setattr(type(ctx.app), "after_start",
                        lambda self, c, p: order.append(("after", None)) or None)
    deploy.deploy(ctx, "v2026.9.24")
    assert order == [("before", True), ("after", None)]


def test_after_start_failure_rolls_back(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(type(ctx.app), "after_start", lambda self, c, p: "schema mismatch")
    deploy.deploy(ctx, "v2026.9.24")
    assert load(ctx)["current"] == CUR
    assert "failed during deploy: schema mismatch" in ctx.notify.sent[-1].text
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — replace the `migrate.py` block in `deploy()` by:

```python
    try:
        reason, details = ctx.app.before_start(ctx, p)
    except Exception as e:
        reason, details = f"migration could not run: {e}", []
    if reason:
        return _fail(ctx, tag, reason, b.id, old, details)
```

and after `reason = service.post_start_check(ctx)` add `reason = reason or ctx.app.after_start(ctx, p)`. Move the migrate call and the two failure texts (`"migration failed: …"`, `"config version …, expected … from the rehearsal"`) into `Hermes.before_start` unchanged. Replace `"Deployed Hermes {tag}."` with `f"Deployed {ctx.app.title} {tag}."`.
- [ ] **Step 4: Run all unit tests.** Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "Deploy: app hooks before and after start"`

### Task 7: Units per app, `add_hosts`

**Files:**
- Modify: `talaria/units.py`, `talaria/apps/base.py`, `talaria/apps/hermes.py`, `talaria/conf.py` (`add_hosts` list key), `templates/hermes.container`, `templates/talaria-check.service`, `templates/talaria-telegram.service`
- Test: `tests/test_units.py`

**Interfaces:**
- Produces: `App.quadlet_vars(ctx) -> dict` (template variables beyond the shared ones); shared variables `data_dir, app_env, bind_ip, port, marker, wait_tailscale, add_hosts, title`; template file name `templates/<app.quadlet_file>`; `Conf.add_hosts: tuple = ()` read from `add_hosts = name:ip name2:ip2`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_units.py (append)
def test_add_hosts_rendered_as_addhost_lines(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.conf.add_hosts = ("clawvisor:100.64.0.1",)
    q = units.render_quadlet(ctx)
    assert "\nAddHost=clawvisor:100.64.0.1\n" in q


@pytest.mark.parametrize("bad", ["clawvisor", "a b:1.2.3.4", "x:not-an-ip", "x:1.2.3.4\nExec=sh"])
def test_bad_add_hosts_refused(tmp_path, bad):
    ctx = make_test_ctx(tmp_path)
    ctx.conf.add_hosts = (bad,)
    with pytest.raises(ValueError, match="add_hosts"):
        units.render_quadlet(ctx)


def test_hermes_quadlet_unchanged_without_add_hosts(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert "AddHost" not in units.render_quadlet(ctx)
```

The existing exact-render tests of `hermes.container` and the unit files stay unchanged and must still pass.

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** — in `units.render_quadlet` validate each entry with `re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,62}:\d{1,3}(\.\d{1,3}){3}", h)` (else `ValueError(f"bad add_hosts entry: {h!r}")`) and pass `add_hosts="".join(f"AddHost={h}\n" for h in hosts)`; render `templates/{ctx.app.quadlet_file}` with the shared variables plus `ctx.app.quadlet_vars(ctx)`. In `templates/hermes.container` put `${add_hosts}` on its own position directly before `Exec=gateway run` **without** a trailing newline of its own, so an empty value leaves the file byte-identical:

```
PublishPort=${bind_ip}:${port}:9119
${add_hosts}Exec=gateway run
```

Unit templates: replace the literal `Hermes` in `Description=` with `${title}`; `render_units` passes `title=ctx.app.title`.
- [ ] **Step 4: Run all unit tests.** Expected: PASS, including the byte-exact Hermes renders.
- [ ] **Step 5: Commit** — `git commit -am "Units: per-app templates, add_hosts setting"`

### Task 8: Setup through the adapter

**Files:**
- Modify: `talaria/setup.py` (`_fresh_image`, `service_phase` secrets/ready text, PLAN texts), `talaria/apps/base.py`, `talaria/apps/hermes.py`
- Test: `tests/test_setup.py`, `tests/test_setup_golden.py`, `tests/test_setup_service_golden.py` (must pass unchanged)

**Interfaces:**
- Produces: `App.can_adopt: bool` (Hermes `True`), `App.prepare(ctx) -> list[str]` (creates missing secrets, returns `OK:` lines to print; never overwrites), `App.ready_text(ctx) -> str` (the final OK line), `App.initial_conf(ctx) -> str` (the first `talaria.conf` contents).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup.py (append)
def test_prepare_never_overwrites_the_dashboard_password(tmp_path):
    from talaria.conf import parse_kv, write_env_value
    ctx = make_test_ctx(tmp_path)
    write_env_value(ctx.paths.app_env, "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "keep")
    assert ctx.app.prepare(ctx) == []
    assert parse_kv(ctx.paths.app_env.read_text())["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"] == "keep"
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — move the dashboard-password block into `Hermes.prepare` (returns `[f"dashboard password generated in {ctx.paths.app_env} (user admin)"]` when it generated one), the final dashboard line into `Hermes.ready_text`, and `"# Talaria settings; see README.\ndata_dir = ~/hermes-data\n"` into `Hermes.initial_conf`. `service_phase` refuses `--adopt` and skips detection when `not ctx.app.can_adopt` (`say("STOP", f"adopting is not supported for {ctx.app.title}")`). The PLAN lines say `ctx.app.title` instead of `Hermes`. Golden outputs stay identical for Hermes.
- [ ] **Step 4: Run all unit tests.** Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "Setup: app hooks for secrets, first config and the final line"`

### Task 9: Texts use the app title; status shows the data size

**Files:**
- Modify: `talaria/status.py`, `talaria/rollback.py`, `talaria/deploy.py`, `talaria/check.py`, `talaria/telegram.py` (MENU descriptions), `README.md`
- Test: `tests/test_status.py`, a new exact-text test per module for a fake app titled `"Demo"`

**Interfaces:**
- Consumes: `ctx.app.title`, `disk.dir_size`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_status.py (append)
def test_status_names_the_app_and_data_size(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    monkeypatch.setattr(type(ctx.app), "title", "Demo")
    ctx.sh.on("systemctl", "--user", "is-active", out="active\n")
    monkeypatch.setattr(status.disk, "free_bytes", lambda p: 19 * status.disk.GB)
    monkeypatch.setattr(status.disk, "dir_size", lambda p: int(0.25 * status.disk.GB))
    text = status.status_text(ctx)
    assert text.splitlines()[0].startswith("Demo unknown")
    assert "19.0 GB free, data 0.25 GB." in text
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — replace every user-facing literal `Hermes` in core modules with `{ctx.app.title}`. The second status line becomes `f"{free:.1f} GB free, data {size:.2f} GB."`; update the one exact-text test of the status line accordingly (this is the only intended visible change in Part A; note it in the release notes). Grep afterwards: `grep -n '"[^"]*Hermes' talaria/*.py` must list only `talaria/apps/hermes.py` and `talaria/adopt.py` (adoption is Hermes-only).
- [ ] **Step 4: Run all unit tests.** Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "Messages name the app; /status shows the data size"`

### Task 10: Release v0.3.0

**Files:**
- Modify: `pyproject.toml`, `talaria/__init__.py` (0.3.0), `uv.lock`, `docs/mutation-report.md`, `README.md` (architecture paragraph: core + adapters)
- Test: `tests/test_upgrade.py` (new)

- [ ] **Step 1: Write the upgrade test** — state and config written by v0.2.5 must load and act unchanged:

```python
# tests/test_upgrade.py
import json

from talaria import rollback, state, units
from tests.fakes import make_test_ctx


def test_v025_install_without_app_key_keeps_working(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.paths.conf_file.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.conf_file.write_text("data_dir = ~/hermes-data\n")
    st = state.load(ctx.paths)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": "b", "changed": True,
                "started": "2026-09-27T04:00:00+00:00"}
    state.save(ctx.paths, st)
    assert ctx.app.name == "hermes"
    assert rollback.interrupted(ctx, state.load(ctx.paths)).startswith("an interrupted deploy")
    assert units.render_quadlet(ctx).startswith("# Managed by Talaria.")
```

- [ ] **Step 2: Run the full unit suite, then mutation testing**

Run: `timeout 3500 systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 uv run mutmut run` then `uv run mutmut export-cicd-stats && uv run python tests/mutation_score.py`
Expected: score ≥ 95 %; `uv run mutmut results | grep -E 'rollback\.|restore\.|marker\.|deploy\.'` lists nothing but the two crash-hook mutants. Kill or justify any new survivor in `talaria/apps/` and `talaria/service.py`.

- [ ] **Step 3: Byte-identity check on this host before tagging** (read-only):

```bash
sudo -u hermes -H cat /home/hermes/.config/containers/systemd/hermes.container > /tmp/claude-1000/-home-max/f5a8438f-7c89-40ba-bd32-2fe5f601926b/scratchpad/q-before
```

After `self-update`, `setup` must report no unit change; compare the file again with `cmp`. Expected: identical.

- [ ] **Step 4: Push the branch, wait for CI (unit 3.10/3.13 + e2e), merge `--ff-only`, tag `v0.3.0`, `self-update v0.3.0` here, verify `talaria status`, `systemctl --user is-active hermes talaria-telegram talaria-check.timer`.**
- [ ] **Step 5: Commit** the version bump and report before tagging — `git commit -am "Version 0.3.0: core and app adapters"`

---

# Part B — Clawvisor adapter (release v0.4.0)

### Task 11: Clawvisor releases and image build

**Files:**
- Create: `talaria/apps/clawvisor.py`, `templates/clawvisor.Containerfile`
- Modify: `talaria/apps/__init__.py` (`NAMES = ("hermes", "clawvisor")`), `talaria/ctx.py` (`Ctx.download`)
- Test: `tests/test_clawvisor.py`

**Interfaces:**
- Consumes: `upstream._ls_remote(sh, repo)`, `ctx.sh`, `ctx.paths.staging`.
- Produces: `ctx.download(url: str, dest: Path, max_bytes: int) -> int` (HTTP status; 0 on network error; follows redirects; aborts above `max_bytes`); `Clawvisor.releases`, `.published` (returns all given tags: availability is checked in `fetch`), `.fetch(ctx, tag, commit) -> {"tag","id","digest","ref": f"build:{tag}","commit"}` where `digest` is the binary's SHA-256; `.reacquire(ctx, rec)` rebuilds from `rec["tag"]`/`rec["commit"]`; `ASSETS_NOT_READY` (`rehearse.Transient` message prefix).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_clawvisor.py
import hashlib

import pytest

from talaria import apps, rehearse
from talaria.images import RevisionMismatch
from tests.fakes import make_test_ctx

BIN = b"\x7fELF fake clawvisor"
SUM = hashlib.sha256(BIN).hexdigest()


def cctx(tmp_path, files):
    ctx = make_test_ctx(tmp_path, app="clawvisor")

    def download(url, dest, max_bytes):
        name = url.rsplit("/", 1)[1]
        if name not in files:
            return 404
        dest.write_bytes(files[name])
        return 200
    ctx.download = download
    ctx.sh.on("uname", "-m", out="x86_64\n")
    ctx.sh.on("podman", "build", out="sha256:built\n")
    ctx.sh.on("podman", "run", out="clawvisor-server 0.9.10\n")
    return ctx


def test_releases_are_plain_semver_tags(tmp_path, monkeypatch):
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    monkeypatch.setattr(cv, "_ls_remote", lambda sh, repo: {
        "v0.9.10": "a", "v0.9.11-rc1": "b", "latest": "c", "v0.9.9": "d"})
    assert ctx.app.releases(ctx) == {"v0.9.10": "a", "v0.9.9": "d"}


def test_fetch_verifies_checksum_and_builds(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    rec = ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert rec == {"tag": "v0.9.10", "id": "sha256:built", "digest": f"sha256:{SUM}",
                   "ref": "build:v0.9.10", "commit": "abc"}
    build = ctx.sh.called("podman", "build")[0]
    assert "--label" in build and "org.opencontainers.image.revision=abc" in build
    assert "localhost/clawvisor:v0.9.10" in build


def test_checksum_mismatch_is_permanent(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{'0' * 64}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    with pytest.raises(RevisionMismatch, match="checksum"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert ctx.sh.called("podman", "build") == []


def test_missing_assets_are_transient(tmp_path):
    ctx = cctx(tmp_path, {})
    with pytest.raises(rehearse.Transient, match="release assets of v0.9.10 are not published yet"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")


def test_binary_version_must_match_the_tag(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    ctx.sh.on("podman", "run", out="clawvisor-server 0.9.9\n")
    with pytest.raises(RevisionMismatch, match="reports version 0.9.9"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")
```

- [ ] **Step 2: Run to verify they fail** — `ValueError: unknown app: 'clawvisor'`.
- [ ] **Step 3: Implement**

```
# templates/clawvisor.Containerfile
FROM ${base}
COPY --chmod=0755 clawvisor-server /clawvisor-server
EXPOSE 25297
USER 65532:65532
ENTRYPOINT ["/clawvisor-server"]
CMD ["server"]
```

```python
# talaria/apps/clawvisor.py
from __future__ import annotations

import hashlib
import re
import shutil
from string import Template

from talaria.apps.base import App
from talaria.images import RevisionMismatch
from talaria.state import ensure_dir
from talaria.upstream import _ls_remote

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BASE = ("gcr.io/distroless/static-debian12@sha256:"
        "afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab")
ARCH = {"x86_64": "amd64", "aarch64": "arm64"}
MAX_BINARY = 300 * 1024 * 1024


class Clawvisor(App):
    name = "clawvisor"
    title = "Clawvisor"
    unit = "clawvisor.service"
    container = "clawvisor"
    quadlet_file = "clawvisor.container"
    env_file = "clawvisor.env"
    local_image = "localhost/clawvisor"
    default_data_dir = "~/clawvisor-data"
    default_port = 25297
    container_port = 25297
    default_image = ""
    default_repo = "https://github.com/clawvisor/clawvisor"
    min_release = "v0.9.9"
    backup_exclude = ()
    can_adopt = False

    def is_release(self, tag: str) -> bool:
        return bool(TAG.match(tag))

    def tag_key(self, tag: str) -> tuple:
        m = TAG.match(tag)
        if not m:
            raise ValueError(f"not a release tag: {tag!r}")
        return tuple(int(x) for x in m.groups())

    def releases(self, ctx) -> dict[str, str]:
        return {t: c for t, c in _ls_remote(ctx.sh, ctx.conf.hermes_repo).items()
                if self.is_release(t)}

    def published(self, ctx, tags) -> set[str]:
        return set(tags)

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        from talaria.rehearse import Transient
        arch = ARCH[ctx.sh.run(["uname", "-m"]).stdout.strip()]
        asset = f"clawvisor-server-linux-{arch}"
        base = f"{ctx.conf.hermes_repo}/releases/download/{tag}"
        work = ctx.paths.staging / f"build-{tag}"
        shutil.rmtree(work, ignore_errors=True)
        ensure_dir(ctx.paths.staging)
        work.mkdir(mode=0o700)
        try:
            if ctx.download(f"{base}/checksums.txt", work / "checksums.txt", 1 << 20) != 200 \
                    or ctx.download(f"{base}/{asset}", work / "clawvisor-server", MAX_BINARY) != 200:
                raise Transient(f"release assets of {tag} are not published yet")
            want = {l.split()[-1]: l.split()[0] for l in
                    (work / "checksums.txt").read_text().splitlines() if len(l.split()) == 2}
            got = hashlib.sha256((work / "clawvisor-server").read_bytes()).hexdigest()
            if want.get(asset) != got:
                raise RevisionMismatch(f"{tag}: checksum of {asset} does not match checksums.txt")
            (work / "Containerfile").write_text(Template(
                (ctx.paths.templates_dir / "clawvisor.Containerfile").read_text()
            ).substitute(base=BASE))
            iid = ctx.sh.run(["podman", "build", "-q", "--pull=missing",
                              "--label", f"org.opencontainers.image.revision={commit}",
                              "--label", f"org.opencontainers.image.version={tag}",
                              "-t", f"{self.local_image}:{tag}", str(work)],
                             timeout=1800).stdout.strip().splitlines()[-1]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out = ctx.sh.run(["podman", "run", "--rm", "--network=none", iid, "--version"],
                         timeout=120).stdout.split()
        if out[-1:] != [tag[1:]]:
            raise RevisionMismatch(f"{tag}: the binary reports version {out[-1] if out else '?'}")
        return {"tag": tag, "id": iid, "digest": f"sha256:{got}", "ref": f"build:{tag}",
                "commit": commit}

    def reacquire(self, ctx, rec: dict) -> None:
        self.fetch(ctx, rec["tag"], rec.get("commit") or "")


APP = Clawvisor()
```

`Conf.hermes_repo` is the app's upstream repo; Task 13 renames it to `repo` (with `hermes_repo` kept as an accepted alias) and makes its default follow the app. Until Task 13 lands, a Clawvisor install would read the Hermes repo URL, so do not release between Task 11 and Task 13; the Task 11 tests do not depend on the URL (they match asset file names only). `Ctx.download` (in `talaria/ctx.py`):

```python
def download(url: str, dest: Path, max_bytes: int) -> int:
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
            n = 0
            while chunk := r.read(1 << 20):
                n += len(chunk)
                if n > max_bytes:
                    raise OSError(f"{url} is larger than {max_bytes} bytes")
                f.write(chunk)
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError):
        return 0
```

Add `download: Callable[[str, Path, int], int] = download` to `Ctx`. Register `"clawvisor"` in `apps.get`.
- [ ] **Step 4: Run all unit tests.** Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "Clawvisor adapter: releases, checksum-verified local image build"`

### Task 12: Clawvisor rehearsal

**Files:**
- Modify: `talaria/apps/clawvisor.py`
- Test: `tests/test_clawvisor.py`

**Interfaces:**
- Consumes: the generic rehearsal frame (Task 5), `ctx.paths.app_env`.
- Produces: `Clawvisor.rehearse(...)` → `{"tag","digest","before": int, "after": int, "new": [names], "latest": str | None}`; `report_lines`; `pending_extra(report) -> {"migrations_after": report["after"]}`; `migrations(db_path) -> list[str]` (module function, read-only).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_clawvisor.py (append)
import sqlite3


def make_db(path, names):
    c = sqlite3.connect(path)
    c.execute("create table schema_migrations (name text primary key, applied_at text)")
    c.executemany("insert into schema_migrations values (?, 'x')", [(n,) for n in names])
    c.commit()
    c.close()


def test_rehearse_reports_new_migrations(tmp_path):
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql", "054_x.sql"])

    def run_new(argv, input):
        make_db_add = sqlite3.connect(copy / "clawvisor.db")
        make_db_add.execute("insert into schema_migrations values ('055_y.sql', 'x')")
        make_db_add.commit(); make_db_add.close()
        from talaria.shell import Result
        return Result(0, "cid\n", "")
    ctx.sh.on("podman", "rm").on("podman", "run", fn=run_new)
    ctx.sh.on("podman", "exec").on("podman", "stop").on("podman", "logs", out="")
    rep = ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i", "digest": "d"},
                           copy, stage)
    assert (rep["before"], rep["after"], rep["new"]) == (2, 3, ["055_y.sql"])
    run = ctx.sh.called("podman", "run")[0]
    assert "--network=none" in run and f"{copy}:/data:Z" in " ".join(run)
    assert ctx.sh.called("podman", "rm", "-f", "talaria-rehearse")   # leftover removed first
    lines, blocks = ctx.app.report_lines(ctx, rep)
    assert "Database migrations: 2 → 3" in lines
    assert blocks == [("Migrations that will run (1)", "055_y.sql")]


def test_rehearse_not_ready_is_permanent_with_log_tail(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql"])
    ctx.sh.on("podman", "rm").on("podman", "run", out="cid\n").on("podman", "stop")
    ctx.sh.on("podman", "exec", rc=1).on("podman", "logs", out="boom: migration 055 failed\n")
    ctx.conf.settle_seconds = 10
    with pytest.raises(rehearse.Permanent, match="did not become ready") as e:
        ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)
    assert e.value.details == [("Log (last lines)", "boom: migration 055 failed")]
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement**

```python
# talaria/apps/clawvisor.py (add)
import sqlite3

NAME = "talaria-rehearse"
ENV = ["CONFIG_FILE=/data/config.yaml", "SERVER_HOST=0.0.0.0", "DATABASE_DRIVER=sqlite",
       "SQLITE_PATH=/data/clawvisor.db", "VAULT_KEY_FILE=/data/vault.key",
       "CLAWVISOR_RELAY_KEY_FILE=/data/daemon-ed25519.key",
       "CLAWVISOR_RELAY_E2E_KEY_FILE=/data/daemon-x25519.key",
       "CLAWVISOR_DAEMON_DATA_DIR=/data", "CLAWVISOR_CONTAINER=1", "MAX_USERS=1",
       "CLAWVISOR_AUTO_UPDATE_ENABLED=false"]


def migrations(db) -> list[str]:
    if not db.is_file() or db.is_symlink():
        return []
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [r[0] for r in c.execute("select name from schema_migrations order by name")]
    except sqlite3.Error:
        return []
    finally:
        c.close()


class Clawvisor(App):
    ...
    def rehearse(self, ctx, st, image, copy, stage) -> dict:
        from talaria.rehearse import Permanent
        before = migrations(copy / "clawvisor.db")
        ctx.sh.run(["podman", "rm", "-f", NAME], check=False, timeout=120)
        envs = [a for e in ENV for a in ("-e", e)]
        ctx.sh.run(["podman", "run", "-d", "--name", NAME, "--network=none",
                    "--userns=keep-id:uid=65532,gid=65532", "-v", f"{copy}:/data:Z",
                    "--env-file", str(ctx.paths.app_env), *envs, image["id"]], timeout=300)
        try:
            ready, waited = False, 0
            while waited < max(ctx.conf.settle_seconds, 10):
                if ctx.sh.run(["podman", "exec", NAME, "/clawvisor-server", "healthcheck"],
                              check=False, timeout=30).returncode == 0:
                    ready = True
                    break
                ctx.sleep(2)
                waited += 2
            if not ready:
                tail = ctx.sh.run(["podman", "logs", "--tail", "20", NAME], check=False,
                                  timeout=60)
                raise Permanent(f"{image['tag']} did not become ready on the copy",
                                [("Log (last lines)", (tail.stdout + tail.stderr).strip())])
        finally:
            ctx.sh.run(["podman", "stop", "-t", "30", NAME], check=False, timeout=120)
            ctx.sh.run(["podman", "rm", "-f", NAME], check=False, timeout=120)
        after = migrations(copy / "clawvisor.db")
        return {"tag": image["tag"], "digest": image.get("digest"), "before": len(before),
                "after": len(after), "new": [n for n in after if n not in set(before)],
                "latest": after[-1] if after else None}

    def report_lines(self, ctx, report):
        lines = [f"Database migrations: {report['before']} → {report['after']}"]
        blocks = [(f"Migrations that will run ({len(report['new'])})", "\n".join(report["new"]))] \
            if report["new"] else []
        return lines, blocks

    def pending_extra(self, report) -> dict:
        return {"migrations_after": report["after"]}
```

(`--env-file ctx.paths.app_env` gives the rehearsal the real `JWT_SECRET`; the copy carries `vault.key`, so it decrypts like production.)
- [ ] **Step 4: Run all unit tests.** Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "Clawvisor rehearsal: offline start on the copy, new migrations in the offer"`

### Task 13: Clawvisor runtime: Quadlet, secrets, health, verification, login link

**Files:**
- Create: `templates/clawvisor.container`
- Modify: `talaria/apps/clawvisor.py`, `talaria/conf.py` (per-app defaults; `repo` key with `hermes_repo` alias), `talaria/cli.py` (`login-link` command), `README.md`
- Test: `tests/test_clawvisor.py`, `tests/test_conf.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `Clawvisor.health(ctx)`, `.after_start(ctx, pending)`, `.before_start` (returns `(None, [])`), `.data_version(data_dir) -> str | None`, `.prepare(ctx) -> list[str]`, `.ready_text`, `.initial_conf`, `.quadlet_vars`; `talaria login-link` (prints a one-time link with `localhost` replaced by `bind_ip`; refuses when stdout is not a TTY).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_clawvisor.py (append)
def test_prepare_generates_secrets_once(tmp_path):
    from talaria.conf import parse_kv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    out = ctx.app.prepare(ctx)
    env = parse_kv(ctx.paths.app_env.read_text())
    key = (ctx.conf.data_dir / "vault.key").read_text()
    assert len(env["JWT_SECRET"]) == 64 and len(key.strip()) == 44
    assert oct(ctx.paths.app_env.stat().st_mode & 0o777) == "0o600"
    assert oct((ctx.conf.data_dir / "vault.key").stat().st_mode & 0o777) == "0o600"
    assert oct(ctx.conf.data_dir.stat().st_mode & 0o777) == "0o700"
    assert out and all("secret" not in l.lower() or "generated" in l for l in out)
    assert ctx.app.prepare(ctx) == []                       # second run: nothing new
    assert parse_kv(ctx.paths.app_env.read_text())["JWT_SECRET"] == env["JWT_SECRET"]
    assert (ctx.conf.data_dir / "vault.key").read_text() == key


def test_prepare_refuses_existing_db_without_vault_key(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    make_db(ctx.conf.data_dir / "clawvisor.db", ["001_init.sql"])
    with pytest.raises(ValueError, match="vault.key is missing"):
        ctx.app.prepare(ctx)


def test_health_and_after_start(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.conf.dashboard_bind, ctx.conf.tailscale_ip = "tailscale", "100.64.0.1"
    ctx.http_get = lambda url, t: (200, b'{"db":"ok","status":"ok","vault":"ok"}') \
        if url == "http://100.64.0.1:25297/ready" else (0, b"")
    assert ctx.app.health(ctx) is None
    ctx.http_get = lambda url, t: (200, b'{"db":"ok","status":"ok","vault":"locked"}')
    assert ctx.app.health(ctx) == "/ready reports vault locked"
    make_db(ctx.conf.data_dir / "clawvisor.db", ["001_init.sql", "002_x.sql"])
    assert ctx.app.after_start(ctx, {"migrations_after": 2}) is None
    assert ctx.app.after_start(ctx, {"migrations_after": 3}) == (
        "the database has 2 migrations, the rehearsal expected 3")


def test_quadlet(tmp_path):
    from talaria import units
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.conf.dashboard_bind, ctx.conf.tailscale_ip = "tailscale", "100.64.0.1"
    q = units.render_quadlet(ctx)
    for line in ("Image=localhost/clawvisor:current", "ReadOnly=true",
                 "UserNS=keep-id:uid=65532,gid=65532", f"Volume={ctx.conf.data_dir}:/data:Z",
                 "PublishPort=100.64.0.1:25297:25297", "Environment=CLAWVISOR_AUTO_UPDATE_ENABLED=false",
                 f"EnvironmentFile={ctx.paths.app_env}"):
        assert f"\n{line}\n" in q


def test_conf_defaults_follow_the_app(tmp_path):
    from talaria.conf import load_conf
    from talaria.ctx import Paths
    p = Paths(tmp_path)
    p.conf_file.parent.mkdir(parents=True)
    p.conf_file.write_text("app = clawvisor\n")
    c = load_conf(p)
    assert (c.dashboard_port, c.repo, c.min_release, c.backup_exclude) == (
        25297, "https://github.com/clawvisor/clawvisor", "v0.9.9", ())
    assert c.data_dir == tmp_path / "clawvisor-data"
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement**

```
# templates/clawvisor.container
# Managed by Talaria. Re-run `talaria setup` after changing talaria.conf.
[Unit]
Description=Clawvisor (managed by Talaria)
Wants=network-online.target
After=network-online.target

[Container]
ContainerName=clawvisor
Image=localhost/clawvisor:current
Pull=never
ReadOnly=true
UserNS=keep-id:uid=65532,gid=65532
Volume=${data_dir}:/data:Z
Environment=CONFIG_FILE=/data/config.yaml SERVER_HOST=0.0.0.0 DATABASE_DRIVER=sqlite
Environment=SQLITE_PATH=/data/clawvisor.db VAULT_KEY_FILE=/data/vault.key
Environment=CLAWVISOR_RELAY_KEY_FILE=/data/daemon-ed25519.key CLAWVISOR_RELAY_E2E_KEY_FILE=/data/daemon-x25519.key
Environment=CLAWVISOR_DAEMON_DATA_DIR=/data CLAWVISOR_CONTAINER=1 MAX_USERS=1
Environment=CLAWVISOR_AUTO_UPDATE_ENABLED=false
EnvironmentFile=${app_env}
PublishPort=${bind_ip}:${port}:25297
${add_hosts}
[Service]
ExecCondition=/bin/sh -c 'test ! -e "${marker}"'
${wait_tailscale}
Restart=on-failure
RestartSec=30
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

```python
# talaria/apps/clawvisor.py (add)
import base64
import json
import os
import secrets as _secrets

from talaria.conf import parse_kv, write_env_value


class Clawvisor(App):
    ...
    def before_start(self, ctx, pending):
        return None, []

    def health(self, ctx) -> str | None:
        code, body = ctx.http_get(f"http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port}/ready", 5.0)
        if code != 200:
            return f"/ready answered {code or 'nothing'}"
        try:
            r = json.loads(body)
        except ValueError:
            return "/ready did not answer JSON"
        bad = [f"{k} {r.get(k)}" for k in ("status", "db", "vault") if r.get(k) != "ok"]
        return f"/ready reports {', '.join(bad)}" if bad else None

    def after_start(self, ctx, pending) -> str | None:
        n = len(migrations(ctx.conf.data_dir / "clawvisor.db"))
        want = pending.get("migrations_after")
        if want is not None and n != want:
            return f"the database has {n} migrations, the rehearsal expected {want}"
        return None

    @staticmethod
    def data_version(data_dir):
        names = migrations(data_dir / "clawvisor.db")
        return names[-1] if names else None

    def prepare(self, ctx) -> list[str]:
        out = []
        data = ctx.conf.data_dir
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(data, 0o700)
        env = parse_kv(ctx.paths.app_env.read_text()) if ctx.paths.app_env.exists() else {}
        if "JWT_SECRET" not in env:
            write_env_value(ctx.paths.app_env, "JWT_SECRET", _secrets.token_hex(32))
            out.append(f"JWT secret generated in {ctx.paths.app_env}")
        key = data / "vault.key"
        if not key.exists():
            if (data / "clawvisor.db").exists():
                raise ValueError(f"{key} is missing but a database exists; restore the key "
                                 "from a backup instead of generating a new one")
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(base64.b64encode(_secrets.token_bytes(32)).decode() + "\n")
            out.append(f"vault key generated in {key}")
        return out

    def ready_text(self, ctx) -> str:
        return (f"Clawvisor is running at http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port}. "
                f"First login: run `{ctx.paths.bin_link} login-link` in your own terminal")

    def initial_conf(self, ctx) -> str:
        return "# Talaria settings; see README.\napp = clawvisor\ndata_dir = ~/clawvisor-data\n"

    def quadlet_vars(self, ctx) -> dict:
        return {}
```

`conf.load_conf`: read the conf file first, pick the app, then fill unset values from the app's defaults (`data_dir`, `dashboard_port`, `repo`, `image`, `min_release`, `backup_exclude`); accept `hermes_repo` as an alias of `repo` and keep `conf.hermes_repo` as a read-only property returning `repo` until all callers use `repo`. `cli.py`: `login-link` runs `podman exec clawvisor /clawvisor-server dashboard --no-open`, replaces `http://localhost:` with `http://{bind_ip}:` and prints it only if `sys.stdout.isatty()` (else exit 1 with "run this in your own terminal"); for Hermes it exits 1 with "not available for Hermes".
- [ ] **Step 4: Run all unit tests.** Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "Clawvisor runtime: Quadlet, secrets, health, post-deploy check, login link"`

### Task 14: End-to-end and contract tests with the real Clawvisor

**Files:**
- Modify: `talaria/conf.py` (test-only key `release_allow`: space-separated tags; when set, `check` only considers these), `tests/e2e/conftest.py` (a second service user `cvtest`), `.github/workflows/ci.yml` (job `e2e-clawvisor`), `.github/workflows/upstream.yml` (contract job runs both)
- Create: `tests/e2e/test_e2e_clawvisor.py`, `tests/contract/test_contract_clawvisor.py`

**Interfaces:**
- Consumes: everything above; the fake Telegram server from `tests/e2e/fake_telegram.py`.

- [ ] **Step 1: Write the e2e test** (runs on GitHub runners only, `TALARIA_E2E=1`):

```python
# tests/e2e/test_e2e_clawvisor.py
import json

import pytest

from tests.e2e.conftest import as_user, root_block, talaria, wait_for

pytestmark = pytest.mark.e2e
USER = "cvtest"


def test_01_fresh_setup_at_v099(cv_env):
    cv_env.conf("app = clawvisor\ndashboard.bind = loopback\nrelease_allow = v0.9.9\n")
    out = cv_env.setup_until_done()
    assert "Clawvisor is running" in out
    st = json.loads(as_user("cat", ".local/state/talaria/state.json", user=USER).stdout)
    assert st["current"]["tag"] == "v0.9.9"


def test_02_update_offer_lists_migrations_and_deploys(cv_env):
    cv_env.conf_add("release_allow = v0.9.9 v0.9.10\n")
    talaria("check", user=USER)
    offer = cv_env.telegram.last_text()
    assert "Clawvisor v0.9.10 is ready to deploy" in offer
    assert "055_task_pending_expansion_envelope.sql" in offer
    cv_env.telegram.tap("ap:v0.9.10")
    wait_for(lambda: "Deployed Clawvisor v0.9.10." in cv_env.telegram.texts())


def test_03_rollback_restores_v099_and_data(cv_env):
    cv_env.telegram.send("/rollback CONFIRM")
    wait_for(lambda: "Rolled back to Clawvisor v0.9.9." in cv_env.telegram.texts())
    newest = as_user("python3", "-c",
                     "import sqlite3; c = sqlite3.connect('file:clawvisor-data/clawvisor.db"
                     "?mode=ro', uri=True); print(c.execute('select max(name) from "
                     "schema_migrations').fetchone()[0])", user=USER, cwd=f"/home/{USER}")
    assert newest.stdout.strip() == "054_install_context.sql"
```

`as_user` passes `cwd="/"` itself; give it an optional `cwd` keyword (default `"/"`) for this call. Build the `cv_env` fixture in `tests/e2e/conftest.py` from the pieces the existing `env` fixture uses (`root_block` to create the user, the fake Telegram server, `seed_conf`), with the user name as a parameter and these helpers: `conf(text)` writes `talaria.conf`, `conf_add(text)` appends, `setup_until_done()` re-runs setup (running root blocks with `sudo bash -c`) until `DONE` and returns the output, and `telegram` with `texts()`, `last_text()`, `send(text)`, `tap(data)` from `fake_telegram.py`.

- [ ] **Step 2: Write the contract test** — download the newest Clawvisor release through `Clawvisor.fetch` with a real `Ctx`, start it offline on an empty dir with generated secrets, assert `healthcheck` succeeds within 30 s and `schema_migrations` is non-empty; marked `contract`, enabled with `TALARIA_CONTRACT=1`.
- [ ] **Step 3: CI** — add job `e2e-clawvisor` to `ci.yml` mirroring the existing `e2e` job (same pinned actions, `TALARIA_E2E=1 uv run pytest -m e2e tests/e2e/test_e2e_clawvisor.py -v`); add `tests/contract/test_contract_clawvisor.py` to the weekly contract run.
- [ ] **Step 4: Push the branch and get CI green** (unit, `e2e`, `e2e-clawvisor`).
- [ ] **Step 5: Commit** — `git commit -am "e2e and contract tests with the real Clawvisor releases"`

### Task 15: Docs, mutation testing, release v0.4.0

**Files:**
- Modify: `README.md` (section "Clawvisor": what it does, setup with `--user clawvisor`, second bot, `login-link`, `add_hosts` for Hermes, SQLite growth note, OAuth out of scope), `AGENTS.md` (ask which app; never show `login-link` output in chat), `docs/mutation-report.md`, `pyproject.toml`, `talaria/__init__.py` (0.4.0), `uv.lock`

- [ ] **Step 1:** Run full unit tests and the full mutation run (commands in Task 10). Kill or justify survivors in `talaria/apps/clawvisor.py`; `rollback/restore/marker/deploy` stay at zero.
- [ ] **Step 2:** Run an adversarial review of `v0.3.0..HEAD` (read-only subagent, same rules as before) and fix Critical/Important findings with tests.
- [ ] **Step 3:** Update the docs and the mutation report.
- [ ] **Step 4:** Merge `--ff-only`, tag `v0.4.0`, wait for the release workflow, `self-update v0.4.0` on the Hermes install here (Hermes behaviour unchanged).
- [ ] **Step 5: Commit** — `git commit -am "Version 0.4.0: Clawvisor adapter"`

---

# Part C — Rollout on this host (owner present)

### Task 16: Clawvisor service user, bot, setup, first login

- [ ] **Step 1:** Owner creates a second bot with @BotFather and stores the token in `~/.env` as `TELEGRAM_CLAWVISOR_BOT_TOKEN` (never pasted into chat).
- [ ] **Step 2:** From a checkout at `v0.4.0` (https clone): `bin/talaria setup --plan --user clawvisor`, then `bin/talaria setup --user clawvisor`. Owner runs the printed root block (creates `clawvisor`, linger, sudo rule).
- [ ] **Step 3:** As `clawvisor`, write `talaria.conf`: `app = clawvisor`, `dashboard.bind = tailscale`; re-run setup. Store the token without printing it: `grep '^TELEGRAM_CLAWVISOR_BOT_TOKEN=' ~/.env | cut -d= -f2- | sudo -u clawvisor -H ~clawvisor/.local/bin/talaria set-token`. Owner sends the `/pair` code to the new bot.
- [ ] **Step 4:** Setup prints `DONE`; verify `sudo -u clawvisor XDG_RUNTIME_DIR=/run/user/$(id -u clawvisor) systemctl --user is-active clawvisor talaria-telegram talaria-check.timer` and `/ready` with `python3 -c 'import urllib.request; print(urllib.request.urlopen("http://100.83.113.68:25297/ready", timeout=5).read().decode())'` (the owner's settings deny `curl`).
- [ ] **Step 5:** Owner runs `sudo -u clawvisor -H ~clawvisor/.local/bin/talaria login-link` in their own terminal and logs in over the tailnet. Configure the LLM intent-verification key in the Clawvisor dashboard (not through Talaria).

### Task 17: Connect Hermes

- [ ] **Step 1:** In `~hermes/.config/talaria/talaria.conf` add `add_hosts = clawvisor:100.83.113.68`; owner approves the Hermes restart; run `talaria setup` as `hermes`; check the rendered Quadlet contains `AddHost=clawvisor:100.83.113.68` and Hermes is healthy.
- [ ] **Step 2:** Follow Clawvisor's `docs/INTEGRATE_HERMES.md` with `CLAWVISOR_URL=http://clawvisor:25297`: add the MCP server entry to Hermes' config (through the Hermes dashboard or `hermes` CLI in the container), complete the MCP OAuth in the browser.
- [ ] **Step 3:** Verify from Hermes: ask it to list its Clawvisor tools; a gated request shows up as an approval in Clawvisor.
- [ ] **Step 4:** Update `/home/max/CLAUDE.md` (Clawvisor section: user, units, URL, bot, login-link, data dir) and the project memory.
