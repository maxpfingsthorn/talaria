"""Run Hermes's config migration and report the result as JSON."""
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout


def migrate(up) -> dict:
    res = {"ok": False, "before": None, "after": None, "latest": None,
           "messages": [], "error": None}
    buf = io.StringIO()
    try:
        before, latest = up.config_versions()
        res["before"], res["latest"] = before, latest
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = up.run_config_migration()
        after, _ = up.config_versions()
        res["after"] = after
        if rc != 0:
            res["error"] = f"migration exited {rc}"
        elif after < latest:
            res["error"] = f"config version is {after} after migration, expected {latest}"
        else:
            res["ok"] = True
    except (Exception, SystemExit) as e:
        res["error"] = f"{type(e).__name__}: {e}"
    res["messages"] = [l for l in buf.getvalue().splitlines() if l.strip()][:200]
    return res


def write_result(res: dict) -> None:
    with open(os.environ["TALARIA_RESULT"], "w") as f:
        json.dump(res, f)


def main() -> None:
    import _upstream
    write_result(migrate(_upstream))


if __name__ == "__main__":
    main()
    sys.exit(0)
