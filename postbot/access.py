"""Кто что может: владелец (ADMIN_IDS), подписчик редактора, обычный пользователь."""
import time

import db
from config import ADMIN_IDS


def is_admin(uid: int) -> bool:
    return not ADMIN_IDS or uid in ADMIN_IDS


def paywall_on() -> bool:
    """Подписка на редактор убрана: обычные пользователи только покупают доступ к шаблонам."""
    return False


def sub_active(uid: int) -> bool:
    return db.sub_until(uid) > time.time()


def has_editor(uid: int) -> bool:
    """Редактор и создание постов — только админам."""
    return is_admin(uid)


def can_use(uid: int, tid: int) -> bool:
    """Доступ к опубликованному шаблону."""
    r = db.shop_get(tid)
    if not r:
        return False
    if is_admin(uid):
        return True
    if not r["published"]:
        return False
    if r["access"] == "free":
        return True
    if r["access"] == "sub":
        return False  # устаревший тип доступа: открыт только админу
    return db.has_purchase(uid, tid) or (r["access"] == "price" and r["price"] == 0)


def templates_for(uid: int) -> list[dict]:
    """Шаблоны для inline: свои (если есть редактор) + доступные опубликованные."""
    out, seen = [], set()
    if has_editor(uid):
        for t in db.find_templates(uid, "", 200):
            out.append(t)
            seen.add(t["id"])
    for r in db.shop_list():
        if r["tid"] not in seen and can_use(uid, r["tid"]):
            t = db.get_template_any(r["tid"])
            if t:
                t["name"] = r["title"]
                out.append(t)
    return out


def get_for(uid: int, tid: int):
    t = db.get_template(uid, tid) if has_editor(uid) else None
    if t:
        return t
    if can_use(uid, tid):
        t = db.get_template_any(tid)
        r = db.shop_get(tid)
        if t and r:
            t["name"] = r["title"]
        return t
    return None
