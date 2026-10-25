import sqlite3
from storage import connect as connect_sqlite
from typing import Optional, List, Dict, Any
from contextlib import closing, nullcontext


class SubscriptionDB:
    """
    Хранение информации о пользователях и их подписках.
    + is_blocked: 0/1 — блокировка (VPN выключаем, поддержку запрещаем)
    """

    def __init__(self, path: str = "vpn_bot.db"):
        self.path = path
        self._init_db()

    # ===== internals =====

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path, check_same_thread=False)

    def _init_db(self):
        with self._connect() as conn, closing(conn.cursor()) as cur:
            # Базовое создание таблицы, если её ещё нет
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER UNIQUE,
                    tg_name TEXT,
                    username TEXT,
                    tariff TEXT DEFAULT 'free',
                    sub_id TEXT,
                    sub_name TEXT,
                    key_created_at TEXT,
                    key_expires_at TEXT,
                    client_id1 TEXT,
                    client_id2 TEXT,
                    client_id3 TEXT,
                    balance_rub INTEGER DEFAULT 0,
                    free_used INTEGER DEFAULT 0,
                    subscription_started_at TEXT,
                    subscription_expires_at TEXT,
                    location_pref TEXT DEFAULT 'auto',
                    is_blocked INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Миграции для уже существующей таблицы
            cur.execute("PRAGMA table_info(users)")
            cols = [row[1] for row in cur.fetchall()]

            if "free_used" not in cols:
                cur.execute("ALTER TABLE users ADD COLUMN free_used INTEGER DEFAULT 0")

            if "subscription_started_at" not in cols:
                cur.execute("ALTER TABLE users ADD COLUMN subscription_started_at TEXT")

            if "subscription_expires_at" not in cols:
                cur.execute("ALTER TABLE users ADD COLUMN subscription_expires_at TEXT")

            if "location_pref" not in cols:
                cur.execute("ALTER TABLE users ADD COLUMN location_pref TEXT DEFAULT 'auto'")

            if "is_blocked" not in cols:
                cur.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")

            conn.commit()

    # ===== helpers =====

    def _row_to_dict(self, row) -> Dict[str, Any]:
        if not row:
            return {}
        # ВАЖНО: порядок колонок должен совпадать с SELECT’ами
        cols = [
            "id",
            "tg_id",
            "tg_name",
            "username",
            "tariff",
            "sub_id",
            "sub_name",
            "key_created_at",
            "key_expires_at",
            "client_id1",
            "client_id2",
            "client_id3",
            "balance_rub",
            "free_used",
            "subscription_started_at",
            "subscription_expires_at",
            "location_pref",
            "is_blocked",
            "created_at",
            "updated_at",
        ]
        return dict(zip(cols, row))

    # ===== public API =====

    def upsert_after_create(
        self,
        tg_id: int,
        tg_name: str,
        username: str,
        sub_id: str,
        sub_name: str,
        key_created_at: str,
        key_expires_at: Optional[str],
        client_ids: List[str],
        tariff: str,
        free_used: int,
        *,
        connection: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Обновить/создать запись после успешного создания/обновления ключа.
        (Даты подписки мы сейчас отдельно обновляем через UPDATE.)
        """
        client_ids = list(client_ids or [])[:3]
        c1 = client_ids[0] if len(client_ids) > 0 else None
        c2 = client_ids[1] if len(client_ids) > 1 else None
        c3 = client_ids[2] if len(client_ids) > 2 else None

        with (nullcontext(connection) if connection is not None else self._connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO users (
                    tg_id, tg_name, username, tariff,
                    sub_id, sub_name,
                    key_created_at, key_expires_at,
                    client_id1, client_id2, client_id3,
                    balance_rub, free_used, is_blocked,
                    subscription_started_at, subscription_expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    tg_name=excluded.tg_name,
                    username=excluded.username,
                    tariff=excluded.tariff,
                    sub_id=excluded.sub_id,
                    sub_name=excluded.sub_name,
                    key_created_at=excluded.key_created_at,
                    key_expires_at=excluded.key_expires_at,
                    client_id1=excluded.client_id1,
                    client_id2=excluded.client_id2,
                    client_id3=excluded.client_id3,
                    free_used=excluded.free_used,
                    subscription_started_at=excluded.subscription_started_at,
                    subscription_expires_at=excluded.subscription_expires_at,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    tg_id,
                    tg_name,
                    username,
                    tariff,
                    sub_id,
                    sub_name,
                    key_created_at,
                    key_expires_at,
                    c1,
                    c2,
                    c3,
                    free_used,
                    key_created_at,
                    key_expires_at,
                ),
            )
            if connection is None:
                conn.commit()

    def mark_free_used(self, tg_id: int) -> None:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                "UPDATE users SET free_used = 1, updated_at = CURRENT_TIMESTAMP WHERE tg_id = ?",
                (tg_id,),
            )
            conn.commit()

    def get_user(self, tg_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    tg_id,
                    tg_name,
                    username,
                    tariff,
                    sub_id,
                    sub_name,
                    key_created_at,
                    key_expires_at,
                    client_id1,
                    client_id2,
                    client_id3,
                    balance_rub,
                    free_used,
                    subscription_started_at,
                    subscription_expires_at,
                    location_pref,
                    is_blocked,
                    created_at,
                    updated_at
                FROM users
                WHERE tg_id = ?
                """,
                (tg_id,),
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_user_by_sub_id(self, sub_id: str) -> Optional[Dict[str, Any]]:
        """
        Найти пользователя по sub_id (wanderlusthi-3wwrpjlnjhcqo8x6).
        Используется, например, bot'ом или DNS-сервером.
        """
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    tg_id,
                    tg_name,
                    username,
                    tariff,
                    sub_id,
                    sub_name,
                    key_created_at,
                    key_expires_at,
                    client_id1,
                    client_id2,
                    client_id3,
                    balance_rub,
                    free_used,
                    subscription_started_at,
                    subscription_expires_at,
                    location_pref,
                    is_blocked,
                    created_at,
                    updated_at
                FROM users
                WHERE sub_id = ?
                ORDER BY
                    CASE WHEN COALESCE(is_blocked, 0) = 0 THEN 0 ELSE 1 END,
                    CASE WHEN COALESCE(location_pref, 'auto') != 'auto' THEN 0 ELSE 1 END,
                    updated_at DESC
                LIMIT 1
                """,
                (sub_id,),
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_sub(self, tg_id: int):
        u = self.get_user(tg_id)
        if u and u.get("sub_id"):
            return u["sub_id"], (u.get("username") or "")
        return None

    def list_users(self) -> List[Dict[str, Any]]:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    tg_id,
                    tg_name,
                    username,
                    tariff,
                    sub_id,
                    sub_name,
                    key_created_at,
                    key_expires_at,
                    client_id1,
                    client_id2,
                    client_id3,
                    balance_rub,
                    free_used,
                    subscription_started_at,
                    subscription_expires_at,
                    location_pref,
                    is_blocked,
                    created_at,
                    updated_at
                FROM users
                ORDER BY id ASC
                """
            )
            rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ===== blocking helpers =====

    def is_blocked(self, tg_id: int) -> bool:
        u = self.get_user(tg_id)
        return bool((u or {}).get("is_blocked") or 0)

    def set_blocked(self, tg_id: int, blocked: bool) -> None:
        val = 1 if blocked else 0
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE users
                SET is_blocked = ?, updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = ?
                """,
                (val, tg_id),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO users (tg_id, is_blocked) VALUES (?, ?)",
                    (tg_id, val),
                )
            conn.commit()

    # ===== billing helpers =====

    def ensure_user(self, tg_id: int, tg_name: str, username: str) -> None:
        """
        Гарантирует, что запись есть (для операций с балансом/тарифом).
        """
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO users (tg_id, tg_name, username)
                VALUES (?, ?, ?)
                ON CONFLICT(tg_id) DO NOTHING
                """,
                (tg_id, tg_name, username),
            )
            conn.commit()

    def change_balance(self, tg_id: int, delta: int) -> int:
        """
        Изменить баланс на delta. Возвращает новый баланс.
        """
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE users
                SET balance_rub = balance_rub + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = ?
                """,
                (delta, tg_id),
            )
            if cur.rowcount == 0:
                # если записи не было — создаём с этим балансом
                cur.execute(
                    """
                    INSERT INTO users (tg_id, balance_rub)
                    VALUES (?, ?)
                    ON CONFLICT(tg_id) DO UPDATE SET
                        balance_rub = users.balance_rub + excluded.balance_rub,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (tg_id, delta),
                )
            conn.commit()

            cur.execute("SELECT balance_rub FROM users WHERE tg_id = ?", (tg_id,))
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def set_tariff(self, tg_id: int, tariff: str) -> None:
        """
        Установить тариф ('free' / 'premium' и т.п.)
        """
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE users
                SET tariff = ?, updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = ?
                """,
                (tariff, tg_id),
            )
            if cur.rowcount == 0:
                cur.execute(
                    """
                    INSERT INTO users (tg_id, tariff)
                    VALUES (?, ?)
                    """,
                    (tg_id, tariff),
                )
            conn.commit()

    # ===== location helpers =====

    def get_location_pref(self, tg_id: int) -> str:
        """
        Вернёт 'auto' / 'lt' / 'nl'. По умолчанию — 'auto'.
        """
        user = self.get_user(tg_id)
        loc = (user or {}).get("location_pref") or "auto"
        if loc not in ("auto", "lt", "nl"):
            return "auto"
        return loc

    def set_location_pref(self, tg_id: int, location: str) -> None:
        """
        Установить локацию: 'auto', 'lt', 'nl'.
        """
        if location not in ("auto", "lt", "nl"):
            raise ValueError(f"Unknown location: {location}")

        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE users
                SET location_pref = ?, updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = ?
                """,
                (location, tg_id),
            )
            if cur.rowcount == 0:
                cur.execute(
                    """
                    INSERT INTO users (tg_id, location_pref)
                    VALUES (?, ?)
                    """,
                    (tg_id, location),
                )
            conn.commit()

    # ===== location by sub_id (для DNS) =====

    def get_location_by_sub_id(self, sub_id: str) -> str:
        """
        Вернуть location_pref ('auto' / 'lt' / 'nl') по sub_id.
        Если ничего не нашли или мусор — вернуть 'auto'.
        """
        if not sub_id:
            return "auto"

        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                SELECT location_pref
                FROM users
                WHERE sub_id = ?
                ORDER BY
                    CASE WHEN COALESCE(is_blocked, 0) = 0 THEN 0 ELSE 1 END,
                    CASE WHEN COALESCE(location_pref, 'auto') != 'auto' THEN 0 ELSE 1 END,
                    updated_at DESC
                LIMIT 1
                """,
                (sub_id,),
            )
            row = cur.fetchone()

        if not row:
            return "auto"

        raw = (row[0] or "auto").strip().lower()

        # Немного нормализации, если вдруг в БД окажется "литва"/"🇱🇹" и т.п.
        if raw.startswith("lt"):
            return "lt"
        if raw.startswith("nl"):
            return "nl"
        if raw.startswith("au"):
            return "auto"

        return "auto"
