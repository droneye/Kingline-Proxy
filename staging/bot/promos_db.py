# promos_db.py
import os
import sqlite3
from storage import connect as connect_sqlite
import string
import random
from contextlib import closing
from typing import Iterable, Optional, Tuple, List


class PromoDB:
    """
    Простая БД промокодов.
    - Таблица promos(code TEXT PRIMARY KEY, amount INTEGER, created_at TEXT, used_at TEXT)
    - Код можно активировать ровно 1 раз: при redeem() строка удаляется (или помечается).
    """

    def __init__(self, path: str = "promos.db"):
        self.path = path
        self._init_db()

    # ---------- internals ----------

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.path, check_same_thread=False)

    def _init_db(self) -> None:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS promos(
                    code TEXT PRIMARY KEY,
                    amount INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    used_at TEXT
                )
                """
            )
            conn.commit()

    # ---------- helpers ----------

    @staticmethod
    def _normalize(code: str) -> str:
        return (code or "").strip().upper()

    @staticmethod
    def _gen_code(prefix: str = "KL", length: int = 10) -> str:
        alphabet = string.ascii_uppercase + string.digits
        body = "".join(random.choice(alphabet) for _ in range(length))
        return f"{prefix}-{body}"

    # ---------- public API ----------

    def add_code(self, code: str, amount: int) -> None:
        code = self._normalize(code)
        if not code:
            raise ValueError("empty promo code")
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                "INSERT OR REPLACE INTO promos(code, amount) VALUES(?, ?)",
                (code, int(amount)),
            )
            conn.commit()

    def bulk_add(self, items: Iterable[Tuple[str, int]]) -> None:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.executemany(
                "INSERT OR REPLACE INTO promos(code, amount) VALUES(?, ?)",
                [(self._normalize(c), int(a)) for c, a in items],
            )
            conn.commit()

    def generate_many(self, amount: int, n: int, prefix: str = "KL") -> List[str]:
        """Сгенерировать n промокодов на сумму amount и сохранить их в БД."""
        codes = []
        for _ in range(n):
            code = self._gen_code(prefix=prefix)
            self.add_code(code, amount)
            codes.append(code)
        return codes

    def redeem(self, code: str) -> Optional[int]:
        """
        Погасить промокод.
        - возвращает сумму (int), если успешен,
        - None, если кода нет.
        Код удаляется (one-time).
        """
        code_n = self._normalize(code)
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute("SELECT amount FROM promos WHERE code = ?", (code_n,))
            row = cur.fetchone()
            if not row:
                return None
            amount = int(row[0] or 0)
            # чтобы нельзя было повторно применить
            cur.execute("DELETE FROM promos WHERE code = ?", (code_n,))
            conn.commit()
            return amount


# Глобальный экземпляр для использования в хендлерах
promo_db = PromoDB(os.getenv("PROMOS_DB_PATH", "promos.db"))
