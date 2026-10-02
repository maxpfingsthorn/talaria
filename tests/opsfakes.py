import copy

import pytest
import json

from talaria import state
from talaria.shell import Result, Shell
from tests.fakes import make_test_ctx

CUR = {"tag": "v2026.8.3", "id": "sha256:cur", "ref": "x@sha256:c", "digest": "sha256:c"}
NEW = {"tag": "v2026.9.24", "id": "sha256:new", "ref": "x@sha256:n", "digest": "sha256:n"}


def ops_ctx(tmp_path, monkeypatch, *, migrate=None, check_results=None):
    """A ctx whose podman/systemctl are faked, tar is real, and helpers are scripted.

    check_results: list of post_start_check outcomes (None = pass), consumed in order.
    """
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl").on("podman", "tag").on("podman", "image", "exists")
    ctx.sh.on("podman", "images", out="[]").on("podman", "rmi")
    ctx.sh.on("tar", fn=lambda argv, input: Shell().run(argv, check=False))
    ctx.sh.on("git", fn=lambda argv, input: Shell().run(argv, check=False))
    d = ctx.conf.data_dir
    (d / "config.yaml").write_text("_config_version: 27\n")
    (d / "memories").mkdir()
    (d / "memories/m.md").write_text("before")
    st = copy.deepcopy(state.DEFAULT)
    st["current"] = dict(CUR)
    st["pending"] = {"tag": "v2026.9.24", "image": dict(NEW), "cfg_after": 30, "report": {}}
    state.save(ctx.paths, st)

    mig = migrate or {"ok": True, "before": 27, "after": 30, "latest": 30,
                      "messages": [], "error": None}

    def fake_helper(ctx_, rec, script, data, work, args=()):
        assert script == "migrate.py"
        if mig["ok"]:
            (data / "config.yaml").write_text(f"_config_version: {mig['after']}\n")
        return mig

    outcomes = list(check_results or [None])
    ctx.checks = outcomes

    from talaria import deploy, rollback
    from talaria.apps import hermes as hermes_app
    monkeypatch.setattr(hermes_app, "run_helper", fake_helper)
    for mod in (deploy, rollback):
        monkeypatch.setattr(mod.service, "post_start_check",
                            lambda c: (c is ctx or pytest.fail("post_start_check got a wrong ctx"))
                            and (outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]))
    return ctx


def load(ctx):
    return state.load(ctx.paths)
