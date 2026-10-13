# Talaria: login links from the bot (v0.7.0)

> Historical: written for Talaria v0.7; paths and test names may have changed since (e.g. helpers/ → tools/).

Status: approved by the owner 2026-10-11.

## Goal

The owner gets a working login link for an app's dashboard from the Talaria bot,
without opening a terminal on the host. Today `talaria login-link` exists only for
Clawvisor and refuses to run without a TTY. gbrain needs a fresh login every day:
its admin sessions are in memory only and Talaria's nightly maintenance restarts it.

Done means: the owner sends `/login gbrain` (or `/login clawvisor`), a link arrives
within seconds, and opening it in a browser on the tailnet logs them in. A pending
gbrain connector approval can be reached the same way.

## Non-goals

- Making gbrain sessions survive restarts (gbrain's design; Talaria keeps the nightly
  maintenance).
- Configuring Tailscale. Talaria documents the serve/funnel setup and ships a helper the
  person runs as root; it never runs it.
- Hermes: its dashboard has a password; no link.

## Owner interface

- `/login [app] [oauth-request]`.
  - `app` is optional when exactly one configured app supports login links (same rule
    as the other app commands); otherwise the bot lists the apps that do.
  - `oauth-request` (gbrain only): the `oauth_request` value from a connector's consent
    URL, or the whole URL; the bot extracts the value. Accepted form:
    `^[A-Za-z0-9_-]{8,256}$` after extraction; anything else is refused with a usage line.
- `/status` shows a "Login link" button for each running app that supports it.
- The reply holds the link(s), the expiry ("single use, expires in 5 minutes" for
  gbrain; Clawvisor's wording), and nothing else.
- MENU and HELP list `/login`.

## Delivery rules (security)

- Sent only to the paired owner chat (existing rule for every reply).
- `disable_web_page_preview` on that message: a preview fetch by Telegram's servers
  could consume a single-use link (and the dashboards are tailnet-only anyway).
- 5 minutes after sending, the bot replaces the message text (`editMessageText`, no
  preview, no buttons) with "Login link for <App> expired. Send /login <app> for a new
  one." so the chat shows what happened and no longer holds the link. Pending edits are
  kept in memory; a bot restart in that window leaves the original message, which by
  then holds an expired link. An edit failure is ignored (journal line without the link).
- The link never reaches the journal, the history file, state, or any log line. The op
  writes it only to its stdout pipe as a dedicated `link` line (below), never through
  `JsonNotifier` (which prints message text to stderr). The hub never prints it.
- The app's owner credential (gbrain's `GBRAIN_ADMIN_BOOTSTRAP_TOKEN`) stays in the app
  account: the op runs `gbrain mcp admin login-link` inside the container, where the
  token is already in the environment. Nothing in the hub account sees it.
- README text changes from "never paste it into a chat" to: the bot sends it in the
  owner chat; it is single-use and short-lived, and useless outside the tailnet when
  the dashboard is tailnet-only. Pasting it into an agent's context stays forbidden;
  the terminal command `talaria login-link` keeps working as before.

## Hub ↔ app

- New op: `talaria op login-link [--oauth-request ID]`. Output: zero or more
  `{"kind": "link", "url": …, "label": …}` lines, then one `reply` line with the
  text around them (expiry, hints), or a `reply` with the refusal ("not available for
  Hermes", "gbrain is not running", "could not get a login link").
- `PROTOCOL` stays 1. The op is new, so an app on Talaria < 0.7.0 rejects it; the hub
  gates on the app's version from `hello` and answers "update Talaria first: /update
  v0.7.0" instead of calling it. (`_still_mismatched` and the skew note are unchanged.)
- Called with `relay.quick`-style timeout 60 s; Unreachable/NoAnswer map to the existing
  texts.
- No new `talaria.conf` or `hub.conf` keys.

## App side

- `App.login_link(ctx, oauth_request) -> list[tuple[label, url]]` (raises a
  `Refused(text)` for the refusals above). Default: not supported.
- Clawvisor: the existing `cli.login_link` logic, split into a function that returns
  the links (the TTY check stays in the CLI command only). The bot gets the same lines
  the terminal prints: `public_url` first, then one per published address with the
  SSH-tunnel hint where needed.
- gbrain: `podman exec <container> gbrain mcp admin login-link --url
  http://127.0.0.1:<container_port>/mcp --json [--oauth-request ID]`, timeout 30 s;
  parse `url` from the JSON. The URL gbrain returns is under its public URL
  (`https://<host>/admin/auth/<nonce>`); Talaria passes it on unchanged (the 443 → admin
  port redirect, below, takes the browser to the tailnet-only admin port).
  Refused when the service is not active.

## Onboarding (docs and helper)

The setup for gbrain gains the `/admin` redirect, so consent links and login links
work from the public address:

- README "Public MCP endpoints with Tailscale Serve and Funnel": after the funnel paths
  and the tailnet-only admin port, add the redirect: on 443, path `/admin` →
  `302:https://<host>.<tailnet>.ts.net:10000${REQUEST_URI}` (host written out, not
  `${HOST}`). Explain: on the public port it answers only with a redirect; the target
  is tailnet-only, so outsiders get nothing; it keeps the query and the browser keeps
  the `#fragment`. Replace the "open the same link with :10000" paragraph.
- `tailscale serve` has no CLI flag for redirects (the serve config supports it since
  Tailscale 1.92; the helper refuses older versions). New helper `helpers/tailscale-admin-redirect.py` (stdlib, run as root
  by the person): reads `tailscale serve status --json`, refuses unless the 443 web
  handler set exists and holds no `/admin` yet, adds the redirect for the given host
  and admin port, prints the before/after handler list, saves the old config to a file
  next to it, and applies it with `tailscale debug localapi POST
  /localapi/v0/serve-config -`. `--undo FILE` restores a saved config. Tested with a fake
  `tailscale` on PATH.
- "Who reaches what" table: add the `/admin` redirect row (public, redirect only).
- AGENT_SETUP step 11 (gbrain): show the helper command with the other root commands
  (never run it); the person logs in with `/login gbrain` in the bot (or the token).
- gbrain `ready_text`: mention `/login gbrain`.

## Errors

| Case | Reply |
|---|---|
| app does not support links (Hermes) | "Login links: <apps that support them>" |
| app's Talaria < 0.7.0 | "update Talaria first: /update v0.7.0" |
| service not active | "<App> is not running" |
| gbrain CLI fails / no `url` in JSON | "Could not get a login link from gbrain; details in the journal" (journal gets the exit code and stderr with the token redacted — never stdout) |
| bad `oauth-request` | usage line |
| unreachable / no answer | existing relay texts |

## Testing

- op: `login-link` emits `link` lines and a `reply`; nothing with the URL on stderr
  (capture stderr in the test); refusals; `--oauth-request` validation.
- gbrain: argv to `podman exec` (with and without `--oauth-request`), JSON parsing,
  non-zero exit, missing `url`, not active.
- Clawvisor: the split keeps the CLI output byte-identical (existing tests) and the
  function returns the same links.
- Bot: `/login` forms (no app with one supporting app, explicit app, URL vs id,
  bad id), version gate, `disable_web_page_preview` set, the expiry edit scheduled and run
  after 5 min (fake clock) with the expired text, edit failure ignored, the button on `/status`.
- Helper: adds exactly one handler, refuses when `/admin` exists or 443 is missing,
  `--undo` restores, never touches other ports.
- Contract/e2e: none needed beyond the unit suite (no new external API besides
  Telegram `editMessageText`, covered with the fake API).

## Release

v0.7.0 (new op, new bot command). Hub and apps update together through the bot as
usual; an app left behind gets the "update Talaria first" reply.
