import html
import json
import re
import time

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
    Message,
    SwitchInlineQueryChosenChat,
)

import db
from render import (BUTTON_TYPES, LINK_PRESETS, STYLES, edit_post, inline_result,
                    send_post)

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
        [cb("🧾 Текст как HTML-код", "hc")],
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
/cancel — отменить ввод"""


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
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
    if edit:  # смена действия у существующей кнопки
        r, i = edit
        try:
            b = d["buttons"][r][i]
        except IndexError:
            await state.clear()
            await m.answer("Кнопка не найдена.")
            return
        b.update(type=t, value=value)
        if data.get("text"):
            b["text"] = data["text"]
    else:
        d["buttons"].append([{"uid": db.new_uid(), "text": data["text"], "type": t,
                              "value": value, "style": None, "icon": None}])
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
    return (f"\n<b>Кнопка:</b> {html.escape(b['text'])}\n"
            f"<b>Тип:</b> {label}\n<b>Значение:</b> <code>{html.escape(val)}</code>\n"
            f"<b>Цвет:</b> {STYLES.get(b.get('style'))}\n"
            f"<b>Иконка:</b> {'✨ есть' if b.get('icon') else 'нет'}\n"
            f"<b>Позиция:</b> ряд {r + 1}, место {i + 1}")


def btn_menu(d: dict, r: int, i: int) -> InlineKeyboardMarkup:
    k = f"{r}:{i}"
    return kb(
        [cb("✏️ Текст", f"bx:txt:{k}"), cb("🔁 Действие", f"bx:act:{k}")],
        [cb("🎨 Цвет", f"bx:sty:{k}"), cb("✨ Иконка", f"bx:ico:{k}")],
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

    if act == "sty":
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
    await state.set_state(S.btn_value)
    await state.update_data(type=t, preset=None, text=None)
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
        sent = await send_post(bot, chat.id, d)
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        await reply_to.answer(f"❌ Не удалось отправить:\n<code>{html.escape(e.message)}</code>\n\n"
                              "Проверь, что бот добавлен в чат и может публиковать сообщения.")
        return
    await state.clear()
    title = chat.title or chat.full_name or str(chat.id)
    db.add_sent(user_id, chat.id, title, sent.message_id, d)
    link = post_link(chat, sent.message_id)
    rows = [[B(text="🔗 Открыть пост", url=link)]] if link else []
    rows.append([cb("🗂 Отправленные", "sl"), BACK])
    await reply_to.answer(f"✅ Отправлено в «{html.escape(title)}».\nЕго можно будет "
                          "отредактировать в «🗂 Отправленные».", reply_markup=kb(*rows))


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
    if re.fullmatch(r"#\d+", s):
        t = db.get_template(uid, int(s[1:]))
        tpls = [t] if t else []
    else:
        tpls = db.find_templates(uid, s)
    results = []
    for t in tpls:
        try:
            results.append(inline_result(t))
        except Exception:  # noqa: BLE001 — битый шаблон не должен ломать выдачу
            continue
    await q.answer(results, cache_time=0, is_personal=True,
                   button=None if results else InlineQueryResultsButton(
                       text="Создать шаблон", start_parameter="new"))


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
