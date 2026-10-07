import re

import pytest

from tests.e2e.conftest import (HUB, USER, AppEnv, as_user, bus_ready, root_block, seed_conf,
                                seed_hub_conf, sh, talaria, wait_for)

pytestmark = pytest.mark.e2e


def hermes_active(user=USER):
    return as_user("systemctl", "--user", "is-active", "hermes.service", user=user,
                   check=False).stdout.strip() == "active"


def bot_active(hub=HUB):
    return as_user("systemctl", "--user", "is-active", "talaria-telegram.service", user=hub,
                   check=False).stdout.strip() == "active"


def cfg_version(user=USER):
    data = f"/home/{user}/hermes-data/config.yaml"
    out = as_user("cat", data, user=user, check=False).stdout
    m = re.search(r"_config_version: (\d+)", out)
    return int(m[1]) if m else None


def test_01_fresh_setup_creates_the_hub(env):
    r = sh(env["src"] / "bin/talaria", "setup", "--dev", check=False)
    assert r.returncode == 10 and "then run setup again with --user hermes" in r.stdout, r.stdout
    assert "talaria ALL=(hermes) NOPASSWD: $home/.local/bin/talaria op *" in r.stdout
    sh("bash", "-c", root_block(r.stdout))
    wait_for(lambda: bus_ready())
    wait_for(lambda: bus_ready(HUB))
    seed_conf()
    seed_hub_conf()
    out = AppEnv(user=USER, app="hermes", src=env["src"], telegram=env["tg"]).setup_until_done()
    assert "set-token" in out and "/pair" in out
    assert hermes_active() and cfg_version() == 1 and bot_active()
    assert as_user("test", "-e", f"/home/{USER}/.config/systemd/user/talaria-telegram.service",
                   check=False).returncode != 0          # the app has no bot of its own


def test_02_update_via_bot_survives_bot_restart(env):
    """Both steps run `talaria op` from the hub's transient unit (not the app's cgroup),
    only XDG_RUNTIME_DIR set: /check rehearses the release with `podman run` (migration
    included) and /approve deploys through the relay, so rootless podman is exercised
    from that cgroup, not only quick ops."""
    tg = env["tg"]
    env["up"].publish(2)
    talaria("backup")                                   # a manual backup for test_04
    tg.inject("/check hermes")
    tg.wait_sent("Hermes v2026.1.2 is ready to deploy")     # only sent after a good rehearsal
    tg.inject("/approve hermes v2026.1.2")
    tg.wait_sent("Deploying Hermes v2026.1.2")
    as_user("systemctl", "--user", "restart", "talaria-telegram.service", user=HUB)
    tg.wait_sent("Deployed Hermes v2026.1.2")
    assert hermes_active() and cfg_version() == 2


def test_03_rollback(env):
    tg = env["tg"]
    tg.inject("/rollback hermes")
    tg.wait_sent("Send /rollback CONFIRM")
    tg.inject("/rollback hermes CONFIRM")
    tg.wait_sent("Rolled back to Hermes v2026.1.1")
    assert hermes_active() and cfg_version() == 1


def test_04_restore(env):
    bid = re.search(r"^(\S+-manual)\s", talaria("backups").stdout, re.M)[1]
    env["tg"].inject(f"/restore hermes {bid} CONFIRM")
    env["tg"].wait_sent(f"Restored backup {bid}")
    assert hermes_active()


def test_05_redeploy_by_tapping_the_button(env):
    tg = env["tg"]
    tg.inject("/check hermes")
    msg = tg.wait_sent("Hermes v2026.1.2 is ready to deploy")
    markup = tg.buttons[tg.sent.index(msg)]
    assert markup["inline_keyboard"][0][0]["callback_data"] == "hermes|ap:v2026.1.2"
    tg.tap("hermes|ap:v2026.1.2")
    tg.wait_sent("Deployed Hermes v2026.1.2")


def test_06_crashing_release_rolls_back_automatically(env):
    tg = env["tg"]
    env["up"].publish(3, "crash")
    tg.inject("/check hermes")
    tg.wait_sent("Hermes v2026.1.3 is ready to deploy")
    tg.inject("/approve hermes v2026.1.3")
    msg = tg.wait_sent("v2026.1.3 failed during deploy", timeout=400)
    assert "Rolled back to Hermes v2026.1.2" in msg
    assert hermes_active() and cfg_version() == 2


def test_07_failing_migration_stops_at_rehearsal(env):
    env["up"].publish(4, "failmigrate")
    env["tg"].inject("/check hermes")
    env["tg"].wait_sent("v2026.1.4 failed the rehearsal")
    assert hermes_active() and cfg_version() == 2


def test_08_crash_mid_deploy_blocks_start_until_rollback(env):
    tg = env["tg"]
    env["up"].publish(5)
    tg.inject("/check hermes")
    tg.wait_sent("Hermes v2026.1.5 is ready to deploy")
    r = talaria("deploy", "v2026.1.5", env=["TALARIA_TEST_CRASH_AT=after_marker"], check=False)
    assert r.returncode != 0
    assert as_user("test", "-e", f"/home/{USER}/.local/state/talaria/changing",
                   check=False).returncode == 0
    # Spike: restarting the user manager times out on GitHub runners, so simulate the
    # boot: systemd tries to start Hermes (the marker must block it) and the bot restarts.
    as_user("systemctl", "--user", "start", "hermes.service", check=False)
    assert not hermes_active()
    as_user("systemctl", "--user", "restart", "talaria-telegram.service", user=HUB)
    tg.wait_sent("Interrupted deploy")
    tg.inject("/rollback hermes CONFIRM")
    tg.wait_sent("Rolled back to Hermes v2026.1.2")
    assert hermes_active() and cfg_version() == 2


def hermes_active_unit(user, unit):
    return as_user("systemctl", "--user", "is-active", unit, user=user,
                   check=False).stdout.strip() == "active"


def test_09_adopt_existing_install_under_its_own_hub(env):
    tg, user, hub = env["tg"], "hermes2", "hubadopt"
    as_user("systemctl", "--user", "stop", "hermes.service", check=False)        # port 9119
    as_user("systemctl", "--user", "stop", "talaria-telegram.service", user=HUB)  # one poller
    sh("sudo", "useradd", "--create-home", "--shell", "/bin/bash", user)
    sh("sudo", "loginctl", "enable-linger", user)
    wait_for(lambda: bus_ready(user))
    as_user("mkdir", "-p", f"/home/{user}/data", f"/home/{user}/.config/containers/systemd",
            user=user)
    as_user("podman", "pull", "-q", "--tls-verify=false",
            "localhost:5000/hermes-agent:v2026.1.1", user=user)
    quadlet = (
        "[Container]\nContainerName=old-hermes\n"
        "Image=localhost:5000/hermes-agent:v2026.1.1\n"
        f"Volume=/home/{user}/data:/opt/data\nUserNS=keep-id:uid=10000,gid=10000\n"
        "Environment=HERMES_DASHBOARD_INSECURE=true TZ=UTC\n"
        "PublishPort=127.0.0.1:9119:9119\nExec=gateway run\n[Install]\nWantedBy=default.target\n")
    as_user("sh", "-c", f"cat > /home/{user}/.config/containers/systemd/old-hermes.container",
            user=user, input=quadlet)
    as_user("systemctl", "--user", "daemon-reload", user=user)
    as_user("systemctl", "--user", "start", "old-hermes.service", user=user)
    wait_for(lambda: hermes_active_unit(user, "old-hermes.service"))
    seed_conf(user)
    setup = [env["src"] / "bin/talaria", "setup", "--dev", "--user", user, "--hub", hub]
    r = sh(*setup, check=False)                 # creates the hub account (root paste)
    assert r.returncode == 10 and "useradd --create-home --shell /bin/bash hubadopt" in r.stdout
    sh("bash", "-c", root_block(r.stdout))
    wait_for(lambda: bus_ready(hub))
    seed_hub_conf(hub)
    r = sh(*setup, check=False)
    assert r.returncode == 10 and "--adopt old-hermes.service" in r.stdout, r.stdout
    assert "HERMES_DASHBOARD_INSECURE" in r.stdout            # listed as dropped
    out = AppEnv(user=user, app="hermes", src=env["src"], telegram=tg,
                 hub=hub).setup_until_done("--adopt", "old-hermes.service")
    assert "adopted old-hermes.service" in out
    assert hermes_active(user)
    assert as_user("test", "-e", f"/home/{user}/.config/containers/systemd/"
                   "old-hermes.container.talaria-orig", user=user, check=False).returncode == 0
    # hand the host back to the main hub and Hermes for the tests that follow
    as_user("systemctl", "--user", "disable", "--now", "talaria-telegram.service",
            "talaria-check.timer", user=hub)
    as_user("systemctl", "--user", "stop", "hermes.service", user=user)
    as_user("systemctl", "--user", "start", "hermes.service")
    as_user("systemctl", "--user", "start", "talaria-telegram.service", user=HUB)
    wait_for(hermes_active)
    wait_for(bot_active)
