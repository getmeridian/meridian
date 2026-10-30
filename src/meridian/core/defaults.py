"""Core defaults that must not depend on CLI-layer ``meridian.config``."""

from __future__ import annotations

from typing import Literal

# Default Reality camouflage target.  Duplicated from ``meridian.config``
# so that the core contract layer stays free of CLI-specific state such as
# paths, image tags, and environment-variable readers.
DEFAULT_SNI: str = "www.google.com"

# Reality camouflage targets tried in order when the operator gives no --sni.
# Each must carry Reality traffic end to end; www.microsoft.com is absent
# because Reality handshakes through it failed in the system lab while these
# succeeded with identical keys and config.
REALITY_SNI_CANDIDATES: tuple[str, ...] = ("www.google.com", "dl.google.com", "www.cloudflare.com")

# The control server's panel TLS server behind the 443 stream router. The second
# listener accepts the PROXY protocol header when the fleet forwards client IPs.
CONTROL_TLS_PORT: int = 8443
CONTROL_PROXY_TLS_PORT: int = 8444

# The shared support page (templates/pwa/support.html), under the connection-page path;
# apps open it as the `support-url` header with the subscription's short ID as fragment.
SUPPORT_PAGE: str = "sub/~pwa/support.html"

# Hours between subscription refreshes (Happ and the Clash family read whole hours).
SUBSCRIPTION_UPDATE_INTERVAL_HOURS: int = 6

# Client routing stays compatible with subscriptions delivered before policy selection.
DEFAULT_CLIENT_ROUTING: Literal["ru-split"] = "ru-split"

# Device/server clock drift worth a warning. Nothing Meridian deploys checks the
# clock to the second: Reality's `maxTimeDiff` is left unset (Xray then skips the
# client timestamp check), and VLESS and Hysteria2 carry no timestamps. A wrong
# clock breaks certificate validation instead (Let's Encrypt backdates
# `notBefore` by an hour; IP certificates live six days), so the failures start
# at hours, not seconds. A synced clock sits within a second or two, so more
# than a minute means automatic time is off; warn before the drift grows.
# Mirrored by `CLOCK_WARN_MS` in the PWA's `selfcheck.js` and `app.js`.
CLOCK_DRIFT_WARN_SECONDS: int = 60
