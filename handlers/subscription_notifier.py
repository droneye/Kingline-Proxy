# handlers/subscription_notifier.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Set

from telegram.ext import ContextTypes
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from config import db, logger  # db = SubscriptionDB


class SubscriptionNotifier:
    """
    Уведомитель по подпискам (один общий job, без таймеров на каждого).

    Уведомляет:
      - за 24 часа до окончания
      - за 1 час до окончания

    Важно:
      - антидубликаты строятся по ключу "tg_id|expires_iso".
        Если expires изменился (продление) — ключ изменится, уведомления снова разрешатся.
    """

    _JOB_NAME = "subscription_notifier_periodic"
    _INTERVAL_SEC = 60
    _FIRST_SEC = 25

    # Окна, чтобы не промахнуться на шаге 60 сек:
    _WINDOW_1D = 10 * 60   # ±10 минут
    _WINDOW_1H = 5 * 60    # ±5 минут

    # антидубликаты (ключи вида "tg_id|expires_iso")
    _sent_1d: Set[str] = set()
    _sent_1h: Set[str] = set()

    # ---------- helpers ----------

    @staticmethod
    def _make_key(tg_id: int, expires_iso: str) -> str:
        return f"{tg_id}|{expires_iso}"

    @staticmethod
    def _parse_dt_any(dt_str: Optional[str]) -> Optional[datetime]:
        """
        Пытается распарсить несколько форматов.
        Возвращает datetime в UTC (tz-aware).
        """
        if not dt_str:
            return None

        s = str(dt_str).strip()
        if not s:
            return None

        fmts = (
            "%Y-%m-%dT%H:%M:%SZ",     # 2025-12-27T12:00:00Z
            "%Y-%m-%dT%H:%M:%S.%fZ",  # 2025-12-27T12:00:00.123Z
            "%Y-%m-%d %H:%M:%S",      # sqlite-like
            "%Y-%m-%d %H:%M",         # sqlite-like short
        )

        for f in fmts:
            try:
                dt = datetime.strptime(s, f)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass

        # fallback: try fromisoformat (может быть с +00:00)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _fmt_exp_msk(expires_utc: datetime) -> str:
        exp_msk = expires_utc + timedelta(hours=3)
        return exp_msk.strftime("%d.%m.%Y %H:%M")

    @staticmethod
    def _within_window(remaining_sec: float, target_sec: int, window_sec: int) -> bool:
        return (target_sec - window_sec) <= remaining_sec <= (target_sec + window_sec)

    @staticmethod
    def _cleanup_sets(active_keys: Set[str]) -> None:
        """
        Подчищаем антидубликаты по актуальным ключам, чтобы множества не росли бесконечно.
        """
        SubscriptionNotifier._sent_1d.intersection_update(active_keys)
        SubscriptionNotifier._sent_1h.intersection_update(active_keys)

    # ---------- запуск job ----------

    @staticmethod
    def ensure_started(context_or_app) -> None:
        """
        Гарантирует запуск фонового job.
        Можно передавать:
          - context (в хендлере)
          - application (в post_init)
        """
        app = getattr(context_or_app, "application", None) or context_or_app
        jq = getattr(app, "job_queue", None)
        if not jq:
            logger.warning("SubscriptionNotifier: job_queue недоступен (app=%r)", app)
            return

        try:
            existing = jq.get_jobs_by_name(SubscriptionNotifier._JOB_NAME)
            if existing:
                return
        except Exception:
            pass

        jq.run_repeating(
            SubscriptionNotifier._periodic_callback,
            interval=SubscriptionNotifier._INTERVAL_SEC,
            first=SubscriptionNotifier._FIRST_SEC,
            name=SubscriptionNotifier._JOB_NAME,
        )
        logger.info("SubscriptionNotifier: запущен job '%s'", SubscriptionNotifier._JOB_NAME)

    # ---------- основной callback ----------

    @staticmethod
    async def _periodic_callback(context: ContextTypes.DEFAULT_TYPE):
        now_utc = datetime.now(timezone.utc)

        try:
            users: List[Dict[str, Any]] = db.list_users()
        except Exception as e:
            logger.exception("SubscriptionNotifier: list_users failed: %s", e)
            return

        active_keys: Set[str] = set()

        for u in users:
            tg_id = u.get("tg_id")
            if not tg_id:
                continue

            # Если хочешь НЕ уведомлять заблокированных — оставь так:
            try:
                if int(u.get("is_blocked") or 0) == 1:
                    continue
            except Exception:
                pass

            tariff = (u.get("tariff") or "").lower()

            # Берём дату окончания (приоритет подписки, затем ключа)
            expires_iso = u.get("subscription_expires_at") or u.get("key_expires_at")
            expires_dt = SubscriptionNotifier._parse_dt_any(expires_iso)
            if not expires_dt or not expires_iso:
                continue

            key = SubscriptionNotifier._make_key(int(tg_id), str(expires_iso))
            active_keys.add(key)

            remaining_sec = (expires_dt - now_utc).total_seconds()
            if remaining_sec <= 0:
                continue

            # 24 часа
            if key not in SubscriptionNotifier._sent_1d:
                if SubscriptionNotifier._within_window(remaining_sec, 24 * 3600, SubscriptionNotifier._WINDOW_1D):
                    await SubscriptionNotifier._send_1d_warning(
                        context=context,
                        tg_id=int(tg_id),
                        tariff=tariff,
                        expires_dt=expires_dt,
                        remaining_sec=remaining_sec,
                    )
                    SubscriptionNotifier._sent_1d.add(key)

            # 1 час
            if key not in SubscriptionNotifier._sent_1h:
                if SubscriptionNotifier._within_window(remaining_sec, 3600, SubscriptionNotifier._WINDOW_1H):
                    await SubscriptionNotifier._send_1h_warning(
                        context=context,
                        tg_id=int(tg_id),
                        tariff=tariff,
                        expires_dt=expires_dt,
                        remaining_sec=remaining_sec,
                    )
                    SubscriptionNotifier._sent_1h.add(key)

        # чистим память
        SubscriptionNotifier._cleanup_sets(active_keys)

    # ---------- тексты/кнопки ----------

    @staticmethod
    def _renew_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("💎 Продлить подписку", callback_data="tariff_full")]]
        )

    @staticmethod
    async def _safe_send(
        context: ContextTypes.DEFAULT_TYPE,
        tg_id: int,
        text: str,
        kb: Optional[InlineKeyboardMarkup],
    ) -> bool:
        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
            return True
        except Exception as e:
            logger.warning("SubscriptionNotifier: send_message failed tg_id=%s: %s", tg_id, e)
            return False

    # ---------- notifications ----------

    @staticmethod
    async def _send_1d_warning(
        context: ContextTypes.DEFAULT_TYPE,
        tg_id: int,
        tariff: str,
        expires_dt: datetime,
        remaining_sec: float,
    ):
        hours = max(0, int(remaining_sec // 3600))
        exp_human = SubscriptionNotifier._fmt_exp_msk(expires_dt)

        plan = "Premium" if tariff == "premium" else "доступ"
        text = (
            f"⏰ <b>{plan} скоро закончится</b>\n\n"
            f"Осталось примерно <b>{hours} часов</b>.\n"
            f"Действует до: <b>{exp_human}</b> (МСК).\n\n"
            "Чтобы не остаться без Proxy — продли заранее."
        )

        ok = await SubscriptionNotifier._safe_send(context, tg_id, text, SubscriptionNotifier._renew_keyboard())
        if ok:
            logger.info("SubscriptionNotifier: 1d warning sent tg_id=%s expires=%s", tg_id, expires_dt.isoformat())

    @staticmethod
    async def _send_1h_warning(
        context: ContextTypes.DEFAULT_TYPE,
        tg_id: int,
        tariff: str,
        expires_dt: datetime,
        remaining_sec: float,
    ):
        mins = max(0, int(remaining_sec // 60))
        exp_human = SubscriptionNotifier._fmt_exp_msk(expires_dt)

        plan = "Premium" if tariff == "premium" else "доступ"
        text = (
            f"⚠️ <b>{plan} заканчивается через час</b>\n\n"
            f"Осталось примерно <b>{mins} минут</b>.\n"
            f"Действует до: <b>{exp_human}</b> (МСК).\n\n"
            "Если хочешь без обрывов — продли сейчас."
        )

        ok = await SubscriptionNotifier._safe_send(context, tg_id, text, SubscriptionNotifier._renew_keyboard())
        if ok:
            logger.info("SubscriptionNotifier: 1h warning sent tg_id=%s expires=%s", tg_id, expires_dt.isoformat())


# ------------------ Как правильно запускать ------------------
#
# 1) Самый надёжный вариант — включить запуск job при старте бота:
#
#   async def post_init(application):
#       from handlers.subscription_notifier import SubscriptionNotifier
#       SubscriptionNotifier.ensure_started(application)
#
# 2) Если ты хочешь запускать только при выдаче/продлении подписки — можно вызвать:
#       SubscriptionNotifier.ensure_started(context)
#
# ------------------------------------------------------------
