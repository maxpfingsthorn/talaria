# AGENT_SETUP.md — setting up Talaria for your user

You are a coding agent asked to "set up Talaria". Follow these steps exactly.

1. Read this file fully before running anything.
2. Clone the repository and check out its latest published stable release:
   ```bash
   git clone https://github.com/maxpfingsthorn/talaria && cd talaria
   git checkout "$(bin/talaria latest-release)"
   ```
   Setup needs `git` ≥ 2.34 and `ssh-keygen`, and installs only a tag signed by the release
   keys (see README, "Release signing keys"). If the release lookup or checkout fails, or
   setup says the tag is not signed by a trusted key, stop and report it. Never fall back to the
   newest git tag: its release checks may not have passed.
3. **Ask the person which app this install manages — Hermes, Clawvisor or gbrain — before
   running anything.** Don't assume: a host can run both, each as its own install.
   Pass `--app clawvisor` or `--app gbrain` for those installs; omit it (or pass `--app hermes`)
   for Hermes.
4. Setup uses a dedicated service user per app, `hermes` by default (`clawvisor` or `gbrain` by default
   for those installs), and one hub account per host, `talaria`, which runs the
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
7. You prepare and explain; **the person runs `setup` in their own terminal**, because it
   asks for their sudo password once (`sudo -v`). **Never ask for the password and never
   type it.** Without a terminal setup stops with `STOP: setup needs your sudo password`:
   relay that. Do not pass `--operator-nopasswd` unless the person asks for passwordless
   sudo; explain that the default is a password. Re-run `bin/talaria setup` (same flags)
   until it prints `DONE`. A `NOTE:` that your login account can act as an account
   without a password offers an optional root paste to tighten it; show it, do not run it.
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
    themselves, in their own terminal, as the service user, or to send `/login clawvisor`
    to the bot, and use the link directly; the agent runs neither.
    The dashboard must be opened over HTTPS or an SSH tunnel to `http://127.0.0.1:<port>`
    (plain http to another address logs out at once); see docs/apps/clawvisor.md.
11. Tell the person to run setup from their own login account with `--app X --user X`
    (never from an app account or the hub account; setup stops if the login account is the
    hub, the app user, or already holds a Talaria config).
    For a Clawvisor install that Hermes must reach, ask the person which networking
    option from docs/apps/clawvisor.md to use: dummy NIC (recommended; needs
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
11a. For a gbrain install:
    - Run setup first: it writes `talaria.conf` and STOPs asking for `dashboard.public_url`.
      Ask the person for the https address MCP connectors will use, add it as
      `dashboard.public_url` (e.g. `https://<host>.<tailnet>.ts.net`, port 443; see
      docs/apps/gbrain.md) to `talaria.conf`, and run setup again.
    - **The agent never sees provider keys; the person adds them.** Tell the person to add
      them (e.g. `OPENROUTER_API_KEY=…`) to `~gbrain/.config/talaria/gbrain.env` themselves,
      in their own terminal and editor as the service user, then restart `gbrain.service`
      (gbrain picks up a changed key only after a restart). Never read, print, `cat`,
      `grep` or copy that file, and never ask for a key in the chat. The same file holds
      the admin token (`GBRAIN_ADMIN_BOOTSTRAP_TOKEN`); the same rules apply. Model tiers
      are gbrain's own config (`gbrain config`); with a non-Anthropic provider the person
      sets them to that provider's model ids (docs/apps/gbrain.md).
    - Public access is Tailscale Serve and Funnel, configured by the person as root (docs/apps/gbrain.md:
      "Public MCP endpoints with Tailscale Serve and Funnel"). Show the commands (including the
      `tools/tailscale-admin-redirect.py` for `/admin` on 443, run by the person with `sudo python3 -I` from their own clone at the release tag, docs/apps/gbrain.md); never run them.
      Explain that only the connector paths become public and that `/register` lets
      anyone create a pending client the person must approve.
    - The person logs in with `/login gbrain` in the bot (or the token in `/admin`), adds the connectors in claude.ai or ChatGPT and
      approves them there; local agents get scoped tokens from `/admin`.
12. **Never run `deploy`, `rollback`, `restore` or `maintain`.** Those are the person's decisions, made
    in Telegram.

Exit codes: 0 done · 10 a person must act · 1 stop or error.
