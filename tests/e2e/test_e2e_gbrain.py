# tests/e2e/test_e2e_gbrain.py
"""gbrain e2e (spec 2026-10-09 §8): a third app under the same hub, from fake releases
(tests/e2e/fake_release.py, one static C program per release). Runs after
test_e2e_clawvisor.py and before test_e2e_hub.py (plan ruling R16)."""
import json
from datetime import datetime

import pytest

from tests.e2e.conftest import HUB, as_user, talaria

pytestmark = pytest.mark.e2e
USER = "gbtest"
DATA = f"/home/{USER}/gbrain-data"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

CONF = """app = gbrain
repo = http://127.0.0.1:8092/gbrain
dashboard.bind = loopback
dashboard.public_url = https://brain.example.invalid
talaria_repo = /tmp/talaria-e2e/talaria-src
settle_seconds = 10
telegram_api = http://127.0.0.1:8081
disk.floor_gb = 0.2
min_release = v0.60.1.0
release_allow = v0.60.1.0
"""


def read(path):
    return as_user("cat", path, user=USER).stdout


def active():
    return as_user("systemctl", "--user", "is-active", "gbrain.service", user=USER,
                   check=False).stdout.strip() == "active"


def test_01_fresh_setup(gb_env):
    app = gb_env["app"]
    app.conf(CONF)
    out = app.setup_until_done()
    assert "brain initialized" in out and "gbrain is running" in out
    token = read(f"/home/{USER}/.config/talaria/gbrain.env").split(
        "GBRAIN_ADMIN_BOOTSTRAP_TOKEN=", 1)[1].split("\n", 1)[0]
    assert token and token not in out
    st = json.loads(read(f"/home/{USER}/.local/state/talaria/state.json"))
    assert st["current"]["tag"] == "v0.60.1.0"
    assert read(f"{DATA}/.talaria-schema").strip() == "1"
    assert "talaria rehearsal marker" in read(f"{DATA}/.gbrain/facts")
    health = as_user("python3", "-c", "import urllib.request; print(urllib.request.urlopen("
                     "'http://127.0.0.1:3131/health').read().decode())", user=USER).stdout
    assert json.loads(health)["status"] == "ok"


def test_02_offer_shows_the_schema_change_and_deploys(gb_env):
    app, rel = gb_env["app"], gb_env["rel"]
    rel.publish("0.60.2.0", 2)
    app.conf_add("release_allow = v0.60.1.0 v0.60.2.0\n")
    app.telegram.wait_polling()     # registering the app restarted the bot
    app.telegram.inject("/check gbrain")
    offer = app.telegram.wait_sent("gbrain v0.60.2.0 is ready to deploy", timeout=900)
    assert "Brain schema: 1 → 2" in offer
    app.telegram.tap("gbrain|ap:v0.60.2.0")
    app.telegram.wait_sent("Deployed gbrain v0.60.2.0.", timeout=900)
    assert read(f"{DATA}/.talaria-schema").strip() == "2"
    assert active()


def test_03_maintenance_runs_dream_and_restarts(gb_env):
    tg = gb_env["app"].telegram
    talaria("maintain", user=HUB)
    tg.wait_sent("gbrain maintenance finished.")
    assert read(f"{DATA}/.gbrain/dreams").count("dream") == 1
    assert active()


def test_04_failed_maintenance_reports_and_restarts(gb_env):
    tg = gb_env["app"].telegram
    env = f"/home/{USER}/.config/talaria/gbrain.env"
    as_user("sh", "-c", f"echo FAKE_DREAM_FAIL=1 >> {env}", user=USER)
    try:
        talaria("maintain", user=HUB)
        msg = tg.wait_sent("gbrain maintenance failed")
        assert "dream: phase synthesize failed" in msg
        assert active()
    finally:
        as_user("sed", "-i", "/^FAKE_DREAM_FAIL=/d", env, user=USER)


def test_05_the_timer_skips_gbrain_on_other_days_but_check_does_not(gb_env):
    app, rel = gb_env["app"], gb_env["rel"]
    rel.publish("0.60.3.0", 3)
    other = DAYS[(datetime.now().weekday() + 3) % 7]
    app.conf_add(f"check.days = {other}\nrelease_allow = v0.60.1.0 v0.60.2.0 v0.60.3.0\n")
    talaria("check", "--timer", user=HUB, timeout=1800)
    assert not any("gbrain v0.60.3.0 is ready" in t for t in app.telegram.sent)
    app.telegram.inject("/check gbrain")
    app.telegram.wait_sent("gbrain v0.60.3.0 is ready to deploy", timeout=900)


def test_06_rollback_restores_image_and_schema(gb_env):
    tg = gb_env["app"].telegram
    tg.inject("/rollback gbrain CONFIRM")
    tg.wait_sent("Rolled back to gbrain v0.60.1.0.", timeout=900)
    assert read(f"{DATA}/.talaria-schema").strip() == "1"
    assert active()
