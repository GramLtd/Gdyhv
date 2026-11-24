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
            CREATE TABLE IF NOT EXISTS popups (
                uid TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                alert INTEGER NOT NULL
            );
            """
        )
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
    return {k: d.get(k) for k in ("html", "media", "buttons", "no_preview", "preview_above")}


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

def add_sent(user_id: int, chat_id: int, title: str, message_id: int, d: dict) -> int:
    cur = conn().execute(
        "INSERT INTO sent(user_id, chat_id, chat_title, message_id, data, created) VALUES(?,?,?,?,?,?)",
        (user_id, chat_id, title, message_id, json.dumps(post_part(d), ensure_ascii=False),
         int(time.time())),
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


def get_popup(uid: str):
    return conn().execute("SELECT text, alert FROM popups WHERE uid=?", (uid,)).fetchone()
