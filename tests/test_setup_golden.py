"""Exact output and exact commands of the operator phase, per branch.

Setup's behaviour *is* its messages and the commands it runs, so these tests pin both.
"""
import pytest

from talaria import setup
from tests.test_setup import args, op_env

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
    assert capsys.readouterr().out == ("OK: Talaria abc123 installed for hermes\nOK: Talaria abc123 installed for talaria\nDONE\n")
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
