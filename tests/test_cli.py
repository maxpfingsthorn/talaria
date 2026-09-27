import subprocess
from pathlib import Path

import talaria
from talaria import cli

ROOT = Path(__file__).resolve().parent.parent


def test_version_command(capsys):
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == talaria.__version__


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
