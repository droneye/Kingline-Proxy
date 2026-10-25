# main.py
from __future__ import annotations

import sys
import os
import threading
import asyncio
import time
import logging
from typing import Optional

# Добавляем путь к корневой папке в PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import Application, ApplicationBuilder, ContextTypes
from diagnostics import configure, event, exception_fields, timed_async, watch_event_loop

from config import BOT_TOKEN, logger, ADMIN_ID
from handlers import register_handlers

from dns.dns_server import start_dns_server_in_background, stop_dns_server
from sub_proxy import start_sub_proxy_in_background, stop_sub_proxy

MINI_APP_URL = (
    os.getenv("KINGLINE_SITE_URL", "https://kingline.online").strip().rstrip("/")
    or "https://kingline.online"
)

TELEGRAM_CONNECT_TIMEOUT = float(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "10"))
TELEGRAM_READ_TIMEOUT = float(os.getenv("TELEGRAM_READ_TIMEOUT", "20"))
TELEGRAM_WRITE_TIMEOUT = float(os.getenv("TELEGRAM_WRITE_TIMEOUT", "20"))
TELEGRAM_POOL_TIMEOUT = float(os.getenv("TELEGRAM_POOL_TIMEOUT", "10"))
TELEGRAM_GET_UPDATES_TIMEOUT = int(os.getenv("TELEGRAM_GET_UPDATES_TIMEOUT", "10"))
TELEGRAM_GET_UPDATES_READ_TIMEOUT = float(
    os.getenv("TELEGRAM_GET_UPDATES_READ_TIMEOUT", "18")
)
TELEGRAM_OPTIONAL_CALL_TIMEOUT = float(os.getenv("TELEGRAM_OPTIONAL_CALL_TIMEOUT", "5"))

# httpx logs include full Bot API URLs. Keep Telegram tokens out of journald.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


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

dns_logger = logging.getLogger("dns")
dns_logger.setLevel(logging.INFO)
_diagnostics_loop_task = None


class MonitoredApplication(Application):
    @timed_async("telegram.update", slow_ms=2000)
    async def process_update(self, update):
        return await super().process_update(update)


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
        _traffic_monitor = TrafficMonitor(
            poll_interval=poll_interval,
            on_cycle_complete=on_cycle_complete,
        )
        _traffic_thread = threading.Thread(
            target=_monitor_thread_target,
            args=(_traffic_monitor,),
            name="traffic-monitor-thread",
            daemon=True,
        )
        _traffic_thread.start()

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
    Запуск всех фоновых сервисов: DNS, Sub-proxy, TrafficMonitor.
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
    except Exception as e:
        logger.exception("Не удалось запустить sub-proxy: %s", e)

    # --- TrafficMonitor ---
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

    if TRAFFIC_MONITOR_AVAILABLE:
        try:
            stop_traffic_monitor_sync(timeout=10.0)
        except Exception as e:
            logger.exception("Ошибка при остановке TrafficMonitor: %s", e)

    try:
        stop_dns_server()
        logger.info("✅ DNS-сервер остановлен")
    except Exception as e:
        logger.exception("Ошибка при остановке DNS-сервера: %s", e)

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
    Глобальный ловец ошибок.
    """
    logger.exception("Unhandled error: %s", context.error)
    if context.error is not None:
        event("telegram_error", "ERROR", **exception_fields(context.error))

    try:
        if ADMIN_ID:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ Ошибка бота: {context.error!r}",
            )
    except Exception:
        pass


# =========================
# PTB post_init / post_stop
# =========================
async def post_init(application) -> None:
    """
    Вызывается после инициализации бота.
    Тут безопасно запускать JobQueue-задачи.
    """
    logger.info("Бот инициализирован")
    global _diagnostics_loop_task
    _diagnostics_loop_task = asyncio.create_task(watch_event_loop(), name="diagnostics-loop-lag")

    try:
        await asyncio.wait_for(
            application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Кабинет",
                    web_app=WebAppInfo(url=MINI_APP_URL),
                )
            ),
            timeout=TELEGRAM_OPTIONAL_CALL_TIMEOUT,
        )
        logger.info("✅ Telegram Mini App menu button настроен: %s", MINI_APP_URL)
    except Exception as e:
        logger.warning("Не удалось настроить Telegram Mini App menu button: %s", e)

    # 1) Запускаем восстановимую проверку pending-платежей YooKassa
    try:
        from handlers.billing_topup import TopupService

        if application.job_queue is None:
            logger.warning(
                "JobQueue недоступен. Установи python-telegram-bot с extras: "
                "pip install 'python-telegram-bot[job-queue]'"
            )
        else:
            application.job_queue.run_repeating(
                TopupService.check_pending_payments_job,
                interval=60,
                first=10,
                name="yookassa_pending_payments_check",
            )
            logger.info("✅ YooKassa pending-check job запущен")
    except Exception as e:
        logger.exception("Не удалось запустить YooKassa pending-check job: %s", e)

    from handlers.subscription_delivery import retry_pending
    from handlers.subscription_service import retry_panel_sync
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            retry_pending, interval=15, first=5,
            name="subscription_receipt_delivery",
        )
        application.job_queue.run_repeating(
            retry_panel_sync, interval=300, first=30,
            name="subscription_optional_panel_sync",
        )

    # 2) Стартуем SubscriptionNotifier
    try:
        from handlers.subscription_notifier import SubscriptionNotifier  # type: ignore

        if hasattr(SubscriptionNotifier, "ensure_started"):
            SubscriptionNotifier.ensure_started(application)  # type: ignore[attr-defined]
        elif hasattr(SubscriptionNotifier, "_ensure_job"):
            logger.warning(
                "SubscriptionNotifier: нет ensure_started(application). "
                "Добавь для корректного автозапуска."
            )

        logger.info("✅ SubscriptionNotifier: проверен/запущен")
    except Exception as e:
        logger.warning("SubscriptionNotifier не запущен: %s", e)

    # 3) Отправляем админу статус
    try:
        if ADMIN_ID:
            services_status = []
            services_status.append(
                "✅ DNS" if (_dns_thread and _dns_thread.is_alive()) else "❌ DNS"
            )
            services_status.append(
                "✅ Sub-proxy"
                if (_sub_proxy_thread and _sub_proxy_thread.is_alive())
                else "⚠️ Sub-proxy"
            )

            if TRAFFIC_MONITOR_AVAILABLE and _traffic_thread and _traffic_thread.is_alive():
                services_status.append("✅ TrafficMonitor")
            else:
                services_status.append("⚠️ TrafficMonitor")

            services_status.append("✅ YooKassa pending-check")

            await asyncio.wait_for(
                application.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="🤖 Бот запущен!\nСтатус сервисов:\n" + "\n".join(services_status),
                ),
                timeout=TELEGRAM_OPTIONAL_CALL_TIMEOUT,
            )
    except Exception as e:
        logger.warning("Не удалось отправить уведомление админу: %s", e)


async def post_stop(application) -> None:
    """
    Вызывается при остановке Application.
    """
    logger.info("Остановка бота...")
    if _diagnostics_loop_task is not None:
        _diagnostics_loop_task.cancel()
        await asyncio.gather(_diagnostics_loop_task, return_exceptions=True)

    try:
        if ADMIN_ID:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text="🛑 Бот остановлен",
            )
    except Exception as e:
        logger.warning("Не удалось отправить уведомление админу об остановке: %s", e)


# =========================
# main
# =========================
def main() -> None:
    configure("bot")
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN не указан")

    logger.info("🚀 Запуск бота и сервисов...")

    services_ok = start_services()
    if not services_ok:
        logger.error("❌ Не удалось запустить некоторые сервисы")

        if not (_dns_thread and _dns_thread.is_alive()):
            logger.error("❌ Критическая ошибка: DNS-сервер не запущен")
            stop_services_sync()
            return

    try:
        app = (
            ApplicationBuilder()
            .application_class(MonitoredApplication)
            .token(BOT_TOKEN)
            .connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
            .read_timeout(TELEGRAM_READ_TIMEOUT)
            .write_timeout(TELEGRAM_WRITE_TIMEOUT)
            .pool_timeout(TELEGRAM_POOL_TIMEOUT)
            .get_updates_connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
            .get_updates_read_timeout(TELEGRAM_GET_UPDATES_READ_TIMEOUT)
            .get_updates_write_timeout(TELEGRAM_WRITE_TIMEOUT)
            .get_updates_pool_timeout(TELEGRAM_POOL_TIMEOUT)
            .post_init(post_init)
            .post_stop(post_stop)
            .build()
        )

        register_handlers(app)
        app.add_error_handler(error_handler)

        logger.info("Бот запускается (long polling)…")

        app.run_polling(
            poll_interval=0.2,
            timeout=TELEGRAM_GET_UPDATES_TIMEOUT,
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
