"""Exact output and exact commands of the operator phase, per branch.

Setup's behaviour *is* its messages and the commands it runs, so these tests pin both.
"""
import pytest

from talaria import setup
from tests.test_setup import args, op_env

R = str(setup.REPO)
INSTALL = "/home/hermes/.local/share/talaria"
SUDO = ["sudo", "-n", "-u", "hermes", "-H", "env", "-u", "XDG_CONFIG_HOME", "-u",
        "XDG_DATA_HOME", "-u", "XDG_STATE_HOME", "-u", "XDG_CACHE_HOME", "HOME=/home/hermes"]
PRE = [(["podman", "--version"], None), (["getenforce"], None)]
CHECKS = [(["sudo", "-n", "-u", "hermes", "true"], None),
          (["sudo", "-n", "-u", "hermes", "test", "-e", INSTALL], None)]
TAG = [(["git", "-C", R, "describe", "--tags", "--exact-match"], None)]
ORIGIN = [(["git", "-C", R, "remote", "get-url", "origin"], None)]
CLONE = [(SUDO + ["git", "clone", "-q", "https://github.com/o/talaria", INSTALL], 600)]
UPDATE = [
    (SUDO + ["git", "-C", INSTALL, "fetch", "-q", "--tags", "origin"], 600),
    (SUDO + ["git", "-C", INSTALL, "checkout", "-q", "v0.1.0"], None),
    (SUDO + ["mkdir", "-p", "/home/hermes/.local/bin"], None),
    (SUDO + ["ln", "-sfn", f"{INSTALL}/bin/talaria", "/home/hermes/.local/bin/talaria"], None),
]
HANDOFF = SUDO + ["XDG_RUNTIME_DIR=/run/user/1001",
                  "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus",
                  "/home/hermes/.local/bin/talaria", "setup", "--as-service"]
ROOT_TAIL = ("loginctl enable-linger hermes\n"
             "echo 'admin ALL=(hermes) NOPASSWD: ALL' > /etc/sudoers.d/talaria-hermes\n"
             "chmod 440 /etc/sudoers.d/talaria-hermes\n"
             "visudo -cf /etc/sudoers.d/talaria-hermes\n")


def run_case(monkeypatch, tmp_path, capsys, kw, a):
    sh, run, calls = op_env(monkeypatch, tmp_path, **kw)
    rc = run(args(**a))
    return rc, capsys.readouterr().out, list(zip(sh.calls, sh.timeouts)), calls


CASES = {
    "install": (dict(), dict(user="hermes", adopt="hermes-gateway.service"), 0,
                "OK: Talaria v0.1.0 installed for hermes\n",
                PRE + CHECKS + TAG + ORIGIN + CLONE + UPDATE,
                [HANDOFF + ["--adopt", "hermes-gateway.service"]]),
    "installed": (dict(installed=True), dict(), 0,
                  "OK: Talaria v0.1.0 installed for hermes\n",
                  PRE + CHECKS + TAG + ORIGIN + UPDATE, [HANDOFF]),
    "newuser": (dict(user_exists=False), dict(), 10,
                "ACTION REQUIRED: run this block as root, then run setup again:\n"
                "useradd --create-home --shell /bin/bash hermes\n"
                "grep -q '^hermes:' /etc/subuid || echo 'WARNING: hermes has no subuid range;"
                " see README'\n" + ROOT_TAIL, PRE, []),
    "confirm": (dict(), dict(), 1,
                "FOUND: account hermes exists but Talaria is not installed for it\n"
                "STOP: confirm with the person, then re-run with --user hermes\n",
                PRE + CHECKS, []),
    "sudo": (dict(sudo_ok=False), dict(user="hermes"), 10,
             "ACTION REQUIRED: run this block as root, then run setup again:\n" + ROOT_TAIL,
             PRE + CHECKS[:1], []),
    "nolinger": (dict(linger=False), dict(user="hermes"), 10,
                 "ACTION REQUIRED: run this block as root, then run setup again:\n" + ROOT_TAIL,
                 PRE + CHECKS, []),
    "plan": (dict(), dict(user="hermes", plan=True), 0,
             "PLAN: install Talaria v0.1.0 for hermes from https://github.com/o/talaria\n"
             "PLAN: then: detect Hermes (fresh or adopt), dashboard password, Telegram bot "
             "token and pairing, units, start Hermes, verify\n",
             PRE + CHECKS + TAG + ORIGIN, []),
    "notag": (dict(tag=None), dict(user="hermes"), 1,
              "STOP: this checkout is not at a release tag; check out the latest tag "
              "(or pass --dev)\n", PRE + CHECKS + TAG, []),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_operator_phase_exact(name, monkeypatch, tmp_path, capsys):
    kw, a, rc, out, cmds, handoff = CASES[name]
    got_rc, got_out, got_cmds, got_handoff = run_case(monkeypatch, tmp_path, capsys, kw, a)
    assert got_out == out
    assert got_cmds == cmds
    assert got_handoff == handoff
    assert got_rc == rc


def test_selinux_exact(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    sh.on("getenforce", out="Enforcing\n")
    assert run(args()) == 1
    assert capsys.readouterr().out == "STOP: SELinux is enforcing; Talaria v1 does not support that\n"


def test_selinux_absent_is_not_checked(monkeypatch, tmp_path):
    sh, run, calls = op_env(monkeypatch, tmp_path, installed=True)
    monkeypatch.setattr(setup, "which", lambda t: None if t == "getenforce" else f"/usr/bin/{t}")
    assert run(args()) == 0
    assert not sh.called("getenforce")


def test_dev_install_exact(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, tag=None)
    sh.on("git", "-C", R, "rev-parse", out="abc123\n")
    assert run(args(user="hermes", dev=True)) == 0
    assert capsys.readouterr().out == "OK: Talaria abc123 installed for hermes\n"
    cmds = list(zip(sh.calls, sh.timeouts))
    assert (["git", "-C", R, "rev-parse", "HEAD"], None) in cmds
    assert (SUDO + ["git", "clone", "-q", R, INSTALL], 600) in cmds
    assert (SUDO + ["git", "-C", INSTALL, "checkout", "-q", "abc123"], None) in cmds
    assert not sh.called("git", "-C", R, "remote")


def test_missing_origin_falls_back_to_checkout(monkeypatch, tmp_path):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    sh.on("git", "-C", R, "remote", rc=2, out="")
    run(args(user="hermes"))
    assert (SUDO + ["git", "clone", "-q", R, INSTALL], 600) in list(zip(sh.calls, sh.timeouts))


def test_handoff_returns_child_exit_code(monkeypatch, tmp_path):
    from tests.test_setup import PW
    sh, _, _ = op_env(monkeypatch, tmp_path)
    ld = tmp_path / "linger"
    rc = setup.operator_phase(sh, args(user="hermes"), getpwnam=lambda n: PW, operator="admin",
                              call=lambda argv: 10, linger_dir=ld)
    assert rc == 10


PREREQ_HINT = {
    "podman": "  hint: Debian/Ubuntu: apt install podman · Fedora/RHEL: dnf install podman"
              " · Arch: pacman -S podman\n",
    "systemd-run": "  hint: Debian/Ubuntu: apt install systemd · Fedora/RHEL: dnf install systemd"
                   " · Arch: pacman -S systemd\n",
}


@pytest.mark.parametrize("tool", sorted(PREREQ_HINT))
def test_prerequisite_hint_exact(tool, monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup, "which", lambda t: None if t == tool else f"/usr/bin/{t}")
    assert run(args()) == 10
    assert capsys.readouterr().out == f"MISSING: {tool}\n" + PREREQ_HINT[tool]


@pytest.mark.parametrize("version,ok", [("podman version 4.9.0", True), ("podman version 5.0.1", True),
                                        ("podman version 4.8.9", False), ("podman version 3.12.0", False),
                                        ("weird", False)])
def test_podman_version_floor(version, ok, monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path, installed=True)
    sh.on("podman", "--version", out=version + "\n")
    rc = run(args())
    out = capsys.readouterr().out
    if ok:
        assert rc == 0 and "MISSING" not in out
    else:
        found = ".".join(version.split()[-1].split(".")[:2]) if version != "weird" else "unknown"
        assert rc == 10 and out == f"MISSING: podman >= 4.9 (found {found})\n"


def test_all_tools_checked(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup, "which", lambda t: None)
    assert run(args()) == 10
    out = capsys.readouterr().out
    for tool in ("podman", "git", "tar", "gzip", "systemd-run", "loginctl", "sudo"):
        assert f"MISSING: {tool}\n" in out
    assert "podman >= 4.9" not in out        # no version check without podman


# ---- set-token ----

class TokAPI:
    def __init__(self, base, token, fail=None):
        self.base, self.token, self.fail = base, token, fail
        self.calls = []

    def call(self, method, **p):
        self.calls.append((method, p))
        if self.fail:
            raise self.fail
        return {"username": "mybot"}


GOOD = "123456:" + "A" * 35


def tok_ctx(tmp_path, monkeypatch, stdin, fail=None, tty=False):
    import io
    from tests.fakes import make_test_ctx
    ctx = make_test_ctx(tmp_path)
    made = []
    monkeypatch.setattr(setup, "TelegramAPI",
                        lambda base, token: (made.append(TokAPI(base, token, fail)), made[-1])[1])
    s = io.StringIO(stdin)
    s.isatty = lambda: tty
    monkeypatch.setattr("sys.stdin", s)
    return ctx, made


def test_set_token_success_exact(tmp_path, monkeypatch, capsys):
    ctx, made = tok_ctx(tmp_path, monkeypatch, GOOD + "  \n")
    assert setup.set_token(ctx) == 0
    assert capsys.readouterr().out == "OK: bot @mybot saved. Now run talaria setup again.\n"
    assert ctx.paths.env_file.read_text() == f"TALARIA_TELEGRAM_TOKEN={GOOD}\n"
    assert (made[0].base, made[0].token, made[0].calls) == (ctx.conf.telegram_api, GOOD, [("getMe", {})])


def test_set_token_uses_getpass_on_a_terminal(tmp_path, monkeypatch, capsys):
    ctx, made = tok_ctx(tmp_path, monkeypatch, "", tty=True)
    prompts = []
    monkeypatch.setattr(setup.getpass, "getpass", lambda p: (prompts.append(p), GOOD)[1])
    assert setup.set_token(ctx) == 0
    assert prompts == ["Telegram bot token: "]


def test_set_token_rejected_by_telegram(tmp_path, monkeypatch, capsys):
    from talaria.notify import ApiError
    ctx, made = tok_ctx(tmp_path, monkeypatch, GOOD + "\n", fail=ApiError(401, None))
    assert setup.set_token(ctx) == 1
    assert capsys.readouterr().err == "Telegram did not accept the token (telegram api status 401).\n"
    assert not ctx.paths.env_file.exists()


@pytest.mark.parametrize("token,ok", [
    ("123:" + "a" * 30, True), ("12:" + "a" * 30, False), ("123:" + "a" * 29, False),
    ("123:" + "a_-" * 10, True), ("123:" + "a" * 29 + "!", False), ("x123:" + "a" * 30, False),
    ("123:" + "a" * 30 + " x", False), ("", False)])
def test_set_token_format(token, ok, tmp_path, monkeypatch, capsys):
    ctx, made = tok_ctx(tmp_path, monkeypatch, token + "\n")
    assert (setup.set_token(ctx) == 0) is ok
    if not ok:
        assert capsys.readouterr().err == "That does not look like a Telegram bot token.\n"
        assert made == []
