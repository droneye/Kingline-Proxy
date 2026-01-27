# handlers/admin.py
from __future__ import annotations

from typing import Optional, Tuple, List, Dict, Any
from collections import defaultdict
import random
import string

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_ID, logger, db
from promos_db import promo_db

# ✅ ВАЖНО: у тебя в config сейчас XUI_PANELS, а XUI_SERVERS может не существовать.
try:
    from config import XUI_SERVERS  # type: ignore
except Exception:
    try:
        from config import XUI_PANELS as XUI_SERVERS  # type: ignore
    except Exception:
        XUI_SERVERS = []

from xui import XUIClient


# ====================== TELEGRAM LIMIT HELPERS ======================

TG_LIMIT = 4096

def _chunk_lines(lines: List[str], limit: int = TG_LIMIT) -> List[str]:
    """
    Склеиваем строки в сообщения так, чтобы каждое было <= limit (с запасом).
    """
    safe = max(500, limit - 120)
    out: List[str] = []
    buf: List[str] = []
    size = 0

    for line in lines:
        line = str(line)
        add = len(line) + 1  # \n
        if buf and (size + add) > safe:
            out.append("\n".join(buf))
            buf = [line]
            size = len(line) + 1
        else:
            buf.append(line)
            size += add

    if buf:
        out.append("\n".join(buf))
    return out


# ====================== DB: BLOCK HELPERS ======================

def _db_has_column(table: str, col: str) -> bool:
    try:
        with db._connect() as conn:  # type: ignore[attr-defined]
            cur = conn.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]
            return col in cols
    except Exception:
        return False


def _ensure_block_column() -> None:
    """
    Добавляет users.is_blocked, если колонки нет.
    """
    if _db_has_column("users", "is_blocked"):
        return
    try:
        with db._connect() as conn:  # type: ignore[attr-defined]
            cur = conn.cursor()
            cur.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
            conn.commit()
            logger.info("✅ миграция: добавлена колонка users.is_blocked")
    except Exception as e:
        # если ALTER уже делали — просто игнор
        logger.warning("is_blocked migration skipped/failed: %r", e)


def _set_blocked_flag(tg_id: int, blocked: bool) -> None:
    """
    Ставит флаг блокировки в БД. Работает даже если в SubscriptionDB нет set_blocked().
    """
    _ensure_block_column()
    val = 1 if blocked else 0
    with db._connect() as conn:  # type: ignore[attr-defined]
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET is_blocked = ?, updated_at = CURRENT_TIMESTAMP WHERE tg_id = ?",
            (val, tg_id),
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO users (tg_id, is_blocked) VALUES (?, ?)",
                (tg_id, val),
            )
        conn.commit()


def _get_blocked_flag(tg_id: int) -> int:
    """
    Возвращает 0/1.
    """
    _ensure_block_column()
    try:
        with db._connect() as conn:  # type: ignore[attr-defined]
            cur = conn.cursor()
            cur.execute("SELECT is_blocked FROM users WHERE tg_id = ? LIMIT 1", (tg_id,))
            row = cur.fetchone()
            return int(row[0] or 0) if row else 0
    except Exception:
        return 0


# ====================== ВСПОМОГАТЕЛЬНОЕ ======================

def _find_user_id_by_username(username: str) -> Optional[int]:
    """Поиск tg_id по username в БД (без @)."""
    uname = (username or "").lstrip("@").strip().lower()
    if not uname:
        return None
    for u in db.list_users():
        if (u.get("username") or "").lower() == uname:
            return int(u["tg_id"])
    return None


def _change_balance(tg_id: int, delta: int) -> int:
    """Добавить delta к балансу и вернуть новый баланс."""
    with db._connect() as conn:  # type: ignore[attr-defined]
        cur = conn.cursor()
        cur.execute("SELECT balance_rub FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        if row is None:
            new_val = int(delta)
            cur.execute(
                "INSERT INTO users (tg_id, balance_rub) VALUES (?, ?)",
                (tg_id, new_val),
            )
        else:
            current = int(row[0] or 0)
            new_val = current + int(delta)
            cur.execute(
                "UPDATE users SET balance_rub = ?, updated_at = CURRENT_TIMESTAMP WHERE tg_id = ?",
                (new_val, tg_id),
            )
        conn.commit()
        return new_val


def _set_balance(tg_id: int, value: int) -> int:
    """Установить баланс точно в value и вернуть его."""
    with db._connect() as conn:  # type: ignore[attr-defined]
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE tg_id = ?", (tg_id,))
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO users (tg_id, balance_rub) VALUES (?, ?)",
                (tg_id, int(value)),
            )
        else:
            cur.execute(
                "UPDATE users SET balance_rub = ?, updated_at = CURRENT_TIMESTAMP WHERE tg_id = ?",
                (int(value), tg_id),
            )
        conn.commit()
        return int(value)


def _parse_target_and_amount(update: Update, args: list) -> Tuple[Optional[int], Optional[str]]:
    """
    Возвращает (tg_id, amount_spec) где amount_spec: '+300' | '-300' | '=2000' | '2000'
    Логика:
      - если есть reply_to_message: target = author.reply, amount_spec = единственный аргумент
      - иначе: первый arg = id/@username, второй = amount_spec
    """
    msg = update.message
    if not msg:
        return None, None

    if msg.reply_to_message and args:
        target_id = msg.reply_to_message.from_user.id if msg.reply_to_message.from_user else None
        return target_id, args[0]

    if len(args) >= 2:
        tgt = args[0].strip()
        amount_spec = args[1].strip()
        if tgt.lstrip("-").isdigit():
            return int(tgt), amount_spec
        uid = _find_user_id_by_username(tgt)
        return uid, amount_spec

    return None, None


def _parse_amount_spec(spec: str) -> Tuple[str, int]:
    """
    Возвращает ('set'|'add', value)
      '=2000' -> ('set', 2000)
      '+300'  -> ('add', +300)
      '-300'  -> ('add', -300)
      '2000'  -> ('set', 2000)
    """
    s = (spec or "").replace(" ", "")
    if not s:
        raise ValueError("empty amount")
    if s[0] in "+-":
        return "add", int(s)
    if s[0] == "=":
        return "set", int(s[1:] or "0")
    return "set", int(s)


def _gen_codes(prefix: str, amount: int, n: int) -> List[str]:
    """Генерирует коды вида KL300-XXXXXXXXXX."""
    codes = []
    for _ in range(n):
        tail = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
        codes.append(f"{prefix}{amount}-{tail}")
    return codes


def _list_all_promos_from_db() -> List[Tuple[str, int]]:
    """
    Возвращает список (code, amount) напрямую из таблицы promos.
    """
    try:
        with promo_db._connect() as conn:  # type: ignore[attr-defined]
            cur = conn.cursor()
            cur.execute("SELECT code, amount FROM promos ORDER BY amount, code")
            return [(str(code), int(amount)) for code, amount in cur.fetchall()]
    except Exception as e:
        logger.exception("Не удалось прочитать промокоды: %s", e)
        return []


def _user_tag_from_row(user_row: Dict[str, Any]) -> str:
    uname = user_row.get("username") or ""
    if uname and not str(uname).startswith("@"):
        uname = "@" + str(uname)
    tag = user_row.get("tg_id")
    return f"tg_id={tag} {uname} sub={user_row.get('sub_id')}"


async def _toggle_user_on_all_xui(user_row: Dict[str, Any], *, enabled: bool) -> List[str]:
    """
    enabled=False => block: выключить всех клиентов на всех серверах
    enabled=True  => unblock: включить обратно
    """
    username_clean = (user_row.get("username") or user_row.get("tg_name") or "").strip()
    sub_id = (user_row.get("sub_id") or "").strip()

    results: List[str] = []
    servers = XUI_SERVERS or []
    if not servers:
        return ["⚠️ XUI_SERVERS/XUI_PANELS пуст — некого обходить. Проверь config.py"]

    for s in servers:
        name = s.get("tag") or s.get("name") or s.get("url") or "server"
        try:
            async with XUIClient(
                root_url=s["url"],
                username=s["username"],
                password=s["password"],
                webbasepath=s.get("webbasepath", "") or "",
                timeout=int(s.get("timeout", 20) or 20),
                logger_=logger,
            ) as xui:
                inbounds = await xui.list_inbounds()
                changed = 0
                for inbound in inbounds:
                    changed += await xui.set_user_clients_enabled(
                        inbound,
                        username_clean=username_clean,
                        sub_id=sub_id,
                        enabled=enabled,
                    )
                results.append(f"✅ {name}: {changed} client(s) -> enable={enabled}")
        except Exception as e:
            results.append(f"❌ {name}: ошибка {e!r}")

    return results


# ====================== /list ======================

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    users = db.list_users()
    if not users:
        await update.message.reply_text("База пуста.")
        return

    lines: List[str] = []
    for usr in users:
        username = usr.get("username") or ""
        if username and not username.startswith("@"):
            uname = f"@{username}"
        elif username:
            uname = username
        else:
            uname = "-"

        cids = [usr.get("client_id1"), usr.get("client_id2"), usr.get("client_id3")]
        cids = [c for c in cids if c]

        start = usr.get("subscription_started_at") or "-"
        end = usr.get("subscription_expires_at") or "-"
        loc = usr.get("location_pref") or "auto"

        is_blocked = _get_blocked_flag(int(usr["tg_id"]))
        blocked = "BLOCKED" if is_blocked == 1 else "ok"

        lines.append(
            f"#{usr['id']} | tg_id={usr['tg_id']} | {uname} | "
            f"tariff={usr.get('tariff')} | free_used={usr.get('free_used')} | "
            f"loc={loc} | blocked={blocked} | "
            f"sub={usr.get('sub_id')} | "
            f"cids={cids} | "
            f"balance={usr.get('balance_rub')} | "
            f"start={start} | end={end}"
        )

    # ✅ режем по символам (а не по 25 строк)
    for chunk in _chunk_lines(lines):
        await update.message.reply_text(chunk)


# ====================== /setbal ======================

async def cmd_setbal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    args = context.args or []
    target_id, amount_spec = _parse_target_and_amount(update, args)

    if target_id is None or amount_spec is None:
        await update.message.reply_text(
            "Использование:\n"
            "  (ответом) /setbal +300  — прибавить\n"
            "  (ответом) /setbal =2000 — установить\n"
            "  /setbal 123456789 +500\n"
            "  /setbal @username =1500\n"
            "  /setbal 123456789 0",
        )
        return

    try:
        mode, value = _parse_amount_spec(amount_spec)
    except Exception:
        await update.message.reply_text("Некорректная сумма. Пример: +300, -300, =2000, 0")
        return

    try:
        if mode == "add":
            new_balance = _change_balance(target_id, value)
            sign = "+" if value > 0 else ""
            await update.message.reply_text(
                f"Готово. Баланс пользователя {target_id}: {sign}{value} ₽ → {new_balance} ₽"
            )
        else:
            new_balance = _set_balance(target_id, value)
            await update.message.reply_text(
                f"Готово. Баланс пользователя {target_id} установлен на {new_balance} ₽"
            )
    except Exception as e:
        logger.exception("setbal failed: %s", e)
        await update.message.reply_text(f"Ошибка при обновлении баланса: {e}")


# ====================== /block /unblock ======================

async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /block <tg_id>
    - ставит is_blocked=1 (автомиграция колонки)
    - выключает все устройства (enable=false) на всех 3x-ui серверах
    - запрещает поддержку: закрывает активную сессию
    """
    if not update.message:
        return
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    args = context.args or []
    if not args or not args[0].lstrip("-").isdigit():
        await update.message.reply_text("Использование: /block <tg_id>")
        return

    target_id = int(args[0])
    user_row = db.get_user(target_id)
    if not user_row:
        await update.message.reply_text(f"Пользователь {target_id} не найден в БД.")
        return

    # 1) Блокируем в БД
    _set_blocked_flag(target_id, True)

    # 2) Закрыть поддержку (если была)
    try:
        from handlers.support import close_support_session  # noqa
        close_support_session(target_id)
    except Exception as e:
        logger.warning("close_support_session not available: %r", e)

    # 3) Выключить клиентов на всех серверах
    lines = await _toggle_user_on_all_xui(user_row, enabled=False)

    # 4) Уведомление пользователю
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="⛔️ Ваш аккаунт заблокирован. Доступ к VPN и поддержке ограничен.",
        )
    except Exception:
        pass

    await update.message.reply_text(
        "✅ Заблокировал пользователя.\n"
        f"{_user_tag_from_row(user_row)}\n\n" +
        "\n".join(lines)
    )


async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /unblock <tg_id>
    - ставит is_blocked=0
    - включает все устройства (enable=true) на всех 3x-ui серверах
    """
    if not update.message:
        return
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    args = context.args or []
    if not args or not args[0].lstrip("-").isdigit():
        await update.message.reply_text("Использование: /unblock <tg_id>")
        return

    target_id = int(args[0])
    user_row = db.get_user(target_id)
    if not user_row:
        await update.message.reply_text(f"Пользователь {target_id} не найден в БД.")
        return

    _set_blocked_flag(target_id, False)

    lines = await _toggle_user_on_all_xui(user_row, enabled=True)

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="✅ Ваш аккаунт разблокирован. Доступ к VPN восстановлен.",
        )
    except Exception:
        pass

    await update.message.reply_text(
        "✅ Разблокировал пользователя.\n"
        f"{_user_tag_from_row(user_row)}\n\n" +
        "\n".join(lines)
    )


# ====================== /admin ======================

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    text = (
        "🛠 <b>Админ-панель</b>\n\n"
        "Доступные команды:\n\n"

        "👥 <b>Пользователи</b>\n"
        "• /list — список всех пользователей\n"
        "• /setbal &lt;tg_id/@user&gt; &lt;+Δ / =V / V&gt; — изменить или установить баланс\n"
        "• /block &lt;tg_id&gt; — заблокировать пользователя (VPN OFF + поддержка OFF)\n"
        "• /unblock &lt;tg_id&gt; — разблокировать пользователя (VPN ON)\n\n"

        "💬 <b>Поддержка</b>\n"
        "• /on — войти в чат поддержки с пользователем\n"
        "• /off — завершить чат поддержки\n\n"

        "📢 <b>Рассылки</b>\n"
        "• /send on — включить массовую рассылку (сообщения копятся)\n"
        "• /send off — отправить накопленные сообщения всем пользователям\n"
        "• /broadcast &lt;tg_id&gt; on — начать таргет-рассылку конкретному пользователю\n"
        "• /broadcast off — завершить и отправить накопленные сообщения этому пользователю\n\n"

        "🎟️ <b>Промокоды</b>\n"
        "• /promo — показать все активные промокоды\n"
        "• /promo add — сгенерировать по 5 промокодов на 300, 600 и 900 ₽\n\n"

        "ℹ️ <b>Служебные</b>\n"
        "• /admin — показать это меню администратора\n"
        "• /key — показать ссылку доступа (проверка)\n"
    )

    await update.message.reply_text(text, parse_mode="HTML")


# ====================== /promo ======================

async def cmd_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    args = context.args or []
    if args and args[0].lower() == "add":
        created: Dict[int, List[str]] = {}
        for amount in (300, 600, 900):
            try:
                codes = promo_db.generate_many(amount=amount, n=5, prefix=f"KL{amount}")
            except TypeError:
                codes = _gen_codes("KL", amount, 5)
                items = [(c, amount) for c in codes]
                try:
                    promo_db.bulk_add(items)  # type: ignore[attr-defined]
                except Exception:
                    for c in codes:
                        promo_db.add_code(c, amount)
            created[amount] = codes

        parts = []
        for amt in (300, 600, 900):
            lst = created.get(amt, [])
            if lst:
                parts.append(f"=== {amt} ₽ ===\n" + "\n".join(lst))
        await update.message.reply_text("\n\n".join(parts) or "Не сгенерировано ни одного кода.")
        return

    items = _list_all_promos_from_db()
    if not items:
        await update.message.reply_text("Промокоды отсутствуют.")
        return

    grouped: defaultdict[int, List[str]] = defaultdict(list)
    for code, amount in sorted(items, key=lambda x: (x[1], x[0])):
        grouped[amount].append(code)

    # 🔒 тоже учитываем лимит сообщений
    blocks: List[str] = []
    for amount in sorted(grouped.keys()):
        blocks.append(f"💳 <b>{amount} ₽</b>\n" + "\n".join(grouped[amount]))

    header = "📦 <b>Доступные промокоды</b>\n\n"
    text = header + "\n\n".join(blocks)

    if len(text) <= 3900:
        await update.message.reply_text(text, parse_mode="HTML")
        return

    await update.message.reply_text("📦 <b>Доступные промокоды</b>", parse_mode="HTML")
    # Отправляем по кускам
    for b in blocks:
        if len(b) <= 3900:
            await update.message.reply_text(b, parse_mode="HTML")
        else:
            # если вдруг ОЧЕНЬ много — режем по строкам
            lines = b.splitlines()
            for chunk in _chunk_lines(lines, limit=3900):
                await update.message.reply_text(chunk, parse_mode="HTML")
