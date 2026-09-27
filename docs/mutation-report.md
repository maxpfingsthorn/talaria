# Mutation testing report

Date: 2026-09-27 · Commit: 754f0c0 · Tool: mutmut 3.8.0 (Python 3.12)

**Score: 95.4 %** (threshold 85 %). 5,361 mutants: 5,108 killed, 6 timeouts (counted as
killed), 247 survived, 0 without tests, 0 suspicious. Scoring is honest:
`tests/mutation_score.py` counts no-tests and suspicious mutants as survivors.

| Module | Mutants | Killed | Timeout | Survived |
|---|---|---|---|---|
| `talaria.setup` | 820 | 774 | 0 | 46 |
| `talaria.adopt` | 492 | 484 | 0 | 8 |
| `talaria.telegram` | 394 | 388 | 1 | 5 |
| `talaria.rehearse` | 388 | 373 | 0 | 15 |
| `talaria.rollback` | 370 | 361 | 0 | 9 |
| `talaria.cli` | 338 | 317 | 0 | 21 |
| `talaria.deploy` | 292 | 290 | 0 | 2 |
| `talaria.images` | 236 | 233 | 0 | 3 |
| `talaria.backup` | 196 | 171 | 0 | 25 |
| `talaria.notify` | 178 | 165 | 0 | 13 |
| `talaria.check` | 178 | 175 | 0 | 3 |
| `talaria.conf` | 140 | 116 | 0 | 24 |
| `talaria.hermes` | 134 | 131 | 2 | 1 |
| `talaria.status` | 124 | 117 | 0 | 7 |
| `talaria.history` | 123 | 123 | 0 | 0 |
| `helpers.confdiff` | 120 | 117 | 0 | 3 |
| `talaria.containers` | 107 | 105 | 0 | 2 |
| `talaria.restore` | 101 | 92 | 0 | 9 |
| `helpers.dbopen` | 76 | 72 | 0 | 4 |
| `talaria.upstream` | 74 | 60 | 0 | 14 |
| `talaria.units` | 71 | 65 | 0 | 6 |
| `talaria.selfupdate` | 68 | 68 | 0 | 0 |
| `talaria.state` | 64 | 62 | 0 | 2 |
| `helpers.migrate` | 63 | 62 | 0 | 1 |
| `talaria.tags` | 53 | 48 | 0 | 5 |
| `talaria.retention` | 48 | 48 | 0 | 0 |
| `talaria.shell` | 44 | 39 | 0 | 5 |
| `talaria.disk` | 32 | 18 | 3 | 11 |
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

Ten lines carry the pragma, each with its reason next to the code:
- `helpers/dbopen.py`, `talaria/rehearse.py`: `uri=True` (Python's sqlite opens
  `file:` URIs anyway); SQL/PRAGMA keyword case.
- `helpers/dbopen.py`: two guards whose mutated branch yields the same `None`.
- `talaria/backup.py`: chunk size of the checksum loop (`read(None)` gives the same hash).
- `talaria/history.py`: `follow_symlinks=None` (also false); `parents=` for a flat directory.
- `talaria/hermes.py`: the initial value of `reason`, always overwritten before use.

## Remaining survivors

The 247 survivors (194 logic, 53 string-only) are spread thinly over many modules.
They were **not** individually reviewed. The largest groups are:
- arguments to stubbed collaborators in setup's fresh-install path;
- directory modes of directories the fixtures pre-create;
- config parsing of rarely used value forms;
- CLI parser details.

The deploy, rollback and restore modules have 20 survivors between them, and the
marker module has none. List them with `uv run mutmut results` after a run.
