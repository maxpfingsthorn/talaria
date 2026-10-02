from __future__ import annotations

from talaria import rollback, state, units
from tests.fakes import make_test_ctx


def test_v025_install_without_app_key_keeps_working(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.paths.conf_file.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.conf_file.write_text("data_dir = ~/hermes-data\n")
    st = state.load(ctx.paths)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": "b", "changed": True,
                "started": "2026-09-27T04:00:00+00:00"}
    state.save(ctx.paths, st)
    assert ctx.app.name == "hermes"
    assert rollback.interrupted(ctx, state.load(ctx.paths)).startswith("an interrupted deploy")
    assert units.render_quadlet(ctx).startswith("# Managed by Talaria.")
