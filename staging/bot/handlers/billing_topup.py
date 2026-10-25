# handlers/billing_topup.py
import asyncio
from datetime import datetime
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import ContextTypes

from config import (
    db,
    logger,
)
from utils import get_username_clean
from .billing_db import BillingDB

# Платежи (опционально)
try:
    from .payments.yookassa import YooKassaGateway
except Exception as e:
    logger.exception("Не удалось импортировать YooKassaGateway: %s", e)
    YooKassaGateway = None  # type: ignore


class TopupService:
    """
    Баланс и пополнение:
      - экран баланса
      - экран «как пополнить»
      - YooKassa: таблица payments, создание платежей
      - быстрая авто-проверка конкретного платежа после создания
      - восстановимая периодическая проверка всех pending-платежей
      - команда /add
    """

    _TOPUP_AMOUNTS = (300, 600, 900)

    # ===== таблица payments =====

    @staticmethod
    def _ensure_payments_table() -> None:
        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        payment_id TEXT UNIQUE,
                        tg_id INTEGER,
                        amount INTEGER,
                        status TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )

                # Миграция старой таблицы без потери данных
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(payments)").fetchall()
                }

                if "chat_id" not in columns:
                    conn.execute("ALTER TABLE payments ADD COLUMN chat_id INTEGER")

                if "message_id" not in columns:
                    conn.execute("ALTER TABLE payments ADD COLUMN message_id INTEGER")

                if "notified_at" not in columns:
                    conn.execute("ALTER TABLE payments ADD COLUMN notified_at TEXT")

                conn.commit()

        except Exception as e:
            logger.exception("Не удалось создать/обновить таблицу payments: %s", e)

    @staticmethod
    def _register_payment(payment_id: str, tg_id: int, amount: int) -> None:
        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO payments (payment_id, tg_id, amount, status)
                    VALUES (?, ?, ?, COALESCE(
                        (SELECT status FROM payments WHERE payment_id = ?),
                        'pending'
                    ))
                    """,
                    (payment_id, tg_id, amount, payment_id),
                )
                conn.commit()
        except Exception as e:
            logger.exception("Не удалось зарегистрировать платёж: %s", e)

    @staticmethod
    def _set_payment_message(payment_id: str, chat_id: int, message_id: int) -> None:
        """
        Сохраняем сообщение со счётом, чтобы потом именно его заменить
        на сообщение об успешной оплате с кнопкой «К тарифам».
        """
        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    """
                    UPDATE payments
                    SET chat_id = ?, message_id = ?
                    WHERE payment_id = ?
                    """,
                    (int(chat_id), int(message_id), payment_id),
                )
                conn.commit()
        except Exception as e:
            logger.exception(
                "Не удалось сохранить сообщение платежа %s: %s",
                payment_id,
                e,
            )

    @staticmethod
    def _get_payment_record(payment_id: str) -> Optional[tuple]:
        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT tg_id, amount, status, chat_id, message_id, notified_at
                    FROM payments
                    WHERE payment_id = ?
                    """,
                    (payment_id,),
                )
                row = cur.fetchone()
                return row if row else None
        except Exception as e:
            logger.exception("Не удалось прочитать платёж: %s", e)
            return None

    @staticmethod
    def _set_payment_status(payment_id: str, status: str) -> None:
        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    "UPDATE payments SET status = ? WHERE payment_id = ?",
                    (status, payment_id),
                )
                conn.commit()
        except Exception as e:
            logger.exception("Не удалось обновить статус платежа: %s", e)

    @staticmethod
    def _get_pending_payments(limit: int = 100) -> list[tuple[str, int, int]]:
        """
        Возвращает pending-платежи:
          [(payment_id, tg_id, amount), ...]

        Это восстановимый polling:
        если бот перезапустился, он снова найдёт старые pending-платежи
        и проверит их в YooKassa.
        """
        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT payment_id, tg_id, amount
                    FROM payments
                    WHERE status IN ('pending', 'waiting_for_capture')
                    ORDER BY created_at ASC
                    LIMIT ?
                    """,
                    (int(limit),),
                )
                return cur.fetchall() or []
        except Exception as e:
            logger.exception("Не удалось получить pending-платежи: %s", e)
            return []

    @staticmethod
    def credit_succeeded_payment(
        payment_id: str,
    ) -> tuple[bool, str, Optional[int], Optional[int], Optional[int]]:
        """
        Безопасно зачисляет успешный платёж.

        Возвращает:
          credited_now, message, tg_id, amount, new_balance

        credited_now=True:
          баланс был пополнен именно сейчас.

        credited_now=False:
          платёж уже был зачтён, не найден или произошла ошибка.

        Важно:
          баланс и статус платежа меняются в одной транзакции.
        """
        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                cur = conn.cursor()

                # Защита от гонки между _auto_check_payment и check_pending_payments_job
                cur.execute("BEGIN IMMEDIATE")

                cur.execute(
                    """
                    SELECT tg_id, amount, status
                    FROM payments
                    WHERE payment_id = ?
                    """,
                    (payment_id,),
                )
                row = cur.fetchone()

                if not row:
                    conn.rollback()
                    return False, "payment_not_found", None, None, None

                tg_id, amount, status = row
                tg_id = int(tg_id)
                amount = int(amount or 0)
                status = str(status or "")

                if status == "succeeded":
                    cur.execute(
                        "SELECT balance_rub FROM users WHERE tg_id = ?",
                        (tg_id,),
                    )
                    user_row = cur.fetchone()
                    current_balance = int(user_row[0] or 0) if user_row else 0

                    conn.commit()
                    return False, "already_succeeded", tg_id, amount, current_balance

                cur.execute(
                    "SELECT balance_rub FROM users WHERE tg_id = ?",
                    (tg_id,),
                )
                user_row = cur.fetchone()

                if user_row is None:
                    cur.execute(
                        """
                        INSERT INTO users (tg_id, balance_rub, updated_at)
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                        """,
                        (tg_id, amount),
                    )
                    new_balance = amount
                else:
                    current_balance = int(user_row[0] or 0)
                    new_balance = current_balance + amount

                    cur.execute(
                        """
                        UPDATE users
                        SET balance_rub = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE tg_id = ?
                        """,
                        (new_balance, tg_id),
                    )

                cur.execute(
                    """
                    UPDATE payments
                    SET status = ?
                    WHERE payment_id = ?
                    """,
                    ("succeeded", payment_id),
                )

                conn.commit()

                return True, "credited", tg_id, amount, new_balance

        except Exception as e:
            logger.exception("Не удалось зачесть успешный платёж %s: %s", payment_id, e)
            return False, "error", None, None, None

    @staticmethod
    def _claim_payment_notification(
        payment_id: str,
    ) -> tuple[bool, Optional[int], Optional[int], Optional[int], Optional[int]]:
        """
        Помечает платёж как уже уведомлённый.

        Возвращает:
          can_notify, tg_id, amount, chat_id, message_id

        Это защита от дублей:
        если один поток уже отредактировал/отправил сообщение,
        второй ничего не отправит.
        """
        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                cur = conn.cursor()
                cur.execute("BEGIN IMMEDIATE")

                cur.execute(
                    """
                    SELECT tg_id, amount, chat_id, message_id, notified_at
                    FROM payments
                    WHERE payment_id = ?
                    """,
                    (payment_id,),
                )
                row = cur.fetchone()

                if not row:
                    conn.rollback()
                    return False, None, None, None, None

                tg_id, amount, chat_id, message_id, notified_at = row

                if notified_at:
                    conn.commit()
                    return False, None, None, None, None

                cur.execute(
                    """
                    UPDATE payments
                    SET notified_at = CURRENT_TIMESTAMP
                    WHERE payment_id = ?
                    """,
                    (payment_id,),
                )

                conn.commit()

                return (
                    True,
                    int(tg_id) if tg_id is not None else None,
                    int(amount or 0),
                    int(chat_id) if chat_id is not None else None,
                    int(message_id) if message_id is not None else None,
                )

        except Exception as e:
            logger.exception(
                "Не удалось поставить notified_at для платежа %s: %s",
                payment_id,
                e,
            )
            return False, None, None, None, None

    @staticmethod
    async def _notify_payment_success_once(
        context: ContextTypes.DEFAULT_TYPE,
        payment_id: str,
        new_balance: int,
        fallback_chat_id: Optional[int] = None,
        fallback_message_id: Optional[int] = None,
    ) -> None:
        """
        Показывает пользователю успешную оплату только один раз.

        Приоритет:
          1. редактируем сообщение со счётом;
          2. если редактировать не получилось — отправляем новое сообщение.

        В успешном сообщении остаётся только кнопка «📚 К тарифам».
        """
        can_notify, tg_id, amount, db_chat_id, db_message_id = (
            TopupService._claim_payment_notification(payment_id)
        )

        if not can_notify:
            logger.info(
                "Уведомление по платежу %s уже было отправлено, дубль пропускаем",
                payment_id,
            )
            return

        chat_id = db_chat_id or fallback_chat_id or tg_id
        message_id = db_message_id or fallback_message_id

        if not chat_id:
            logger.warning("Некуда отправить уведомление по платежу %s", payment_id)
            return

        text = (
            f"✅ Оплата на {amount} ₽ прошла успешно!\n"
            f"Новый баланс: {new_balance} ₽"
        )

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📚 К тарифам",
                        callback_data="tariff_back",
                    )
                ]
            ]
        )

        if message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
                return
            except Exception as e:
                logger.warning(
                    "Не удалось отредактировать сообщение платежа %s: %s",
                    payment_id,
                    e,
                )

        try:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning(
                "Не удалось отправить сообщение об оплате %s: %s",
                payment_id,
                e,
            )

    @classmethod
    def init_class(cls) -> None:
        cls._ensure_payments_table()

    # ===== экраны =====

    @staticmethod
    async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not update.message:
            return

        username_clean = get_username_clean(update)
        BillingDB.ensure_user_row(user.id, user.full_name or "", username_clean)
        info = db.get_user(user.id) or {}

        bal = int(info.get("balance_rub") or 0)
        tariff = (info.get("tariff") or "free").lower()

        if tariff == "free":
            tariff_h = "Trial"
        elif tariff == "premium":
            tariff_h = "Premium"
        else:
            tariff_h = tariff

        text = (
            f"💰 Текущий баланс: <b>{bal} ₽</b>\n"
            f"📦 Активный тариф: <b>{tariff_h}</b>"
        )

        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("💳 Пополнить", callback_data="topup_menu")]]
        )

        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    @staticmethod
    async def show_topup_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return

        await update.message.reply_text(
            "💳 Пополнение баланса доступно через кнопки в боте.",
            parse_mode="HTML",
        )

    # ===== восстановимая проверка всех pending-платежей =====

    @staticmethod
    async def check_pending_payments_job(context: ContextTypes.DEFAULT_TYPE):
        """
        Периодически проверяет pending-платежи в YooKassa.

        Даже если этот job и _auto_check_payment одновременно увидят succeeded,
        баланс начислится один раз, и сообщение пользователю уйдёт один раз.
        """
        if YooKassaGateway is None:
            logger.warning("YooKassaGateway не настроен, pending-платежи не проверяются")
            return

        pending = TopupService._get_pending_payments(limit=100)
        if not pending:
            return

        try:
            gateway = YooKassaGateway()
        except Exception as e:
            logger.exception("Не удалось создать YooKassaGateway для pending-check: %s", e)
            return

        logger.info("Проверяем pending-платежи YooKassa: %s шт.", len(pending))

        for payment_id, tg_id, amount in pending:
            payment_id = str(payment_id)

            try:
                info = await gateway.get_payment(payment_id)
                status_yk = str(info.get("status") or "")

                if status_yk == "succeeded":
                    credited_now, msg, real_tg_id, real_amount, new_balance = (
                        TopupService.credit_succeeded_payment(payment_id)
                    )

                    logger.info(
                        "Pending-check: payment_id=%s status=succeeded credited_now=%s msg=%s tg_id=%s amount=%s balance=%s",
                        payment_id,
                        credited_now,
                        msg,
                        real_tg_id,
                        real_amount,
                        new_balance,
                    )

                    if real_tg_id and new_balance is not None:
                        await TopupService._notify_payment_success_once(
                            context=context,
                            payment_id=payment_id,
                            new_balance=int(new_balance),
                        )

                elif status_yk == "canceled":
                    TopupService._set_payment_status(payment_id, "canceled")
                    logger.info("Pending-check: payment_id=%s canceled", payment_id)

                elif status_yk == "waiting_for_capture":
                    TopupService._set_payment_status(payment_id, "waiting_for_capture")
                    logger.info(
                        "Pending-check: payment_id=%s waiting_for_capture",
                        payment_id,
                    )

                else:
                    logger.info(
                        "Pending-check: payment_id=%s status=%s, пока не зачисляем",
                        payment_id,
                        status_yk,
                    )

            except Exception as e:
                logger.warning(
                    "Pending-check: ошибка проверки payment_id=%s tg_id=%s amount=%s: %s",
                    payment_id,
                    tg_id,
                    amount,
                    e,
                )

            await asyncio.sleep(0.2)

    # ===== авто-проверка конкретного платежа =====

    @staticmethod
    async def _auto_check_payment(
        context: ContextTypes.DEFAULT_TYPE,
        payment_id: str,
        tg_id: int,
        amount: int,
        chat_id: int,
        message_id: int,
    ):
        """
        Быстрая локальная авто-проверка сразу после создания счёта.
        Проверяет каждые 5 секунд.

        Главный восстановимый механизм всё равно check_pending_payments_job,
        который переживает перезапуск бота.
        """
        if YooKassaGateway is None:
            return

        try:
            gateway = YooKassaGateway()
        except Exception as e:
            logger.exception("Не удалось создать YooKassaGateway в авто-проверке: %s", e)
            return

        MAX_WAIT_SEC = 60 * 60
        POLL_INTERVAL_SEC = 5

        start = datetime.now()

        while (datetime.now() - start).total_seconds() < MAX_WAIT_SEC:
            try:
                info = await gateway.get_payment(payment_id)
                status_yk = str(info.get("status") or "")
            except Exception as e:
                logger.warning(
                    "Ошибка авто-проверки платежа %s для tg_id=%s: %s",
                    payment_id,
                    tg_id,
                    e,
                )
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            if status_yk == "succeeded":
                credited_now, msg, credited_tg_id, credited_amount, new_balance = (
                    TopupService.credit_succeeded_payment(payment_id)
                )

                if credited_tg_id is None or new_balance is None:
                    logger.warning(
                        "Авто-проверка: платёж %s succeeded в YooKassa, но не найден в локальной БД",
                        payment_id,
                    )
                    return

                await TopupService._notify_payment_success_once(
                    context=context,
                    payment_id=payment_id,
                    new_balance=int(new_balance),
                    fallback_chat_id=chat_id,
                    fallback_message_id=message_id,
                )

                logger.info(
                    "Авто-проверка: платёж %s на %s ₽ для tg_id=%s зачтён, credited_now=%s, msg=%s",
                    payment_id,
                    credited_amount or amount,
                    credited_tg_id,
                    credited_now,
                    msg,
                )
                return

            if status_yk in ("canceled", "expired"):
                TopupService._set_payment_status(payment_id, status_yk)
                logger.info(
                    "Авто-проверка: платёж %s для tg_id=%s статус=%s — прекращаем опрос",
                    payment_id,
                    tg_id,
                    status_yk,
                )
                return

            if status_yk == "waiting_for_capture":
                TopupService._set_payment_status(payment_id, "waiting_for_capture")
                logger.info(
                    "Авто-проверка: платёж %s для tg_id=%s waiting_for_capture",
                    payment_id,
                    tg_id,
                )

            await asyncio.sleep(POLL_INTERVAL_SEC)

        logger.info(
            "Авто-проверка: платёж %s для tg_id=%s не оплачен в течение 60 минут",
            payment_id,
            tg_id,
        )

    # ===== callback пополнения =====

    @staticmethod
    async def handle_topup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        callback_data:
          - topup_menu
          - topup_amt_300 / topup_amt_600 / topup_amt_900
          - topup_promo
          - topup_cancel
        """
        query = update.callback_query
        if not query:
            return

        await query.answer()
        data = (query.data or "").strip()

        user = query.from_user
        chat_id = query.message.chat.id if query.message else user.id

        tg_id = user.id
        tg_name = user.full_name or ""
        username_clean = (user.username or f"user{tg_id}").replace("@", "").replace(" ", "")
        BillingDB.ensure_user_row(tg_id, tg_name, username_clean)

        # --- Меню сумм ---
        if data == "topup_menu":
            context.user_data["await_promo"] = False

            rows = [
                [
                    InlineKeyboardButton(
                        f"➕ {amt} ₽",
                        callback_data=f"topup_amt_{amt}",
                    )
                ]
                for amt in TopupService._TOPUP_AMOUNTS
            ]
            rows.append(
                [InlineKeyboardButton("Промокод", callback_data="topup_promo")]
            )
            rows.append(
                [InlineKeyboardButton("Не пополнять", callback_data="topup_cancel")]
            )

            kb = InlineKeyboardMarkup(rows)

            try:
                await query.edit_message_text(
                    "Выбери сумму пополнения 👇",
                    reply_markup=kb,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id,
                    "Выбери сумму пополнения 👇",
                    reply_markup=kb,
                )
            return

        # --- Ввод промокода ---
        if data == "topup_promo":
            context.user_data["await_promo"] = True

            text = (
                "Введи промокод одним сообщением.\n"
                "Пример: <code>KL300-AB12CD34EF</code>"
            )
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Вернуться к пополнению", callback_data="topup_menu")]]
            )

            try:
                await query.edit_message_text(
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            return

        # --- Отмена ---
        if data == "topup_cancel":
            context.user_data["await_promo"] = False
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
            return

        # --- Создание платежа ---
        if data.startswith("topup_amt_"):
            context.user_data["await_promo"] = False

            try:
                amount = int(data.rsplit("_", 1)[-1])
            except Exception:
                amount = 0

            if amount not in TopupService._TOPUP_AMOUNTS:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                return

            if YooKassaGateway is None:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id,
                    "Платёжный модуль ещё не настроен.",
                )
                return

            try:
                gateway = YooKassaGateway()
                confirmation_url, payment_id = await gateway.create_payment(
                    amount=amount,
                    description=f"Пополнение баланса на {amount} ₽ (tg_id={tg_id})",
                    tg_id=tg_id,
                )

                TopupService._register_payment(payment_id, tg_id, amount)

                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                f"Тестовый платёж {amount} ₽",
                                url=confirmation_url,
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "Не пополнять",
                                callback_data="topup_cancel",
                            )
                        ],
                    ]
                )

                text = (
                    f"🧾 Счёт создан на <b>{amount} ₽</b>.\n\n"
                    "Это тестовый платёж: реальные деньги не списываются.\n"
                    "После оплаты бот проверит платёж автоматически."
                )

                try:
                    await query.edit_message_text(
                        text=text,
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
                    message_id = query.message.message_id
                except Exception:
                    msg = await context.bot.send_message(
                        chat_id,
                        text,
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
                    message_id = msg.message_id

                TopupService._set_payment_message(
                    payment_id=payment_id,
                    chat_id=chat_id,
                    message_id=message_id,
                )

                task = asyncio.create_task(
                    TopupService._auto_check_payment(
                        context=context,
                        payment_id=payment_id,
                        tg_id=tg_id,
                        amount=amount,
                        chat_id=chat_id,
                        message_id=message_id,
                    )
                )

                def _log_task_result(t: asyncio.Task):
                    try:
                        exc = t.exception()
                        if exc:
                            logger.exception(
                                "Фоновая авто-проверка платежа упала: payment_id=%s error=%s",
                                payment_id,
                                exc,
                            )
                    except asyncio.CancelledError:
                        logger.warning(
                            "Фоновая авто-проверка платежа отменена: payment_id=%s",
                            payment_id,
                        )
                    except Exception as e:
                        logger.warning(
                            "Не удалось прочитать результат фоновой авто-проверки payment_id=%s: %s",
                            payment_id,
                            e,
                        )

                task.add_done_callback(_log_task_result)

            except Exception as e:
                logger.exception("Ошибка создания платежа")
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id,
                    f"❌ Не удалось создать платёж: {e}",
                )

    # ===== тестовое пополнение /add =====

    @staticmethod
    async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return

        user = update.effective_user
        if not user:
            return

        args = context.args or []
        if not args:
            await update.message.reply_text("Использование: /add 300")
            return

        try:
            amount = int(args[0])
        except ValueError:
            await update.message.reply_text(
                "Сумма должна быть числом. Пример: /add 300"
            )
            return

        if amount == 0:
            await update.message.reply_text("Ноль пополнять нельзя.")
            return

        username_clean = get_username_clean(update)
        BillingDB.ensure_user_row(user.id, user.full_name or "", username_clean)
        new_balance = BillingDB.change_balance(user.id, amount)

        sign = "+" if amount > 0 else ""
        await update.message.reply_text(
            f"Баланс изменён: {sign}{amount} ₽\nНовый баланс: {new_balance} ₽"
        )


# создаём/обновляем таблицу payments при импорте модуля
TopupService.init_class()


# ===== публичные функции =====

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await TopupService.show_balance(update, context)


async def show_topup_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await TopupService.show_topup_info(update, context)


async def handle_topup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await TopupService.handle_topup_callback(update, context)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await TopupService.cmd_add(update, context)