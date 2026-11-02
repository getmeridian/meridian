---
title: Privacy
description: Choose what device limits and usage history store, and understand what a seized control server reveals.
---

Meridian starts with **device limits off** and **usage history off**. You opt in to extra records when you need them. No level stores client IP addresses, connection logs, or request paths.

The control service already holds people's accounts and connection credentials, lifetime traffic totals, and last seen. Someone who takes it can read that state. These settings decide which device details and daily records are added to it.

## Server capacity

Node agents report CPU count and load, total and available memory, host network counters and rates, and Xray process uptime and resident memory. These are server aggregates, independent of device limits and personal usage history. No client addresses, device details, interface identifiers, or per-person connections are collected. The pinned core cannot supply safe aggregate connection counts, so those stay unavailable.

Host network traffic includes protocol overhead and other services; it differs from traffic counted for people. Counters start at zero in each counter epoch and restart after agent restarts, interface changes, resets, wraparound, or the exact-integer ceiling. The first sample has no rate. Failed collection is unavailable, older agents are “not reported,” and a measured zero remains zero.

The service keeps one hour of recent observations in memory, at most one point per 10-second bucket (360 points per node). Agents normally report once a minute. SQLite retains the current UTC hour and the preceding 167 hourly buckets: integer averages and peaks, and network bytes only across adjacent received samples in the same counter epoch. Unmeasured intervals are gaps; zero means a measured zero. Old hourly buckets are pruned every 30 seconds. The latest sample stays while the node exists; deleting a node removes its capacity data.

The Servers screen shows current capacity and the latest 24 UTC hourly buckets, including the incomplete current hour, with measured network duration. A sample becomes stale after 180 seconds without an accepted collection; hellos, polls, and duplicate reports do not refresh it. Recent memory series clear on service restart; hourly rollups survive. Server aggregates ride encrypted snapshots at every privacy level, and restored current samples remain stale until new evidence arrives. A seized service reveals how busy each server was; these series name no people or connections.

A separately issued `metrics.read` token can scrape node capacity, durable node traffic totals, and service request counts/latency, task health, and SQLite size through the existing private control ingress. Labels never contain people, client addresses, devices, URLs, credentials, or secret paths; request observations retain only fixed route IDs and aggregate counters in memory. Compatible presets do not gain this grant. Prometheus keeps its own copies and retention: revocation stops new scrapes but does not remove existing copies. Pages and the console make no external requests. See [API tokens and scraping](/en/client-management/) for setup.

## Device limits

| Level | What Meridian keeps | What you see |
| --- | --- | --- |
| `off` (default) | No device records; devices are not tracked or limited | No device count |
| `private` | HMAC-SHA256 fingerprints of device IDs, made with a secret shared only inside your fleet; no systems, models, or apps | A device count and the cap |
| `full` | Device ID, platform, OS version, model, user agent, and first and last subscription fetch | Each person's device list |

**Private disclosure:**

> Each person's devices are counted by an unreadable fingerprint. Meridian stores no device names, systems, or apps. Someone who takes the control server can only check whether a device they already have was used.

**Full disclosure:**

> Meridian stores each device's ID, system, model, and app, and when it last fetched the subscription. Someone who takes the control server learns which devices each person uses. Addresses are never stored.

The default cap is **3 devices**, adjustable from **1 to 100**. It applies when a person has no cap of their own. See [Client management](/en/client-management/).

With countable device limits enabled, `/info` and the connection page reveal only used and allowed slots (`user.deviceSlots.used` and `allowed`). Anyone with that personal link can see these counts, including a full or lowered cap. No device identifiers, names, systems, models, or device timestamps are included. The page keeps the count only in memory and shows nothing when limits are off. When slots are full, a new device receives a limit notice on subscription refresh; existing registrations stay allowed. Apps that do not report a supported device identifier are not counted.

## Usage history

| Level | What Meridian keeps |
| --- | --- |
| `off` (default) | Lifetime totals and last seen, without daily history |
| `daily` | Upload and download totals per person and total bytes per server, per UTC day |

Daily history keeps **90 days** by default. You can choose **7–730 days**, including today. Older rows are pruned every 30 seconds; shortening retention removes them immediately.

> Meridian keeps how much each person and each server used per day, for N days. Someone who takes the control server learns when each person was active.

Traffic belongs to the UTC day when new counter growth reaches the service. A delayed report belongs to the day it arrives. Retries count only once. Use from before history was enabled is not reconstructed; empty days mean no recorded traffic. Resetting a person's traffic allowance keeps their daily history.

The web panel shows a person's chart and a server chart with a server picker. The CLI shows compact tables:

```bash
meridian client usage alice --days 30
meridian client show alice                 # last 7 days when history is on
meridian fleet usage --days 7
```

Requests are capped at the saved retention. The usage APIs answer `409 history_off` while history is off.

## Change the settings

These choices belong to the control service. Applying the fleet file never changes them.

```bash
meridian privacy                          # same as privacy show
meridian privacy show --json
meridian privacy set --devices private --device-limit 3
meridian privacy set --history daily --history-days 90
meridian privacy set --devices off --history off --yes --json
```

Only options you pass change. Raising a level prints its disclosure before confirmation; `--yes` skips the question. JSON changes require `--yes`. Repeating the same settings changes nothing.

In the web panel, open **Settings → Privacy**. Each option shows its disclosure. Review the confirmation before saving; the owner needs a recent password check and their sign-in code if enabled. API reads need `read`; writes need `admin`.

Every setting change records who made it (the token's name, `owner`, or `root`), when, and the complete before and after values. The web panel and CLI show the newest 50 changes.

## Lowering a level

- `full` → `private` converts stored IDs to keyed fingerprints and deletes their systems, models, and apps.
- Devices → `off` deletes all device records.
- `daily` → `off` deletes all daily records for people and servers. Lifetime totals and last seen remain.
- Shortening retention deletes records outside the new cap.

The setting, deletion, and audit entry land together. The response reports the number of records removed or converted. Removing a person also removes their daily records; aggregate server history stays until retention removes it.

Fingerprints cannot be reversed into device IDs. Switching `private` → `full` clears private records; devices register again at their next subscription fetch.

## Snapshots and restore

Privacy settings and the change log ride encrypted snapshots. Device records and daily history ride them only while their corresponding level is on.

The fleet fingerprint key is a service secret and **never rides a snapshot**. After a restore, **private device records are cleared** and devices register again at their next subscription fetch. Full device records and enabled daily history survive a restore.

Lowering a level affects live state and future snapshots. Snapshots already saved still contain the records their saved level included; protect those copies with your backup keys. See [Recovery](/en/recovery/).
