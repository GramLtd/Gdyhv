import asyncio
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, CallbackQuery, InlineQuery, Message

import db
from config import ADMIN_IDS, BOT_TOKEN
from handlers import router
from render import edit_post, render_text  # noqa: F401


async def timer_loop(bot: Bot):
    """Раз в минуту обновляет живые таймеры в отправленных постах."""
    import time
    from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
    while True:
        await asyncio.sleep(60 - time.time() % 60 + 1)
        for row, d in db.sent_with_live_timer():
            expired = d["timer"]["until"] <= time.time()
            try:
                await edit_post(bot, row["chat_id"], row["message_id"], d, d)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramBadRequest as e:
                if "not modified" not in e.message:
                    expired = True  # пост удалён или недоступен — больше не трогаем
            except Exception:  # noqa: BLE001
                logging.exception("timer edit failed")
            if expired:
                d["timer"]["finished"] = True
                db.set_sent_data(row["id"], d)
            await asyncio.sleep(0.1)


class AccessMiddleware(BaseMiddleware):
    """Если задан ADMIN_IDS — пускаем только их. Кнопки-окна (pp:) работают для всех."""

    async def __call__(self, handler, event, data):
        if ADMIN_IDS:
            user = getattr(event, "from_user", None)
            is_popup = (isinstance(event, CallbackQuery) and (event.data or "").startswith(("pp:", "pg:"))) \
                or (isinstance(event, Message) and event.successful_payment is not None)
            if not is_popup and (user is None or user.id not in ADMIN_IDS):
                if isinstance(event, Message) and event.chat.type == "private":
                    await event.answer("⛔ Нет доступа.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("Нет доступа", show_alert=True)
                elif isinstance(event, InlineQuery):
                    await event.answer([], cache_time=60, is_personal=True)
                return None
        return await handler(event, data)


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("Укажи BOT_TOKEN в .env")
    db.conn()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    for obs in (dp.message, dp.callback_query, dp.inline_query):
        obs.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="post", description="Редактор поста"),
        BotCommand(command="new", description="Новый пост"),
        BotCommand(command="templates", description="Шаблоны"),
        BotCommand(command="sent", description="Отправленные посты"),
        BotCommand(command="emoji", description="ID премиум-эмодзи"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="cancel", description="Отменить ввод"),
    ])
    asyncio.create_task(timer_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
