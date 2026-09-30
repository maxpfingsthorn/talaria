# Mutation testing report

Date: 2026-09-30 · Commit: v0.2.4 (public-readiness fixes) · Tool: mutmut 3.8.0 (Python 3.12)

**Score: 95.3 %** (threshold 85 %). 6,282 mutants: 5,980 killed, 6 timeouts (counted as
killed), 294 survived, 0 without tests, 0 suspicious. Scoring is honest:
`tests/mutation_score.py` counts no-tests and suspicious mutants as survivors.

| Module | Mutants | Killed | Timeout | Survived |
|---|---|---|---|---|
| `talaria.setup` | 910 | 853 | 0 | 57 |
| `talaria.telegram` | 677 | 643 | 1 | 33 |
| `talaria.rollback` | 576 | 576 | 0 | 0 |
| `talaria.adopt` | 556 | 539 | 0 | 17 |
| `talaria.rehearse` | 461 | 432 | 0 | 29 |
| `talaria.cli` | 344 | 323 | 0 | 21 |
| `talaria.deploy` | 313 | 311 | 0 | 2 |
| `talaria.images` | 236 | 233 | 0 | 3 |
| `talaria.notify` | 234 | 219 | 0 | 15 |
| `talaria.backup` | 206 | 181 | 0 | 25 |
| `talaria.check` | 178 | 175 | 0 | 3 |
| `talaria.history` | 174 | 170 | 0 | 4 |
| `helpers.confdiff` | 160 | 158 | 0 | 2 |
| `talaria.conf` | 140 | 116 | 0 | 24 |
| `talaria.hermes` | 135 | 132 | 2 | 1 |
| `talaria.status` | 132 | 125 | 0 | 7 |
| `talaria.containers` | 107 | 105 | 0 | 2 |
| `talaria.restore` | 102 | 102 | 0 | 0 |
| `helpers.dbopen` | 76 | 72 | 0 | 4 |
| `talaria.upstream` | 74 | 60 | 0 | 14 |
| `talaria.units` | 73 | 67 | 0 | 6 |
| `talaria.selfupdate` | 68 | 68 | 0 | 0 |
| `talaria.state` | 64 | 62 | 0 | 2 |
| `helpers.migrate` | 63 | 62 | 0 | 1 |
| `talaria.tags` | 53 | 48 | 0 | 5 |
| `talaria.retention` | 48 | 48 | 0 | 0 |
| `talaria.shell` | 44 | 39 | 0 | 5 |
| `talaria.disk` | 41 | 27 | 3 | 11 |
| `talaria.ctx` | 22 | 19 | 0 | 3 |
| `talaria.marker` | 15 | 15 | 0 | 0 |

## What the tests pin

The first run scored 61.9 %. Most survivors were exact command lines (podman and
systemctl flags, timeouts) and user-facing texts that no test asserted. The suite now
pins, per branch:
- the exact commands each operation runs, with their timeouts;
- the exact messages and setup output;
- the exact files written (state, markers, sidecars, env files).

Mutation work also found and fixed one real bug: the manual recovery steps for a
non-Quadlet adoption printed a broken `mv` line.

## Equivalent mutants (excluded with `# pragma: no mutate`)

Thirteen lines carry the pragma, each with its reason next to the code:
- `helpers/dbopen.py`, `talaria/rehearse.py`: `uri=True` (Python's sqlite opens
  `file:` URIs anyway); SQL/PRAGMA keyword case.
- `helpers/dbopen.py`: two guards whose mutated branch yields the same `None`.
- `talaria/backup.py`: chunk size of the checksum loop (`read(None)` gives the same hash).
- `talaria/history.py`: `parents=` for a flat directory.
- `talaria/hermes.py`: the initial value of `reason`, always overwritten before use.
- `talaria/restore.py`: the extraction dir's mode (tar applies the archived mode of `.`), two
  steps that ignore `ctx`, and the done-marker `unlink` whose file always exists there.

## Remaining survivors

The 294 survivors are spread thinly over many modules.
They were **not** individually reviewed. The largest groups are:
- arguments to stubbed collaborators in setup's fresh-install path;
- directory modes of directories the fixtures pre-create;
- config parsing of rarely used value forms;
- CLI parser details.

The deploy, rollback and restore modules have none left: their 36 survivors were each
killed by a test or marked equivalent, and the marker module had none. v0.2.4 kept it that
way for the new pre-rollback backup and button expiry. The two deploy mutants listed as
"segfault" (in the table under Survived) trigger the e2e crash hook, which kills the test process:
they are detected, just not labelled killed. List them with `uv run mutmut results` after a run.
