"""e2e tests against the real Clawvisor releases, registered with the same hub as Hermes
(GitHub runners only, TALARIA_E2E=1).
Uses `cv_env` (tests/e2e/conftest.py): a second service user, cvtest, set up fresh
against the actual upstream repo, pinned to specific tags with `release_allow` so the
test does not break when a new Clawvisor release ships."""
import json

import pytest

from tests.e2e.conftest import as_user

pytestmark = pytest.mark.e2e
USER = "cvtest"

CONF = """app = clawvisor
dashboard.bind = loopback
talaria_repo = /tmp/talaria-e2e/talaria-src
settle_seconds = 10
telegram_api = http://127.0.0.1:8081
disk.floor_gb = 0.2
release_allow = v0.9.9
"""


def test_01_fresh_setup_at_v099(cv_env):
    cv_env.conf(CONF)
    out = cv_env.setup_until_done()
    assert "Clawvisor is running" in out
    st = json.loads(as_user("cat", ".local/state/talaria/state.json", user=USER,
                            cwd=f"/home/{USER}").stdout)
    assert st["current"]["tag"] == "v0.9.9"


def test_02_update_offer_lists_migrations_and_deploys(cv_env):
    cv_env.conf_add("release_allow = v0.9.9 v0.9.10\n")
    cv_env.telegram.inject("/check clawvisor")
    offer = cv_env.telegram.wait_sent("Clawvisor v0.9.10 is ready to deploy")
    assert "055_task_pending_expansion_envelope.sql" in offer
    cv_env.telegram.tap("clawvisor|ap:v0.9.10")
    cv_env.telegram.wait_sent("Deployed Clawvisor v0.9.10.")


def test_03_rollback_restores_v099_and_data(cv_env):
    cv_env.telegram.inject("/rollback clawvisor CONFIRM")
    cv_env.telegram.wait_sent("Rolled back to Clawvisor v0.9.9.")
    newest = as_user("python3", "-c",
                     "import sqlite3; c = sqlite3.connect('file:clawvisor-data/clawvisor.db"
                     "?mode=ro', uri=True); print(c.execute('select max(name) from "
                     "schema_migrations').fetchone()[0])", user=USER, cwd=f"/home/{USER}")
    assert newest.stdout.strip() == "054_install_context.sql"
