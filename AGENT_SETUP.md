# AGENT_SETUP.md — setting up Talaria for your user

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
4. Setup uses a dedicated service user per app, `hermes` by default (`clawvisor` by default
   for a Clawvisor install), and one hub account per host, `talaria`, which runs the
   Telegram bot for every app. **Confirm both names with the person first** (`--hub NAME`
   picks another hub name), then run `bin/talaria setup --plan --app APP --user NAME` and
   explain the plan in plain words. Pass the same flags on every later run. If setup reports
   `FOUND: account … exists`, ask whether that account should be used.
5. `MISSING: <tool>`: work out the install command for this distribution (the hint line
   names the packages for Debian/Ubuntu, Fedora/RHEL and Arch). Ask before running it.
6. `ACTION REQUIRED`: relay it in plain words. When it prints a `sudo bash … <<'TALARIA'`
   command, show the whole command to the person to paste into their own terminal.
   **Never run it yourself** — it needs the person's sudo password. **Never ask for secrets in the chat.**
   The person runs `set-token` in their own terminal as the hub account, once per host
   (setup prints the exact command). A second app uses the same bot: no new token, no
   pairing.
   Show the pairing code; the person sends it to the bot.
7. Re-run `bin/talaria setup` (with the same flags) until it prints `DONE`.
7a. If setup plans to "move the bot" from an app's account to the hub (an install from
   v0.4), explain it: the same bot and chat, no new pairing, the app is not restarted.
   `NOTE:` lines are informational; relay them.
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
    The dashboard must be opened over HTTPS or an SSH tunnel to `http://127.0.0.1:<port>`
    (plain http to another address logs out at once); see the README's Clawvisor section.
11. For a Clawvisor install that Hermes must reach, ask the person which networking
    option from the README's Clawvisor section to use: dummy NIC (recommended; needs
    root, so the person runs that snippet), Tailscale, or host loopback (no root, but
    exposes all host-loopback services to the Hermes container). Edit only the
    `talaria.conf` of the user you run as; for the other app, tell the person which line
    to add and that they re-run that app's setup as that user. Also ask whether the person
    wants browser access to Clawvisor on a second address (Tailscale if they use it, else
    a LAN address, else an SSH tunnel). A "LAN address" means a private address on a
    network the person trusts (home or office). On a cloud VM the private/VPC address
    may be reachable from elsewhere (1:1 NAT, provider network): prefer Tailscale or an
    SSH tunnel there. With Tailscale, recommend
    `dashboard.bind = 10.254.254.1 tailscale` (dummy NIC for Hermes, Tailscale for the
    browser).
12. **Never run `deploy`, `rollback` or `restore`.** Those are the person's decisions, made
    in Telegram.

Exit codes: 0 done · 10 a person must act · 1 stop or error.
