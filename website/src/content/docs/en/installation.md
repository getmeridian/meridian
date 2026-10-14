---
title: Installation
description: Install the Meridian CLI on your local machine.
---

## Quick install

```
curl -sSf https://getmeridian.org/install.sh | bash
```

This script:
1. Installs [uv](https://docs.astral.sh/uv/) if it is not present, with pipx or pip as fallbacks. The uv installer is pinned by version and checked against its SHA-256 before it runs.
2. Installs `meridian-vpn` from PyPI (set `MERIDIAN_VERSION=4.0.0` to install one exact release)
3. Adds the tool directory to your shell PATH when needed
4. Creates `/usr/local/bin/meridian` only when passwordless sudo is available

## Verified install

Every [GitHub release](https://github.com/getmeridian/meridian/releases) attaches `install.sh` and its `SHA256SUMS`. To check the script before running it, download both from the release and verify:

```
VERSION=4.0.0
curl -fLO https://github.com/getmeridian/meridian/releases/download/v$VERSION/install.sh
curl -fLO https://github.com/getmeridian/meridian/releases/download/v$VERSION/SHA256SUMS
sha256sum -c SHA256SUMS          # macOS: shasum -a 256 -c SHA256SUMS
less install.sh                  # optional: read it
MERIDIAN_VERSION=$VERSION bash install.sh
```

To check the copy at getmeridian.org instead, compare `curl -sSf https://getmeridian.org/install.sh | sha256sum` with the release's `SHA256SUMS`; they match unless the script changed after that release. When the newest release is a beta, the website's copy installs that beta by default, and that one line makes its checksum differ from the release asset.

## Manual install

With uv (recommended):
```
uv tool install meridian-vpn
```

With pipx:
```
pipx install meridian-vpn
```

## Update

```
meridian update
```

Meridian checks PyPI now and then before interactive commands and prints a notice when a newer version exists. It never installs updates on its own. `meridian update` upgrades through whichever package manager installed it and confirms that the active `meridian` command reports the new version. Set `MERIDIAN_DISABLE_UPDATE_CHECK=1` to turn off the background check in automation.

Updating the CLI changes nothing on your servers. If a server works, there is no rush; read the [release notes](https://github.com/getmeridian/meridian/releases), then run the same `meridian deploy` command (or `meridian apply`) when you are ready to apply the new version.

## Upgrading from 3.x

Meridian 4 is **not an in-place upgrade**. It replaces the 3x-ui panel with Remnawave, and links from a 3.x server cannot carry over: every user needs a new link.

Your 3.x servers keep working. You have two choices:

- **Keep managing them with 3.x.** Reinstall the 3.x CLI:
  ```
  uv tool install 'meridian-vpn<4'
  ```
  (or `pipx install 'meridian-vpn<4'`). Meridian 3.17.3 and later stay on 3.x when you run `meridian update`, unless you pass `--major`.
- **Move to Meridian 4.**
  1. Deploy a fresh server with Meridian 4: `meridian deploy NEW_IP`
  2. Give your users their new connection pages (`meridian client add NAME` for each person)
  3. Once everyone has switched, tear down the old server with `meridian teardown OLD_IP` — Meridian 4's teardown also removes 3x-ui

Meridian 4 keeps its own state in `~/.config/meridian/` and never reads or changes 3.x state in `~/.meridian/`, so both versions can live on one machine; fleet commands that find only 3.x state explain these options instead.

These docs describe Meridian 4. The [Meridian 3.17.3 docs](https://github.com/getmeridian/meridian/tree/v3.17.3/website/src/content/docs/en) stay on GitHub for servers you keep on 3.x.

## Requirements

- **Python 3.11+** (uv installs and manages it for you)
- **SSH access** to your server, as root or a user with passwordless sudo. Key login is best; if the server accepts only a password, `meridian deploy` and `meridian setup` offer to create a key for Meridian and install it with that password once (see [Password-only servers](/en/deploy/#password-only-servers))

Terminal QR codes are rendered by the bundled `segno` package; no system dependency is required.

## Verify installation

```
meridian --version
```
