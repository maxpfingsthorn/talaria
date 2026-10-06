# AGENTS.md — setting up Talaria for your user

You are a coding agent asked to "set up Talaria". Follow these steps exactly.

1. Read this file fully before running anything.
2. Clone the repository and check out its latest release tag:
   ```bash
   git clone https://github.com/maxpfingsthorn/talaria && cd talaria
   git checkout "$(git tag -l 'v*' --sort=-v:refname | head -1)"
   ```
3. **Ask the person which app this install manages — Hermes or Clawvisor — before
   running anything.** Don't assume: a host can run both, each as its own install.
   Pass `--app clawvisor` for a Clawvisor install; omit it (or pass `--app hermes`)
   for Hermes.
4. Setup uses a dedicated service user, `hermes` by default (`clawvisor` by default
   for a Clawvisor install). **Confirm the name with the person first**, then run
   `bin/talaria setup --plan --app APP --user NAME` and explain the plan in plain
   words. Pass the same `--app APP --user NAME` on every later run. If setup reports
   `FOUND: account … exists`, ask whether that account should be used.
5. `MISSING: <tool>`: work out the install command for this distribution (the hint line
   names the packages for Debian/Ubuntu, Fedora/RHEL and Arch). Ask before running it.
6. `ACTION REQUIRED`: relay it in plain words. **Never ask for secrets in the chat.**
   The person runs `set-token` in their own terminal, against this install's own bot
   (Clawvisor's install needs its own, second bot — never reuse Hermes's token).
   Show the pairing code; the person sends it to the bot.
7. Re-run `bin/talaria setup` (with the same flags) until it prints `DONE`.
8. `FOUND` / `STOP`: explain and let the person choose.
9. Adopting an existing install: show the diff setup printed, say that it restarts the
   agent, and get an explicit yes before running `setup --adopt UNIT`. The diff hides
   `Environment=` values; never print the old Quadlet file itself. If the plan lists
   variables that are not carried over, ask the person whether the app needs them.
   Clawvisor supports fresh installs only — there is no `--adopt` for it.
10. For a Clawvisor install, first login is a one-time link from `talaria login-link`.
    **Never run this command yourself and never show its output in chat, a ticket, or
    anywhere else it could be logged.** Tell the person to run `talaria login-link`
    themselves, in their own terminal, as the service user, and use the link directly.
11. **Never run `deploy`, `rollback` or `restore`.** Those are the person's decisions, made
    in Telegram.

Exit codes: 0 done · 10 a person must act · 1 stop or error.
