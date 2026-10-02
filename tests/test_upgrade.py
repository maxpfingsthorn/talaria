from __future__ import annotations

from pathlib import Path

from talaria import ctx as ctxmod
from talaria import rollback, state, units

GOLDEN = Path(__file__).parent / "golden"
# The literal home path baked into tests/golden/default.hermes.container (see test_units.py).
GOLDEN_HOME = Path("/home/talaria-fixture")


def test_v025_install_without_app_key_keeps_working(tmp_path, monkeypatch):
    # The v0.2.5 conf is written *before* the ctx is built, through make_ctx (with
    # Path.home monkeypatched), so load_conf actually parses it -- unlike the previous
    # version of this test, which wrote the file after make_test_ctx had already
    # called load_conf, so the file was never read.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    conf_dir = tmp_path / ".config/talaria"
    conf_dir.mkdir(parents=True)
    conf_dir.joinpath("talaria.conf").write_text("data_dir = ~/hermes-data\n")  # no `app =` line

    ctx = ctxmod.make_ctx()
    assert ctx.app.name == "hermes"
    assert ctx.paths.app == "hermes"

    st = state.load(ctx.paths)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": "b", "changed": True,
                "started": "2026-09-27T04:00:00+00:00"}
    # A v0.2.5-shaped pending record: only the keys that version ever wrote.
    pending = {"tag": "v2026.9.24", "image": {"id": "sha256:new"}, "cfg_after": 30, "report": {}}
    st["pending"] = pending
    state.save(ctx.paths, st)

    assert rollback.interrupted(ctx, state.load(ctx.paths)).startswith("an interrupted deploy")
    # The v0.2.5 pending record is acted on unchanged: nothing strips or rewrites it
    # just because it predates the app/adapter keys of v0.3.0.
    assert state.load(ctx.paths)["pending"] == pending

    expected = (GOLDEN / "default.hermes.container").read_text().replace(
        str(GOLDEN_HOME), str(tmp_path))
    assert units.render_quadlet(ctx) == expected
