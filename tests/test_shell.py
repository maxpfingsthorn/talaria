import pytest

from talaria.shell import CommandError, Shell


def test_run_captures_output():
    r = Shell().run(["sh", "-c", "echo hi; echo err >&2"])
    assert (r.returncode, r.stdout, r.stderr) == (0, "hi\n", "err\n")


def test_run_raises_on_failure_with_stderr_tail():
    with pytest.raises(CommandError) as e:
        Shell().run(["sh", "-c", "echo boom >&2; exit 3"])
    assert e.value.result.returncode == 3
    assert "boom" in str(e.value)


def test_run_check_false_returns_result():
    assert Shell().run(["false"], check=False).returncode == 1


def test_run_passes_input():
    assert Shell().run(["cat"], input="x").stdout == "x"
