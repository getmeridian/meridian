# remnawave — What `meridian migrate` reads from a Remnawave panel

## Design decisions
- **Only migrate's reader remains** — `__init__.py` is the compatibility facade: `RemnawaveReader` (`client.py`, users only), the `User`/`Node`/`Host` shapes the control service's access adapters still return (`models.py`), the typed error hierarchy (`runtime.py`), and `XRAY_JSON_CLIENT_TYPE`.
- **Raw REST over one `httpx.Client`, no SDK** — the official SDK stalled at 2.8, and Meridian needs only a few endpoints; `_request` owns retry, typed errors, and envelope unwrapping.
- **Convert at the boundary** — `models.py` owns stable Meridian shapes and converts REST payloads immediately (`parse_user()`).
- **`User` serves both sides** — it reads lifetime/reset and panel-only metadata for migrate, and its HWID cap field also carries the control service's nullable device cap (panel caps and registrations are reported, never imported). `User.language` carries only Meridian's nullable subscription-language preference, and the control service's optional first-traffic time and last reporting node ride it too; legacy panel reads leave all three unknown.

## What's done well
- Wire shapes are pinned by tests against the 3.4.5 fixture and the upstream `remnawave/backend` contract (`libs/contract`).
- The facade keeps panel details out of commands, provisioning, and reconciliation code.

## Pitfalls
- **Users are addressed by numeric `id`** (a string in `User.id`), never by UUID.
- **3.x answers some calls with an empty 2xx** — 204 for DELETE and bulk host actions, 202 for a node restart; `_request` returns None for them.
- **Remnawave's "never" is a 2099 date** — every panel user needs an `expireAt`, so migrate reads a year from `NEVER_EXPIRES_YEAR` (2099) on as no expiry; `MONTH_ROLLING` is panel-only and reported, not imported.
- **The public Xray JSON selector is `json`** (`XRAY_JSON_CLIENT_TYPE`) and returns an array of generated configs; `xray-json` is a template name in Remnawave, which answered 403 to it as an unknown client type.
- **Reality Hosts use `securityLayer=DEFAULT`** — `REALITY` is an Inbound transport security value, not a Host enum.
- **Keep certificate verification enabled for credential-bearing requests** — explicit overrides are for caller-supplied trust contexts and isolated tests.
