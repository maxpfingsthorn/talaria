import os
import subprocess
from pathlib import Path

import pytest

import talaria
from talaria import cli
from talaria.shell import Result
from tests.fakes import make_test_ctx

ROOT = Path(__file__).resolve().parent.parent
# the bash wrapper is not mutated; under mutmut its subprocess cannot reach mutmut's state
not_under_mutmut = pytest.mark.skipif(bool(os.environ.get("MUTANT_UNDER_TEST")),
                                      reason="subprocess of the unmutated bash wrapper")


def test_version_command(capsys):
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == talaria.__version__


@not_under_mutmut
def test_bin_wrapper_runs_module():
    out = subprocess.run([str(ROOT / "bin/talaria"), "version"],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == talaria.__version__


def test_unknown_command_exits_2():
    try:
        cli.main(["nope"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected SystemExit")


@pytest.mark.parametrize("cmd", ["deploy", "rehearse", "reject"])
def test_release_command_refuses_the_other_apps_tag_scheme(cmd, tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    rc = cli.main([cmd, "v0.9.10"], make=lambda: ctx)
    assert rc == 2
    assert "v0.9.10 is not a Hermes release tag" in capsys.readouterr().err


def test_login_link_refuses_hermes(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    rc = cli.main(["login-link"], make=lambda: ctx)
    assert rc == 1
    assert capsys.readouterr().err == "not available for Hermes\n"


def test_login_link_refuses_without_a_tty(tmp_path, monkeypatch, capsys):
    import sys
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    rc = cli.main(["login-link"], make=lambda: ctx)
    assert rc == 1
    assert capsys.readouterr().err == "run this in your own terminal\n"


def test_login_link_prints_the_link_with_the_bind_ip(tmp_path, monkeypatch, capsys):
    import sys
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("podman", "exec", out="Open this link: http://localhost:25297/login?token=abc\n")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    rc = cli.main(["login-link"], make=lambda: ctx)
    assert rc == 0
    assert capsys.readouterr().out == (
        "Open this link: http://127.0.0.1:25297/login?token=abc\n"
                                      "Through an SSH tunnel, open it as http://127.0.0.1:25297/... instead (rest of the link unchanged).\n")
    assert ctx.sh.called("podman", "exec", "-e", "HOME=/data/.login-link", "clawvisor",
                         "/clawvisor-server", "dashboard", "--no-open")


def test_login_link_runs_podman_from_root(tmp_path, monkeypatch):
    """The service user may not be able to enter the caller's cwd; podman would fail."""
    import os
    import sys
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    seen = []
    ctx.sh.on("podman", "exec", out="Open: http://localhost:25297/x\n",
              fn=lambda argv, input: (seen.append(os.getcwd()),
                                      Result(0, "Open: http://localhost:25297/x\n", ""))[1])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["login-link"], make=lambda: ctx) == 0
    assert seen == ["/"]


def test_login_link_rewrites_the_published_port_too(tmp_path, monkeypatch, capsys):
    """Guards M1: Clawvisor always prints its container-internal port (25297), which the
    Quadlet republishes under dashboard.port; a changed dashboard.port must be rewritten
    too, not just the host."""
    import sys
    ctx = make_test_ctx(tmp_path, app="clawvisor", dashboard_port=8443)
    ctx.sh.on("podman", "exec", out="Open this link: http://localhost:25297/login?token=abc\n")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    rc = cli.main(["login-link"], make=lambda: ctx)
    assert rc == 0
    assert capsys.readouterr().out == (
        "Open this link: http://127.0.0.1:8443/login?token=abc\n"
                                      "Through an SSH tunnel, open it as http://127.0.0.1:8443/... instead (rest of the link unchanged).\n")


def test_login_link_refuses_on_a_failed_podman_exec_without_echoing_stdout(tmp_path, monkeypatch,
                                                                           capsys):
    import sys
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("podman", "exec", rc=1,
             out="Open this link: http://localhost:25297/login?token=SECRET\n")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    rc = cli.main(["login-link"], make=lambda: ctx)
    err = capsys.readouterr().err
    assert rc == 1
    assert err == "STOP: could not get a login link from Clawvisor\n"
    assert "SECRET" not in err and "localhost" not in err


def test_login_link_refuses_on_a_timeout(tmp_path, monkeypatch, capsys):
    import sys
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("podman", "exec",
             fn=lambda argv, input: (_ for _ in ()).throw(
                 subprocess.TimeoutExpired(cmd=argv, timeout=30)))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    rc = cli.main(["login-link"], make=lambda: ctx)
    assert rc == 1
    assert capsys.readouterr().err == "STOP: podman exec timed out\n"


@not_under_mutmut
def test_bin_wrapper_ignores_callers_cwd(tmp_path):
    fake = tmp_path / "talaria"
    fake.mkdir()
    (fake / "__init__.py").write_text("__version__ = 'impostor'\n")
    (fake / "__main__.py").write_text("print('impostor')\n")
    out = subprocess.run([str(ROOT / "bin/talaria"), "version"], cwd=tmp_path,
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == talaria.__version__


def _link(tmp_path, monkeypatch, capsys, **kw):
    import sys
    ctx = make_test_ctx(tmp_path, app="clawvisor", **kw)
    ctx.sh.on("podman", "exec", out="Open this link: http://localhost:25297/login?token=abc\n")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["login-link"], make=lambda: ctx) == 0
    return capsys.readouterr().out.splitlines()


def test_login_link_one_line_per_address_and_tunnel_hint(tmp_path, monkeypatch, capsys):
    out = _link(tmp_path, monkeypatch, capsys, dashboard_bind="10.254.254.1 tailscale",
                tailscale_ip="100.64.0.7")
    assert out[:2] == ["Open this link: http://10.254.254.1:25297/login?token=abc",
                       "Open this link: http://100.64.0.7:25297/login?token=abc"]
    assert len(out) == 3 and "http://127.0.0.1:25297/" in out[2] and "token" not in out[2]


def test_login_link_tailscale_only_has_no_tunnel_hint(tmp_path, monkeypatch, capsys):
    out = _link(tmp_path, monkeypatch, capsys, dashboard_bind="tailscale",
                tailscale_ip="100.64.0.7")
    assert out == ["Open this link: http://100.64.0.7:25297/login?token=abc"]


def test_versions_agree():
    import re
    py = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)[1]
    lock = re.search(r'name = "talaria"\nversion = "([^"]+)"', (ROOT / "uv.lock").read_text())[1]
    assert py == lock == talaria.__version__ == "0.5.3"


def test_login_link_prints_the_public_url_first(tmp_path, monkeypatch, capsys):
    import sys
    ctx = make_test_ctx(tmp_path, app="clawvisor", dashboard_public_url="https://h.ts.net")
    ctx.sh.on("podman", "exec", out="Open this link: http://localhost:25297/login?token=abc\n")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["login-link"], make=lambda: ctx) == 0
    assert capsys.readouterr().out == (
        "Open this link: https://h.ts.net/login?token=abc\n"
        "Open this link: http://127.0.0.1:25297/login?token=abc\n"
        "Through an SSH tunnel, open it as http://127.0.0.1:25297/... instead "
        "(rest of the link unchanged).\n")


def _link_ctx(tmp_path, monkeypatch, **on):
    import sys
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("podman", "exec", **on)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    return ctx


def test_login_link_writes_its_own_session_file_and_removes_it(tmp_path, monkeypatch):
    import json
    import stat
    ctx = _link_ctx(tmp_path, monkeypatch, out="Open: http://localhost:25297/x\n")
    seen = {}
    orig = ctx.sh.run

    def run(argv, **kw):
        d = ctx.conf.data_dir / ".login-link"
        f = d / ".clawvisor" / ".local-session"
        seen["json"] = json.loads(f.read_text())
        seen["modes"] = [stat.S_IMODE(p.stat().st_mode) for p in (d, d / ".clawvisor", f)]
        return orig(argv, **kw)
    monkeypatch.setattr(ctx.sh, "run", run)
    assert cli.main(["login-link"], make=lambda: ctx) == 0
    assert seen["json"] == {"server_url": "http://localhost:25297", "magic_token": ""}
    assert seen["modes"] == [0o700, 0o700, 0o600]
    assert not (ctx.conf.data_dir / ".login-link").exists()
    assert ctx.sh.calls[-1][:5] == ["podman", "exec", "-e", "HOME=/data/.login-link", "clawvisor"]


@pytest.mark.parametrize("on", [dict(rc=1), dict(fn=lambda argv, input: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 30)))])
def test_login_link_removes_the_session_dir_on_failure(tmp_path, monkeypatch, on):
    ctx = _link_ctx(tmp_path, monkeypatch, **on)
    assert cli.main(["login-link"], make=lambda: ctx) == 1
    assert not (ctx.conf.data_dir / ".login-link").exists()
