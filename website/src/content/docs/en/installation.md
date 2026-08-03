---
title: Installation
description: Install the Meridian CLI on your local machine.
order: 2
section: guides
---

## Quick install

```
curl -sSf https://getmeridian.org/install.sh | bash
```

This script:
1. Installs [uv](https://docs.astral.sh/uv/) if it is not present, with pipx or pip as fallbacks
2. Installs `meridian-vpn` from PyPI
3. Adds the tool directory to your shell PATH when needed
4. Creates `/usr/local/bin/meridian` only when passwordless sudo is available

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

Meridian periodically checks PyPI before interactive commands and prints a notice when a newer version exists. It never installs updates automatically. `meridian update` explicitly upgrades through the available package manager; review release notes before a major update. Set `MERIDIAN_DISABLE_UPDATE_CHECK=1` to disable background version checks in automation.

## Requirements

- **Python 3.11+** (the project minimum; uv can install and manage it automatically)
- **SSH access** to your target server. Terminal commands require key access; Studio can use the password once to install a key

Terminal QR codes are rendered by the bundled `segno` Python package — no system dependency required.

## Verify installation

```
meridian --version
```
