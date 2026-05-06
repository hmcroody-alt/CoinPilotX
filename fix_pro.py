import sqlite3

USER_ID = 6067278162

conn = sqlite3.connect("coinpilotx.db")
cur = conn.cursor()

# Add column if missing
try:
    cur.execute("ALTER TABLE users ADD COLUMN is_pro INTEGER DEFAULT 0")
except Exception:
    pass

# Upgrade user
cur.execute("UPDATE users SET is_pro=1 WHERE user_id=?", (USER_ID,))
conn.commit()

# Verify
cur.execute("SELECT user_id, display_name, is_pro FROM users WHERE user_id=?", (USER_ID,))
result = cur.fetchone()

print("RESULT:", result)

conn.close()
