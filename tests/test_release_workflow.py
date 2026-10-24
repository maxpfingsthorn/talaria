"""Execute the publication shell with a fake GitHub CLI; never contact GitHub."""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


@pytest.mark.parametrize("scenario,success", [
    ("new", True), ("tag_exists", True), ("release_exists", True),
    ("wrong_commit", False), ("annotated_tag", False), ("api_failure", False),
    ("draft_release", False),
])
def test_publication_retries_preserve_validated_commit(tmp_path, scenario, success):
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    script = next(step["run"] for step in workflow["jobs"]["publish"]["steps"]
                  if step.get("name") == "create validated tag and publish release")
    gh = tmp_path / "gh"
    gh.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
case = os.environ['SCENARIO']
if args[:3] == ['api', '--method', 'POST']:
    sys.exit(0 if case == 'new' else 1)
if args[0] == 'api' and '/git/ref/tags/' in args[1]:
    if case == 'api_failure':
        sys.exit(1)
    print('' if case == 'annotated_tag' else 'b' * 40 if case == 'wrong_commit' else 'a' * 40)
    sys.exit(0)
if args[:2] == ['release', 'create']:
    sys.exit(1 if case in ('release_exists', 'draft_release') else 0)
if args[0] == 'api' and '/releases/tags/' in args[1]:
    print('false' if case == 'draft_release' else 'true')
    sys.exit(0)
sys.exit(2)
""")
    gh.chmod(0o755)
    calls = tmp_path / "calls"
    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], capture_output=True,
                            text=True, env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
                                            "SCENARIO": scenario, "CALLS": str(calls),
                                            "RELEASE_TAG": "v1.2.3", "GITHUB_SHA": SHA,
                                            "GITHUB_REPOSITORY": "example/talaria"})
    assert (result.returncode == 0) is success, result.stderr
    requests = [json.loads(line) for line in calls.read_text().splitlines()]
    if scenario in ("wrong_commit", "annotated_tag", "api_failure"):
        assert not any(args[:2] == ["release", "create"] for args in requests)
    else:
        assert any(args[:3] == ["release", "create", "v1.2.3"] for args in requests)
