"""Weekly contract test against the newest real Clawvisor release. Needs network, podman
and disk. Enabled with TALARIA_CONTRACT=1."""
import os

import pytest

from talaria import apps, tags
from talaria.conf import Conf
from talaria.ctx import Ctx, Paths
from talaria.shell import Shell

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def image(tmp_path_factory):
    if os.environ.get("TALARIA_CONTRACT") != "1":
        pytest.skip("set TALARIA_CONTRACT=1")
    home = tmp_path_factory.mktemp("home")
    app = apps.get("clawvisor")
    conf = Conf(data_dir=home / "clawvisor-data", app="clawvisor", repo=app.default_repo,
               min_release=app.min_release, settle_seconds=30)
    ctx = Ctx(paths=Paths(home, "clawvisor"), conf=conf, sh=Shell(), notify=None, app=app)
    git = tags.releases(ctx)
    tag = max(git, key=app.tag_key)
    assert tag, "no Clawvisor release tag found"
    rec = app.fetch(ctx, tag, git[tag])
    return ctx, rec


def test_fresh_release_passes_healthcheck_and_has_migrations(image):
    """Fetches the newest Clawvisor release, starts it offline (--network=none) on an
    empty data dir with freshly generated secrets, and checks it the same way Talaria's
    rehearsal does: a passing healthcheck within settle_seconds and migrations applied."""
    ctx, rec = image
    for _ in ctx.app.prepare(ctx):     # generates JWT_SECRET and vault.key on the empty dir
        pass
    report = ctx.app.rehearse(ctx, {}, rec, ctx.conf.data_dir, None)
    assert report["after"] > 0 and report["latest"], report
