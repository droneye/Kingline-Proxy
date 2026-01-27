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
      - ЮKassa: таблица payments, создание платежей, авто-проверка статуса
      - команда /add (тестовое пополнение)
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
                conn.commit()
        except Exception as e:
            logger.exception("Не удалось создать таблицу payments: %s", e)

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
    def _get_payment_record(payment_id: str) -> Optional[tuple]:
        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                cur = conn.cursor()
                cur.execute(
                    "SELECT tg_id, amount, status FROM payments WHERE payment_id = ?",
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

    # ===== авто-проверка платежа =====

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
        До 15 минут опрашивает YooKassa.
        При статусе 'succeeded':
          - меняет статус платежа в БД,
          - зачисляет деньги (если ещё не зачисляли),
          - редактирует сообщение со счётом на «Оплата прошла успешно»,
          - добавляет кнопку «📚 К тарифам».
        """
        if YooKassaGateway is None:
            return

        try:
            gateway = YooKassaGateway()
        except Exception as e:
            logger.exception("Не удалось создать YooKassaGateway в авто-проверке: %s", e)
            return

        MAX_WAIT_SEC = 15 * 60
        POLL_INTERVAL_SEC = 10

        start = datetime.now()

        while (datetime.now() - start).total_seconds() < MAX_WAIT_SEC:
            try:
                info = await gateway.get_payment(payment_id)
                status_yk = info.get("status")
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
                # Проверяем, не был ли уже зачтён
                rec = TopupService._get_payment_record(payment_id)
                already_succeeded = False
                if rec:
                    _, _, rec_status = rec
                    already_succeeded = (rec_status == "succeeded")

                TopupService._set_payment_status(payment_id, "succeeded")

                if not already_succeeded:
                    new_balance = BillingDB.change_balance(tg_id, int(amount or 0))
                else:
                    new_balance = BillingDB.get_balance(tg_id)

                text = (
                    f"✅ Оплата на {amount} ₽ прошла успешно!\n"
                    f"Новый баланс: {new_balance} ₽"
                )

                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "📚 К тарифам", callback_data="tariff_back"
                            )
                        ]
                    ]
                )

                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
                except Exception as e:
                    logger.warning(
                        "Не удалось отредактировать сообщение счёта %s: %s",
                        payment_id,
                        e,
                    )
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            parse_mode="HTML",
                            reply_markup=kb,
                        )
                    except Exception as e2:
                        logger.warning(
                            "Не удалось отправить сообщение после оплаты %s: %s",
                            payment_id,
                            e2,
                        )

                logger.info(
                    "Авто-проверка: платёж %s на %s ₽ для tg_id=%s успешно зачтён",
                    payment_id,
                    amount,
                    tg_id,
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

            await asyncio.sleep(POLL_INTERVAL_SEC)

        logger.info(
            "Авто-проверка: платёж %s для tg_id=%s не оплачен в течение 15 минут",
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
          - topup_check_<payment_id>  (ручная проверка — оставлена как запасной вариант)
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
            # если возвращаемся из режима промокода — просто сбрасываем флаг
            context.user_data["await_promo"] = False

            rows = [
                [
                    InlineKeyboardButton(
                        f"➕ {amt} ₽", callback_data=f"topup_amt_{amt}"
                    )
                ]
                for amt in TopupService._TOPUP_AMOUNTS
            ]
            rows.append(
                [InlineKeyboardButton("Промокод", callback_data="topup_promo")] #🎟️ Промокод
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

        # --- Ввод промокода из меню пополнения ---
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

        # --- Проверка оплаты (ручная, на всякий случай) ---
        if data.startswith("topup_check_"):
            if YooKassaGateway is None:
                await context.bot.send_message(
                    chat_id,
                    "Платёжный модуль ещё не настроен.",
                )
                return

            payment_id = data.replace("topup_check_", "", 1)

            rec = TopupService._get_payment_record(payment_id)
            if not rec:
                await context.bot.send_message(
                    chat_id,
                    "❗ Информация о платеже не найдена. Если ты оплатил — напиши в поддержку.",
                )
                return

            rec_tg_id, amount, status = rec
            if int(rec_tg_id) != int(tg_id):
                await context.bot.send_message(
                    chat_id,
                    "❗ Этот платёж привязан к другому аккаунту.",
                )
                return

            if status == "succeeded":
                new_bal = BillingDB.get_balance(tg_id)
                await context.bot.send_message(
                    chat_id,
                    f"✅ Этот платёж уже зачтён.\nТекущий баланс: {new_bal} ₽",
                )
                return

            try:
                gateway = YooKassaGateway()
                info = await gateway.get_payment(payment_id)
            except Exception as e:
                logger.exception("Ошибка проверки платежа YooKassa")
                await context.bot.send_message(
                    chat_id,
                    f"❌ Не удалось проверить платёж: {e}",
                )
                return

            status_yk = info.get("status")
            if status_yk == "succeeded":
                TopupService._set_payment_status(payment_id, "succeeded")
                new_balance = BillingDB.change_balance(tg_id, int(amount or 0))

                await context.bot.send_message(
                    chat_id,
                    (
                        f"✅ Оплата на {amount} ₽ прошла успешно!\n"
                        f"Новый баланс: {new_balance} ₽"
                    ),
                )
            else:
                await context.bot.send_message(
                    chat_id,
                    (
                        "⏳ Платёж пока не оплачен.\n"
                        f"Текущий статус: <b>{status_yk}</b>.\n"
                        "Если ты уже оплатил, подожди немного и попробуй ещё раз, "
                        "либо напиши в поддержку."
                    ),
                    parse_mode="HTML",
                )
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
                                f"Оплатить {amount} ₽ в ЮKassa",
                                url=confirmation_url,
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "Не пополнять", callback_data="topup_cancel"
                            )
                        ],
                    ]
                )

                text = (
                    f"🧾 Счёт создан на <b>{amount} ₽</b>.\n\n"
                    "Нажми кнопку ниже, чтобы перейти к оплате.\n"
                    "Мы автоматически проверим оплату в течение 15 минут 🙂"
                )

                # message_id нам нужен для последующего редактирования
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

                # Запускаем авто-проверку в фоне
                asyncio.create_task(
                    TopupService._auto_check_payment(
                        context=context,
                        payment_id=payment_id,
                        tg_id=tg_id,
                        amount=amount,
                        chat_id=chat_id,
                        message_id=message_id,
                    )
                )

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


# создаём таблицу payments при импорте модуля
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
