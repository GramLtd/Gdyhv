import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "postbot.db")
# Через запятую. Пусто — ботом может пользоваться любой (у каждого свои шаблоны).
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
