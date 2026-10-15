# Remnawave adapter

## Design decisions

- `__init__.py` is the compatibility facade consumed by Meridian; resource behavior stays in `client.py` and `control_plane.py`.
- Raw REST over one `httpx.Client`, no SDK: the official SDK stalled at 2.8 and Meridian needs only a few dozen endpoints. `_request` owns retry, typed errors, and envelope unwrapping.
- `models.py` owns stable Meridian shapes and converts REST payloads immediately at the boundary.
- `runtime.py` holds only the typed error hierarchy; `versions.py` owns the supported floor, tested major, never-downgrade image resolution, and the upgradable pins; `registry.py` reads Docker Hub tags and multi-arch index digests on the deployer's machine.
- **Version policy** — `config.py` pins backend, node, and subscription-page images as `tag@sha256:<index digest>` (move them together, refresh with `scripts/pin_image_digests.py`, update the CHANGELOG matrix). Pins replace older or same-version tags and keep newer or floating operator tags. `fleet upgrade` saves newer same-major releases in `ClusterConfig.image_pins`, and `effective_pins()` feeds the newer pin into the reviewed `runtime_pins`. Panels below `REMNAWAVE_MIN_VERSION` are refused; an untested newer major warns.

## What's done well

- Wire shapes are pinned by tests against the 3.4.5 fixture and the upstream `remnawave/backend` contract (`libs/contract`).
- The facade keeps panel details out of commands, provisioning, and reconciliation code.

## Pitfalls
- A saved `image_pins` entry is trusted only for an upgradable component, the same repository and major, an X.Y.Z tag, and a sha256 digest; anything else in `cluster.yml` falls back to the CLI pin.
- PostgreSQL and Valkey go through `resolve_data_image`: an older major cannot open a newer data directory, so a newer or unorderable operator tag is kept. PostgreSQL also passes `cross_major=False`: a newer pinned major needs a dump and restore, so the running major stays and `fleet upgrade` reports it as `held_major`.
- Users are addressed by numeric `id` (string in `User.id`); uuid-era bindings re-resolve by username, never hit `/api/users/{uuid}`.
- 3.x answers DELETE and bulk host actions with 204 and node restart with 202: `_request` returns None; treat 2xx as success and 404 as already gone, never read `isDeleted`.
- Revoke (`actions/revoke`, `revokeOnlyPasswords: false`) replaces the short UUID, VLESS UUID (also the Hysteria2 password), and passwords but keeps settings and HWID devices; the panel pushes an active user's new keys to connected nodes (`prevVlessUuid`), so nothing restarts.
- Remnawave's Zod pipe silently strips unknown body keys — a misspelled or removed field is a no-op, not an error. Check the contract before adding fields.
- The subscription title lives in `customResponseHeaders["profile-title"]` as `rwEncodeBase64:<plain text>` (the panel encodes) beside `profile-update-interval` (whole hours, `SUBSCRIPTION_UPDATE_INTERVAL_HOURS`); the record is replaced wholesale, so read-merge-write it.
- Reality keypairs come from `GET /api/system/tools/x25519/generate` (unpadded base64url, 30 per call); exits need no Docker, Xray, or unzip before their Profile exists, and `reality_keys.py` rejects anything that is not 32 bytes.
- External squad headers are `responseHeadersAdd`/`responseHeadersRemove`; node Xray version is `versions.xray`; keygen returns `secretKey`.
- Omit unmanaged subscription settings and response rules from PATCH payloads; `null` can erase panel-owned policy.
- Apps get Xray JSON (and its failover) only through `serveJsonAtBaseSubscription`, which upgrades the base64 fallback for upstream's list of JSON-capable User-Agents; the lab's `/json` fetch never exercised app UAs, so Happ silently got base64. Don't hand-write UA rules.
- Custom headers pass through Remnawave's per-user template engine (`{{SHORT_UUID}}`, …); `support-url` is seeded as `https://dummy.docs.rw`, so Meridian replaces it (and only it or its own link) with `PanelConfig.support_url()`.
- The public Xray JSON selector is `json` and returns an array of generated configs; `xray-json` is a template name and returns 403 as an unknown client type.
- Reality Hosts use `securityLayer=DEFAULT`; `REALITY` is an Inbound transport security value, not a Host enum.
- Hysteria2 is `hysteria2` in Meridian keys and URLs but protocol/network `hysteria` plus version `2` in Remnawave profiles.
- API token creation requires `name` and `expiresInDays` (the provisioner token uses wildcard scope); tokens are signed with `APP_SECRET`, which upgrades carry over from 2.x `JWT_AUTH_SECRET`.
- Keep certificate verification enabled for credential-bearing requests; explicit overrides are for caller-supplied trust contexts and isolated tests.
- Image references may be `repo:tag@sha256:...`: compare versions by tag via `parse_image()` and treat an equal digest as the same image in any spelling; never string-compare or `rfind(':')` a reference.
