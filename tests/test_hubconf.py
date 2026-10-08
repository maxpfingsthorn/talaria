import pytest

from talaria import hubconf, setup
from talaria.conf import read_telegram_env
from talaria.ctx import Paths, make_hub_ctx
from talaria.hubconf import HubConf, is_hub, load_hub_conf, register_app


def home(tmp_path, conf=None, env=None):
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True, exist_ok=True)
    if conf is not None:
        p.hub_conf.write_text(conf)
    if env is not None:
        p.env_file.write_text(env)
    return p


def test_role_is_the_presence_of_hub_conf(tmp_path):
    p = home(tmp_path)
    assert not is_hub(p)
    p.hub_conf.write_text("")
    assert is_hub(p)
    assert p.hub_conf == tmp_path / ".config/talaria/hub.conf"
    assert p.hub_state == tmp_path / ".local/state/talaria/hub.json"


def test_defaults_without_a_file(tmp_path):
    c = load_hub_conf(Paths(tmp_path))
    assert c == HubConf()
    assert (c.apps, c.check_time, c.talaria_repo, c.telegram_api, c.telegram_token,
            c.telegram_user_id) == ((), "04:30", "https://github.com/maxpfingsthorn/talaria",
                                    "https://api.telegram.org", "", 0)


def test_keys_and_secrets(tmp_path):
    p = home(tmp_path, "# hub\napps = hermes:hermes clawvisor:cv\ncheck.time = 03:15\n"
                       "talaria_repo = /srv/t\ntelegram_api = http://127.0.0.1:8081\n",
             "TALARIA_TELEGRAM_TOKEN=1:abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    c = load_hub_conf(p)
    assert c.apps == (("hermes", "hermes"), ("clawvisor", "cv"))
    assert (c.check_time, c.talaria_repo, c.telegram_api) == ("03:15", "/srv/t",
                                                              "http://127.0.0.1:8081")
    assert (c.telegram_token, c.telegram_user_id) == ("1:abc", 42)
    assert "1:abc" not in repr(c)


@pytest.mark.parametrize("line,msg", [
    ("colour = red", "unknown key in"),
    ("apps = hermes", "'hermes' is not <app>:<user>"),
    ("apps = hub:talaria", "'hub:talaria' is not <app>:<user>"),
    ("apps = nope:x", "'nope:x' is not <app>:<user> with app one of hermes, clawvisor"),
    ("apps = hermes:bad;name", "not a valid account name: 'bad;name'"),
    ("apps = hermes:a hermes:b", "hermes is registered twice"),
    ("apps = hermes:a clawvisor:a", "the account a is registered twice"),
])
def test_invalid_hub_conf(tmp_path, line, msg):
    p = home(tmp_path, line + "\n")
    with pytest.raises(ValueError, match=msg.replace("(", r"\(")):
        load_hub_conf(p, strict=True)


def test_register_into_an_empty_conf(tmp_path):
    p = home(tmp_path, "# Talaria hub settings; see README.\n")
    assert register_app(p, "hermes", "hermes") is True
    assert p.hub_conf.read_text() == ("# Talaria hub settings; see README.\n"
                                      "apps = hermes:hermes\n")


def test_register_keeps_comments_order_and_place(tmp_path):
    p = home(tmp_path, "# hub\napps = hermes:hermes\ncheck.time = 03:00\n")
    assert register_app(p, "clawvisor", "clawvisor") is True
    assert p.hub_conf.read_text() == ("# hub\napps = hermes:hermes clawvisor:clawvisor\n"
                                      "check.time = 03:00\n")
    assert register_app(p, "clawvisor", "clawvisor") is False
    assert p.hub_conf.read_text().count("clawvisor:clawvisor") == 1


def test_register_refuses_the_same_app_for_another_account(tmp_path):
    p = home(tmp_path, "apps = hermes:hermes\n")
    with pytest.raises(ValueError, match="already registers hermes for the account hermes"):
        register_app(p, "hermes", "hermes2")


def test_register_refuses_an_account_already_serving_another_app(tmp_path):
    p = home(tmp_path, "apps = hermes:agent\n")
    with pytest.raises(ValueError, match="the account agent is registered twice"):
        register_app(p, "clawvisor", "agent")
    assert p.hub_conf.read_text() == "apps = hermes:agent\n"


@pytest.mark.parametrize("app,user", [("hub", "x"), ("talaria", "x"), ("nope", "x"), ("hermes", "a b")])
def test_register_validates_the_entry(tmp_path, app, user):
    p = home(tmp_path, "")
    with pytest.raises(ValueError):
        register_app(p, app, user)


def test_read_telegram_env(tmp_path):
    assert read_telegram_env(tmp_path / "missing") == ("", 0)
    f = tmp_path / ".env"
    f.write_text("TALARIA_TELEGRAM_TOKEN=1:x\nTALARIA_TELEGRAM_USER_ID=\nOTHER=1\n")
    assert read_telegram_env(f) == ("1:x", 0)


def test_make_hub_ctx(tmp_path):
    home(tmp_path, "apps = hermes:hermes\n", "TALARIA_TELEGRAM_TOKEN=1:abc\n")
    ctx = make_hub_ctx(tmp_path)
    assert ctx.paths.home == tmp_path and ctx.app is None
    assert ctx.conf.apps == (("hermes", "hermes"),) and ctx.conf.telegram_token == "1:abc"
    assert ctx.notify.conf is ctx.conf


def test_setup_uses_the_same_name_rule():
    assert setup.NAME_RE is hubconf.NAME_RE


def test_hub_conf_unknown_key_warns_at_runtime(tmp_path, capsys):
    p = home(tmp_path, "colour = red\ncheck.time = 05:00\n")
    assert load_hub_conf(p).check_time == "05:00"
    assert capsys.readouterr().err == \
        f"[talaria] ignoring unknown key in {p.hub_conf}: colour (newer Talaria?)\n"
    assert make_hub_ctx(tmp_path).conf.check_time == "05:00"
    with pytest.raises(ValueError, match="unknown key in .*: colour"):
        load_hub_conf(p, strict=True)


def test_hub_conf_bad_known_value_still_errors_at_runtime(tmp_path):
    p = home(tmp_path, "colour = red\napps = hermes\n")
    with pytest.raises(ValueError, match="not <app>:<user>"):
        load_hub_conf(p)
