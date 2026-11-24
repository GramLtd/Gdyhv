"""SQLite-хранилище: черновики, шаблоны, отправленные посты, тексты всплывающих окон."""
import json
import secrets
import sqlite3
import time

from config import DB_PATH

_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS drafts (
                user_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                data TEXT NOT NULL,
                updated INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                message_id INTEGER NOT NULL,
                data TEXT NOT NULL,
                created INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payments (
                uid TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paid (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT, payer_id INTEGER, amount INTEGER, charge_id TEXT,
                status TEXT, created INTEGER
            );
            CREATE TABLE IF NOT EXISTS popups (
                uid TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                alert INTEGER NOT NULL
            );
            """
        )
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(sent)")}
        if "bot_id" not in cols:  # каким ботом отправлен пост — только он может его менять
            _conn.execute("ALTER TABLE sent ADD COLUMN bot_id INTEGER NOT NULL DEFAULT 0")
            _conn.commit()
    return _conn


# ---------- черновик ----------

def empty_draft() -> dict:
    return {
        "html": "",
        "media": None,          # {"type": "photo|video|animation|document", "file_id": "..."}
        "buttons": [],          # [[{uid,text,type,value,style,icon}, ...], ...]
        "no_preview": False,    # отключить превью ссылок
        "preview_above": False, # превью ссылки над текстом
        "edit_target": None,    # id записи из sent, если редактируем отправленный пост
        "template_id": None,    # id шаблона, из которого загружен черновик
        "timer": None,          # {"until": unix, "mode": "live|native", "done": "текст"}
    }


def get_draft(user_id: int) -> dict:
    row = conn().execute("SELECT data FROM drafts WHERE user_id=?", (user_id,)).fetchone()
    d = empty_draft()
    if row:
        d.update(json.loads(row["data"]))
    return d


def save_draft(user_id: int, d: dict) -> None:
    conn().execute(
        "INSERT INTO drafts(user_id, data) VALUES(?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
        (user_id, json.dumps(d, ensure_ascii=False)),
    )
    conn().commit()


def new_uid() -> str:
    return secrets.token_urlsafe(8)


# ---------- шаблоны ----------

def post_part(d: dict) -> dict:
    """Только содержимое поста (без служебных полей черновика)."""
    return {k: d.get(k) for k in ("html", "media", "buttons", "no_preview", "preview_above", "timer")}


def list_templates(user_id: int):
    return conn().execute(
        "SELECT id, name, updated FROM templates WHERE user_id=? ORDER BY updated DESC", (user_id,)
    ).fetchall()


def get_template(user_id: int, tid: int):
    row = conn().execute(
        "SELECT * FROM templates WHERE id=? AND user_id=?", (tid, user_id)
    ).fetchone()
    if not row:
        return None
    return {"id": row["id"], "name": row["name"], "data": json.loads(row["data"])}


def find_templates(user_id: int, query: str, limit: int = 50):
    q = f"%{query.strip()}%"
    rows = conn().execute(
        "SELECT * FROM templates WHERE user_id=? AND name LIKE ? ORDER BY updated DESC LIMIT ?",
        (user_id, q, limit),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "data": json.loads(r["data"])} for r in rows]


def create_template(user_id: int, name: str, d: dict) -> int:
    cur = conn().execute(
        "INSERT INTO templates(user_id, name, data, updated) VALUES(?,?,?,?)",
        (user_id, name, json.dumps(post_part(d), ensure_ascii=False), int(time.time())),
    )
    conn().commit()
    return cur.lastrowid


def update_template(user_id: int, tid: int, d: dict) -> None:
    conn().execute(
        "UPDATE templates SET data=?, updated=? WHERE id=? AND user_id=?",
        (json.dumps(post_part(d), ensure_ascii=False), int(time.time()), tid, user_id),
    )
    conn().commit()


def rename_template(user_id: int, tid: int, name: str) -> None:
    conn().execute("UPDATE templates SET name=? WHERE id=? AND user_id=?", (name, tid, user_id))
    conn().commit()


def delete_template(user_id: int, tid: int) -> None:
    conn().execute("DELETE FROM templates WHERE id=? AND user_id=?", (tid, user_id))
    conn().commit()


# ---------- отправленные ----------

def add_sent(user_id: int, chat_id: int, title: str, message_id: int, d: dict,
             bot_id: int = 0) -> int:
    cur = conn().execute(
        "INSERT INTO sent(user_id, chat_id, chat_title, message_id, data, created, bot_id) "
        "VALUES(?,?,?,?,?,?,?)",
        (user_id, chat_id, title, message_id, json.dumps(post_part(d), ensure_ascii=False),
         int(time.time()), bot_id),
    )
    conn().commit()
    return cur.lastrowid


def list_sent(user_id: int, limit: int = 15):
    return conn().execute(
        "SELECT * FROM sent WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
    ).fetchall()


def get_sent(user_id: int, sid: int):
    return conn().execute("SELECT * FROM sent WHERE id=? AND user_id=?", (sid, user_id)).fetchone()


def update_sent(user_id: int, sid: int, d: dict) -> None:
    conn().execute(
        "UPDATE sent SET data=? WHERE id=? AND user_id=?",
        (json.dumps(post_part(d), ensure_ascii=False), sid, user_id),
    )
    conn().commit()


def delete_sent(user_id: int, sid: int) -> None:
    conn().execute("DELETE FROM sent WHERE id=? AND user_id=?", (sid, user_id))
    conn().commit()


# ---------- всплывающие окна ----------

def put_popup(uid: str, text: str, alert: bool) -> None:
    conn().execute(
        "INSERT INTO popups(uid, text, alert) VALUES(?,?,?) "
        "ON CONFLICT(uid) DO UPDATE SET text=excluded.text, alert=excluded.alert",
        (uid, text, int(alert)),
    )
    conn().commit()


def put_page_vals(uid: str, vals: dict) -> None:
    """Значения переменных поста — чтобы следующий экран цепочки «сменить пост» их унаследовал."""
    conn().execute("CREATE TABLE IF NOT EXISTS page_vals (uid TEXT PRIMARY KEY, data TEXT NOT NULL)")
    conn().execute("INSERT OR REPLACE INTO page_vals(uid, data) VALUES(?,?)",
                   (uid, json.dumps(vals, ensure_ascii=False)))
    conn().commit()


def get_page_vals(uid: str) -> dict:
    conn().execute("CREATE TABLE IF NOT EXISTS page_vals (uid TEXT PRIMARY KEY, data TEXT NOT NULL)")
    row = conn().execute("SELECT data FROM page_vals WHERE uid=?", (uid,)).fetchone()
    return json.loads(row["data"]) if row else {}


def get_popup(uid: str):
    return conn().execute("SELECT text, alert FROM popups WHERE uid=?", (uid,)).fetchone()


# ---- платные кнопки (звёзды / подарки) ----

def put_payment(uid: str, owner_id: int, data: dict) -> None:
    conn().execute(
        "INSERT INTO payments(uid, owner_id, data) VALUES(?,?,?) "
        "ON CONFLICT(uid) DO UPDATE SET data=excluded.data",
        (uid, owner_id, json.dumps(data, ensure_ascii=False)))
    conn().commit()


def get_payment(uid: str):
    row = conn().execute("SELECT owner_id, data FROM payments WHERE uid=?", (uid,)).fetchone()
    if not row:
        return None
    return {"owner_id": row["owner_id"], **json.loads(row["data"])}


def log_paid(uid: str, payer_id: int, amount: int, charge_id: str, status: str) -> None:
    conn().execute("INSERT INTO paid(uid, payer_id, amount, charge_id, status, created) "
                   "VALUES(?,?,?,?,?,?)", (uid, payer_id, amount, charge_id, status, int(time.time())))
    conn().commit()


def get_template_any(tid: int):
    """Шаблон по id без проверки владельца — для кнопок «сменить пост»."""
    row = conn().execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "name": row["name"], "data": json.loads(row["data"])}


def update_sent_by_msg(chat_id: int, message_id: int, d: dict) -> None:
    conn().execute("UPDATE sent SET data=? WHERE chat_id=? AND message_id=?",
                   (json.dumps(post_part(d), ensure_ascii=False), chat_id, message_id))
    conn().commit()


def sent_with_live_timer():
    rows = conn().execute("SELECT * FROM sent WHERE data LIKE '%\"mode\": \"live\"%'").fetchall()
    out = []
    for r in rows:
        d = json.loads(r["data"])
        t = d.get("timer")
        if t and t.get("mode") == "live" and not t.get("finished") and "{timer}" in (d.get("html") or ""):
            out.append((r, d))
    return out


def set_sent_data(sid: int, d: dict) -> None:
    conn().execute("UPDATE sent SET data=? WHERE id=?", (json.dumps(post_part(d), ensure_ascii=False), sid))
    conn().commit()


# ---- платный доступ ----

def _init_access():
    conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS subs (user_id INTEGER PRIMARY KEY, until INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS shop (
            tid INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            descr TEXT NOT NULL DEFAULT '',
            access TEXT NOT NULL DEFAULT 'free',   -- free | sub | price
            price INTEGER NOT NULL DEFAULT 0,
            prompts TEXT NOT NULL DEFAULT '{}',
            published INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS purchases (
            user_id INTEGER NOT NULL, tid INTEGER NOT NULL, created INTEGER,
            PRIMARY KEY (user_id, tid)
        );
        CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, seen INTEGER);
        """)


def get_setting(key: str, default: str = "") -> str:
    _init_access()
    row = conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value) -> None:
    _init_access()
    conn().execute("INSERT INTO settings(key, value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn().commit()


def sub_until(uid: int) -> int:
    _init_access()
    row = conn().execute("SELECT until FROM subs WHERE user_id=?", (uid,)).fetchone()
    return row["until"] if row else 0


def extend_sub(uid: int, days: int) -> int:
    start = max(sub_until(uid), int(time.time()))
    until = start + days * 86400
    conn().execute("INSERT INTO subs(user_id, until) VALUES(?,?) "
                   "ON CONFLICT(user_id) DO UPDATE SET until=excluded.until", (uid, until))
    conn().commit()
    return until


def revoke_sub(uid: int) -> None:
    _init_access()
    conn().execute("DELETE FROM subs WHERE user_id=?", (uid,))
    conn().commit()


def shop_get(tid: int):
    _init_access()
    row = conn().execute("SELECT * FROM shop WHERE tid=?", (tid,)).fetchone()
    if not row:
        return None
    r = dict(row)
    r["prompts"] = json.loads(r["prompts"])
    return r


def shop_save(r: dict) -> None:
    _init_access()
    conn().execute(
        "INSERT INTO shop(tid, title, descr, access, price, prompts, published) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(tid) DO UPDATE SET title=excluded.title, descr=excluded.descr, "
        "access=excluded.access, price=excluded.price, prompts=excluded.prompts, "
        "published=excluded.published",
        (r["tid"], r["title"], r.get("descr", ""), r.get("access", "free"), int(r.get("price", 0)),
         json.dumps(r.get("prompts", {}), ensure_ascii=False), int(r.get("published", 0))))
    conn().commit()


def shop_list(published_only: bool = True):
    _init_access()
    q = "SELECT s.* FROM shop s JOIN templates t ON t.id = s.tid"
    if published_only:
        q += " WHERE s.published=1"
    out = []
    for row in conn().execute(q + " ORDER BY s.tid DESC").fetchall():
        r = dict(row)
        r["prompts"] = json.loads(r["prompts"])
        out.append(r)
    return out


def has_purchase(uid: int, tid: int) -> bool:
    _init_access()
    return conn().execute("SELECT 1 FROM purchases WHERE user_id=? AND tid=?",
                          (uid, tid)).fetchone() is not None


def add_purchase(uid: int, tid: int) -> None:
    _init_access()
    conn().execute("INSERT OR IGNORE INTO purchases(user_id, tid, created) VALUES(?,?,?)",
                   (uid, tid, int(time.time())))
    conn().commit()


def _users_username_col():
    cols = {r[1] for r in conn().execute("PRAGMA table_info(users)")}
    if "username" not in cols:
        conn().execute("ALTER TABLE users ADD COLUMN username TEXT")
        conn().commit()


def touch_user(uid: int, name: str, username: str | None = None) -> None:
    _init_access()
    _users_username_col()
    conn().execute("INSERT INTO users(user_id, name, seen, username) VALUES(?,?,?,?) ON CONFLICT(user_id) "
                   "DO UPDATE SET name=excluded.name, seen=excluded.seen, "
                   "username=COALESCE(excluded.username, users.username)",
                   (uid, name, int(time.time()), (username or "").lower() or None))
    conn().commit()


def user_by_username(username: str):
    _init_access()
    _users_username_col()
    return conn().execute("SELECT * FROM users WHERE username=?",
                          (username.lstrip("@").lower(),)).fetchone()


def template_users(tid: int) -> list:
    """Кому открыт шаблон (купили или выданы админом)."""
    _init_access()
    _users_username_col()
    return conn().execute(
        "SELECT p.user_id, u.name, u.username FROM purchases p LEFT JOIN users u ON u.user_id=p.user_id "
        "WHERE p.tid=? ORDER BY p.created DESC", (tid,)).fetchall()


def remove_purchase(uid: int, tid: int) -> None:
    _init_access()
    conn().execute("DELETE FROM purchases WHERE user_id=? AND tid=?", (uid, tid))
    conn().commit()


def stats() -> dict:
    _init_access()
    now = int(time.time())
    c = conn()
    return {
        "users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "subs": c.execute("SELECT COUNT(*) FROM subs WHERE until>?", (now,)).fetchone()[0],
        "purchases": c.execute("SELECT COUNT(*) FROM purchases").fetchone()[0],
        "stars": c.execute("SELECT COALESCE(SUM(amount),0) FROM paid WHERE status='ok'").fetchone()[0],
    }
