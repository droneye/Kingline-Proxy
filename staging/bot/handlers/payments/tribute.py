# handlers/payments/tribute.py

import os
import json
from typing import Optional, Dict, Any, Union, Tuple
from decimal import Decimal, ROUND_HALF_UP

import aiohttp

# Пытаемся взять конфиг из config.py, иначе читаем из ENV
try:
    from config import logger as _logger  # type: ignore
    from config import TRIBUTE_API_KEY  # type: ignore

    # Эти переменные могут отсутствовать в config.py — тогда тихо берём из ENV
    try:
        from config import TRIBUTE_PRODUCT_WEBLINK_300  # type: ignore
    except Exception:
        TRIBUTE_PRODUCT_WEBLINK_300 = os.getenv("TRIBUTE_PRODUCT_WEBLINK_300", "")
    try:
        from config import TRIBUTE_PRODUCT_WEBLINK_600  # type: ignore
    except Exception:
        TRIBUTE_PRODUCT_WEBLINK_600 = os.getenv("TRIBUTE_PRODUCT_WEBLINK_600", "")
    try:
        from config import TRIBUTE_PRODUCT_WEBLINK_900  # type: ignore
    except Exception:
        TRIBUTE_PRODUCT_WEBLINK_900 = os.getenv("TRIBUTE_PRODUCT_WEBLINK_900", "")

    try:
        from config import TRIBUTE_PRODUCT_LINK_300  # type: ignore
    except Exception:
        TRIBUTE_PRODUCT_LINK_300 = os.getenv("TRIBUTE_PRODUCT_LINK_300", "")
    try:
        from config import TRIBUTE_PRODUCT_LINK_600  # type: ignore
    except Exception:
        TRIBUTE_PRODUCT_LINK_600 = os.getenv("TRIBUTE_PRODUCT_LINK_600", "")
    try:
        from config import TRIBUTE_PRODUCT_LINK_900  # type: ignore
    except Exception:
        TRIBUTE_PRODUCT_LINK_900 = os.getenv("TRIBUTE_PRODUCT_LINK_900", "")

    try:
        from config import TRIBUTE_PRODUCT_ID_300  # type: ignore
    except Exception:
        TRIBUTE_PRODUCT_ID_300 = os.getenv("TRIBUTE_PRODUCT_ID_300", "")
    try:
        from config import TRIBUTE_PRODUCT_ID_600  # type: ignore
    except Exception:
        TRIBUTE_PRODUCT_ID_600 = os.getenv("TRIBUTE_PRODUCT_ID_600", "")
    try:
        from config import TRIBUTE_PRODUCT_ID_900  # type: ignore
    except Exception:
        TRIBUTE_PRODUCT_ID_900 = os.getenv("TRIBUTE_PRODUCT_ID_900", "")

except Exception:  # pragma: no cover
    _logger = None
    TRIBUTE_API_KEY = os.getenv("TRIBUTE_API_KEY", "")

    TRIBUTE_PRODUCT_WEBLINK_300 = os.getenv("TRIBUTE_PRODUCT_WEBLINK_300", "")
    TRIBUTE_PRODUCT_WEBLINK_600 = os.getenv("TRIBUTE_PRODUCT_WEBLINK_600", "")
    TRIBUTE_PRODUCT_WEBLINK_900 = os.getenv("TRIBUTE_PRODUCT_WEBLINK_900", "")

    TRIBUTE_PRODUCT_LINK_300 = os.getenv("TRIBUTE_PRODUCT_LINK_300", "")
    TRIBUTE_PRODUCT_LINK_600 = os.getenv("TRIBUTE_PRODUCT_LINK_600", "")
    TRIBUTE_PRODUCT_LINK_900 = os.getenv("TRIBUTE_PRODUCT_LINK_900", "")

    TRIBUTE_PRODUCT_ID_300 = os.getenv("TRIBUTE_PRODUCT_ID_300", "")
    TRIBUTE_PRODUCT_ID_600 = os.getenv("TRIBUTE_PRODUCT_ID_600", "")
    TRIBUTE_PRODUCT_ID_900 = os.getenv("TRIBUTE_PRODUCT_ID_900", "")


def _log(msg: str):
    try:
        if _logger:
            _logger.info(msg)
        else:
            print(msg)
    except Exception:
        pass


def _to_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        return int(str(v).strip())
    except Exception:
        return None


class TributeGateway:
    """
    Async-клиент Tribute Creator API (НЕ Shop API):
      - list_products
      - get_product
      - cancel_digital_product_purchase (Stars-only refund endpoint)
      - get_topup_product_url (получить ссылку на товар 300/600/900)
      - find_product_by_amount_rub (fallback через API)
    """

    API_BASE = "https://tribute.tg/api/v1"

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 20,
    ):
        self.api_key = (api_key or TRIBUTE_API_KEY or "").strip()
        self.timeout = timeout

        if not self.api_key:
            raise RuntimeError("Tribute: не задан TRIBUTE_API_KEY (config.py или ENV).")

        # Конфигурируем ссылки на товары (приоритет: webLink, потом link)
        self._product_links_by_amount: Dict[int, Dict[str, str]] = {
            300: {
                "webLink": (TRIBUTE_PRODUCT_WEBLINK_300 or "").strip(),
                "link": (TRIBUTE_PRODUCT_LINK_300 or "").strip(),
            },
            600: {
                "webLink": (TRIBUTE_PRODUCT_WEBLINK_600 or "").strip(),
                "link": (TRIBUTE_PRODUCT_LINK_600 or "").strip(),
            },
            900: {
                "webLink": (TRIBUTE_PRODUCT_WEBLINK_900 or "").strip(),
                "link": (TRIBUTE_PRODUCT_LINK_900 or "").strip(),
            },
        }

        # (Опционально) ID товаров для валидации webhook'ов
        self._product_ids_by_amount: Dict[int, Optional[int]] = {
            300: _to_int(TRIBUTE_PRODUCT_ID_300),
            600: _to_int(TRIBUTE_PRODUCT_ID_600),
            900: _to_int(TRIBUTE_PRODUCT_ID_900),
        }

    @staticmethod
    def _normalize_currency(currency: str) -> str:
        c = (currency or "").strip().lower()
        if c not in {"rub", "usd", "eur"}:
            raise ValueError("Tribute: currency должна быть одной из {'rub','usd','eur'}")
        return c

    @staticmethod
    def amount_major_to_minor(amount: Union[int, float, str, Decimal], currency: str) -> int:
        """
        Перевод суммы из обычных единиц в минимальные:
          300 RUB -> 30000
          9.99 USD -> 999
        """
        _ = TributeGateway._normalize_currency(currency)
        dec = Decimal(str(amount))
        if dec <= 0:
            raise ValueError("Tribute: amount должен быть > 0")
        return int((dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def _headers(self) -> Dict[str, str]:
        return {
            "Api-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        expected_statuses: Tuple[int, ...] = (200,),
    ) -> Any:
        url = f"{self.API_BASE}{path}"

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=method.upper(),
                url=url,
                headers=self._headers(),
                params=params,
                json=json_body,
                timeout=self.timeout,
            ) as resp:
                txt = await resp.text()

                if resp.status not in expected_statuses:
                    _log(f"Tribute {method.upper()} {path} error HTTP {resp.status}: {txt}")
                    raise RuntimeError(f"Tribute HTTP {resp.status}: {txt}")

                if not txt.strip():
                    return {}

                try:
                    return json.loads(txt)
                except json.JSONDecodeError:
                    return {"raw": txt}

    # ===== Creator Products API =====

    async def list_products(
        self,
        page: int = 1,
        size: int = 20,
        product_type: Optional[str] = None,  # digital|custom|physical
        desc: bool = False,
    ) -> dict:
        params: Dict[str, Any] = {
            "page": int(page),
            "size": int(size),
            "desc": bool(desc),
        }
        if product_type:
            pt = (product_type or "").strip().lower()
            if pt not in {"digital", "custom", "physical"}:
                raise ValueError("Tribute: product_type должен быть digital|custom|physical")
            params["type"] = pt

        data = await self._request("GET", "/products", params=params, expected_statuses=(200,))
        if not isinstance(data, dict):
            raise RuntimeError(f"Tribute: неожиданный ответ list_products: {data}")
        return data

    async def get_product(self, product_id: int) -> dict:
        if not isinstance(product_id, int) or product_id <= 0:
            raise ValueError("Tribute: product_id должен быть положительным int")

        data = await self._request("GET", f"/products/{product_id}", expected_statuses=(200,))
        if not isinstance(data, dict):
            raise RuntimeError(f"Tribute: неожиданный ответ get_product: {data}")
        return data

    async def cancel_digital_product_purchase(self, purchase_id: int) -> dict:
        """
        Важно: по докам Tribute этот endpoint работает для refund только для Stars-оплат.
        Для карт/других методов может не сработать.
        """
        if not isinstance(purchase_id, int) or purchase_id <= 0:
            raise ValueError("Tribute: purchase_id должен быть положительным int")

        data = await self._request(
            "POST",
            f"/products/purchases/{purchase_id}/cancel",
            expected_statuses=(200,),
        )
        if not isinstance(data, dict):
            raise RuntimeError(f"Tribute: неожиданный ответ cancel_digital_product_purchase: {data}")
        return data

    # ===== Хелперы для бота (выдача ссылки на товар по сумме) =====

    def get_configured_product_id(self, amount_rub: int) -> Optional[int]:
        return self._product_ids_by_amount.get(int(amount_rub))

    def get_topup_product_url(self, amount_rub: int, prefer_web_link: bool = True) -> str:
        """
        Быстрый путь: взять ссылку из ENV/config.
        amount_rub: 300 / 600 / 900
        """
        amount_rub = int(amount_rub)
        if amount_rub not in self._product_links_by_amount:
            raise ValueError(f"Tribute: неподдерживаемая сумма пополнения {amount_rub}")

        row = self._product_links_by_amount.get(amount_rub) or {}
        if prefer_web_link:
            url = (row.get("webLink") or row.get("link") or "").strip()
        else:
            url = (row.get("link") or row.get("webLink") or "").strip()

        if not url:
            raise RuntimeError(
                f"Tribute: не задана ссылка на товар для {amount_rub} ₽ "
                f"(TRIBUTE_PRODUCT_WEBLINK_{amount_rub} / TRIBUTE_PRODUCT_LINK_{amount_rub})"
            )

        return url

    async def find_product_by_amount_rub(
        self,
        amount_rub: int,
        *,
        allow_statuses: Tuple[str, ...] = ("approved", "pending", "new"),
        prefer_web_link: bool = True,
        scan_pages: int = 5,
        page_size: int = 100,
    ) -> dict:
        """
        Fallback: ищет товар по API среди products.
        Ищет currency=rub и amount=amount_rub*100.
        """
        amount_rub = int(amount_rub)
        if amount_rub <= 0:
            raise ValueError("Tribute: amount_rub должен быть > 0")

        target_minor = self.amount_major_to_minor(amount_rub, "rub")
        allowed = {str(x).strip().lower() for x in allow_statuses}

        # Сначала попробуем по конфигурируемому product_id (если задан)
        pid = self.get_configured_product_id(amount_rub)
        if pid:
            p = await self.get_product(pid)
            p_amount = _to_int(p.get("amount"))
            p_currency = str(p.get("currency") or "").strip().lower()
            p_status = str(p.get("status") or "").strip().lower()
            if p_amount == target_minor and p_currency == "rub" and (not allowed or p_status in allowed):
                return p

        # Иначе сканируем список
        for page in range(1, int(scan_pages) + 1):
            resp = await self.list_products(page=page, size=page_size, desc=True)
            rows = resp.get("rows") or []
            if not isinstance(rows, list):
                rows = []

            for p in rows:
                if not isinstance(p, dict):
                    continue
                p_amount = _to_int(p.get("amount"))
                p_currency = str(p.get("currency") or "").strip().lower()
                p_status = str(p.get("status") or "").strip().lower()

                if p_amount != target_minor:
                    continue
                if p_currency != "rub":
                    continue
                if allowed and p_status not in allowed:
                    continue

                return p

            meta = resp.get("meta") or {}
            total = _to_int(meta.get("total"))
            limit = _to_int(meta.get("limit")) or page_size
            if total is not None and page * limit >= total:
                break

        raise RuntimeError(f"Tribute: не найден подходящий товар для {amount_rub} ₽")

    async def get_topup_product_url_fallback_api(self, amount_rub: int, prefer_web_link: bool = True) -> str:
        """
        Если ссылки не заданы в ENV — можно вызвать этот метод,
        он найдёт товар по API и вернёт webLink/link.
        """
        p = await self.find_product_by_amount_rub(amount_rub, prefer_web_link=prefer_web_link)
        url = ""
        if prefer_web_link:
            url = str(p.get("webLink") or p.get("link") or "").strip()
        else:
            url = str(p.get("link") or p.get("webLink") or "").strip()

        if not url:
            raise RuntimeError(f"Tribute: у товара для {amount_rub} ₽ нет webLink/link")
        return url