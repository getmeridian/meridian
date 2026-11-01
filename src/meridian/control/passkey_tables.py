"""Local owner passkey storage; neither table travels in a snapshot."""

PASSKEY_TABLES = (
    """CREATE TABLE IF NOT EXISTS ui_passkey_owner (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        user_handle BLOB NOT NULL CHECK (length(user_handle) = 32)
    )""",
    """CREATE TABLE IF NOT EXISTS ui_passkeys (
        credential_id TEXT PRIMARY KEY,
        public_key BLOB NOT NULL,
        algorithm INTEGER NOT NULL CHECK (algorithm IN (-7, -8)),
        sign_count INTEGER NOT NULL CHECK (sign_count BETWEEN 0 AND 4294967295),
        rp_id TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        last_used_at INTEGER
    )""",
)
