import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "postbot.db")
# Через запятую. Пусто — ботом может пользоваться любой (у каждого свои шаблоны).
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
# Часовой пояс для ввода времени таймера
TZ_NAME = os.getenv("TZ_NAME", "Europe/Moscow")
# Отдельный бот-магазин для пользователей (покупка доступа к шаблонам). Пусто — магазин внутри основного бота.
SHOP_BOT_TOKEN = os.getenv("SHOP_BOT_TOKEN", "")
