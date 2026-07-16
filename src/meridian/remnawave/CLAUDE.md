# Remnawave adapter

## Design decisions

- `__init__.py` is the compatibility facade consumed by Meridian; resource behavior stays in `client.py`.
- `models.py` owns stable Meridian shapes and converts SDK/REST payloads immediately at the boundary.
- `runtime.py` owns the per-thread async bridge and translates transport/SDK failures into typed errors.
- `control_plane.py` uses explicit REST wire shapes for V4-managed resources; the SDK remains for proven legacy operations.

## What's done well

- SDK aliases are read through snake_case Python attributes and guarded by real DTO round-trip tests.
- The facade keeps panel details out of commands, provisioning, and reconciliation code.

## Pitfalls

- Patch `meridian.remnawave.client.sdk_call` in adapter tests; patching the facade alias does not replace the client binding.
- Never expose SDK DTOs or response dictionaries beyond this package.
- Omit unmanaged subscription settings and response rules from PATCH payloads; `null` can erase panel-owned policy.
- Keep component versions as one tested tuple; do not bump an image or SDK independently.
- Reality Hosts use `securityLayer=DEFAULT`; `REALITY` is an Inbound transport security value, not a Host enum.
