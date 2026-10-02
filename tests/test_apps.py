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
