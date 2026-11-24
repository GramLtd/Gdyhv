import html
import json
import re
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton as B,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultsButton,
    LabeledPrice,
    PreCheckoutQuery,
    Message,
    SwitchInlineQueryChosenChat,
)

import access
import db
from render import (apply_vars, has_vars, parse_args, BUTTON_TYPES, LINK_PRESETS, STYLES, TZ, edit_post, fmt_left, inline_result,
                    send_post, start_timer, switch_in_place)

router = Router()

TEXT_LIMIT, CAPTION_LIMIT = 4096, 1024
RAW_HTML_RE = re.compile(
    r"</?(b|strong|i|em|u|ins|s|strike|del|a|code|pre|blockquote|tg-spoiler|tg-emoji|span)\b",
    re.I)


class S(StatesGroup):
    text = State()
    media = State()
    btn_text = State()
    btn_value = State()
    btn_icon = State()
    btn_rename = State()
    tpl_name = State()
    tpl_rename = State()
    target = State()
    emoji = State()
    gift_to = State()
    btn_popup = State()
    timer_until = State()
    timer_done = State()
    fill = State()


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def cb(text: str, data: str) -> B:
    return B(text=text, callback_data=data)


BACK = cb("⬅️ К посту", "panel")


# ===================== панель управления =====================

def panel_kb(d: dict) -> InlineKeyboardMarkup:
    n = sum(len(r) for r in d["buttons"])
    rows = [
        [cb("✏️ Текст", "txt"), cb("🖼 Медиа", "med")],
        [cb("➕ Кнопка", "ba"), cb(f"🧩 Кнопки ({n})", "bl")],
        [cb("🔗 Превью ссылок: " + ("выкл" if d["no_preview"] else "вкл"), "tp"),
         cb(("⬆️ Медиа/превью над текстом" if d["preview_above"] else "⬇️ Медиа/превью под текстом"),
            "ta")],
        [cb("🔣 Переменные {link} {1}", "vh")],
        [cb("🧾 Текст как HTML-код", "hc"),
         cb("⏱ Таймер" + (" ✅" if d.get("timer") else ""), "tm")],
    ]
    if d.get("template_id"):
        rows.append([cb("💾 Обновить шаблон", "tu"), cb("💾 Как новый", "ts")])
    else:
        rows.append([cb("💾 Сохранить шаблон", "ts")])
    rows.append([cb("📂 Шаблоны", "tl"), cb("🗂 Отправленные", "sl")])
    if d.get("edit_target"):
        rows.append([cb("♻️ Обновить отправленный пост", "up")])
        rows.append([cb("✖️ Отменить редактирование", "upx")])
    rows.append([cb("📤 Отправить", "sm"), cb("🧹 Новый пост", "nw")])
    return kb(*rows)


async def preview(bot: Bot, chat_id: int, d: dict) -> str | None:
    """Отправляет превью. Возвращает текст ошибки или None."""
    if not d["html"] and not d["media"] and not d["buttons"]:
        return None
    try:
        await send_post(bot, chat_id, d)
    except TelegramBadRequest as e:
        has_extras = any(b.get("icon") or b.get("style") for r in d["buttons"] for b in r)
        if has_extras:
            try:
                await send_post(bot, chat_id, d, plain=True)
                return ("⚠️ Telegram отклонил премиум-иконки/цвета кнопок, показываю без них.\n"
                        f"<code>{html.escape(e.message)}</code>")
            except TelegramBadRequest:
                pass
        return f"❌ Telegram не принял пост:\n<code>{html.escape(e.message)}</code>"
    return None


async def show_panel(bot: Bot, chat_id: int, user_id: int, note: str = "", with_preview=True):
    d = db.get_draft(user_id)
    err = await preview(bot, chat_id, d) if with_preview else None
    head = "🛠 <b>Редактор поста</b>"
    if d.get("edit_target"):
        head += "\n♻️ Режим редактирования уже отправленного поста"
    if d.get("template_id"):
        t = db.get_template(user_id, d["template_id"])
        if t:
            head += f"\n📄 Шаблон: <b>{html.escape(t['name'])}</b>"
    if not d["html"] and not d["media"] and not d["buttons"]:
        head += ("\n\nПост пустой. Просто пришли мне сообщение — текст с форматированием, "
                 "ссылками и премиум-эмодзи, фото/видео с подписью или пересланный пост.")
    else:
        head += "\n\n👆 Выше — так будет выглядеть пост."
    parts = [p for p in (note, err, head) if p]
    await bot.send_message(chat_id, "\n\n".join(parts), reply_markup=panel_kb(d))


async def back_to_panel(c: CallbackQuery, note: str = "", with_preview=True):
    try:
        await c.message.delete()
    except TelegramBadRequest:
        pass
    await show_panel(c.bot, c.message.chat.id, c.from_user.id, note, with_preview)


# ===================== команды =====================

HELP = """<b>Что умеет бот</b>

✏️ <b>Текст</b> — пиши как обычно в Telegram: жирный, курсив, спойлер, цитаты, \
<b>ссылки внутри текста</b> (выдели слово → «Добавить ссылку») и <b>премиум-эмодзи</b>. \
Можно прислать и HTML-код: <code>&lt;a href="https://…"&gt;слово&lt;/a&gt;</code>, \
<code>&lt;tg-emoji emoji-id="…"&gt;🔥&lt;/tg-emoji&gt;</code>.

🧩 <b>Кнопки</b> — ссылки, Mini App, всплывающие окна, уведомления, копирование текста, \
inline-запросы, готовые ссылки на меню Telegram (настройки, Premium, Stars, стикеры, буст…). \
Цвет кнопки и премиум-эмодзи иконка, ряды и порядок.

💾 <b>Шаблоны</b> — сохраняй, загружай, переименовывай, отправляй через inline: \
<code>@бот название</code> в любом чате.

📤 <b>Отправка</b> — сюда, в канал/группу (бот — админ) или через inline. \
Отправленные посты можно потом <b>отредактировать</b>.

/new — новый пост
/post — открыть редактор
/templates — шаблоны
/sent — отправленные посты
/emoji — узнать ID премиум-эмодзи
/menu — шаблоны для пользователей
/admin — платный доступ и магазин шаблонов (владелец)
/cancel — отменить ввод"""


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    if not access.has_editor(m.from_user.id):
        import shop
        await shop.user_home(m.bot, m.chat.id, m.from_user.id)
        return
    await m.answer("👋 Привет! Я помогу собрать красивый пост.\n\n" + HELP)
    await show_panel(m.bot, m.chat.id, m.from_user.id)


@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(HELP)


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await show_panel(m.bot, m.chat.id, m.from_user.id, "Отменено.", with_preview=False)


@router.message(Command("new"))
async def new_cmd(m: Message, state: FSMContext):
    await state.clear()
    db.save_draft(m.from_user.id, db.empty_draft())
    await show_panel(m.bot, m.chat.id, m.from_user.id)


@router.message(Command("post"))
async def post_cmd(m: Message, state: FSMContext):
    await state.clear()
    await show_panel(m.bot, m.chat.id, m.from_user.id)


@router.callback_query(F.data == "panel")
async def panel_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await back_to_panel(c, with_preview=False)


@router.callback_query(F.data == "nw")
async def new_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    db.save_draft(c.from_user.id, db.empty_draft())
    await c.answer("Новый пост")
    await back_to_panel(c)


# ===================== текст и медиа =====================

def message_html(m: Message) -> str:
    """Текст сообщения в HTML. Если прислан «сырой» HTML-код без форматирования — берём как есть."""
    raw = m.text if m.text is not None else (m.caption or "")
    ents = m.entities if m.text is not None else m.caption_entities
    formatted = [e for e in (ents or []) if e.type not in
                 ("url", "mention", "hashtag", "cashtag", "bot_command", "email", "phone_number")]
    if not formatted and RAW_HTML_RE.search(raw):
        return raw
    return m.html_text if raw else ""


def media_of(m: Message):
    if m.photo:
        return {"type": "photo", "file_id": m.photo[-1].file_id}
    if m.video:
        return {"type": "video", "file_id": m.video.file_id}
    if m.animation:
        return {"type": "animation", "file_id": m.animation.file_id}
    if m.document:
        return {"type": "document", "file_id": m.document.file_id}
    return None


def buttons_from_markup(m: Message) -> list:
    """Переносит кнопки из пересланного поста."""
    rows = []
    mk = m.reply_markup
    if not mk or not getattr(mk, "inline_keyboard", None):
        return rows
    for row in mk.inline_keyboard:
        r = []
        for b in row:
            item = {"uid": db.new_uid(), "text": b.text, "style": b.style,
                    "icon": b.icon_custom_emoji_id}
            if b.url:
                item.update(type="url", value=b.url)
            elif b.web_app:
                item.update(type="webapp", value=b.web_app.url)
            elif b.copy_text:
                item.update(type="copy", value=b.copy_text.text)
            else:
                continue
            r.append(item)
        if r:
            rows.append(r)
    return rows


async def apply_text(m: Message, new_html: str, state: FSMContext):
    uid = m.from_user.id
    d = db.get_draft(uid)
    limit = CAPTION_LIMIT if d["media"] else TEXT_LIMIT
    plain_len = len(re.sub(r"<[^>]+>", "", new_html))
    if plain_len > limit:
        await m.answer(f"❌ Слишком длинно: {plain_len} символов, лимит {limit}"
                       + (" (подпись к медиа)" if d["media"] else "") + ". Пришли короче.")
        return
    old = d["html"]
    d["html"] = new_html
    db.save_draft(uid, d)
    err = await preview(m.bot, m.chat.id, d)
    if err and err.startswith("❌"):
        d["html"] = old
        db.save_draft(uid, d)
        await m.answer(err + "\n\nТекст не сохранён, исправь и пришли снова.")
        return
    await state.clear()
    await show_panel(m.bot, m.chat.id, uid, "✅ Текст обновлён." + (f"\n{err}" if err else ""),
                     with_preview=False)


@router.callback_query(F.data == "txt")
async def txt_cb(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.text)
    await c.answer()
    await c.message.edit_text(
        "✏️ Пришли новый текст поста.\n\n"
        "• Форматируй прямо в Telegram (выдели текст → меню форматирования): "
        "жирный, курсив, подчёркнутый, зачёркнутый, спойлер, моноширинный, цитата, "
        "<b>ссылка</b>.\n"
        "• Премиум-эмодзи вставляются как обычно.\n"
        "• Или пришли HTML-код (возьми текущий через «🧾 Текст как HTML-код», поправь и пришли).\n"
        "• «-» — удалить текст.",
        reply_markup=kb([BACK]))


@router.message(S.text, F.text)
async def txt_in(m: Message, state: FSMContext):
    await apply_text(m, "" if m.text.strip() == "-" else message_html(m), state)


@router.callback_query(F.data == "hc")
async def html_code(c: CallbackQuery):
    d = db.get_draft(c.from_user.id)
    await c.answer()
    if not d["html"]:
        await c.message.answer("Текст пустой.")
        return
    await c.message.answer("🧾 Текущий текст в HTML. Нажми на блок, чтобы скопировать, "
                           "отредактируй и пришли через «✏️ Текст»:")
    code = html.escape(d["html"])
    for i in range(0, len(code), 3800):
        await c.message.answer(f"<pre><code class=\"language-html\">{code[i:i+3800]}</code></pre>")


@router.callback_query(F.data == "med")
async def med_cb(c: CallbackQuery, state: FSMContext):
    d = db.get_draft(c.from_user.id)
    await state.set_state(S.media)
    await c.answer()
    rows = [[BACK]]
    if d["media"]:
        rows.insert(0, [cb("🗑 Убрать медиа", "medx")])
    await c.message.edit_text(
        "🖼 Пришли фото, видео, GIF или файл. Подпись (если есть) заменит текст поста.\n"
        "Лимит подписи к медиа — 1024 символа.", reply_markup=kb(*rows))


@router.callback_query(F.data == "medx")
async def med_del(c: CallbackQuery, state: FSMContext):
    d = db.get_draft(c.from_user.id)
    d["media"] = None
    db.save_draft(c.from_user.id, d)
    await state.clear()
    await c.answer("Медиа убрано")
    await back_to_panel(c)


async def apply_media(m: Message, state: FSMContext):
    uid = m.from_user.id
    d = db.get_draft(uid)
    d["media"] = media_of(m)
    if m.caption:
        d["html"] = message_html(m)
    imported = buttons_from_markup(m)
    if imported:
        d["buttons"] = imported
    if len(re.sub(r"<[^>]+>", "", d["html"])) > CAPTION_LIMIT:
        await m.answer("❌ Текст поста длиннее 1024 символов — так много нельзя в подписи к медиа. "
                       "Сначала сократи текст.")
        return
    db.save_draft(uid, d)
    await state.clear()
    await show_panel(m.bot, m.chat.id, uid, "✅ Медиа добавлено.")


MEDIA_FILTER = F.photo | F.video | F.animation | F.document


@router.message(S.media, MEDIA_FILTER)
async def med_in(m: Message, state: FSMContext):
    await apply_media(m, state)


# ===================== кнопки: добавление =====================

def type_menu(prefix: str) -> InlineKeyboardMarkup:
    rows = [[cb(label, f"{prefix}:{t}")] for t, (label, _) in BUTTON_TYPES.items()]
    rows.insert(1, [cb("📚 Меню и разделы Telegram", f"{prefix}:presets")])
    rows.append([BACK])
    return kb(*rows)


@router.callback_query(F.data == "ba")
async def btn_add(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await c.message.edit_text("➕ <b>Что будет делать кнопка?</b>", reply_markup=type_menu("bt"))


@router.callback_query(F.data.startswith("bt:"))
async def btn_type(c: CallbackQuery, state: FSMContext):
    t = c.data.split(":", 1)[1]
    await c.answer()
    if t == "presets":
        rows = [[cb(p[0], f"bp:{i}")] for i, p in enumerate(LINK_PRESETS)]
        rows.append([cb("⬅️ Назад", "ba")])
        await c.message.edit_text(
            "📚 <b>Ссылка на раздел Telegram</b>\nЧасть tg://-ссылок открывается не во всех "
            "клиентах (лучше всего — в официальных приложениях).", reply_markup=kb(*rows))
        return
    await state.set_state(S.btn_text)
    await state.update_data(type=t, preset=None)
    await c.message.edit_text(f"{BUTTON_TYPES[t][0]}\n\nОтправь <b>текст кнопки</b>.",
                              reply_markup=kb([BACK]))


@router.callback_query(F.data.startswith("bp:"))
async def btn_preset(c: CallbackQuery, state: FSMContext):
    i = int(c.data.split(":")[1])
    await c.answer()
    await state.set_state(S.btn_text)
    await state.update_data(type="url", preset=i)
    await c.message.edit_text(f"{LINK_PRESETS[i][0]}\n\nОтправь <b>текст кнопки</b>.",
                              reply_markup=kb([BACK]))


def normalize_url(v: str) -> str | None:
    v = v.strip()
    if v.startswith("@"):
        v = "https://t.me/" + v[1:]
    elif v.startswith("t.me/") or v.startswith("telegram.me/"):
        v = "https://" + v
    elif re.match(r"^[\w-]+\.[a-z]{2,}(/|$)", v, re.I):
        v = "https://" + v
    if re.match(r"^(https?|tg)://\S+$", v, re.I):
        return v
    return None


def validate_value(t: str, v: str) -> tuple[str | None, str | None]:
    """Возвращает (значение, ошибка)."""
    if t in ("url", "webapp") and "{" in v:
        return v.strip(), None  # шаблонная ссылка: {link} подставится при заполнении
    if t == "url":
        u = normalize_url(v)
        return (u, None) if u else (None, "Нужна ссылка вида https://…, t.me/… или tg://…")
    if t == "webapp":
        u = normalize_url(v)
        if not u or not u.lower().startswith("https://"):
            return None, "Для Mini App нужна https-ссылка."
        return u, None
    if t in ("alert", "toast"):
        return (v, None) if len(v) <= 200 else (None, f"Максимум 200 символов, у тебя {len(v)}.")
    if t == "copy":
        return (v, None) if len(v) <= 256 else (None, f"Максимум 256 символов, у тебя {len(v)}.")
    if t == "stars":
        amount, _, desc = v.partition("|")
        amount = amount.strip()
        if not amount.isdigit() or not 1 <= int(amount) <= 10000:
            return None, "Нужно число звёзд от 1 до 10000."
        return f"{int(amount)}|{desc.strip()[:255]}", None
    if t == "page":
        return (v, None) if v.isdigit() else (None, "Выбери шаблон из списка.")
    if t in ("inline", "inline_here", "inline_pick"):
        return ("" if v.strip() == "-" else v), None
    return v, None


@router.message(S.btn_text, F.text)
async def btn_text_in(m: Message, state: FSMContext):
    text = m.text.strip()
    if len(text) > 64:
        await m.answer("Текст кнопки лучше уложить в 64 символа. Пришли короче.")
        return
    data = await state.get_data()
    await state.update_data(text=text)
    if data.get("preset") is not None:
        label, pattern, hint = LINK_PRESETS[data["preset"]]
        if "{x}" not in pattern:
            await finish_button(m, state, pattern)
            return
        await state.set_state(S.btn_value)
        await m.answer(f"Теперь отправь: <b>{hint}</b>\nШаблон: <code>{html.escape(pattern)}</code>",
                       reply_markup=kb([BACK]))
        return
    if data["type"] == "gift":
        await gift_picker(m.bot, m.chat.id, state)
        return
    if data["type"] == "page":
        await page_picker(m.bot, m.chat.id, m.from_user.id, state)
        return
    await state.set_state(S.btn_value)
    await m.answer(BUTTON_TYPES[data["type"]][1], reply_markup=kb([BACK]))


@router.message(S.btn_value, F.text)
async def btn_value_in(m: Message, state: FSMContext):
    data = await state.get_data()
    v = m.text.strip()
    if data.get("preset") is not None:
        pattern = LINK_PRESETS[data["preset"]][1]
        x = v.lstrip("@")
        if pattern.startswith("https://t.me/{x}") and x.startswith(("https://t.me/", "t.me/")):
            x = x.split("t.me/", 1)[1]
        v = pattern.replace("{x}", x)
    await finish_button(m, state, v)


async def finish_button(m: Message, state: FSMContext, raw_value: str):
    data = await state.get_data()
    t = data["type"]
    value, err = validate_value(t, raw_value)
    if err:
        await m.answer(f"❌ {err}\nПопробуй ещё раз.")
        return
    uid = m.from_user.id
    d = db.get_draft(uid)
    edit = data.get("edit")
    extra = {}
    if t in ("stars", "gift"):
        try:
            extra["link"], value = await make_invoice(m.bot, uid, t, value, data)
        except TelegramBadRequest as e:
            await state.clear()
            await m.answer(f"❌ Telegram не создал счёт: <code>{html.escape(e.message)}</code>",
                           reply_markup=kb([BACK]))
            return
    if edit:  # смена действия у существующей кнопки
        r, i = edit
        try:
            b = d["buttons"][r][i]
        except IndexError:
            await state.clear()
            await m.answer("Кнопка не найдена.")
            return
        b.update(type=t, value=value, **extra)
        if data.get("text"):
            b["text"] = data["text"]
    else:
        d["buttons"].append([{"uid": db.new_uid(), "text": data["text"], "type": t,
                              "value": value, "style": None, "icon": None, **extra}])
        r, i = len(d["buttons"]) - 1, 0
    db.save_draft(uid, d)
    await state.clear()
    err = await preview(m.bot, m.chat.id, d)
    await m.answer(("✅ Кнопка сохранена.\n" + (err + "\n" if err else "")) + btn_info(d, r, i),
                   reply_markup=btn_menu(d, r, i))


# ===================== кнопки: список и редактирование =====================

def btn_info(d: dict, r: int, i: int) -> str:
    b = d["buttons"][r][i]
    label = BUTTON_TYPES.get(b["type"], ("?",))[0]
    val = b.get("value") or "—"
    if b["type"] == "page":
        t = db.get_template_any(int(val)) if val.isdigit() else None
        val = f"→ шаблон «{t['name']}»" if t else "→ шаблон удалён!"
        pop = b.get("popup") or {}
        if pop.get("text"):
            val += (f"\n+ {'💬 окно' if pop.get('alert') else '🔔 уведомление'}: {pop['text']}")
    if b["type"] == "stars":
        val = "⭐ " + val.replace("|", " — ").rstrip(" —")
    return (f"\n<b>Кнопка:</b> {html.escape(b['text'])}\n"
            f"<b>Тип:</b> {label}\n<b>Значение:</b> <code>{html.escape(val)}</code>\n"
            f"<b>Цвет:</b> {STYLES.get(b.get('style'))}\n"
            f"<b>Иконка:</b> {'✨ есть' if b.get('icon') else 'нет'}\n"
            f"<b>Позиция:</b> ряд {r + 1}, место {i + 1}")


def btn_menu(d: dict, r: int, i: int) -> InlineKeyboardMarkup:
    k = f"{r}:{i}"
    extra = []
    if d["buttons"][r][i]["type"] == "page":
        pop = d["buttons"][r][i].get("popup") or {}
        extra = [cb("💬 Окно при нажатии" + (" ✅" if pop.get("text") else ""), f"bx:pop:{k}")]
        if pop.get("text"):
            extra.append(cb("Тип: " + ("💬 окно" if pop.get("alert") else "🔔 сверху"), f"bx:popt:{k}"))
    return kb(
        [cb("✏️ Текст", f"bx:txt:{k}"), cb("🔁 Действие", f"bx:act:{k}")],
        [cb("🎨 Цвет", f"bx:sty:{k}"), cb("✨ Иконка", f"bx:ico:{k}")],
        extra,
        [cb("⬅️", f"bx:l:{k}"), cb("➡️", f"bx:r:{k}"),
         cb("⤴️ В ряд выше", f"bx:up:{k}"), cb("⤵️ Отдельный ряд", f"bx:dn:{k}")],
        [cb("🗑 Удалить", f"bx:del:{k}"), cb("🧩 Все кнопки", "bl")],
        [BACK],
    )


@router.callback_query(F.data == "bl")
async def btn_list(c: CallbackQuery, state: FSMContext):
    await state.clear()
    d = db.get_draft(c.from_user.id)
    await c.answer()
    rows = [[cb(b["text"], f"be:{r}:{i}") for i, b in enumerate(row)]
            for r, row in enumerate(d["buttons"])]
    rows.append([cb("➕ Добавить кнопку", "ba")])
    if d["buttons"]:
        rows.append([cb("🗑 Удалить все", "bclr")])
    rows.append([BACK])
    await c.message.edit_text("🧩 <b>Кнопки поста</b> — раскладка как в посте. Нажми, чтобы изменить."
                              if d["buttons"] else "Кнопок пока нет.", reply_markup=kb(*rows))


@router.callback_query(F.data == "bclr")
async def btn_clear(c: CallbackQuery):
    d = db.get_draft(c.from_user.id)
    d["buttons"] = []
    db.save_draft(c.from_user.id, d)
    await c.answer("Кнопки удалены")
    await back_to_panel(c)


@router.callback_query(F.data.startswith("be:"))
async def btn_open(c: CallbackQuery):
    _, r, i = c.data.split(":")
    r, i = int(r), int(i)
    d = db.get_draft(c.from_user.id)
    await c.answer()
    try:
        await c.message.edit_text("⚙️" + btn_info(d, r, i), reply_markup=btn_menu(d, r, i))
    except IndexError:
        await c.message.edit_text("Кнопка не найдена.", reply_markup=kb([BACK]))


@router.callback_query(F.data.startswith("bx:"))
async def btn_action(c: CallbackQuery, state: FSMContext):
    _, act, r, i = c.data.split(":")
    r, i = int(r), int(i)
    uid = c.from_user.id
    d = db.get_draft(uid)
    rows = d["buttons"]
    try:
        b = rows[r][i]
    except IndexError:
        await c.answer("Кнопка не найдена", show_alert=True)
        return

    if act == "txt":
        await state.set_state(S.btn_rename)
        await state.update_data(edit=[r, i])
        await c.answer()
        await c.message.edit_text(f"Текущий текст: <code>{html.escape(b['text'])}</code>\n"
                                  "Отправь новый текст кнопки.", reply_markup=kb([BACK]))
        return
    if act == "act":
        await state.clear()
        await state.update_data(edit=[r, i])
        await c.answer()
        await c.message.edit_text("🔁 Выбери новое действие кнопки:", reply_markup=type_menu("bat"))
        return
    if act == "ico":
        await state.set_state(S.btn_icon)
        await state.update_data(edit=[r, i])
        await c.answer()
        await c.message.edit_text(
            "✨ Пришли <b>одно премиум-эмодзи</b> (или его ID) — оно появится иконкой перед текстом "
            "кнопки. «-» — убрать иконку.\n\n⚠️ Telegram разрешает иконки и цвета кнопок только "
            "ботам с купленным на Fragment username. Иначе бот покажет кнопку без них.",
            reply_markup=kb([BACK]))
        return

    if act == "pop":
        await state.set_state(S.btn_popup)
        await state.update_data(edit=[r, i])
        await c.answer()
        cur = (b.get("popup") or {}).get("text")
        await c.message.edit_text(
            "💬 Пришли текст, который покажется при нажатии (до 200 символов). Пост и кнопки "
            "при этом сменятся на выбранный шаблон.\n«-» — убрать окно."
            + (f"\n\nСейчас: <code>{html.escape(cur)}</code>" if cur else ""),
            reply_markup=kb([BACK]))
        return

    if act == "popt" and b.get("popup"):
        b["popup"]["alert"] = not b["popup"].get("alert")
        note = "Окно по центру" if b["popup"]["alert"] else "Уведомление сверху"
    elif act == "sty":
        keys = list(STYLES)
        b["style"] = keys[(keys.index(b.get("style")) + 1) % len(keys)]
        note = f"Цвет: {STYLES[b['style']]}"
    elif act == "l" and i > 0:
        rows[r][i - 1], rows[r][i] = rows[r][i], rows[r][i - 1]
        i -= 1
        note = "Сдвинута влево"
    elif act == "r" and i < len(rows[r]) - 1:
        rows[r][i + 1], rows[r][i] = rows[r][i], rows[r][i + 1]
        i += 1
        note = "Сдвинута вправо"
    elif act == "up" and r > 0:
        rows[r].pop(i)
        rows[r - 1].append(b)
        if not rows[r]:
            rows.pop(r)
        r, i = r - 1, len(rows[r - 1]) - 1
        note = "Перенесена в ряд выше"
    elif act == "dn" and len(rows[r]) > 1:
        rows[r].pop(i)
        rows.insert(r + 1, [b])
        r, i = r + 1, 0
        note = "Теперь в отдельном ряду"
    elif act == "del":
        rows[r].pop(i)
        if not rows[r]:
            rows.pop(r)
        db.save_draft(uid, d)
        await c.answer("Удалено")
        await back_to_panel(c)
        return
    else:
        await c.answer("Тут двигать некуда")
        return
    db.save_draft(uid, d)
    await c.answer(note)
    await c.message.edit_text("⚙️" + btn_info(d, r, i) + "\n\n<i>Превью обновится при возврате к посту.</i>",
                              reply_markup=btn_menu(d, r, i))


@router.message(S.btn_popup, F.text)
async def btn_popup_in(m: Message, state: FSMContext):
    r, i = (await state.get_data())["edit"]
    d = db.get_draft(m.from_user.id)
    try:
        b = d["buttons"][r][i]
    except IndexError:
        await state.clear()
        await m.answer("Кнопка не найдена.", reply_markup=kb([BACK]))
        return
    v = m.text.strip()
    if v == "-":
        b.pop("popup", None)
    elif len(v) > 200:
        await m.answer("Слишком длинно: максимум 200 символов.")
        return
    else:
        b["popup"] = {"text": v, "alert": (b.get("popup") or {}).get("alert", True)}
    db.save_draft(m.from_user.id, d)
    await state.clear()
    await m.answer("✅ Сохранено." + btn_info(d, r, i), reply_markup=btn_menu(d, r, i))


@router.callback_query(F.data.startswith("bat:"))
async def btn_change_type(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("edit"):
        await c.answer("Начни заново", show_alert=True)
        return
    t = c.data.split(":", 1)[1]
    await c.answer()
    if t == "presets":
        rows = [[cb(p[0], f"bap:{i}")] for i, p in enumerate(LINK_PRESETS)]
        rows.append([BACK])
        await c.message.edit_text("📚 Выбери раздел Telegram:", reply_markup=kb(*rows))
        return
    await state.update_data(type=t, preset=None, text=None)
    if t == "gift":
        await c.message.delete()
        await gift_picker(c.bot, c.message.chat.id, state)
        return
    if t == "page":
        await c.message.delete()
        await page_picker(c.bot, c.message.chat.id, c.from_user.id, state)
        return
    await state.set_state(S.btn_value)
    await c.message.edit_text(BUTTON_TYPES[t][1], reply_markup=kb([BACK]))


@router.callback_query(F.data.startswith("bap:"))
async def btn_change_preset(c: CallbackQuery, state: FSMContext):
    i = int(c.data.split(":")[1])
    await state.update_data(type="url", preset=i, text=None)
    await c.answer()
    label, pattern, hint = LINK_PRESETS[i]
    if "{x}" not in pattern:
        await c.message.delete()
        await finish_button(c.message.model_copy(update={"from_user": c.from_user}), state, pattern)
        return
    await state.set_state(S.btn_value)
    await c.message.edit_text(f"Отправь: <b>{hint}</b>\nШаблон: <code>{html.escape(pattern)}</code>",
                              reply_markup=kb([BACK]))


@router.message(S.btn_rename, F.text)
async def btn_rename_in(m: Message, state: FSMContext):
    data = await state.get_data()
    r, i = data["edit"]
    d = db.get_draft(m.from_user.id)
    d["buttons"][r][i]["text"] = m.text.strip()[:64]
    db.save_draft(m.from_user.id, d)
    await state.clear()
    await m.answer("✅ Текст изменён." + btn_info(d, r, i), reply_markup=btn_menu(d, r, i))


@router.message(S.btn_icon)
async def btn_icon_in(m: Message, state: FSMContext):
    data = await state.get_data()
    r, i = data["edit"]
    icon = None
    if m.text and m.text.strip() == "-":
        icon = ""
    else:
        for e in (m.entities or []):
            if e.type == "custom_emoji":
                icon = e.custom_emoji_id
                break
        if icon is None and m.sticker and m.sticker.custom_emoji_id:
            icon = m.sticker.custom_emoji_id
        if icon is None and m.text and m.text.strip().isdigit():
            icon = m.text.strip()
    if icon is None:
        await m.answer("Не нашёл премиум-эмодзи. Пришли именно премиум-эмодзи (анимированное из "
                       "наборов), его числовой ID или «-».")
        return
    d = db.get_draft(m.from_user.id)
    d["buttons"][r][i]["icon"] = icon or None
    db.save_draft(m.from_user.id, d)
    await state.clear()
    err = await preview(m.bot, m.chat.id, d)
    await m.answer(("✅ Иконка " + ("установлена." if icon else "убрана.")) +
                   (f"\n{err}" if err else "") + btn_info(d, r, i), reply_markup=btn_menu(d, r, i))


# ===================== превью ссылок =====================

@router.callback_query(F.data.in_({"tp", "ta"}))
async def toggles(c: CallbackQuery):
    d = db.get_draft(c.from_user.id)
    key = "no_preview" if c.data == "tp" else "preview_above"
    d[key] = not d[key]
    db.save_draft(c.from_user.id, d)
    await c.answer("Готово")
    await back_to_panel(c)


# ===================== шаблоны =====================

def auto_name(d: dict) -> str:
    plain = re.sub(r"<[^>]+>", "", d["html"] or "").strip().replace("\n", " ")
    return (html.unescape(plain)[:30] or "Пост") + time.strftime(" · %d.%m %H:%M")


@router.callback_query(F.data == "ts")
async def tpl_save(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.tpl_name)
    await c.answer()
    d = db.get_draft(c.from_user.id)
    await c.message.edit_text(
        "💾 Отправь название шаблона.\nИли нажми кнопку — назову автоматически.",
        reply_markup=kb([cb(f"✨ {auto_name(d)}", "tsa")], [BACK]))


async def do_save(bot: Bot, chat_id: int, uid: int, name: str):
    d = db.get_draft(uid)
    tid = db.create_template(uid, name[:60], d)
    d["template_id"] = tid
    db.save_draft(uid, d)
    await show_panel(bot, chat_id, uid, f"✅ Шаблон «{html.escape(name)}» сохранён.\n"
                     f"Отправить его в любой чат: <code>@{(await bot.me()).username} {html.escape(name[:20])}</code>",
                     with_preview=False)


@router.callback_query(F.data == "tsa")
async def tpl_save_auto(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await c.message.delete()
    await do_save(c.bot, c.message.chat.id, c.from_user.id, auto_name(db.get_draft(c.from_user.id)))


@router.message(S.tpl_name, F.text)
async def tpl_name_in(m: Message, state: FSMContext):
    await state.clear()
    await do_save(m.bot, m.chat.id, m.from_user.id, m.text.strip())


@router.callback_query(F.data == "tu")
async def tpl_update(c: CallbackQuery):
    d = db.get_draft(c.from_user.id)
    t = d.get("template_id") and db.get_template(c.from_user.id, d["template_id"])
    if not t:
        d["template_id"] = None
        db.save_draft(c.from_user.id, d)
        await c.answer("Шаблон не найден — сохрани как новый", show_alert=True)
        return
    db.update_template(c.from_user.id, t["id"], d)
    await c.answer(f"Шаблон «{t['name']}» обновлён", show_alert=True)


async def templates_screen(bot: Bot, chat_id: int, uid: int, msg: Message | None = None):
    rows = [[cb(f"📄 {t['name']}", f"to:{t['id']}")] for t in db.list_templates(uid)[:50]]
    rows.append([BACK])
    text = "📂 <b>Твои шаблоны</b>" if len(rows) > 1 else "Шаблонов пока нет. Собери пост и нажми «💾»."
    if msg:
        await msg.edit_text(text, reply_markup=kb(*rows))
    else:
        await bot.send_message(chat_id, text, reply_markup=kb(*rows))


@router.message(Command("templates"))
async def tpl_cmd(m: Message, state: FSMContext):
    await state.clear()
    await templates_screen(m.bot, m.chat.id, m.from_user.id)


@router.callback_query(F.data == "tl")
async def tpl_list(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    await templates_screen(c.bot, c.message.chat.id, c.from_user.id, c.message)


def tpl_menu(t: dict) -> InlineKeyboardMarkup:
    tid = t["id"]
    return kb(
        [cb("📥 Открыть в редакторе", f"tld:{tid}")],
        [B(text="↗️ Отправить в любой чат (inline)",
           switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
               query=f"#{tid}", allow_user_chats=True, allow_bot_chats=True,
               allow_group_chats=True, allow_channel_chats=True))],
        [cb("✍️ Заполнить переменные", f"tfl:{tid}")] if has_vars(t["data"]) else None,
        [cb("✏️ Переименовать", f"trn:{tid}"), cb("🗑 Удалить", f"tdl:{tid}")],
        [cb("⬅️ Шаблоны", "tl")],
    )


@router.callback_query(F.data.startswith("to:"))
async def tpl_open(c: CallbackQuery):
    t = db.get_template(c.from_user.id, int(c.data.split(":")[1]))
    if not t:
        await c.answer("Шаблон не найден", show_alert=True)
        return
    await c.answer()
    await c.message.delete()
    err = await preview(c.bot, c.message.chat.id, t["data"])
    await c.message.answer(f"📄 Шаблон <b>{html.escape(t['name'])}</b>" + (f"\n\n{err}" if err else ""),
                           reply_markup=tpl_menu(t))


@router.callback_query(F.data.startswith("tld:"))
async def tpl_load(c: CallbackQuery, state: FSMContext):
    await state.clear()
    t = db.get_template(c.from_user.id, int(c.data.split(":")[1]))
    if not t:
        await c.answer("Шаблон не найден", show_alert=True)
        return
    d = db.empty_draft()
    d.update(t["data"])
    d["template_id"] = t["id"]
    for row in d["buttons"]:  # новые uid, чтобы окна в старых постах не менялись
        for b in row:
            b["uid"] = db.new_uid()
    db.save_draft(c.from_user.id, d)
    await c.answer("Загружено")
    await back_to_panel(c, "📥 Шаблон загружен. Меняй что угодно и жми «💾 Обновить шаблон».",
                        with_preview=False)


@router.callback_query(F.data.startswith("trn:"))
async def tpl_rename(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.tpl_rename)
    await state.update_data(tid=int(c.data.split(":")[1]))
    await c.answer()
    await c.message.edit_text("Отправь новое название шаблона.", reply_markup=kb([cb("⬅️ Шаблоны", "tl")]))


@router.message(S.tpl_rename, F.text)
async def tpl_rename_in(m: Message, state: FSMContext):
    tid = (await state.get_data())["tid"]
    db.rename_template(m.from_user.id, tid, m.text.strip()[:60])
    await state.clear()
    await m.answer("✅ Переименовано.")
    await templates_screen(m.bot, m.chat.id, m.from_user.id)


@router.callback_query(F.data.startswith("tdl:"))
async def tpl_delete_ask(c: CallbackQuery):
    tid = c.data.split(":")[1]
    await c.answer()
    await c.message.edit_text("Удалить шаблон? Отменить будет нельзя.",
                              reply_markup=kb([cb("🗑 Да, удалить", f"tdy:{tid}"), cb("Нет", f"to:{tid}")]))


@router.callback_query(F.data.startswith("tdy:"))
async def tpl_delete(c: CallbackQuery):
    tid = int(c.data.split(":")[1])
    db.delete_template(c.from_user.id, tid)
    d = db.get_draft(c.from_user.id)
    if d.get("template_id") == tid:
        d["template_id"] = None
        db.save_draft(c.from_user.id, d)
    await c.answer("Удалено")
    await templates_screen(c.bot, c.message.chat.id, c.from_user.id, c.message)


# ===================== отправка =====================

def recent_targets(uid: int):
    seen, out = set(), []
    for r in db.list_sent(uid, 50):
        if r["chat_id"] != uid and r["chat_id"] not in seen:
            seen.add(r["chat_id"])
            out.append((r["chat_id"], r["chat_title"] or str(r["chat_id"])))
    return out[:5]


@router.callback_query(F.data == "sm")
async def send_menu(c: CallbackQuery, state: FSMContext):
    d = db.get_draft(c.from_user.id)
    if not d["html"] and not d["media"] and not d["buttons"]:
        await c.answer("Пост пустой", show_alert=True)
        return
    await state.set_state(S.target)
    await c.answer()
    rows = [[cb("📍 Сюда (чтобы переслать)", "sh")]]
    rows += [[cb(f"📢 {title}", f"st:{cid}")] for cid, title in recent_targets(c.from_user.id)]
    rows.append([cb("↗️ Через inline в любой чат", "si")])
    rows.append([BACK])
    await c.message.edit_text(
        "📤 <b>Куда отправить?</b>\n\n"
        "Чтобы отправить в <b>канал или группу</b> — добавь бота туда администратором "
        "(право «Публикация сообщений») и пришли сюда @username, ID чата "
        "или перешли любое сообщение оттуда.", reply_markup=kb(*rows))


def post_link(chat, message_id: int) -> str | None:
    if getattr(chat, "username", None):
        return f"https://t.me/{chat.username}/{message_id}"
    s = str(chat.id)
    if s.startswith("-100"):
        return f"https://t.me/c/{s[4:]}/{message_id}"
    return None


async def deliver(bot: Bot, user_id: int, chat_id, reply_to: Message, state: FSMContext):
    d = db.get_draft(user_id)
    try:
        chat = await bot.get_chat(chat_id)
        if chat.type != "private":
            member = await bot.get_chat_member(chat.id, user_id)
            if member.status not in ("creator", "administrator"):
                await reply_to.answer("❌ Ты не администратор этого чата.")
                return
        d = start_timer(d)  # таймер «от отправки» стартует здесь, у каждого поста свой
        sent = await send_post(bot, chat.id, d)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        await reply_to.answer(f"❌ Не удалось отправить:\n<code>{html.escape(e.message)}</code>\n\n"
                              "Проверь, что бот добавлен в чат и может публиковать сообщения.")
        return
    await state.clear()
    title = chat.title or chat.full_name or str(chat.id)
    db.add_sent(user_id, chat.id, title, sent.message_id, d, bot.id)
    link = post_link(chat, sent.message_id)
    rows = [[B(text="🔗 Открыть пост", url=link)]] if link else []
    if access.has_editor(user_id):
        rows.append([cb("🗂 Отправленные", "sl"), BACK])
    else:
        rows.append([cb("⬅️ Меню", "u:home")])
    await reply_to.answer(f"✅ Отправлено в «{html.escape(title)}»." + (
                          "\nЕго можно будет отредактировать в «🗂 Отправленные»."
                          if access.has_editor(user_id) else ""), reply_markup=kb(*rows))


@router.callback_query(F.data == "sh")
async def send_here(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await c.message.delete()
    await deliver(c.bot, c.from_user.id, c.message.chat.id, c.message, state)


@router.callback_query(F.data.startswith("st:"))
async def send_recent(c: CallbackQuery, state: FSMContext):
    await c.answer("Отправляю…")
    await deliver(c.bot, c.from_user.id, int(c.data.split(":")[1]), c.message, state)


@router.message(S.target)
async def send_target_in(m: Message, state: FSMContext):
    target = None
    if m.forward_origin and getattr(m.forward_origin, "chat", None):
        target = m.forward_origin.chat.id
    elif m.text:
        t = m.text.strip()
        if "t.me/" in t:
            t = "@" + t.split("t.me/", 1)[1].split("/")[0]
        if re.fullmatch(r"-?\d+", t):
            target = int(t)
        elif re.fullmatch(r"@?\w{4,}", t):
            target = "@" + t.lstrip("@")
    if target is None:
        await m.answer("Не понял, куда отправлять. Пришли @username, ID (-100…) или перешли сообщение из канала.")
        return
    await deliver(m.bot, m.from_user.id, target, m, state)


@router.callback_query(F.data == "si")
async def send_inline(c: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = c.from_user.id
    d = db.get_draft(uid)
    tid = d.get("template_id")
    if tid and db.get_template(uid, tid):
        db.update_template(uid, tid, d)
    else:
        tid = db.create_template(uid, auto_name(d), d)
        d["template_id"] = tid
        db.save_draft(uid, d)
    await c.answer()
    me = await c.bot.me()
    await c.message.edit_text(
        f"↗️ Нажми кнопку и выбери чат — пост отправится от твоего имени «via @{me.username}».\n"
        f"Так же можно в любом чате набрать <code>@{me.username} </code> и выбрать шаблон.\n\n"
        "ℹ️ Для этого в @BotFather должен быть включён inline-режим (/setinline).",
        reply_markup=kb([B(text="↗️ Выбрать чат", switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
            query=f"#{tid}", allow_user_chats=True, allow_bot_chats=True,
            allow_group_chats=True, allow_channel_chats=True))], [BACK]))


@router.inline_query()
async def inline_q(q: InlineQuery):
    uid = q.from_user.id
    s = q.query.strip()
    results = []
    # 1) «#id аргументы» или «название шаблона аргументы» — заполнение переменных
    picked = []
    m = re.match(r"#(\d+)\s*(.*)$", s, re.S)
    if m:
        t = access.get_for(uid, int(m.group(1)))
        if t:
            picked.append((t, m.group(2)))
    else:
        low = s.lower()
        for t in access.templates_for(uid):
            name = t["name"].lower()
            if low == name or low.startswith(name + " "):
                picked.append((t, s[len(name):]))
        picked.sort(key=lambda x: -len(x[0]["name"]))
    for t, rest in picked[:10]:
        vals = parse_args(rest) if rest.strip() else None
        try:
            r = inline_result(t, vals, rid=f"{t['id']}-{abs(hash(rest)) % 10**9}")
            if vals:
                r.description = "✅ " + ", ".join(f"{{{k}}}={v[:20]}" for k, v in vals.items()
                                                 if k in ("link_text",) or k.isdigit())[:120]
            elif has_vars(t["data"]):
                r.description = "Допиши через пробел: ссылку и значения {1} {2}…"
            results.append(r)
        except Exception:  # noqa: BLE001
            continue
    # 2) обычный поиск по названию
    if not results:
        for t in [t for t in access.templates_for(uid) if s.lower() in t["name"].lower()][:50]:
            try:
                r = inline_result(t)
                if has_vars(t["data"]):
                    r.description = f"Шаблон с переменными: «{t['name']} ссылка 500 …»"
                results.append(r)
            except Exception:  # noqa: BLE001
                continue
    await q.answer(results[:50], cache_time=0, is_personal=True,
                   button=None if results else InlineQueryResultsButton(
                       text="🛍 Магазин шаблонов" if access.SHOP_MODE.get() else "Создать шаблон",
                       start_parameter="shop" if access.SHOP_MODE.get() else "new"))


# ===================== переменные =====================

VARS_HELP = """🔣 <b>Переменные в шаблоне</b>

Пиши их в тексте, в тексте кнопок, в ссылках и во всплывающих окнах — при заполнении \
они заменятся на значения:

<code>{link}</code> — сама ссылка
<code>{link_text}</code> — текст из ссылки, без неё: \
<code>t.me/nft/PlushPepe-1234</code> → <b>Plush Pepe #1234</b>
<code>{link_name}</code> — <b>Plush Pepe</b>
<code>{link_num}</code> — <b>1234</b>
<code>{link_slug}</code> — <b>PlushPepe-1234</b>
<code>{1}</code>, <code>{2}</code> … <code>{9}</code> — значения по порядку

🔗 <b>Слово-ссылка:</b> выдели слово → «Добавить ссылку» → <code>https://link.com</code>. \
При заполнении туда встанет настоящая ссылка. Для кнопки-ссылки укажи <code>{link}</code>.

💬 Окно кнопки: <code>Цена {link_text} — {1} ⭐</code> — цифры подтянутся те же, что в тексте.

⚡ <b>Быстро через inline</b> в любом чате:
<code>@{bot} название ссылка 500 текст</code>
Если значение с пробелами — разделяй «|»:
<code>@{bot} название ссылка | 500 | два слова</code>"""


@router.callback_query(F.data == "vh")
async def vars_help(c: CallbackQuery):
    await c.answer()
    me = await c.bot.me()
    await c.message.edit_text(VARS_HELP.replace("{bot}", me.username), reply_markup=kb([BACK]))


@router.callback_query(F.data.startswith("tfl:"))
async def tpl_fill(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.fill)
    await state.update_data(tid=int(c.data.split(":")[1]))
    await c.answer()
    await c.message.edit_text(
        "✍️ Пришли значения: ссылку и значения для {1} {2}… через пробел "
        "(или через «|», если внутри есть пробелы).\nПример: <code>https://t.me/nft/PlushPepe-1234 500</code>",
        reply_markup=kb([cb("⬅️ Шаблоны", "tl")]))


@router.message(S.fill, F.text)
async def tpl_fill_in(m: Message, state: FSMContext):
    t = db.get_template(m.from_user.id, (await state.get_data())["tid"])
    await state.clear()
    if not t:
        await m.answer("Шаблон не найден.")
        return
    d = db.empty_draft()
    d.update(apply_vars(t["data"], parse_args(m.text)))
    db.save_draft(m.from_user.id, d)
    await show_panel(m.bot, m.chat.id, m.from_user.id,
                     "✅ Заполнено. Это новый черновик — отправляй или правь (шаблон не изменился).")


# ===================== отправленные посты =====================

async def sent_screen(bot: Bot, chat_id: int, uid: int, msg: Message | None = None):
    rows = []
    for r in db.list_sent(uid):
        when = time.strftime("%d.%m %H:%M", time.localtime(r["created"]))
        rows.append([cb(f"{when} · {r['chat_title'] or r['chat_id']}", f"so:{r['id']}")])
    rows.append([BACK])
    text = "🗂 <b>Последние отправленные посты</b>" if len(rows) > 1 else "Отправленных постов пока нет."
    if msg:
        await msg.edit_text(text, reply_markup=kb(*rows))
    else:
        await bot.send_message(chat_id, text, reply_markup=kb(*rows))


@router.message(Command("sent"))
async def sent_cmd(m: Message, state: FSMContext):
    await state.clear()
    await sent_screen(m.bot, m.chat.id, m.from_user.id)


@router.callback_query(F.data == "sl")
async def sent_list(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    try:
        await sent_screen(c.bot, c.message.chat.id, c.from_user.id, c.message)
    except TelegramBadRequest:
        await sent_screen(c.bot, c.message.chat.id, c.from_user.id)


@router.callback_query(F.data.startswith("so:"))
async def sent_open(c: CallbackQuery):
    sid = int(c.data.split(":")[1])
    r = db.get_sent(c.from_user.id, sid)
    if not r:
        await c.answer("Не найдено", show_alert=True)
        return
    await c.answer()
    await c.message.edit_text(
        f"📨 Пост в «{html.escape(r['chat_title'] or str(r['chat_id']))}», сообщение #{r['message_id']}",
        reply_markup=kb([cb("✏️ Редактировать", f"se:{sid}")],
                        [cb("🗑 Удалить из чата", f"sd:{sid}"), cb("➖ Убрать из списка", f"sf:{sid}")],
                        [cb("⬅️ Отправленные", "sl")]))


@router.callback_query(F.data.startswith("se:"))
async def sent_edit(c: CallbackQuery, state: FSMContext):
    await state.clear()
    sid = int(c.data.split(":")[1])
    r = db.get_sent(c.from_user.id, sid)
    if not r:
        await c.answer("Не найдено", show_alert=True)
        return
    d = db.empty_draft()
    d.update(json.loads(r["data"]))
    d["edit_target"] = sid
    db.save_draft(c.from_user.id, d)
    await c.answer()
    await back_to_panel(c, "✏️ Пост загружен в редактор. Внеси изменения и нажми "
                           "«♻️ Обновить отправленный пост».")


@router.callback_query(F.data == "up")
async def sent_update(c: CallbackQuery):
    uid = c.from_user.id
    d = db.get_draft(uid)
    r = d.get("edit_target") and db.get_sent(uid, d["edit_target"])
    if not r:
        await c.answer("Пост не найден", show_alert=True)
        return
    try:
        await edit_post(c.bot, r["chat_id"], r["message_id"], json.loads(r["data"]), d)
    except ValueError as e:
        await c.answer(str(e), show_alert=True)
        return
    except TelegramBadRequest as e:
        if "not modified" not in e.message:
            await c.answer(f"Ошибка: {e.message}"[:200], show_alert=True)
            return
    db.update_sent(uid, r["id"], d)
    d["edit_target"] = None
    db.save_draft(uid, d)
    await c.answer("✅ Пост обновлён", show_alert=True)
    await back_to_panel(c, with_preview=False)


@router.callback_query(F.data == "upx")
async def sent_update_cancel(c: CallbackQuery):
    d = db.get_draft(c.from_user.id)
    d["edit_target"] = None
    db.save_draft(c.from_user.id, d)
    await c.answer("Редактирование отменено")
    await back_to_panel(c, with_preview=False)


@router.callback_query(F.data.startswith("sd:"))
async def sent_delete_msg(c: CallbackQuery):
    sid = int(c.data.split(":")[1])
    r = db.get_sent(c.from_user.id, sid)
    if r:
        try:
            await c.bot.delete_message(r["chat_id"], r["message_id"])
        except TelegramBadRequest as e:
            await c.answer(f"Не удалось: {e.message}"[:200], show_alert=True)
            return
        db.delete_sent(c.from_user.id, sid)
    await c.answer("Удалено")
    await sent_screen(c.bot, c.message.chat.id, c.from_user.id, c.message)


@router.callback_query(F.data.startswith("sf:"))
async def sent_forget(c: CallbackQuery):
    db.delete_sent(c.from_user.id, int(c.data.split(":")[1]))
    await c.answer("Убрано из списка")
    await sent_screen(c.bot, c.message.chat.id, c.from_user.id, c.message)


# ===================== премиум-эмодзи =====================

@router.message(Command("emoji"))
async def emoji_cmd(m: Message, state: FSMContext):
    await state.set_state(S.emoji)
    await m.answer("✨ Пришли сообщение с премиум-эмодзи — покажу их ID и HTML-код. /cancel — выход.")


@router.message(S.emoji)
async def emoji_in(m: Message):
    ents = [e for e in (m.entities or m.caption_entities or []) if e.type == "custom_emoji"]
    raw = m.text or m.caption or ""
    lines = []
    for e in ents:
        ch = e.extract_from(raw)
        snippet = html.escape('<tg-emoji emoji-id="%s">%s</tg-emoji>' % (e.custom_emoji_id, ch))
        lines.append(f"{ch} <code>{e.custom_emoji_id}</code>\n<code>{snippet}</code>")
    if m.sticker and m.sticker.custom_emoji_id:
        lines.append(f"Стикер-эмодзи: <code>{m.sticker.custom_emoji_id}</code>")
    await m.answer("\n\n".join(lines) if lines else "Премиум-эмодзи не найдено.")


# ===================== всплывающие окна (работают для всех) =====================

@router.callback_query(F.data.startswith("pp:"))
async def popup(c: CallbackQuery):
    p = db.get_popup(c.data[3:])
    if not p:
        await c.answer()
        return
    await c.answer(p["text"], show_alert=bool(p["alert"]))


# ===================== звёзды и подарки =====================

async def make_invoice(bot: Bot, owner: int, t: str, value: str, data: dict):
    """Создаёт ссылку на оплату в Stars. Возвращает (ссылка, значение для показа)."""
    pid = db.new_uid()
    if t == "stars":
        amount, _, desc = value.partition("|")
        amount = int(amount)
        title = data.get("text") or "Поддержка"
        info = {"kind": "stars", "amount": amount}
        shown = value
        description = desc or f"Оплата {amount} ⭐"
    else:
        amount = data["gift_stars"]
        info = {"kind": "gift", "amount": amount, "gift_id": data["gift_id"],
                "to": data["gift_to"], "to_title": data["gift_to_title"],
                "note": data.get("gift_note") or ""}
        title = data.get("text") or "Подарок"
        shown = f"{data['gift_emoji']} за {amount} ⭐ → {data['gift_to_title']}"
        description = f"Подарок {data['gift_emoji']} для {data['gift_to_title']}"
    title = re.sub(r"\s+", " ", title)[:32] or "Оплата"
    link = await bot.create_invoice_link(
        title=title, description=description[:255], payload=f"pay:{pid}",
        currency="XTR", prices=[LabeledPrice(label=title, amount=amount)])
    db.put_payment(pid, owner, info)
    return link, shown


async def gift_picker(bot: Bot, chat_id: int, state: FSMContext):
    try:
        gifts = (await bot.get_available_gifts()).gifts
    except TelegramBadRequest as e:
        await state.clear()
        await bot.send_message(chat_id, f"❌ Не удалось получить список подарков: {e.message}",
                               reply_markup=kb([BACK]))
        return
    gifts = [g for g in gifts if g.remaining_count is None or g.remaining_count > 0][:60]
    cache = {g.id: [g.sticker.emoji or "🎁", g.star_count] for g in gifts}
    await state.update_data(gift_cache=cache)
    rows, row = [], []
    for g in gifts:
        mark = "🔥" if g.total_count else ""
        row.append(cb(f"{g.sticker.emoji or '🎁'} {g.star_count}⭐{mark}", f"gp:{g.id}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([BACK])
    await bot.send_message(
        chat_id, "🎁 <b>Выбери подарок</b>\nЧеловек, нажавший кнопку, оплатит его звёздами, "
        "а бот сразу отправит этот подарок получателю.\n🔥 — лимитированный.",
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("gp:"))
async def gift_pick(c: CallbackQuery, state: FSMContext):
    gid = c.data[3:]
    data = await state.get_data()
    g = (data.get("gift_cache") or {}).get(gid)
    if not g:
        await c.answer("Начни заново", show_alert=True)
        return
    await state.update_data(gift_id=gid, gift_emoji=g[0], gift_stars=g[1])
    await state.set_state(S.gift_to)
    await c.answer()
    await c.message.edit_text(
        f"Подарок {g[0]} за {g[1]} ⭐\n\n<b>Кому бот отправит подарок?</b>\n"
        "• «-» — тебе\n• @username канала или его ID — каналу\n"
        "• ID пользователя — ему (он должен был хоть раз написать боту)\n\n"
        "Можно добавить подпись к подарку через «|»: <code>- | Спасибо за поддержку!</code>",
        reply_markup=kb([BACK]))


@router.message(S.gift_to, F.text)
async def gift_to_in(m: Message, state: FSMContext):
    who, _, note = m.text.partition("|")
    who = who.strip()
    if who in ("-", ""):
        to, title = {"user_id": m.from_user.id}, "автору поста"
    elif re.fullmatch(r"\d+", who):
        to, title = {"user_id": int(who)}, f"пользователю {who}"
    elif re.fullmatch(r"-\d+|@?\w{4,}", who):
        try:
            chat = await m.bot.get_chat(int(who) if who.lstrip("-").isdigit() else "@" + who.lstrip("@"))
        except TelegramBadRequest as e:
            await m.answer(f"❌ Не нашёл чат: {e.message}. Попробуй ещё раз.")
            return
        if chat.type == "private":
            to, title = {"user_id": chat.id}, chat.full_name
        else:
            to, title = {"chat_id": chat.id}, chat.title or str(chat.id)
    else:
        await m.answer("Не понял получателя. «-», @канал, ID канала или ID пользователя.")
        return
    await state.update_data(gift_to=to, gift_to_title=title, gift_note=note.strip()[:128])
    await finish_button(m, state, "gift")


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    pid = q.invoice_payload.removeprefix("pay:")
    p = db.get_payment(pid)
    if not p or p["amount"] != q.total_amount:
        await q.answer(ok=False, error_message="Эта кнопка больше не активна.")
        return
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def paid(m: Message):
    sp = m.successful_payment
    pid = sp.invoice_payload.removeprefix("pay:")
    p = db.get_payment(pid)
    payer = m.from_user
    who = html.escape(payer.full_name) + (f" (@{payer.username})" if payer.username else "")
    if not p:
        db.log_paid(pid, payer.id, sp.total_amount, sp.telegram_payment_charge_id, "unknown")
        await m.answer("Спасибо! Оплата получена.")
        return
    if p["kind"] in ("sub", "tpl"):
        db.log_paid(pid, payer.id, sp.total_amount, sp.telegram_payment_charge_id, "ok")
        import shop
        await shop.on_paid(m, p, who)
        return
    if p["kind"] == "stars":
        db.log_paid(pid, payer.id, sp.total_amount, sp.telegram_payment_charge_id, "ok")
        await m.answer(f"💛 Спасибо! Получено {sp.total_amount} ⭐")
        await notify(m.bot, p["owner_id"], f"⭐ {who} отправил(а) {sp.total_amount} звёзд.")
        return
    try:
        await m.bot.send_gift(gift_id=p["gift_id"], text=p.get("note") or None, **p["to"])
    except TelegramBadRequest as e:
        db.log_paid(pid, payer.id, sp.total_amount, sp.telegram_payment_charge_id, "refunded")
        try:
            await m.bot.refund_star_payment(payer.id, sp.telegram_payment_charge_id)
            await m.answer("😔 Не получилось отправить подарок — звёзды возвращены.")
        except TelegramBadRequest:
            await m.answer("😔 Не получилось отправить подарок. Свяжитесь с автором поста.")
        await notify(m.bot, p["owner_id"], f"❗ {who} оплатил(а) подарок, но отправить его не "
                     f"удалось: <code>{html.escape(e.message)}</code>")
        return
    db.log_paid(pid, payer.id, sp.total_amount, sp.telegram_payment_charge_id, "ok")
    await m.answer(f"🎁 Готово! Подарок отправлен: {html.escape(p['to_title'])}.")
    await notify(m.bot, p["owner_id"], f"🎁 {who} купил(а) подарок → {html.escape(p['to_title'])}.")


async def notify(bot: Bot, uid: int, text: str):
    try:
        await bot.send_message(uid, text)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


# ===================== кнопка «сменить пост» =====================

async def page_picker(bot: Bot, chat_id: int, uid: int, state: FSMContext):
    tpls = db.list_templates(uid)
    rows = [[cb(f"📄 {t['name']}", f"pgp:{t['id']}")] for t in tpls[:50]]
    rows.append([BACK])
    await bot.send_message(
        chat_id,
        "🔄 <b>Во что превратится пост после нажатия?</b>\n\n"
        "Выбери шаблон — по нажатию бот заменит текст, медиа и кнопки поста на содержимое "
        "шаблона. В шаблоне могут быть свои кнопки «🔄 Сменить пост» — так строятся цепочки, "
        "меню, квизы, кнопка «назад».\n\n"
        "💡 Шаблон <b>без текста и медиа</b> заменит только кнопки, текст останется.\n"
        "⚠️ В каналах и группах пост меняется у всех, кто его видит.\n"
        "⚠️ Текстовый пост не может стать постом с медиа (ограничение Telegram)."
        + ("" if tpls else "\n\n❗ Сначала сохрани нужный экран как шаблон (💾), потом вернись сюда."),
        reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("pgp:"))
async def page_pick(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("type"):
        await c.answer("Начни заново", show_alert=True)
        return
    await c.answer()
    await c.message.delete()
    await finish_button(c.message.model_copy(update={"from_user": c.from_user}), state,
                        c.data.split(":")[1])


@router.callback_query(F.data.startswith("pg:"))
async def page_press(c: CallbackQuery):
    parts = c.data.split(":")
    t = db.get_template_any(int(parts[1]))
    pop = db.get_popup(parts[2]) if len(parts) > 2 else None
    vals = db.get_page_vals(parts[2]) if len(parts) > 2 else {}
    if not t:
        await c.answer("Эта кнопка больше не работает", show_alert=True)
        return
    # переменные (ссылки, цифры) предыдущего экрана переходят в новый — по всей цепочке
    d = start_timer(apply_vars(t["data"], vals) if vals else t["data"])
    for row in d.get("buttons") or []:  # окна/подарки нового экрана должны работать
        for b in row:
            b.setdefault("uid", db.new_uid())
    msg = c.message if c.message and hasattr(c.message, "message_id") and c.message.date else None
    try:
        if c.inline_message_id:
            await switch_in_place(c.bot, d, inline_id=c.inline_message_id)
        else:
            has_media = bool(getattr(msg, "photo", None) or getattr(msg, "video", None)
                             or getattr(msg, "animation", None) or getattr(msg, "document", None))
            await switch_in_place(c.bot, d, chat_id=c.message.chat.id,
                                  message_id=c.message.message_id, cur_has_media=has_media)
            db.update_sent_by_msg(c.message.chat.id, c.message.message_id, d)
    except TelegramBadRequest as e:
        if "not modified" not in e.message:
            await c.answer("Не получилось обновить пост", show_alert=True)
            return
    if pop:
        await c.answer(pop["text"], show_alert=bool(pop["alert"]))
    else:
        await c.answer()


# ===================== таймер =====================

def timer_text(d: dict) -> str:
    t = d.get("timer")
    s = ("⏱ <b>Таймер</b>\n\nПоставь <code>{timer}</code> в тексте поста там, где он нужен.\n\n"
         "• <b>Живой</b> — бот каждую минуту обновляет пост: <code>2 д 05:13</code>, по "
         "окончании пишет твой текст. Работает в постах, отправленных ботом "
         "(сюда, в канал, в группу), не через inline.\n"
         "• <b>Telegram</b> — Telegram сам показывает «через 3 часа» по-русски у каждого "
         "зрителя, бот пост не трогает. Работает везде.\n\n")
    if t and t.get("duration"):
        s += (f"<b>Длительность:</b> {fmt_left(t['duration'])} — ⏳ отсчёт стартует отдельно "
              "для каждого поста с момента его отправки\n"
              f"<b>Режим:</b> {'живой' if t['mode'] == 'live' else 'Telegram'}\n"
              f"<b>По окончании:</b> {html.escape(t.get('done') or 'время вышло')}")
        if "{timer}" not in (d.get("html") or ""):
            s += "\n\n⚠️ В тексте нет <code>{timer}</code> — таймер не виден."
    elif t:
        when = datetime.fromtimestamp(t["until"], TZ).strftime("%d.%m.%Y %H:%M")
        left = int(t["until"] - time.time())
        s += (f"<b>До:</b> {when} ({TZ.key})\n<b>Осталось:</b> {fmt_left(left)}\n"
              f"<b>Режим:</b> {'живой' if t['mode'] == 'live' else 'Telegram'}\n"
              f"<b>По окончании:</b> {html.escape(t.get('done') or 'время вышло')}")
        if "{timer}" not in (d.get("html") or ""):
            s += "\n\n⚠️ В тексте нет <code>{timer}</code> — таймер не виден."
    else:
        s += "Таймер не задан."
    return s


def timer_kb(d: dict) -> InlineKeyboardMarkup:
    t = d.get("timer")
    rows = [[cb("🕐 Точное время окончания", "tmu")],
            [cb("⏳ Длительность от отправки поста", "tmr")]]
    if t:
        rows.append([cb("Режим: " + ("🔁 живой" if t["mode"] == "live" else "📅 Telegram"), "tmm"),
                     cb("🏁 Текст по окончании", "tmd")])
        rows.append([cb("🗑 Убрать таймер", "tmx")])
    rows.append([BACK])
    return kb(*rows)


@router.callback_query(F.data == "tm")
async def timer_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    d = db.get_draft(c.from_user.id)
    await c.answer()
    await c.message.edit_text(timer_text(d), reply_markup=timer_kb(d))


@router.callback_query(F.data == "tmu")
async def timer_until(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.timer_until)
    await c.answer()
    now = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")
    await c.message.edit_text(
        f"🕐 Когда таймер закончится? Часовой пояс: {TZ.key}, сейчас {now}.\n\n"
        "Примеры:\n<code>31.12.2026 23:59</code>\n<code>25.12 18:00</code>\n"
        "<code>20:00</code> — сегодня (или завтра, если уже прошло)\n"
        "<code>+2ч</code>, <code>+30м</code>, <code>+3д 4ч</code> — через столько",
        reply_markup=kb([cb("⬅️ Таймер", "tm")]))


def parse_until(text: str) -> int | None:
    t = text.strip().lower()
    now = datetime.now(TZ)
    if t.startswith("+"):
        total = 0
        for num, unit in re.findall(r"(\d+)\s*([дdчhмm])", t):
            total += int(num) * {"д": 86400, "d": 86400, "ч": 3600, "h": 3600,
                                 "м": 60, "m": 60}[unit]
        return int(now.timestamp()) + total if total else None
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M", "%d.%m %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(t, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            dt = now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
            if dt <= now:
                dt = dt.fromtimestamp(dt.timestamp() + 86400, TZ)
        else:
            if "%Y" not in fmt and "%y" not in fmt:
                dt = dt.replace(year=now.year)
            dt = dt.replace(tzinfo=TZ)
        return int(dt.timestamp())
    return None


@router.callback_query(F.data == "tmr")
async def timer_rel(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.timer_until)
    await state.update_data(relative=True)
    await c.answer()
    await c.message.edit_text(
        "⏳ Сколько будет идти таймер <b>с момента отправки</b> каждого поста?\n\n"
        "Примеры: <code>2ч</code>, <code>30м</code>, <code>1д 12ч</code>, <code>45м</code>\n\n"
        "Сохрани пост шаблоном — и каждый отправленный по нему пост (сюда, в канал, "
        "через inline, из магазина) получит свой отсчёт.",
        reply_markup=kb([cb("⬅️ Таймер", "tm")]))


@router.message(S.timer_until, F.text)
async def timer_until_in(m: Message, state: FSMContext):
    if (await state.get_data()).get("relative"):
        txt = m.text.strip()
        until = parse_until(txt if txt.startswith("+") else "+" + txt)
        if not until:
            await m.answer("Не понял. Пример: <code>2ч</code>, <code>30м</code>, <code>1д 12ч</code>")
            return
        d = db.get_draft(m.from_user.id)
        t = d.get("timer") or {"mode": "live", "done": "⏰ Время вышло!"}
        t.pop("until", None)
        t.update(duration=until - int(time.time()), finished=False)
        d["timer"] = t
        note = ""
        if "{timer}" not in (d.get("html") or ""):
            d["html"] = (d.get("html") or "") + "\n\n⏱ До конца: {timer}"
            note = "Добавил в конец текста строку «⏱ До конца: {timer}» — перенеси куда хочешь.\n\n"
        db.save_draft(m.from_user.id, d)
        await state.clear()
        await m.answer("✅ " + note + timer_text(d), reply_markup=timer_kb(d))
        return
    until = parse_until(m.text)
    if not until or until <= time.time():
        await m.answer("Не понял время (или оно уже прошло). Пример: <code>31.12.2026 23:59</code> "
                       "или <code>+2ч</code>")
        return
    d = db.get_draft(m.from_user.id)
    t = d.get("timer") or {"mode": "live", "done": "⏰ Время вышло!"}
    t.pop("duration", None)
    t.update(until=until, finished=False)
    d["timer"] = t
    note = ""
    if "{timer}" not in (d.get("html") or ""):
        d["html"] = (d.get("html") or "") + "\n\n⏱ До конца: {timer}"
        note = "Добавил в конец текста строку «⏱ До конца: {timer}» — перенеси куда хочешь.\n\n"
    db.save_draft(m.from_user.id, d)
    await state.clear()
    await m.answer("✅ " + note + timer_text(d), reply_markup=timer_kb(d))


@router.callback_query(F.data == "tmm")
async def timer_mode(c: CallbackQuery):
    d = db.get_draft(c.from_user.id)
    if d.get("timer"):
        d["timer"]["mode"] = "native" if d["timer"]["mode"] == "live" else "live"
        db.save_draft(c.from_user.id, d)
    await c.answer()
    await c.message.edit_text(timer_text(d), reply_markup=timer_kb(d))


@router.callback_query(F.data == "tmd")
async def timer_done(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.timer_done)
    await c.answer()
    await c.message.edit_text("🏁 Что написать вместо таймера, когда время выйдет?",
                              reply_markup=kb([cb("⬅️ Таймер", "tm")]))


@router.message(S.timer_done, F.text)
async def timer_done_in(m: Message, state: FSMContext):
    d = db.get_draft(m.from_user.id)
    if d.get("timer"):
        d["timer"]["done"] = m.text.strip()[:200]
        db.save_draft(m.from_user.id, d)
    await state.clear()
    await m.answer("✅ " + timer_text(d), reply_markup=timer_kb(d))


@router.callback_query(F.data == "tmx")
async def timer_del(c: CallbackQuery):
    d = db.get_draft(c.from_user.id)
    d["timer"] = None
    d["html"] = (d.get("html") or "").replace("\n\n⏱ До конца: {timer}", "")
    db.save_draft(c.from_user.id, d)
    await c.answer("Таймер убран")
    await back_to_panel(c)


# ===================== сообщение без команды = контент поста =====================

@router.message(F.chat.type == "private", MEDIA_FILTER)
async def any_media(m: Message, state: FSMContext):
    await apply_media(m, state)


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def any_text(m: Message, state: FSMContext):
    imported = buttons_from_markup(m)
    if imported:
        d = db.get_draft(m.from_user.id)
        d["buttons"] = imported
        db.save_draft(m.from_user.id, d)
    await apply_text(m, message_html(m), state)


