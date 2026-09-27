"""Fail if the mutmut score is below the threshold (spec §13.4). Honest scoring:
no-tests and suspicious mutants count as survivors; timeouts count as killed."""
import json
import sys
from pathlib import Path

THRESHOLD = 0.85


def main() -> int:
    s = json.loads(Path("mutants/mutmut-cicd-stats.json").read_text())
    killed = s.get("killed", 0) + s.get("timeout", 0)
    total = s.get("total", 0) - s.get("skipped", 0)
    score = killed / total if total else 0.0
    print(f"mutants: {total}, killed: {s.get('killed', 0)}, timeout: {s.get('timeout', 0)}, "
          f"survived: {s.get('survived', 0)}, no_tests: {s.get('no_tests', 0)}, "
          f"suspicious: {s.get('suspicious', 0)}, score: {score:.1%}")
    return 0 if score >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
