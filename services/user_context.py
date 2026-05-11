from . import db as db_service


def connect():
    return db_service.connect()


def row_to_dict(row):
    return dict(row) if row else None


def get_user_by_telegram(telegram_user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_user_id=? LIMIT 1", (telegram_user_id,))
    row = row_to_dict(cur.fetchone())
    conn.close()
    return row


def get_user_by_id(user_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=? LIMIT 1", (user_id,))
    row = row_to_dict(cur.fetchone())
    conn.close()
    return row


def mask_email(email):
    if not email or "@" not in email:
        return "Not set"
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        return name[:1] + "***@" + domain
    return name[:2] + "***@" + domain


def log_interaction(user_id, feature, prompt="", response="", metadata=""):
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_ai_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                feature TEXT,
                prompt TEXT,
                response TEXT,
                metadata TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            INSERT INTO user_ai_interactions (user_id, feature, prompt, response, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (user_id or 0, feature, prompt[:4000], response[:4000], metadata[:4000]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
