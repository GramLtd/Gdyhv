import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "")
PORT = int(os.getenv("PORT", "8000"))
DB_PATH = os.getenv("DB_PATH", "game.db")

# Админы: по username (без @) и/или по числовому ID. ID надёжнее — username можно сменить.
ADMIN_USERNAMES = {u.strip().lstrip("@").lower() for u in
                   os.getenv("ADMIN_USERNAMES", "ibuybalenciaga").split(",") if u.strip()}
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

# --- TON-пополнения ---
TON_WALLET = os.getenv("TON_WALLET", "")              # кошелёк проекта для приёма TON
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "")  # необязательно, снимает лимит 1 rps
TONCENTER_URL = os.getenv("TONCENTER_URL", "https://toncenter.com/api/v2")
XMR_PER_TON = 0.012         # курс по умолчанию; меняется в админке
MIN_TON_DEPOSIT = 0.5
XMR_PER_STAR = 0.00004      # курс пополнения звёздами; меняется в админке

# --- Экономика игры (хешрейт RandomX в KH/s, баланс в XMR) ---
COIN = "XMR"
COIN_PER_GH_PER_SEC = 5e-9  # XMR в секунду за 1 KH/s
STORAGE_HOURS = 4           # сколько часов копится без "Забрать"
REF_BONUS_COINS = 0.0005    # бонус пригласившему
REF_HASH_BONUS = 0.10       # +10% к хешрейту за каждого друга (макс. ниже)
REF_HASH_BONUS_MAX = 1.0
DAILY_BONUS = 0.0002
MIN_WITHDRAW = 0.05         # по умолчанию; меняется в админке
DEFAULT_WELCOME = (
    "<b>Добро пожаловать в XMR Miner!</b>\n\n"
    "Ферма на RandomX майнит Monero, пока вы отдыхаете. Забирайте {coin}, "
    "пополняйте баланс звёздами или TON и выводите на свой Monero-кошелёк."
)

# Уровни улучшения: (хешрейт KH/s, цена в XMR)
LEVELS = [
    (1, 0), (1.6, 0.0005), (2.6, 0.0015), (4.2, 0.004), (6.8, 0.01),
    (11, 0.025), (18, 0.06), (29, 0.15), (47, 0.35), (76, 0.8),
]

# Магазин за Telegram Stars (XTR). id: (название, описание, цена в звёздах, раздел)
SHOP = {
    "boost2":    ("Буст ×2 · 24 ч", "Удваивает скорость майнинга на сутки", 500, "boost"),
    "boost3":    ("Буст ×3 · 24 ч", "Утраивает скорость майнинга на сутки", 750, "boost"),
    "level":     ("+1 уровень", "Следующий уровень фермы без оплаты монетами", 100, "boost"),
    "storage12": ("Хранилище 12 ч", "Монеты копятся до 12 часов без захода · навсегда", 250, "storage"),
    "storage24": ("Хранилище 24 ч", "Монеты копятся целые сутки · навсегда", 450, "storage"),
    "skin_gold":    ("Золото", "Скин кулера", 150, "skin"),
    "skin_emerald": ("Изумруд", "Скин кулера", 150, "skin"),
    "skin_crimson": ("Рубин", "Скин кулера", 150, "skin"),
    "skin_ice":     ("Лёд", "Скин кулера", 150, "skin"),
    "skin_black":   ("Карбон", "Скин кулера", 300, "skin"),
}
STORAGE_ITEMS = {"storage12": 12, "storage24": 24}

# Пакеты пополнения баланса звёздами: id -> количество Stars (XMR считается по курсу из админки)
STAR_PACKS = {"pack100": 100, "pack500": 500, "pack1000": 1000, "pack2500": 2500}
