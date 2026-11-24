"""Сборка поста: клавиатура, отправка, редактирование, inline-результаты."""
from aiogram import Bot
from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedDocument,
    InlineQueryResultCachedGif,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InputMediaAnimation,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    LinkPreviewOptions,
    SwitchInlineQueryChosenChat,
    WebAppInfo,
)

import html as _html
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import db
from config import TZ_NAME

TZ = ZoneInfo(TZ_NAME)


def fmt_left(sec: int) -> str:
    if sec <= 0:
        return "0:00"
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return (f"{d} д {h:02d}:{m:02d}" if d else f"{h:02d}:{m:02d}")


def render_text(d: dict) -> str:
    """Подставляет таймер вместо {timer}."""
    text = d.get("html") or ""
    t = d.get("timer")
    if not t or "{timer}" not in text:
        return text
    left = int(t["until"] - time.time())
    if t.get("mode") == "native":
        when = datetime.fromtimestamp(t["until"], TZ).strftime("%d.%m.%Y %H:%M")
        rep = f'<tg-time unix="{t["until"]}" format="r">{when}</tg-time>'
        if left <= 0 and t.get("done"):
            rep = _html.escape(t["done"])
    elif left <= 0:
        rep = _html.escape(t.get("done") or "время вышло")
    else:
        rep = fmt_left(left)
    return text.replace("{timer}", rep)

# тип кнопки -> (подпись в меню, что спрашиваем у пользователя)
BUTTON_TYPES = {
    "url": ("🔗 Ссылка", "Отправь ссылку (https://…, t.me/…, tg://…)"),
    "webapp": ("📱 Mini App (Web App)", "Отправь https-ссылку Mini App.\n"
               "⚠️ Работает только в личке с ботом. В каналах/группах используй "
               "обычную ссылку вида t.me/bot/app — она тоже откроет Mini App."),
    "alert": ("💬 Всплывающее окно", "Отправь текст окна (до 200 символов)"),
    "toast": ("🔔 Уведомление сверху", "Отправь текст уведомления (до 200 символов)"),
    "copy": ("📋 Копировать текст", "Отправь текст, который скопируется (до 256 символов)"),
    "stars": ("⭐ Запрос звёзд (оплата Stars)",
              "Сколько звёзд запросить? (1–10000)\nМожно добавить описание через «|»: "
              "<code>50 | Поддержать канал</code>"),
    "gift": ("🎁 Запрос подарка Telegram", ""),
    "page": ("🔄 Сменить пост (новый текст и кнопки)", ""),
    "inline": ("↗️ Inline-запрос в другой чат", "Отправь текст запроса (можно пустым: «-»)"),
    "inline_here": ("🔎 Inline-запрос в этом чате", "Отправь текст запроса (можно пустым: «-»)"),
    "inline_pick": ("👥 Выбор чата (пользователи/группы/каналы)",
                    "Отправь текст запроса (можно пустым: «-»)"),
}

# Готовые ссылки на разделы и меню Telegram. {x} — подставляется значение пользователя.
LINK_PRESETS = [
    ("👤 Профиль / канал / бот", "https://t.me/{x}", "username без @"),
    ("✉️ Написать в ЛС с текстом", "https://t.me/{x}", "username?text=Привет"),
    ("🤖 Бот со /start параметром", "https://t.me/{x}", "botname?start=ref123"),
    ("➕ Добавить бота в группу", "https://t.me/{x}?startgroup=start", "username бота"),
    ("📢 Добавить бота в канал", "https://t.me/{x}?startchannel&admin=post_messages", "username бота"),
    ("🔗 Поделиться ссылкой", "https://t.me/share/url?url={x}", "ссылка, которой делиться"),
    ("🎟 Пригласительная ссылка", "https://t.me/+{x}", "хеш после t.me/+"),
    ("🧩 Стикерпак", "https://t.me/addstickers/{x}", "имя набора"),
    ("✨ Набор премиум-эмодзи", "https://t.me/addemoji/{x}", "имя набора"),
    ("🚀 Буст канала", "https://t.me/boost/{x}", "username канала"),
    ("📱 Mini App по ссылке", "https://t.me/{x}", "botname/appname"),
    ("💬 Сообщение / пост", "https://t.me/{x}", "channel/123"),
    ("📞 Номер телефона", "https://t.me/{x}", "+79990000000"),
    ("🎨 Тема оформления", "https://t.me/addtheme/{x}", "имя темы"),
    ("🌐 Язык интерфейса", "https://t.me/setlanguage/{x}", "код пакета, напр. ru"),
    ("🔐 Прокси", "https://t.me/proxy?{x}", "server=..&port=..&secret=.."),
    ("⚙️ Настройки Telegram", "tg://settings", None),
    ("⭐ Telegram Premium", "tg://premium_offer", None),
    ("🎁 Подарить Premium", "tg://premium_multigift", None),
    ("💫 Telegram Stars", "tg://stars", None),
    ("📂 Папки чатов", "tg://settings/folders", None),
    ("🔒 Конфиденциальность", "tg://settings/privacy", None),
    ("🔔 Уведомления", "tg://settings/notifications", None),
    ("🎨 Оформление", "tg://settings/themes", None),
    ("🔎 Поиск по Telegram", "tg://search?query={x}", "что искать"),
]

STYLES = {None: "⚪ Обычная", "primary": "🔵 Синяя", "success": "🟢 Зелёная", "danger": "🔴 Красная"}


def build_button(b: dict) -> InlineKeyboardButton:
    kw = {"text": b["text"]}
    if b.get("style"):
        kw["style"] = b["style"]
    if b.get("icon"):
        kw["icon_custom_emoji_id"] = b["icon"]
    t, v = b["type"], b.get("value") or ""
    if t == "url":
        kw["url"] = v
    elif t == "webapp":
        kw["web_app"] = WebAppInfo(url=v)
    elif t in ("alert", "toast"):
        db.put_popup(b["uid"], v, t == "alert")
        kw["callback_data"] = f"pp:{b['uid']}"
    elif t == "page":
        kw["callback_data"] = f"pg:{v}"
    elif t in ("stars", "gift"):
        kw["url"] = b["link"]
    elif t == "copy":
        kw["copy_text"] = CopyTextButton(text=v)
    elif t == "inline":
        kw["switch_inline_query"] = v
    elif t == "inline_here":
        kw["switch_inline_query_current_chat"] = v
    elif t == "inline_pick":
        kw["switch_inline_query_chosen_chat"] = SwitchInlineQueryChosenChat(
            query=v, allow_user_chats=True, allow_bot_chats=True,
            allow_group_chats=True, allow_channel_chats=True)
    return InlineKeyboardButton(**kw)


def build_markup(d: dict, plain: bool = False) -> InlineKeyboardMarkup | None:
    """plain=True — без стилей/иконок (запасной вариант, если боту они недоступны)."""
    rows = []
    for row in d.get("buttons") or []:
        r = []
        for b in row:
            if plain:
                b = {**b, "style": None, "icon": None}
            r.append(build_button(b))
        if r:
            rows.append(r)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def preview_opts(d: dict) -> LinkPreviewOptions:
    return LinkPreviewOptions(is_disabled=bool(d.get("no_preview")),
                              show_above_text=bool(d.get("preview_above")))


async def send_post(bot: Bot, chat_id: int, d: dict, plain: bool = False):
    markup = build_markup(d, plain)
    text = render_text(d)
    m = d.get("media")
    if m:
        common = dict(chat_id=chat_id, caption=text or None, parse_mode="HTML",
                      reply_markup=markup, show_caption_above_media=bool(d.get("preview_above")))
        if m["type"] == "photo":
            return await bot.send_photo(photo=m["file_id"], **common)
        if m["type"] == "video":
            return await bot.send_video(video=m["file_id"], **common)
        if m["type"] == "animation":
            return await bot.send_animation(animation=m["file_id"], **common)
        common.pop("show_caption_above_media")
        return await bot.send_document(document=m["file_id"], **common)
    if not text:
        text = "‎"  # невидимый символ — пост только из кнопок
    return await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup,
                                  link_preview_options=preview_opts(d))


def _input_media(d: dict):
    m = d["media"]
    cls = {"photo": InputMediaPhoto, "video": InputMediaVideo,
           "animation": InputMediaAnimation, "document": InputMediaDocument}[m["type"]]
    kw = dict(media=m["file_id"], caption=render_text(d) or None, parse_mode="HTML")
    if m["type"] != "document":
        kw["show_caption_above_media"] = bool(d.get("preview_above"))
    return cls(**kw)


async def edit_post(bot: Bot, chat_id: int, message_id: int, old: dict, d: dict):
    markup = build_markup(d)
    if bool(old.get("media")) != bool(d.get("media")):
        raise ValueError("Нельзя превратить текстовый пост в пост с медиа (и наоборот) — "
                         "это ограничение Telegram. Отправь пост заново.")
    if d.get("media"):
        if old["media"] != d["media"]:
            return await bot.edit_message_media(chat_id=chat_id, message_id=message_id,
                                                media=_input_media(d), reply_markup=markup)
        return await bot.edit_message_caption(chat_id=chat_id, message_id=message_id,
                                              caption=render_text(d) or None, parse_mode="HTML",
                                              reply_markup=markup)
    return await bot.edit_message_text(text=render_text(d) or "‎", chat_id=chat_id,
                                       message_id=message_id, parse_mode="HTML",
                                       reply_markup=markup,
                                       link_preview_options=preview_opts(d))


def inline_result(tpl: dict):
    d, rid, title = tpl["data"], str(tpl["id"]), tpl["name"]
    markup = build_markup(d)
    text = render_text(d)
    m = d.get("media")
    if not m:
        return InlineQueryResultArticle(
            id=rid, title=title, description="Текстовый пост",
            input_message_content=InputTextMessageContent(
                message_text=text or "‎", parse_mode="HTML",
                link_preview_options=preview_opts(d)),
            reply_markup=markup)
    kw = dict(id=rid, title=title, caption=text or None, parse_mode="HTML", reply_markup=markup)
    if m["type"] == "photo":
        return InlineQueryResultCachedPhoto(photo_file_id=m["file_id"], **kw)
    if m["type"] == "video":
        return InlineQueryResultCachedVideo(video_file_id=m["file_id"], **kw)
    if m["type"] == "animation":
        return InlineQueryResultCachedGif(gif_file_id=m["file_id"], **kw)
    return InlineQueryResultCachedDocument(document_file_id=m["file_id"], **kw)


async def switch_in_place(bot: Bot, d: dict, *, chat_id=None, message_id=None,
                          inline_id=None, cur_has_media=None):
    """Превращает нажатое сообщение в пост d. Пустой текст без медиа — меняем только кнопки."""
    markup = build_markup(d)
    where = ({"inline_message_id": inline_id} if inline_id
             else {"chat_id": chat_id, "message_id": message_id})
    text = render_text(d)
    if not text and not d.get("media"):
        return await bot.edit_message_reply_markup(reply_markup=markup, **where)
    if d.get("media"):
        return await bot.edit_message_media(media=_input_media(d), reply_markup=markup, **where)
    if cur_has_media is None:  # inline-сообщение: не знаем, что внутри — пробуем оба
        try:
            return await bot.edit_message_text(text=text, parse_mode="HTML", reply_markup=markup,
                                               link_preview_options=preview_opts(d), **where)
        except Exception:  # noqa: BLE001
            cur_has_media = True
    if cur_has_media:
        return await bot.edit_message_caption(caption=text, parse_mode="HTML",
                                              reply_markup=markup, **where)
    return await bot.edit_message_text(text=text, parse_mode="HTML", reply_markup=markup,
                                       link_preview_options=preview_opts(d), **where)
