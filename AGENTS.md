# AGENTS.md — setting up Talaria for your user

You are a coding agent asked to "set up Talaria". Follow these steps exactly.

1. Read this file fully before running anything.
2. Clone the repository and check out its latest release tag:
   ```bash
   git clone https://github.com/maxpfingsthorn/talaria && cd talaria
   git checkout "$(git tag -l 'v*' --sort=-v:refname | head -1)"
   ```
   Then run `bin/talaria setup --plan` and explain the plan in plain words.
3. Setup uses a dedicated service user, `hermes` by default. **Confirm the name with the
   person before handing over any root block.** If setup reports `FOUND: account … exists`,
   ask whether that account should be used, and re-run with `--user NAME`.
4. `MISSING: <tool>`: work out the install command for this distribution (the hint line
   names the packages for Debian/Ubuntu, Fedora/RHEL and Arch). Ask before running it.
5. `ACTION REQUIRED`: relay it in plain words. **Never ask for secrets in the chat.**
   The person runs `set-token` in their own terminal. Show the pairing code; the person
   sends it to the bot.
6. Re-run `bin/talaria setup` (with the same flags) until it prints `DONE`.
7. `FOUND` / `STOP`: explain and let the person choose.
8. Adopting an existing install: show the diff setup printed, say that it restarts the
   agent, and get an explicit yes before running `setup --adopt UNIT`.
9. **Never run `deploy`, `rollback` or `restore`.** Those are the person's decisions, made
   in Telegram.

Exit codes: 0 done · 10 a person must act · 1 stop or error.
