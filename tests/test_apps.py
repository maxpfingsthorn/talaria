import pytest

from talaria import apps
from talaria.conf import load_conf
from talaria.ctx import Paths


def test_registry():
    h = apps.get("hermes")
    assert (h.name, h.title, h.unit, h.container) == ("hermes", "Hermes", "hermes.service", "hermes")
    assert (h.quadlet_file, h.env_file, h.local_image) == ("hermes.container", "hermes.env",
                                                          "localhost/hermes-agent")
    assert (h.default_port, h.min_release) == (9119, "v2026.6.5")
    with pytest.raises(ValueError, match="unknown app: 'nope'"):
        apps.get("nope")


def test_conf_defaults_to_hermes(tmp_path):
    assert load_conf(Paths(tmp_path)).app == "hermes"


def test_conf_app_key_is_validated(tmp_path):
    p = Paths(tmp_path)
    p.conf_file.parent.mkdir(parents=True)
    p.conf_file.write_text("app = nope\n")
    with pytest.raises(ValueError, match="unknown app: 'nope'"):
        load_conf(p)


def test_paths_follow_the_app(tmp_path):
    p = Paths(tmp_path)
    assert p.quadlet == tmp_path / ".config/containers/systemd/hermes.container"
    assert p.app_env == p.hermes_env == tmp_path / ".config/talaria/hermes.env"


def test_hermes_releases_published_fetch(tmp_path, monkeypatch):
    from talaria.apps import hermes as h
    from tests.fakes import make_test_ctx
    ctx = make_test_ctx(tmp_path)
    monkeypatch.setattr(h, "git_release_tags", lambda sh, repo: {"v2026.9.24": "c"})
    monkeypatch.setattr(h, "registry_tags", lambda sh, image, tls_verify=True: {"v2026.9.24"})
    monkeypatch.setattr(h, "pull_verify", lambda c, tag, commit: {"tag": tag, "id": commit})
    app = ctx.app
    assert app.releases(ctx) == {"v2026.9.24": "c"}
    assert app.published(ctx, ["v2026.9.24"]) == {"v2026.9.24"}
    assert app.fetch(ctx, "v2026.9.24", "c") == {"tag": "v2026.9.24", "id": "c"}


def test_generic_rehearse_delegates_to_the_app(tmp_path, monkeypatch):
    from talaria import rehearse, state
    from tests.fakes import make_test_ctx
    ctx = make_test_ctx(tmp_path)
    (ctx.conf.data_dir / "f").write_text("x")
    seen = {}

    class FakeApp(type(ctx.app)):
        def fetch(self, c, tag, commit):
            return {"tag": tag, "id": "sha256:i", "digest": "d"}
        def rehearse(self, c, st, image, copy, stage):
            seen["copy"] = (copy / "f").read_text()
            return {"tag": image["tag"], "digest": "d", "x": 1}
        def report_lines(self, c, report):
            return ["Line"], [("Block", "body")]
        def pending_extra(self, report):
            return {"x": report["x"]}
    ctx.app = FakeApp()
    st = state.load(ctx.paths)
    rehearse.rehearse(ctx, st, "v2026.9.24", "c")
    assert seen["copy"] == "x" and st["pending"]["x"] == 1
    m = ctx.notify.sent[-1]
    assert "Line" in m.text and m.untrusted == [("Block", "body")]
    assert m.buttons == [[("Approve v2026.9.24", "ap:v2026.9.24"), ("Reject", "rj:v2026.9.24")]]
