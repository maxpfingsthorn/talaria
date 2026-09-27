from talaria import selfupdate
from tests.fakes import make_test_ctx


def test_self_update_checks_out_and_restarts(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on("systemctl").on(str(ctx.paths.bin_link))
    assert selfupdate.self_update(ctx, "v0.2.0") == 0
    d = str(ctx.paths.install_dir)
    assert ctx.sh.calls[:2] == [["git", "-C", d, "fetch", "-q", "--tags", "origin"],
                                ["git", "-C", d, "checkout", "-q", "v0.2.0"]]
    assert [str(ctx.paths.bin_link), "setup", "--as-service"] in ctx.sh.calls
    assert ctx.sh.calls[-1] == ["systemctl", "--user", "restart", "talaria-telegram.service"]


def test_self_update_unknown_tag(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", "-C", str(ctx.paths.install_dir), "fetch")
    ctx.sh.on("git", "-C", str(ctx.paths.install_dir), "checkout", rc=1, err="no such ref")
    assert selfupdate.self_update(ctx, "v9.9.9") == 1
    assert "no such ref" in capsys.readouterr().err
