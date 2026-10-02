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
    ctx.conf.hermes_repo = "https://example/repo"
    ctx.conf.image = "img:tag"
    ctx.conf.registry_tls_verify = False
    calls = {}
    monkeypatch.setattr(h, "git_release_tags",
                        lambda sh, repo: (calls.__setitem__("releases", (sh, repo)),
                                          {"v2026.9.24": "c"})[1])
    monkeypatch.setattr(h, "registry_tags",
                        lambda sh, image, tls_verify: (
                            calls.__setitem__("published", (sh, image, tls_verify)),
                            {"v2026.9.24"})[1])
    monkeypatch.setattr(h, "pull_verify",
                        lambda c, tag, commit: (calls.__setitem__("fetch", c),
                                                {"tag": tag, "id": commit})[1])
    app = ctx.app
    assert app.releases(ctx) == {"v2026.9.24": "c"}
    assert calls["releases"] == (ctx.sh, "https://example/repo")
    assert app.published(ctx, ["v2026.9.24"]) == {"v2026.9.24"}
    assert calls["published"] == (ctx.sh, "img:tag", False)
    assert app.fetch(ctx, "v2026.9.24", "c") == {"tag": "v2026.9.24", "id": "c"}
    assert calls["fetch"] is ctx


def test_hermes_reacquire_tls_flag_exact(tmp_path):
    from tests.fakes import make_test_ctx
    ctx = make_test_ctx(tmp_path)   # registry_tls_verify defaults to True
    ctx.sh.on("podman", "pull")
    ctx.app.reacquire(ctx, {"id": "sha256:i1", "ref": "x@d"})
    assert ctx.sh.calls[-1] == ["podman", "pull", "-q", "x@d"]

    ctx2 = make_test_ctx(tmp_path, registry_tls_verify=False)
    ctx2.sh.on("podman", "pull")
    ctx2.app.reacquire(ctx2, {"id": "sha256:i1", "ref": "x@d"})
    assert ctx2.sh.calls[-1] == ["podman", "pull", "-q", "--tls-verify=false", "x@d"]


def test_hermes_prepare_generates_a_24_byte_token(tmp_path, monkeypatch):
    from talaria.apps import hermes as h
    from tests.fakes import make_test_ctx
    ctx = make_test_ctx(tmp_path)
    calls = []
    monkeypatch.setattr(h.secrets, "token_urlsafe", lambda n: (calls.append(n), "tok")[1])
    ctx.app.prepare(ctx)
    assert calls == [24]


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
