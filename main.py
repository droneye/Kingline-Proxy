# main.py
from __future__ import annotations

import sys
import os
import threading
import asyncio
import time
import logging
from typing import Optional, Tuple

# Добавляем путь к корневой папке в PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from telegram.ext import ApplicationBuilder, ContextTypes

from config import BOT_TOKEN, logger, ADMIN_ID
from handlers import register_handlers

from dns.dns_server import start_dns_server_in_background, stop_dns_server
from sub_proxy import start_sub_proxy_in_background, stop_sub_proxy


# =========================
# TrafficMonitor (optional)
# =========================
TrafficMonitor = None
TRAFFIC_MONITOR_AVAILABLE = False

try:
    from dns.traffic_monitor import TrafficMonitor  # type: ignore
    TRAFFIC_MONITOR_AVAILABLE = True
    logger.info("✅ TrafficMonitor успешно импортирован")
except ImportError as e:
    logger.warning("TrafficMonitor не доступен: %s", e)
    logger.warning("Продолжаем работу без мониторинга трафика")
except Exception as e:
    logger.error("Неожиданная ошибка при импорте TrafficMonitor: %s", e)


# =========================
# Globals: services handles
# =========================
_traffic_monitor = None
_traffic_thread: Optional[threading.Thread] = None

_dns_server = None
_dns_thread: Optional[threading.Thread] = None

_sub_proxy_server = None
_sub_proxy_thread: Optional[threading.Thread] = None

# Логгер для DNS-компонентов (если нужно отдельно настроить)
dns_logger = logging.getLogger("dns")
dns_logger.setLevel(logging.INFO)


# =========================
# TrafficMonitor thread
# =========================
def _monitor_thread_target(monitor_instance) -> None:
    """
    Запускает internal background loop TrafficMonitor внутри собственного event loop в этом потоке.
    """
    try:
        logger.info("Запуск TrafficMonitor в отдельном потоке...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(monitor_instance._background_loop())  # noqa: SLF001
    except Exception as e:
        logger.exception("TrafficMonitor thread exited with exception: %s", e)
    finally:
        logger.info("Поток TrafficMonitor завершен")


def start_traffic_monitor(poll_interval: float = 60.0, on_cycle_complete=None) -> bool:
    """
    Запускает TrafficMonitor в отдельном демоническом потоке.
    Возвращает True если успешно запущен, False в случае ошибки.
    """
    global _traffic_monitor, _traffic_thread

    if not TRAFFIC_MONITOR_AVAILABLE:
        logger.warning("TrafficMonitor недоступен, пропускаем запуск")
        return False

    if _traffic_monitor is not None and _traffic_thread is not None and _traffic_thread.is_alive():
        logger.info("TrafficMonitor уже запущен")
        return True

    try:
        _traffic_monitor = TrafficMonitor(poll_interval=poll_interval, on_cycle_complete=on_cycle_complete)
        _traffic_thread = threading.Thread(
            target=_monitor_thread_target,
            args=(_traffic_monitor,),
            name="traffic-monitor-thread",
            daemon=True,
        )
        _traffic_thread.start()

        # Даем время на инициализацию
        time.sleep(1.5)

        if _traffic_thread.is_alive():
            logger.info("✅ TrafficMonitor успешно запущен в отдельном потоке")
            return True

        logger.error("❌ Поток TrafficMonitor не запустился")
        _traffic_monitor = None
        _traffic_thread = None
        return False

    except Exception as e:
        logger.exception("Ошибка при запуске TrafficMonitor: %s", e)
        _traffic_monitor = None
        _traffic_thread = None
        return False


def stop_traffic_monitor_sync(timeout: float = 10.0) -> None:
    """
    Синхронная версия остановки TrafficMonitor.
    """
    global _traffic_monitor, _traffic_thread

    if _traffic_monitor is None:
        logger.info("TrafficMonitor не запущен, пропускаем остановку")
        return

    logger.info("Остановка TrafficMonitor...")

    try:
        # Устанавливаем флаг остановки (внутренний протокол твоего TrafficMonitor)
        _traffic_monitor._stop = True  # noqa: SLF001
    except Exception as e:
        logger.exception("Ошибка при установке флага остановки TrafficMonitor: %s", e)

    if _traffic_thread and _traffic_thread.is_alive():
        logger.info("Ожидание завершения TrafficMonitor (до %s сек)...", timeout)
        _traffic_thread.join(timeout)

        if _traffic_thread.is_alive():
            logger.warning("TrafficMonitor поток не завершился за %s сек", timeout)
        else:
            logger.info("✅ TrafficMonitor успешно остановлен")

    _traffic_monitor = None
    _traffic_thread = None


# =========================
# Start/stop services
# =========================
def start_services() -> bool:
    """
    Запуск всех фоновых сервисов (DNS, Sub-proxy, TrafficMonitor).
    Возвращает True если всё критичное поднялось.
    """
    global _dns_server, _dns_thread, _sub_proxy_server, _sub_proxy_thread

    services_ok = True

    # --- DNS ---
    try:
        logger.info("Запуск DNS-сервера...")
        _dns_server, _dns_thread = start_dns_server_in_background()
        if _dns_thread and _dns_thread.is_alive():
            logger.info("✅ DNS-сервер успешно запущен в фоне")
        else:
            logger.error("❌ DNS-сервер не запустился")
            services_ok = False
    except Exception as e:
        logger.exception("Не удалось запустить DNS-сервер: %s", e)
        services_ok = False

    # --- Sub-proxy ---
    try:
        logger.info("Запуск sub-proxy...")
        _sub_proxy_server, _sub_proxy_thread = start_sub_proxy_in_background()
        if _sub_proxy_thread and _sub_proxy_thread.is_alive():
            logger.info("✅ Sub-proxy успешно запущен в фоне")
        else:
            logger.error("❌ Sub-proxy не запустился")
            # sub-proxy обычно не критичен для бота — оставим как деградацию
    except Exception as e:
        logger.exception("Не удалось запустить sub-proxy: %s", e)

    # --- TrafficMonitor (optional) ---
    if TRAFFIC_MONITOR_AVAILABLE:
        try:
            logger.info("Запуск TrafficMonitor...")
            if start_traffic_monitor(poll_interval=60.0, on_cycle_complete=None):
                logger.info("✅ TrafficMonitor успешно запущен")
            else:
                logger.warning("⚠️ TrafficMonitor не запустился (не критично)")
        except Exception as e:
            logger.exception("Не удалось запустить TrafficMonitor: %s", e)
    else:
        logger.warning("⚠️ TrafficMonitor недоступен, работаем без мониторинга трафика")

    return services_ok


def stop_services_sync() -> None:
    """
    Синхронная остановка всех сервисов.
    """
    logger.info("Остановка всех сервисов...")

    # TrafficMonitor
    if TRAFFIC_MONITOR_AVAILABLE:
        try:
            stop_traffic_monitor_sync(timeout=10.0)
        except Exception as e:
            logger.exception("Ошибка при остановке TrafficMonitor: %s", e)

    # DNS
    try:
        stop_dns_server()
        logger.info("✅ DNS-сервер остановлен")
    except Exception as e:
        logger.exception("Ошибка при остановке DNS-сервера: %s", e)

    # Sub-proxy
    try:
        stop_sub_proxy()
        logger.info("✅ Sub-proxy остановлен")
    except Exception as e:
        logger.exception("Ошибка при остановке sub-proxy: %s", e)


# =========================
# PTB error handler
# =========================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Глобальный ловец ошибок: пишем в лог и (опционально) шлём админу.
    """
    logger.exception("Unhandled error: %s", context.error)

    # Не допускаем рекурсий/падений внутри error_handler
    try:
        if ADMIN_ID:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ Ошибка бота: {context.error!r}")
    except Exception:
        pass


# =========================
# PTB post_init / post_stop
# =========================
async def post_init(application) -> None:
    """
    Вызывается после инициализации бота.
    Тут безопасно запускать JobQueue-нотифаеры, т.к. app уже создан.
    """
    logger.info("Бот инициализирован")

    # 1) Стартуем SubscriptionNotifier (если модуль есть)
    try:
        from handlers.subscription_notifier import SubscriptionNotifier  # type: ignore
        # IMPORTANT: если у тебя ensure_started называется иначе — поменяй строку ниже.
        # Я предполагаю, что ты сделаешь метод ensure_started(application) или ensure_job(context).
        # Чтобы не падать — завернул в try.
        if hasattr(SubscriptionNotifier, "ensure_started"):
            SubscriptionNotifier.ensure_started(application)  # type: ignore[attr-defined]
        elif hasattr(SubscriptionNotifier, "_ensure_job"):
            # fallback на твой старый вариант, где нужен context:
            # делаем "фейковый" context через job_queue нельзя, поэтому лучше иметь ensure_started(application).
            logger.warning("SubscriptionNotifier: нет ensure_started(application). Добавь для корректного автозапуска.")
        logger.info("✅ SubscriptionNotifier: проверен/запущен")
    except Exception as e:
        logger.warning("SubscriptionNotifier не запущен: %s", e)

    # 2) Отправляем админу статус
    try:
        if ADMIN_ID:
            services_status = []
            services_status.append("✅ DNS" if (_dns_thread and _dns_thread.is_alive()) else "❌ DNS")
            services_status.append("✅ Sub-proxy" if (_sub_proxy_thread and _sub_proxy_thread.is_alive()) else "⚠️ Sub-proxy")
            if TRAFFIC_MONITOR_AVAILABLE and _traffic_thread and _traffic_thread.is_alive():
                services_status.append("✅ TrafficMonitor")
            else:
                services_status.append("⚠️ TrafficMonitor")

            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text="🤖 Бот запущен!\nСтатус сервисов:\n" + "\n".join(services_status),
            )
    except Exception as e:
        logger.warning("Не удалось отправить уведомление админу: %s", e)


async def post_stop(application) -> None:
    """
    Вызывается при остановке Application.
    """
    logger.info("Остановка бота...")

    try:
        if ADMIN_ID:
            await application.bot.send_message(chat_id=ADMIN_ID, text="🛑 Бот остановлен")
    except Exception as e:
        logger.warning("Не удалось отправить уведомление админу об остановке: %s", e)


# =========================
# main
# =========================
def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN не указан")

    logger.info("🚀 Запуск бота и сервисов...")

    # Поднимаем сервисы ДО run_polling
    services_ok = start_services()
    if not services_ok:
        logger.error("❌ Не удалось запустить некоторые сервисы")
        # DNS обычно критичен для твоей схемы:
        if not (_dns_thread and _dns_thread.is_alive()):
            logger.error("❌ Критическая ошибка: DNS-сервер не запущен")
            stop_services_sync()
            return

    try:
        app = (
            ApplicationBuilder()
            .token(BOT_TOKEN)
            .post_init(post_init)
            .post_stop(post_stop)
            .build()
        )

        register_handlers(app)
        app.add_error_handler(error_handler)

        logger.info("Бот запускается (long polling)…")

        app.run_polling(
            allowed_updates=[
                "message",
                "callback_query",
                "my_chat_member",
                "chat_member",
            ],
            drop_pending_updates=True,
            close_loop=False,
        )

    except KeyboardInterrupt:
        logger.info("Получен KeyboardInterrupt")
    except Exception as e:
        logger.exception("Ошибка при работе бота: %s", e)
    finally:
        logger.info("Останавливаем сервисы...")
        stop_services_sync()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Завершение работы по запросу пользователя")
        sys.exit(0)
    except Exception as e:
        logger.exception("Неожиданная ошибка: %s", e)
        sys.exit(1)
