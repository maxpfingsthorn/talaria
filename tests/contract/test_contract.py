"""Weekly contract test against the newest official Hermes image (spec §13.3).
Needs network, podman and ~4 GB of disk. Enabled with TALARIA_CONTRACT=1."""
import os

import pytest

from talaria import containers, images, tags, upstream
from talaria.conf import Conf
from talaria.ctx import Ctx, Paths
from talaria.shell import Shell

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def img(tmp_path_factory):
    if os.environ.get("TALARIA_CONTRACT") != "1":
        pytest.skip("set TALARIA_CONTRACT=1")
    home = tmp_path_factory.mktemp("home")
    ctx = Ctx(paths=Paths(home), conf=Conf(data_dir=home / "data"), sh=Shell(), notify=None)
    git = upstream.git_release_tags(ctx.sh, ctx.conf.hermes_repo)
    reg = upstream.registry_tags(ctx.sh, ctx.conf.image)
    tag = tags.pick_candidate(git, reg, None, set(), ctx.conf.min_release)   # F1, F3
    assert tag, "no release tag with a published image"
    rec = images.pull_verify(ctx, tag, git[tag])                             # F2
    return ctx, rec


def run(ctx, rec, entry, *args):
    return ctx.sh.run(["podman", "run", "--rm", "--network=none", "--entrypoint", entry,
                       rec["id"], *args], check=False)


def test_uid_10000(img):                                                    # F4
    ctx, rec = img
    assert ":10000:10000:" in run(ctx, rec, "getent", "passwd", "hermes").stdout


def test_skip_variable_exists(img):                                          # F6
    ctx, rec = img
    assert run(ctx, rec, "grep", "-q", "HERMES_SKIP_CONFIG_MIGRATION",
               "/opt/hermes/scripts/docker_config_migrate.py").returncode == 0


def test_dashboard_contract(img):                                            # F10, F11
    ctx, rec = img
    script = run(ctx, rec, "cat", "/opt/hermes/docker/s6-rc.d/dashboard/run").stdout
    assert "HERMES_DASHBOARD:-" in script and "BASIC_AUTH_USERNAME" in script
    assert run(ctx, rec, "grep", "-rlq", "auth_required", "/opt/hermes/hermes_cli").returncode == 0


def test_helpers_against_real_image(img, tmp_path):
    ctx, rec = img
    data, work = tmp_path / "data", tmp_path / "work"
    data.mkdir()
    work.mkdir()
    mig = containers.run_helper(ctx, rec, "migrate.py", data, work)
    assert mig["ok"] and mig["after"] == mig["latest"], mig
    db = containers.run_helper(ctx, rec, "dbopen.py", data, work, args=["--create"])
    assert db["ok"] and db["after"] and db["after"] <= db["schema_version"], db
    (work / "a.yaml").write_text("x: 1\nmodel: {api_key: s}\n")
    (work / "b.yaml").write_text("x: 2\nmodel: {api_key: t}\n")
    diff = containers.run_helper(ctx, rec, "confdiff.py", data, work,
                                 args=["/opt/talaria-out/a.yaml", "/opt/talaria-out/b.yaml"])
    assert diff["changed"] == [["model.api_key", "***", "***"], ["x", 1, 2]], diff
    assert containers.run_doctor(ctx, rec, data).strip()


def test_version_output(img):                                                # F18
    ctx, rec = img
    out = run(ctx, rec, "/opt/hermes/.venv/bin/hermes", "--version").stdout
    assert images.version_tag(out) == rec["tag"], out
