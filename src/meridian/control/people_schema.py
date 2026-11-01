"""One self-contained schema step for people paging, row versions, and receipts."""

PEOPLE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS people_versions ("
    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL UNIQUE "
    "REFERENCES users(user_id) ON DELETE CASCADE, version INTEGER NOT NULL DEFAULT 1)",
    "INSERT OR IGNORE INTO people_versions (user_id) SELECT user_id FROM users ORDER BY username",
    "CREATE TRIGGER IF NOT EXISTS people_insert AFTER INSERT ON users BEGIN "
    "INSERT INTO people_versions (user_id) VALUES (NEW.user_id); END",
    "CREATE TRIGGER IF NOT EXISTS people_update AFTER UPDATE ON users BEGIN "
    "UPDATE people_versions SET version = version + 1 WHERE user_id = NEW.user_id; END",
    "CREATE TRIGGER IF NOT EXISTS people_traffic_insert AFTER INSERT ON traffic_totals BEGIN "
    "UPDATE people_versions SET version = version + 1 WHERE user_id = NEW.user_id; END",
    "CREATE TRIGGER IF NOT EXISTS people_traffic_update AFTER UPDATE ON traffic_totals BEGIN "
    "UPDATE people_versions SET version = version + 1 WHERE user_id = NEW.user_id; END",
    "CREATE TRIGGER IF NOT EXISTS people_traffic_delete AFTER DELETE ON traffic_totals BEGIN "
    "UPDATE people_versions SET version = version + 1 WHERE user_id = OLD.user_id; END",
    "CREATE INDEX IF NOT EXISTS people_status ON users(status, username)",
    "CREATE INDEX IF NOT EXISTS people_tag ON users(tag, username)",
    "CREATE INDEX IF NOT EXISTS people_expiry ON users(expire_at, username)",
    "CREATE INDEX IF NOT EXISTS people_activation ON users(activation_duration_days, expire_at)",
    "CREATE INDEX IF NOT EXISTS people_activity ON traffic_totals(last_seen_at, user_id)",
    "CREATE TABLE IF NOT EXISTS bulk_receipts ("
    "id TEXT PRIMARY KEY, preview_hash TEXT NOT NULL, body TEXT NOT NULL, versions TEXT NOT NULL)",
)
