# Mask lab secrets and addresses before output leaves the terminal (paste, logs, issues):
#   uv run meridian test 2>&1 | sed -E -f tests/realvm/redact.sed
# BSD sed has no \b, so patterns anchor on their own shape.
s/([0-9]{1,3}\.){3}[0-9]{1,3}/<ip>/g
s/([0-9]{1,3}-){3}[0-9]{1,3}/<ip>/g
s#/sub/[A-Za-z0-9_-]+#/sub/<token>#g
s/obfs-password=[^&]+/obfs-password=<p>/g
s/AGE-SECRET-KEY-1[0-9A-Z]+/<age-secret>/g
s/age1[02-9ac-hj-np-z]{58}/<age-public>/g
s/([a-z0-9-]+\.)+(com|net|org|io|ru|de|info|app|dev|me|co)([^a-z0-9.-]|$)/<host>\3/g
s/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/<uuid>/g
# Any name with two or more dots (scanned camouflage sites use every TLD); dotted Python paths go too.
s/([a-z0-9-]+\.){2,}[a-z]{2,}([^a-z0-9.-]|$)/<host>\2/g
