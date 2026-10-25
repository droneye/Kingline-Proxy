# handlers/payments/tribute_webhook_server.py

import os
import json
import hmac
import hashlib
from typing import Any, Optional, Dict

from aiohttp import web

from handlers.billing_db import BillingDB
from handlers.payments.tribute import TRIBUTE_API_KEY

# Если есть логгер и db в config.py — используем
try:
    from config import logger, db  # type: ignore
except Exception:
    logger = None
    db = None  # type: ignore


def _log(msg: str):
    try:
        if logger:
            logger.info(msg)
        else:
            print(msg)
    except Exception:
        pass


def _log_exc(msg: str):
    try:
        if logger:
            logger.exception(msg)
        else:
            print(msg)
    except Exception:
        pass


TRIBUTE_WEBHOOK_PATH_SECRET = os.getenv("TRIBUTE_WEBHOOK_PATH_SECRET", "").strip()
BIND_HOST = os.getenv("TRIBUTE_WEBHOOK_BIND_HOST", "127.0.0.1").strip()
BIND_PORT = int(os.getenv("TRIBUTE_WEBHOOK_BIND_PORT", "8088"))

# Автосписание при refund (по умолчанию выключено)
TRIBUTE_ENABLE_AUTO_REFUND_DEBIT = os.getenv("TRIBUTE_ENABLE_AUTO_REFUND_DEBIT", "0").strip() in {
    "1", "true", "yes", "on"
}

# Опциональные whitelist ID товаров для топапа
def _safe_int_env(name: str) -> Optional[int]:
    try:
        v = os.getenv(name, "").strip()
        if not v:
            return None
        return int(v)
    except Exception:
        return None


TOPUP_PRODUCT_ID_TO_RUB: Dict[int, int] = {}
for _rub in (300, 600, 900):
    _pid = _safe_int_env(f"TRIBUTE_PRODUCT_ID_{_rub}")
    if _pid:
        TOPUP_PRODUCT_ID_TO_RUB[_pid] = _rub

if not TRIBUTE_WEBHOOK_PATH_SECRET:
    raise RuntimeError("Не задан TRIBUTE_WEBHOOK_PATH_SECRET")

if not TRIBUTE_API_KEY:
    raise RuntimeError("Не задан TRIBUTE_API_KEY (нужен для проверки trbt-signature)")

WEBHOOK_PATH = f"/tribute/webhook/{TRIBUTE_WEBHOOK_PATH_SECRET}"


# ===== DB helpers =====

def _ensure_tribute_tables() -> None:
    if db is None:
        raise RuntimeError("config.db не найден — нужен доступ к SQLite для webhook-обработки")

    try:
        with db._connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tribute_purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    purchase_id TEXT UNIQUE,
                    tg_id INTEGER,
                    product_id INTEGER,
                    amount_minor INTEGER,
                    currency TEXT,
                    credited_amount_rub INTEGER DEFAULT 0,
                    status TEXT,
                    event_name TEXT,
                    raw_payload TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
    except Exception:
        _log_exc("Не удалось создать таблицу tribute_purchases")
        raise


def _get_tribute_purchase(purchase_id: str) -> Optional[dict]:
    if db is None:
        return None
    try:
        with db._connect() as conn:  # type: ignore[attr-defined]
            cur = conn.cursor()
            cur.execute(
                """
                SELECT purchase_id, tg_id, product_id, amount_minor, currency,
                       credited_amount_rub, status, event_name
                FROM tribute_purchases
                WHERE purchase_id = ?
                """,
                (purchase_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "purchase_id": row[0],
                "tg_id": row[1],
                "product_id": row[2],
                "amount_minor": row[3],
                "currency": row[4],
                "credited_amount_rub": row[5],
                "status": row[6],
                "event_name": row[7],
            }
    except Exception:
        _log_exc(f"Не удалось прочитать tribute_purchases для purchase_id={purchase_id}")
        return None


def _upsert_tribute_purchase(
    *,
    purchase_id: str,
    tg_id: Optional[int],
    product_id: Optional[int],
    amount_minor: Optional[int],
    currency: Optional[str],
    credited_amount_rub: Optional[int],
    status: str,
    event_name: str,
    raw_payload: str,
) -> None:
    if db is None:
        raise RuntimeError("config.db не найден")

    status = (status or "").strip() or "unknown"
    event_name = (event_name or "").strip() or "unknown"

    try:
        with db._connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO tribute_purchases (
                    purchase_id, tg_id, product_id, amount_minor, currency,
                    credited_amount_rub, status, event_name, raw_payload, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(purchase_id) DO UPDATE SET
                    tg_id = COALESCE(excluded.tg_id, tribute_purchases.tg_id),
                    product_id = COALESCE(excluded.product_id, tribute_purchases.product_id),
                    amount_minor = COALESCE(excluded.amount_minor, tribute_purchases.amount_minor),
                    currency = COALESCE(excluded.currency, tribute_purchases.currency),
                    credited_amount_rub = CASE
                        WHEN excluded.credited_amount_rub IS NULL THEN tribute_purchases.credited_amount_rub
                        ELSE excluded.credited_amount_rub
                    END,
                    status = excluded.status,
                    event_name = excluded.event_name,
                    raw_payload = excluded.raw_payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    purchase_id,
                    tg_id,
                    product_id,
                    amount_minor,
                    currency,
                    credited_amount_rub if credited_amount_rub is not None else None,
                    status,
                    event_name,
                    raw_payload[:20000],
                ),
            )
            conn.commit()
    except Exception:
        _log_exc(f"Не удалось upsert tribute_purchases для purchase_id={purchase_id}")
        raise


# ===== misc helpers =====

def _to_int(v: Any) -> Optional[int]:
    try:
        if v is None or isinstance(v, bool):
            return None
        return int(str(v).strip())
    except Exception:
        return None


def _extract_event_name(payload: Any) -> str:
    if isinstance(payload, dict):
        v = payload.get("name")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown"


def _extract_payload_obj(payload: Any) -> dict:
    if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
        return payload["payload"]  # type: ignore[return-value]
    return {}


def _normalize_currency(v: Any) -> str:
    return str(v or "").strip().lower()


def _verify_signature(raw_body: bytes, signature_header: str, api_key: str) -> bool:
    """
    Tribute creator webhooks: trbt-signature = HMAC-SHA256(raw_body, api_key)
    """
    if not signature_header or not api_key:
        return False

    provided = signature_header.strip()
    # На случай префикса формата "sha256=<hex>"
    if "=" in provided and provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1].strip()

    expected = hmac.new(
        api_key.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(provided.lower(), expected.lower())


def _resolve_credit_amount_rub(payload_obj: dict) -> Optional[int]:
    """
    Определяем, сколько рублей зачислять по покупке.
    Приоритет:
      1) Whitelist по product_id (TRIBUTE_PRODUCT_ID_300/600/900)
      2) Fallback по amount/currency из payload (только RUB и суммы 300/600/900)
    """
    product_id = _to_int(payload_obj.get("product_id"))
    amount_minor = _to_int(payload_obj.get("amount"))
    currency = _normalize_currency(payload_obj.get("currency"))

    # 1) Строгий путь через product_id whitelist (если настроен)
    if TOPUP_PRODUCT_ID_TO_RUB:
        if product_id in TOPUP_PRODUCT_ID_TO_RUB:
            return int(TOPUP_PRODUCT_ID_TO_RUB[product_id])  # type: ignore[index]
        return None  # whitelist включен, но товар не входит в список

    # 2) Fallback по сумме (если product_id whitelist не задан)
    if currency != "rub" or amount_minor is None or amount_minor <= 0:
        return None
    if amount_minor % 100 != 0:
        return None

    rub = amount_minor // 100
    if rub in (300, 600, 900):
        return int(rub)
    return None


# ===== HTTP handlers =====

async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok\n", content_type="text/plain")


async def handle_tribute_webhook(request: web.Request) -> web.Response:
    """
    Creator API webhook handler (без Shop API).

    Обрабатывает:
      - new_digital_product -> зачисление баланса
      - digital_product_refunded -> (опционально) списание назад

    Идемпотентность:
      - ключ = purchase_id (из payload)
    """
    # 1) Считываем raw body (для HMAC)
    try:
        raw_body = await request.read()
    except Exception:
        _log_exc("Tribute webhook: failed to read request body")
        return web.json_response({"ok": False, "error": "read_body_failed"}, status=400)

    # 2) Проверяем подпись
    signature = request.headers.get("trbt-signature", "")
    if not _verify_signature(raw_body, signature, TRIBUTE_API_KEY):
        _log("Tribute webhook: invalid signature")
        return web.json_response({"ok": False, "error": "invalid_signature"}, status=401)

    # 3) Парсим JSON
    raw_text = raw_body.decode("utf-8", errors="replace")
    try:
        envelope = json.loads(raw_text) if raw_text.strip() else {}
    except json.JSONDecodeError:
        _log(f"Tribute webhook: invalid JSON: {raw_text[:1000]}")
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    if not isinstance(envelope, dict):
        _log(f"Tribute webhook: payload is not object: {type(envelope)}")
        return web.json_response({"ok": False, "error": "invalid_payload"}, status=400)

    event_name = _extract_event_name(envelope)
    payload = _extract_payload_obj(envelope)

    _log(
        "Tribute webhook received: "
        f"name={event_name}, payload={json.dumps(envelope, ensure_ascii=False)[:4000]}"
    )

    # Инициализируем таблицу на всякий случай
    try:
        _ensure_tribute_tables()
    except Exception:
        return web.json_response({"ok": False, "error": "db_init_failed"}, status=500)

    # ===== УСПЕШНАЯ ПОКУПКА ЦИФРОВОГО ТОВАРА =====
    if event_name == "new_digital_product":
        purchase_id = _to_int(payload.get("purchase_id"))
        tg_id = _to_int(payload.get("telegram_user_id"))
        product_id = _to_int(payload.get("product_id"))
        amount_minor = _to_int(payload.get("amount"))
        currency = _normalize_currency(payload.get("currency"))

        if purchase_id is None or tg_id is None:
            _log(
                f"Tribute webhook: missing purchase_id/tg_id in new_digital_product "
                f"(purchase_id={purchase_id}, tg_id={tg_id})"
            )
            return web.json_response({"ok": True, "ignored": "missing_required_fields"}, status=200)

        purchase_key = str(purchase_id)
        existing = _get_tribute_purchase(purchase_key)
        if existing and str(existing.get("status") or "") == "succeeded":
            _log(f"Tribute webhook: purchase {purchase_key} already succeeded, skip credit")
            return web.json_response({"ok": True, "status": "already_succeeded"}, status=200)

        credit_rub = _resolve_credit_amount_rub(payload)
        if credit_rub is None:
            # Сохраняем как ignored (пригодится для диагностики)
            try:
                _upsert_tribute_purchase(
                    purchase_id=purchase_key,
                    tg_id=tg_id,
                    product_id=product_id,
                    amount_minor=amount_minor,
                    currency=currency,
                    credited_amount_rub=0,
                    status="ignored_not_topup",
                    event_name=event_name,
                    raw_payload=raw_text,
                )
            except Exception:
                pass

            _log(
                f"Tribute webhook: purchase {purchase_key} ignored "
                f"(not topup product / amount), product_id={product_id}, amount_minor={amount_minor}, currency={currency}"
            )
            return web.json_response({"ok": True, "status": "ignored_not_topup"}, status=200)

        # Регистрируем пользователя в billing, если вдруг записи ещё нет
        try:
            BillingDB.ensure_user_row(int(tg_id), "", f"user{tg_id}")
        except Exception:
            # не критично: change_balance часто сам сработает, но логируем
            _log_exc(f"Tribute webhook: ensure_user_row failed for tg_id={tg_id}")

        # Сначала зачисляем, потом ставим succeeded.
        # Дубли защищаются purchase_id UNIQUE + проверкой existing.status == succeeded.
        try:
            new_balance = BillingDB.change_balance(int(tg_id), int(credit_rub))
        except Exception:
            _log_exc(f"Tribute webhook: change_balance failed for purchase_id={purchase_key}")
            return web.json_response({"ok": True, "error": "credit_failed"}, status=200)

        try:
            _upsert_tribute_purchase(
                purchase_id=purchase_key,
                tg_id=tg_id,
                product_id=product_id,
                amount_minor=amount_minor,
                currency=currency,
                credited_amount_rub=int(credit_rub),
                status="succeeded",
                event_name=event_name,
                raw_payload=raw_text,
            )
        except Exception:
            _log_exc(
                f"Tribute webhook: purchase credited but DB status save failed "
                f"(purchase_id={purchase_key}, tg_id={tg_id}, amount_rub={credit_rub})"
            )
            # Возвращаем 200, чтобы Tribute не ретраил и не дал повторного зачисления
            return web.json_response(
                {
                    "ok": True,
                    "warning": "credited_but_persist_failed",
                    "purchase_id": purchase_key,
                    "tg_id": int(tg_id),
                    "amount_rub": int(credit_rub),
                },
                status=200,
            )

        _log(
            "Tribute webhook: credited balance successfully: "
            f"purchase_id={purchase_key}, tg_id={tg_id}, product_id={product_id}, "
            f"amount_minor={amount_minor}, currency={currency}, amount_rub={credit_rub}, new_balance={new_balance}"
        )

        return web.json_response(
            {
                "ok": True,
                "status": "credited",
                "purchase_id": purchase_key,
                "tg_id": int(tg_id),
                "amount_rub": int(credit_rub),
                "new_balance": int(new_balance or 0),
            },
            status=200,
        )

    # ===== REFUND ЦИФРОВОГО ТОВАРА =====
    if event_name == "digital_product_refunded":
        purchase_id = _to_int(payload.get("purchase_id"))
        tg_id = _to_int(payload.get("telegram_user_id"))
        product_id = _to_int(payload.get("product_id"))
        amount_minor = _to_int(payload.get("amount"))
        currency = _normalize_currency(payload.get("currency"))

        if purchase_id is None:
            _log("Tribute webhook: refund without purchase_id")
            return web.json_response({"ok": True, "ignored": "missing_purchase_id"}, status=200)

        purchase_key = str(purchase_id)
        existing = _get_tribute_purchase(purchase_key)

        # Если записи нет — просто сохраним факт refund (без списания)
        if not existing:
            try:
                _upsert_tribute_purchase(
                    purchase_id=purchase_key,
                    tg_id=tg_id,
                    product_id=product_id,
                    amount_minor=amount_minor,
                    currency=currency,
                    credited_amount_rub=0,
                    status="refunded_unmatched",
                    event_name=event_name,
                    raw_payload=raw_text,
                )
            except Exception:
                pass

            _log(f"Tribute webhook: refund for unknown purchase_id={purchase_key}")
            return web.json_response({"ok": True, "status": "refund_unmatched_saved"}, status=200)

        prev_status = str(existing.get("status") or "")
        credited_amount_rub = _to_int(existing.get("credited_amount_rub")) or 0
        prev_tg_id = _to_int(existing.get("tg_id")) or tg_id

        # Уже обработан refund — идемпотентность
        if prev_status in {"refunded", "refunded_no_debit"}:
            return web.json_response({"ok": True, "status": "already_refunded_processed"}, status=200)

        # По умолчанию просто помечаем refund (без авто-списания)
        if not TRIBUTE_ENABLE_AUTO_REFUND_DEBIT:
            try:
                _upsert_tribute_purchase(
                    purchase_id=purchase_key,
                    tg_id=prev_tg_id,
                    product_id=product_id,
                    amount_minor=amount_minor,
                    currency=currency,
                    credited_amount_rub=credited_amount_rub,
                    status="refunded_no_debit",
                    event_name=event_name,
                    raw_payload=raw_text,
                )
            except Exception:
                _log_exc(f"Tribute webhook: failed to mark refunded_no_debit for purchase_id={purchase_key}")

            _log(
                f"Tribute webhook: refund received (no auto debit) "
                f"purchase_id={purchase_key}, tg_id={prev_tg_id}, credited_amount_rub={credited_amount_rub}"
            )
            return web.json_response({"ok": True, "status": "refunded_no_debit"}, status=200)

        # Автосписание включено
        debit_rub = int(credited_amount_rub or 0)
        if prev_status == "succeeded" and debit_rub > 0 and prev_tg_id is not None:
            try:
                new_balance = BillingDB.change_balance(int(prev_tg_id), -debit_rub)
                _upsert_tribute_purchase(
                    purchase_id=purchase_key,
                    tg_id=prev_tg_id,
                    product_id=product_id,
                    amount_minor=amount_minor,
                    currency=currency,
                    credited_amount_rub=debit_rub,
                    status="refunded",
                    event_name=event_name,
                    raw_payload=raw_text,
                )
                _log(
                    f"Tribute webhook: refund debited successfully purchase_id={purchase_key}, "
                    f"tg_id={prev_tg_id}, debit_rub={debit_rub}, new_balance={new_balance}"
                )
                return web.json_response(
                    {
                        "ok": True,
                        "status": "refunded_debited",
                        "purchase_id": purchase_key,
                        "tg_id": int(prev_tg_id),
                        "debit_rub": debit_rub,
                        "new_balance": int(new_balance or 0),
                    },
                    status=200,
                )
            except Exception:
                _log_exc(f"Tribute webhook: failed to debit refund purchase_id={purchase_key}")
                return web.json_response({"ok": True, "error": "refund_debit_failed"}, status=200)

        # Если нечего списывать — просто помечаем
        try:
            _upsert_tribute_purchase(
                purchase_id=purchase_key,
                tg_id=prev_tg_id,
                product_id=product_id,
                amount_minor=amount_minor,
                currency=currency,
                credited_amount_rub=credited_amount_rub,
                status="refunded_no_debit",
                event_name=event_name,
                raw_payload=raw_text,
            )
        except Exception:
            _log_exc(f"Tribute webhook: failed to mark refund_no_debit for purchase_id={purchase_key}")

        return web.json_response({"ok": True, "status": "refunded_no_debit"}, status=200)

    # ===== Остальные события Tribute creator API — игнорируем =====
    return web.json_response({"ok": True, "ignored": f"event:{event_name}"}, status=200)


def create_app() -> web.Application:
    _ensure_tribute_tables()
    app = web.Application(client_max_size=2 * 1024 * 1024)
    app.router.add_get("/health", handle_health)
    app.router.add_post(WEBHOOK_PATH, handle_tribute_webhook)
    return app


if __name__ == "__main__":
    _log(f"Starting Tribute creator webhook server on {BIND_HOST}:{BIND_PORT}, path={WEBHOOK_PATH}")
    web.run_app(create_app(), host=BIND_HOST, port=BIND_PORT)