"""The only module that imports Hermes code. Runs inside a Hermes image."""
import importlib.util
from pathlib import Path

HERMES_ROOT = Path("/opt/hermes")


def config_versions():
    from hermes_cli.config import check_config_version
    return check_config_version()


def run_config_migration() -> int:
    path = HERMES_ROOT / "scripts" / "docker_config_migrate.py"
    spec = importlib.util.spec_from_file_location("docker_config_migrate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main()


def open_state_db(path) -> None:
    from hermes_state import SessionDB
    db = SessionDB(db_path=Path(path))
    db.close()


def schema_version() -> int:
    from hermes_state import SCHEMA_VERSION
    return int(SCHEMA_VERSION)
