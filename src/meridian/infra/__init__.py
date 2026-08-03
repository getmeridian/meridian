"""Cloud infrastructure providers for VM lifecycle management.

Used by:
- `tests/realvm/` harness — provisions real VMs for integration testing
- (future) `meridian deploy --create-vm <provider>` — user-facing VM creation

Each provider is an optional dependency: `pip install meridian-vpn[hetzner]`
pulls only the Hetzner SDK; `[all-providers]` pulls everything.
"""
