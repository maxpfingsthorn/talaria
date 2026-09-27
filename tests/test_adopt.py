import json

import pytest

from talaria import adopt, state
from talaria.conf import parse_kv
from talaria.shell import Result
from tests.fakes import make_test_ctx


def container(name="hermes-gateway", unit="hermes-gateway.service", mounts=None, env=None):
    return {"Id": f"id-{name}", "Name": name, "Image": "sha256:old",
            "Config": {"Env": env or ["HERMES_HOME=/opt/data", "PATH=/usr/bin",
                                      "HERMES_DASHBOARD_INSECURE=true", "TZ=Europe/Berlin",
                                      "HERMES_LOG_LEVEL=debug", "FOO=bar"],
                       "Labels": {"PODMAN_SYSTEMD_UNIT": unit}},
            "Mounts": mounts if mounts is not None else
            [{"Type": "bind", "Source": "/home/h/data", "Destination": "/opt/data"}]}


def actx(tmp_path, containers):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "ps", out=json.dumps([{"Id": c["Id"]} for c in containers]))
    ctx.sh.on("podman", "container", "inspect", out=json.dumps(containers))
    ctx.sh.on("podman", "image", "inspect",
              out=json.dumps([{"Id": "sha256:old", "RepoDigests": [],
                               "Config": {"Env": ["HERMES_HOME=/opt/data", "PATH=/usr/bin"]}}]))
    ctx.sh.on("podman", "run", out="Hermes Agent v0.16.0 (2026.6.5)\n")
    return ctx


def test_detect_finds_hermes_and_quadlet(tmp_path):
    ctx = actx(tmp_path, [container(), {**container("db"), "Config": {"Env": ["X=1"],
                                                                      "Labels": {}}}])
    ctx.paths.quadlet_dir.mkdir(parents=True)
    (ctx.paths.quadlet_dir / "hermes-gateway.container").write_text("[Container]\n")
    found = adopt.detect(ctx)
    assert [f.unit for f in found] == ["hermes-gateway.service"]
    assert found[0].quadlet == ctx.paths.quadlet_dir / "hermes-gateway.container"


def test_detect_skips_own_managed_container(tmp_path):
    ctx = actx(tmp_path, [container("hermes", "hermes.service")])
    ctx.paths.quadlet.parent.mkdir(parents=True)
    ctx.paths.quadlet.write_text("x")
    assert adopt.detect(ctx) == []


def test_plan_env_allow_list(tmp_path):
    ctx = actx(tmp_path, [container()])
    p = adopt.plan(ctx, adopt.detect(ctx)[0])
    assert p.problems == []
    assert p.kept == {"TZ": "Europe/Berlin", "HERMES_LOG_LEVEL": "debug"}
    assert sorted(p.dropped) == ["FOO", "HERMES_DASHBOARD_INSECURE"]
    assert str(p.data_dir) == "/home/h/data" and p.image["tag"] == "v2026.6.5"
    assert "PublishPort=127.0.0.1:9119:9119" in p.diff


@pytest.mark.parametrize("mounts", [
    [],
    [{"Type": "bind", "Source": "/a", "Destination": "/opt/data"},
     {"Type": "bind", "Source": "/run/podman.sock", "Destination": "/var/run/docker.sock"}],
    [{"Type": "volume", "Source": "vol", "Destination": "/opt/data"}],
])
def test_plan_refuses_other_mounts(tmp_path, mounts):
    ctx = actx(tmp_path, [container(mounts=mounts)])
    assert adopt.plan(ctx, adopt.detect(ctx)[0]).problems


def test_plan_refuses_old_hermes(tmp_path):
    ctx = actx(tmp_path, [container()])
    ctx.sh.on("podman", "run", out="Hermes Agent v0.10.0 (2026.4.3)\n")
    assert "older than" in adopt.plan(ctx, adopt.detect(ctx)[0]).problems[0]


def test_apply(tmp_path):
    ctx = actx(tmp_path, [container(mounts=[{"Type": "bind", "Source": str(tmp_path / "data"),
                                             "Destination": "/opt/data"}])])
    (tmp_path / "data").mkdir()
    (tmp_path / "data/config.yaml").write_text("_config_version: 27\n")
    ctx.paths.quadlet_dir.mkdir(parents=True)
    q = ctx.paths.quadlet_dir / "hermes-gateway.container"
    q.write_text("[Container]\n")

    def fake_tar(argv, input):
        out = argv[argv.index("-czf") + 1]
        open(out, "wb").write(b"archive")
        return Result(0)

    ctx.sh.on("systemctl").on("podman", "stop").on("podman", "rm")
    ctx.sh.on("podman", "unshare", "tar", fn=fake_tar)
    ctx.sh.on("podman", "unshare", "find", out="")
    f = adopt.detect(ctx)[0]
    p = adopt.plan(ctx, f)
    assert adopt.apply(ctx, f, p) == 0
    st = state.load(ctx.paths)
    assert st["current"]["tag"] == "v2026.6.5" and st["adopt_backup"].endswith("-adopt")
    assert (ctx.paths.backups / f"{st['adopt_backup']}.json").exists()
    assert not q.exists() and q.with_name(q.name + ".talaria-orig").exists()
    assert parse_kv(ctx.paths.conf_file.read_text())["data_dir"] == str(tmp_path / "data")
    assert parse_kv(ctx.paths.hermes_env.read_text())["TZ"] == "Europe/Berlin"
    assert not ctx.sh.called("podman", "unshare", "chown")
    assert ["podman", "rm", "-f", "id-hermes-gateway"] in ctx.sh.calls


def test_apply_chowns_foreign_files(tmp_path):
    ctx = actx(tmp_path, [container(mounts=[{"Type": "bind", "Source": str(tmp_path / "data"),
                                             "Destination": "/opt/data"}])])
    (tmp_path / "data").mkdir()
    ctx.sh.on("systemctl").on("podman", "stop").on("podman", "rm")
    ctx.sh.on("podman", "unshare", "tar",
              fn=lambda a, i: (open(a[a.index("-czf") + 1], "wb").write(b"x"), Result(0))[1])
    ctx.sh.on("podman", "unshare", "find", out=f"{tmp_path}/data/x\n")
    ctx.sh.on("podman", "unshare", "chown")
    f = adopt.detect(ctx)[0]
    adopt.apply(ctx, f, adopt.plan(ctx, f))
    assert ctx.sh.called("podman", "unshare", "chown")


def test_manual_steps_mention_backup_and_quadlet(tmp_path):
    ctx = actx(tmp_path, [container()])
    f = adopt.detect(ctx)[0]
    p = adopt.plan(ctx, f)
    st = state.load(ctx.paths)
    st["adopt_backup"] = "20260927T043000Z-adopt"
    state.save(ctx.paths, st)
    text = adopt.manual_steps(ctx, p)
    assert "20260927T043000Z-adopt.tar.gz" in text and "--numeric-owner" in text
    assert "systemctl --user start hermes-gateway.service" in text
