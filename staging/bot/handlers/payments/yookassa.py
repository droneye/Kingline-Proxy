# handlers/payments/yookassa.py

import os
import uuid
import base64
import json
from typing import Tuple, Optional

import aiohttp

# Пытаемся взять конфиг из твоего config.py, иначе читаем из ENV
try:
    from config import logger as _logger  # type: ignore
    from config import (
        YK_SHOP_ID,        # os.getenv("YK_SHOP_ID")
        YK_SECRET_KEY,     # os.getenv("YK_SECRET_KEY")
        YK_RETURN_URL,     # os.getenv("YK_RETURN_URL")  # куда вернётся пользователь
    )
except Exception:  # pragma: no cover
    _logger = None
    YK_SHOP_ID = os.getenv("YK_SHOP_ID", "")
    YK_SECRET_KEY = os.getenv("YK_SECRET_KEY", "")
    YK_RETURN_URL = os.getenv("YK_RETURN_URL", "https://t.me/your_bot")

# Email, который будет попадать в чек как email покупателя,
# если ты явно не укажешь другой в будущем.
# ⚠️ РЕКОМЕНДУЮ заменить на свой (например, billing@твой-домен)
YK_RECEIPT_EMAIL = os.getenv("YK_RECEIPT_EMAIL", "billing@example.com")


def _log(msg: str):
    try:
        if _logger:
            _logger.info(msg)
        else:
            print(msg)
    except Exception:
        pass


class YooKassaGateway:
    """
    Минималистичный асинхронный клиент YooKassa для:
      - создания платёжных ссылок;
      - проверки статуса платежей.

    Без официального SDK.
    """

    API_URL = "https://api.yookassa.ru/v3/payments"

    def __init__(
        self,
        shop_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        return_url: Optional[str] = None,
        timeout: int = 20,
        receipt_email: Optional[str] = None,
    ):
        """
        :param shop_id: Идентификатор магазина в YooKassa
        :param secret_key: Секретный ключ магазина
        :param return_url: URL, куда YooKassa вернёт пользователя после оплаты
        :param timeout: таймаут HTTP-запросов
        :param receipt_email: email по умолчанию для чека (если не используется свой на уровне бота)
        """
        # Берём значения из аргументов, потом из config.py, потом из ENV
        self.shop_id = (shop_id or YK_SHOP_ID or "").strip()
        self.secret_key = (secret_key or YK_SECRET_KEY or "").strip()
        self.return_url = (return_url or YK_RETURN_URL or "").strip()
        self.timeout = timeout

        # email, который пойдёт в чек (если не укажешь другой)
        self.receipt_email = (receipt_email or YK_RECEIPT_EMAIL or "").strip()
        if not self.receipt_email:
            # Это крайний случай, чтобы чек всё равно был валидным.
            # Лучше ЗАДАЙ YK_RECEIPT_EMAIL в ENV или config.py.
            self.receipt_email = "billing@example.com"

        if not self.secret_key:
            raise RuntimeError(
                "YooKassa: не задан YK_SECRET_KEY (в config.py или ENV)."
            )

        if not self.shop_id:
            _log(
                "YooKassa: WARNING: YK_SHOP_ID пустой — будет использоваться OAuth Bearer, "
                "это подходит только для OAuth-интеграций."
            )

    # ===== низкоуровневая авторизация =====

    def _auth_header(self) -> str:
        """
        Логика:
          • если есть shop_id → считаем, что это обычный магазин → Basic <base64(shopId:secretKey)>
          • если shop_id пустой → считаем, что secret_key — это OAuth-токен → Bearer <token>
        """

        if self.shop_id:
            # Классическая схема: shopId + secretKey (в т.ч. тестовые, начинающиеся с test_)
            token = base64.b64encode(f"{self.shop_id}:{self.secret_key}".encode()).decode()
            return f"Basic {token}"

        # Режим без shop_id — используем secret_key как OAuth-токен
        return f"Bearer {self.secret_key}"

    # ===== высокоуровневые методы =====

    async def create_payment(
        self,
        amount: int,
        description: str,
        tg_id: int,
        currency: str = "RUB",
        return_url: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Создаёт платёж в YooKassa.

        :param amount: сумма в рублях (целое число)
        :param description: описание платежа
        :param tg_id: Telegram ID пользователя (для metadata)
        :param currency: валюта (по умолчанию RUB)
        :param return_url: URL, на который вернётся пользователь после оплаты
        :returns: (confirmation_url, payment_id)
        """

        # === Формирование чека (receipt) ===
        # Важно: если в ЛК YooKassa включена фискализация (чек 54-ФЗ),
        # поле "receipt" обязательно и должно быть валидным.
        # Здесь делаем базовый чек:
        #  - 1 позиция "Пополнение баланса Kingline VPN"
        #  - количество 1
        #  - сумма = amount
        #  - НДС: vat_code=1 (без НДС) — при необходимости поменяй под свой договор.
        receipt = {
            "customer": {
                # Если хочешь — потом вместо этого email подставляй email конкретного пользователя.
                "email": self.receipt_email,
            },
            "items": [
                {
                    "description": "Пополнение баланса Kingline VPN",
                    "quantity": "1.0",
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": currency,
                    },
                    # ⚠️ vat_code нужно выставить в соответствии с тем,
                    # что у тебя указано в договоре с YooKassa:
                    # 1 — без НДС
                    # 2 — 0%
                    # 3 — 10%
                    # 4 — 20%
                    # 5,6 — расчётные ставки
                    "vat_code": 1,
                    # Можно дополнительно указать:
                    # "payment_mode": "full_prepayment",
                    # "payment_subject": "service",
                }
            ],
        }

        payload = {
            "amount": {
                "value": f"{amount:.2f}",
                "currency": currency,
            },
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": return_url or self.return_url,
            },
            "description": description[:127],
            "metadata": {"tg_id": tg_id, "amount": amount},
            "receipt": receipt,
        }

        headers = {
            "Authorization": self._auth_header(),
            "Idempotence-Key": str(uuid.uuid4()),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Для отладки — логируем, ЧТО именно отправляем
        _log(f"YooKassa create_payment payload: {json.dumps(payload, ensure_ascii=False)}")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.API_URL,
                data=json.dumps(payload, ensure_ascii=False),
                headers=headers,
                timeout=self.timeout,
            ) as resp:
                txt = await resp.text()
                if resp.status not in (200, 201):
                    # сюда как раз и попадает твой 400 invalid_request, если чек "missing or illegal"
                    _log(f"YooKassa create_payment error HTTP {resp.status}: {txt}")
                    raise RuntimeError(f"YooKassa HTTP {resp.status}: {txt}")

                data = json.loads(txt)
                payment_id = str(data.get("id") or "")
                conf = data.get("confirmation") or {}
                url = conf.get("confirmation_url") or conf.get("url") or ""
                if not url:
                    raise RuntimeError(
                        f"YooKassa: нет confirmation_url в ответе: {data}"
                    )

                _log(f"YooKassa payment created: id={payment_id}, amount={amount}")
                return url, payment_id

    async def get_payment(self, payment_id: str) -> dict:
        """
        Получить информацию о платеже по payment_id.
        Возвращает dict с полным ответом YooKassa.
        """
        headers = {
            "Authorization": self._auth_header(),
            "Accept": "application/json",
        }

        url = f"{self.API_URL}/{payment_id}"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=self.timeout,
            ) as resp:
                txt = await resp.text()
                if resp.status != 200:
                    _log(f"YooKassa get_payment error HTTP {resp.status}: {txt}")
                    raise RuntimeError(f"YooKassa GET HTTP {resp.status}: {txt}")
                data = json.loads(txt)
                _log(
                    f"YooKassa payment status: id={payment_id}, status={data.get('status')}"
                )
                return data

# The staging build has no live merchant credentials or network payment adapter.
if os.getenv('KINGLINE_STAGING') == '1':
    from test_payments import TestPaymentGateway as YooKassaGateway
