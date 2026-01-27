# refferal/ref_db.py

import sqlite3
import json
from typing import Any, Dict, List, Optional, Tuple
from contextlib import closing


class ReferralDB:
    """
    Хранилище реферальной информации в отдельной БД ref.db.

    Таблица referrals:
      - inviter_tg_id : tg_id пригласившего (уникальный)
      - ref_code      : реферальный код пригласившего (уникальный)
      - invited_json  : JSON-массив объектов:
                        [{"tg_id": 123, "rewarded": 0}, ...]
                        rewarded=1 означает, что за этого друга бонус уже выдан.
    """

    def __init__(self, path: str = "ref.db"):
        self.path = path
        self._init_db()

    # ===== internals =====

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, check_same_thread=False)

    def _init_db(self) -> None:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inviter_tg_id INTEGER UNIQUE NOT NULL,
                    ref_code TEXT UNIQUE NOT NULL,
                    invited_json TEXT DEFAULT '[]'
                )
                """
            )
            conn.commit()

    # ===== helpers =====

    @staticmethod
    def _row_to_dict(row: tuple) -> Dict[str, Any]:
        if not row:
            return {}
        cols = ["id", "inviter_tg_id", "ref_code", "invited_json"]
        return dict(zip(cols, row))

    @staticmethod
    def _parse_invited(invited_json: str) -> List[Dict[str, Any]]:
        if not invited_json:
            return []
        try:
            data = json.loads(invited_json)
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []

    @staticmethod
    def _dump_invited(items: List[Dict[str, Any]]) -> str:
        return json.dumps(items, ensure_ascii=False)

    # ===== basic queries =====

    def get_by_inviter(self, inviter_tg_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                SELECT id, inviter_tg_id, ref_code, invited_json
                FROM referrals
                WHERE inviter_tg_id = ?
                """,
                (inviter_tg_id,),
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        code = (code or "").strip().upper()
        if not code:
            return None

        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                SELECT id, inviter_tg_id, ref_code, invited_json
                FROM referrals
                WHERE ref_code = ?
                """,
                (code,),
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_invitation_for_invited(
        self, invited_tg_id: int
    ) -> Optional[Tuple[int, bool]]:
        """
        Найти, есть ли уже запись о том, что invited_tg_id
        использовал чей-то реферальный код.

        Возвращает (inviter_tg_id, rewarded_already) или None.
        """
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                "SELECT inviter_tg_id, invited_json FROM referrals"
            )
            rows = cur.fetchall()

        for inviter_tg_id, invited_json in rows:
            items = self._parse_invited(invited_json or "[]")
            for it in items:
                if int(it.get("tg_id") or 0) == int(invited_tg_id):
                    rewarded = bool(it.get("rewarded"))
                    return int(inviter_tg_id), rewarded

        return None

    # ===== public API =====

    def get_or_create_code(self, inviter_tg_id: int) -> str:
        """
        Гарантирует наличие реферального кода для пользователя.
        Возвращает сам код.
        """
        row = self.get_by_inviter(inviter_tg_id)
        if row and row.get("ref_code"):
            return str(row["ref_code"])

        # создаём новый код
        code = self._generate_unique_code()

        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO referrals (inviter_tg_id, ref_code, invited_json)
                VALUES (?, ?, '[]')
                ON CONFLICT(inviter_tg_id) DO UPDATE SET
                    ref_code = excluded.ref_code
                """,
                (inviter_tg_id, code),
            )
            conn.commit()

        return code

    def add_invited_user(self, inviter_tg_id: int, invited_tg_id: int) -> None:
        """
        Добавляет invited_tg_id в список приглашённых для inviter_tg_id,
        если его там ещё нет. rewarded по умолчанию = 0.
        """
        row = self.get_by_inviter(inviter_tg_id)
        if not row:
            # если почему-то нет строки — создаём код по умолчанию
            ref_code = self._generate_unique_code()
            invited_items: List[Dict[str, Any]] = []
        else:
            ref_code = row["ref_code"]
            invited_items = self._parse_invited(row.get("invited_json") or "[]")

        for it in invited_items:
            if int(it.get("tg_id") or 0) == int(invited_tg_id):
                # уже есть, ничего не меняем
                return

        invited_items.append({"tg_id": int(invited_tg_id), "rewarded": 0})

        invited_json = self._dump_invited(invited_items)

        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO referrals (inviter_tg_id, ref_code, invited_json)
                VALUES (?, ?, ?)
                ON CONFLICT(inviter_tg_id) DO UPDATE SET
                    ref_code = excluded.ref_code,
                    invited_json = excluded.invited_json
                """,
                (inviter_tg_id, ref_code, invited_json),
            )
            conn.commit()

    def mark_rewarded(self, inviter_tg_id: int, invited_tg_id: int) -> None:
        """
        Помечает, что за invited_tg_id бонус уже выдан.
        """
        row = self.get_by_inviter(inviter_tg_id)
        if not row:
            return

        invited_items = self._parse_invited(row.get("invited_json") or "[]")

        changed = False
        for it in invited_items:
            if int(it.get("tg_id") or 0) == int(invited_tg_id):
                if not it.get("rewarded"):
                    it["rewarded"] = 1
                    changed = True

        if not changed:
            return

        invited_json = self._dump_invited(invited_items)

        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE referrals
                SET invited_json = ?
                WHERE inviter_tg_id = ?
                """,
                (invited_json, inviter_tg_id),
            )
            conn.commit()

    # ===== вспомогательные методы =====

    def _generate_unique_code(self, length: int = 8) -> str:
        """
        Генерирует уникальный реферальный код (A-Z0-9) заданной длины.
        """
        import secrets
        import string

        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(length))
            if not self.get_by_code(code):
                return code
