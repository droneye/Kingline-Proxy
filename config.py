# config.py
import os
import logging
from typing import List, Dict, Any

from dotenv import load_dotenv
from db import SubscriptionDB

load_dotenv()

# ======================= #
#   БАЗОВЫЕ НАСТРОЙКИ     #
# ======================= #

BOT_TOKEN = (os.getenv("BOT_TOKEN", "") or "").strip()
LOG_LEVEL = (os.getenv("LOG_LEVEL", "INFO") or "INFO").upper()
ADMIN_ID = int((os.getenv("ADMIN_ID", "0") or "0").strip() or 0)

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN не указан в .env")

# ======================= #
#   СБОР ВСЕХ ПАНЕЛЕЙ     #
# ======================= #

def collect_xui_panels() -> List[Dict[str, str]]:
    """
    Новый формат (рекомендуемый):

      # перечень тегов (какие собирать)
      XUI_TAGS=LT,NL

      # 3x-ui API Configuration for LT
      XUI_LT_URL=https://vpn.kingline.online
      XUI_LT_USERNAME=...
      XUI_LT_PASSWORD=...
      XUI_LT_WEBBASEPATH=        (опционально)

      # 3x-ui API Configuration for NL
      XUI_NL_URL=https://net.kingline.online
      XUI_NL_USERNAME=...
      XUI_NL_PASSWORD=...
      XUI_NL_WEBBASEPATH=        (опционально)

    Фолбэк формат (нумерованный):
      XUI_URL_1=...
      XUI_USERNAME_1=...
      XUI_PASSWORD_1=...
      XUI_WEBBASEPATH_1=...      (опционально)

    Возвращает:
      [{"tag": "...", "url": "...", "username": "...", "password": "...", "webbasepath": "..."}, ...]
    """
    panels: List[Dict[str, str]] = []

    # --- 1) Именованные теги (LT, NL, ...) ---
    tags_env = (os.getenv("XUI_TAGS", "") or "").strip()
    if tags_env:
        tags = [t.strip().upper() for t in tags_env.split(",") if t.strip()]
    else:
        # дефолт: если не указали XUI_TAGS — пробуем LT и NL
        tags = ["LT", "NL"]

    for tag in tags:
        url = (os.getenv(f"XUI_{tag}_URL", "") or "").strip().rstrip("/")
        username = (os.getenv(f"XUI_{tag}_USERNAME", "") or "").strip()
        password = (os.getenv(f"XUI_{tag}_PASSWORD", "") or "").strip()
        webbasepath = (os.getenv(f"XUI_{tag}_WEBBASEPATH", "") or "").strip()

        # если блок вообще не задан — пропускаем
        if not url and not username and not password and not webbasepath:
            continue

        # если начали задавать — требуем всё обязательное
        if not url:
            raise SystemExit(f"❌ В .env отсутствует XUI_{tag}_URL")
        if not username:
            raise SystemExit(f"❌ В .env отсутствует XUI_{tag}_USERNAME")
        if not password:
            raise SystemExit(f"❌ В .env отсутствует XUI_{tag}_PASSWORD")

        panels.append(
            {
                "tag": tag,
                "url": url,
                "username": username,
                "password": password,
                "webbasepath": webbasepath,
            }
        )

    # --- 2) Фолбэк: нумерованные панели ---
    if not panels:
        i = 1
        while True:
            url = (os.getenv(f"XUI_URL_{i}", "") or "").strip().rstrip("/")
            username = (os.getenv(f"XUI_USERNAME_{i}", "") or "").strip()
            password = (os.getenv(f"XUI_PASSWORD_{i}", "") or "").strip()
            webbasepath = (os.getenv(f"XUI_WEBBASEPATH_{i}", "") or "").strip()

            if not url and not username and not password and not webbasepath:
                break  # панелей больше нет

            if not url:
                raise SystemExit(f"❌ В .env отсутствует XUI_URL_{i}")
            if not username:
                raise SystemExit(f"❌ В .env отсутствует XUI_USERNAME_{i}")
            if not password:
                raise SystemExit(f"❌ В .env отсутствует XUI_PASSWORD_{i}")

            panels.append(
                {
                    "tag": f"S{i}",
                    "url": url,
                    "username": username,
                    "password": password,
                    "webbasepath": webbasepath,
                }
            )
            i += 1

    if not panels:
        raise SystemExit(
            "❌ Не найдено ни одной XUI панели в .env.\n"
            "Ожидаю XUI_TAGS + XUI_<TAG>_URL/USERNAME/PASSWORD (например LT/NL) "
            "или XUI_URL_1/XUI_USERNAME_1/XUI_PASSWORD_1."
        )

    return panels


# Список всех панелей
XUI_PANELS: List[Dict[str, str]] = collect_xui_panels()

# Совместимость с кодом, который ожидает "XUI_SERVERS"
XUI_SERVERS: List[Dict[str, str]] = XUI_PANELS

# Первая панель — по умолчанию (для логов/дефолтных действий)
XUI_URL = XUI_PANELS[0]["url"]

# ======================= #
#     INBOUND IDS         #
# ======================= #

INBOUND_IDS_ENV = (os.getenv("INBOUND_IDS", "4,5,6") or "4,5,6").strip()
INBOUND_IDS: List[int] = [int(x) for x in INBOUND_IDS_ENV.split(",") if x.strip().isdigit()]
if not INBOUND_IDS:
    raise SystemExit("❌ INBOUND_IDS пуст. Пример: INBOUND_IDS=4,5,6")

# ======================= #
#     SUBSCRIPTION URL    #
# ======================= #

SUB_BASE = (os.getenv("SUB_BASE", "") or "").strip()
if not SUB_BASE:
    # по умолчанию берём домен 1-й панели (URL уже без /panel)
    SUB_BASE = XUI_URL.rstrip("/") + "/sub/"
SUB_BASE = SUB_BASE.rstrip("/") + "/"

XUI_SUB_SUFFIX = (os.getenv("XUI_SUB_SUFFIX", "") or "").strip()

# ======================= #
#      YooKassa           #
# ======================= #

YK_SHOP_ID = (os.getenv("YK_SHOP_ID", "") or "").strip()
YK_SECRET_KEY = (os.getenv("YK_SECRET_KEY", "") or "").strip()
YK_RETURN_URL = (os.getenv("YK_RETURN_URL", "") or "").strip()

# ======================= #
#          DB             #
# ======================= #

DB_PATH = (os.getenv("DB_PATH", "vpn_bot.db") or "vpn_bot.db").strip()
db = SubscriptionDB(DB_PATH)

# ======================= #
#     БИЛЛИНГ ТАРИФА      #
# ======================= #

FULL_TARIFF_PRICE = int((os.getenv("FULL_TARIFF_PRICE", "300") or "300").strip())

# ======================= #
#      Logging setup      #
# ======================= #

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("vpn-bot")

# Вывод конфигурации
logger.info("🔥 Загружено панелей: %s", len(XUI_PANELS))
for idx, p in enumerate(XUI_PANELS, start=1):
    tag = p.get("tag", f"#{idx}")
    wb = p.get("webbasepath") or ""
    wb_txt = f" (webbasepath={wb})" if wb else ""
    logger.info("  Панель %s [%s] → %s%s", idx, tag, p["url"], wb_txt)

logger.info("INBOUND_IDS=%s", INBOUND_IDS)
logger.info("SUB_BASE=%s", SUB_BASE)
logger.info("DB_PATH=%s", DB_PATH)
logger.info("ADMIN_ID=%s", ADMIN_ID)
