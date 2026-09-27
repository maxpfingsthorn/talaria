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


def test_run_helper_exact_argv(tmp_path):
    ctx = make_test_ctx(tmp_path)
    work = tmp_path / "work"
    work.mkdir()

    def fake(argv, input):
        (work / "confdiff.json").write_text("{}")
        return Result(0)

    ctx.sh.on("podman", "run", fn=fake)
    containers.run_helper(ctx, REC, "confdiff.py", tmp_path / "d", work, args=["a", "b"])
    assert ctx.sh.calls == [[
        "podman", "run", "--rm", "--network=none", "--userns=keep-id:uid=10000,gid=10000",
        "--user", "10000:10000", "-v", f"{tmp_path/'d'}:/opt/data",
        "-v", f"{ctx.paths.helpers_dir}:/opt/talaria:ro", "-v", f"{work}:/opt/talaria-out",
        "-e", "HERMES_HOME=/opt/data", "-e", "HOME=/opt/data",
        "-e", "PYTHONPATH=/opt/hermes:/opt/talaria",
        "-e", "TALARIA_RESULT=/opt/talaria-out/confdiff.json",
        "-w", "/opt/hermes", "--entrypoint", "/opt/hermes/.venv/bin/python",
        "sha256:img", "/opt/talaria/confdiff.py", "a", "b"]]
    assert ctx.sh.timeouts == [900]


def test_run_helper_removes_stale_result_first(tmp_path):
    ctx = make_test_ctx(tmp_path)
    (tmp_path / "migrate.json").write_text('{"stale": true}')
    ctx.sh.on("podman", "run", rc=1, err="boom")
    with pytest.raises(containers.HelperError):
        containers.run_helper(ctx, REC, "migrate.py", tmp_path, tmp_path)


def test_run_helper_stem_of_dotted_name(tmp_path):
    ctx = make_test_ctx(tmp_path)

    def fake(argv, input):
        (tmp_path / "a.b.json").write_text("{}")
        return Result(0)

    ctx.sh.on("podman", "run", fn=fake)
    containers.run_helper(ctx, REC, "a.b.py", tmp_path, tmp_path)
    assert "TALARIA_RESULT=/opt/talaria-out/a.b.json" in ctx.sh.calls[0]


def test_run_helper_error_tail_exact(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "run", rc=2, err="\n".join(f"l{i}" for i in range(8)) + "\n")
    with pytest.raises(containers.HelperError) as e:
        containers.run_helper(ctx, REC, "migrate.py", tmp_path, tmp_path)
    assert str(e.value) == "migrate.py wrote no result (exit 2): l3 | l4 | l5 | l6 | l7"


def test_run_helper_error_tail_from_stdout(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "run", rc=1, out="only stdout\n")
    with pytest.raises(containers.HelperError, match="only stdout"):
        containers.run_helper(ctx, REC, "migrate.py", tmp_path, tmp_path)


def test_run_doctor_exact_argv(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "run", out="a\n", err="b\n")
    assert containers.run_doctor(ctx, REC, tmp_path / "d") == "a\nb\n"
    assert ctx.sh.calls == [[
        "podman", "run", "--rm", "--network=none", "--userns=keep-id:uid=10000,gid=10000",
        "--user", "10000:10000", "-v", f"{tmp_path/'d'}:/opt/data",
        "-e", "HERMES_HOME=/opt/data", "-e", "HOME=/opt/data",
        "--entrypoint", "/opt/hermes/.venv/bin/hermes", "sha256:img", "doctor"]]
    assert ctx.sh.timeouts == [300]
