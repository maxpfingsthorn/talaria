# Mutation testing report

Date: 2026-10-07 · Commit: 1620728 + this report (v0.5.0, hub) · Tool: mutmut 3.8.0 (Python 3.12)

**Score: 96.8 %** (threshold 85 %). 9,829 mutants: 9,512 killed, 7 timeouts (counted as
killed), 310 survived, 0 without tests, 0 suspicious. Scoring is honest:
`tests/mutation_score.py` counts no-tests and suspicious mutants as survivors. The full run
was made once on the final code; an earlier full run on the pre-fix code (9,971 mutants,
94.6 %) was used to find the hub-module survivors listed below.

| Module | Mutants | Killed | Timeout | Survived |
|---|---|---|---|---|
| `talaria.setup` | 1678 | 1630 | 0 | 48 |
| `talaria.telegram` | 826 | 825 | 1 | 0 |
| `talaria.cli` | 654 | 599 | 0 | 55 |
| `talaria.rollback` | 578 | 578 | 0 | 0 |
| `talaria.adopt` | 567 | 550 | 0 | 17 |
| `talaria.op` | 553 | 553 | 0 | 0 |
| `talaria.apps.clawvisor` | 546 | 525 | 2 | 19 |
| `talaria.apps.hermes` | 392 | 392 | 0 | 0 |
| `talaria.deploy` | 271 | 271 | 0 | 0 |
| `talaria.conf` | 243 | 219 | 0 | 24 |
| `talaria.images` | 235 | 228 | 0 | 7 |
| `talaria.notify` | 234 | 219 | 0 | 15 |
| `talaria.hubupdate` | 222 | 222 | 0 | 0 |
| `talaria.rehearse` | 220 | 202 | 0 | 18 |
| `talaria.selfupdate` | 208 | 208 | 0 | 0 |
| `helpers.confdiff` | 206 | 202 | 0 | 4 |
| `talaria.backup` | 206 | 181 | 0 | 25 |
| `talaria.hubexec` | 181 | 181 | 0 | 0 |
| `talaria.relay` | 178 | 178 | 0 | 0 |
| `talaria.history` | 174 | 170 | 0 | 4 |
| `talaria.check` | 161 | 158 | 0 | 3 |
| `talaria.status` | 136 | 129 | 0 | 7 |
| `talaria.units` | 131 | 124 | 0 | 7 |
| `talaria.containers` | 107 | 105 | 0 | 2 |
| `talaria.restore` | 102 | 102 | 0 | 0 |
| `talaria.service` | 98 | 96 | 2 | 0 |
| `talaria.hubconf` | 91 | 91 | 0 | 0 |
| `talaria.ctx` | 77 | 61 | 0 | 16 |
| `helpers.dbopen` | 76 | 72 | 0 | 4 |
| `talaria.upstream` | 74 | 60 | 0 | 14 |
| `talaria.hubcheck` | 65 | 65 | 0 | 0 |
| `talaria.state` | 64 | 62 | 0 | 2 |
| `helpers.migrate` | 63 | 62 | 0 | 1 |
| `talaria.tags` | 57 | 55 | 0 | 2 |
| `talaria.retention` | 48 | 48 | 0 | 0 |
| `talaria.shell` | 44 | 39 | 0 | 5 |
| `talaria.disk` | 41 | 28 | 2 | 11 |
| `talaria.marker` | 15 | 15 | 0 | 0 |
| `talaria.apps.__init__` | 7 | 7 | 0 | 0 |

`service`'s 2 timeouts are pre-existing. `deploy`, `rollback`, `restore` and `marker` have 0
survivors.

## What changed in v0.5.0 (hub)

New modules and their survivors (from the table above):

| Module | Mutants | Survivors |
|---|---|---|
| `talaria.hubconf` | 91 | 0 |
| `talaria.hubexec` | 181 | 0 |
| `talaria.relay` | 178 | 0 |
| `talaria.telegram` (rewritten for the hub) | 826 | 0 (1 timeout) |
| `talaria.hubcheck` | 65 | 0 |
| `talaria.hubupdate` | 222 | 0 |
| `talaria.op` | 553 | 0 |
| `talaria.selfupdate` | 208 | 0 |

In `talaria.setup`, the new functions (`account_lines`, `op_rule_lines`, `root_block`,
`operator_phase`, `hub_phase`, `_telegram_ready`, `move_env`, `_v04_bot`, `_migrate`,
`import_telegram`, `_older_than_installed`) have 0 survivors. The 48 that remain are in
`prerequisites`, `selinux_enforcing`, `say`, `_fresh_image`, `service_phase` and `setup`:
older code, not reviewed individually.

The first full run had 137 survivors in the eight hub modules and 81 in the new `setup`
functions. The 69 tests in `tests/test_hub_survivors.py` pin what the behavioural tests did
not: exact stderr texts, argv and timeouts, which object (`ctx`, `st`, `api`) a collaborator
receives, `check=False` on best-effort commands (a non-zero exit must not raise), the
200/300-character cuts, first-versus-last separator splits, and the `sudo -n` probes of other
accounts. `rollback`, `restore`, `marker` and `deploy` remain at 0 survivors.

New `# pragma: no mutate` lines, each with its reason next to the code:
- `hubexec.parse_lines`: `d = None` (any non-dict takes the same branch).
- `hubconf.register_app`: the `.tmp` name of a file that is renamed away.
- `relay.relay`: `sent = False` (`None` is falsy too); `traceback.print_exc(file=sys.stderr)`
  (stderr is the default); the same call in `hubcheck.check`.
- `hubupdate._update_app`: `last = ""` (only read through `last or …`).
- `selfupdate.dry_run`: the `shutil.rmtree(wt, ignore_errors=True)` before `worktree add`
  (the `ignore_errors` variants are equivalent on a removable directory; the removal itself
  is pinned by a test).
- `op`: the `ArgumentParser`, `add_subparsers` and `add_parser` lines (`None` equals `False`;
  `prog` and `add_help=True` are pinned by tests; `required` is moot because `main` checks
  the op name first); `(data or "")` in `decide_button`; the tuple-to-list conversion of
  message blocks (json writes tuples as lists anyway); the not-a-release-tag
  `print(..., file=sys.stderr)` (`main` redirects stdout to stderr).
- `telegram`: `FORMS[what], []` (`None` and `[]` both mean no arguments); `data or ""`;
  `f"Error: {e}"[:200]` (cut again at the answer); `split("@", 1)[0]` (the maxsplit does not
  change element 0; `rsplit` is pinned by a test).
- `setup`: the `kv.get(…, "")` defaults in `import_telegram`; the initial `installed` and
  `hub_installed` values and the `create=False` arguments (a missing account always ends in
  the root paste before they are read; `None` equals `False`);
  `getattr(args, "import_telegram", False)`, `added = False` and
  `getattr(args, "no_restart", False)` (`None` equals `False`; the `True` defaults are pinned
  by tests); `args.register.partition(":")` (`rpartition` fails the same way).

The e2e suite now runs hub, Hermes and Clawvisor on one runner: `test_e2e.py`,
`test_e2e_clawvisor.py` and `test_e2e_hub.py` in one CI job. It is not part of the mutation
run.

## Clawvisor adapter (`talaria.apps.clawvisor`)

546 mutants: 525 killed, 2 timeouts (`rehearse`, readiness-poll counters), 19 survivors.
`rollback`, `restore`, `marker`, `deploy` and `apps.hermes` stay at zero true survivors.
The remaining Clawvisor survivors, not chased further:
- `prepare` (12): file/dir modes and message wording of the first-run bootstrap
  (`chmod`/`mkdir` arguments on directories the fixtures pre-create, generated-secret length).
- `fetch` (4): staging-dir and download-label details that only differ in error text.
- `rehearse` (1), `reacquire` (1), `data_version` (1): argument/label variants with no
  behavioural effect in the fakes.

Equivalent mutants carry `# pragma: no mutate` with a reason: the sqlite `uri=True`
connection and SQL keyword case in `migrations()`/`_migrations_or_raise()`, `values +=` vs
`=` on an empty list in `_redact`, and the unread-vault fallback `v = ""`.

## What changed in v0.3.0 (since v0.2.5)

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

The remaining survivors are spread thinly over many modules, as in v0.2.5.
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
