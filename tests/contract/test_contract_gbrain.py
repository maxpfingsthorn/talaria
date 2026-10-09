# tests/contract/test_contract_gbrain.py
"""Contract test against the newest real gbrain release (TALARIA_CONTRACT=1; network,
podman, ~200 MB). Pins what the adapter assumes about the real CLI: the release API's
digest, `--version`, `init --pglite`, `config set`, `remember`/`recall` and the shape of
`doctor --json` (parse_schema)."""
import os

import pytest

from talaria import apps, images, rehearse, tags
from talaria.conf import Conf
from talaria.ctx import Ctx, Paths
from talaria.shell import Shell

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if os.environ.get("TALARIA_CONTRACT") != "1":
        pytest.skip("set TALARIA_CONTRACT=1")
    home = tmp_path_factory.mktemp("home")
    app = apps.get("gbrain")
    conf = Conf(data_dir=home / "gbrain-data", app="gbrain", repo=app.default_repo,
                min_release=app.min_release, dashboard_public_url="https://brain.example.invalid")
    ctx = Ctx(paths=Paths(home, "gbrain"), conf=conf, sh=Shell(), notify=None, app=app)
    git = tags.releases(ctx)
    tag = max(git, key=app.tag_key)
    return ctx, app.fetch(ctx, tag, git[tag])


def test_a_fresh_brain_initializes_and_rehearses(built):
    ctx, rec = built
    ctx.app.prepare(ctx)
    images.retag(ctx, "current", rec)
    assert ctx.app.initialize(ctx)
    v = ctx.app.data_version(ctx.conf.data_dir)
    assert isinstance(v, int) and v > 0
    copy = ctx.paths.home / "copy"
    rehearse.copy_data(ctx.conf.data_dir, copy, ())
    report = ctx.app.rehearse(ctx, {}, rec, copy, None)
    assert report["after"] == v and report["before"] == v


def test_b_cli_shapes_the_adapter_relies_on(built):
    """Facts learned from the real binary: `remember` needs --provenance, `recall --query`
    degrades to keywords without an embedding provider (still finds the marker), and
    `doctor --json` may be wrapped in bracketed notices (parse_schema copes)."""
    from talaria.apps.gbrain import MARKER, MARKER_TEXT, oneoff, parse_schema
    ctx, rec = built
    data, image = ctx.conf.data_dir, rec["id"]
    assert oneoff(ctx, image, data, ["remember", MARKER_TEXT]).returncode != 0
    out = oneoff(ctx, image, data, ["recall", "--query", MARKER])
    assert out.returncode == 0 and MARKER in out.stdout.lower()
    assert parse_schema(oneoff(ctx, image, data, ["doctor", "--json"]).stdout) > 0
