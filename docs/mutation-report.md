# Mutation testing report

Date: 2026-10-02 · Commit: v0.3.0 (core and app adapters) · Tool: mutmut 3.8.0 (Python 3.12)

**Score: 95.5 %** (threshold 85 %). 6,428 mutants: 6,131 killed (including 2 segfault
mutants in the deploy module's e2e crash hook, which kill the test process instead of
raising normally -- see below), 8 timeouts (counted as killed), 289 survived, 0 without
tests, 0 suspicious. Scoring is honest: `tests/mutation_score.py` counts no-tests and
suspicious mutants as survivors.

| Module | Mutants | Killed | Timeout | Survived |
|---|---|---|---|---|
| `talaria.setup` | 894 | 839 | 0 | 55 |
| `talaria.telegram` | 679 | 643 | 3 | 33 |
| `talaria.rollback` | 578 | 578 | 0 | 0 |
| `talaria.adopt` | 567 | 550 | 0 | 17 |
| `talaria.apps.hermes` | 393 | 393 | 0 | 0 |
| `talaria.cli` | 359 | 338 | 0 | 21 |
| `talaria.deploy` | 271 | 271 | 0 | 0 |
| `talaria.notify` | 234 | 219 | 0 | 15 |
| `talaria.images` | 219 | 216 | 0 | 3 |
| `talaria.rehearse` | 209 | 195 | 0 | 14 |
| `talaria.backup` | 206 | 181 | 0 | 25 |
| `helpers.confdiff` | 206 | 202 | 0 | 4 |
| `talaria.check` | 184 | 181 | 0 | 3 |
| `talaria.history` | 174 | 170 | 0 | 4 |
| `talaria.conf` | 144 | 120 | 0 | 24 |
| `talaria.status` | 136 | 128 | 0 | 8 |
| `talaria.containers` | 107 | 105 | 0 | 2 |
| `talaria.restore` | 102 | 102 | 0 | 0 |
| `talaria.service` | 98 | 96 | 2 | 0 |
| `talaria.units` | 86 | 74 | 0 | 12 |
| `helpers.dbopen` | 76 | 72 | 0 | 4 |
| `talaria.upstream` | 74 | 60 | 0 | 14 |
| `talaria.selfupdate` | 68 | 68 | 0 | 0 |
| `talaria.state` | 64 | 62 | 0 | 2 |
| `helpers.migrate` | 63 | 62 | 0 | 1 |
| `talaria.tags` | 56 | 51 | 0 | 5 |
| `talaria.retention` | 48 | 48 | 0 | 0 |
| `talaria.shell` | 44 | 39 | 0 | 5 |
| `talaria.disk` | 41 | 27 | 3 | 11 |
| `talaria.ctx` | 29 | 22 | 0 | 7 |
| `talaria.marker` | 15 | 15 | 0 | 0 |
| `talaria.apps` | 4 | 4 | 0 | 0 |

The `deploy` row's Killed count includes its 2 crash-hook mutants (see below); the
module has no true survivors. `service`'s 2 timeouts are pre-existing (the settle-loop
increment, already timeouts in v0.2.5) and count as killed.

## What changed since v0.2.5

Tasks 1–9 moved Hermes-specific code behind `talaria/apps/` (an `App` adapter,
`talaria/apps/hermes.py`) and a new app-neutral `talaria/service.py`. The previous
report's `talaria.hermes` row is gone; its mutants now live in `talaria.apps.hermes`
(393 mutants, 0 survivors) and `talaria.service` (98 mutants, 0 survivors beyond the
two pre-existing timeouts). Every new survivor the full run turned up in those two
modules was killed with a focused test (exact command lines and args passed to
collaborators, exact token lengths, exact boundary and label text) or marked
equivalent; none were left unexamined, unlike the rest of the codebase's remaining
296 → 289 survivors, which continue to not be reviewed individually (see below).

Two deploy-module survivors needed a new test here too: `ctx.app.after_start(ctx, p)`
had never been pinned to receive the real `ctx` and the real pending record (Hermes's
own `after_start` ignores both args, so nothing failed before); `tests/test_deploy.py`
now asserts the exact objects passed.

## What the tests pin

The first run scored 61.9 %. Most survivors were exact command lines (podman and
systemctl flags, timeouts) and user-facing texts that no test asserted. The suite now
pins, per branch:
- the exact commands each operation runs, with their timeouts;
- the exact messages and setup output;
- the exact files written (state, markers, sidecars, env files);
- the exact arguments the app adapter's hooks are called with (`ctx`, pending records,
  the rehearsal's `copy`/`stage` paths).

Mutation work also found and fixed one real bug: the manual recovery steps for a
non-Quadlet adoption printed a broken `mv` line.

## Equivalent mutants (excluded with `# pragma: no mutate`)

Fourteen lines carry the pragma, each with its reason next to the code:
- `helpers/dbopen.py`, `talaria/rehearse.py`: `uri=True` (Python's sqlite opens
  `file:` URIs anyway); SQL/PRAGMA keyword case.
- `helpers/dbopen.py`: two guards whose mutated branch yields the same `None`.
- `talaria/backup.py`: chunk size of the checksum loop (`read(None)` gives the same hash).
- `talaria/history.py`: `parents=` for a flat directory.
- `talaria/service.py`: the initial value of `reason`, always overwritten before use.
- `talaria/restore.py`: the extraction dir's mode (tar applies the archived mode of `.`), two
  steps that ignore `ctx`, and the done-marker `unlink` whose file always exists there.
- `talaria/apps/hermes.py`: `follow_symlinks=False` on the config-copy in `rehearse()` —
  `has_cfg` already excludes symlinked configs before this call runs, so the flag's
  value never changes behaviour there.

## Remaining survivors

The 289 survivors are spread thinly over many modules, as in v0.2.5.
They were **not** individually reviewed, except for the ones in `talaria/apps/` and
`talaria/service.py` (this task's scope), all of which were killed or marked
equivalent above. The largest groups elsewhere are the same as before:
- arguments to stubbed collaborators in setup's fresh-install path;
- directory modes of directories the fixtures pre-create;
- config parsing of rarely used value forms;
- CLI parser details.

The deploy, rollback, restore, marker, service and apps modules have no survivors left.
Deploy's table row counts its 2 crash-hook mutants as killed: they trigger the e2e
crash hook, which kills the test process rather than raising a normal assertion
failure, so they are detected but mutmut's own run log may label them differently.
List them with `uv run mutmut results` after a run to see how the tool recorded them.
