import asyncio
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, CallbackQuery, InlineQuery, Message

import db
from config import ADMIN_IDS, BOT_TOKEN
from handlers import router


class AccessMiddleware(BaseMiddleware):
    """Если задан ADMIN_IDS — пускаем только их. Кнопки-окна (pp:) работают для всех."""

    async def __call__(self, handler, event, data):
        if ADMIN_IDS:
            user = getattr(event, "from_user", None)
            is_popup = isinstance(event, CallbackQuery) and (event.data or "").startswith("pp:")
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
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
