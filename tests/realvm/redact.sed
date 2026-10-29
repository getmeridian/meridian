# Mask lab secrets and addresses before output leaves the terminal (paste, logs, issues):
#   uv run meridian test 2>&1 | sed -E -f tests/realvm/redact.sed
# BSD sed has no \b or lookaround, so patterns anchor on their own shape; boundary rules run
# twice where one match's trailing boundary can be the next one's leading one.

# Secrets first, before broader rules cut them apart.
s/mrd_[0-9a-f]{12}_[0-9a-f]{64}/<api-token>/g
s/AGE-SECRET-KEY-1[0-9A-Z]+/<age-secret>/g
s/age1[02-9ac-hj-np-z]{58}/<age-public>/g
s#/sub/[A-Za-z0-9_-]+#/sub/<token>#g
# Hysteria2's salamander password rides in share links (twice), Xray JSON, and Mihomo YAML.
s/([?&](obfs-password|fm)=)[^&#"' ]+/\1<p>/g
s/("password": *")[^"]+/\1<p>/g
s/(obfs-password: *)[^ ]+/\1<p>/g
s/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/<uuid>/g
# Panel and page secret paths are 16-32 hex characters in a URL path.
s#/[0-9a-f]{16,32}(/|$|[?#"' ])#/<path>\1#g

# File names keep their dots: hide them from the hostname rules, restored at the end.
s/\.(conf|bak|new|tmp|log|json|ya?ml|service|socket|timer|tar|gz|tgz|zip|deb|sh|py|txt|md|d)([^A-Za-z0-9_-]|$)/@DOT@\1\2/g
s/\.(conf|bak|new|tmp|log|json|ya?ml|service|socket|timer|tar|gz|tgz|zip|deb|sh|py|txt|md|d)([^A-Za-z0-9_-]|$)/@DOT@\1\2/g

# Hostnames before addresses, so a dashed-IP name (198-51-100-7.sslip.io) goes whole. A trailing
# dot ends a sentence or an FQDN; a letter after one (x.conf) means it was no hostname.
s/([A-Za-z0-9-]+\.)+(com|net|org|io|ru|de|info|app|dev|me|co|uk|fi|nl|fr|xyz|cloud)([^A-Za-z0-9_-]|$)/<host>\3/g
# Any name with two or more dots (scanned camouflage sites use every TLD); dotted Python paths go too.
s/([A-Za-z0-9-]+\.){2,}([A-Za-z]{2,}|xn--[a-z0-9-]+)([^A-Za-z0-9_-]|$)/<host>\3/g

s/(^|[^0-9])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9]|$)/\1<ip>\3/g
s/(^|[^0-9])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9]|$)/\1<ip>\3/g
s/(^|[^0-9])([0-9]{1,3}-){3}[0-9]{1,3}([^0-9]|$)/\1<ip>\3/g
s/(^|[^0-9])([0-9]{1,3}-){3}[0-9]{1,3}([^0-9]|$)/\1<ip>\3/g
# Global (2000::/3) and link-local IPv6; timestamps (12:30:45) never start with four hex digits.
s/(^|[^0-9a-fA-F:])([23][0-9a-fA-F]{3}|fe80):[0-9a-fA-F:]+/\1<ip6>/g

s/@DOT@/./g
