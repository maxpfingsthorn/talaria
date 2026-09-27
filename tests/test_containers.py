import json

import pytest

from talaria import containers
from talaria.shell import Result
from tests.fakes import make_test_ctx

REC = {"id": "sha256:img"}


def test_run_helper_argv_and_result(tmp_path):
    ctx = make_test_ctx(tmp_path)
    work = tmp_path / "work"
    work.mkdir()

    def fake(argv, input):
        (work / "migrate.json").write_text(json.dumps({"ok": True}))
        return Result(0)

    ctx.sh.on("podman", "run", fn=fake)
    res = containers.run_helper(ctx, REC, "migrate.py", tmp_path / "data", work)
    assert res == {"ok": True}
    argv = ctx.sh.calls[0]
    for part in ["--network=none", "--userns=keep-id:uid=10000,gid=10000",
                 "--user", "10000:10000", f"{tmp_path/'data'}:/opt/data",
                 "TALARIA_RESULT=/opt/talaria-out/migrate.json",
                 "PYTHONPATH=/opt/hermes:/opt/talaria", "sha256:img", "/opt/talaria/migrate.py"]:
        assert part in argv, part
    assert "HERMES_SKIP_CONFIG_MIGRATION=1" not in argv
    assert not (work / "migrate.json").exists()


def test_run_helper_without_result_raises(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "run", rc=1, err="Traceback: nope")
    with pytest.raises(containers.HelperError, match="nope"):
        containers.run_helper(ctx, REC, "dbopen.py", tmp_path, tmp_path)


def test_run_doctor_returns_text_even_on_failure(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "run", rc=1, out="✓ a\n✗ b\n")
    assert containers.run_doctor(ctx, REC, tmp_path) == "✓ a\n✗ b\n"
    assert "doctor" in ctx.sh.calls[0]
