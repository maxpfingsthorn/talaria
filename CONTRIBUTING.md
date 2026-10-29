# Contributing to Talaria

Thanks for your interest. Talaria is a small project with one maintainer, so please read
this before you put time into a change.

## Before you start

- **Bugs:** open an issue with one of the forms ("Setup is stuck" or "Something went
  wrong"). Remove host names, IP addresses, user and chat IDs, tokens and keys from anything
  you paste.
- **Security problems:** never in a public issue. See [SECURITY.md](SECURITY.md).
- **Questions and ideas:** use [Discussions](https://github.com/maxpfingsthorn/talaria/discussions).
- **Larger changes** (a new app, another messenger, a new platform): start a discussion
  first. The README lists what is [not supported and not planned](README.md#what-it-does);
  a pull request in one of those areas will most likely be declined.

Small fixes (typos, clearer messages, a failing edge case with a test) are welcome as pull
requests straight away.

## Development setup

You need Linux, Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/). Rootless podman is only
needed to run Talaria itself, not for the unit tests.

```bash
git clone https://github.com/maxpfingsthorn/talaria && cd talaria
git checkout develop
uv sync
uv run pytest            # unit tests: fast, no podman, no root
uv run ruff check .      # lint; CI runs it too
```

Optional: `uvx pre-commit install` runs ruff and shellcheck before each commit.

Other test suites:

| Suite | Command | Where |
|---|---|---|
| End-to-end | `TALARIA_E2E=1 uv run pytest -m e2e tests/e2e` | **Only on a disposable machine.** It creates users, sudo rules and systemd units. CI runs it on pull requests into `main`. |
| Contract | `TALARIA_CONTRACT=1 uv run pytest -m contract tests/contract` | Pulls the newest Hermes image; checks Talaria's assumptions about it. |
| Mutation | `uv run mutmut run && uv run mutmut export-cicd-stats && uv run python tests/mutation_score.py` | Slow. Releases require a score of at least 85 %. |

## Making a change

- Branch off `develop` and open your pull request against `develop`. `main` only receives
  releases.
- Keep a pull request to one topic. Add a test for every behaviour change; write it first
  and check that it fails without your change.
- Runtime code uses the Python standard library only. Development tools go in the `dev`
  dependency group in `pyproject.toml`.
- Match the style of the surrounding code. Comments explain *why*, not *what*.
- User-facing text (bot messages, setup output) is short, concrete and tells the reader
  what to do next.
- The repository is public and generic: no real host names, IP addresses, accounts or
  tokens, also not in tests or docs. Use placeholders such as `<host>.<tailnet>.ts.net` and
  `100.64.0.1`. CI rejects anything that looks like a real Tailscale address.

CI must be green before a pull request is merged: the unit tests on Python 3.10 and 3.13,
and the branch up to date with `develop`. Review is best effort; it may take a few days.

## Rules that keep existing installs working

Talaria updates itself on hosts nobody is watching, so these rules matter more than any
style question. A pull request that breaks one of them will be sent back.

- **Configuration.** At runtime Talaria ignores keys in `talaria.conf` and `hub.conf` that it
  does not know (with a warning); only `setup` refuses them. So a new key must be optional,
  with a default that keeps today's behaviour, and a value that existing installs may have
  written must stay accepted.
- **The `op` protocol** between the hub and each app stays at `PROTOCOL = 1`. A hub must keep
  working with apps one release behind it, and the other way round.
- **New `op` arguments** are sent only to apps that report a version that accepts them (see
  `FIRST_PIN_RELEASE` in `talaria/hubupdate.py`). The existing arguments of
  `op self-update` must stay accepted by every later version.
- **Self-update runs the old code.** The update *to* a version is carried out by the version
  before it; a fix in the update path only takes effect one release later.
- **Secrets never reach logs.** Login links, tokens and keys must not appear on stderr, in
  the journal or in exception messages.
- **Untrusted data.** An app's data directory is writable by the app. Code that reads or
  writes there must not follow symlinks (see `talaria/safefs.py` and the rehearsal copy in
  `talaria/rehearse.py`).

## Releases

Only the maintainer releases (at most once a week unless a fix is urgent).

1. Add a `## vX.Y.Z (date)` entry to `CHANGELOG.md` for operators and bump `__version__`.
2. Open a pull request `develop` → `main` and merge it with a merge commit when every gate is
   green: unit tests, `e2e`, mutation testing (at least 85 %) and the contract test all run
   on this pull request.
3. On their own machine, the maintainer signs the tag with one of the keys in
   `keys/allowed_signers`: `git tag -s vX.Y.Z origin/main -m "Talaria vX.Y.Z"`, and pushes it.
4. The tag-push workflow runs `tools/verify-release-tag` (signature against the tagged
   commit's `keys/allowed_signers`, commit on `main`, version equals tag) and publishes a
   `git archive` tarball with a build-provenance attestation. It catches operator mistakes,
   not a stolen credential; the signature checks in the installs are the defence.
5. Replace the generated release notes with the changelog entry.

Adding or removing a signing key goes in a release signed by a key the installs already
trust. Tags cannot be moved or deleted.

## Licence

By contributing, you agree that your contribution is licensed under the
[MIT licence](LICENSE) of this project.

## Code of conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).
