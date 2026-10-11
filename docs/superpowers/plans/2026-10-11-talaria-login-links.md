# Talaria v0.7.0 login links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner gets a gbrain or Clawvisor login link from the Talaria bot (`/login`), and the gbrain onboarding documents a 443 `/admin` redirect so consent and login links work from the public address.

**Architecture:** Each app type gets a `login_link` hook that returns display lines (never prints). A new `talaria op login-link` emits them as one `link` JSON line on stdout only. The bot gates on the app's Talaria version, sends the link with previews off, and 5 minutes later replaces the message with an "expired" notice. A stdlib root helper adds/undoes the Tailscale serve redirect.

**Tech Stack:** Python ≥ 3.10 stdlib only; pytest via uv; Telegram Bot API (`sendMessage`, `editMessageText`); podman; Tailscale LocalAPI.

**Spec:** `docs/superpowers/specs/2026-10-11-talaria-login-links-design.md`

## Global Constraints

- Stdlib only; Python ≥ 3.10. Match surrounding style; comments only where the why is non-obvious. Existing `# pragma: no mutate …` stay unless their line is removed.
- No new `talaria.conf` / `hub.conf` keys. `PROTOCOL` stays 1. Existing op subcommands keep their arguments.
- A login link (any URL from `login_link`) must never reach stderr, the journal, history, state or a log line — in the app, the op, the hub or the bot. Inside `op`, `print()` goes to stderr (`contextlib.redirect_stdout(sys.stderr)` in `op.main`) and the hub copies op stderr to its journal, so app code returns lines and never prints them.
- The owner credential (`GBRAIN_ADMIN_BOOTSTRAP_TOKEN`) never leaves the app account; error text from gbrain goes through `gbrain._redact` before any message or log.
- Public repo: no real host names/IPs/accounts; placeholders only (`<host>.<tailnet>.ts.net`, `100.64.0.1`).
- Never run anything against the live host (`bin/talaria setup`, `sudo`, `systemctl`, `podman`, `tailscale`).
- Tests, always capped:
  `cd /home/max/talaria && timeout 600 systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 uv run pytest -q -p no:cacheprovider tests --ignore=tests/e2e --ignore=tests/contract`
  (one file: replace `tests --ignore…` with the path).
- Version 0.7.0 (bumped in Task 1 so later tasks' fakes report it).

## Review Focus

- The URL leaking to stderr/journal through an error path (gbrain CLI fails after printing JSON; Clawvisor prints a link and exits non-zero) — Task 1 tests `test_gbrain_login_link_failure_never_logs_stdout`, Task 2 test `test_login_link_never_writes_the_url_to_stderr`.
- An owner pasting the whole consent URL including `#oauth-consent` or extra query params — Task 3 test `test_login_accepts_the_consent_url`.
- A bot restart or Telegram error while an expiry edit is pending — Task 3 test `test_expiry_edit_failure_is_ignored_and_dropped`.
- An app on Talaria 0.6.x during the rollout — Task 3 test `test_login_gate_on_old_app_version`.
- The helper run twice, or on a host whose 443 has no web handlers — Task 4 tests `test_refuses_when_admin_exists`, `test_refuses_without_443_handlers`.

---

### Task 1: App hook `login_link` (Clawvisor refactor, gbrain), version 0.7.0

**Files:**
- Modify: `talaria/apps/base.py` (new `LoginRefused`, `has_login_link`, `login_link`)
- Modify: `talaria/apps/clawvisor.py` (move link logic from `cli.login_link`)
- Modify: `talaria/apps/gbrain.py` (new `login_link`)
- Modify: `talaria/cli.py:97-157` (`login_link` becomes TTY check + print of the hook's lines)
- Modify: `talaria/__init__.py`, `pyproject.toml`, `uv.lock` (0.6.1 → 0.7.0; in `uv.lock` only the `talaria` package's own `version` line)
- Test: `tests/test_cli.py` (existing login-link tests must pass unchanged), `tests/test_clawvisor.py`, `tests/test_gbrain.py`

**Interfaces:**
- Produces (in `talaria/apps/base.py`):
  ```python
  class LoginRefused(Exception):
      """A login link cannot be made; str(e) is the owner-facing reason (no secrets)."""

  class App:
      has_login_link = False
      def login_link(self, ctx, oauth_request: str | None = None) -> list[str]:
          """Lines to show the owner; at least one contains the link. Never prints."""
          raise LoginRefused(f"not available for {self.title}")
  ```
- Clawvisor and gbrain set `has_login_link = True`.
- `OAUTH_REQUEST_RE = re.compile(r"^[a-f0-9]{64}$")` in `talaria/apps/base.py` (gbrain's own format; used by op and bot).

- [ ] **Step 1: Write failing tests.**

`tests/test_clawvisor.py` (add):
```python
from talaria.apps.base import LoginRefused

def test_login_link_hook_returns_the_lines(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("systemctl", out="active\n")
    ctx.sh.on("podman", "exec", out="Open this link: http://localhost:25297/login?token=abc\n")
    lines = ctx.app.login_link(ctx)
    assert lines == ["Open this link: http://127.0.0.1:25297/login?token=abc",
                     "Through an SSH tunnel, open it as http://127.0.0.1:25297/... instead "
                     "(rest of the link unchanged)."]

def test_login_link_hook_refuses_an_oauth_request(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    with pytest.raises(LoginRefused, match="only gbrain"):
        ctx.app.login_link(ctx, "a" * 64)

def test_login_link_hook_refuses_when_not_running(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("systemctl", out="inactive\n", rc=3)
    with pytest.raises(LoginRefused, match="Clawvisor is not running"):
        ctx.app.login_link(ctx)
    assert not ctx.sh.called("podman", "exec")

def test_login_link_hook_never_prints(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("systemctl", out="active\n")
    ctx.sh.on("podman", "exec", out="Open: http://localhost:25297/x?token=abc\n")
    ctx.app.login_link(ctx)
    out = capsys.readouterr()
    assert "token=abc" not in out.out + out.err
```
(Check how `FakeShell.on` matches `systemctl --user is-active clawvisor.service`; `service.is_active` runs `systemctl --user is-active <unit>` through `ctx.sh`. Adapt the prefix to what `FakeShell` needs; the existing `tests/test_service.py` shows the form.)

`tests/test_gbrain.py` (add):
```python
import json
from talaria.apps.base import LoginRefused

LINK = "https://host.example.ts.net/admin/auth/" + "b" * 64

def _link_json(url=LINK, expires=300):
    return json.dumps({"status": "login_link", "url": url, "expires_in": expires,
                       "next_action": "Open this single-use link"}) + "\n"

def test_gbrain_login_link_runs_the_cli_in_the_container(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.sh.on("systemctl", out="active\n")
    ctx.sh.on("podman", "exec", out=_link_json())
    assert ctx.app.login_link(ctx) == [f"Open this link: {LINK}",
                                       "Single use, expires in 5 minutes."]
    assert ctx.sh.called("podman", "exec", "gbrain", "/usr/local/bin/gbrain", "mcp", "admin",
                         "login-link", "--url",
                         f"http://127.0.0.1:{ctx.app.container_port}/mcp", "--json")

def test_gbrain_login_link_passes_the_oauth_request(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.sh.on("systemctl", out="active\n")
    rid = "c" * 64
    ctx.sh.on("podman", "exec", out=_link_json(f"{LINK}?oauth_request={rid}"))
    ctx.app.login_link(ctx, rid)
    assert ctx.sh.called("podman", "exec", "gbrain", "/usr/local/bin/gbrain", "mcp", "admin",
                         "login-link", "--url", f"http://127.0.0.1:{ctx.app.container_port}/mcp",
                         "--json", "--oauth-request", rid)

@pytest.mark.parametrize("out", ["", "not json\n", json.dumps({"status": "error"}) + "\n",
                                 _link_json(url="http://x/admin/auth/1")])
def test_gbrain_login_link_bad_output_is_refused(tmp_path, monkeypatch, out):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.sh.on("systemctl", out="active\n")
    ctx.sh.on("podman", "exec", out=out)
    with pytest.raises(LoginRefused, match="Could not get a login link from gbrain"):
        ctx.app.login_link(ctx)

def test_gbrain_login_link_failure_never_logs_stdout(tmp_path, monkeypatch, capsys):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.sh.on("systemctl", out="active\n")
    ctx.sh.on("podman", "exec", out=_link_json(), err="boom", rc=1)
    with pytest.raises(LoginRefused) as e:
        ctx.app.login_link(ctx)
    out = capsys.readouterr()
    assert LINK not in out.out + out.err + str(e.value)
    assert "exit 1" in out.err and "boom" in out.err

def test_gbrain_login_link_redacts_the_token_in_errors(tmp_path, monkeypatch, capsys):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.paths.app_env.write_text("GBRAIN_ADMIN_BOOTSTRAP_TOKEN=" + "t" * 40 + "\n")
    ctx.sh.on("systemctl", out="active\n")
    ctx.sh.on("podman", "exec", err="bad token " + "t" * 40, rc=1)
    with pytest.raises(LoginRefused):
        ctx.app.login_link(ctx)
    assert "t" * 40 not in capsys.readouterr().err

def test_gbrain_login_link_refuses_when_not_running(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.sh.on("systemctl", out="inactive\n", rc=3)
    with pytest.raises(LoginRefused, match="gbrain is not running"):
        ctx.app.login_link(ctx)

def test_hermes_has_no_login_link(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert not ctx.app.has_login_link
    with pytest.raises(LoginRefused, match="not available for Hermes"):
        ctx.app.login_link(ctx)
```
(If `gctx` writes `app_env` itself, adapt the redaction test to use its file. If `FakeShell.on` does not take `err=`/`rc=`, use its existing keyword names.)

- [ ] **Step 2: Run them; they fail** (`AttributeError`/`ImportError`).

- [ ] **Step 3: Implement.**

`talaria/apps/base.py`: add `import re`, `LoginRefused`, `OAUTH_REQUEST_RE`, and on `App` the `has_login_link = False` attribute and the `login_link` default shown in Interfaces.

`talaria/apps/clawvisor.py`: move the body of `cli.login_link` after the TTY check into `Clawvisor.login_link(self, ctx, oauth_request=None) -> list[str]`:
- `if oauth_request: raise LoginRefused("only gbrain takes an OAuth request")`
- `if not service.is_active(ctx): raise LoginRefused("Clawvisor is not running")`
- every `print("STOP: …", file=sys.stderr); return 1` becomes `raise LoginRefused("<same text without 'STOP: '>")` (keep the wording: "could not prepare the login session", "podman exec timed out", "could not get a login link from Clawvisor");
- the three `print(...)` of link lines become `lines.append(...)` with the same strings; return `lines`.
- Keep `os.chdir("/")`, the O_NOFOLLOW session-file handling, and the `finally` cleanup exactly as they are.

`talaria/cli.py` `login_link(ctx)`:
```python
def login_link(ctx) -> int:
    if not ctx.app.has_login_link:
        print(f"not available for {ctx.app.title}", file=sys.stderr)
        return 1
    if not sys.stdout.isatty():
        print("run this in your own terminal", file=sys.stderr)
        return 1
    try:
        lines = ctx.app.login_link(ctx)
    except LoginRefused as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0
```
The existing `tests/test_cli.py` login-link tests must pass unchanged, except: the CLI now checks `is_active` first, so add `ctx.sh.on("systemctl", out="active\n")` (or the matching form) to those tests' setup — no other edits to their assertions. Remove now-unused imports from `cli.py`.

`talaria/apps/gbrain.py`:
```python
    has_login_link = True

    def login_link(self, ctx, oauth_request=None) -> list[str]:
        if not service.is_active(ctx):
            raise LoginRefused("gbrain is not running")
        argv = ["podman", "exec", self.container, "/usr/local/bin/gbrain", "mcp", "admin",
                "login-link", "--url", f"http://127.0.0.1:{self.container_port}/mcp", "--json"]
        if oauth_request:
            argv += ["--oauth-request", oauth_request]
        fail = "Could not get a login link from gbrain; details in the journal"
        try:
            r = ctx.sh.run(argv, check=False, timeout=30)
        except subprocess.TimeoutExpired:
            raise LoginRefused(fail) from None
        if r.returncode != 0:   # stdout may hold a link: never log it
            print(f"[talaria] gbrain login-link: exit {r.returncode}: "
                  f"{_redact(ctx, (r.stderr or '').strip()[-500:])}", file=sys.stderr)
            raise LoginRefused(fail)
        d = None
        for line in reversed(r.stdout.splitlines()):
            if line.startswith("{"):
                try:
                    d = json.loads(line)
                except ValueError:
                    pass
                break
        url = d.get("url") if isinstance(d, dict) and d.get("status") == "login_link" else None
        if not isinstance(url, str) or not url.startswith("https://") or "/admin/auth/" not in url:
            print("[talaria] gbrain login-link: no login link in its output", file=sys.stderr)
            raise LoginRefused(fail)
        minutes = max(1, int(d.get("expires_in") or 300) // 60)
        return [f"Open this link: {url}", f"Single use, expires in {minutes} minutes."]
```
(Import `service`, `json`, `subprocess`, `sys`, `LoginRefused` as needed; check which are already imported. `self.container_port` is the class attribute the Quadlet uses.)

Version: `__version__ = "0.7.0"`, `pyproject.toml` `version = "0.7.0"`, `uv.lock` talaria entry `version = "0.7.0"`.

- [ ] **Step 4: Run the three test files, then the full suite; all pass.**
- [ ] **Step 5: Commit** — "Apps: login_link hook for Clawvisor and gbrain; v0.7.0".

---

### Task 2: `talaria op login-link`

**Files:**
- Modify: `talaria/op.py` (OPS, parser, `_run`)
- Test: `tests/test_op.py`

**Interfaces:**
- Consumes: `App.has_login_link`, `App.login_link(ctx, oauth_request) -> list[str]`, `LoginRefused`, `OAUTH_REQUEST_RE` (Task 1).
- Produces: `talaria op login-link [--oauth-request ID]` writes exactly one JSON line to stdout: either `{"v": 1, "kind": "link", "text": "<lines joined by \n>"}` or `{"v": 1, "kind": "reply", "text": "<refusal>", "buttons": []}`. Exit 0 for both. `--oauth-request` with a value not matching `OAUTH_REQUEST_RE` → argparse error → exit 2, no line.

- [ ] **Step 1: Write failing tests** in `tests/test_op.py` (reuse the `opx` fixture and `lines(buf)` helper; look at `test_status_and_backups_reply` for the call shape):
```python
def test_login_link_emits_one_link_line(opx, monkeypatch):
    monkeypatch.setattr(type(opx.ctx.app), "has_login_link", True, raising=False)
    monkeypatch.setattr(type(opx.ctx.app), "login_link",
                        lambda self, ctx, oauth_request=None: ["Open: https://h/admin/auth/x", "Single use"])
    rc, out = opx.run(["login-link"])
    assert rc == 0
    assert out == [{"v": 1, "kind": "link", "text": "Open: https://h/admin/auth/x\nSingle use"}]

def test_login_link_passes_a_valid_oauth_request(opx, monkeypatch):
    seen = []
    monkeypatch.setattr(type(opx.ctx.app), "has_login_link", True, raising=False)
    monkeypatch.setattr(type(opx.ctx.app), "login_link",
                        lambda self, ctx, oauth_request=None: seen.append(oauth_request) or ["x"])
    opx.run(["login-link", "--oauth-request", "d" * 64])
    assert seen == ["d" * 64]

@pytest.mark.parametrize("bad", ["D" * 64, "d" * 63, "x", "d" * 64 + "&a=b"])
def test_login_link_refuses_a_bad_oauth_request(opx, bad):
    rc, out = opx.run(["login-link", "--oauth-request", bad])
    assert rc == 2 and out == []

def test_login_link_refusal_is_a_reply(opx):      # opx is Hermes
    rc, out = opx.run(["login-link"])
    assert rc == 0
    assert out == [{"v": 1, "kind": "reply", "text": "not available for Hermes", "buttons": []}]

def test_login_link_never_writes_the_url_to_stderr(opx, monkeypatch, capsys):
    monkeypatch.setattr(type(opx.ctx.app), "has_login_link", True, raising=False)
    monkeypatch.setattr(type(opx.ctx.app), "login_link",
                        lambda self, ctx, oauth_request=None: ["https://h/admin/auth/SECRET"])
    opx.run(["login-link"])
    assert "SECRET" not in capsys.readouterr().err
```
(Adapt to the real `opx` fixture: if it is a function `opx(argv) -> (rc, lines)` or provides `ctx` differently, keep the assertions and change only the call shape. Restore monkeypatched class attributes via `monkeypatch`, never by hand.)

- [ ] **Step 2: Run; they fail** (`login-link` not in OPS → exit 2).

- [ ] **Step 3: Implement** in `talaria/op.py`:
```python
OPS = (..., "quadlet", "login-link")

def _oauth_request(v: str) -> str:
    if not OAUTH_REQUEST_RE.match(v):
        raise argparse.ArgumentTypeError("not an OAuth request id")
    return v
# in build_parser:
    add("login-link").add_argument("--oauth-request", type=_oauth_request)
# in _run, before the long-op branch:
    if o == "login-link":
        try:
            lines = ctx.app.login_link(ctx, args.oauth_request)
        except LoginRefused as e:
            return _reply(out, str(e))
        emit(out, "link", text="\n".join(lines))
        return 0
```
Do not route through `JsonNotifier`/`ctx.notify` (it prints message text to stderr). Update the module docstring's op list if it has one.

- [ ] **Step 4: Run `tests/test_op.py`, then the full suite.**
- [ ] **Step 5: Commit** — "op login-link: one link line on stdout, nothing on stderr".

---

### Task 3: Bot `/login`, version gate, expiry notice, buttons

**Files:**
- Modify: `talaria/relay.py` (new `version`, `login`)
- Modify: `talaria/telegram.py` (`/login`, `hub|ln:` button, `/status` buttons, expiry, MENU/HELP)
- Modify: `tests/hubfakes.py` (`hello(..., version=__version__)` parameter)
- Test: `tests/test_telegram.py`, `tests/test_relay.py`

**Interfaces:**
- Consumes: op `login-link` lines (Task 2); `apps.get(name).has_login_link`; `OAUTH_REQUEST_RE`.
- Produces:
  ```python
  # relay.py
  def version(entry) -> str | None:   # the app's Talaria version from hello, None if unknown
  def login(entry, oauth_request: str | None = None) -> tuple[str, bool]:
      """(text, is_link). Unreachable/NoAnswer/failed map to the existing texts with False."""
  # telegram.py
  FIRST_LOGIN_RELEASE = "v0.7.0"
  LINK_TTL = 300   # seconds until the expiry notice replaces the link
  UPDATE_FIRST = "{title} runs Talaria {ver}; update Talaria first: /update v{hub}"
  EXPIRED = "Login link for {title} expired. Send /login {app} for a new one."
  LOGIN_USAGE = "Usage: /login [app] [OAuth request id or consent URL]"
  Bot.expiring: list[tuple[datetime, int, str, str]]   # (due, message_id, title, app)
  Bot.login(args: list[str])            # dispatch target for /login; returns an answer or None
  Bot.send_link(app: str, text: str)    # sendMessage + schedule the expiry edit
  Bot.expire_links()                    # called at the start of poll_once
  ```

Behaviour of `/login` (in `Bot.login`):
1. `supported = [n for n in self.hub.apps if apps.get(n).has_login_link]`.
2. App selection: if `args and args[0] in self.hub.apps` → that app, rest = `args[1:]`. Else if `args and APP_WORD.match(args[0])` → `f"Unknown app: {args[0]}. Apps: {', '.join(self.hub.apps)}"`. Else if `len(supported) == 1` → that app, rest = `args`. Else if no `supported` → `"No app here offers login links."`. Else if `args` → `LOGIN_USAGE`. Else → `("Which app?", [[(title, f"hub|ln:{n}") for n in supported]])`.
3. Chosen app not in `supported` → `f"Login links: {', '.join(titles of supported)}"` (or the "No app here" text when empty).
4. `rest`: `[]` → no request. One element → if it starts with `http`, take `urllib.parse.parse_qs(urllib.parse.urlsplit(x).query).get("oauth_request", [""])[0]`, else `x` itself; must match `OAUTH_REQUEST_RE`, else `LOGIN_USAGE`. More than one → `LOGIN_USAGE`.
5. `self._still_mismatched(app)` → `relay.VERSIONS`.
6. `v = relay.version(e)`; if `v` and `semver_newer(FIRST_LOGIN_RELEASE, f"v{v}")` → `UPDATE_FIRST.format(title=e.title, ver=v, hub=__version__)`.
7. `text, is_link = relay.login(e, request)`; if `is_link`: `self.send_link(app, text)`; return `None`. Else return `text`.

`send_link`: `r = self.api.call("sendMessage", chat_id=<owner>, text=text[:4096], disable_web_page_preview=True)`; append `(self.ctx.now() + timedelta(seconds=LINK_TTL), r["message_id"], title, app)` to `self.expiring`. An `ApiError` here propagates like any reply error (the `handle` wrapper answers "Error: …" — the error text holds no URL).

`expire_links`: for each entry with `due <= self.ctx.now()`: remove it, then `self._try("editMessageText", chat_id=<owner>, message_id=mid, text=EXPIRED.format(title=title, app=app), disable_web_page_preview=True)` (`_try` already swallows and logs `ApiError` without parameters). Called first thing in `poll_once`.

Buttons: `/status` (all apps) returns `(text, rows)` where `rows = [[(f"🔑 Login link: {e.title}", f"hub|ln:{n}")] for n, e in self.hub.apps.items() if apps.get(n).has_login_link and n not in self.mismatch]`; per-app `/status` adds that app's row to the buttons it already gets. `hub_button`: `kind == "ln"` and `arg in self.hub.apps` → `self._send(self.login([arg]))`; return `(self.hub.apps[arg].title, None)`.

MENU: add `("login", "Get a dashboard login link: /login [app]")` after `status`. HELP: add `/login [app]` after `/status ·`. Update `test_help_text_exact` accordingly.

- [ ] **Step 1: Write failing tests** in `tests/test_telegram.py` (use `_bot`, `upd`, `cb`, `calls`, `ex`, `line`, `reply`, `hello`; add a `version` parameter to `hubfakes.hello`):
```python
RID = "e" * 64
LINKTEXT = "Open this link: https://h.example.ts.net/admin/auth/" + "f" * 64

def _login_bot(tmp_path, names=("hermes", "gbrain")):
    ctx, api, b = _bot(tmp_path, names)
    ex(b.hub, "gbrain").on("login-link", lines=[line("link", text=LINKTEXT)])
    return ctx, api, b

def test_login_picks_the_only_app_with_links(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login"))
    sent = calls(api, "sendMessage")[-1]
    assert sent["text"] == LINKTEXT and sent["disable_web_page_preview"] is True
    assert ex(b.hub, "gbrain").ops()[-1] == ["login-link"]

def test_login_accepts_the_consent_url(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, f"/login gbrain https://h.example.ts.net/admin/?oauth_request={RID}&x=1#oauth-consent"))
    assert ex(b.hub, "gbrain").ops()[-1] == ["login-link", "--oauth-request", RID]

@pytest.mark.parametrize("arg", ["E" * 64, "e" * 63, "https://h/admin/?x=1", "a b"])
def test_login_bad_request_gets_usage(tmp_path, arg):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, f"/login gbrain {arg}"))
    assert api.sent()[-1] == telegram.LOGIN_USAGE
    assert ["login-link"] not in [o[:1] for o in ex(b.hub, "gbrain").ops()]

def test_login_hermes_lists_the_apps_with_links(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login hermes"))
    assert api.sent()[-1] == "Login links: gbrain"

def test_login_asks_which_app_with_two(tmp_path):
    ctx, api, b = _login_bot(tmp_path, ("clawvisor", "gbrain"))
    b.handle(upd(2, "/login"))
    m = calls(api, "sendMessage")[-1]
    assert m["text"] == "Which app?"
    assert [k["callback_data"] for k in m["reply_markup"]["inline_keyboard"][0]] == \
        ["hub|ln:clawvisor", "hub|ln:gbrain"]

def test_login_gate_on_old_app_version(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    ex(b.hub, "gbrain").on("hello", lines=[hello(app="gbrain", version="0.6.1")])
    b.handle(upd(2, "/login gbrain"))
    assert api.sent()[-1] == telegram.UPDATE_FIRST.format(title="gbrain", ver="0.6.1", hub=__version__)
    assert ["login-link"] not in ex(b.hub, "gbrain").ops()

def test_login_refusal_is_replied(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    ex(b.hub, "gbrain").on("login-link", lines=[reply("gbrain is not running")])
    b.handle(upd(2, "/login"))
    assert api.sent()[-1] == "gbrain is not running"
    assert b.expiring == []

def test_expiry_replaces_the_link(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login"))
    ctx.clock.sleep(299); b.expire_links()
    assert calls(api, "editMessageText") == []
    ctx.clock.sleep(2); b.expire_links()
    assert calls(api, "editMessageText") == [{
        "chat_id": OWNER, "message_id": 1, "disable_web_page_preview": True,
        "text": "Login link for gbrain expired. Send /login gbrain for a new one."}]
    assert b.expiring == []

def test_expiry_edit_failure_is_ignored_and_dropped(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login"))
    real = api.call
    def failing(method, **p):
        if method == "editMessageText":
            raise ApiError(400, None)
        return real(method, **p)
    api.call = failing
    ctx.clock.sleep(301); b.expire_links()
    assert b.expiring == []

def test_poll_once_runs_the_expiry(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login"))
    ctx.clock.sleep(301); b.poll_once()
    assert len(calls(api, "editMessageText")) == 1

def test_status_offers_login_buttons(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/status"))
    rows = calls(api, "sendMessage")[-1]["reply_markup"]["inline_keyboard"]
    assert rows == [[{"text": "🔑 Login link: gbrain", "callback_data": "hub|ln:gbrain"}]]

def test_login_button(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(cb("hub|ln:gbrain"))
    assert calls(api, "sendMessage")[-1]["text"] == LINKTEXT
```
`tests/test_relay.py`: `relay.version` returns the hello version / None on unreachable; `relay.login` maps `link` → `(text, True)`, `reply` → `(text, False)`, `Unreachable` → `(unreachable_text, False)`, non-zero rc without lines → `(failed_text(entry, "login-link"), False)`.
(Adapt helper names to the real fixtures; the `FakeAPI` returns `{"message_id": 1}` for every non-`getUpdates` call.)

- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement** as specified above. `relay.version`:
```python
def version(entry) -> str | None:
    try:
        lines = entry.executor.call(["hello"])
    except (Unreachable, NoAnswer):
        return None
    h = next((d for d in lines if d.get("kind") == "hello"), None)
    v = h.get("version") if h else None
    return v if isinstance(v, str) else None
```
`relay.login`:
```python
def login(entry, oauth_request=None) -> tuple[str, bool]:
    argv = ["login-link"] + (["--oauth-request", oauth_request] if oauth_request else [])
    try:
        lines = entry.executor.call(argv, timeout=60)
    except Unreachable:
        return unreachable_text(entry.title), False
    except NoAnswer:
        return no_answer_text(entry.title), False
    for d in lines:
        if d.get("kind") == "link":
            return str(d.get("text") or ""), True
        if d.get("kind") == "reply":
            return str(d.get("text") or ""), False
    return failed_text(entry, "login-link"), False
```
`dispatch`: handle `/login` before the `APP_CMDS` check (`if cmd == "/login": return self.login(args)`). `/status` without args returns `(text, rows)` when `rows`, else the text.
- [ ] **Step 4: Run the two test files, then the full suite.**
- [ ] **Step 5: Commit** — "Bot: /login with expiry notice, version gate and status buttons".

---

### Task 4: Root helper `helpers/tailscale-admin-redirect.py`

**Files:**
- Create: `helpers/tailscale-admin-redirect.py` (executable, `#!/usr/bin/env python3`, stdlib)
- Test: `tests/test_helpers.py` (look at how it already tests `helpers/*.py`; run the helper as a subprocess with a fake `tailscale` script first on `PATH`)

**Interfaces:**
- CLI: `tailscale-admin-redirect.py --host <host>.<tailnet>.ts.net [--admin-port 10000] [--dry-run]` and `tailscale-admin-redirect.py --undo <saved.json>`.
- Behaviour:
  1. `tailscale version` → first line `X.Y.Z`; refuse (exit 1, "needs Tailscale 1.92 or newer") when older than 1.92.
  2. `tailscale serve status --json` → dict. Key `f"{host}:443"` must exist in `Web` with a `Handlers` dict, else exit 1 "443 on <host> serves no paths; set up the funnel paths first (README)". `/admin` already in `Handlers` → exit 1 "/admin is already configured on 443".
  3. Save the current config to `./tailscale-serve.before-admin-redirect.<UTC yyyymmddHHMM>.json` (mode 0600, O_EXCL) and print its path.
  4. Add `Handlers["/admin"] = {"Redirect": f"302:https://{host}:{port}${{REQUEST_URI}}"}`; print the 443 handler list before and after.
  5. Unless `--dry-run`: run `tailscale debug localapi POST /localapi/v0/serve-config -` with the new JSON on stdin; non-zero → exit 1 with its stderr; then re-read `serve status --json` and confirm `/admin` is present.
  6. `--undo FILE`: load it (must be a JSON object), POST it the same way, print "restored".
  - `--host` must match `^[a-z0-9-]+(\.[a-z0-9-]+)+$`; `--admin-port` 1–65535 and not 443.

- [ ] **Step 1: Write failing tests** (fake `tailscale` is a small Python script written to `tmp_path/bin/tailscale` that answers `version`, `serve status --json` from a JSON file, and records `debug localapi POST …` stdin to a file and writes it back as the new status):
  - `test_adds_exactly_one_handler` — other ports and the 7 existing paths unchanged; `/admin` is `{"Redirect": "302:https://h.example.ts.net:10000${REQUEST_URI}"}`; a backup file exists.
  - `test_dry_run_changes_nothing`.
  - `test_refuses_when_admin_exists`.
  - `test_refuses_without_443_handlers`.
  - `test_refuses_old_tailscale` (version `1.90.0`).
  - `test_undo_restores` — after add + undo, the status equals the original.
  - `test_bad_host_or_port` (`--host 'x;rm'`, `--admin-port 443`).
- [ ] **Step 2: Run; fail.**
- [ ] **Step 3: Implement** with `subprocess.run([...], capture_output=True, text=True, timeout=30)`, `argparse`, `json`, `os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600)`.
- [ ] **Step 4: Run the test file, then the full suite.**
- [ ] **Step 5: Commit** — "Helper: add or undo the 443 /admin redirect".

---

### Task 5: Docs and onboarding text

**Files:**
- Modify: `README.md` (Clawvisor login section ~239-246, gbrain "Who reaches what" ~411-426, "Public MCP endpoints with Tailscale Serve and Funnel" ~428-452, bot command list wherever `/rollback`/`/restore` are listed)
- Modify: `AGENT_SETUP.md` (step 11 gbrain bullets ~75-80, and the Clawvisor login-link step)
- Modify: `talaria/apps/gbrain.py` `ready_text`
- Test: `tests/test_gbrain.py` (`ready_text` expectation), golden tests if they pin `ready_text`

- [ ] **Step 1:** README:
  - Clawvisor: keep the terminal command; add "or send `/login clawvisor` to the bot: the bot sends it in your private chat with previews off and replaces it with an "expired" notice after 5 minutes." Replace "never paste it into a chat, a ticket, …" with "never paste it into an agent's context, a ticket, or anywhere it could be logged; the bot's private chat is the exception, because the link is single-use and short-lived."
  - gbrain "Who reaches what": add row `| /admin (redirect only) | anyone; answers with a redirect to the tailnet-only admin port | public, port 443 |`. Replace the paragraph "A connector's consent link points at the public address … approve it over your tailnet." with: "A connector's consent link and the bot's login links point at the public address (`https://<host>.<tailnet>.ts.net/admin/…`). With the redirect below, that address sends your browser to the tailnet-only admin port, keeping the rest of the link; outside your tailnet the target does not answer. gbrain keeps admin sessions in memory, so every restart (also the nightly maintenance) logs you out: send `/login gbrain` for a new link."
  - Funnel section: after the `tailscale serve --bg --https=10000 …` line, add the helper step:
    ```bash
    # the admin redirect on 443 (Tailscale 1.92+; the serve CLI has no flag for it)
    python3 ~/.local/share/talaria/helpers/tailscale-admin-redirect.py --host <host>.<tailnet>.ts.net
    ```
    (check the real install path of helpers in an app/hub account: `ctx.paths` or setup's checkout dir; use that path) and explain in two sentences: it adds only `/admin` as a redirect (`302:https://<host>.<tailnet>.ts.net:10000${REQUEST_URI}`), saves the previous config and prints it; `--undo <file>` restores. Change "`tailscale funnel status` should list only the paths above as public" to "…only the paths above (and `/admin` as a redirect)".
  - Bot command list: add `/login [app]`.
- [ ] **Step 2:** AGENT_SETUP step 11: in the "Public access" bullet add the helper command to "Show the commands; never run them". Change "The person logs in to `/admin`" to "The person logs in with `/login gbrain` in the bot (or the token in `/admin`)". Clawvisor login step: mention `/login clawvisor` as the alternative to the terminal command, still never run by the agent.
- [ ] **Step 3:** `ready_text`: append " Log in with /login gbrain in the bot." to the returned string; update the test that pins it.
- [ ] **Step 4:** Grep for stale text: `grep -n "with :10000\|:10000 after the host" README.md AGENT_SETUP.md` → none. Run the full suite plus the CI guard locally:
  `bash -c "! grep -rnE '100\.[0-9]+\.[0-9]+\.[0-9]+|openclaw|@gmail\.com' --include='*.md' --include='*.py' README.md AGENT_SETUP.md talaria helpers templates docs | grep -v 'docs/superpowers/' | grep -vE '100\.64\.0\.(0/10|1)([^0-9]|$)'"`
- [ ] **Step 5: Commit** — "Docs: /login, the 443 /admin redirect and its helper".

---

## Self-review notes

- Spec coverage: owner interface → Task 3; delivery rules (preview off, expiry edit, no logging) → Tasks 1–3; hub↔app op and version gate → Tasks 2–3; app side → Task 1; onboarding docs + helper → Tasks 4–5; errors table → Tasks 1–3; release version → Task 1.
- Deviation from the spec, recorded as a ruling: the op emits one `link` line with `text` (lines joined) instead of one line per link with `url`/`label`; the hub never needs individual URLs, and one line keeps the message identical to the terminal output.
- `OAUTH_REQUEST_RE` is gbrain's own format (`^[a-f0-9]{64}$`, from its CLI), narrower than the spec's draft regex.
