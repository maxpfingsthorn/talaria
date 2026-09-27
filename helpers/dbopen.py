"""Open state.db the way Hermes does (which migrates it) and report versions."""
import json
import os
import sqlite3
import sys
from pathlib import Path


def read_version(path):
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = c.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        finally:
            c.close()
        return int(row[0]) if row else None
    except (sqlite3.Error, ValueError, TypeError):
        return None


def dbopen(up, db_path: Path) -> dict:
    res = {"ok": False, "before": None, "after": None, "schema_version": None, "error": None}
    try:
        res["schema_version"] = up.schema_version()
        if not Path(db_path).exists():
            res["ok"] = True
            return res
        res["before"] = read_version(db_path)
        up.open_state_db(db_path)
        res["after"] = read_version(db_path)
        res["ok"] = True
    except (Exception, SystemExit) as e:
        res["error"] = f"{type(e).__name__}: {e}"
    return res


def main() -> None:
    import _upstream
    res = dbopen(_upstream, Path(os.environ.get("HERMES_HOME", "/opt/data")) / "state.db")
    with open(os.environ["TALARIA_RESULT"], "w") as f:
        json.dump(res, f)


if __name__ == "__main__":
    main()
    sys.exit(0)
