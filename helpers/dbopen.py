"""Open state.db the way Hermes does (which migrates it) and report versions."""
import json
import os
import sqlite3
import sys
from pathlib import Path


def read_version(path):
    try:
        # equivalent mutants: Python's sqlite accepts file: URIs even without uri=True
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)  # pragma: no mutate
        try:
            # equivalent mutants: SQL keywords and identifiers are case-insensitive
            row = c.execute("SELECT version FROM schema_version LIMIT 1").fetchone()  # pragma: no mutate
        finally:
            c.close()
        # equivalent mutant: int(None[0]) raises TypeError, which returns None below
        return int(row[0]) if row else None  # pragma: no mutate
    except (sqlite3.Error, ValueError, TypeError):
        return None


def dbopen(up, db_path: Path, create: bool = False) -> dict:
    res = {"ok": False, "before": None, "after": None, "schema_version": None, "error": None}
    try:
        res["schema_version"] = up.schema_version()
        if not Path(db_path).exists() and not create:
            res["ok"] = True
            return res
        # equivalent mutant: read_version of a missing file also returns None
        res["before"] = read_version(db_path) if Path(db_path).exists() else None  # pragma: no mutate
        up.open_state_db(db_path)
        res["after"] = read_version(db_path)
        res["ok"] = True
    except (Exception, SystemExit) as e:
        res["error"] = f"{type(e).__name__}: {e}"
    return res


def main(argv=None) -> None:
    import _upstream
    create = "--create" in (argv if argv is not None else sys.argv[1:])
    res = dbopen(_upstream, Path(os.environ.get("HERMES_HOME", "/opt/data")) / "state.db",
                 create=create)
    with open(os.environ["TALARIA_RESULT"], "w") as f:
        json.dump(res, f)


if __name__ == "__main__":
    main()
    sys.exit(0)
