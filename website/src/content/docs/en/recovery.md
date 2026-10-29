---
title: IP Blocked Recovery
description: What to do when your server's IP gets blocked — diagnosis and recovery options.
---

## Diagnose

Run from your own machine (no SSH needed):

```
meridian test 198.51.100.10
```

If the TCP check on port 443 fails from your network but works from elsewhere (another network or a VPN), the IP is most likely blocked. A TCP failure everywhere usually means a cloud firewall or a server that is down.

When nothing on an exit answers from your network, `meridian test` ends with the `node replace` command for that exit (below); `--json` returns it as `data.recovery`.

To see what exactly is blocked, run [`meridian test 198.51.100.10 --probe`](/en/cli-reference/#censors-view-meridian-test---probe) from inside the censored network: it reports whether the server IP or the camouflage SNI looks blocked. People without the CLI can open their connection page; if it loads on their network, the control server is reachable from there.

## Immediate relief

If you deployed with a **domain** (`--domain`) and the record is proxied through Cloudflare, the XHTTP and WSS paths still work — they reach Cloudflare, not your blocked IP. Apps using the Xray JSON or Mihomo subscription switch over on their own; others can pick the XHTTP or WSS entry by hand.

If you have a **relay**, clients connecting through it are unaffected as long as the relay itself can still reach the exit.

## Recovery options

### Add a replacement exit

The fastest path, as long as your control server (the one running the control service) is still reachable from your machine:

```bash
# 1. Get a new VPS from your provider (new IP)
# 2. Add it to the fleet
meridian node add 198.51.100.20 --name replacement
```

Clients keep their accounts. They receive the new exit on their next subscription refresh — ask them to refresh in their app if it doesn't happen on its own.

### Or move the blocked exit to a new server

`node replace` moves an exit to a fresh server, keeping its ID, keys, protocol paths, and relays, then cleans up the old server:

```bash
meridian node replace exit-1 198.51.100.20
```

Clients pick up the new address on their next subscription refresh. The control server can't be moved this way: its address is part of every subscription URL.

If the old server is blocked from your own network too, the move still deploys the new server first, so clients move over; only the cleanup of the old server fails. Finish it with `meridian apply` once the block lifts or from a network that reaches the old server. If it never will (you deleted it at your provider, or it stays blocked), let Meridian forget it:

```bash
meridian apply --forget-unreachable
```

Meridian then skips that cleanup; the control service still forgets what it held. Whatever ran on the old server keeps serving the users it had, including clients you remove later, until you delete or wipe it at your provider. Pass `--forget-unreachable` to `node replace` directly to do it in one step.

### Add a domain for CDN fallback

If you weren't using a domain, add one to the new exit:

```bash
meridian node add 198.51.100.20 --domain vpn.example.com
```

See [Domain mode](/en/domain-mode/) for the Cloudflare steps.

### Add a relay

A relay on a domestic server gives users an entry point that stays reachable when foreign IPs are blocked:

```bash
meridian relay add 203.0.113.10 --exit 198.51.100.20
```

To point a relay at a different exit, remove it with `meridian relay remove` and add it again with `--exit` set to the new exit.

### Control server lost

If the control server is gone (deleted, seized, or blocked for good), rebuild it on a new server from a snapshot:

```bash
meridian restore --to 198.51.100.30
```

Every machine that manages the fleet saves an encrypted snapshot of the control service after each change, so the newest one is already on your machine. Restore reviews what it will do, installs the control service on the new server from that snapshot, and applies the fleet: every exit's agent enrolls with the new server, and clients keep their accounts, limits, and traffic.

- **Links**: on a bare IP, every subscription URL contains the control server's address, so each link changes; restore prints every client's new link to send. With a domain, links stay the same once you point its DNS record at the new server; the review names the records.
- **Old server**: restore refuses while the old control server still answers, because two control services would split the fleet. If it answers but you no longer trust it (seized, for example), pass `--force`.
- **Keys**: the new server gets a new fleet authority and new signing keys, so a seized server can't sign anything your nodes accept. The console owner sets a new password through the link `meridian panel` prints.
- **Another machine**: on a machine without the fleet, copy the snapshot file over and run `meridian restore --to NEW_IP --snapshot FILE --recovery-key`, then paste the recovery key when asked. The snapshot carries the fleet's own files; that machine still needs SSH access to the new server and to your exits.

The recovery key is shown once, at the end of your first deploy; `meridian backup recovery-key` makes a new one. Keep it offline, away from the machines that manage the fleet. See [`meridian restore`](/en/cli-reference/#meridian-restore).

## Managing from more than one machine

Everything Meridian knows about a fleet lives in `~/.config/meridian/` on the machine you deploy from. To manage the fleet from a second machine, copy that directory over, leaving out `device.key` and `snapshots/` so the second machine gets its own backup key on its first change. Copy it again after each change you make elsewhere. `meridian backup devices` lists the machines that receive snapshots, and `meridian backup revoke` removes one you no longer use.

You can't accidentally undo another machine's work. The control service keeps a record of the last change applied. Before changing anything, Meridian compares your copy with that record, and `meridian plan` warns ahead of time if an apply would be refused:

- **"Another machine changed this fleet"**: your copy is out of date. Copy `~/.config/meridian/` from the machine that applied last, then run the command again.
- **"Another apply is running"**: someone is changing the fleet right now. Wait for them to finish. If that run crashed, its claim expires within 30 minutes; a crashed run on your own machine doesn't block you at all.
- **"This control server belongs to a different copy of the fleet"**: this `~/.config/meridian/` was never copied from the machine that manages the fleet.

## Prepare before a block

1. **Add a relay** on a domestic server: `meridian relay add RELAY_IP --exit EXIT_IP`
2. **Use a domain** for CDN fallback: deploy with `--domain vpn.example.com`
3. **Both** — clients then have three ways in: relay (domestic), CDN (Cloudflare), and direct
4. **Keep the recovery key offline** — with it and a snapshot, `meridian restore` rebuilds a lost control server
5. **Back up `~/.config/meridian/`** — it is what lets you manage the fleet from another machine

## Keep the old server

Don't tear down the blocked server right away — blocks are sometimes lifted after days or weeks. Check now and then:

```bash
meridian test 198.51.100.10
```

When you no longer need it, remove it with `meridian node remove`.
