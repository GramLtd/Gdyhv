"""Пополнение баланса в TON: игрок переводит TON на кошелёк проекта с комментарием XMR<id>.
Фоновая задача опрашивает toncenter и зачисляет XMR по курсу из админки."""
import asyncio
import logging
import re

import aiohttp

from . import db, settings
from .config import TON_WALLET, TONCENTER_API_KEY, TONCENTER_URL

log = logging.getLogger("xmrmine.ton")
COMMENT_RE = re.compile(r"^\s*XMR(\d{3,15})\s*$", re.I)
NANO = 1_000_000_000


def comment_for(uid: int) -> str:
    return f"XMR{uid}"


def transfer_link(uid: int, ton: float | None = None) -> str:
    url = f"ton://transfer/{TON_WALLET}?text={comment_for(uid)}"
    if ton:
        url += f"&amount={int(ton * NANO)}"
    return url


async def _fetch(session) -> list[dict]:
    params = {"address": TON_WALLET, "limit": 50, "archival": "true"}
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}
    async with session.get(f"{TONCENTER_URL}/getTransactions", params=params, headers=headers,
                           timeout=aiohttp.ClientTimeout(total=20)) as r:
        data = await r.json(content_type=None)
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "toncenter error"))
    return data["result"]


async def process(txs: list[dict], notify) -> int:
    credited = 0
    for tx in txs:
        msg = tx.get("in_msg") or {}
        if not msg.get("source") or tx.get("out_msgs"):  # исходящие / bounce
            continue
        tx_hash = tx["transaction_id"]["hash"]
        nano = int(msg.get("value") or 0)
        m = COMMENT_RE.match(msg.get("message") or "")
        uid = int(m.group(1)) if m else None
        if uid and not db.get(uid):
            uid = None
        ton = nano / NANO
        ok = ton >= settings.min_ton_deposit()
        xmr = round(ton * settings.xmr_per_ton(), 8) if (uid and ok) else 0.0
        if not db.add_ton_deposit(tx_hash, uid, msg["source"], ton, xmr):
            continue  # уже обработан
        if xmr > 0:
            db.add_balance(uid, xmr)
            credited += 1
            await notify(uid, f"💎 Пополнение: {ton:g} TON → +{xmr:g} {settings.coin()}")
        elif uid:
            await notify(uid, f"⚠️ Получено {ton:g} TON — меньше минимума "
                              f"{settings.min_ton_deposit():g} TON. Напишите в поддержку.")
    return credited


async def watcher(bot, interval: int = 20):
    if not TON_WALLET:
        log.warning("TON_WALLET не задан — пополнение в TON отключено")
        return

    async def notify(uid, text):
        try:
            await bot.send_message(uid, text)
        except Exception:
            pass

    async with aiohttp.ClientSession() as s:
        while True:
            try:
                await process(await _fetch(s), notify)
            except Exception as e:
                log.warning("TON poll failed: %s", e)
            await asyncio.sleep(interval)
