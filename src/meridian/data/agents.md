# Meridian for AI agents

Meridian deploys and runs censorship-resistant proxy servers. One command turns a fresh Debian or Ubuntu server into a hardened VLESS+Reality endpoint and returns a connection page the user can share. Meridian's job is to get every layer right: firewall, certificates, SNI routing, TLS fingerprints, and verification with real traffic. The operator shouldn't have to understand any of them.

You are probably helping one of two people:

- **A deployer**, who runs servers and gives access to others. They need commands run, results explained, and problems diagnosed.
- **A user of a deployer's server**, who has a connection page or subscription link. They need help installing an app and connecting. They don't run the CLI.

## How Meridian works

- **Fleet roles.** One server is the *control server*. It runs the Remnawave panel, its database, and the connection pages. *Exits* are where traffic leaves for the internet, and the control server is usually an exit too. *Relays* are plain TCP forwarders, placed where an exit's address is blocked. *Gateways* pick an exit (or a pool of exits) for each destination by rule, or send it directly from the gateway.
- **One reviewed topology.** The deployer's machine keeps the whole fleet description in `cluster.yml`, inside Meridian's home directory. Every change is an edit to that topology: `deploy`, `setup`, `node add`, `relay add`, `client add`, and `route add` all edit it. Meridian then compiles it into a hash-reviewed plan and applies it with checkpoints. An interrupted apply resumes where it stopped. A converged fleet plans no changes.
- **Users live in the panel.** Client accounts, their keys, and their traffic are stored in Remnawave on the control server. `cluster.yml` lists which clients Meridian manages.
- **Delivery.** Each client gets a connection page with QR codes and app links, plus a subscription URL. Apps refresh the subscription, so fleet changes reach devices without new links.
- **Connection methods.** Reality and XHTTP run on TCP 443, Hysteria2 on UDP 443 as a fallback, and WSS through Cloudflare when a domain is set. Apps try them in order.

## Where state lives

- The home directory is `~/.config/meridian` (or `$XDG_CONFIG_HOME/meridian`). `MERIDIAN_HOME` overrides it. Meridian also uses an existing `~/.meridian-v4` or `~/.meridian` when either holds a fleet (it has a `servers.json`) and the default directory doesn't exist yet.
- That directory is how the deployer manages the fleet: `cluster.yml` holds the topology and panel access, and `servers.json` holds saved SSH targets. Tell the user to back it up. To manage the fleet from another machine, they copy the whole directory there.
- Never edit `cluster.yml` by hand. Change the fleet through commands.

## Rules for agents

1. **Run non-interactively.** Pass `--yes` to commands that confirm. `meridian setup` is a wizard that needs a terminal. Use `deploy`, `node add`, `relay add`, and `client add` instead, or `setup --intent FILE --yes` with a complete topology document.
2. **Prefer JSON.** Commands that support `--json` print one `meridian.output/v1` envelope on stdout. Read results under `data`; failures come back as an envelope too. Progress, warnings, and update notices go to stderr. `meridian api commands` lists every command with a machine-readable contract.
3. **Read before you change.** Run `meridian plan` to see pending changes. It exits `0` when converged and `2` when changes are pending. Then run `meridian apply --yes`.
4. **Ask before destroying.** Get explicit confirmation before `teardown`, `node remove`, `relay remove`, `client remove`, or `client reissue`. Removal deletes the panel and server state for what disappears, and people relying on it lose access; a reissue cuts off the client's devices until they import the new link.
5. **Treat links as secrets.** Connection pages and subscription URLs grant access. They appear in full only in the output of the command that produced them (`deploy`, `client add`, `client show`, `client reissue`). Hand each one only to the person it belongs to, and don't log it. Never run `meridian panel --show-password` unless the user asks for the panel password.
6. **Test from the right place.** `meridian check` inspects servers over SSH. `meridian test` sends real traffic from the machine it runs on, and `meridian test --probe` (short form: `meridian probe`) checks what a censor sees. Results about blocking only mean something when run from inside the censored network.
7. **Report with doctor.** `meridian doctor` collects a redacted diagnostic report that is safe to attach to a bug report. `meridian logs SERVER` shows recent node, panel, nginx, and relay logs with the same redaction; narrow it with `--grep TEXT` and `--component NAME`.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success, or every check passed |
| `1` | Internal error (a Meridian bug) |
| `2` | Invalid input or a user-fixable problem; for `plan`, changes are pending |
| `3` | Required evidence was unavailable (SSH, panel, or local state); retry or fix access |
| `4` | A check or test completed and found problems |
| `5` | The operation was cancelled at a safe point; rerunning resumes it |
| `130` | Interrupted with Ctrl-C; no JSON envelope is printed |

## Common tasks

```bash
# Deploy one server; it becomes the control server and an exit
meridian deploy 198.51.100.10 --yes --json

# Preview a deploy without touching the server
meridian deploy 198.51.100.10 --dry-run --json

# Give a person access and get their connection page
meridian client add alice --json

# Give them 50 GB a month until the end of 2026; later lift both, or refill the month now
meridian client set alice --limit 50GB --reset month --expires 2026-12-31 --json
meridian client set alice --limit none --expires never --json
meridian client reset-traffic alice --json

# A link leaked: replace it (the old page and subscription stop working)
meridian client reissue alice --yes --json

# Add an exit in another country, then a relay in front of it
meridian node add 198.51.100.20 --name nl --yes
meridian relay add 203.0.113.10 --exit nl --yes

# Is everything healthy? Does traffic pass from here?
meridian check --json
meridian test --json

# `meridian test --probe` says the camouflage SNI is blocked: switch to another site
meridian node sni nl www.cloudflare.com --yes
meridian relay sni 203.0.113.10 dl.google.com --yes

# What would change, then apply it
meridian plan --json
meridian apply --yes --json
```

Servers are named like ssh targets: `[user@]host[:port]`, a saved server name, or `local` for the machine Meridian runs on. Meridian connects with the user's SSH keys and configuration. It needs root or passwordless sudo.

## Learn more

- `meridian help COMMAND` or `meridian COMMAND --help` shows one command's options and examples.
- `meridian api schemas`, `meridian api schema NAME`, and `meridian api workflow deploy` print the JSON contracts that UIs and scripts build on.
- `https://getmeridian.org/llms.txt` indexes the documentation as Markdown, and `https://getmeridian.org/llms-full.txt` holds all of it in one file.
