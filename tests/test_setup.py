import argparse
import copy
import os
import io
from types import SimpleNamespace
from pathlib import Path

import pytest

from talaria import setup, state
from talaria.conf import parse_kv
from tests.fakes import FakeShell, make_test_ctx


def args(**kw):
    base = dict(plan=False, user=None, adopt=None, dev=False, app=None, as_service=False,
                hub="talaria", as_hub=False, register=None, import_telegram=False)
    base.update(kw)
    return argparse.Namespace(**base)


PW = SimpleNamespace(pw_name="hermes", pw_uid=1001, pw_gid=1001, pw_dir="/home/hermes")
HUBPW = SimpleNamespace(pw_name="talaria", pw_uid=1002, pw_gid=1002, pw_dir="/home/talaria")


def op_env(monkeypatch, tmp_path, *, user_exists=True, sudo_ok=True, linger=True,
           installed=False, tag="v0.1.0", hub_exists=True, hub_sudo_ok=True, hub_linger=True,
           hub_installed=True, probe_rc=0, probe_err="", v04_units=False, v04_token=False,
           move_rc=0):
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
    s = setup.sudo_as("hermes", "/home/hermes")
    sh.on(*s, "test", "-e", "/home/hermes/.config/systemd/user/talaria-telegram.service",
          rc=0 if v04_units else 1)
    sh.on(*s, "grep", "-qs", rc=0 if v04_token else 1)
    sh.moves, sh.marks = [], []
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


def test_missing_tool_reported_with_hints(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path)
    monkeypatch.setattr(setup, "which", lambda t: None if t == "podman" else f"/usr/bin/{t}")
    assert run(args()) == 10
    out = capsys.readouterr().out
    assert "MISSING: podman" in out and "apt install podman" in out and "dnf install" in out


def test_old_podman_is_missing(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path)
    sh.on("podman", "--version", out="podman version 4.3.1\n")
    assert run(args()) == 10
    assert "podman >= 4.9" in capsys.readouterr().out


def test_selinux_enforcing_stops(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path)
    sh.on("getenforce", out="Enforcing\n")
    assert run(args()) == 1 and "STOP" in capsys.readouterr().out


def test_new_user_gets_one_root_block(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path, user_exists=False)
    assert run(args()) == 10
    out = capsys.readouterr().out
    assert out.count("ACTION REQUIRED") == 1
    assert "id hermes >/dev/null 2>&1 || useradd --create-home --shell /bin/bash hermes" in out
    assert "admin ALL=(hermes) NOPASSWD: ALL" in out and "loginctl enable-linger hermes" in out
    assert 'talaria ALL=(hermes) NOPASSWD: $home/.local/bin/talaria op *' in out


def test_existing_user_needs_confirmation(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path)
    assert run(args()) == 1
    out = capsys.readouterr().out
    assert "FOUND: account hermes exists" in out and "--user hermes" in out


def test_existing_user_without_sudo_rule(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path, sudo_ok=False)
    assert run(args(user="hermes")) == 10
    out = capsys.readouterr().out
    assert "useradd" not in out and "NOPASSWD" in out


def test_not_at_release_tag_stops_unless_dev(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path, tag=None)
    sh.on("git", "-C", str(setup.REPO), "rev-parse", out="abc123\n")
    assert run(args(user="hermes")) == 1
    assert run(args(user="hermes", dev=True)) == 0


def test_installs_and_hands_over(monkeypatch, tmp_path):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    assert run(args(user="hermes", adopt="hermes-gateway.service")) == 0
    sudo = [c for c in sh.calls if c[0] == "sudo"]
    assert any("clone" in c for c in sudo)
    assert any("checkout" in c and "v0.1.0" in c for c in sudo)
    argv = calls[0]
    assert argv[:5] == ["sudo", "-n", "-u", "hermes", "-H"]
    assert "XDG_RUNTIME_DIR=/run/user/1001" in argv
    assert argv[-6:] == ["setup", "--as-service", "--app", "hermes", "--adopt",
                         "hermes-gateway.service"]
    assert calls[1][-4:] == ["setup", "--as-hub", "--register", "hermes:hermes"]
    assert calls[1][:5] == ["sudo", "-n", "-u", "talaria", "-H"]
    assert "XDG_RUNTIME_DIR=/run/user/1002" in calls[1]


def test_plan_changes_nothing(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    assert run(args(user="hermes", plan=True)) == 0
    assert calls == [] and not any("clone" in c for c in sh.calls)
    assert "PLAN:" in capsys.readouterr().out


def test_plan_names_the_app_passed_in(monkeypatch, tmp_path, capsys):
    """operator_phase has no ctx (the service user's talaria.conf does not exist yet), so
    the app it describes in the PLAN text is an explicit parameter; it defaults to hermes
    for every other test in this file."""
    sh, _, _ = op_env(monkeypatch, tmp_path)
    rc = setup.operator_phase(sh, args(user="hermes", plan=True),
                              getpwnam=lambda n: PW, operator="admin", app="clawvisor",
                              linger_dir=tmp_path / "linger")
    assert rc == 0
    out = capsys.readouterr().out
    assert "then: detect Clawvisor (fresh or adopt), secrets, units, start Clawvisor, verify" in out


def test_operator_phase_rejects_an_unknown_app(tmp_path, capsys):
    rc = setup.operator_phase(FakeShell(), args(), getpwnam=lambda n: PW, operator="admin",
                              app="bogus")
    assert rc == 1
    assert capsys.readouterr().out == (
        "STOP: unknown app: 'bogus'; choose one of hermes, clawvisor, gbrain\n")


def test_setup_dispatch_rejects_an_unknown_app_before_doing_anything(capsys):
    assert setup.setup(args(app="bogus")) == 1
    assert capsys.readouterr().out == (
        "STOP: unknown app: 'bogus'; choose one of hermes, clawvisor, gbrain\n")


def test_setup_dispatch_rejects_an_unknown_app_as_service_too(capsys):
    assert setup.setup(args(app="bogus", as_service=True)) == 1
    assert capsys.readouterr().out == (
        "STOP: unknown app: 'bogus'; choose one of hermes, clawvisor, gbrain\n")


def _captured_dispatch(monkeypatch, a):
    """Run setup.setup(args(**a)) with operator_phase stubbed out; return the
    (app, explicit_app) kwargs it was called with."""
    seen = {}
    monkeypatch.setattr(setup, "operator_phase",
                        lambda sh, args_, **kw: seen.update(kw) or 0)
    setup.setup(args(**a))
    return seen["app"], seen["explicit_app"]


def test_app_flag_wins_and_is_forwarded(monkeypatch):
    """Rule 1: --app, whatever --user says, decides the app and is forwarded."""
    app, explicit = _captured_dispatch(monkeypatch, dict(app="clawvisor", user="anything"))
    assert (app, explicit) == ("clawvisor", True)


def test_user_named_clawvisor_without_app_selects_and_forwards_clawvisor(monkeypatch):
    """Rule 2, the I3 fix: --user clawvisor alone must install Clawvisor, not silently
    default to Hermes for the clawvisor account."""
    app, explicit = _captured_dispatch(monkeypatch, dict(user="clawvisor"))
    assert (app, explicit) == ("clawvisor", True)


def test_user_hermes_without_app_selects_and_forwards_hermes(monkeypatch):
    """Rule 2 also covers the ordinary Hermes flow (--user hermes, no --app): this must
    keep forwarding --app hermes, matching the v0.3.0 golden handoff byte-for-byte."""
    app, explicit = _captured_dispatch(monkeypatch, dict(user="hermes"))
    assert (app, explicit) == ("hermes", True)


def test_no_app_no_user_falls_back_to_hermes_without_forwarding(monkeypatch):
    """Rule 3: neither flag given. Still installs hermes (today's default), but does not
    forward --app, so the service phase can fall back to an existing conf instead of a
    bare default overriding it."""
    app, explicit = _captured_dispatch(monkeypatch, dict())
    assert (app, explicit) == ("hermes", False)


def test_user_not_an_app_name_falls_back_to_hermes_without_forwarding(monkeypatch):
    """Rule 3: --user names an account that isn't an app name either."""
    app, explicit = _captured_dispatch(monkeypatch, dict(user="bob"))
    assert (app, explicit) == ("hermes", False)


def test_clawvisor_default_account_name_and_plan_text(monkeypatch, tmp_path, capsys):
    """With no --user, the account to create/use follows the chosen app."""
    monkeypatch.setattr(setup, "which", lambda t: f"/usr/bin/{t}")
    sh = FakeShell()
    sh.on("sudo")
    sh.on("podman", "--version", out="podman version 4.9.3\n")
    sh.on("getenforce", out="Permissive\n")
    sh.on("git", "-C", str(setup.REPO), "describe", out="v0.1.0\n")
    sh.on("git", "-C", str(setup.REPO), "remote", out="https://github.com/o/talaria\n")
    sh.on(*setup.sudo_as("clawvisor", PW.pw_dir), "test", rc=1)
    sh.on(*setup.sudo_as("clawvisor", PW.pw_dir), "grep", rc=1)
    ld = tmp_path / "linger"
    ld.mkdir()
    (ld / "clawvisor").touch()
    (ld / "talaria").touch()
    rc = setup.operator_phase(sh, args(plan=True), getpwnam=lambda n: PW, operator="admin",
                              linger_dir=ld, app="clawvisor")
    out = capsys.readouterr().out
    assert rc == 0
    assert "install Talaria v0.1.0 for clawvisor from" in out


def test_clawvisor_handoff_carries_the_app_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "which", lambda t: f"/usr/bin/{t}")
    sh = FakeShell()
    sh.on("sudo")
    sh.on("podman", "--version", out="podman version 4.9.3\n")
    sh.on("getenforce", out="Permissive\n")
    sh.on("git", "-C", str(setup.REPO), "describe", out="v0.1.0\n")
    sh.on("git", "-C", str(setup.REPO), "remote", out="https://github.com/o/talaria\n")
    sh.on(*setup.sudo_as("clawvisor", PW.pw_dir), "test", rc=1)
    sh.on(*setup.sudo_as("clawvisor", PW.pw_dir), "grep", rc=1)
    ld = tmp_path / "linger"
    ld.mkdir()
    (ld / "clawvisor").touch()
    (ld / "talaria").touch()
    calls = []
    rc = setup.operator_phase(sh, args(), getpwnam=lambda n: PW, operator="admin",
                              call=lambda argv: calls.append(argv) or 0, linger_dir=ld,
                              app="clawvisor")
    assert rc == 0
    assert calls[0][-3:] == ["--as-service", "--app", "clawvisor"]


def test_hermes_handoff_still_carries_its_own_app_flag(monkeypatch, tmp_path):
    """Hermes's handoff now names its app explicitly too, so a re-run can always detect a
    contradiction with an existing talaria.conf."""
    sh, run, calls = op_env(monkeypatch, tmp_path)
    assert run(args(user="hermes")) == 0
    assert calls[0][-3:] == ["--as-service", "--app", "hermes"]


# ---- service phase ----

class PairAPI:
    def __init__(self):
        self.calls = []

    def call(self, method, **p):
        self.calls.append(method)
        return []


@pytest.fixture
def svc(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, telegram_token="1:" + "a" * 35)
    ctx.sh.on("systemctl").on("systemctl", "--user", "is-active", out="inactive\n")
    ctx.sh.on("podman", "tag")
    monkeypatch.setattr(setup.adopt, "detect", lambda c: [])
    monkeypatch.setattr(ctx.app, "releases", lambda c: {"v2026.9.24": "c"})
    monkeypatch.setattr(ctx.app, "published", lambda c, tags: {"v2026.9.24"})
    monkeypatch.setattr(ctx.app, "fetch",
                        lambda c, t, commit: {"tag": t, "id": "sha256:n", "ref": "r", "digest": "d"})
    monkeypatch.setattr(setup.service, "post_start_check", lambda c: None)
    monkeypatch.setattr(setup.telegram, "pair",
                        lambda c, api, code, timeout_s=900, announce=lambda: None: (
                            announce(), {"id": 42, "first_name": "Ann", "username": "ann"})[1])
    monkeypatch.setattr(setup.telegram, "TelegramAPI", lambda base, token: PairAPI())
    return ctx


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




def test_rerun_is_idempotent(svc, capsys):
    assert setup.service_phase(svc, args(as_service=True)) == 0
    pw1 = svc.paths.hermes_env.read_text()
    svc.sh.on("systemctl", "--user", "is-active", out="active\n")
    starts = len(svc.sh.called("systemctl", "--user", "start", "hermes.service"))
    assert setup.service_phase(svc, args(as_service=True)) == 0
    assert svc.paths.hermes_env.read_text() == pw1
    assert len(svc.sh.called("systemctl", "--user", "start", "hermes.service")) == starts


def test_fresh_image_respects_release_allow(svc, monkeypatch):
    monkeypatch.setattr(svc.app, "releases", lambda c: {"v2026.8.3": "a", "v2026.9.24": "b"})
    monkeypatch.setattr(svc.app, "published", lambda c, tags: set(tags))
    monkeypatch.setattr(svc.app, "fetch",
                        lambda c, t, commit: {"tag": t, "id": "sha256:n", "ref": "r", "digest": "d"})
    svc.conf.release_allow = ("v2026.8.3",)   # v2026.9.24 would otherwise be picked
    st = copy.deepcopy(state.DEFAULT)
    assert setup._fresh_image(svc, st) is True
    assert st["current"]["tag"] == "v2026.8.3"


def test_several_installs_stop(svc, monkeypatch, capsys):
    from talaria.adopt import Found
    f = lambda u: Found(unit=u, container="c", name=u, image_id="i", mounts=[], env={},
                        quadlet=None)
    monkeypatch.setattr(setup.adopt, "detect", lambda c: [f("a.service"), f("b.service")])
    assert setup.service_phase(svc, args(as_service=True)) == 1
    out = capsys.readouterr().out
    assert out.count("FOUND:") == 2 and "--adopt" in out


def test_adopt_flag_refused_when_app_cannot_adopt(svc, monkeypatch, capsys):
    monkeypatch.setattr(type(svc.app), "can_adopt", False)
    spy = []
    monkeypatch.setattr(setup.adopt, "detect", lambda c: spy.append(1) or [])
    rc = setup.service_phase(svc, args(as_service=True, adopt="hermes.service"))
    out = capsys.readouterr().out
    assert rc == 1
    assert out == "STOP: adopting is not supported for Hermes\n"
    assert spy == []
    assert svc.sh.calls == []


def test_detection_skipped_when_app_cannot_adopt(svc, monkeypatch, capsys):
    monkeypatch.setattr(type(svc.app), "can_adopt", False)
    spy = []
    monkeypatch.setattr(setup.adopt, "detect", lambda c: spy.append(1) or [])
    rc = setup.service_phase(svc, args(as_service=True))
    out = capsys.readouterr().out
    assert spy == []                                  # detection itself was never called
    assert rc == 0 and "no existing Hermes found: fresh install" in out


def test_set_token_validates(monkeypatch, tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("not-a-token\n"))
    assert setup.set_token(ctx) == 1
    assert not ctx.paths.env_file.exists()


def test_dev_install_clones_local_checkout(monkeypatch, tmp_path):
    sh, run, calls = op_env(monkeypatch, tmp_path, tag=None)
    sh.on("git", "-C", str(setup.REPO), "rev-parse", out="abc123\n")
    run(args(user="hermes", dev=True))
    clone = [c for c in sh.calls if "clone" in c][0]
    assert str(setup.REPO) in clone


def test_handoff_drops_callers_xdg_dirs(monkeypatch, tmp_path):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    run(args(user="hermes"))
    argv = calls[0]
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        assert argv[argv.index(var) - 1] == "-u"
    assert "HOME=/home/hermes" in argv


def test_setup_leaves_callers_cwd(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(setup, "operator_phase", lambda sh, a, **kw: seen.append(os.getcwd()) or 0)
    monkeypatch.chdir(tmp_path)
    assert setup.setup(args()) == 0
    assert seen == ["/"]      # the service user may not be able to enter the caller's cwd


# ---- v0.2.4 ----

@pytest.mark.parametrize("user", ["hermes; rm -rf /", "a b", "x'y", "-x", "a" * 33])
def test_bad_user_name_stops_before_any_root_block(monkeypatch, tmp_path, capsys, user):
    sh, run, _ = op_env(monkeypatch, tmp_path, user_exists=False)
    assert run(args(user=user)) == 1
    out = capsys.readouterr().out
    assert "STOP: not a valid account name" in out and "useradd" not in out


def test_bad_operator_name_stops(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path, user_exists=False)
    assert setup.operator_phase(sh, args(), getpwnam=lambda n: PW, operator="a'b") == 1
    assert "not a valid account name: \"a'b\"" in capsys.readouterr().out


def test_new_user_block_says_to_rerun_with_user(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path, user_exists=False)
    assert run(args()) == 10
    assert "then run setup again with --user hermes:" in capsys.readouterr().out


def test_plan_on_fresh_host_is_a_plan(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, user_exists=False)
    assert run(args(plan=True)) == 0
    out = capsys.readouterr().out
    assert "PLAN: create the account hermes" in out and "useradd" not in out


@pytest.mark.parametrize("url, want", [
    ("git@github.com:o/talaria.git", "https://github.com/o/talaria"),
    ("ssh://git@github.com/o/talaria", "https://github.com/o/talaria"),
    ("https://github.com/o/talaria", "https://github.com/o/talaria"),
    ("/srv/talaria", "/srv/talaria"),
    ("git@gitlab.com:o/talaria.git", None),
    ("ssh://host/talaria", None),
    ("ssh://git@github.com:22/o/talaria.git", "https://github.com/o/talaria"),
    ("https://user:TOKEN@github.com/o/talaria", "https://github.com/o/talaria"),
    ("https://x-access-token@github.com/o/talaria", "https://github.com/o/talaria"),
])
def test_install_url(url, want):
    assert setup.install_url(url) == want


def test_ssh_origin_is_cloned_over_https(monkeypatch, tmp_path):
    sh, run, _ = op_env(monkeypatch, tmp_path)
    sh.on("git", "-C", str(setup.REPO), "remote", out="git@github.com:o/talaria.git\n")
    assert run(args(user="hermes")) == 0
    clone = [c for c in sh.calls if "clone" in c][0]
    assert "https://github.com/o/talaria" in clone


def test_non_github_ssh_origin_stops(monkeypatch, tmp_path, capsys):
    sh, run, _ = op_env(monkeypatch, tmp_path)
    sh.on("git", "-C", str(setup.REPO), "remote", out="git@gitlab.com:o/talaria.git\n")
    assert run(args(user="hermes")) == 1
    assert "clone Talaria over https" in capsys.readouterr().out


# ---- app adapter hooks ----

def test_prepare_never_overwrites_the_dashboard_password(tmp_path):
    from talaria.conf import parse_kv, write_env_value
    ctx = make_test_ctx(tmp_path)
    write_env_value(ctx.paths.app_env, "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "keep")
    assert ctx.app.prepare(ctx) == []
    assert parse_kv(ctx.paths.app_env.read_text())["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"] == "keep"


def test_host_loopback_without_slirp4netns_stops(svc, monkeypatch, capsys):
    svc.conf.host_loopback = True
    monkeypatch.setattr(setup, "which", lambda t: None)
    assert setup.service_phase(svc, args(as_service=True)) == 1
    out = capsys.readouterr().out
    assert out.startswith("STOP:") and "slirp4netns" in out
    assert not svc.paths.quadlet.exists()


def test_host_loopback_with_slirp4netns_proceeds(svc, monkeypatch, capsys):
    svc.conf.host_loopback = True
    monkeypatch.setattr(setup, "which", lambda t: "/usr/bin/" + t)
    assert setup.service_phase(svc, args(as_service=True)) == 0
    assert "allow_host_loopback=true" in svc.paths.quadlet.read_text()


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
