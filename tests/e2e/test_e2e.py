import re
import subprocess

import pytest

from tests.e2e.conftest import (USER, as_user, bus_ready, root_block, seed_conf, sh, talaria,
                                uid, wait_for)

pytestmark = pytest.mark.e2e


def hermes_active(user=USER):
    return as_user("systemctl", "--user", "is-active", "hermes.service", user=user,
                   check=False).stdout.strip() == "active"


def cfg_version(user=USER):
    data = f"/home/{user}/hermes-data/config.yaml"
    out = as_user("cat", data, user=user, check=False).stdout
    m = re.search(r"_config_version: (\d+)", out)
    return int(m[1]) if m else None


def setup_cmd(env, *extra):
    return sh(env["src"] / "bin/talaria", "setup", "--dev", *extra, check=False)


def pair(env, *extra, user=USER):
    p = subprocess.Popen([str(env["src"] / "bin/talaria"), "setup", "--dev", *extra],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = ""
    for line in p.stdout:
        out += line
        m = re.search(r"/pair ([A-Z0-9]{8})", line)
        if m:
            env["tg"].inject(f"/pair {m[1]}")
    assert p.wait(timeout=900) == 0, out
    return out


def test_01_fresh_setup(env):
    r = setup_cmd(env)
    assert r.returncode == 10, r.stdout
    sh("sudo", "bash", "-euc", root_block(r.stdout))
    wait_for(lambda: bus_ready())
    seed_conf()
    r = setup_cmd(env, "--user", USER)
    assert r.returncode == 10 and "set-token" in r.stdout, r.stdout
    t = talaria("set-token", input="123456:" + "a" * 35 + "\n")
    assert "fakebot" in t.stdout
    out = pair(env, "--user", USER)
    assert out.rstrip().endswith("DONE") and hermes_active() and cfg_version() == 1


def test_02_update_via_bot_survives_bot_restart(env):
    env["up"].publish(2)
    talaria("backup")                                   # a manual backup for test_04
    talaria("check")
    env["tg"].wait_sent("/approve v2026.1.2")
    env["tg"].inject("/approve v2026.1.2")
    env["tg"].wait_sent("Deploying v2026.1.2")
    as_user("systemctl", "--user", "restart", "talaria-telegram.service")
    env["tg"].wait_sent("Deployed Hermes v2026.1.2")
    assert hermes_active() and cfg_version() == 2


def test_03_rollback(env):
    env["tg"].inject("/rollback")
    env["tg"].wait_sent("Send /rollback CONFIRM")
    env["tg"].inject("/rollback CONFIRM")
    env["tg"].wait_sent("Rolled back to Hermes v2026.1.1")
    assert hermes_active() and cfg_version() == 1


def test_04_restore(env):
    bid = re.search(r"^(\S+-manual)\s", talaria("backups").stdout, re.M)[1]
    env["tg"].inject(f"/restore {bid} CONFIRM")
    env["tg"].wait_sent(f"Restored backup {bid}")
    assert hermes_active()


def test_05_redeploy_by_tapping_the_button(env):
    talaria("check")
    tg = env["tg"]
    msg = tg.wait_sent("/approve v2026.1.2")
    markup = tg.buttons[tg.sent.index(msg)]
    assert markup["inline_keyboard"][0][0]["callback_data"] == "ap:v2026.1.2"
    tg.tap("ap:v2026.1.2")
    tg.wait_sent("Deployed Hermes v2026.1.2")


def test_06_crashing_release_rolls_back_automatically(env):
    env["up"].publish(3, "crash")
    talaria("check")
    env["tg"].inject("/approve v2026.1.3")
    msg = env["tg"].wait_sent("v2026.1.3 failed during deploy", timeout=400)
    assert "Rolled back to Hermes v2026.1.2" in msg
    assert hermes_active() and cfg_version() == 2


def test_07_failing_migration_stops_at_rehearsal(env):
    env["up"].publish(4, "failmigrate")
    talaria("check")
    env["tg"].wait_sent("v2026.1.4 failed the rehearsal")
    assert hermes_active() and cfg_version() == 2


def test_08_crash_mid_deploy_blocks_start_until_rollback(env):
    env["up"].publish(5)
    talaria("check")
    env["tg"].wait_sent("/approve v2026.1.5")
    r = talaria("deploy", "v2026.1.5", env=["TALARIA_TEST_CRASH_AT=after_marker"], check=False)
    assert r.returncode != 0
    assert as_user("test", "-e", f"/home/{USER}/.local/state/talaria/changing",
                   check=False).returncode == 0
    # Spike: restarting the user manager times out on GitHub runners, so simulate the
    # boot: systemd tries to start Hermes (the marker must block it) and the bot restarts.
    as_user("systemctl", "--user", "start", "hermes.service", check=False)
    assert not hermes_active()
    as_user("systemctl", "--user", "restart", "talaria-telegram.service")
    env["tg"].wait_sent("Interrupted deploy")
    env["tg"].inject("/rollback CONFIRM")
    env["tg"].wait_sent("Rolled back to Hermes v2026.1.2")
    assert hermes_active() and cfg_version() == 2


def test_09_adopt_existing_install(env):
    for unit in ("talaria-telegram.service", "hermes.service", "talaria-check.timer"):
        as_user("systemctl", "--user", "stop", unit, check=False)
    user = "hermes2"
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
    # the runner's account already has sudo to every user, so no root block is needed here
    seed_conf(user)
    r = setup_cmd(env, "--user", user)
    assert r.returncode == 10 and "--adopt old-hermes.service" in r.stdout, r.stdout
    assert "HERMES_DASHBOARD_INSECURE" in r.stdout            # listed as dropped
    talaria("set-token", user=user, input="123456:" + "b" * 35 + "\n")
    out = pair(env, "--user", user, "--adopt", "old-hermes.service", user=user)
    assert "adopted old-hermes.service" in out and out.rstrip().endswith("DONE")
    assert hermes_active(user)
    assert as_user("test", "-e", f"/home/{user}/.config/containers/systemd/"
                   "old-hermes.container.talaria-orig", user=user, check=False).returncode == 0


def hermes_active_unit(user, unit):
    return as_user("systemctl", "--user", "is-active", unit, user=user,
                   check=False).stdout.strip() == "active"
