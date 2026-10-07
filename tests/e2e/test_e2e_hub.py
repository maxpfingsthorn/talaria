"""Hub e2e (spec §9): both apps under one bot, Talaria self-update through the bot, and
the move of a v0.4 install's bot to a new hub. Runs after test_e2e.py and
test_e2e_clawvisor.py in the same session."""
import pytest

from tests.e2e.conftest import (HUB, WORK, AppEnv, as_user, bus_ready, root_block, seed_conf,
                                sh, wait_for)

pytestmark = pytest.mark.e2e


def unit_active(unit, user):
    return as_user("systemctl", "--user", "is-active", unit, user=user,
                   check=False).stdout.strip() == "active"


def test_01_status_lists_both_apps(env, cv_env):
    tg = env["tg"]
    tg.inject("/status")
    msg = tg.wait_sent("Clawvisor")
    assert "Hermes" in msg


def test_02_update_talaria_through_the_bot(env, cv_env):
    src, tg = env["src"], env["tg"]
    sh("git", "-C", src, "-c", "user.email=e2e@example.invalid", "-c", "user.name=e2e",
       "commit", "-q", "--allow-empty", "-m", "e2e release")
    sh("git", "-C", src, "tag", "v9.0.0")
    sh("chmod", "-R", "a+rX", src)
    tg.inject("/update v9.0.0")
    offer = tg.wait_sent("Talaria v9.0.0 is available", timeout=600)
    assert "Hermes would restart: no" in offer and "Clawvisor would restart: no" in offer
    assert tg.buttons[tg.sent.index(offer)]["inline_keyboard"][0][0]["callback_data"] == \
        "hub|up:v9.0.0"
    tg.tap("hub|up:v9.0.0")
    tg.wait_sent("Talaria v9.0.0 installed: Hermes ✓ · Clawvisor ✓", timeout=900)
    for user in (HUB, "hermes", "cvtest"):
        tag = as_user("git", "-C", f"/home/{user}/.local/share/talaria", "describe", "--tags",
                      "--exact-match", user=user).stdout.strip()
        assert tag == "v9.0.0", user
    wait_for(lambda: unit_active("talaria-telegram.service", HUB))
    assert unit_active("hermes.service", "hermes")


def test_03_moves_the_bot_of_a_v04_install_to_a_new_hub(env):
    tg, user, hub = env["tg"], "hermes3", "hub2"
    as_user("systemctl", "--user", "stop", "talaria-telegram.service", user=HUB)  # one poller
    old = WORK / "talaria-v042"
    sh("git", "clone", "-q", env["src"], old)
    sh("git", "-C", old, "checkout", "-q", "ca5e749")   # v0.4.2 code (untagged: released with v0.5.0)
    sh("chmod", "-R", "a+rX", old)
    r = sh(old / "bin/talaria", "setup", "--dev", "--user", user, check=False)
    assert r.returncode == 10, r.stdout
    sh("bash", "-c", root_block(r.stdout))
    wait_for(lambda: bus_ready(user))
    seed_conf(user)
    v04 = AppEnv(user=user, app="hermes", src=old, telegram=tg, hub=None)
    v04.conf_add("dashboard.port = 9121\n")            # Hermes (user hermes) holds 9119
    v04.setup_until_done()                             # v0.4.2: own bot, set-token, pairing
    assert unit_active("talaria-telegram.service", user)
    # v0.5 in --dev mode installs from the main checkout, not from the v0.4.2 clone
    as_user("git", "-C", f"/home/{user}/.local/share/talaria", "remote", "set-url", "origin",
            str(env["src"]), user=user)
    quadlet = f"/home/{user}/.config/containers/systemd/hermes.container"
    before = as_user("cat", quadlet, user=user).stdout
    started = as_user("systemctl", "--user", "show", "hermes.service", "-p",
                      "ActiveEnterTimestamp", user=user).stdout

    out = AppEnv(user=user, app="hermes", src=env["src"], telegram=tg,
                 hub=hub).setup_until_done()
    assert "/pair" not in out and "set-token" not in out      # same bot, same owner
    assert as_user("test", "-e", f"/home/{user}/.config/systemd/user/talaria-telegram.service",
                   user=user, check=False).returncode != 0
    assert "TALARIA_TELEGRAM" not in as_user("cat", f"/home/{user}/.config/talaria/.env",
                                             user=user).stdout
    assert as_user("grep", "-c", "^TALARIA_TELEGRAM_TOKEN=", f"/home/{hub}/.config/talaria/.env",
                   user=hub).stdout.strip() == "1"
    assert as_user("cat", quadlet, user=user).stdout == before          # Quadlet untouched
    assert as_user("systemctl", "--user", "show", "hermes.service", "-p",
                   "ActiveEnterTimestamp", user=user).stdout == started  # not restarted
    wait_for(lambda: unit_active("talaria-telegram.service", hub))
    tg.inject("/status")
    tg.wait_sent("Hermes")
