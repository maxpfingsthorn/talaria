import os
import subprocess
from pathlib import Path

import pytest

import talaria
from talaria import cli
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
    assert capsys.readouterr().out == "Open this link: http://127.0.0.1:25297/login?token=abc\n"
    assert ctx.sh.called("podman", "exec", "clawvisor", "/clawvisor-server", "dashboard",
                         "--no-open")


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
