import os
import re

from dummy_build import LATEST, MODE


def _path():
    return os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "config.yaml")


def check_config_version():
    try:
        m = re.search(r"^_config_version:\s*(\d+)", open(_path()).read(), re.M)
    except FileNotFoundError:
        m = None
    return (int(m[1]) if m else 0), LATEST


def migrate_config(interactive=False, quiet=False):
    if MODE == "failmigrate":
        raise RuntimeError("dummy migration failure")
    text = open(_path()).read() if os.path.exists(_path()) else ""
    text = re.sub(r"^_config_version:.*\n?", "", text, flags=re.M)
    with open(_path(), "w") as f:
        f.write(f"_config_version: {LATEST}\n{text}")
    print(f"✓ dummy migration to {LATEST}")
