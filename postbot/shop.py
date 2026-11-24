"""Платный доступ: подписка на редактор, доступ к шаблонам, меню под конкретный шаблон, админка."""
import html
import json
import re
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup,
                           LabeledPrice, Message, SwitchInlineQueryChosenChat)

import access
import db
from config import ADMIN_IDS
from render import TZ, VAR_RE, apply_vars, me_vals, link_vars, send_post, start_timer

router = Router()


class U(StatesGroup):
    fill = State()
    target = State()


class A(StatesGroup):
    pay = State()
    grant = State()
    title = State()
    descr = State()
    price = State()
    prompt = State()
    mtext = State()
    tpl_user = State()


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def cb(text: str, data: str) -> B:
    return B(text=text, callback_data=data)


def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M")


def var_keys(d: dict) -> list[str]:
    """Что спросить у пользователя: ссылку (одна на все {link…}) и {1}..{9} по порядку."""
    blob = json.dumps([d.get("html"), d.get("buttons")], ensure_ascii=False)
    from render import LOOSE_VAR_RE, clean_var
    found = set(VAR_RE.findall(blob)) | {
        k for k in (clean_var(x) for x in LOOSE_VAR_RE.findall(blob))
        if re.fullmatch(r"link|link_text|link_name|link_num|link_slug|[1-9]", k)}
    if re.search(r"https?://(?:www\.)?link(?:\.[a-z]{2,6})?/?[\"<]", blob):
        found.add("link")
    keys = ["link"] if any(k.startswith("link") for k in found) else []
    keys += sorted(k for k in found if k.isdigit())
    return keys


# Тексты меню для обычных пользователей: редактируются в админке («📝 Тексты меню»)
MENU_TEXTS = {
    "welcome": ("Приветствие", "👋 <b>Добро пожаловать!</b>"),
    "hint": ("Подсказка над списком шаблонов", "🛍 Выбери шаблон — бот сам соберёт по нему готовый пост."),
    "empty": ("Если шаблонов нет", "Пока здесь пусто."),
    "locked": ("Под закрытым шаблоном", "🔒 Доступ навсегда — {price} ⭐"),
    "buy_btn": ("Кнопка покупки", "💰 Купить за {price} ⭐"),
    "bought": ("После покупки", "✅ Шаблон «{title}» твой навсегда."),
    "redirect": ("Основной бот → магазин", "🛍 Шаблоны постов и доступ к ним — в нашем боте-магазине."),
}


def mtext(key: str, **kw) -> str:
    v = db.get_setting("mt_" + key, "") or MENU_TEXTS[key][1]
    try:
        return v.format(**kw)
    except (KeyError, IndexError, ValueError):
        return v


def default_prompt(k: str) -> str:
    return "🔗 Пришли ссылку" if k == "link" else f"✏️ Значение {{{k}}}"


def access_label(r: dict) -> str:
    return {"free": "🆓 Бесплатно", "sub": "🔒 Только админ",
            "price": f"💰 {r['price']} ⭐ навсегда"}.get(r["access"], "?")


# ===================== покупка =====================

async def send_invoice(bot: Bot, chat_id: int, kind: str, amount: int, title: str,
                       descr: str, **extra):
    pid = db.new_uid()
    owner = next(iter(ADMIN_IDS), 0)
    db.put_payment(pid, owner, {"kind": kind, "amount": amount, **extra})
    await bot.send_invoice(chat_id=chat_id, title=title[:32], description=descr[:255],
                           payload=f"pay:{pid}", currency="XTR",
                           prices=[LabeledPrice(label=title[:32], amount=amount)])


async def on_paid(m: Message, p: dict, who: str):
    from handlers import notify
    uid = m.from_user.id
    if p["kind"] == "sub":
        until = db.extend_sub(uid, p["days"])
        await m.answer(f"✅ Доступ к редактору открыт до <b>{fmt_date(until)}</b>.\n"
                       "Жми /post, чтобы собрать пост.")
        await notify(m.bot, p["owner_id"], f"💳 {who}: подписка {p['days']} дн. за {p['amount']} ⭐")
    else:
        db.add_purchase(uid, p["tid"])
        r = db.shop_get(p["tid"])
        await m.answer(mtext("bought", title=html.escape(r["title"] if r else "?"), price=p["amount"]),
                       reply_markup=kb([cb("✍️ Создать пост", f"u:f:{p['tid']}")]))
        await notify(m.bot, p["owner_id"], f"🛍 {who} купил(а) шаблон «{r['title'] if r else p['tid']}» "
                     f"за {p['amount']} ⭐")


# ===================== меню пользователя =====================

def visible_templates(uid: int) -> list[dict]:
    return db.shop_list(published_only=not access.is_admin(uid))


async def user_home(bot: Bot, chat_id: int, uid: int, msg: Message | None = None):
    rows = []
    items = visible_templates(uid)
    for r in items:
        mark = "✅" if access.can_use(uid, r["tid"]) else "🔒"
        rows.append([cb(f"{mark} {r['title']}", f"u:t:{r['tid']}")])
    text = mtext("welcome") + "\n\n"
    text += (mtext("hint") + "\n") if items else mtext("empty")
    if access.has_editor(uid):
        rows.append([cb("🛠 Редактор", "panel")])
    if msg:
        try:
            await msg.edit_text(text, reply_markup=kb(*rows))
            return
        except TelegramBadRequest:
            pass
    await bot.send_message(chat_id, text, reply_markup=kb(*rows))


async def shop_redirect(m: Message):
    """Основной бот, когда есть отдельный бот-магазин: обычных пользователей отправляем туда."""
    name = access.SHOP_BOT["username"]
    arg = (m.text or "").partition(" ")[2].strip()
    link = f"https://t.me/{name}" + (f"?start={arg}" if arg.startswith(("t_", "shop")) else "")
    await m.answer(mtext("redirect"), reply_markup=kb([B(text="🛍 Открыть магазин шаблонов", url=link)]))


@router.message(CommandStart(deep_link=True))
async def start_deep(m: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    db.touch_user(m.from_user.id, m.from_user.full_name, m.from_user.username)
    arg = command.args or ""
    if arg.startswith("t_") and arg[2:].isdigit():
        await template_card(m.bot, m.chat.id, m.from_user.id, int(arg[2:]))
    elif arg == "shop" or not access.has_editor(m.from_user.id):
        await user_home(m.bot, m.chat.id, m.from_user.id)
    else:
        from handlers import show_panel
        await show_panel(m.bot, m.chat.id, m.from_user.id)


@router.message(Command("menu"))
async def menu_cmd(m: Message, state: FSMContext):
    await state.clear()
    db.touch_user(m.from_user.id, m.from_user.full_name, m.from_user.username)
    await user_home(m.bot, m.chat.id, m.from_user.id)


@router.callback_query(F.data == "u:home")
async def home_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await user_home(c.bot, c.message.chat.id, c.from_user.id, c.message)


async def template_card(bot: Bot, chat_id: int, uid: int, tid: int, msg: Message | None = None):
    r = db.shop_get(tid)
    t = db.get_template_any(tid)
    if not r or not t or (not r["published"] and not access.is_admin(uid)):
        await bot.send_message(chat_id, "Шаблон не найден или снят с публикации.",
                               reply_markup=kb([cb("⬅️ Меню", "u:home")]))
        return
    if msg:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
    try:
        await send_post(bot, chat_id, t["data"])  # как выглядит шаблон
    except TelegramBadRequest:
        pass
    text = f"📄 <b>{html.escape(r['title'])}</b>\n"
    if r["descr"]:
        text += f"\n{html.escape(r['descr'])}\n"
    rows = []
    if access.can_use(uid, tid):
        rows.append([cb("✍️ Создать пост по шаблону", f"u:f:{tid}")])
        rows.append([B(text="↗️ Отправить через inline", switch_inline_query_chosen_chat=
                       SwitchInlineQueryChosenChat(query=f"#{tid} ", allow_user_chats=True,
                                                   allow_bot_chats=True, allow_group_chats=True,
                                                   allow_channel_chats=True))])
    elif r["access"] == "sub" or r["price"] <= 0:
        text += "\n🔒 Шаблон пока недоступен."
    else:
        text += "\n" + mtext("locked", price=r["price"], title=r["title"])
        rows.append([cb(mtext("buy_btn", price=r["price"], title=r["title"])[:60], f"u:b:{tid}")])
    if access.is_admin(uid):
        rows.append([cb("⚙️ Настроить (админ)", f"a:t:{tid}")])
    rows.append([cb("⬅️ Меню", "u:home")])
    await bot.send_message(chat_id, text, reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("u:t:"))
async def card_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await template_card(c.bot, c.message.chat.id, c.from_user.id, int(c.data[4:]), c.message)


@router.callback_query(F.data.startswith("u:b:"))
async def buy_tpl(c: CallbackQuery):
    tid = int(c.data[4:])
    r = db.shop_get(tid)
    if not r or not r["published"] or r["access"] != "price" or r["price"] <= 0:
        await c.answer("Недоступно", show_alert=True)
        return
    await c.answer()
    await send_invoice(c.bot, c.message.chat.id, "tpl", r["price"], r["title"],
                       f"Доступ навсегда к шаблону «{r['title']}»", tid=tid)


# ---------- заполнение шаблона ----------

async def ask_next(bot: Bot, chat_id: int, state: FSMContext):
    data = await state.get_data()
    keys, idx = data["keys"], data["idx"]
    if idx >= len(keys):
        await finish_fill(bot, chat_id, data["uid"], state)
        return
    r = db.shop_get(data["tid"]) or {"prompts": {}}
    k = keys[idx]
    prompt = r["prompts"].get(k) or default_prompt(k)
    await bot.send_message(chat_id, f"<b>Шаг {idx + 1} из {len(keys)}</b>\n{html.escape(prompt)}",
                           reply_markup=kb([cb("✖️ Отмена", f"u:t:{data['tid']}")]))


@router.callback_query(F.data.startswith("u:f:"))
async def fill_start(c: CallbackQuery, state: FSMContext):
    tid, uid = int(c.data[4:]), c.from_user.id
    t = db.get_template_any(tid)
    if not t or not access.can_use(uid, tid):
        await c.answer("Нет доступа", show_alert=True)
        return
    await c.answer()
    await state.set_state(U.fill)
    await state.update_data(tid=tid, uid=uid, uname=c.from_user.username, keys=var_keys(t["data"]), idx=0, vals={})
    await ask_next(c.bot, c.message.chat.id, state)


@router.message(U.fill, F.text)
async def fill_in(m: Message, state: FSMContext):
    data = await state.get_data()
    k = data["keys"][data["idx"]]
    v = m.text.strip()
    vals = data["vals"]
    if k == "link":
        if not re.match(r"^(https?://|t\.me/|tg://|@)\S+$", v, re.I):
            await m.answer("Это не похоже на ссылку. Пришли ссылку вида https://… или t.me/…")
            return
        vals.update(link_vars(v))
    else:
        vals[k] = v
    await state.update_data(vals=vals, idx=data["idx"] + 1)
    await ask_next(m.bot, m.chat.id, state)


async def finish_fill(bot: Bot, chat_id: int, uid: int, state: FSMContext):
    data = await state.get_data()
    t = db.get_template_any(data["tid"])
    d = db.empty_draft()
    d.update(apply_vars(t["data"], {**data["vals"], **me_vals(uid, data.get("uname"))}))
    db.save_draft(uid, d)  # в черновике таймер ещё «от отправки»: у каждой отправки свой отсчёт
    await state.set_state(None)
    await state.update_data(last_tid=data["tid"], last_vals=data["vals"])
    started = start_timer(d)
    try:
        sent = await send_post(bot, chat_id, started)
    except TelegramBadRequest as e:
        await bot.send_message(chat_id, f"❌ Telegram не принял пост: <code>{html.escape(e.message)}</code>")
        return
    # запоминаем, чтобы бот сам обновлял живой таймер в этом сообщении
    db.add_sent(uid, chat_id, "Чат с ботом", sent.message_id, started, bot.id)
    args = " | ".join([data["vals"].get("link", "")] +
                      [data["vals"][k] for k in data["keys"] if k.isdigit()]).strip(" |")
    rows = [
        [B(text="↗️ Отправить в любой чат", switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
            query=f"#{data['tid']} {args}"[:256], allow_user_chats=True, allow_bot_chats=True,
            allow_group_chats=True, allow_channel_chats=True))],
        [cb("📢 В свой канал/группу", "u:ch")],
        [cb("🔄 Заполнить заново", f"u:f:{data['tid']}"), cb("⬅️ Меню", "u:home")],
    ]
    if access.has_editor(uid):
        rows.insert(2, [cb("🛠 Открыть в редакторе", "panel")])
    await bot.send_message(chat_id, "👆 Готово! Перешли этот пост или отправь кнопками ниже.",
                           reply_markup=kb(*rows))


@router.callback_query(F.data == "u:ch")
async def to_channel(c: CallbackQuery, state: FSMContext):
    await state.set_state(U.target)
    await c.answer()
    await c.message.answer(
        "📢 Добавь бота в канал/группу администратором (право «Публикация сообщений»), "
        "затем пришли сюда @username, ID или перешли сообщение оттуда.",
        reply_markup=kb([cb("✖️ Отмена", "u:home")]))


@router.message(U.target)
async def to_channel_in(m: Message, state: FSMContext):
    from handlers import send_target_in
    await send_target_in(m, state)


# ===================== админка =====================

def admin_only(c_or_m) -> bool:
    return bool(ADMIN_IDS) and c_or_m.from_user.id in ADMIN_IDS


async def admin_home(bot: Bot, chat_id: int, msg: Message | None = None):
    st = db.stats()
    shop_name = access.SHOP_BOT["username"] or (await bot.me()).username
    text = (
        "👑 <b>Админка</b>\n\n"
        f"👥 Пользователей: {st['users']}\n"
        f"🛍 Покупок шаблонов: {st['purchases']}\n💰 Всего получено: {st['stars']} ⭐\n\n"
        "Редактор постов доступен только админам; пользователи покупают доступ к шаблонам.\n"
        f"Ссылка на магазин: <code>https://t.me/{shop_name}?start=shop</code>")
    rows = [
        [cb("🛍 Шаблоны для пользователей", "a:tl")],
        [cb("📝 Тексты меню", "a:mt")],
        [cb("🎁 Выдать / забрать доступ", "a:g")],
        [cb("⬅️ К посту", "panel")],
    ]
    if msg:
        try:
            await msg.edit_text(text, reply_markup=kb(*rows))
            return
        except TelegramBadRequest:
            pass
    await bot.send_message(chat_id, text, reply_markup=kb(*rows))


@router.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    if not ADMIN_IDS:
        await m.answer("Сначала впиши свой Telegram ID в ADMIN_IDS в файле .env и перезапусти бота — "
                       "без этого платный доступ не включить (сейчас ботом могут пользоваться все).")
        return
    if not admin_only(m):
        return
    await state.clear()
    await admin_home(m.bot, m.chat.id)


@router.callback_query(F.data.startswith("a:"))
async def admin_guard(c: CallbackQuery, state: FSMContext):
    if not admin_only(c):
        await c.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    parts = c.data.split(":")
    act = parts[1]
    await c.answer()
    if act == "h":
        await admin_home(c.bot, c.message.chat.id, c.message)
    elif act == "pt":
        db.set_setting("pay_on", "0" if access.paywall_on() else "1")
        await admin_home(c.bot, c.message.chat.id, c.message)
    elif act == "pp":
        await state.set_state(A.pay)
        await c.message.edit_text("Пришли цену в звёздах и срок в днях через пробел, например "
                                  "<code>150 30</code>", reply_markup=kb([cb("⬅️ Админка", "a:h")]))
    elif act == "g":
        await state.set_state(A.grant)
        await c.message.edit_text(
            "🎁 Пришли ID пользователя и число дней: <code>123456789 30</code>\n"
            "Забрать доступ: <code>123456789 0</code>\n"
            "Подарить шаблон навсегда: <code>123456789 t5</code> (5 — номер шаблона)",
            reply_markup=kb([cb("⬅️ Админка", "a:h")]))
    elif act == "mt":
        await menu_texts_screen(c.message)
    elif act == "mte":
        await state.set_state(A.mtext)
        await state.update_data(key=parts[2])
        label, default = MENU_TEXTS[parts[2]]
        cur = db.get_setting("mt_" + parts[2], "") or default
        await c.message.edit_text(
            f"✏️ <b>{label}</b>\nСейчас:\n<code>{html.escape(cur)}</code>\n\nПришли новый текст "
            "(HTML: &lt;b&gt;, &lt;i&gt;…). Можно {price} и {title}, где это уместно. «-» — вернуть по умолчанию.",
            reply_markup=kb([cb("⬅️ Тексты", "a:mt")]))
    elif act == "ua":
        await state.set_state(A.tpl_user)
        await state.update_data(tid=int(parts[2]))
        await c.message.edit_text(
            "➕ Кому открыть этот шаблон? Пришли:\n• ID пользователя (<code>123456789</code>)\n"
            "• или @username (если он уже писал боту)\n• или перешли его сообщение.\n"
            "Можно несколько через пробел или запятую.",
            reply_markup=kb([cb("⬅️ Назад", f"a:t:{parts[2]}")]))
    elif act == "ul":
        await template_users_screen(c.message, int(parts[2]))
    elif act == "ur":
        db.remove_purchase(int(parts[3]), int(parts[2]))
        await template_users_screen(c.message, int(parts[2]))
    elif act == "tl":
        await admin_templates(c)
    elif act == "t":
        await admin_tpl(c.bot, c.message, int(parts[2]))
    elif act in ("pub", "acc"):
        tid = int(parts[2])
        r = db.shop_get(tid)
        if act == "pub":
            r["published"] = 0 if r["published"] else 1
        else:
            order = ["free", "price"]
            r["access"] = order[(order.index(r["access"]) + 1) % 2] if r["access"] in order else "free"
        db.shop_save(r)
        await admin_tpl(c.bot, c.message, tid)
    elif act in ("ti", "de", "pr"):
        await state.set_state({"ti": A.title, "de": A.descr, "pr": A.price}[act])
        await state.update_data(tid=int(parts[2]))
        q = {"ti": "Пришли название для покупателей.",
             "de": "Пришли описание (что делает шаблон). «-» — убрать.",
             "pr": "Пришли цену в звёздах (доступ навсегда)."}[act]
        await c.message.edit_text(q, reply_markup=kb([cb("⬅️ Назад", f"a:t:{parts[2]}")]))
    elif act == "pq":
        tid, k = int(parts[2]), parts[3]
        await state.set_state(A.prompt)
        await state.update_data(tid=tid, key=k)
        await c.message.edit_text(
            f"Что спросить у пользователя для <code>{{{k}}}</code>?\n"
            f"Например: «🔗 Ссылка на NFT» или «💰 Цена в звёздах». «-» — по умолчанию.",
            reply_markup=kb([cb("⬅️ Назад", f"a:t:{tid}")]))
    elif act == "rm":
        db.conn().execute("DELETE FROM shop WHERE tid=?", (int(parts[2]),))
        db.conn().commit()
        await admin_templates(c)


async def menu_texts_screen(msg: Message):
    rows = [[cb(f"✏️ {label}", f"a:mte:{k}")] for k, (label, _) in MENU_TEXTS.items()]
    rows.append([cb("⬅️ Админка", "a:h")])
    await msg.edit_text("📝 <b>Тексты меню для пользователей</b>\nВыбери, что изменить.", reply_markup=kb(*rows))


@router.message(A.mtext, F.text)
async def a_mtext(m: Message, state: FSMContext):
    key = (await state.get_data())["key"]
    v = m.html_text.strip()
    try:
        v.format(price=1, title="x")
    except (KeyError, IndexError, ValueError):
        await m.answer("В тексте лишние фигурные скобки. Допустимы только {price} и {title}.")
        return
    db.set_setting("mt_" + key, "" if v == "-" else v[:500])
    await state.clear()
    await m.answer("✅ Сохранено.", reply_markup=kb([cb("📝 Тексты меню", "a:mt")]))


def user_label(uid: int, name, username) -> str:
    return (f"@{username}" if username else (name or str(uid)))[:40]


async def template_users_screen(msg: Message, tid: int):
    users = db.template_users(tid)
    rows = [[cb(f"❌ {user_label(u['user_id'], u['name'], u['username'])} · {u['user_id']}",
                f"a:ur:{tid}:{u['user_id']}")] for u in users[:40]]
    rows.append([cb("➕ Добавить пользователя", f"a:ua:{tid}")])
    rows.append([cb("⬅️ К шаблону", f"a:t:{tid}")])
    text = (f"👥 <b>Доступ к шаблону</b> — {len(users)} чел.\nНажми на пользователя, чтобы забрать доступ."
            if users else "👥 Пока ни у кого нет доступа к этому шаблону.")
    await msg.edit_text(text, reply_markup=kb(*rows))


@router.message(A.tpl_user)
async def a_tpl_user(m: Message, state: FSMContext):
    tid = (await state.get_data())["tid"]
    found, bad = [], []
    fwd = m.forward_origin
    if fwd is not None:
        u = getattr(fwd, "sender_user", None)
        if not u:
            await m.answer("У этого пользователя скрыта пересылка — пришли его ID или @username.")
            return
        db.touch_user(u.id, u.full_name, u.username)
        found.append((u.id, user_label(u.id, u.full_name, u.username)))
    else:
        for tok in re.split(r"[\s,]+", (m.text or "").strip()):
            if not tok:
                continue
            if tok.lstrip("-").isdigit():
                found.append((int(tok), tok))
            elif re.fullmatch(r"@?\w{4,32}", tok):
                row = db.user_by_username(tok)
                if row:
                    found.append((row["user_id"], "@" + row["username"]))
                else:
                    bad.append(tok)
            else:
                bad.append(tok)
    for uid, _ in found:
        db.add_purchase(uid, tid)
        r = db.shop_get(tid)
        try:
            await (access.SHOP_BOT.get("bot") or m.bot).send_message(uid, f"🎁 Тебе открыт шаблон «{html.escape(r['title'] if r else '')}».",
                                     reply_markup=kb([cb("✍️ Создать пост", f"u:f:{tid}")]))
        except Exception:  # noqa: BLE001 — пользователь ещё не писал этому боту
            pass
    text = ""
    if found:
        text += "✅ Доступ открыт: " + ", ".join(html.escape(l) for _, l in found)
    if bad:
        text += ("\n" if text else "") + ("❓ Не нашёл: " + ", ".join(html.escape(b) for b in bad) +
                                           "\n@username работает, только если человек уже писал боту — "
                                           "иначе пришли ID.")
    if not found:
        await m.answer(text or "Пришли ID, @username или перешли сообщение.")
        return
    await state.clear()
    await m.answer(text, reply_markup=kb([cb("👥 Доступ", f"a:ul:{tid}"), cb("⬅️ К шаблону", f"a:t:{tid}")]))


async def admin_templates(c: CallbackQuery):
    rows = []
    for t in db.list_templates(c.from_user.id)[:50]:
        r = db.shop_get(t["id"])
        mark = ("🟢" if r["published"] else "⚪") if r else "➕"
        rows.append([cb(f"{mark} {t['name']}", f"a:t:{t['id']}")])
    rows.append([cb("⬅️ Админка", "a:h")])
    await c.message.edit_text(
        "🛍 <b>Шаблоны для пользователей</b>\n🟢 опубликован · ⚪ скрыт · ➕ ещё не настроен\n\n"
        "У каждого опубликованного шаблона своё меню: пользователь отвечает на твои вопросы "
        "(ссылка, цена…) и получает готовый пост.", reply_markup=kb(*rows))


async def admin_tpl(bot: Bot, msg: Message | None, tid: int, chat_id: int | None = None):
    t = db.get_template_any(tid)
    if not t:
        await bot.send_message(chat_id or msg.chat.id, "Шаблон не найден.",
                               reply_markup=kb([cb("⬅️", "a:tl")]))
        return
    r = db.shop_get(tid)
    if not r:
        r = {"tid": tid, "title": t["name"], "descr": "", "access": "free", "price": 0,
             "prompts": {}, "published": 0}
        db.shop_save(r)
    shop_name = access.SHOP_BOT["username"] or (await bot.me()).username
    keys = var_keys(t["data"])
    qs = "\n".join(f"  <code>{{{k}}}</code> → {html.escape(r['prompts'].get(k) or default_prompt(k))}"
                   for k in keys) or "  (переменных нет — пользователь сразу получит пост)"
    text = (f"⚙️ <b>{html.escape(r['title'])}</b>\n"
            f"{html.escape(r['descr']) or '<i>без описания</i>'}\n\n"
            f"<b>Статус:</b> {'🟢 опубликован' if r['published'] else '⚪ скрыт'}\n"
            f"<b>Доступ:</b> {access_label(r)}\n<b>Вопросы пользователю:</b>\n{qs}\n\n"
            f"Прямая ссылка на меню шаблона:\n<code>https://t.me/{shop_name}?start=t_{tid}</code>")
    rows = [
        [cb("⚪ Скрыть" if r["published"] else "🟢 Опубликовать", f"a:pub:{tid}")],
        [cb(f"Доступ: {access_label(r)}", f"a:acc:{tid}")],
    ]
    if r["access"] == "price":
        rows.append([cb("💲 Цена", f"a:pr:{tid}")])
    rows.append([cb("✏️ Название", f"a:ti:{tid}"), cb("📝 Описание", f"a:de:{tid}")])
    rows += [[cb(f"❓ Вопрос для {{{k}}}", f"a:pq:{tid}:{k}")] for k in keys]
    n_users = len(db.template_users(tid))
    rows.append([cb("➕ Добавить пользователя", f"a:ua:{tid}"),
                 cb(f"👥 Доступ ({n_users})", f"a:ul:{tid}")])
    rows.append([cb("👁 Как видит пользователь", f"u:t:{tid}")])
    rows.append([cb("🗑 Убрать из магазина", f"a:rm:{tid}"), cb("⬅️ Шаблоны", "a:tl")])
    if msg:
        try:
            await msg.edit_text(text, reply_markup=kb(*rows))
            return
        except TelegramBadRequest:
            pass
    await bot.send_message(chat_id or msg.chat.id, text, reply_markup=kb(*rows))


@router.message(A.pay, F.text)
async def a_pay(m: Message, state: FSMContext):
    nums = re.findall(r"\d+", m.text)
    if len(nums) < 2 or not 1 <= int(nums[0]) <= 10000 or int(nums[1]) < 1:
        await m.answer("Нужно два числа: цена (1–10000) и дни. Пример: <code>150 30</code>")
        return
    db.set_setting("pay_price", int(nums[0]))
    db.set_setting("pay_days", int(nums[1]))
    await state.clear()
    await admin_home(m.bot, m.chat.id)


@router.message(A.grant, F.text)
async def a_grant(m: Message, state: FSMContext):
    mt = re.fullmatch(r"\s*(\d+)\s+(t?)(\d+)\s*", m.text)
    if not mt:
        await m.answer("Формат: <code>123456789 30</code> или <code>123456789 t5</code>")
        return
    uid, is_tpl, n = int(mt.group(1)), mt.group(2), int(mt.group(3))
    if is_tpl:
        db.add_purchase(uid, n)
        res = f"шаблон #{n} выдан навсегда"
    elif n == 0:
        db.revoke_sub(uid)
        res = "подписка снята"
    else:
        res = f"подписка до {fmt_date(db.extend_sub(uid, n))}"
    await state.clear()
    await m.answer(f"✅ {uid}: {res}.")
    await admin_home(m.bot, m.chat.id)


@router.message(A.title, F.text)
@router.message(A.descr, F.text)
@router.message(A.price, F.text)
@router.message(A.prompt, F.text)
async def a_tpl_field(m: Message, state: FSMContext):
    st = await state.get_state()
    data = await state.get_data()
    r = db.shop_get(data["tid"])
    v = m.text.strip()
    if st == A.title.state:
        r["title"] = v[:60]
    elif st == A.descr.state:
        r["descr"] = "" if v == "-" else v[:500]
    elif st == A.price.state:
        if not v.isdigit() or not 1 <= int(v) <= 10000:
            await m.answer("Цена — число от 1 до 10000.")
            return
        r["price"] = int(v)
    else:
        if v == "-":
            r["prompts"].pop(data["key"], None)
        else:
            r["prompts"][data["key"]] = v[:200]
    db.shop_save(r)
    await state.clear()
    await m.answer("✅ Сохранено.")
    await admin_tpl(m.bot, None, data["tid"], chat_id=m.chat.id)
