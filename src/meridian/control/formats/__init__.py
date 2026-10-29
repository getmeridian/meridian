"""Subscription formats the control service renders, one module per format.

`selector` picks a format from the request (User-Agent, Accept, or an explicit
`/sub/<token>/<selector>` suffix); `entries` holds what every format shares: view order,
fingerprint copies, and the placeholder entries a suspended user sees instead.
"""
