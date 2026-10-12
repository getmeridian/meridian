# Remnawave adapter

## Design decisions

- `__init__.py` is the compatibility facade consumed by Meridian; resource behavior stays in `client.py` and `control_plane.py`.
- Raw REST over one `httpx.Client`, no SDK: the official SDK stalled at 2.8 and Meridian needs only a few dozen endpoints. `_request` owns retry, typed errors, and envelope unwrapping.
- `models.py` owns stable Meridian shapes and converts REST payloads immediately at the boundary.
- `runtime.py` holds only the typed error hierarchy; `versions.py` owns the supported floor, tested major, and never-downgrade image resolution.

## What's done well

- Wire shapes are pinned by tests against the 3.4.5 fixture and the upstream `remnawave/backend` contract (`libs/contract`).
- The facade keeps panel details out of commands, provisioning, and reconciliation code.

## Pitfalls

- Users are addressed by numeric `id` (string in `User.id`); uuid-era bindings re-resolve by username, never hit `/api/users/{uuid}`.
- 3.x answers DELETE and bulk host actions with 204 and node restart with 202: `_request` returns None; treat 2xx as success and 404 as already gone, never read `isDeleted`.
- Remnawave's Zod pipe silently strips unknown body keys — a misspelled or removed field is a no-op, not an error. Check the contract before adding fields.
- The subscription title lives in `customResponseHeaders["profile-title"]` as `rwEncodeBase64:<plain text>` (the panel encodes); the record is replaced wholesale, so read-merge-write it.
- External squad headers are `responseHeadersAdd`/`responseHeadersRemove`; node Xray version is `versions.xray`; keygen returns `secretKey`.
- Omit unmanaged subscription settings and response rules from PATCH payloads; `null` can erase panel-owned policy.
- The public Xray JSON selector is `json` and returns an array of generated configs; `xray-json` is a template name and returns 403 as an unknown client type.
- Reality Hosts use `securityLayer=DEFAULT`; `REALITY` is an Inbound transport security value, not a Host enum.
- Keep certificate verification enabled for credential-bearing requests; explicit overrides are for caller-supplied trust contexts and isolated tests.
