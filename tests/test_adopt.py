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
    ctx.paths.quadlet.write_text("# Managed by Talaria. x\n")
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
    assert "systemctl --user enable --now hermes-gateway.service" in text


# ---- exact behaviour (mutation testing) ----

def apply_ctx(tmp_path, *, find_out="", fail_stops=False, quadlet=True):
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("_config_version: 27\n")
    ctx = actx(tmp_path, [container(mounts=[{"Type": "bind", "Source": str(data),
                                             "Destination": "/opt/data"}],
                                    env=["HERMES_HOME=/opt/data", "PATH=/usr/bin", "TZ=UTC",
                                         "HERMES_X=a=b"])])
    if quadlet:
        ctx.paths.quadlet_dir.mkdir(parents=True)
        (ctx.paths.quadlet_dir / "hermes-gateway.container").write_text("[Container]\n")

    def fake_tar(argv, input):
        open(argv[argv.index("-czf") + 1], "wb").write(b"archive-bytes")
        return Result(0)

    rc = 5 if fail_stops else 0
    ctx.sh.on("systemctl", rc=rc).on("podman", "stop", rc=rc).on("podman", "rm", rc=rc)
    ctx.sh.on("podman", "unshare", "tar", fn=fake_tar)
    ctx.sh.on("podman", "unshare", "find", out=find_out)
    ctx.sh.on("podman", "unshare", "chown")
    return ctx, data


def test_apply_exact_commands_and_sidecar(tmp_path):
    import hashlib
    ctx, data = apply_ctx(tmp_path)
    f = adopt.detect(ctx)[0]
    p = adopt.plan(ctx, f)
    before = len(ctx.sh.calls)
    assert adopt.apply(ctx, f, p) == 0
    bk = ctx.paths.backups
    tmp = str(bk / ".20260927T043000Z-adopt.tar.gz.tmp")
    got = list(zip(ctx.sh.calls, ctx.sh.timeouts))[before:]
    assert got == [
        (["systemctl", "--user", "stop", "hermes-gateway.service"], 600),
        (["podman", "stop", "id-hermes-gateway"], 600),
        (["podman", "unshare", "tar", "--numeric-owner", "-czf", tmp, "-C", str(data), "."], 7200),
        (["podman", "unshare", "find", str(data), "!", "-user", "0", "-print", "-quit"], None),
        (["podman", "rm", "-f", "id-hermes-gateway"], None),
    ]
    side = json.loads((bk / "20260927T043000Z-adopt.json").read_text())
    assert side == {"id": "20260927T043000Z-adopt", "label": "adopt",
                    "created": "2026-09-27T04:30:00+00:00", "image": p.image, "cfg_version": 27,
                    "sha256": hashlib.sha256(b"archive-bytes").hexdigest(), "size": 13,
                    "data_size": len("_config_version: 27\n")}
    assert (bk / "20260927T043000Z-adopt.tar.gz").read_bytes() == b"archive-bytes"
    assert not (bk / ".20260927T043000Z-adopt.tar.gz.tmp").exists()
    assert (bk.stat().st_mode & 0o777) == 0o700
    assert parse_kv(ctx.paths.hermes_env.read_text()) == {"TZ": "UTC", "HERMES_X": "a=b"}


def test_apply_tolerates_already_stopped_and_removed(tmp_path):
    ctx, data = apply_ctx(tmp_path, fail_stops=True)
    f = adopt.detect(ctx)[0]
    assert adopt.apply(ctx, f, adopt.plan(ctx, f)) == 0


def test_apply_chown_exact(tmp_path):
    ctx, data = apply_ctx(tmp_path, find_out="  x\n")
    f = adopt.detect(ctx)[0]
    adopt.apply(ctx, f, adopt.plan(ctx, f))
    i = ctx.sh.calls.index(["podman", "unshare", "chown", "-R", "0:0", str(data)])
    assert ctx.sh.timeouts[i] == 3600


def test_apply_without_quadlet_disables_generated_unit(tmp_path, capsys):
    ctx, data = apply_ctx(tmp_path, quadlet=False)
    ctx.paths.units_dir.mkdir(parents=True)
    unit_file = ctx.paths.units_dir / "hermes-gateway.service"
    unit_file.write_text("[Service]\n")
    f = adopt.detect(ctx)[0]
    assert f.quadlet is None
    adopt.apply(ctx, f, adopt.plan(ctx, f))
    assert ["systemctl", "--user", "disable", "hermes-gateway.service"] in ctx.sh.calls
    assert not unit_file.exists()
    assert (ctx.paths.units_dir / "hermes-gateway.service.talaria-orig").read_text() == "[Service]\n"
    assert capsys.readouterr().out.endswith("OK: adopted hermes-gateway.service; backup "
                                            "20260927T043000Z-adopt\n")


def test_apply_without_quadlet_or_unit_file(tmp_path):
    ctx, data = apply_ctx(tmp_path, quadlet=False)
    f = adopt.detect(ctx)[0]
    assert adopt.apply(ctx, f, adopt.plan(ctx, f)) == 0


def test_detect_exact_found(tmp_path):
    ctx = actx(tmp_path, [container(env=["HERMES_HOME=/opt/data", "A=x=y", "NOEQUALS"])])
    f = adopt.detect(ctx)[0]
    assert f == adopt.Found(unit="hermes-gateway.service", container="id-hermes-gateway",
                            name="hermes-gateway", image_id="sha256:old",
                            mounts=[{"Type": "bind", "Source": "/home/h/data",
                                     "Destination": "/opt/data"}],
                            env={"HERMES_HOME": "/opt/data", "A": "x=y"}, quadlet=None)
    assert ctx.sh.calls[0] == ["podman", "ps", "-a", "--format", "json"]
    assert ctx.sh.calls[1] == ["podman", "container", "inspect", "id-hermes-gateway"]


def test_detect_without_systemd_label(tmp_path):
    c = container()
    c["Config"]["Labels"] = None
    ctx = actx(tmp_path, [c])
    assert adopt.detect(ctx)[0].unit == "container-hermes-gateway.service"


def test_detect_no_containers(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "ps", out="")
    assert adopt.detect(ctx) == []
    assert len(ctx.sh.calls) == 1


def test_detect_hermes_service_without_own_quadlet_is_found(tmp_path):
    ctx = actx(tmp_path, [container("hermes", "hermes.service")])
    assert [f.unit for f in adopt.detect(ctx)] == ["hermes.service"]


def test_plan_problem_texts_exact(tmp_path):
    ctx = actx(tmp_path, [container(mounts=[{"Type": "bind", "Source": "/a", "Destination": "/x"}])])
    ctx.sh.on("podman", "run", out="no version here")
    p = adopt.plan(ctx, adopt.detect(ctx)[0])
    assert p.problems == ["only a single bind mount at /opt/data is supported; found: /a→/x",
                          "Hermes of unknown version is older than v2026.6.5; update it by hand first"]
    assert p.data_dir is None and p.diff == ""


def test_plan_no_mounts_text(tmp_path):
    ctx = actx(tmp_path, [container(mounts=[])])
    assert adopt.plan(ctx, adopt.detect(ctx)[0]).problems[0].endswith("found: none")


def test_plan_old_version_text(tmp_path):
    ctx = actx(tmp_path, [container()])
    ctx.sh.on("podman", "run", out="Hermes Agent v0.10.0 (2026.4.3)\n")
    assert adopt.plan(ctx, adopt.detect(ctx)[0]).problems == [
        "Hermes v2026.4.3 is older than v2026.6.5; update it by hand first"]


def test_plan_exact_floor_is_accepted(tmp_path):
    ctx = actx(tmp_path, [container()])
    assert adopt.plan(ctx, adopt.detect(ctx)[0]).problems == []


def test_plan_diff_headers_and_restores_conf(tmp_path):
    ctx = actx(tmp_path, [container()])
    ctx.paths.quadlet_dir.mkdir(parents=True)
    q = ctx.paths.quadlet_dir / "hermes-gateway.container"
    q.write_text("[Container]\nImage=old\n")
    before = ctx.conf.data_dir
    p = adopt.plan(ctx, adopt.detect(ctx)[0])
    assert p.diff.startswith(f"--- {q}\n+++ hermes.container\n")
    assert "-Image=old\n" in p.diff and "+Volume=/home/h/data:/opt/data\n" in p.diff
    assert ctx.conf.data_dir == before


def test_plan_diff_without_quadlet(tmp_path):
    ctx = actx(tmp_path, [container()])
    p = adopt.plan(ctx, adopt.detect(ctx)[0])
    assert p.diff.startswith("--- hermes-gateway.service\n+++ hermes.container\n")
    assert "-(container without a Quadlet file)\n" in p.diff


def test_plan_managed_vars_dropped_even_if_hermes_prefixed(tmp_path):
    env = ["HERMES_HOME=/opt/data"] + [f"{k}=v" for k in sorted(adopt.MANAGED - {"HERMES_HOME"})]
    ctx = actx(tmp_path, [container(env=env)])
    p = adopt.plan(ctx, adopt.detect(ctx)[0])
    assert p.kept == {} and sorted(p.dropped) == sorted(adopt.MANAGED - {"HERMES_HOME"})


def test_print_plan_exact(capsys):
    f = adopt.Found("u.service", "c", "n", "i", [], {}, None)
    adopt.print_plan(adopt.Plan(f, {"tag": "v2026.8.3"}, "/d", {"TZ": "UTC", "HERMES_A": "1"},
                                ["FOO", "BAR"], [], "DIFFTEXT"))
    assert capsys.readouterr().out == ("FOUND: Hermes v2026.8.3 in unit u.service, data at /d\n"
                                       "  environment kept: TZ, HERMES_A\n"
                                       "  environment dropped: FOO, BAR\n"
                                       "DIFFTEXT\n")


def test_print_plan_minimal(capsys):
    f = adopt.Found("u.service", "c", "n", "i", [], {}, None)
    adopt.print_plan(adopt.Plan(f, None, None, {}, [], [], ""))
    assert capsys.readouterr().out == "FOUND: Hermes None in unit u.service, data at None\n"


def test_manual_steps_exact(tmp_path):
    ctx = actx(tmp_path, [container()])
    ctx.paths.quadlet_dir.mkdir(parents=True)
    q = ctx.paths.quadlet_dir / "hermes-gateway.container"
    q.write_text("x")
    p = adopt.plan(ctx, adopt.detect(ctx)[0])
    st = state.load(ctx.paths)
    st["adopt_backup"] = "B"
    state.save(ctx.paths, st)
    assert adopt.manual_steps(ctx, p) == "\n".join([
        "Manual way back to the previous install:",
        "  systemctl --user stop hermes.service",
        "  mv /home/h/data /home/h/data.talaria-failed && mkdir /home/h/data",
        f"  podman unshare tar --numeric-owner -xzf {ctx.paths.backups}/B.tar.gz -C /home/h/data",
        f"  rm {ctx.paths.quadlet}",
        f"  mv {ctx.paths.quadlet_dir}/hermes-gateway.container.talaria-orig {q}",
        "  systemctl --user daemon-reload",
        "  systemctl --user start hermes-gateway.service",
    ])


def test_manual_steps_without_backup_or_quadlet(tmp_path):
    ctx = actx(tmp_path, [container()])
    text = adopt.manual_steps(ctx, adopt.plan(ctx, adopt.detect(ctx)[0]))
    assert "<adopt backup>.tar.gz" in text
    u = ctx.paths.units_dir / "hermes-gateway.service"
    assert text.endswith(f"  rm {ctx.paths.quadlet}\n"
                         f"  mv {u}.talaria-orig {u}   # if it exists\n"
                         "  systemctl --user daemon-reload\n"
                         "  systemctl --user enable --now hermes-gateway.service")


# ---- review C2: only Talaria's own hermes.container counts as managed ----

def test_users_own_hermes_container_is_detected(tmp_path):
    ctx = actx(tmp_path, [container("hermes", "hermes.service")])
    ctx.paths.quadlet_dir.mkdir(parents=True)
    ctx.paths.quadlet.write_text("[Container]\nImage=x\n")      # written by the user
    found = adopt.detect(ctx)
    assert [f.unit for f in found] == ["hermes.service"]
    assert found[0].quadlet == ctx.paths.quadlet


def test_is_managed(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert adopt.is_managed(ctx) is False
    ctx.paths.quadlet_dir.mkdir(parents=True)
    ctx.paths.quadlet.write_text("[Container]\n")
    assert adopt.is_managed(ctx) is False
    ctx.paths.quadlet.write_text("# Managed by Talaria. Re-run ...\n[Unit]\n")
    assert adopt.is_managed(ctx) is True


def test_stopped_quadlets_found(tmp_path):
    ctx = actx(tmp_path, [])
    ctx.sh.on("podman", "ps", out="[]")
    ctx.paths.quadlet_dir.mkdir(parents=True)
    (ctx.paths.quadlet_dir / "old.container").write_text("[Container]\nVolume=/d:/opt/data\n")
    (ctx.paths.quadlet_dir / "db.container").write_text("[Container]\nImage=postgres\n")
    (ctx.paths.quadlet_dir / "x.container.talaria-orig").write_text("Volume=/d:/opt/data\n")
    assert adopt.stopped_quadlets(ctx, []) == [ctx.paths.quadlet_dir / "old.container"]
    f = adopt.Found("old.service", "c", "n", "i", [], {}, ctx.paths.quadlet_dir / "old.container")
    assert adopt.stopped_quadlets(ctx, [f]) == []


def test_plan_refuses_mount_point_data_dir(tmp_path, monkeypatch):
    ctx = actx(tmp_path, [container()])
    monkeypatch.setattr(adopt, "renamable", lambda p: False)
    assert adopt.plan(ctx, adopt.detect(ctx)[0]).problems == [
        "the data dir /home/h/data is a mount point or on another filesystem than its parent; "
        "Talaria restores by renaming it, so it must be a plain directory"]
