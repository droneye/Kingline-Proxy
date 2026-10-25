#!/usr/bin/env python3
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import pickle
import re
import secrets
import smtplib
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from functools import lru_cache
from html import escape
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlparse


DB_PATH = os.getenv("KINGLINE_DB_PATH", "/root/kingline/vpn_bot.db")
KINGLINE_DIR = os.getenv("KINGLINE_BOT_DIR", "/root/kingline")
HOST = os.getenv("KINGLINE_API_HOST", "127.0.0.1")
PORT = int(os.getenv("KINGLINE_API_PORT", "8787"))
COOKIE_NAME = "kingline_staging_session"
SESSION_DAYS = 7
PASSWORD_ITERATIONS = 310_000
MAX_BODY_BYTES = 12 * 1024 * 1024
JSON_CONTENT_TYPE = "application/json"
POST_SECURITY_HEADER = "X-Kingline-Request"
POST_SECURITY_VALUE = "fetch"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ADMIN_EMAILS_ENV = os.getenv("KINGLINE_ADMIN_EMAILS", "operator@example.invalid")
PRIMARY_ADMIN_EMAIL = os.getenv("KINGLINE_OWNER_EMAIL", "operator@example.invalid").strip().lower()
SUPPORT_ADMIN_NAME = os.getenv("KINGLINE_SUPPORT_NAME", "Vladislav").strip() or "Vladislav"
SUPPORT_UPLOAD_DIR = os.getenv(
    "KINGLINE_SUPPORT_UPLOAD_DIR",
    "/var/www/kingline_site/support_uploads",
)
SUPPORT_UPLOAD_URL_PREFIX = "/support_uploads"
ADMIN_PERMISSION_KEYS = (
    "analytics",
    "balance",
    "message",
    "block",
    "promos",
    "dns",
    "support",
    "payments",
    "broadcast",
    "delete_users",
    "nodes",
    "node_credentials",
    "technical",
    "vpn_config",
    "documentation",
    "user_transport",
)
TOPUP_AMOUNTS = (300, 600, 900)
TRIAL_HOURS = 24
TELEGRAM_BONUS_DAYS = 3
MAGIC_LINK_MINUTES = 15
EMAIL_VERIFY_MINUTES = 60
PASSWORD_RESET_MINUTES = 30
REGISTRATION_CODE_ATTEMPTS = 6
EMAIL_LINK_CODE_ATTEMPTS = 6
SITE_URL = os.getenv("KINGLINE_SITE_URL", "https://kingline.online").rstrip("/")
TELEGRAM_BOT_URL = os.getenv("KINGLINE_TELEGRAM_BOT_URL", "https://t.me/kinglinbot").rstrip("/")
TELEGRAM_INTERNAL_EMAIL_DOMAIN = os.getenv(
    "KINGLINE_TELEGRAM_EMAIL_DOMAIN",
    "telegram.kingline.local",
).strip().lower()
TELEGRAM_WEBAPP_AUTH_MAX_AGE_SECONDS = int(
    os.getenv("KINGLINE_TELEGRAM_WEBAPP_AUTH_MAX_AGE_SECONDS", str(24 * 60 * 60))
)
BRAND_NAME = "Kingline Proxy"
XRAY_RESTART_DELAY_SECONDS = int(os.getenv("KINGLINE_XRAY_RESTART_DELAY_SECONDS", "15"))
EMAIL_REMINDER_INTERVAL_SECONDS = 60
TELEGRAM_LINK_REMINDER_DELAY_HOURS = 12
EMAIL_REMINDER_WINDOWS = (
    ("24h", 24 * 60 * 60, 10 * 60),
    ("1h", 60 * 60, 5 * 60),
)
LOGIN_EMAIL_WINDOW_SECONDS = 15 * 60
LOGIN_EMAIL_MAX_ATTEMPTS = 5
LOGIN_EMAIL_LOCK_MINUTES = 15
ADMIN_2FA_MINUTES = 10
ADMIN_2FA_ATTEMPTS = 6

RATE_LIMIT_WINDOW_SECONDS = 10 * 60
RATE_LIMIT_MAX_ATTEMPTS = 8
RATE_LIMITS = {}
SENSITIVE_POST_RATE_LIMIT_PATHS = {
    "/api/auth/magic-link",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/telegram-webapp",
    "/api/auth/password-reset",
    "/api/auth/password-reset-confirm",
    "/api/account/email-link/start",
    "/api/account/email-link/confirm",
    "/api/account/trial",
    "/api/account/premium",
    "/api/account/topup",
    "/api/account/check-payment",
    "/api/account/promo",
    "/api/account/referral-code",
    "/api/admin/2fa/send",
    "/api/admin/2fa/verify",
    "/api/admin/user/delete",
}
EMAIL_REMINDER_THREAD_STARTED = False
XRAY_RESTART_PENDING = False
XRAY_RESTART_PENDING_LOCK = threading.Lock()
XUI_ONLINE_CACHE_TTL_SECONDS = 5
XUI_ONLINE_CACHE = {"created": 0.0, "data": None, "refreshing": False}
XUI_ONLINE_CACHE_LOCK = threading.Lock()
XUI_ONLINE_TIMEOUT_SECONDS = 4
XUI_ANALYTICS_TIMEOUT_SECONDS = 8
XUI_ANALYTICS_POLL_INTERVAL_SECONDS = 60
XUI_ANALYTICS_SESSION_GAP_SECONDS = 3 * 60
XUI_ANALYTICS_THREAD_STARTED = False
XUI_ADMIN_ANALYTICS_CACHE_TTL_SECONDS = int(
    os.getenv("KINGLINE_XUI_ADMIN_ANALYTICS_CACHE_TTL_SECONDS", "15")
)
XUI_ADMIN_ANALYTICS_CACHE = {}
XUI_ADMIN_ANALYTICS_CACHE_LOCK = threading.Lock()
XUI_ALL_ACCESS_LOG_PATH = os.getenv("KINGLINE_XUI_ALL_ACCESS_LOG", "/root/user_data/all_access.log")
XUI_ACCESS_IMPORT_CHUNK_BYTES = int(
    os.getenv("KINGLINE_XUI_ACCESS_IMPORT_CHUNK_BYTES", str(64 * 1024 * 1024))
)
XUI_ACCESS_IMPORT_IDLE_SECONDS = float(os.getenv("KINGLINE_XUI_ACCESS_IMPORT_IDLE_SECONDS", "2"))
XUI_ACCESS_IMPORT_ACTIVE_SECONDS = float(os.getenv("KINGLINE_XUI_ACCESS_IMPORT_ACTIVE_SECONDS", "0.15"))
XUI_ACCESS_IMPORT_THREAD_STARTED = False
XUI_ACCESS_IMPORT_LOCK = threading.Lock()
XUI_INSIGHTS_PREWARM_INTERVAL_SECONDS = int(
    os.getenv("KINGLINE_XUI_INSIGHTS_PREWARM_INTERVAL_SECONDS", "45")
)
XUI_INSIGHTS_PREWARM_THREAD_STARTED = False
XUI_LIVE_SITE_LOOKBACK_SECONDS = int(os.getenv("KINGLINE_XUI_LIVE_SITE_LOOKBACK_SECONDS", "180"))
XUI_LIVE_SITE_TAIL_BYTES = int(os.getenv("KINGLINE_XUI_LIVE_SITE_TAIL_BYTES", str(2 * 1024 * 1024)))
XUI_LIVE_SITE_CACHE_TTL_SECONDS = 3
XUI_LIVE_SITE_CACHE = {"created": 0.0, "data": None}
XUI_LIVE_SITE_CACHE_LOCK = threading.Lock()
XUI_USER_INSIGHTS_CACHE_TTL_SECONDS = int(os.getenv("KINGLINE_XUI_USER_INSIGHTS_CACHE_TTL_SECONDS", "300"))
XUI_USER_INSIGHTS_CACHE = {}
XUI_USER_INSIGHTS_CACHE_LOCK = threading.Lock()
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
DOMAIN_CATEGORY_DIR = os.path.join(SERVER_DIR, "data", "domain_categories")
ADULT_DOMAINS_PATH = os.getenv(
    "KINGLINE_ADULT_DOMAINS_PATH",
    os.path.join(SERVER_DIR, "data", "adult_domains.txt"),
)
DOMAIN_CATEGORY_CACHE_PATH = os.getenv(
    "KINGLINE_DOMAIN_CATEGORY_CACHE_PATH",
    os.path.join(SERVER_DIR, "data", "domain_categories.cache.pkl"),
)
ADULT_DOMAIN_SET = None
ADULT_DOMAIN_LOCK = threading.Lock()
DOMAIN_CATEGORY_SOURCE_FILES = (
    ("Порносайты", (ADULT_DOMAINS_PATH,)),
    ("Реклама", (os.path.join(DOMAIN_CATEGORY_DIR, "ads.txt"),)),
    ("Трекеры", (os.path.join(DOMAIN_CATEGORY_DIR, "tracking.txt"),)),
    ("Азартные игры", (os.path.join(DOMAIN_CATEGORY_DIR, "gambling.txt"),)),
    ("Пиратский контент", (os.path.join(DOMAIN_CATEGORY_DIR, "piracy.txt"),)),
    ("Торренты", (os.path.join(DOMAIN_CATEGORY_DIR, "torrent.txt"),)),
    ("Крипто", (os.path.join(DOMAIN_CATEGORY_DIR, "crypto.txt"),)),
    ("Наркотики", (os.path.join(DOMAIN_CATEGORY_DIR, "drugs.txt"),)),
    ("Видео", (os.path.join(DOMAIN_CATEGORY_DIR, "youtube.txt"), os.path.join(DOMAIN_CATEGORY_DIR, "smart_tv.txt"))),
    ("Соцсети", (
        os.path.join(DOMAIN_CATEGORY_DIR, "facebook.txt"),
        os.path.join(DOMAIN_CATEGORY_DIR, "twitter.txt"),
        os.path.join(DOMAIN_CATEGORY_DIR, "tiktok.txt"),
    )),
    ("Мессенджеры", (os.path.join(DOMAIN_CATEGORY_DIR, "whatsapp.txt"),)),
)
DOMAIN_CATEGORY_SETS = None
DOMAIN_CATEGORY_LOCK = threading.Lock()
DOMAIN_CATEGORY_PRELOAD_STARTED = False
GEOIP_LOOKUP_URL = os.getenv("KINGLINE_GEOIP_LOOKUP_URL", "https://ipwho.is/{ip}").strip()
GEOIP_LOOKUP_TIMEOUT_SECONDS = float(os.getenv("KINGLINE_GEOIP_LOOKUP_TIMEOUT_SECONDS", "4"))

sys.path.insert(0, KINGLINE_DIR)
from storage import connect as connect_sqlite  # noqa: E402
os.environ["DB_PATH"] = DB_PATH
os.environ.setdefault("PROMOS_DB_PATH", os.path.join(KINGLINE_DIR, "promos.db"))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(KINGLINE_DIR, ".env"))
except Exception:
    pass

from config import ADMIN_ID, BOT_TOKEN, FULL_TARIFF_PRICE, db as bot_db, logger  # noqa: E402
from dns.user_locator import DNSUserLocator  # noqa: E402
from handlers.billing_db import BillingDB  # noqa: E402
from handlers.payments.yookassa import YooKassaGateway  # noqa: E402
from utils import build_backup_sub_link, build_sub_link, extract_clients_from_inbound, now_iso  # noqa: E402
from xui import XUIClient  # noqa: E402
from diagnostics import (  # noqa: E402
    RequestDiagnosticsMixin, configure as configure_diagnostics, timed_async, timed_sync,
)

try:
    from backend.domain_classifier import (  # noqa: E402
        classify_domain_by_keywords,
        classify_domain_with_local_ai,
        warm_local_domain_ai,
    )
except ImportError:  # pragma: no cover - direct script execution
    from domain_classifier import (  # noqa: E402
        classify_domain_by_keywords,
        classify_domain_with_local_ai,
        warm_local_domain_ai,
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value):
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z") and "T" in raw:
        try:
            return datetime.fromisoformat(f"{raw[:-1]}+00:00").astimezone(timezone.utc)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def connect():
    conn = connect_sqlite(DB_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def init_db():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT,
                password_hash TEXT NOT NULL,
                linked_tg_id INTEGER UNIQUE,
                email_verified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_login_at TEXT
            )
            """
        )

        account_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(web_accounts)").fetchall()
        }
        if "web_tg_id" not in account_cols:
            conn.execute("ALTER TABLE web_accounts ADD COLUMN web_tg_id INTEGER")
        if "telegram_bonus_claimed" not in account_cols:
            conn.execute(
                "ALTER TABLE web_accounts ADD COLUMN telegram_bonus_claimed INTEGER DEFAULT 0"
            )
        if "telegram_bonus_claimed_at" not in account_cols:
            conn.execute("ALTER TABLE web_accounts ADD COLUMN telegram_bonus_claimed_at TEXT")
        if "email_reminders_enabled" not in account_cols:
            conn.execute(
                "ALTER TABLE web_accounts ADD COLUMN email_reminders_enabled INTEGER DEFAULT 1"
            )
        if "email_announcements_enabled" not in account_cols:
            conn.execute(
                "ALTER TABLE web_accounts ADD COLUMN email_announcements_enabled INTEGER DEFAULT 1"
            )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_sessions (
                token_hash TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT,
                user_agent TEXT,
                ip TEXT,
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        session_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(web_sessions)").fetchall()
        }
        if "admin_verified_at" not in session_cols:
            conn.execute("ALTER TABLE web_sessions ADD COLUMN admin_verified_at TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_link_codes (
                code TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                bonus_days INTEGER NOT NULL DEFAULT 3,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                used_tg_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_login_attempts (
                email TEXT PRIMARY KEY,
                failed_count INTEGER DEFAULT 0,
                first_failed_at TEXT,
                last_failed_at TEXT,
                locked_until TEXT,
                last_ip TEXT,
                user_agent TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_admin_2fa_challenges (
                token_hash TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                code_hash TEXT NOT NULL,
                sent_to_tg_id INTEGER NOT NULL,
                admin_tg_message_id INTEGER,
                expires_at TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ip TEXT,
                user_agent TEXT,
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        admin_2fa_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(web_admin_2fa_challenges)").fetchall()
        }
        if "admin_tg_message_id" not in admin_2fa_cols:
            conn.execute(
                "ALTER TABLE web_admin_2fa_challenges ADD COLUMN admin_tg_message_id INTEGER"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_admin_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_account_id INTEGER,
                admin_email TEXT,
                action TEXT NOT NULL,
                target TEXT,
                summary_json TEXT DEFAULT '{}',
                ip TEXT,
                user_agent TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_magic_links (
                token_hash TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                name TEXT,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ip TEXT,
                user_agent TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_email_verifications (
                token_hash TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ip TEXT,
                user_agent TEXT,
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_password_resets (
                token_hash TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ip TEXT,
                user_agent TEXT,
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_pending_registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                attempts INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ip TEXT,
                user_agent TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_email_link_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                attempts INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ip TEXT,
                user_agent TEXT,
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_account_jobs (
                job_id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_email_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                tg_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                reminder_kind TEXT NOT NULL,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, tg_id, expires_at, reminder_kind),
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_telegram_link_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                tg_id INTEGER NOT NULL,
                trigger_kind TEXT NOT NULL,
                trigger_at TEXT NOT NULL,
                due_at TEXT NOT NULL,
                sent_at TEXT,
                canceled_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id),
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_xui_user_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sampled_at TEXT NOT NULL,
                username TEXT NOT NULL,
                tg_id INTEGER,
                name TEXT,
                up_bytes INTEGER DEFAULT 0,
                down_bytes INTEGER DEFAULT 0,
                total_bytes INTEGER DEFAULT 0,
                online_clients INTEGER DEFAULT 0,
                panels_json TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_xui_client_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                client_email TEXT NOT NULL,
                tg_id INTEGER,
                name TEXT,
                panel_tag TEXT,
                inbound_id INTEGER,
                updated_at TEXT NOT NULL,
                UNIQUE(username, client_email, panel_tag, inbound_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_xui_ip_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                tg_id INTEGER,
                ip TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER DEFAULT 1,
                panels_json TEXT DEFAULT '[]',
                UNIQUE(username, ip)
            )
            """
        )
        ip_history_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(web_xui_ip_history)").fetchall()
        }
        ip_history_additions = {
            "country": "TEXT",
            "city": "TEXT",
            "isp": "TEXT",
            "asn": "TEXT",
            "org": "TEXT",
            "geo_updated_at": "TEXT",
        }
        for column, definition in ip_history_additions.items():
            if column not in ip_history_cols:
                conn.execute(f"ALTER TABLE web_xui_ip_history ADD COLUMN {column} {definition}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_xui_device_profiles (
                username TEXT PRIMARY KEY,
                device_label TEXT,
                device_family TEXT,
                confidence INTEGER DEFAULT 0,
                signals_json TEXT DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_xui_user_last_seen (
                username TEXT PRIMARY KEY,
                tg_id INTEGER,
                name TEXT,
                last_seen_at TEXT NOT NULL,
                online_clients INTEGER DEFAULT 0,
                panels_json TEXT DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        device_profile_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(web_xui_device_profiles)").fetchall()
        }
        if "devices_json" not in device_profile_cols:
            conn.execute("ALTER TABLE web_xui_device_profiles ADD COLUMN devices_json TEXT DEFAULT '[]'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_xui_access_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seen_at TEXT NOT NULL,
                username TEXT NOT NULL,
                ip TEXT,
                server TEXT,
                domain TEXT NOT NULL,
                port INTEGER DEFAULT 0,
                count INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_xui_access_import_state (
                path TEXT PRIMARY KEY,
                byte_offset INTEGER DEFAULT 0,
                file_size INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_xui_access_daily_counts (
                username TEXT NOT NULL,
                day TEXT NOT NULL,
                request_count INTEGER DEFAULT 0,
                PRIMARY KEY(username, day)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_xui_user_insights_cache (
                username TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                message_text TEXT NOT NULL,
                attachments_json TEXT DEFAULT '[]',
                reaction TEXT,
                sender_name TEXT,
                admin_tg_message_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                tg_id INTEGER UNIQUE,
                is_owner INTEGER DEFAULT 0,
                can_balance INTEGER DEFAULT 1,
                can_message INTEGER DEFAULT 1,
                can_block INTEGER DEFAULT 1,
                can_promos INTEGER DEFAULT 1,
                can_dns INTEGER DEFAULT 1,
                can_support INTEGER DEFAULT 1,
                can_payments INTEGER DEFAULT 1,
                can_broadcast INTEGER DEFAULT 1,
                can_delete_users INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                created_by_account_id INTEGER
            )
            """
        )
        support_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(web_support_messages)").fetchall()
        }
        support_additions = {
            "attachments_json": "TEXT DEFAULT '[]'",
            "reaction": "TEXT",
            "sender_name": "TEXT",
        }
        for column, definition in support_additions.items():
            if column not in support_cols:
                conn.execute(f"ALTER TABLE web_support_messages ADD COLUMN {column} {definition}")
        admin_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(web_admins)").fetchall()
        }
        admin_additions = {
            "is_owner": "INTEGER DEFAULT 0",
            "can_analytics": "INTEGER DEFAULT 1",
            "can_balance": "INTEGER DEFAULT 1",
            "can_message": "INTEGER DEFAULT 1",
            "can_block": "INTEGER DEFAULT 1",
            "can_promos": "INTEGER DEFAULT 1",
            "can_dns": "INTEGER DEFAULT 1",
            "can_support": "INTEGER DEFAULT 1",
            "can_payments": "INTEGER DEFAULT 1",
            "can_broadcast": "INTEGER DEFAULT 1",
            "can_delete_users": "INTEGER DEFAULT 0",
            "can_nodes": "INTEGER DEFAULT 0",
            "can_node_credentials": "INTEGER DEFAULT 0",
            "can_technical": "INTEGER DEFAULT 0",
            "can_vpn_config": "INTEGER DEFAULT 0",
            "can_documentation": "INTEGER DEFAULT 0",
            "can_user_transport": "INTEGER DEFAULT 0",
        }
        for column, definition in admin_additions.items():
            if column not in admin_cols:
                conn.execute(f"ALTER TABLE web_admins ADD COLUMN {column} {definition}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_support_typing (
                account_id INTEGER NOT NULL,
                actor TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(account_id, actor),
                FOREIGN KEY(account_id) REFERENCES web_accounts(id)
                    ON DELETE CASCADE
            )
            """
        )
        for admin_email in [
            value.strip().lower()
            for value in ADMIN_EMAILS_ENV.split(",")
            if value.strip()
        ]:
            conn.execute(
                """
                INSERT OR IGNORE INTO web_admins (email, created_at)
                VALUES (?, ?)
                """,
                (admin_email, iso_now()),
            )
        if PRIMARY_ADMIN_EMAIL:
            conn.execute(
                """
                INSERT OR IGNORE INTO web_admins (
                    email,
                    is_owner,
                    can_analytics,
                    can_balance,
                    can_message,
                    can_block,
                    can_promos,
                    can_dns,
                    can_support,
                    can_payments,
                    can_broadcast,
                    can_delete_users,
                    created_at
                )
                VALUES (?, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ?)
                """,
                (PRIMARY_ADMIN_EMAIL, iso_now()),
            )
            conn.execute(
                """
                UPDATE web_admins
                SET
                    is_owner = 1,
                    can_analytics = 1,
                    can_balance = 1,
                    can_message = 1,
                    can_block = 1,
                    can_promos = 1,
                    can_dns = 1,
                    can_support = 1,
                    can_payments = 1,
                    can_broadcast = 1,
                    can_delete_users = 1
                WHERE LOWER(email) = LOWER(?)
                """,
                (PRIMARY_ADMIN_EMAIL,),
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_sessions_account_id
            ON web_sessions(account_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_accounts_linked_tg_id
            ON web_accounts(linked_tg_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_accounts_web_tg_id
            ON web_accounts(web_tg_id)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_web_accounts_web_tg_id
            ON web_accounts(web_tg_id)
            WHERE web_tg_id IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_magic_links_email
            ON web_magic_links(email)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_email_verifications_account_id
            ON web_email_verifications(account_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_password_resets_account_id
            ON web_password_resets(account_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_pending_registrations_email
            ON web_pending_registrations(email)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_email_link_requests_account
            ON web_email_link_requests(account_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_email_link_requests_email
            ON web_email_link_requests(email)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_account_jobs_account
            ON web_account_jobs(account_id, status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_email_reminders_account
            ON web_email_reminders(account_id, tg_id, expires_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_telegram_link_reminders_due
            ON web_telegram_link_reminders(due_at, sent_at, canceled_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_user_samples_user_time
            ON web_xui_user_samples(username, sampled_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_user_samples_time
            ON web_xui_user_samples(sampled_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_user_last_seen_time
            ON web_xui_user_last_seen(last_seen_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_ip_history_user
            ON web_xui_ip_history(username, last_seen_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_ip_history_ip
            ON web_xui_ip_history(ip, geo_updated_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_access_events_user_time
            ON web_xui_access_events(username, seen_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_access_events_user_domain_time
            ON web_xui_access_events(username, domain, seen_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_access_events_time
            ON web_xui_access_events(seen_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_access_events_time_user
            ON web_xui_access_events(seen_at, username)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_access_daily_counts_day_user
            ON web_xui_access_daily_counts(day, username)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_access_events_username_id
            ON web_xui_access_events(username, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_xui_user_insights_cache_updated
            ON web_xui_user_insights_cache(updated_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_support_messages_account
            ON web_support_messages(account_id, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_admins_email
            ON web_admins(email)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_admins_tg_id
            ON web_admins(tg_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_support_typing_account
            ON web_support_typing(account_id, actor)
            """
        )
        conn.execute(
            """
            UPDATE web_accounts
            SET web_tg_id = -id
            WHERE web_tg_id IS NULL
            """
        )
        conn.commit()


def normalize_email(value):
    return (value or "").strip().lower()


def telegram_internal_email(tg_id):
    return f"tg{int(tg_id)}@{TELEGRAM_INTERNAL_EMAIL_DOMAIN}"


def is_telegram_internal_email(email):
    value = normalize_email(email)
    return bool(
        value
        and TELEGRAM_INTERNAL_EMAIL_DOMAIN
        and value.endswith(f"@{TELEGRAM_INTERNAL_EMAIL_DOMAIN}")
    )


def public_email(email):
    return "" if is_telegram_internal_email(email) else (email or "")


def normalize_name(value, email):
    raw = str(value or "").strip()
    if len(raw) >= 2:
        return raw[:80]
    local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    return (local[:80] or BRAND_NAME)


def username_from_email(email, fallback="user"):
    local = normalize_email(email).split("@", 1)[0]
    cleaned = re.sub(r"[^a-z0-9_-]+", "", local.lower())
    return cleaned[:48] or fallback


def public_site_url():
    return os.getenv("KINGLINE_SITE_URL", SITE_URL).rstrip("/")


def safe_username(account):
    account_id = int(account["id"])
    return username_from_email(account["email"], f"user{account_id}")


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "$".join(
        [
            "pbkdf2_sha256",
            str(PASSWORD_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password, stored_hash):
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_raw)
        expected = base64.b64decode(digest_raw)
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def account_select():
    return """
        id,
        email,
        name,
        linked_tg_id,
        web_tg_id,
        email_verified,
        telegram_bonus_claimed,
        COALESCE(email_reminders_enabled, 1) AS email_reminders_enabled,
        COALESCE(email_announcements_enabled, 1) AS email_announcements_enabled
    """


def public_account(row):
    admin = admin_access_for_account(row)
    visible_email = public_email(row["email"])
    telegram_auth = is_telegram_internal_email(row["email"])
    return {
        "id": row["id"],
        "email": visible_email,
        "name": row["name"] or "",
        "linkedTgId": row["linked_tg_id"],
        "webTgId": row["web_tg_id"],
        "emailVerified": bool(row["email_verified"]) and bool(visible_email),
        "authProvider": "telegram" if telegram_auth else "email",
        "hasRealEmail": bool(visible_email) and bool(row["email_verified"]),
        "needsEmailLink": bool(telegram_auth),
        "telegramBonusClaimed": bool(row["telegram_bonus_claimed"] or 0),
        "emailRemindersEnabled": bool(row["email_reminders_enabled"] or 0),
        "emailAnnouncementsEnabled": bool(row["email_announcements_enabled"] or 0),
        "isAdmin": bool(admin),
        "isOwnerAdmin": bool(admin and admin.get("isOwner")),
        "adminPermissions": admin.get("permissions") if admin else {},
    }


def row_to_dict(row):
    return dict(row) if row else None


def get_user_row(tg_id):
    if not tg_id:
        return None
    return bot_db.get_user(int(tg_id))


def has_active_premium(user):
    if not user or (user.get("tariff") or "").lower() != "premium":
        return False
    expires = parse_iso(user.get("key_expires_at") or user.get("subscription_expires_at"))
    return bool(expires and expires > utc_now())


def normalize_location(value):
    raw = str(value or "auto").strip().lower()
    if raw.startswith("lt") or raw in {"литва", "lithuania", "🇱🇹"}:
        return "lt"
    if raw.startswith("nl") or raw == "de" or raw in {
        "германия",
        "germany",
        "deutschland",
        "нидерланды",
        "netherlands",
        "holland",
        "🇩🇪",
        "🇳🇱",
    }:
        return "nl"
    return raw if raw == "auto" else "auto"


def merge_location_pref(primary, fallback):
    primary_loc = normalize_location(primary)
    if primary_loc != "auto":
        return primary_loc

    fallback_loc = normalize_location(fallback)
    if fallback_loc != "auto":
        return fallback_loc

    return "auto"


def public_subscription(user):
    if not user:
        return {
            "tariff": "free",
            "balanceRub": 0,
            "active": False,
            "freeUsed": False,
            "canUseTrial": True,
            "premiumPrice": FULL_TARIFF_PRICE,
            "location": "auto",
        }

    expires_at = user.get("subscription_expires_at") or user.get("key_expires_at")
    expires_dt = parse_iso(expires_at)
    active = bool(expires_dt and expires_dt > utc_now() and not user.get("is_blocked"))
    sub_id = user.get("sub_id")
    tariff = (user.get("tariff") or "free").lower()
    free_used = bool(int(user.get("free_used") or 0))

    payload = {
        "tariff": tariff,
        "subId": sub_id,
        "subName": user.get("sub_name"),
        "balanceRub": int(user.get("balance_rub") or 0),
        "expiresAt": expires_at,
        "active": active,
        "freeUsed": free_used,
        "canUseTrial": not free_used and not has_active_premium(user),
        "premiumPrice": FULL_TARIFF_PRICE,
        "location": normalize_location(user.get("location_pref")),
        "blocked": bool(user.get("is_blocked") or 0),
        "subscriptionUrl": build_sub_link(sub_id) if sub_id else None,
    }
    return payload


def ensure_web_user(account):
    linked_tg_id = account["linked_tg_id"]
    raw_web_tg_id = account["web_tg_id"]
    web_tg_id = int(linked_tg_id or raw_web_tg_id or -int(account["id"]))
    existing = get_user_row(web_tg_id) or {}
    old_web_user = None
    if linked_tg_id and raw_web_tg_id and int(raw_web_tg_id) != web_tg_id:
        old_web_user = get_user_row(int(raw_web_tg_id)) or {}
    merged_location = merge_location_pref(
        existing.get("location_pref"),
        (old_web_user or {}).get("location_pref"),
    )
    username = (existing.get("username") or safe_username(account)).replace(" ", "")
    name = account["name"] or public_email(account["email"]) or f"Telegram {web_tg_id}"
    BillingDB.ensure_user_row(web_tg_id, name, username)
    current = get_user_row(web_tg_id) or {}
    if normalize_location(current.get("location_pref")) != merged_location:
        bot_db.set_location_pref(web_tg_id, merged_location)
    return web_tg_id, username


def get_account_by_session(token):
    if not token:
        return None, None

    token_digest = hash_token(token)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                s.token_hash,
                s.expires_at,
                a.id,
                a.email,
                a.name,
                a.linked_tg_id,
                a.web_tg_id,
                a.email_verified,
                a.telegram_bonus_claimed,
                COALESCE(a.email_reminders_enabled, 1) AS email_reminders_enabled,
                COALESCE(a.email_announcements_enabled, 1) AS email_announcements_enabled
            FROM web_sessions s
            JOIN web_accounts a ON a.id = s.account_id
            WHERE s.token_hash = ?
            LIMIT 1
            """,
            (token_digest,),
        ).fetchone()

        if not row:
            return None, None

        expires_at = parse_iso(row["expires_at"])
        if not expires_at or expires_at <= utc_now():
            conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (token_digest,))
            conn.commit()
            return None, None

        conn.execute(
            "UPDATE web_sessions SET last_seen_at = ? WHERE token_hash = ?",
            (iso_now(), token_digest),
        )
        conn.commit()
        return row, token_digest


def is_rate_limited(client_ip, path):
    now = utc_now().timestamp()
    key = (client_ip, path)
    attempts = [ts for ts in RATE_LIMITS.get(key, []) if now - ts < RATE_LIMIT_WINDOW_SECONDS]
    if len(attempts) >= RATE_LIMIT_MAX_ATTEMPTS:
        RATE_LIMITS[key] = attempts
        return True
    attempts.append(now)
    RATE_LIMITS[key] = attempts
    return False


def login_lock_for_email(email):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT locked_until
            FROM web_login_attempts
            WHERE email = ?
            LIMIT 1
            """,
            (normalize_email(email),),
        ).fetchone()
    locked_until = parse_iso(row["locked_until"]) if row else None
    return locked_until if locked_until and locked_until > utc_now() else None


def record_login_failure(email, client_ip, user_agent):
    email = normalize_email(email)
    now_dt = utc_now()
    now_text = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            """
            SELECT failed_count, first_failed_at
            FROM web_login_attempts
            WHERE email = ?
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        first_failed = parse_iso(row["first_failed_at"]) if row else None
        failed_count = int(row["failed_count"] or 0) if row else 0
        if not first_failed or (now_dt - first_failed).total_seconds() > LOGIN_EMAIL_WINDOW_SECONDS:
            first_failed = now_dt
            failed_count = 0
        failed_count += 1
        locked_until = ""
        if failed_count >= LOGIN_EMAIL_MAX_ATTEMPTS:
            locked_until = (
                now_dt + timedelta(minutes=LOGIN_EMAIL_LOCK_MINUTES)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur.execute(
            """
            INSERT INTO web_login_attempts (
                email,
                failed_count,
                first_failed_at,
                last_failed_at,
                locked_until,
                last_ip,
                user_agent
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                failed_count = excluded.failed_count,
                first_failed_at = excluded.first_failed_at,
                last_failed_at = excluded.last_failed_at,
                locked_until = excluded.locked_until,
                last_ip = excluded.last_ip,
                user_agent = excluded.user_agent
            """,
            (
                email,
                failed_count,
                first_failed.strftime("%Y-%m-%dT%H:%M:%SZ"),
                now_text,
                locked_until or None,
                client_ip,
                (user_agent or "")[:300],
            ),
        )
        conn.commit()
    return parse_iso(locked_until) if locked_until else None


def clear_login_failures(email):
    with connect() as conn:
        conn.execute("DELETE FROM web_login_attempts WHERE email = ?", (normalize_email(email),))
        conn.commit()


def create_magic_link(email, name, client_ip, user_agent):
    token = secrets.token_urlsafe(36)
    token_digest = hash_token(token)
    expires_at = (utc_now() + timedelta(minutes=MAGIC_LINK_MINUTES)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    clean_name = normalize_name(name, email)
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM web_magic_links
            WHERE email = ? AND consumed_at IS NULL
            """,
            (email,),
        )
        conn.execute(
            """
            DELETE FROM web_magic_links
            WHERE expires_at <= ?
            """,
            (iso_now(),),
        )
        conn.execute(
            """
            INSERT INTO web_magic_links (
                token_hash,
                email,
                name,
                expires_at,
                ip,
                user_agent
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token_digest,
                email,
                clean_name,
                expires_at,
                client_ip,
                (user_agent or "")[:300],
            ),
        )
        conn.commit()
    return f"{public_site_url()}/api/auth/magic?{urlencode({'token': token})}", expires_at


def consume_magic_link(token):
    token_digest = hash_token(token or "")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            """
            SELECT token_hash, email, name, expires_at, consumed_at
            FROM web_magic_links
            WHERE token_hash = ?
            LIMIT 1
            """,
            (token_digest,),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("magic_link_invalid")
        if row["consumed_at"]:
            conn.rollback()
            raise ValueError("magic_link_used")
        expires = parse_iso(row["expires_at"])
        if not expires or expires <= utc_now():
            cur.execute(
                "UPDATE web_magic_links SET consumed_at = ? WHERE token_hash = ?",
                (iso_now(), token_digest),
            )
            conn.commit()
            raise ValueError("magic_link_expired")

        account = cur.execute(
            f"SELECT {account_select()} FROM web_accounts WHERE email = ? LIMIT 1",
            (row["email"],),
        ).fetchone()

        if not account:
            cur.execute(
                """
                INSERT INTO web_accounts (
                    email,
                    name,
                    password_hash,
                    email_verified,
                    updated_at,
                    last_login_at
                )
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (row["email"], normalize_name(row["name"], row["email"]), "", iso_now(), iso_now()),
            )
            account_id = int(cur.lastrowid)
            web_tg_id = -account_id
            cur.execute(
                "UPDATE web_accounts SET web_tg_id = ? WHERE id = ?",
                (web_tg_id, account_id),
            )
        else:
            account_id = int(account["id"])
            web_tg_id = account["web_tg_id"] or -account_id
            cur.execute(
                """
                UPDATE web_accounts
                SET email_verified = 1,
                    last_login_at = ?,
                    updated_at = ?,
                    web_tg_id = ?
                WHERE id = ?
                """,
                (iso_now(), iso_now(), web_tg_id, account_id),
            )

        cur.execute(
            "UPDATE web_magic_links SET consumed_at = ? WHERE token_hash = ?",
            (iso_now(), token_digest),
        )
        conn.commit()

        return conn.execute(
            f"SELECT {account_select()} FROM web_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()


def smtp_config():
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_from = (
        os.getenv("SMTP_FROM", "").strip()
        or os.getenv("SMTP_USER", "").strip()
        or os.getenv("MAIL_FROM", "").strip()
    )
    if not smtp_host or not smtp_from:
        raise RuntimeError("mail_not_configured")

    smtp_port = int(os.getenv("SMTP_PORT", "465" if os.getenv("SMTP_SSL") == "1" else "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    use_ssl = os.getenv("SMTP_SSL", "0") == "1"
    use_tls = os.getenv("SMTP_TLS", "1") != "0" and not use_ssl
    return smtp_host, smtp_port, smtp_from, smtp_user, smtp_password, use_ssl, use_tls


def brand_logo_url():
    return f"{public_site_url()}/brand/custom/logo.png"


def format_msk(value):
    dt = value if isinstance(value, datetime) else parse_iso(value)
    if not dt:
        return str(value or "")
    return (dt.astimezone(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M МСК")


def email_shell(title, lead, content_html, cta_label=None, cta_url=None, code=None, footer_text=None):
    safe_title = escape(str(title), quote=True)
    safe_lead = escape(str(lead), quote=True)
    logo = escape(brand_logo_url(), quote=True)
    cta_html = ""
    if cta_label and cta_url:
        cta_html = (
            f'<a href="{escape(str(cta_url), quote=True)}" '
            'style="display:block;margin:22px 0 0;padding:15px 18px;border-radius:16px;'
            'background:#f04455;color:#ffffff;'
            'font-size:16px;font-weight:800;text-align:center;text-decoration:none;">'
            f"{escape(str(cta_label))}</a>"
        )
    code_html = ""
    if code:
        code_html = (
            '<div style="margin:18px 0 0;padding:18px;border:1px solid #5f2630;'
            'border-radius:18px;background:#321d22;text-align:center;">'
            '<div style="color:#d5b8be;font-size:13px;font-weight:700;">Код подтверждения</div>'
            f'<div style="margin-top:8px;color:#ffffff;font-size:32px;letter-spacing:6px;'
            f'font-weight:900;">{escape(str(code))}</div></div>'
        )
    if footer_text is None:
        footer_text = "Если вы не выполняли это действие, просто проигнорируйте письмо."
    footer_html = ""
    if footer_text:
        footer_html = (
            '<tr><td style="padding:16px 6px 0;color:#9f9196;font-size:12px;'
            'line-height:1.5;text-align:center;">'
            f"{escape(str(footer_text))}</td></tr>"
        )
    return f"""<!doctype html>
<html>
  <body style="margin:0;background:#171113;color:#f8f2f4;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
    <div style="display:none;overflow:hidden;line-height:1px;opacity:0;max-height:0;max-width:0;">{safe_lead}</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" bgcolor="#171113" style="background:#171113;padding:24px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;">
            <tr>
              <td style="padding:0 0 14px;text-align:left;">
                <table role="presentation" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="padding-right:12px;">
                      <img src="{logo}" width="52" height="52" alt="{BRAND_NAME}" style="display:block;width:52px;height:52px;border-radius:16px;border:1px solid #5c4d52;">
                    </td>
                    <td>
                      <div style="color:#ffffff;font-size:18px;font-weight:900;line-height:1.15;">{BRAND_NAME}</div>
                      <div style="margin-top:3px;color:#bdaeb3;font-size:13px;">личный кабинет</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td bgcolor="#241f21" style="border:1px solid #4a3d42;border-radius:24px;background:#241f21;padding:24px;">
                <div style="display:inline-block;padding:7px 11px;border:1px solid #5f2630;border-radius:999px;background:#351f24;color:#ff8795;font-size:12px;font-weight:800;">Kingline Proxy</div>
                <h1 style="margin:16px 0 8px;color:#ffffff;font-size:28px;line-height:1.12;font-weight:900;">{safe_title}</h1>
                <p style="margin:0;color:#d2c3c8;font-size:16px;line-height:1.6;">{safe_lead}</p>
                {code_html}
                <div style="margin-top:18px;color:#eadfe2;font-size:15px;line-height:1.65;">{content_html}</div>
                {cta_html}
              </td>
            </tr>
            {footer_html}
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def send_mail(email, subject, text, html=None, headers=None):
    # Staging captures mail locally; production recipients are never contacted.
    from staging_safety import capture_mail
    return capture_mail(email, subject, text, html)


def _unused_production_send_mail(email, subject, text, html=None, headers=None):
    smtp_host, smtp_port, smtp_from, smtp_user, smtp_password, use_ssl, use_tls = smtp_config()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = email
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain="kingline.online")
    message["X-Auto-Response-Suppress"] = "All"
    for name, value in (headers or {}).items():
        if name and value:
            message[str(name)] = str(value)
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(smtp_host, smtp_port, timeout=12) as smtp:
        if use_tls:
            smtp.starttls()
        if smtp_user and smtp_password:
            smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)


def send_telegram_message(chat_id, text):
    if int(chat_id or 0) != int(ADMIN_ID):
        return None
    if not BOT_TOKEN or not chat_id:
        return None

    payload = json.dumps(
        {
            "chat_id": int(chat_id),
            "text": str(text)[:3900],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError("telegram_send_failed")
    return (data.get("result") or {}).get("message_id")


def delete_telegram_message(chat_id, message_id):
    if int(chat_id or 0) != int(ADMIN_ID):
        return False
    if not BOT_TOKEN or not chat_id or not message_id:
        return False
    payload = json.dumps(
        {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
        return bool(data.get("ok"))
    except Exception:
        logger.exception("Telegram delete message failed chat_id=%s message_id=%s", chat_id, message_id)
        return False


def send_telegram_admin_message(text):
    return send_telegram_message(ADMIN_ID, text)


def _json_list(value):
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


SUPPORT_UPLOAD_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


def _upload_magic_matches(ext, blob):
    if ext == ".png":
        return blob.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {".jpg", ".jpeg"}:
        return blob.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"
    if ext == ".pdf":
        return blob.startswith(b"%PDF-")
    return False


def _safe_upload_name(name, ext):
    raw = os.path.basename(str(name or "file")).strip()
    stem = os.path.splitext(raw)[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)[:70].strip(".-") or "file"
    return f"{stem}{ext}"


def _validated_upload(name, content_type, blob):
    declared = str(content_type or "").split(";", 1)[0].strip().lower()
    original_ext = os.path.splitext(os.path.basename(str(name or "")))[1].lower()
    candidates = []
    for ext, mime in SUPPORT_UPLOAD_TYPES.items():
        if declared == mime:
            candidates.append(ext)
    if original_ext in SUPPORT_UPLOAD_TYPES:
        candidates.append(original_ext)
    for ext in dict.fromkeys(candidates):
        if _upload_magic_matches(ext, blob):
            normalized_ext = ".jpg" if ext == ".jpeg" else ext
            return _safe_upload_name(name, normalized_ext), SUPPORT_UPLOAD_TYPES[ext]
    return None, None


def store_support_attachments(attachments):
    stored = []
    os.makedirs(SUPPORT_UPLOAD_DIR, exist_ok=True)
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        encoded = str(item.get("data") or "")
        if not encoded:
            continue
        if "," in encoded and encoded.split(",", 1)[0].startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            blob = base64.b64decode(encoded, validate=True)
        except Exception:
            continue
        if not blob or len(blob) > 8 * 1024 * 1024:
            continue
        original_name, content_type = _validated_upload(
            item.get("name"),
            item.get("type"),
            blob,
        )
        if not original_name or not content_type:
            continue
        token = secrets.token_hex(12)
        filename = f"{token}-{original_name}"
        path = os.path.join(SUPPORT_UPLOAD_DIR, filename)
        with open(path, "wb") as fh:
            fh.write(blob)
        stored.append(
            {
                "name": original_name,
                "type": content_type,
                "size": len(blob),
                "url": f"{SUPPORT_UPLOAD_URL_PREFIX}/{filename}",
            }
        )
    return stored[:4]


def public_support_message(row):
    return {
        "id": row["id"],
        "direction": row["direction"],
        "text": row["message_text"],
        "attachments": _json_list(row["attachments_json"] if "attachments_json" in row.keys() else "[]"),
        "reaction": row["reaction"] if "reaction" in row.keys() else "",
        "senderName": row["sender_name"] if "sender_name" in row.keys() else "",
        "createdAt": row["created_at"],
    }


def list_support_messages(account_id, after_id=0, limit=80):
    after_id = max(0, int(after_id or 0))
    limit = min(120, max(1, int(limit or 80)))
    with connect() as conn:
        if after_id:
            rows = conn.execute(
                """
                SELECT
                    id,
                    direction,
                    message_text,
                    attachments_json,
                    reaction,
                    sender_name,
                    created_at
                FROM web_support_messages
                WHERE account_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (int(account_id), after_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    id,
                    direction,
                    message_text,
                    attachments_json,
                    reaction,
                    sender_name,
                    created_at
                FROM web_support_messages
                WHERE account_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(account_id), limit),
            ).fetchall()
            rows = list(reversed(rows))
    return [public_support_message(row) for row in rows]


def support_typing_state(account_id, viewer):
    actor = "admin" if viewer == "user" else "user"
    with connect() as conn:
        row = conn.execute(
            """
            SELECT updated_at
            FROM web_support_typing
            WHERE account_id = ? AND actor = ?
            LIMIT 1
            """,
            (int(account_id), actor),
        ).fetchone()
    if not row:
        return {"actor": actor, "isTyping": False}
    updated = parse_iso(row["updated_at"])
    return {
        "actor": actor,
        "isTyping": bool(updated and (utc_now() - updated).total_seconds() < 8),
    }


def set_support_typing(account_id, actor):
    actor = "admin" if actor == "admin" else "user"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO web_support_typing (account_id, actor, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id, actor) DO UPDATE SET
                updated_at = excluded.updated_at
            """,
            (int(account_id), actor, iso_now()),
        )
        conn.commit()


def create_support_message(
    account_id,
    direction,
    message_text,
    admin_tg_message_id=None,
    attachments=None,
    sender_name=None,
):
    text = str(message_text or "").strip()
    stored_attachments = store_support_attachments(attachments or [])
    if not text and not stored_attachments:
        raise ValueError("message_required")
    text = text[:4000]
    direction = direction if direction in {"user", "admin", "system"} else "user"
    sender_name = str(sender_name or "").strip()[:80]
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO web_support_messages (
                account_id,
                direction,
                message_text,
                attachments_json,
                sender_name,
                admin_tg_message_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(account_id),
                direction,
                text,
                json.dumps(stored_attachments, ensure_ascii=False),
                sender_name,
                admin_tg_message_id,
                iso_now(),
            ),
        )
        message_id = int(cur.lastrowid)
        conn.commit()
        row = conn.execute(
            """
            SELECT
                id,
                direction,
                message_text,
                attachments_json,
                reaction,
                sender_name,
                created_at
            FROM web_support_messages
            WHERE id = ?
            """,
            (message_id,),
        ).fetchone()
    return public_support_message(row)


def set_support_reaction(account_id, message_id, reaction):
    reaction = str(reaction or "").strip()[:12]
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM web_support_messages
            WHERE id = ? AND account_id = ?
            LIMIT 1
            """,
            (int(message_id), int(account_id)),
        ).fetchone()
        if not row:
            raise ValueError("message_not_found")
        conn.execute(
            """
            UPDATE web_support_messages
            SET reaction = ?
            WHERE id = ? AND account_id = ?
            """,
            (reaction, int(message_id), int(account_id)),
        )
        conn.commit()


def notify_admin_about_support(account, message_text, attachments=None):
    name = account["name"] or "Без имени"
    attachment_lines = ""
    for attachment in attachments or []:
        url = public_site_url() + attachment.get("url", "")
        attachment_lines += f"\n📎 {escape(attachment.get('name') or 'file')}: {escape(url)}"
    text = (
        "<b>🌐 Web-поддержка</b>\n"
        f"account_id={int(account['id'])}\n"
        f"email=<code>{escape(account['email'])}</code>\n"
        f"Имя: <b>{escape(name)}</b>\n\n"
        f"{escape(str(message_text))}{attachment_lines}\n\n"
        "Ответить можно реплаем на это сообщение или командой:\n"
        f"<code>/webmsg {int(account['id'])} текст ответа</code>\n"
        f"Также можно по email: <code>/webmsg {escape(account['email'])} текст ответа</code>"
    )
    return send_telegram_admin_message(text)


def notify_admin_about_support_async(account, message):
    account_payload = {
        "id": int(account["id"]),
        "email": account["email"],
        "name": account["name"] or "",
    }
    message_id = int(message["id"])
    message_text = message.get("text") or ""
    attachments = list(message.get("attachments") or [])

    def worker():
        try:
            tg_message_id = notify_admin_about_support(
                account_payload,
                message_text,
                attachments,
            )
            if tg_message_id:
                with connect() as conn:
                    conn.execute(
                        """
                        UPDATE web_support_messages
                        SET admin_tg_message_id = ?
                        WHERE id = ?
                        """,
                        (int(tg_message_id), message_id),
                    )
                    conn.commit()
        except Exception:
            logger.exception("Web support Telegram notification failed")

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"kingline-web-support-notify-{message_id}",
    ).start()


def promo_db_path():
    return os.environ.get("PROMOS_DB_PATH") or os.path.join(KINGLINE_DIR, "promos.db")


def ref_db_path():
    return os.environ["REFERRAL_DB_PATH"]


def ensure_referrals_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_tg_id INTEGER UNIQUE NOT NULL,
            ref_code TEXT UNIQUE NOT NULL,
            invited_json TEXT DEFAULT '[]'
        )
        """
    )


def normalize_referral_code(value):
    return re.sub(r"\s+", "", str(value or "").strip().upper())[:32]


def _dump_referral_items(items):
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _referral_invitation_for(conn, invited_tg_id):
    rows = conn.execute(
        "SELECT inviter_tg_id, ref_code, invited_json FROM referrals"
    ).fetchall()
    for row in rows:
        for item in _referral_items(row["invited_json"]):
            if int(item.get("tg_id") or 0) == int(invited_tg_id):
                return {
                    "inviterTgId": int(row["inviter_tg_id"]),
                    "code": row["ref_code"] or "",
                    "rewarded": int(item.get("rewarded") or 0),
                }
    return None


def _referral_has_invited(conn, inviter_tg_id, invited_tg_id):
    row = conn.execute(
        """
        SELECT invited_json
        FROM referrals
        WHERE inviter_tg_id = ?
        LIMIT 1
        """,
        (int(inviter_tg_id),),
    ).fetchone()
    if not row:
        return False
    return any(
        int(item.get("tg_id") or 0) == int(invited_tg_id)
        for item in _referral_items(row["invited_json"])
    )


def apply_referral_code(account, code_raw):
    web_tg_id, _username = ensure_web_user(account)
    code = normalize_referral_code(code_raw)
    if not code:
        raise ValueError("referral_empty")

    path = ref_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with connect_sqlite(path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        ensure_referrals_table(conn)
        row = cur.execute(
            """
            SELECT inviter_tg_id, ref_code, invited_json
            FROM referrals
            WHERE ref_code = ?
            LIMIT 1
            """,
            (code,),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("referral_not_found")

        inviter_tg_id = int(row["inviter_tg_id"])
        if inviter_tg_id == int(web_tg_id):
            conn.rollback()
            raise ValueError("referral_own_code")
        if _referral_has_invited(conn, web_tg_id, inviter_tg_id):
            conn.rollback()
            raise ValueError("referral_mutual")
        if _referral_invitation_for(conn, web_tg_id):
            conn.rollback()
            raise ValueError("referral_already_used")

        invited_items = _referral_items(row["invited_json"])
        invited_items.append({"tg_id": int(web_tg_id), "rewarded": 0})
        cur.execute(
            """
            UPDATE referrals
            SET invited_json = ?
            WHERE inviter_tg_id = ?
            """,
            (_dump_referral_items(invited_items), inviter_tg_id),
        )
        conn.commit()

    return public_referrals_for(web_tg_id)


def public_admin(row):
    permissions = {
        key: bool(row[f"can_{key}"] if f"can_{key}" in row.keys() else (0 if key in {"nodes", "node_credentials", "technical", "vpn_config", "documentation", "user_transport"} else 1))
        for key in ADMIN_PERMISSION_KEYS
    }
    return {
        "id": row["id"],
        "email": row["email"] or "",
        "tgId": row["tg_id"],
        "isOwner": bool(row["is_owner"] or 0),
        "permissions": permissions,
        "createdAt": row["created_at"],
        "createdByAccountId": row["created_by_account_id"],
    }


def admin_row_for_account(account):
    if not account:
        return None
    email = normalize_email(account["email"])
    tg_candidates = {
        int(value)
        for value in (account["linked_tg_id"], account["web_tg_id"])
        if value is not None
    }
    with connect() as conn:
        if email:
            row = conn.execute(
                """
                SELECT
                    id,
                    email,
                    tg_id,
                    is_owner,
                    can_analytics,
                    can_balance,
                    can_message,
                    can_block,
                    can_promos,
                    can_dns,
                    can_support,
                    can_payments,
                    can_broadcast,
                    can_delete_users,
                    can_nodes,
                    can_node_credentials,
                    can_technical,
                    can_vpn_config,
                    can_documentation,
                    can_user_transport,
                    created_at,
                    created_by_account_id
                FROM web_admins
                WHERE LOWER(email) = LOWER(?)
                ORDER BY is_owner DESC, id ASC
                LIMIT 1
                """,
                (email,),
            ).fetchone()
            if row:
                return row
        for tg_id in tg_candidates:
            row = conn.execute(
                """
                SELECT
                    id,
                    email,
                    tg_id,
                    is_owner,
                    can_analytics,
                    can_balance,
                    can_message,
                    can_block,
                    can_promos,
                    can_dns,
                    can_support,
                    can_payments,
                    can_broadcast,
                    can_delete_users,
                    can_nodes,
                    can_node_credentials,
                    can_technical,
                    can_vpn_config,
                    can_documentation,
                    can_user_transport,
                    created_at,
                    created_by_account_id
                FROM web_admins
                WHERE tg_id = ?
                ORDER BY is_owner DESC, id ASC
                LIMIT 1
                """,
                (tg_id,),
            ).fetchone()
            if row:
                return row
    return None


def admin_access_for_account(account):
    row = admin_row_for_account(account)
    return public_admin(row) if row else None


def is_admin_account(account):
    return bool(admin_access_for_account(account))


def is_owner_admin(account):
    access = admin_access_for_account(account)
    return bool(access and access.get("isOwner"))


def admin_has_permission(account, permission):
    access = admin_access_for_account(account)
    if not access:
        return False
    if access.get("isOwner"):
        return True
    permissions = access.get("permissions") or {}
    return bool(permissions.get(permission))


def _positive_tg_id(value):
    try:
        tg_id = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return tg_id if tg_id > 0 else 0


def admin_2fa_target_tg_id(account):
    row = admin_row_for_account(account)
    candidates = [
        _positive_tg_id(account["linked_tg_id"] if account else 0),
        _positive_tg_id(row["tg_id"] if row and "tg_id" in row.keys() else 0),
        _positive_tg_id(account["web_tg_id"] if account else 0),
    ]
    if row and bool(row["is_owner"] or 0):
        candidates.append(_positive_tg_id(ADMIN_ID))
    for tg_id in candidates:
        if tg_id:
            return tg_id
    return 0


def admin_2fa_hint(tg_id):
    if not tg_id:
        return ""
    raw = str(tg_id)
    return f"Telegram ***{raw[-4:]}" if len(raw) > 4 else "Telegram"


def admin_session_verified(token_digest):
    if not token_digest:
        return False
    with connect() as conn:
        row = conn.execute(
            """
            SELECT admin_verified_at
            FROM web_sessions
            WHERE token_hash = ?
            LIMIT 1
            """,
            (token_digest,),
        ).fetchone()
    return bool(row and row["admin_verified_at"])


def admin_2fa_code_hash(token_digest, code):
    return hash_token(f"{token_digest}:{str(code or '').strip()}")


def create_admin_2fa_challenge(account, token_digest, client_ip, user_agent):
    tg_id = admin_2fa_target_tg_id(account)
    if not tg_id:
        raise ValueError("admin_2fa_unavailable")
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = (utc_now() + timedelta(minutes=ADMIN_2FA_MINUTES)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    old_tg_id = 0
    old_message_id = 0
    with connect() as conn:
        old_row = conn.execute(
            """
            SELECT sent_to_tg_id, admin_tg_message_id
            FROM web_admin_2fa_challenges
            WHERE token_hash = ?
            LIMIT 1
            """,
            (token_digest,),
        ).fetchone()
        if old_row:
            old_tg_id = int(old_row["sent_to_tg_id"] or 0)
            old_message_id = int(old_row["admin_tg_message_id"] or 0)
        conn.execute(
            """
            INSERT INTO web_admin_2fa_challenges (
                token_hash,
                account_id,
                code_hash,
                sent_to_tg_id,
                expires_at,
                attempts,
                created_at,
                updated_at,
                ip,
                user_agent
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(token_hash) DO UPDATE SET
                account_id = excluded.account_id,
                code_hash = excluded.code_hash,
                sent_to_tg_id = excluded.sent_to_tg_id,
                admin_tg_message_id = NULL,
                expires_at = excluded.expires_at,
                attempts = 0,
                updated_at = excluded.updated_at,
                ip = excluded.ip,
                user_agent = excluded.user_agent
            """,
            (
                token_digest,
                int(account["id"]),
                admin_2fa_code_hash(token_digest, code),
                tg_id,
                expires_at,
                iso_now(),
                iso_now(),
                client_ip,
                (user_agent or "")[:300],
            ),
        )
        conn.commit()
    message_id = send_telegram_message(
        tg_id,
        (
            "<b>Вход в админку Kingline Proxy</b>\n\n"
            "Код подтверждения:\n"
            f"<code>{escape(code)}</code>\n\n"
            f"Действует {ADMIN_2FA_MINUTES} минут.\n"
            "Если это были не вы, смените пароль."
        ),
    )
    if message_id:
        with connect() as conn:
            conn.execute(
                """
                UPDATE web_admin_2fa_challenges
                SET admin_tg_message_id = ?,
                    updated_at = ?
                WHERE token_hash = ?
                """,
                (int(message_id), iso_now(), token_digest),
            )
            conn.commit()
    if old_tg_id and old_message_id:
        delete_telegram_message(old_tg_id, old_message_id)
    return {"sentTo": admin_2fa_hint(tg_id), "expiresAt": expires_at}


def verify_admin_2fa_challenge(account, token_digest, code):
    clean_code = str(code or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d{6}", clean_code):
        raise ValueError("admin_2fa_invalid")
    tg_id = 0
    message_id = 0
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            """
            SELECT *
            FROM web_admin_2fa_challenges
            WHERE token_hash = ? AND account_id = ?
            LIMIT 1
            """,
            (token_digest, int(account["id"])),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("admin_2fa_required")
        expires = parse_iso(row["expires_at"])
        if not expires or expires <= utc_now():
            cur.execute("DELETE FROM web_admin_2fa_challenges WHERE token_hash = ?", (token_digest,))
            conn.commit()
            raise ValueError("admin_2fa_expired")
        if int(row["attempts"] or 0) >= ADMIN_2FA_ATTEMPTS:
            cur.execute("DELETE FROM web_admin_2fa_challenges WHERE token_hash = ?", (token_digest,))
            conn.commit()
            raise ValueError("admin_2fa_too_many")
        if not hmac.compare_digest(row["code_hash"], admin_2fa_code_hash(token_digest, clean_code)):
            cur.execute(
                """
                UPDATE web_admin_2fa_challenges
                SET attempts = COALESCE(attempts, 0) + 1,
                    updated_at = ?
                WHERE token_hash = ?
                """,
                (iso_now(), token_digest),
            )
            conn.commit()
            raise ValueError("admin_2fa_invalid")
        tg_id = int(row["sent_to_tg_id"] or 0)
        message_id = int(row["admin_tg_message_id"] or 0) if "admin_tg_message_id" in row.keys() else 0
        cur.execute(
            """
            UPDATE web_sessions
            SET admin_verified_at = ?
            WHERE token_hash = ?
            """,
            (iso_now(), token_digest),
        )
        cur.execute("DELETE FROM web_admin_2fa_challenges WHERE token_hash = ?", (token_digest,))
        conn.commit()
    if tg_id and message_id:
        delete_telegram_message(tg_id, message_id)


def admin_2fa_bootstrap(account, token_digest):
    tg_id = admin_2fa_target_tg_id(account)
    return {
        "ok": True,
        "admin2faRequired": True,
        "telegramHint": admin_2fa_hint(tg_id),
        "telegramAvailable": bool(tg_id),
        "admin": {
            "accountId": account["id"],
            "email": account["email"],
            "isOwner": is_owner_admin(account),
            "canManageAdmins": False,
        },
    }


def log_admin_action(account, action, target="", summary=None, client_ip="", user_agent=""):
    try:
        summary_text = json.dumps(summary or {}, ensure_ascii=False, default=str)[:3000]
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO web_admin_audit_log (
                    admin_account_id,
                    admin_email,
                    action,
                    target,
                    summary_json,
                    ip,
                    user_agent,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(account["id"]) if account else None,
                    account["email"] if account else "",
                    str(action or "")[:80],
                    str(target or "")[:160],
                    summary_text,
                    str(client_ip or "")[:80],
                    str(user_agent or "")[:300],
                    iso_now(),
                ),
            )
            conn.commit()
    except Exception:
        logger.exception("Admin audit log failed action=%s", action)


def admin_audit_payload(query):
    raw_limit = (query.get("limit") or ["200"])[0]
    try:
        limit = int(raw_limit)
    except Exception:
        limit = 200
    limit = max(20, min(limit, 500))

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                admin_account_id,
                admin_email,
                action,
                target,
                summary_json,
                ip,
                user_agent,
                created_at
            FROM web_admin_audit_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    items = []
    for row in rows:
        summary = json_loads_safe(row["summary_json"], {})
        if not isinstance(summary, dict):
            summary = {}
        items.append(
            {
                "id": row["id"],
                "adminAccountId": row["admin_account_id"],
                "adminEmail": public_email(row["admin_email"]) or row["admin_email"] or "Админ",
                "action": row["action"] or "",
                "target": row["target"] or "",
                "summary": summary,
                "ip": row["ip"] or "",
                "userAgent": row["user_agent"] or "",
                "createdAt": row["created_at"],
            }
        )

    return {"ok": True, "items": items}


def web_account_for_tg_expression():
    return """
        SELECT email
        FROM web_accounts wa
        WHERE wa.linked_tg_id = u.tg_id
           OR wa.web_tg_id = u.tg_id
        ORDER BY wa.id ASC
        LIMIT 1
    """


def admin_public_user(row, number):
    tg_id = row["tg_id"]
    visible_email = public_email(row["email"])
    return {
        "number": number,
        "userId": row["user_id"],
        "tgId": tg_id,
        "name": row["tg_name"] or row["web_name"] or visible_email or "",
        "username": row["username"] or "",
        "email": visible_email,
        "webAccountId": row["web_account_id"],
        "tariff": row["tariff"] or "free",
        "startedAt": row["subscription_started_at"] or row["key_created_at"],
        "expiresAt": row["subscription_expires_at"] or row["key_expires_at"],
        "balanceRub": int(row["balance_rub"] or 0),
        "blocked": bool(row["is_blocked"] or 0),
        "location": normalize_location(row["location_pref"]),
        "emailVerified": bool(row["email_verified"] or 0),
        "telegramLinked": bool(row["linked_tg_id"]),
        "canMessageTelegram": bool(tg_id and int(tg_id) > 0),
        "canMessageEmail": bool(visible_email),
    }


def admin_list_users():
    users = []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id AS user_id,
                u.tg_id,
                u.tg_name,
                u.username,
                u.tariff,
                u.key_created_at,
                u.key_expires_at,
                u.balance_rub,
                u.subscription_started_at,
                u.subscription_expires_at,
                u.location_pref,
                u.is_blocked,
                wa.id AS web_account_id,
                wa.email,
                wa.name AS web_name,
                wa.email_verified,
                wa.linked_tg_id
            FROM users u
            LEFT JOIN web_accounts wa
              ON wa.linked_tg_id = u.tg_id
              OR wa.web_tg_id = u.tg_id
            ORDER BY u.id ASC
            """
        ).fetchall()
        for index, row in enumerate(rows, start=1):
            users.append(admin_public_user(row, index))

        missing_accounts = conn.execute(
            """
            SELECT
                NULL AS user_id,
                COALESCE(wa.linked_tg_id, wa.web_tg_id) AS tg_id,
                NULL AS tg_name,
                NULL AS username,
                'free' AS tariff,
                NULL AS key_created_at,
                NULL AS key_expires_at,
                0 AS balance_rub,
                NULL AS subscription_started_at,
                NULL AS subscription_expires_at,
                'auto' AS location_pref,
                0 AS is_blocked,
                wa.id AS web_account_id,
                wa.email,
                wa.name AS web_name,
                wa.email_verified,
                wa.linked_tg_id
            FROM web_accounts wa
            WHERE NOT EXISTS (
                SELECT 1
                FROM users u
                WHERE u.tg_id = wa.linked_tg_id
                   OR u.tg_id = wa.web_tg_id
            )
            ORDER BY wa.id ASC
            """
        ).fetchall()
        for row in missing_accounts:
            users.append(admin_public_user(row, len(users) + 1))
    return users


def delete_user_records(admin_account, tg_id=0, web_account_id=0):
    tg_id = int(tg_id or 0)
    web_account_id = int(web_account_id or 0)
    if not tg_id and not web_account_id:
        raise ValueError("invalid_user")

    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        accounts = cur.execute(
            f"""
            SELECT {account_select()}
            FROM web_accounts
            WHERE (? > 0 AND id = ?)
               OR (? > 0 AND (linked_tg_id = ? OR web_tg_id = ?))
            """,
            (web_account_id, web_account_id, tg_id, tg_id, tg_id),
        ).fetchall()

        account_ids = {int(row["id"]) for row in accounts}
        tg_ids = {tg_id} if tg_id else set()
        emails = set()
        for row in accounts:
            if int(row["id"]) == int(admin_account["id"]):
                conn.rollback()
                raise ValueError("cannot_remove_self")
            if is_owner_admin(row) or normalize_email(row["email"]) == PRIMARY_ADMIN_EMAIL:
                conn.rollback()
                raise ValueError("cannot_remove_owner")
            emails.add(normalize_email(row["email"]))
            for candidate in (row["linked_tg_id"], row["web_tg_id"]):
                if candidate:
                    tg_ids.add(int(candidate))

        self_tg_ids = {
            int(value)
            for value in (admin_account["linked_tg_id"], admin_account["web_tg_id"])
            if value
        }
        if tg_ids.intersection(self_tg_ids):
            conn.rollback()
            raise ValueError("cannot_remove_self")

        if tg_ids:
            placeholders = ",".join("?" for _ in tg_ids)
            owner_row = cur.execute(
                f"""
                SELECT id
                FROM web_admins
                WHERE is_owner = 1 AND tg_id IN ({placeholders})
                LIMIT 1
                """,
                tuple(tg_ids),
            ).fetchone()
            if owner_row:
                conn.rollback()
                raise ValueError("cannot_remove_owner")

        if not is_owner_admin(admin_account):
            admin_checks = []
            admin_params = []
            if emails:
                placeholders = ",".join("?" for _ in emails)
                admin_checks.append(f"LOWER(email) IN ({placeholders})")
                admin_params.extend(email.lower() for email in emails)
            if tg_ids:
                placeholders = ",".join("?" for _ in tg_ids)
                admin_checks.append(f"tg_id IN ({placeholders})")
                admin_params.extend(tg_ids)
            if admin_checks:
                target_admin = cur.execute(
                    f"""
                    SELECT id
                    FROM web_admins
                    WHERE is_owner = 0
                      AND ({' OR '.join(admin_checks)})
                    LIMIT 1
                    """,
                    tuple(admin_params),
                ).fetchone()
                if target_admin:
                    conn.rollback()
                    raise ValueError("permission_denied")

        usernames = set()
        if tg_ids:
            placeholders = ",".join("?" for _ in tg_ids)
            rows = cur.execute(
                f"SELECT username FROM users WHERE tg_id IN ({placeholders})",
                tuple(tg_ids),
            ).fetchall()
            usernames = {
                normalize_xui_online_username(row["username"])
                for row in rows
                if normalize_xui_online_username(row["username"])
            }

        if account_ids:
            placeholders = ",".join("?" for _ in account_ids)
            params = tuple(account_ids)
            for table in (
                "web_sessions",
                "web_link_codes",
                "web_email_verifications",
                "web_password_resets",
                "web_email_link_requests",
                "web_account_jobs",
                "web_email_reminders",
                "web_telegram_link_reminders",
                "web_support_messages",
                "web_support_typing",
                "web_admin_2fa_challenges",
            ):
                cur.execute(
                    f"DELETE FROM {table} WHERE account_id IN ({placeholders})",
                    params,
                )
            cur.execute(
                f"DELETE FROM web_accounts WHERE id IN ({placeholders})",
                params,
            )

        if emails:
            placeholders = ",".join("?" for _ in emails)
            cur.execute(
                f"DELETE FROM web_pending_registrations WHERE email IN ({placeholders})",
                tuple(emails),
            )
            cur.execute(
                f"DELETE FROM web_magic_links WHERE email IN ({placeholders})",
                tuple(emails),
            )
            cur.execute(
                f"DELETE FROM web_login_attempts WHERE email IN ({placeholders})",
                tuple(emails),
            )
            cur.execute(
                f"""
                DELETE FROM web_admins
                WHERE is_owner = 0 AND email IN ({placeholders})
                """,
                tuple(emails),
            )

        if tg_ids:
            placeholders = ",".join("?" for _ in tg_ids)
            params = tuple(tg_ids)
            from vpn_preferences import forget
            for actor in tg_ids:forget(cur,actor)
            cur.execute(
                f"DELETE FROM web_admins WHERE is_owner = 0 AND tg_id IN ({placeholders})",
                params,
            )
            cur.execute(
                f"DELETE FROM payments WHERE tg_id IN ({placeholders})",
                params,
            )
            cur.execute(
                f"DELETE FROM referrals WHERE inviter_tg_id IN ({placeholders})",
                params,
            )
            cur.execute(
                f"DELETE FROM users WHERE tg_id IN ({placeholders})",
                params,
            )
            cur.execute(
                f"DELETE FROM web_xui_user_samples WHERE tg_id IN ({placeholders})",
                params,
            )
            cur.execute(
                f"DELETE FROM web_xui_client_aliases WHERE tg_id IN ({placeholders})",
                params,
            )
            cur.execute(
                f"DELETE FROM web_xui_ip_history WHERE tg_id IN ({placeholders})",
                params,
            )

        if usernames:
            placeholders = ",".join("?" for _ in usernames)
            params = tuple(usernames)
            cur.execute(
                f"DELETE FROM web_xui_user_samples WHERE username IN ({placeholders})",
                params,
            )
            cur.execute(
                f"DELETE FROM web_xui_client_aliases WHERE username IN ({placeholders})",
                params,
            )
            cur.execute(
                f"DELETE FROM web_xui_ip_history WHERE username IN ({placeholders})",
                params,
            )

        conn.commit()


def normalize_xui_online_username(value):
    raw = str(value or "").strip()
    if "#" in raw:
        raw = raw.split("#", 1)[0].strip()
    return raw.replace("@", "").replace(" ", "").strip().lower()


def is_system_xui_username(username):
    normalized = normalize_xui_online_username(username)
    return normalized in {"", "api", "whitelist", "whitelisted", "system"} or normalized.startswith("whitelist")


def extract_xui_online_items(payload):
    source = payload
    if isinstance(source, dict):
        for key in ("obj", "data", "rows", "online", "onlines", "clients"):
            if key in source:
                source = source.get(key)
                break
    if isinstance(source, dict):
        for key in ("emails", "users", "clients", "list", "items"):
            value = source.get(key)
            if isinstance(value, list):
                source = value
                break
        if isinstance(source, dict):
            source = list(source.values())
    if not isinstance(source, list):
        return []

    items = []
    for item in source:
        if isinstance(item, dict):
            raw = (
                item.get("email")
                or item.get("clientEmail")
                or item.get("username")
                or item.get("name")
                or item.get("subId")
                or item.get("id")
            )
        else:
            raw = item
        username = normalize_xui_online_username(raw)
        if username and not is_system_xui_username(username):
            raw_text = str(raw or "").strip()
            items.append({"raw": raw_text, "email": raw_text, "username": username})
    return items


def xui_online_name_map():
    result = {}
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id AS user_id,
                u.tg_id,
                u.username,
                u.tg_name,
                wa.id AS web_account_id,
                wa.name AS web_name,
                wa.email
            FROM users u
            LEFT JOIN web_accounts wa
              ON wa.linked_tg_id = u.tg_id
              OR wa.web_tg_id = u.tg_id
            """
        ).fetchall()
    for row in rows:
        key = normalize_xui_online_username(row["username"])
        if not key or key in result:
            continue
        result[key] = {
            "name": row["web_name"] or row["tg_name"] or row["email"] or row["username"] or key,
            "tgId": row["tg_id"],
            "userId": row["user_id"],
            "webAccountId": row["web_account_id"],
            "email": row["email"] or "",
        }
    return result


async def fetch_xui_panel_online(panel):
    tag = (panel.get("tag") or "XUI").strip().upper()
    url = (panel.get("url") or "").strip().rstrip("/")
    host = urlparse(url).netloc or url
    try:
        async with asyncio.timeout(XUI_ONLINE_TIMEOUT_SECONDS), XUIClient(
            url,
            panel.get("username") or "",
            panel.get("password") or "",
            webbasepath=panel.get("webbasepath") or "",
            timeout=3,
            verify_ssl=panel.get("verify_ssl", True),
            logger_=logger,
        ) as xui:
            status, payload = await xui._post_form(
                xui._panel_api_url("/api/inbounds/onlines"),
                {},
            )
        if status != 200 or (isinstance(payload, dict) and payload.get("success") is False):
            raise RuntimeError(f"onlines HTTP {status}")
        items = extract_xui_online_items(payload)
        return {
            "tag": tag,
            "host": host,
            "ok": True,
            "count": len(items),
            "items": items,
        }
    except Exception as exc:
        logger.warning("XUI online unavailable tag=%s host=%s: %s", tag, host, type(exc).__name__)
        return {
            "tag": tag,
            "host": host,
            "ok": False,
            "count": 0,
            "items": [],
        }


async def fetch_xui_online_snapshot():
    panels = BillingDB._get_panels()
    metadata = xui_online_name_map()
    panel_results = {
        (panel.get("tag") or "XUI").strip().upper(): {
            "tag": (panel.get("tag") or "XUI").strip().upper(),
            "host": urlparse(panel.get("url") or "").netloc,
            "ok": False, "pending": True, "count": 0, "items": [],
        }
        for panel in panels
    }
    tasks = [asyncio.create_task(fetch_xui_panel_online(panel)) for panel in panels]
    try:
        for completed in asyncio.as_completed(tasks):
            panel = await completed
            panel_results[panel["tag"]] = panel
            snapshot = build_xui_online_snapshot(list(panel_results.values()), metadata)
            with XUI_ONLINE_CACHE_LOCK:
                XUI_ONLINE_CACHE.update(created=time.monotonic(), data=snapshot)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return build_xui_online_snapshot(list(panel_results.values()), metadata)


def build_xui_online_snapshot(panel_results, metadata):
    users = {}
    for panel in panel_results:
        for item in panel["items"]:
            username = item["username"]
            info = metadata.get(username, {})
            entry = users.setdefault(
                username,
                {
                    "username": username,
                    "name": info.get("name", ""),
                    "tgId": info.get("tgId"),
                    "userId": info.get("userId"),
                    "webAccountId": info.get("webAccountId"),
                    "clientCount": 0,
                    "panels": [],
                },
            )
            entry["clientCount"] += 1
            if panel["tag"] not in entry["panels"]:
                entry["panels"].append(panel["tag"])

    online_users = sorted(users.values(), key=lambda item: item["username"])
    return {
        "ok": True,
        "updatedAt": iso_now() if any(panel["ok"] for panel in panel_results) else None,
        "loading": any(panel.get("pending") for panel in panel_results) and not any(
            panel["ok"] for panel in panel_results
        ),
        "partial": any(not panel["ok"] for panel in panel_results),
        "error": "panels_unavailable" if panel_results and not any(
            panel["ok"] or panel.get("pending") for panel in panel_results
        ) else None,
        "count": len(online_users),
        "rawCount": sum(panel["count"] for panel in panel_results),
        "users": online_users,
        "panels": [
            {
                "tag": panel["tag"],
                "host": panel["host"],
                "ok": panel["ok"],
                "pending": bool(panel.get("pending")),
                "count": panel["count"],
            }
            for panel in panel_results
        ],
    }


def refresh_xui_online_cache():
    try:
        snapshot = asyncio.run(fetch_xui_online_snapshot())
        with XUI_ONLINE_CACHE_LOCK:
            XUI_ONLINE_CACHE.update(created=time.monotonic(), data=snapshot)
    except Exception:
        logger.exception("XUI online refresh failed")
    finally:
        with XUI_ONLINE_CACHE_LOCK:
            XUI_ONLINE_CACHE["refreshing"] = False


def admin_online_snapshot():
    now = time.monotonic()
    with XUI_ONLINE_CACHE_LOCK:
        cached = XUI_ONLINE_CACHE.get("data")
        created = float(XUI_ONLINE_CACHE.get("created") or 0.0)
        stale = not cached or now - created >= XUI_ONLINE_CACHE_TTL_SECONDS
        if stale and not XUI_ONLINE_CACHE["refreshing"]:
            XUI_ONLINE_CACHE["refreshing"] = True
            threading.Thread(
                target=refresh_xui_online_cache, daemon=True, name="kingline-xui-online",
            ).start()
        return {
            **(cached or {"ok": True, "loading": True, "count": 0, "rawCount": 0,
                         "users": [], "panels": [], "updatedAt": None}),
            "stale": bool(stale),
            "refreshing": XUI_ONLINE_CACHE["refreshing"],
        }


def int_from_any(value, default=0):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def json_loads_safe(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def extract_xui_client_stats(inbound):
    stats = []
    obj = inbound.get("obj") if isinstance(inbound.get("obj"), dict) else {}
    for key in ("clientStats", "client_stats", "stats"):
        if isinstance(inbound.get(key), list):
            stats.extend(inbound[key])
        if isinstance(obj.get(key), list):
            stats.extend(obj[key])
    return [row for row in stats if isinstance(row, dict)]


def extract_xui_clients_for_analytics(inbound):
    clients_by_email = {}
    for client in extract_clients_from_inbound(inbound):
        email = str(client.get("email") or "").strip()
        if not email:
            continue
        clients_by_email[email] = dict(client)

    for stat in extract_xui_client_stats(inbound):
        email = str(stat.get("email") or stat.get("clientEmail") or "").strip()
        if not email:
            continue
        merged = clients_by_email.setdefault(email, {})
        merged.update(stat)
        merged["email"] = email

    return list(clients_by_email.values())


def xui_client_traffic(client):
    up = int_from_any(
        client.get("up")
        or client.get("upload")
        or client.get("uplink")
        or client.get("sent")
    )
    down = int_from_any(
        client.get("down")
        or client.get("download")
        or client.get("downlink")
        or client.get("recv")
        or client.get("received")
    )
    total = int_from_any(client.get("total") or client.get("totalBytes"))
    if total <= 0:
        total = up + down
    return up, down, total


def merge_panel_json(existing, panel_tag):
    panels = set(json_loads_safe(existing, []))
    if panel_tag:
        panels.add(panel_tag)
    return json.dumps(sorted(panels), ensure_ascii=False)


def upsert_xui_ip(username, tg_id, ip, panel_tag, seen_at):
    ip = str(ip or "").strip()
    if not username or not ip:
        return
    with connect() as conn:
        row = conn.execute(
            """
            SELECT panels_json
            FROM web_xui_ip_history
            WHERE username = ? AND ip = ?
            """,
            (username, ip),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE web_xui_ip_history
                SET tg_id = COALESCE(?, tg_id),
                    last_seen_at = ?,
                    seen_count = seen_count + 1,
                    panels_json = ?
                WHERE username = ? AND ip = ?
                """,
                (
                    tg_id,
                    seen_at,
                    merge_panel_json(row["panels_json"], panel_tag),
                    username,
                    ip,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO web_xui_ip_history (
                    username,
                    tg_id,
                    ip,
                    first_seen_at,
                    last_seen_at,
                    panels_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    tg_id,
                    ip,
                    seen_at,
                    seen_at,
                    merge_panel_json("[]", panel_tag),
                ),
            )
        conn.commit()


async def fetch_xui_panel_analytics(panel):
    tag = (panel.get("tag") or "XUI").strip().upper()
    url = (panel.get("url") or "").strip().rstrip("/")
    host = urlparse(url).netloc or url
    result = {"tag": tag, "host": host, "ok": False, "users": {}, "aliases": []}
    try:
        async with asyncio.timeout(XUI_ANALYTICS_TIMEOUT_SECONDS), XUIClient(
            url,
            panel.get("username") or "",
            panel.get("password") or "",
            webbasepath=panel.get("webbasepath") or "",
            timeout=3,
            verify_ssl=panel.get("verify_ssl", True),
            logger_=logger,
        ) as xui:
            inbounds = await xui.list_inbounds()
            status, online_payload = await xui._post_form(
                xui._panel_api_url("/api/inbounds/onlines"),
                {},
            )
        online_counts = {}
        if status == 200:
            for item in extract_xui_online_items(online_payload):
                online_counts[item["username"]] = online_counts.get(item["username"], 0) + 1

        users = {}
        aliases = []
        for inbound in inbounds:
            inbound_id = int_from_any(inbound.get("id") or (inbound.get("obj") or {}).get("id"))
            for client in extract_xui_clients_for_analytics(inbound):
                email = str(client.get("email") or "").strip()
                username = normalize_xui_online_username(email)
                if not username or is_system_xui_username(username):
                    continue
                up, down, total = xui_client_traffic(client)
                entry = users.setdefault(
                    username,
                    {
                        "username": username,
                        "up": 0,
                        "down": 0,
                        "total": 0,
                        "online": 0,
                        "panels": set(),
                    },
                )
                entry["up"] += up
                entry["down"] += down
                entry["total"] += total
                entry["online"] = max(entry["online"], online_counts.get(username, 0))
                entry["panels"].add(tag)
                aliases.append(
                    {
                        "username": username,
                        "clientEmail": email,
                        "panelTag": tag,
                        "inboundId": inbound_id,
                    }
                )
        for username, count in online_counts.items():
            if is_system_xui_username(username):
                continue
            entry = users.setdefault(
                username,
                {
                    "username": username,
                    "up": 0,
                    "down": 0,
                    "total": 0,
                    "online": 0,
                    "panels": set(),
                },
            )
            entry["online"] = max(entry["online"], count)
            entry["panels"].add(tag)
        result.update({"ok": True, "users": users, "aliases": aliases})
    except Exception as exc:
        logger.warning("XUI analytics unavailable tag=%s host=%s: %s", tag, host, type(exc).__name__)
    return result


async def collect_xui_analytics_snapshot():
    panels = BillingDB._get_panels()
    panel_results = await asyncio.gather(
        *(fetch_xui_panel_analytics(panel) for panel in panels),
        return_exceptions=False,
    )
    metadata = xui_online_name_map()
    users = {}
    aliases = []
    for panel in panel_results:
        aliases.extend(panel.get("aliases") or [])
        for username, item in (panel.get("users") or {}).items():
            info = metadata.get(username, {})
            entry = users.setdefault(
                username,
                {
                    "username": username,
                    "tgId": info.get("tgId"),
                    "name": info.get("name", ""),
                    "up": 0,
                    "down": 0,
                    "total": 0,
                    "online": 0,
                    "panels": set(),
                },
            )
            entry["up"] += int(item.get("up") or 0)
            entry["down"] += int(item.get("down") or 0)
            entry["total"] += int(item.get("total") or 0)
            entry["online"] += int(item.get("online") or 0)
            entry["panels"].update(item.get("panels") or [])
    return users, aliases, panel_results


@timed_sync("analytics.snapshot", slow_ms=4000)
def store_xui_analytics_snapshot():
    sampled_at = iso_now()
    users, aliases, panels = asyncio.run(collect_xui_analytics_snapshot())
    with connect() as conn:
        for item in users.values():
            online_clients = int(item.get("online") or 0)
            conn.execute(
                """
                INSERT INTO web_xui_user_samples (
                    sampled_at,
                    username,
                    tg_id,
                    name,
                    up_bytes,
                    down_bytes,
                    total_bytes,
                    online_clients,
                    panels_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sampled_at,
                    item["username"],
                    item.get("tgId"),
                    item.get("name") or "",
                    int(item.get("up") or 0),
                    int(item.get("down") or 0),
                    int(item.get("total") or 0),
                    online_clients,
                    json.dumps(sorted(item.get("panels") or []), ensure_ascii=False),
                ),
            )
            if online_clients > 0:
                conn.execute(
                    """
                    INSERT INTO web_xui_user_last_seen (
                        username,
                        tg_id,
                        name,
                        last_seen_at,
                        online_clients,
                        panels_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(username) DO UPDATE SET
                        tg_id = excluded.tg_id,
                        name = excluded.name,
                        last_seen_at = excluded.last_seen_at,
                        online_clients = excluded.online_clients,
                        panels_json = excluded.panels_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item["username"],
                        item.get("tgId"),
                        item.get("name") or "",
                        sampled_at,
                        online_clients,
                        json.dumps(sorted(item.get("panels") or []), ensure_ascii=False),
                        sampled_at,
                    ),
                )
        for alias in aliases:
            info = users.get(alias["username"], {})
            conn.execute(
                """
                INSERT INTO web_xui_client_aliases (
                    username,
                    client_email,
                    tg_id,
                    name,
                    panel_tag,
                    inbound_id,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(username, client_email, panel_tag, inbound_id)
                DO UPDATE SET
                    tg_id = excluded.tg_id,
                    name = excluded.name,
                    updated_at = excluded.updated_at
                """,
                (
                    alias["username"],
                    alias["clientEmail"],
                    info.get("tgId"),
                    info.get("name") or "",
                    alias.get("panelTag") or "",
                    alias.get("inboundId") or 0,
                    sampled_at,
                ),
            )
        conn.commit()
    logger.info(
        "XUI analytics snapshot stored users=%s panels=%s",
        len(users),
        [(p.get("tag"), p.get("ok")) for p in panels],
    )
    return len(users)


def xui_analytics_loop():
    time.sleep(10)
    while True:
        try:
            store_xui_analytics_snapshot()
        except Exception:
            logger.exception("XUI analytics cycle failed")
        time.sleep(XUI_ANALYTICS_POLL_INTERVAL_SECONDS)


def start_xui_analytics_thread():
    global XUI_ANALYTICS_THREAD_STARTED
    if XUI_ANALYTICS_THREAD_STARTED:
        return
    XUI_ANALYTICS_THREAD_STARTED = True
    thread = threading.Thread(
        target=xui_analytics_loop,
        daemon=True,
        name="kingline-xui-analytics",
    )
    thread.start()


def parse_admin_datetime(value):
    if not value:
        return None
    parsed = parse_iso(value)
    if parsed:
        return parsed
    raw = str(value).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def analytics_period(query):
    now = utc_now()
    range_key = (query.get("range") or ["month"])[0] or "month"
    if range_key == "ten":
        return range_key, now - timedelta(minutes=10), now
    if range_key == "hour":
        return range_key, now - timedelta(hours=1), now
    if range_key == "day":
        return range_key, now - timedelta(days=1), now
    if range_key == "week":
        return range_key, now - timedelta(days=7), now
    if range_key == "year":
        return range_key, now - timedelta(days=365), now
    if range_key == "all":
        with connect() as conn:
            row = conn.execute(
                "SELECT MIN(sampled_at) AS first_sample FROM web_xui_user_samples"
            ).fetchone()
        start = parse_iso(row["first_sample"]) if row and row["first_sample"] else now - timedelta(days=30)
        return range_key, start, now
    if range_key == "custom":
        start = parse_admin_datetime((query.get("start") or [""])[0])
        end = parse_admin_datetime((query.get("end") or [""])[0]) or now
        if not start or start >= end:
            start = end - timedelta(days=30)
        return range_key, start, end
    return "month", now - timedelta(days=30), now


def ensure_recent_xui_analytics():
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT MAX(sampled_at) AS sampled_at FROM web_xui_user_samples"
            ).fetchone()
        latest = parse_iso(row["sampled_at"]) if row and row["sampled_at"] else None
        if not latest or (utc_now() - latest).total_seconds() > XUI_ANALYTICS_POLL_INTERVAL_SECONDS * 2:
            store_xui_analytics_snapshot()
    except Exception:
        logger.exception("Unable to seed recent XUI analytics")


def bucket_label(dt, range_key):
    dt = dt + timedelta(hours=3)
    if range_key in {"ten", "hour", "day"}:
        return dt.strftime("%H:%M")
    if range_key == "year":
        return dt.strftime("%m.%Y")
    return dt.strftime("%d.%m")


def analytics_buckets(start, end, range_key):
    total_seconds = max(60, int((end - start).total_seconds()))
    total_days = total_seconds / 86400
    target = 24
    bucket_seconds = max(60, int(total_seconds / target))
    if range_key == "ten":
        bucket_seconds = 60
    elif range_key == "hour":
        bucket_seconds = 5 * 60
    elif range_key == "day":
        bucket_seconds = 60 * 60
    elif range_key == "week":
        bucket_seconds = 24 * 60 * 60
    elif range_key == "month":
        bucket_seconds = 24 * 60 * 60
    elif range_key == "year":
        bucket_seconds = 30 * 24 * 60 * 60
    elif range_key in {"all", "custom"}:
        if total_days <= 2:
            bucket_seconds = 60 * 60
        elif total_days <= 90:
            bucket_seconds = 24 * 60 * 60
        elif total_days <= 730:
            bucket_seconds = 30 * 24 * 60 * 60
        else:
            bucket_seconds = 90 * 24 * 60 * 60
    count = max(1, min(60, int((total_seconds + bucket_seconds - 1) / bucket_seconds)))
    buckets = []
    for index in range(count):
        dt = start + timedelta(seconds=index * bucket_seconds)
        buckets.append(
            {
                "start": dt,
                "label": bucket_label(dt, range_key),
                "upBytes": 0,
                "downBytes": 0,
                "totalBytes": 0,
                "onlineSeconds": 0,
                "onlineUsers": set(),
            }
        )
    return buckets, bucket_seconds


def analytics_sample_lookback(start, bucket_seconds):
    seconds = max(
        XUI_ANALYTICS_POLL_INTERVAL_SECONDS * 4,
        min(int(bucket_seconds or 0), 60 * 60),
    )
    return start - timedelta(seconds=seconds)


def sample_online_sessions(samples, start, end, sample_interval_seconds=None):
    interval_seconds = int(sample_interval_seconds or XUI_ANALYTICS_POLL_INTERVAL_SECONDS)
    session_gap_seconds = max(XUI_ANALYTICS_SESSION_GAP_SECONDS, int(interval_seconds * 1.5))
    sessions = []
    session_start = None
    last_online = None
    for row in samples:
        dt = parse_iso(row["sampled_at"])
        if not dt or dt < start or dt > end:
            continue
        if int(row["online_clients"] or 0) <= 0:
            continue
        if session_start is None:
            session_start = dt
        elif last_online and (dt - last_online).total_seconds() > session_gap_seconds:
            sessions.append((session_start, min(end, last_online + timedelta(seconds=interval_seconds))))
            session_start = dt
        last_online = dt
    if session_start and last_online:
        sessions.append((session_start, min(end, last_online + timedelta(seconds=interval_seconds))))
    seconds = sum(max(0, int((finish - begin).total_seconds())) for begin, finish in sessions)
    return seconds, sessions, last_online


XUI_ACCEPTED_LOG_RE = re.compile(
    r"^(?:\[(?P<panel>[^\]]+)\]\s+)?"
    r"(?P<ts>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    r"from\s+(?P<src>\[[^\]]+\]|[^:\s]+)(?::\d+)?\s+"
    r"accepted\s+(?P<proto>tcp|udp):(?P<target>\[[^\]]+\]|[^:\s]+)"
    r"(?::(?P<port>\d+))?\s+\[[^\]]*\]\s+email:\s*(?P<email>.+)$",
    re.IGNORECASE,
)

SITE_DISPLAY_RULES = (
    (("googlevideo.com", "youtube.com", "youtu.be", "ytimg.com", "youtubei.googleapis.com"), "YouTube"),
    (("cdninstagram.com", "instagram.com"), "Instagram"),
    (("facebook.com", "fbcdn.net", "messenger.com"), "Facebook"),
    (("t.me", "telegram.org", "telegram-cdn.org"), "Telegram"),
    (("vk.com", "vk.ru", "vkuseraudio.net", "userapi.com", "vk-cdn.net"), "VK"),
    (("openai.com", "chatgpt.com"), "ChatGPT"),
    (("spotify.com", "scdn.co"), "Spotify"),
    (("gmail.com", "googlemail.com"), "Gmail"),
    (("apple.com", "icloud.com", "mzstatic.com", "itunes.apple.com"), "Apple"),
    (("google.com", "googleapis.com", "gstatic.com", "ggpht.com", "googleusercontent.com"), "Google"),
    (("yandex.ru", "yandex.net", "ya.ru"), "Yandex"),
    (("mail.ru", "vk.company"), "Mail.ru"),
    (("avito.ru",), "Avito"),
    (("ozon.ru",), "Ozon"),
    (("wildberries.ru", "wb.ru"), "Wildberries"),
    (("discord.com", "discord.gg", "discordapp.com", "discord.media"), "Discord"),
    (("steampowered.com", "steamcontent.com", "steamstatic.com", "steamserver.net"), "Steam"),
    (("tiktok.com", "tiktokcdn.com", "byteoversea.com"), "TikTok"),
    (("whatsapp.com", "whatsapp.net"), "WhatsApp"),
    (("netflix.com", "nflxvideo.net", "nflximg.net"), "Netflix"),
    (("anydesk.com",), "AnyDesk"),
)

SITE_CATEGORY_RULES = (
    ("Порносайты", ("pornhub.com", "phncdn.com", "pornhubpremium.com", "xvideos.com", "xvideos-cdn.com", "xnxx.com", "redtube.com", "youporn.com", "youpornpremium.com", "tube8.com", "spankbang.com", "xhamster.com", "xhamsterlive.com", "xhamster.desi", "xhamsterlive.desi", "onlyfans.com", "fansly.com", "manyvids.com", "chaturbate.com", "bongacams.com", "stripchat.com", "livejasmin.com", "cam4.com", "brazzers.com", "brazzersnetwork.com", "realitykings.com", "mofos.com", "digitalplayground.com", "bangbros.com", "naughtyamerica.com", "teamskeet.com", "tushy.com", "blacked.com", "vixen.com", "deeper.com", "adulttime.com", "fakehub.com", "faphouse.com", "erome.com", "eporner.com", "porntrex.com", "drtuber.com", "tnaflix.com", "nuvid.com", "beeg.com", "sex.com", "redgifs.com", "rule34.", "nhentai.", "hentaihaven.", "hanime.", "e621.net", "f95zone.to")),
    ("Соцсети", ("instagram.com", "facebook.com", "fbcdn.net", "vk.com", "vk.ru", "userapi.com", "vk-portal.net", "ok.ru", "okcdn.ru", "tiktok.com", "tiktokcdn.com", "twitter.com", "x.com", "snapchat.com", "livejournal.com", "disqus.com", "licdn.com")),
    ("Мессенджеры", ("telegram.org", "t.me", "whatsapp.com", "whatsapp.net", "discord.com", "discord.gg", "discordapp.com", "discord.media", "viber.com", "signal.org", "skype.com", "wechat.com", "imoim.net", "letsmessagenow.com")),
    ("Видео", ("youtube.com", "googlevideo.com", "ytimg.com", "youtubei.googleapis.com", "netflix.com", "nflxvideo.net", "nflximg.net", "twitch.tv", "ttvnw.net", "rutube.ru", "kinopoisk.ru", "ivi.ru", "okko.tv", "premier.one", "wink.ru", "more.tv", "amediateka.ru", "vimeo.com", "dailymotion.com", "vkvideo.ru")),
    ("Музыка", ("spotify.com", "scdn.co", "music.apple.com", "music.yandex.ru", "soundcloud.com", "deezer.com", "tidal.com", "zvuk.com", "music.youtube.com", "bandcamp.com")),
    ("AI", ("chatgpt.com", "openai.com", "claude.ai", "anthropic.com", "gemini.google.com", "copilot.microsoft.com", "deepseek.com", "perplexity.ai", "poe.com", "grok.com")),
    ("Поиск", ("google.com", "www.google.", "yandex.ru", "yandex.net", "yastatic.net", "ya.ru", "bing.com", "duckduckgo.com", "search.brave.com", "rambler.ru")),
    ("Почта", ("gmail.com", "googlemail.com", "mail.google.com", "mail.ru", "e.mail.ru", "list.ru", "bk.ru", "inbox.ru", "mail.yandex.ru", "imap.mail.me.com", "outlook.live.com", "hotmail.com", "live.com", "proton.me", "protonmail.com")),
    ("Покупки", ("ozon.ru", "wildberries.ru", "wb.ru", "avito.ru", "aliexpress.com", "aliexpress.ru", "alicdn.com", "aestatic.net", "amazon.", "market.yandex.ru", "megamarket.ru", "lamoda.ru", "kazanexpress.ru", "joom.com", "etsy.com", "ebay.com", "chipdip.ru", "kickstarter.com")),
    ("Финансы", ("sberbank.ru", "sber.ru", "tbank.ru", "tinkoff.ru", "yoomoney.ru", "qiwi.com", "alfabank.ru", "vtb.ru", "gazprombank.ru", "raiffeisen.ru", "binance.com", "bybit.com", "okx.com", "tonkeeper.com")),
    ("Игры", ("steampowered.com", "steamcontent.com", "steamstatic.com", "steamserver.net", "epicgames.com", "riotgames.com", "roblox.com", "battle.net", "blizzard.com", "minecraft.net", "ea.com", "playstation.net", "xboxlive.com", "unity3d.com")),
    ("Работа", ("github.com", "gitlab.com", "notion.so", "bitrix24.ru", "slack.com", "zoom.us", "microsoft.com", "microsoftpersonalcontent.com", "office.com", "sharepoint.com", "onedrive.live.com", "teams.microsoft.com", "atlassian.com", "jira.", "trello.com", "figma.com", "miro.com", "linkedin.com", "hh.ru", "adobe.com", "acrobat.com")),
    ("Разработка", ("habr.com", "habrastorage.org", "stackoverflow.com", "stackexchange.com", "npmjs.com", "unpkg.com", "jsdelivr.net", "pypi.org", "python.org", "nodejs.org", "docker.com", "eclipse.org", "jetbrains.com", "digitalocean.com", "vercel.app", "netlify.app", "supabase.co", "analog.com", "wordpress.com", "kzhi.tech", "twibright.com", "arm.com", "caxapa.ru", "hackerearth.com")),
    ("Облака и файлы", ("icloud.com", "drive.google.com", "docs.google.com", "dropbox.com", "box.com", "mega.nz", "disk.yandex.ru", "cloud.mail.ru", "wetransfer.com", "mediafire.com", "airdrop.", "airdroid.com")),
    ("Новости", ("ria.ru", "rbc.ru", "lenta.ru", "meduza.io", "tass.ru", "kommersant.ru", "vedomosti.ru", "bbc.", "cnn.com", "reuters.com", "theguardian.com", "msn.com")),
    ("Образование", ("coursera.org", "udemy.com", "stepik.org", "duolingo.com", "wikipedia.org", "wikimedia.org", "khanacademy.org", "skillbox.ru", "geekbrains.ru")),
    ("Карты и такси", ("maps.google.", "2gis.", "yandexnavi.", "taxi.yandex.", "uber.com", "city-mobil.ru", "gett.com")),
    ("Еда", ("burgerking.ru", "burgerking.com", "burger-king", "bk.com", "vkusnoitochka.ru", "vkusno-i-tochka.ru", "mcdonalds.com", "mcdonalds.ru", "kfc.ru", "kfc.com", "rostics.ru", "dodopizza.ru", "dodopizza.com", "dominos.com", "dominospizza.ru", "pizzahut.com", "papajohns.ru", "eda.yandex.", "yandexeda", "delivery-club.ru", "deliveryclub.ru", "samokat.ru", "lavka.yandex.", "sbermarket.ru", "kuper.ru", "doordash.com", "ubereats.com", "glovoapp.com", "wolt.com", "sushivesla.ru", "tanuki.ru", "yakitoriya.ru", "teremok.ru", "kroshka-kartoshka.ru", "cofix.ru", "coffeemania.ru", "shokoladnitsa.ru", "starbucks.com", "dostaevsky.ru", "vkusvill.ru", "azbukavkusa.ru", "perekrestok.ru", "pyaterochka.ru", "lenta.com", "magnit.ru")),
    ("Путешествия", ("booking.com", "airbnb.", "aviasales.ru", "tutu.ru", "ostrovok.ru", "aeroflot.ru", "s7.ru", "rzd.ru", "tripadvisor.")),
    ("Знакомства", ("tinder.com", "gotinder.com", "tindersparks.com", "bumble.com", "bumbcdn.com", "badoo.com", "badoocdn.com", "mamba.ru", "love.mail.ru", "loveplanet.ru", "pure.app", "grindr.com", "grindr.io", "hornet.com", "hornetapp.com", "scruff.com", "taimi.com", "planetromeo.com", "romeo.com", "adam4adam.com", "gaydar.net", "jackd.com", "okcupid.com", "match.com", "hinge.co", "happn.com", "feeld.co", "her.app", "wapa.app", "wapoapp.com", "zoosk.com", "lovoo.com", "pof.com", "meetme.com", "coffeemeetsbagel.com", "hily.com", "hilyapp.com", "raya.app", "theleague.com", "chispa-app.com", "blk-app.com", "muzz.com", "jdate.com", "eharmony.com", "silversingles.com", "ourtime.com", "growlrapp.com")),
    ("Безопасность", ("avast.com", "avg.com", "avira.com", "avcdn.net", "kaspersky.", "eset.com", "bitdefender.com", "malwarebytes.com", "mcafee.com", "norton.com", "drweb.", "pki.goog", "lencr.org", "digicert.com", "sectigo.com", "letsencrypt.org", "actalis.it")),
    ("Трекеры", ("skcrtxr.com", "protechts.net", "apptracer.ru", "nel.goog")),
    ("CDN и инфраструктура", ("cloudflare.com", "cloudfront.net", "akamaized.net", "akamaihd.net", "fastly.net", "fastly-edge.com", "cdn77.org", "edgesuite.net", "edgekey.net", "azureedge.net", "googleusercontent.com", "gstatic.com", "ggpht.com", "googleapis.com", "appspot.com", "firebaseio.com", "firebaseapp.com", "amazonaws.com", "ampproject.org", "bt.co", "azure.com")),
    ("Windows и Microsoft", ("windows.com", "windowsupdate.com", "msftauth.net", "msftauthimages.net", "msftconnecttest.com", "msftncsi.com")),
    ("Устройства и софт", ("sonymobile.com", "samsung.com", "xiaomi.com", "huawei.com", "miui.com")),
    ("Торренты", ("rainberrytv.com",)),
    ("Apple и iCloud", ("apple.com", "icloud.com", "mzstatic.com", "itunes.apple.com", "aaplimg.com", "cdn-apple.com")),
    ("Android и Google Play", ("android.com", "android.clients.google.com", "play.googleapis.com", "gvt1.com", "gvt2.com", "gvt3.com")),
)
SITE_CATEGORY_SAFE_OVERRIDES = (
    ("Мессенджеры", ("discord.com", "discord.gg", "discordapp.com", "discord.media")),
)

DEVICE_SIGNAL_RULES = (
    ("Apple", "Apple-устройство", ("gdmf.apple.com", "mesu.apple.com", "ocsp.apple.com", "ocsp2.apple.com", "push.apple.com", "icloud.com", "itunes.apple.com", "mzstatic.com", "captive.apple.com")),
    ("Samsung", "Samsung", ("samsung.com", "samsungcloud.com", "samsungapps.com", "samsungknox.com", "ospserver.net")),
    ("Xiaomi", "Xiaomi", ("miui.com", "xiaomi.com", "xiaomi.net", "mi.com", "market.xiaomi.com", "tracking.miui.com")),
    ("Huawei", "Huawei / Honor", ("huawei.com", "hicloud.com", "dbankcdn.com", "huaweicloud.com", "honor.cn", "honor.com")),
    ("Android", "Android", ("android.clients.google.com", "connectivitycheck.gstatic.com", "gvt1.com", "gvt2.com", "play.googleapis.com", "googleapis.com")),
    ("Windows", "Windows", ("msftconnecttest.com", "msftncsi.com", "windowsupdate.com", "microsoft.com", "office.com")),
)
MOBILE_ISP_PARTS = (
    "mobile",
    "cellular",
    "wireless",
    "lte",
    "4g",
    "5g",
    "мобиль",
    "сотов",
    "mts",
    "мтс",
    "megafon",
    "mega fon",
    "мегафон",
    "beeline",
    "vimpelcom",
    "veon",
    "билайн",
    "tele2",
    "t2 mobile",
    "t2 russia",
    "yota",
    "skartel",
    "йота",
    "motiv",
    "мотив",
    "letai",
    "летай",
    "win mobile",
    "volna mobile",
    "волна",
    "sbermobile",
    "sber mobile",
    "tinkoff mobile",
    "t-mobile",
)
HOME_ISP_PARTS = (
    "rostelecom",
    "ростелеком",
    "er-telecom",
    "dom.ru",
    "дом.ру",
    "mgts",
    "мгтс",
    "mts home",
    "beeline broadband",
    "ttk",
    "trans-telecom",
    "akado",
    "акадо",
    "sevensky",
    "netbynet",
    "wifire",
)

SYSTEM_SITE_EXACT = {
    "1.1.1.1",
    "1.0.0.1",
    "8.8.8.8",
    "8.8.4.4",
    "one.one.one.one",
    "dns.quad9.net",
    "dns.adguard-dns.com",
    "localhost",
    "127.0.0.1",
    "::1",
}
SYSTEM_SITE_PARTS = (
    "kingline.online",
    "cloudflare-dns.com",
    "security.cloudflare-dns.com",
    "family.cloudflare-dns.com",
    "dns.google",
    "connectivitycheck.gstatic.com",
    "captive.apple.com",
    "msftconnecttest.com",
    "msftncsi.com",
    "pool.ntp.org",
    "ocsp.",
    "crl.",
    "gdmf.apple.com",
    "mesu.apple.com",
    "push.apple.com",
    "app-analytics-services.com",
    "metrics.icloud.com",
    "xp.apple.com",
    "gspe1-ssl.ls.apple.com",
    "safebrowsing.googleapis.com",
)
REGISTRABLE_THIRD_LEVEL_SUFFIXES = {"co", "com", "net", "org", "ac", "gov", "edu"}


def read_tail_text(path, max_bytes):
    with open(path, "rb") as file:
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(max(0, size - max_bytes), os.SEEK_SET)
        if size > max_bytes:
            file.readline()
        return file.read().decode("utf-8", errors="ignore")


def parse_xui_access_time(value):
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z") and "T" in raw:
        return parse_iso(raw)
    for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def compact_event_from_json(line):
    try:
        payload = json.loads(line)
    except Exception:
        return None
    username = normalize_xui_online_username(payload.get("u") or payload.get("email") or payload.get("username"))
    if is_system_xui_username(username):
        return None
    seen_at = parse_xui_access_time(payload.get("t") or payload.get("ts"))
    target = str(payload.get("d") or payload.get("domain") or payload.get("target") or "").strip().strip("[]")
    source_ip = str(payload.get("ip") or payload.get("sourceIp") or "").strip().strip("[]")
    if not seen_at or not username or not target:
        return None
    return {
        "server": str(payload.get("s") or payload.get("server") or "").strip(),
        "username": username,
        "ip": source_ip,
        "domain": decode_domain(target),
        "port": int_from_any(payload.get("p") or payload.get("port")),
        "count": max(1, int_from_any(payload.get("c") or payload.get("count"), 1)),
        "seenAt": seen_at,
    }


def raw_event_from_line(line):
    match = XUI_ACCEPTED_LOG_RE.search(line)
    if not match:
        return None
    username = normalize_xui_online_username(match.group("email"))
    if is_system_xui_username(username):
        return None
    seen_at = parse_xui_access_time(match.group("ts"))
    target = (match.group("target") or "").strip().strip("[]")
    source_ip = (match.group("src") or "").strip().strip("[]")
    if not seen_at or not username or not target:
        return None
    return {
        "server": (match.group("panel") or "").strip(),
        "username": username,
        "ip": source_ip,
        "domain": decode_domain(target),
        "port": int_from_any(match.group("port")),
        "count": 1,
        "seenAt": seen_at,
    }


def parse_xui_log_event_line(line):
    raw = str(line or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        return compact_event_from_json(raw)
    return raw_event_from_line(raw)


@lru_cache(maxsize=250_000)
def decode_domain(host):
    labels = []
    for label in str(host or "").strip(".").lower().split("."):
        if not label:
            continue
        try:
            labels.append(label.encode("ascii").decode("idna"))
        except Exception:
            labels.append(label)
    return ".".join(labels)


@lru_cache(maxsize=250_000)
def is_ip_host(host):
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def is_system_site(host, proto, port):
    normalized = decode_domain(host).strip("[]")
    if not normalized or normalized in SYSTEM_SITE_EXACT:
        return True
    if proto.lower() == "udp" and str(port or "") == "53":
        return True
    if is_ip_host(normalized):
        return True
    return any(part in normalized for part in SYSTEM_SITE_PARTS)


@lru_cache(maxsize=250_000)
def registrable_domain(host):
    labels = [part for part in decode_domain(host).split(".") if part]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in REGISTRABLE_THIRD_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@lru_cache(maxsize=250_000)
def site_display_name(host):
    normalized = decode_domain(host)
    for suffixes, display in SITE_DISPLAY_RULES:
        if any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in suffixes):
            return display
    domain = registrable_domain(normalized)
    if not domain:
        return ""
    base = domain.split(".", 1)[0]
    if not base:
        return domain
    return base.replace("-", " ").replace("_", " ").title()


def load_domain_list_file(path):
    domains = set()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as file:
            for line in file:
                raw = line.strip().lower()
                if not raw or raw.startswith("#"):
                    continue
                parts = raw.split()
                candidate = parts[-1] if parts else raw
                candidate = candidate.strip().strip(".")
                if not candidate or candidate in {"0.0.0.0", "127.0.0.1", "::1", "localhost"}:
                    continue
                if candidate.startswith("||"):
                    candidate = candidate[2:]
                candidate = candidate.lstrip(".").split("^", 1)[0].split("/", 1)[0].strip(".")
                if "." in candidate and not is_ip_host(candidate):
                    domains.add(decode_domain(candidate))
    except FileNotFoundError:
        logger.warning("Domain category file is missing: %s", path)
    except Exception:
        logger.exception("Unable to load domain category file %s", path)
    return domains


def domain_category_source_paths():
    paths = []
    for _category, category_paths in DOMAIN_CATEGORY_SOURCE_FILES:
        paths.extend(category_paths)
    return paths


def domain_category_cache_is_fresh():
    try:
        cache_mtime = os.path.getmtime(DOMAIN_CATEGORY_CACHE_PATH)
    except OSError:
        return False
    for path in domain_category_source_paths():
        try:
            if os.path.getmtime(path) > cache_mtime:
                return False
        except OSError:
            return False
    return True


def load_domain_category_cache():
    if not domain_category_cache_is_fresh():
        return None
    try:
        with open(DOMAIN_CATEGORY_CACHE_PATH, "rb") as file:
            payload = pickle.load(file)
        if isinstance(payload, dict) and all(isinstance(value, set) for value in payload.values()):
            return payload
    except Exception:
        logger.info("Domain category cache is not usable: %s", DOMAIN_CATEGORY_CACHE_PATH, exc_info=True)
    return None


def save_domain_category_cache(category_sets):
    try:
        tmp_path = f"{DOMAIN_CATEGORY_CACHE_PATH}.tmp"
        with open(tmp_path, "wb") as file:
            pickle.dump(category_sets, file, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp_path, DOMAIN_CATEGORY_CACHE_PATH)
    except Exception:
        logger.info("Unable to save domain category cache: %s", DOMAIN_CATEGORY_CACHE_PATH, exc_info=True)


def load_domain_category_sources():
    global DOMAIN_CATEGORY_SETS, ADULT_DOMAIN_SET
    if DOMAIN_CATEGORY_SETS is not None:
        return DOMAIN_CATEGORY_SETS
    with DOMAIN_CATEGORY_LOCK:
        if DOMAIN_CATEGORY_SETS is not None:
            return DOMAIN_CATEGORY_SETS
        cached = load_domain_category_cache()
        if cached is not None:
            ADULT_DOMAIN_SET = cached.get("Порносайты", set())
            DOMAIN_CATEGORY_SETS = cached
            logger.info(
                "Loaded domain category cache: %s",
                {category: len(domains) for category, domains in cached.items()},
            )
            return DOMAIN_CATEGORY_SETS
        category_sets = {}
        for category, paths in DOMAIN_CATEGORY_SOURCE_FILES:
            merged = set()
            for path in paths:
                merged.update(load_domain_list_file(path))
            if merged:
                category_sets[category] = merged
        ADULT_DOMAIN_SET = category_sets.get("Порносайты", set())
        DOMAIN_CATEGORY_SETS = category_sets
        logger.info(
            "Loaded domain category sources: %s",
            {category: len(domains) for category, domains in category_sets.items()},
        )
        save_domain_category_cache(category_sets)
        return DOMAIN_CATEGORY_SETS


def preload_domain_category_sources():
    try:
        load_domain_category_sources()
        warm_local_domain_ai()
    except Exception:
        logger.exception("Domain category preload failed")


def start_domain_category_preload_thread():
    global DOMAIN_CATEGORY_PRELOAD_STARTED
    if DOMAIN_CATEGORY_PRELOAD_STARTED:
        return
    DOMAIN_CATEGORY_PRELOAD_STARTED = True
    thread = threading.Thread(
        target=preload_domain_category_sources,
        daemon=True,
        name="kingline-domain-category-preload",
    )
    thread.start()


def load_adult_domains():
    global ADULT_DOMAIN_SET
    if ADULT_DOMAIN_SET is not None:
        return ADULT_DOMAIN_SET
    if DOMAIN_CATEGORY_SETS is not None:
        ADULT_DOMAIN_SET = DOMAIN_CATEGORY_SETS.get("Порносайты", set())
        return ADULT_DOMAIN_SET
    with ADULT_DOMAIN_LOCK:
        if ADULT_DOMAIN_SET is not None:
            return ADULT_DOMAIN_SET
        cached = load_domain_category_cache()
        if cached is not None:
            ADULT_DOMAIN_SET = cached.get("Порносайты", set())
            return ADULT_DOMAIN_SET
        domains = set()
        domains.update(load_domain_list_file(ADULT_DOMAINS_PATH))
        ADULT_DOMAIN_SET = domains
        logger.info("Loaded %s adult domains from %s", len(domains), ADULT_DOMAINS_PATH)
        return ADULT_DOMAIN_SET


@lru_cache(maxsize=250_000)
def is_adult_domain(host):
    normalized = decode_domain(host).strip(".")
    if not normalized:
        return False
    domains = load_adult_domains()
    labels = normalized.split(".")
    for index in range(len(labels)):
        candidate = ".".join(labels[index:])
        if candidate in domains:
            return True
    return False


@lru_cache(maxsize=250_000)
def category_from_domain_sources(host):
    normalized = decode_domain(host).strip(".")
    if not normalized:
        return ""
    labels = normalized.split(".")
    candidates = [".".join(labels[index:]) for index in range(len(labels))]
    for category, _paths in DOMAIN_CATEGORY_SOURCE_FILES:
        domains = load_domain_category_sources().get(category) or set()
        if any(candidate in domains for candidate in candidates):
            return category
    return ""


@lru_cache(maxsize=250_000)
def site_category(host):
    normalized = decode_domain(host)
    for category, suffixes in SITE_CATEGORY_SAFE_OVERRIDES:
        if any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in suffixes):
            return category
    if is_adult_domain(normalized):
        return "Порносайты"
    for category, suffixes in SITE_CATEGORY_RULES:
        if any(normalized == suffix or normalized.endswith(f".{suffix}") or suffix in normalized for suffix in suffixes):
            return category
    keyword_category, confidence, _signal = classify_domain_by_keywords(normalized)
    if keyword_category and confidence >= 90:
        return keyword_category
    source_category = category_from_domain_sources(normalized)
    if source_category:
        return source_category
    ai_category, ai_confidence, _ai_signal = classify_domain_with_local_ai(normalized)
    if ai_category and ai_confidence >= 62:
        return ai_category
    return "Другое"


def is_user_site_event(event):
    domain = event.get("domain") if isinstance(event, dict) else ""
    if not domain:
        return False
    return not is_system_site(domain, "tcp", event.get("port"))


def network_kind_from_geo(geo):
    text = " ".join(
        str(geo.get(key) or "")
        for key in ("isp", "org", "asn")
        if isinstance(geo, dict)
    ).lower()
    if not text:
        return "unknown"
    # Fixed divisions must precede mixed operators such as MTS / Beeline.
    if any(part in text for part in HOME_ISP_PARTS):
        return "home"
    if any(part in text for part in MOBILE_ISP_PARTS):
        return "mobile"
    return "unknown"


def device_label_for_family(family, network_kind="unknown"):
    if family == "Apple":
        if network_kind == "mobile":
            return "iPhone / iPad"
        return "iPhone / iPad / Mac"
    return next((rule_label for rule_family, rule_label, _ in DEVICE_SIGNAL_RULES if rule_family == family), family)


def detect_device_profile(events, network_geos=None):
    scores = {}
    signals = {}
    for event in events:
        domain = decode_domain(event.get("domain"))
        if not domain:
            continue
        for family, label, suffixes in DEVICE_SIGNAL_RULES:
            if any(domain == suffix or domain.endswith(f".{suffix}") for suffix in suffixes):
                scores[family] = scores.get(family, 0) + int(event.get("count") or 1)
                signals.setdefault(family, set()).add(site_display_name(domain) or domain)
    network_kinds = [network_kind_from_geo(geo) for geo in (network_geos or []) if isinstance(geo, dict)]
    has_mobile_network = "mobile" in network_kinds
    if not scores:
        if has_mobile_network:
            return {
                "family": "mobile",
                "label": "Смартфон",
                "confidence": 45,
                "signals": ["мобильный оператор"],
                "devices": [
                    {
                        "family": "mobile",
                        "label": "Смартфон",
                        "confidence": 45,
                        "signals": ["мобильный оператор"],
                    }
                ],
            }
        return {
            "family": "unknown",
            "label": "Не определено",
            "confidence": 0,
            "signals": [],
            "devices": [],
        }
    total = max(1, sum(scores.values()))
    top_score = max(scores.values())
    vendor_android_present = any(family in scores for family in ("Samsung", "Xiaomi", "Huawei"))
    devices = []
    for family, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        if family == "Android" and vendor_android_present and score < top_score * 0.72:
            continue
        if score < max(2, top_score * 0.18) and family != max(scores, key=scores.get):
            continue
        confidence = min(98, max(36, int(score * 100 / total)))
        if has_mobile_network and family in {"Apple", "Samsung", "Xiaomi", "Huawei", "Android"}:
            confidence = min(98, confidence + 8)
        devices.append(
            {
                "family": family,
                "label": device_label_for_family(family, "mobile" if has_mobile_network else "unknown"),
                "confidence": confidence,
                "signals": sorted(signals.get(family, []))[:8],
            }
        )
    if has_mobile_network and not any(device["family"] in {"Apple", "Samsung", "Xiaomi", "Huawei", "Android", "mobile"} for device in devices):
        devices.append(
            {
                "family": "mobile",
                "label": "Смартфон",
                "confidence": 45,
                "signals": ["мобильный оператор"],
            }
        )
    if not devices:
        family = max(scores, key=scores.get)
        devices.append(
            {
                "family": family,
                "label": device_label_for_family(family, "mobile" if has_mobile_network else "unknown"),
                "confidence": min(98, max(35, int(scores[family] * 100 / total))),
                "signals": sorted(signals.get(family, []))[:8],
            }
        )
    primary = devices[0]
    return {
        "family": primary["family"],
        "label": primary["label"],
        "confidence": primary["confidence"],
        "signals": primary["signals"],
        "devices": devices[:5],
    }


def cached_geo_for_ip(ip):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT country, city, isp, asn, org, geo_updated_at
            FROM web_xui_ip_history
            WHERE ip = ?
              AND geo_updated_at IS NOT NULL
            ORDER BY geo_updated_at DESC
            LIMIT 1
            """,
            (ip,),
        ).fetchone()
    if not row:
        return None
    updated = parse_iso(row["geo_updated_at"])
    ttl = 30 * 86400 if row['isp'] or row['org'] else 600
    if not updated or (utc_now() - updated).total_seconds() > ttl:
        return None
    return {
        "country": row["country"] or "",
        "city": row["city"] or "",
        "isp": row["isp"] or "",
        "asn": row["asn"] or "",
        "org": row["org"] or "",
        "updatedAt": row["geo_updated_at"],
    }


def fetch_geo_for_ip(ip):
    try:
        parsed_ip = ipaddress.ip_address(str(ip or "").strip())
        if parsed_ip.is_private or parsed_ip.is_loopback or parsed_ip.is_reserved:
            return {"country": "", "city": "", "isp": "", "asn": "", "org": ""}
    except ValueError:
        return {"country": "", "city": "", "isp": "", "asn": "", "org": ""}
    if not GEOIP_LOOKUP_URL:
        return {"country": "", "city": "", "isp": "", "asn": "", "org": ""}
    try:
        url = GEOIP_LOOKUP_URL.format(ip=quote(str(ip), safe=""))
        request = urllib.request.Request(url, headers={"User-Agent": "KinglineProxy/1.0"})
        with urllib.request.urlopen(request, timeout=GEOIP_LOOKUP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(262144).decode("utf-8", errors="replace"))
        connection = payload.get("connection") if isinstance(payload.get("connection"), dict) else {}
        result = {
            "country": payload.get("country") or payload.get("country_name") or "",
            "city": payload.get("city") or "",
            "isp": connection.get("isp") or payload.get("isp") or payload.get("org") or "",
            "asn": str(connection.get("asn") or payload.get("asn") or ""),
            "org": connection.get("org") or payload.get("org") or "",
        }
        if payload.get('success') is not False and result['isp']:
            return result
    except Exception:
        logger.info("Primary GeoIP lookup unavailable")
    # Authoritative ASN fallback, without guessing a city or access technology.
    try:
        def ripe(endpoint, resource):
            request=urllib.request.Request('https://stat.ripe.net/data/'+endpoint+'/data.json?resource='+quote(str(resource),safe=''), headers={'User-Agent':'Kingline/1.0'})
            with urllib.request.urlopen(request,timeout=GEOIP_LOOKUP_TIMEOUT_SECONDS) as response:
                return json.loads(response.read(262144))['data']
        network=ripe('network-info',ip);asn=network.get('asns',[None])[0]
        holder=ripe('as-overview','AS'+str(asn)).get('holder','') if asn else ''
        return {'country':'','city':'','isp':holder,'asn':str(asn or ''),'org':holder}
    except Exception:
        logger.info('ASN lookup unavailable')
        return {"country": "", "city": "", "isp": "", "asn": "", "org": ""}


def store_geo_for_user_ip(username, ip, geo):
    if not username or not ip:
        return
    now_iso = iso_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE web_xui_ip_history
            SET country = ?,
                city = ?,
                isp = ?,
                asn = ?,
                org = ?,
                geo_updated_at = ?
            WHERE username = ? AND ip = ?
            """,
            (
                geo.get("country", ""),
                geo.get("city", ""),
                geo.get("isp", ""),
                geo.get("asn", ""),
                geo.get("org", ""),
                now_iso,
                username,
                ip,
            ),
        )
        conn.commit()
    # Invalidate both derived caches after enrichment, otherwise old blank
    # network data outlives a successful lookup.
    with connect() as conn:
        conn.execute('DELETE FROM web_xui_user_insights_cache WHERE username=?',(username,))
        conn.commit()
    with XUI_USER_INSIGHTS_CACHE_LOCK:
        XUI_USER_INSIGHTS_CACHE.pop(username,None)


def geo_for_user_ip(username, ip):
    cached = cached_geo_for_ip(ip)
    if cached:
        return cached
    geo = fetch_geo_for_ip(ip)
    store_geo_for_user_ip(username, ip, geo)
    return {**geo, "updatedAt": iso_now()}


def iter_xui_log_events(username=None):
    selected = normalize_xui_online_username(username)
    try:
        with open(XUI_ALL_ACCESS_LOG_PATH, "r", encoding="utf-8", errors="ignore") as file:
            for line in file:
                event = parse_xui_log_event_line(line)
                if not event:
                    continue
                if selected and event["username"] != selected:
                    continue
                yield event
    except FileNotFoundError:
        return


def upsert_xui_daily_counts(conn, rows):
    daily = {}
    for row in rows:
        seen_at, username, _ip, _server, _domain, _port, count = row
        if not username or is_system_xui_username(username):
            continue
        day = str(seen_at or "")[:10]
        if not day:
            continue
        key = (username, day)
        daily[key] = daily.get(key, 0) + int(count or 1)
    if not daily:
        return
    conn.executemany(
        """
        INSERT INTO web_xui_access_daily_counts (username, day, request_count)
        VALUES (?, ?, ?)
        ON CONFLICT(username, day) DO UPDATE SET
            request_count = request_count + excluded.request_count
        """,
        [(username, day, total) for (username, day), total in daily.items()],
    )


def sync_xui_access_events(max_bytes=None, block=True):
    acquired = XUI_ACCESS_IMPORT_LOCK.acquire(blocking=block)
    if not acquired:
        return 0
    try:
        try:
            stat = os.stat(XUI_ALL_ACCESS_LOG_PATH)
        except FileNotFoundError:
            return 0
        with connect() as conn:
            row = conn.execute(
                """
                SELECT byte_offset, file_size
                FROM web_xui_access_import_state
                WHERE path = ?
                """,
                (XUI_ALL_ACCESS_LOG_PATH,),
            ).fetchone()
            offset = int(row["byte_offset"] or 0) if row else 0
            if offset > stat.st_size:
                offset = 0
            if offset == 0:
                conn.execute("DELETE FROM web_xui_access_events")
                conn.execute("DELETE FROM web_xui_access_daily_counts")
                conn.execute("DELETE FROM web_xui_user_insights_cache")
                conn.commit()

        limit_until = stat.st_size if max_bytes is None else min(stat.st_size, offset + max_bytes)
        imported = 0
        batch = []
        new_offset = offset
        with connect() as conn:
            with open(XUI_ALL_ACCESS_LOG_PATH, "r", encoding="utf-8", errors="ignore") as file:
                file.seek(offset)
                while file.tell() < limit_until:
                    line = file.readline()
                    if not line:
                        break
                    event = parse_xui_log_event_line(line)
                    if not event:
                        new_offset = file.tell()
                        continue
                    batch.append(
                        (
                            event["seenAt"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                            event["username"],
                            event.get("ip", ""),
                            event.get("server", ""),
                            event["domain"],
                            int(event.get("port") or 0),
                            int(event.get("count") or 1),
                        )
                    )
                    new_offset = file.tell()
                    if len(batch) >= 20000:
                        conn.executemany(
                            """
                            INSERT INTO web_xui_access_events (
                                seen_at, username, ip, server, domain, port, count
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            batch,
                        )
                        upsert_xui_daily_counts(conn, batch)
                        conn.execute(
                            """
                            INSERT INTO web_xui_access_import_state (path, byte_offset, file_size, updated_at)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(path) DO UPDATE SET
                                byte_offset = excluded.byte_offset,
                                file_size = excluded.file_size,
                                updated_at = excluded.updated_at
                            """,
                            (XUI_ALL_ACCESS_LOG_PATH, new_offset, stat.st_size, iso_now()),
                        )
                        conn.commit()
                        imported += len(batch)
                        batch = []
                new_offset = file.tell()
            if batch:
                conn.executemany(
                    """
                    INSERT INTO web_xui_access_events (
                        seen_at, username, ip, server, domain, port, count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch,
                )
                upsert_xui_daily_counts(conn, batch)
                conn.commit()
                imported += len(batch)
            conn.execute(
                """
                INSERT INTO web_xui_access_import_state (path, byte_offset, file_size, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    byte_offset = excluded.byte_offset,
                    file_size = excluded.file_size,
                    updated_at = excluded.updated_at
                """,
                (XUI_ALL_ACCESS_LOG_PATH, new_offset, stat.st_size, iso_now()),
            )
            conn.commit()
        return imported
    finally:
        XUI_ACCESS_IMPORT_LOCK.release()


def xui_access_import_lag_bytes():
    try:
        stat = os.stat(XUI_ALL_ACCESS_LOG_PATH)
    except FileNotFoundError:
        return 0
    with connect() as conn:
        row = conn.execute(
            """
            SELECT byte_offset
            FROM web_xui_access_import_state
            WHERE path = ?
            """,
            (XUI_ALL_ACCESS_LOG_PATH,),
        ).fetchone()
    offset = int(row["byte_offset"] or 0) if row else 0
    if offset > stat.st_size:
        return stat.st_size
    return max(0, stat.st_size - offset)


def xui_access_import_loop():
    time.sleep(3)
    while True:
        try:
            imported = sync_xui_access_events(XUI_ACCESS_IMPORT_CHUNK_BYTES, block=False)
            lag = xui_access_import_lag_bytes()
            if imported >= 100 or lag > 1024 * 1024:
                logger.info(
                    "XUI access import progress imported=%s lag_mb=%.1f",
                    imported,
                    lag / 1024 / 1024,
                )
            time.sleep(XUI_ACCESS_IMPORT_ACTIVE_SECONDS if lag else XUI_ACCESS_IMPORT_IDLE_SECONDS)
        except Exception:
            logger.exception("XUI access import cycle failed")
            time.sleep(5)


def start_xui_access_import_thread():
    global XUI_ACCESS_IMPORT_THREAD_STARTED
    if XUI_ACCESS_IMPORT_THREAD_STARTED:
        return
    XUI_ACCESS_IMPORT_THREAD_STARTED = True
    thread = threading.Thread(
        target=xui_access_import_loop,
        daemon=True,
        name="kingline-xui-access-import",
    )
    thread.start()


def iter_xui_db_events(username):
    username = normalize_xui_online_username(username)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT seen_at, username, ip, server, domain, port, count
            FROM web_xui_access_events
            WHERE username = ?
            ORDER BY seen_at ASC, id ASC
            """,
            (username,),
        ).fetchall()
    for row in rows:
        seen_at = parse_iso(row["seen_at"])
        if not seen_at:
            continue
        yield {
            "seenAt": seen_at,
            "username": row["username"],
            "ip": row["ip"] or "",
            "server": row["server"] or "",
            "domain": row["domain"] or "",
            "port": int(row["port"] or 0),
            "count": int(row["count"] or 1),
        }


@timed_sync("analytics.user_insights", slow_ms=2000)
def aggregate_user_insights(username):
    username = normalize_xui_online_username(username)
    top_sites = {}
    categories = {}
    timeline = []

    with connect() as conn:
        domain_rows = conn.execute(
            """
            SELECT domain,
                   MAX(port) AS port,
                   SUM(count) AS total_count,
                   MAX(seen_at) AS last_seen_at
            FROM web_xui_access_events
            WHERE username = ?
            GROUP BY domain
            """,
            (username,),
        ).fetchall()
        timeline_rows = conn.execute(
            """
            SELECT seen_at, ip, server, domain, port, count
            FROM web_xui_access_events
            WHERE username = ?
            ORDER BY seen_at DESC, id DESC
            LIMIT 3000
            """,
            (username,),
        ).fetchall()
        ip_rows = conn.execute(
            """
            SELECT seen_at, ip, server
            FROM (
                SELECT id,
                       seen_at,
                       ip,
                       server,
                       LAG(ip) OVER (ORDER BY seen_at ASC, id ASC) AS prev_ip
                FROM web_xui_access_events
                WHERE username = ?
                  AND ip IS NOT NULL
                  AND ip != ''
            )
            WHERE prev_ip IS NULL OR ip != prev_ip
            ORDER BY seen_at DESC, id DESC
            LIMIT 80
            """,
            (username,),
        ).fetchall()

    device_events = []
    for row in domain_rows:
        event = {
            "domain": row["domain"] or "",
            "port": int(row["port"] or 0),
            "count": int(row["total_count"] or 0),
        }
        device_events.append(event)
        if not is_user_site_event(event):
            continue
        display = site_display_name(event["domain"])
        if not display:
            continue
        domain = registrable_domain(event["domain"])
        count = int(event.get("count") or 1)
        site_key = display.lower()
        site = top_sites.setdefault(
            site_key,
            {"name": display, "domain": domain, "count": 0, "lastSeenAt": None},
        )
        site["count"] += count
        site["lastSeenAt"] = row["last_seen_at"]
        category_name = site_category(event["domain"])
        category = categories.setdefault(category_name, {"name": category_name, "count": 0, "sites": {}})
        category["count"] += count
        category_site = category["sites"].setdefault(site_key, {"name": display, "domain": domain, "count": 0})
        category_site["count"] += count

    for row in reversed(timeline_rows):
        event = {
            "domain": row["domain"] or "",
            "port": int(row["port"] or 0),
            "count": int(row["count"] or 1),
        }
        if not is_user_site_event(event):
            continue
        display = site_display_name(event["domain"])
        if not display:
            continue
        timeline.append(
            {
                "time": row["seen_at"],
                "ip": row["ip"] or "",
                "server": row["server"] or "",
                "site": display,
                "domain": registrable_domain(event["domain"]),
                "category": site_category(event["domain"]),
                "count": event["count"],
            }
        )

    recent_ip_history = [
        {
            "ip": row["ip"],
            "server": row["server"] or "",
            "changedAt": row["seen_at"],
        }
        for row in reversed(ip_rows)
    ]
    unique_recent_ips = []
    for item in reversed(recent_ip_history):
        ip = item["ip"]
        if ip not in unique_recent_ips:
            unique_recent_ips.append(ip)
        if len(unique_recent_ips) >= 8:
            break
    for item in recent_ip_history[-40:]:
        upsert_xui_ip(username, None, item.get("ip"), item.get("server"), item.get("changedAt"))
    geos = {ip: cached_geo_for_ip(ip) or {} for ip in unique_recent_ips}
    for item in recent_ip_history:
        item["geo"] = geos.get(item["ip"]) or cached_geo_for_ip(item["ip"]) or {}
    current_ip = recent_ip_history[-1]["ip"] if recent_ip_history else ""
    current_geo = cached_geo_for_ip(current_ip) or {} if current_ip else {}
    known_geos = [geo for geo in list(geos.values()) + ([current_geo] if current_geo else []) if geo]
    device = detect_device_profile(device_events, known_geos)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO web_xui_device_profiles (
                username, device_label, device_family, confidence, signals_json, devices_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                device_label = excluded.device_label,
                device_family = excluded.device_family,
                confidence = excluded.confidence,
                signals_json = excluded.signals_json,
                devices_json = excluded.devices_json,
                updated_at = excluded.updated_at
            """,
            (
                username,
                device["label"],
                device["family"],
                device["confidence"],
                json.dumps(device["signals"], ensure_ascii=False),
                json.dumps(device.get("devices") or [], ensure_ascii=False),
                iso_now(),
            ),
        )
        conn.commit()

    category_rows = []
    for category in categories.values():
        sites = sorted(category["sites"].values(), key=lambda item: item["count"], reverse=True)
        category_rows.append(
            {
                "name": category["name"],
                "count": category["count"],
                "sites": sites[:30],
            }
        )

    return {
        "ok": True,
        "username": username,
        "updatedAt": iso_now(),
        "device": device,
        "network": {
            "ip": current_ip,
            "kind": network_kind_from_geo(current_geo),
            **current_geo,
        },
        "ipHistory": list(reversed(recent_ip_history)),
        "topSites": sorted(top_sites.values(), key=lambda item: item["count"], reverse=True)[:30],
        "categories": sorted(category_rows, key=lambda item: item["count"], reverse=True),
        "timeline": timeline[-900:],
        "totalEvents": sum(int(site["count"]) for site in top_sites.values()),
    }


def load_user_insights_cache(username):
    username = normalize_xui_online_username(username)
    if not username:
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT payload_json, updated_at
            FROM web_xui_user_insights_cache
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except Exception:
        return None
    if isinstance(payload, dict):
        payload.setdefault("updatedAt", row["updated_at"])
        return payload
    return None


def store_user_insights_cache(username, payload):
    username = normalize_xui_online_username(username)
    if not username or not isinstance(payload, dict):
        return
    updated_at = payload.get("updatedAt") or iso_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO web_xui_user_insights_cache (username, payload_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (username, json.dumps(payload, ensure_ascii=False), updated_at),
        )
        conn.commit()


def cached_or_aggregate_user_insights(username, force=False):
    username = normalize_xui_online_username(username)
    if not username:
        raise ValueError("invalid_username")
    now = time.monotonic()
    if not force:
        with XUI_USER_INSIGHTS_CACHE_LOCK:
            cached = XUI_USER_INSIGHTS_CACHE.get(username)
            if cached and now - cached["created"] < XUI_USER_INSIGHTS_CACHE_TTL_SECONDS:
                return cached["data"]
        db_cached = load_user_insights_cache(username)
        if db_cached:
            with XUI_USER_INSIGHTS_CACHE_LOCK:
                XUI_USER_INSIGHTS_CACHE[username] = {
                    "created": time.monotonic(),
                    "data": db_cached,
                }
            return db_cached
    data = aggregate_user_insights(username)
    store_user_insights_cache(username, data)
    with XUI_USER_INSIGHTS_CACHE_LOCK:
        XUI_USER_INSIGHTS_CACHE[username] = {"created": time.monotonic(), "data": data}
    return data


def recent_xui_access_usernames(limit=50):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT recent.username,
                   (SELECT MAX(seen_at) FROM web_xui_access_events e
                    WHERE e.username = recent.username) AS last_seen_at
            FROM (
                SELECT DISTINCT username
                FROM web_xui_access_daily_counts
                WHERE day >= ? AND username != ''
            ) recent
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            ((utc_now() - timedelta(days=1)).strftime("%Y-%m-%d"), int(limit)),
        ).fetchall()
    return [
        normalize_xui_online_username(row["username"])
        for row in rows
        if row["username"] and not is_system_xui_username(row["username"])
    ]


def xui_insights_prewarm_loop():
    time.sleep(12)
    while True:
        try:
            if xui_access_import_lag_bytes() > XUI_ACCESS_IMPORT_CHUNK_BYTES:
                time.sleep(min(15, XUI_INSIGHTS_PREWARM_INTERVAL_SECONDS))
                continue
            usernames = recent_xui_access_usernames(60)
            refreshed = 0
            for username in usernames:
                if not username:
                    continue
                cached = load_user_insights_cache(username)
                updated = parse_iso((cached or {}).get("updatedAt"))
                if updated and (utc_now() - updated).total_seconds() < XUI_USER_INSIGHTS_CACHE_TTL_SECONDS:
                    continue
                cached_or_aggregate_user_insights(username, force=True)
                refreshed += 1
                time.sleep(0.03)
            if refreshed:
                logger.info("XUI insights cache refreshed users=%s", refreshed)
        except Exception:
            logger.exception("XUI insights prewarm cycle failed")
        time.sleep(XUI_INSIGHTS_PREWARM_INTERVAL_SECONDS)


def start_xui_insights_prewarm_thread():
    global XUI_INSIGHTS_PREWARM_THREAD_STARTED
    if XUI_INSIGHTS_PREWARM_THREAD_STARTED:
        return
    XUI_INSIGHTS_PREWARM_THREAD_STARTED = True
    thread = threading.Thread(
        target=xui_insights_prewarm_loop,
        daemon=True,
        name="kingline-xui-insights-prewarm",
    )
    thread.start()


def admin_user_insights_payload(query):
    username = normalize_xui_online_username((query.get("username") or [""])[0])
    if not username or is_system_xui_username(username):
        raise ValueError("invalid_username")
    return cached_or_aggregate_user_insights(username)


def admin_user_site_history_payload(query):
    username = normalize_xui_online_username((query.get("username") or [""])[0])
    if not username or is_system_xui_username(username):
        raise ValueError("invalid_username")
    q = str((query.get("q") or [""])[0] or "").strip().lower()
    domain = decode_domain((query.get("domain") or [""])[0]).strip(".").lower()
    category_filter = str((query.get("category") or [""])[0] or "").strip()
    if not q and not domain and not category_filter:
        return {"ok": True, "username": username, "items": [], "sessions": [], "total": 0}
    if category_filter:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT seen_at, ip, server, domain, port, count
                FROM web_xui_access_events
                WHERE username = ?
                ORDER BY seen_at ASC, id ASC
                """,
                (username,),
            ).fetchall()
        all_items = []
        for row in rows:
            event = {
                "domain": row["domain"] or "",
                "port": int(row["port"] or 0),
                "count": int(row["count"] or 1),
            }
            if not is_user_site_event(event) or site_category(event["domain"]) != category_filter:
                continue
            display = site_display_name(event["domain"])
            if not display:
                continue
            all_items.append(
                {
                    "time": row["seen_at"],
                    "ip": row["ip"] or "",
                    "server": row["server"] or "",
                    "site": display,
                    "domain": registrable_domain(event["domain"]),
                    "category": category_filter,
                    "count": event["count"],
                }
            )
        sessions = []
        current = None
        previous_at = None
        gap_seconds = 2 * 60 * 60
        for item in all_items:
            item_at = parse_iso(item["time"])
            should_start = (
                current is None
                or item_at is None
                or previous_at is None
                or (item_at - previous_at).total_seconds() > gap_seconds
            )
            if should_start:
                if current:
                    current["sites"] = sorted(
                        current.pop("_sites").values(),
                        key=lambda site: site["count"],
                        reverse=True,
                    )[:12]
                    sessions.append(current)
                current = {
                    "startAt": item["time"],
                    "endAt": item["time"],
                    "count": 0,
                    "_sites": {},
                }
            current["endAt"] = item["time"]
            current["count"] += int(item.get("count") or 1)
            site = current["_sites"].setdefault(
                item["domain"],
                {"name": item["site"], "domain": item["domain"], "count": 0},
            )
            site["count"] += int(item.get("count") or 1)
            previous_at = item_at
        if current:
            current["sites"] = sorted(
                current.pop("_sites").values(),
                key=lambda site: site["count"],
                reverse=True,
            )[:12]
            sessions.append(current)
        return {
            "ok": True,
            "username": username,
            "query": q,
            "domain": domain,
            "category": category_filter,
            "total": sum(int(item.get("count") or 1) for item in all_items),
            "items": list(reversed(all_items))[:500],
            "sessions": list(reversed(sessions))[:120],
        }

    params = [username]
    where = ["username = ?"]
    if domain:
        where.append("(domain = ? OR domain LIKE ?)")
        params.extend([domain, f"%.{domain}"])
    else:
        safe_q = q.replace("%", "").replace("_", "")
        where.append("LOWER(domain) LIKE ?")
        params.append(f"%{safe_q}%")
    sql_where = " AND ".join(where)
    with connect() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS count FROM web_xui_access_events WHERE {sql_where}",
            params,
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT seen_at, ip, server, domain, port, count
            FROM web_xui_access_events
            WHERE {sql_where}
            ORDER BY seen_at DESC, id DESC
            LIMIT 500
            """,
            params,
        ).fetchall()
    items = []
    for row in rows:
        event = {
            "domain": row["domain"] or "",
            "port": int(row["port"] or 0),
            "count": int(row["count"] or 1),
        }
        if not is_user_site_event(event):
            continue
        display = site_display_name(event["domain"])
        if not display:
            continue
        items.append(
            {
                "time": row["seen_at"],
                "ip": row["ip"] or "",
                "server": row["server"] or "",
                "site": display,
                "domain": registrable_domain(event["domain"]),
                "category": site_category(event["domain"]),
                "count": event["count"],
            }
        )
    return {
        "ok": True,
        "username": username,
        "query": q,
        "domain": domain,
        "total": int(total_row["count"] or 0),
        "items": items,
    }


def parse_xui_live_site_line(line, since, online_usernames):
    event = parse_xui_log_event_line(line)
    if not event:
        return None
    username = event["username"]
    if username not in online_usernames:
        return None
    seen_at = event["seenAt"]
    if not seen_at or seen_at < since:
        return None
    target = event["domain"]
    if is_system_site(target, "tcp", event.get("port")):
        return None
    name = site_display_name(target)
    if not name:
        return None
    return {
        "username": username,
        "name": name,
        "domain": decode_domain(target),
        "seenAt": seen_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": event.get("count", 1),
    }


def xui_live_sites_for_online_users(online_users):
    online_map = {
        user["username"]: user
        for user in (online_users or [])
        if user.get("username") and not is_system_xui_username(user.get("username"))
    }
    if not online_map:
        return []
    now = utc_now()
    since = now - timedelta(seconds=XUI_LIVE_SITE_LOOKBACK_SECONDS)
    grouped = {username: {} for username in online_map}
    try:
        text = read_tail_text(XUI_ALL_ACCESS_LOG_PATH, XUI_LIVE_SITE_TAIL_BYTES)
    except FileNotFoundError:
        return [
            {
                "username": user["username"],
                "name": user.get("name") or user["username"],
                "clientCount": user.get("clientCount", 0),
                "sites": [],
            }
            for user in online_map.values()
        ]
    except Exception:
        logger.exception("Unable to parse XUI access log for live sites")
        return []

    online_usernames = set(online_map)
    for line in reversed(text.splitlines()):
        event = parse_xui_live_site_line(line, since, online_usernames)
        if not event:
            continue
        site_key = event["name"].lower()
        user_sites = grouped.setdefault(event["username"], {})
        if site_key not in user_sites:
            user_sites[site_key] = {
                "name": event["name"],
                "domain": event["domain"],
                "seenAt": event["seenAt"],
            }
        if all(len(sites) >= 6 for sites in grouped.values()):
            break

    rows = []
    for username, user in online_map.items():
        sites = sorted(
            grouped.get(username, {}).values(),
            key=lambda site: site.get("seenAt") or "",
            reverse=True,
        )
        rows.append(
            {
                "username": username,
                "name": user.get("name") or username,
                "clientCount": user.get("clientCount", 0),
                "sites": sites[:6],
            }
        )
    return rows


def admin_live_sites_payload():
    now = time.monotonic()
    with XUI_LIVE_SITE_CACHE_LOCK:
        cached = XUI_LIVE_SITE_CACHE.get("data")
        created = float(XUI_LIVE_SITE_CACHE.get("created") or 0.0)
        if cached and now - created < XUI_LIVE_SITE_CACHE_TTL_SECONDS:
            return cached

    online = admin_online_snapshot()
    payload = {
        "ok": True,
        "updatedAt": iso_now(),
        "count": int(online.get("count") or 0),
        "users": xui_live_sites_for_online_users(online.get("users") or []),
    }
    with XUI_LIVE_SITE_CACHE_LOCK:
        XUI_LIVE_SITE_CACHE["created"] = time.monotonic()
        XUI_LIVE_SITE_CACHE["data"] = payload
    return payload


def admin_request_counts_for_period(start, end, range_key, selected=""):
    selected = normalize_xui_online_username(selected)
    use_daily = range_key in {"week", "month", "year", "all"} or (end - start).total_seconds() > 2 * 86400
    with connect() as conn:
        if use_daily:
            params = [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]
            clause = ""
            if selected:
                clause = " AND username = ?"
                params.append(selected)
            rows = conn.execute(
                f"""
                SELECT username, SUM(request_count) AS request_count
                FROM web_xui_access_daily_counts
                WHERE day >= ?
                  AND day <= ?
                  {clause}
                GROUP BY username
                """,
                params,
            ).fetchall()
        else:
            params = [
                start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ]
            clause = ""
            if selected:
                clause = " AND username = ?"
                params.append(selected)
            rows = conn.execute(
                f"""
                SELECT username, SUM(count) AS request_count
                FROM web_xui_access_events
                WHERE seen_at >= ?
                  AND seen_at <= ?
                  {clause}
                GROUP BY username
                """,
                params,
            ).fetchall()
    return {
        row["username"]: int(row["request_count"] or 0)
        for row in rows
        if row["username"] and not is_system_xui_username(row["username"])
    }


@timed_sync("analytics.report", slow_ms=2000)
def admin_analytics_payload(query):
    cache_key = json.dumps(
        {
            "range": (query.get("range") or ["month"])[0],
            "user": (query.get("user") or [""])[0],
            "start": (query.get("start") or [""])[0],
            "end": (query.get("end") or [""])[0],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    now_monotonic = time.monotonic()
    with XUI_ADMIN_ANALYTICS_CACHE_LOCK:
        cached = XUI_ADMIN_ANALYTICS_CACHE.get(cache_key)
        if cached and now_monotonic - cached["created"] < XUI_ADMIN_ANALYTICS_CACHE_TTL_SECONDS:
            return cached["data"]

    range_key, start, end = analytics_period(query)
    selected = normalize_xui_online_username((query.get("user") or [""])[0])
    if selected in {"", "all"}:
        selected = ""
    buckets, bucket_seconds = analytics_buckets(start, end, range_key)
    period_seconds = max(60, int((end - start).total_seconds()))
    sample_stride_minutes = 0
    if period_seconds > 90 * 86400:
        sample_stride_minutes = 60
    elif period_seconds > 2 * 86400:
        sample_stride_minutes = 30
    sample_interval_seconds = (
        sample_stride_minutes * 60
        if sample_stride_minutes
        else XUI_ANALYTICS_POLL_INTERVAL_SECONDS
    )
    lookback = analytics_sample_lookback(start, bucket_seconds)
    params = [lookback.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")]
    user_clause = ""
    if selected:
        user_clause = " AND username = ?"
        params.append(selected)
    sample_stride_clause = ""
    if sample_stride_minutes == 30:
        sample_stride_clause = " AND substr(sampled_at, 15, 2) IN ('00', '30')"
    elif sample_stride_minutes == 60:
        sample_stride_clause = " AND substr(sampled_at, 15, 2) = '00'"

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                sampled_at,
                username,
                tg_id,
                name,
                up_bytes,
                down_bytes,
                total_bytes,
                online_clients,
                panels_json
            FROM web_xui_user_samples
            WHERE sampled_at >= ?
              AND sampled_at <= ?
              {user_clause}
              {sample_stride_clause}
            ORDER BY username ASC, sampled_at ASC
            """,
            params,
        ).fetchall()
        last_seen_params = []
        last_seen_clause = ""
        if selected:
            last_seen_clause = " AND username = ?"
            last_seen_params.append(selected)
        last_seen_rows = conn.execute(
            f"""
            SELECT username, last_seen_at
            FROM web_xui_user_last_seen
            WHERE 1 = 1
              {last_seen_clause}
            """,
            last_seen_params,
        ).fetchall()
    absolute_last_seen = {
        row["username"]: parse_iso(row["last_seen_at"])
        for row in last_seen_rows
        if row["username"] and row["last_seen_at"]
    }
    request_counts = admin_request_counts_for_period(start, end, range_key, selected)

    grouped = {}
    for row in rows:
        grouped.setdefault(row["username"], []).append(row)

    totals = {"upBytes": 0, "downBytes": 0, "totalBytes": 0, "onlineSeconds": 0}
    users = []
    period_days = max(1 / 24, (end - start).total_seconds() / 86400)
    period_hours = max(1, (end - start).total_seconds() / 3600)

    for username, samples in grouped.items():
        if is_system_xui_username(username):
            continue
        user_up = user_down = user_total = 0
        prev = None
        panels = set()
        name = ""
        tg_id = None
        latest_clients = 0
        latest_sample_at = None
        all_time_total = 0
        for row in samples:
            dt = parse_iso(row["sampled_at"])
            if not dt:
                continue
            panels.update(json_loads_safe(row["panels_json"], []))
            name = row["name"] or name
            tg_id = row["tg_id"] if row["tg_id"] is not None else tg_id
            latest_clients = int(row["online_clients"] or 0)
            latest_sample_at = dt
            all_time_total = int(row["total_bytes"] or 0)
            if prev and dt >= start:
                up_delta = max(0, int(row["up_bytes"] or 0) - int(prev["up_bytes"] or 0))
                down_delta = max(0, int(row["down_bytes"] or 0) - int(prev["down_bytes"] or 0))
                total_delta = up_delta + down_delta
                user_up += up_delta
                user_down += down_delta
                user_total += total_delta
                bucket_index = int((dt - start).total_seconds() // bucket_seconds)
                if 0 <= bucket_index < len(buckets):
                    buckets[bucket_index]["upBytes"] += up_delta
                    buckets[bucket_index]["downBytes"] += down_delta
                    buckets[bucket_index]["totalBytes"] += total_delta
            prev = row

        online_seconds, sessions, period_last_seen = sample_online_sessions(
            samples,
            start,
            end,
            sample_interval_seconds=sample_interval_seconds,
        )
        last_seen = absolute_last_seen.get(username) or period_last_seen
        for begin, finish in sessions:
            cursor = begin
            while cursor < finish:
                bucket_index = int((cursor - start).total_seconds() // bucket_seconds)
                next_edge = start + timedelta(seconds=(bucket_index + 1) * bucket_seconds)
                part_end = min(finish, next_edge)
                if 0 <= bucket_index < len(buckets):
                    buckets[bucket_index]["onlineSeconds"] += max(0, int((part_end - cursor).total_seconds()))
                    buckets[bucket_index]["onlineUsers"].add(username)
                cursor = part_end

        totals["upBytes"] += user_up
        totals["downBytes"] += user_down
        totals["totalBytes"] += user_total
        totals["onlineSeconds"] += online_seconds
        users.append(
            {
                "username": username,
                "tgId": tg_id,
                "name": name or username,
                "panels": sorted(panels),
                "lastSeenAt": last_seen.strftime("%Y-%m-%dT%H:%M:%SZ") if last_seen else None,
                "onlineSeconds": online_seconds,
                "sessions": len(sessions),
                "upBytes": user_up,
                "downBytes": user_down,
                "totalBytes": user_total,
                "allTimeBytes": all_time_total,
                "requestCount": request_counts.get(username, 0),
                "avgBytesPerDay": int(user_total / period_days),
                "avgBytesPerHour": int(user_total / period_hours),
                "clientCount": latest_clients,
                "onlineNow": bool(
                    latest_clients > 0
                    and latest_sample_at
                    and (utc_now() - latest_sample_at).total_seconds()
                    <= XUI_ANALYTICS_SESSION_GAP_SECONDS + XUI_ANALYTICS_POLL_INTERVAL_SECONDS * 2
                ),
            }
        )

    users.sort(key=lambda item: item["totalBytes"], reverse=True)
    series = [
        {
            "label": bucket["label"],
            "upBytes": bucket["upBytes"],
            "downBytes": bucket["downBytes"],
            "totalBytes": bucket["totalBytes"],
            "onlineSeconds": bucket["onlineSeconds"],
            "activeUsers": len(bucket["onlineUsers"]),
        }
        for bucket in buckets
    ]
    payload = {
        "ok": True,
        "range": range_key,
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bucketSeconds": bucket_seconds,
        "selectedUser": selected or "all",
        "totals": {
            **totals,
            "avgBytesPerDay": int(totals["totalBytes"] / period_days),
            "avgBytesPerHour": int(totals["totalBytes"] / period_hours),
        },
        "users": users,
        "series": series,
    }
    with XUI_ADMIN_ANALYTICS_CACHE_LOCK:
        XUI_ADMIN_ANALYTICS_CACHE[cache_key] = {"created": time.monotonic(), "data": payload}
        if len(XUI_ADMIN_ANALYTICS_CACHE) > 80:
            oldest_key = min(
                XUI_ADMIN_ANALYTICS_CACHE,
                key=lambda key: XUI_ADMIN_ANALYTICS_CACHE[key]["created"],
            )
            XUI_ADMIN_ANALYTICS_CACHE.pop(oldest_key, None)
    return payload


def online_session_history(username, limit=1000):
    username = normalize_xui_online_username(username)
    if not username:
        raise ValueError("invalid_user")
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT sampled_at, online_clients, panels_json
            FROM web_xui_user_samples
            WHERE username = ?
            ORDER BY sampled_at ASC
            """,
            (username,),
        ).fetchall()

    sessions = []
    session_start = None
    last_online = None
    max_clients = 0
    panels = set()

    def close_session():
        nonlocal session_start, last_online, max_clients, panels
        if not session_start or not last_online:
            session_start = None
            last_online = None
            max_clients = 0
            panels = set()
            return
        finish = last_online + timedelta(seconds=XUI_ANALYTICS_POLL_INTERVAL_SECONDS)
        duration = max(0, int((finish - session_start).total_seconds()))
        if duration >= XUI_ANALYTICS_POLL_INTERVAL_SECONDS:
            sessions.append(
                {
                    "startedAt": session_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endedAt": finish.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "durationSeconds": duration,
                    "maxClients": max_clients,
                    "panels": sorted(panels),
                }
            )
        session_start = None
        last_online = None
        max_clients = 0
        panels = set()

    for row in rows:
        dt = parse_iso(row["sampled_at"])
        if not dt:
            continue
        online_clients = int(row["online_clients"] or 0)
        if online_clients <= 0:
            continue
        if (
            session_start is not None
            and last_online is not None
            and (dt - last_online).total_seconds() > XUI_ANALYTICS_SESSION_GAP_SECONDS
        ):
            close_session()
        if session_start is None:
            session_start = dt
            max_clients = 0
            panels = set()
        last_online = dt
        max_clients = max(max_clients, online_clients)
        panels.update(json_loads_safe(row["panels_json"], []))
    close_session()
    sessions.sort(key=lambda item: item["startedAt"], reverse=True)
    return {
        "ok": True,
        "username": username,
        "items": sessions[: int(limit or 1000)],
        "total": len(sessions),
    }


def extract_ips_from_payload(payload):
    found = set()
    ip_re = re.compile(r"(?:(?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F:]{3,})")

    def walk(value):
        if isinstance(value, dict):
            for key in ("obj", "data", "ips", "clientIps", "list", "rows"):
                if key in value:
                    walk(value[key])
            for item in value.values():
                if isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        else:
            text = str(value or "")
            for match in ip_re.findall(text):
                if "." in match or ":" in match:
                    found.add(match)

    walk(payload)
    return sorted(found)


async def refresh_xui_ip_history_for(username):
    username = normalize_xui_online_username(username)
    if not username:
        return
    with connect() as conn:
        aliases = conn.execute(
            """
            SELECT username, client_email, tg_id, panel_tag
            FROM web_xui_client_aliases
            WHERE username = ?
            ORDER BY updated_at DESC
            LIMIT 24
            """,
            (username,),
        ).fetchall()
    if not aliases:
        await asyncio.to_thread(store_xui_analytics_snapshot)
        with connect() as conn:
            aliases = conn.execute(
                """
                SELECT username, client_email, tg_id, panel_tag
                FROM web_xui_client_aliases
                WHERE username = ?
                ORDER BY updated_at DESC
                LIMIT 24
                """,
                (username,),
            ).fetchall()

    panels = {p["tag"]: p for p in BillingDB._get_panels()}
    seen_at = iso_now()
    by_panel = {}
    for alias in aliases:
        by_panel.setdefault(alias["panel_tag"], []).append(alias)

    async def refresh_panel(tag, rows):
        panel = panels.get(tag)
        if not panel:
            return
        try:
            async with asyncio.timeout(XUI_ANALYTICS_TIMEOUT_SECONDS), XUIClient(
                panel["url"],
                panel.get("username") or "",
                panel.get("password") or "",
                webbasepath=panel.get("webbasepath") or "",
                timeout=3,
                verify_ssl=panel.get("verify_ssl", True),
                logger_=logger,
            ) as xui:
                for row in rows:
                    email = row["client_email"]
                    endpoint = xui._panel_api_url(f"/api/inbounds/clientIps/{quote(email, safe='')}")
                    status, payload = await xui._get_json(endpoint)
                    if status != 200:
                        status, payload = await xui._post_form(endpoint, {})
                    if status != 200:
                        continue
                    for ip in extract_ips_from_payload(payload):
                        upsert_xui_ip(username, row["tg_id"], ip, tag, seen_at)
        except Exception as exc:
            logger.warning("XUI IP history unavailable tag=%s: %s", tag, type(exc).__name__)

    await asyncio.gather(*(refresh_panel(tag, rows) for tag, rows in by_panel.items()))


def admin_ip_history_payload(query):
    username = normalize_xui_online_username((query.get("username") or [""])[0])
    if not username:
        raise ValueError("invalid_user")
    try:
        asyncio.run(refresh_xui_ip_history_for(username))
    except Exception:
        logger.exception("XUI IP history refresh failed username=%s", username)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT ip, first_seen_at, last_seen_at, seen_count, panels_json
            FROM web_xui_ip_history
            WHERE username = ?
            ORDER BY last_seen_at DESC
            LIMIT 100
            """,
            (username,),
        ).fetchall()
    return {
        "ok": True,
        "username": username,
        "items": [
            {
                "ip": row["ip"],
                "firstSeenAt": row["first_seen_at"],
                "lastSeenAt": row["last_seen_at"],
                "seenCount": int(row["seen_count"] or 0),
                "panels": json_loads_safe(row["panels_json"], []),
            }
            for row in rows
        ],
    }


def admin_list_admins():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                email,
                tg_id,
                is_owner,
                can_analytics,
                can_balance,
                can_message,
                can_block,
                can_promos,
                can_dns,
                can_support,
                can_payments,
                can_broadcast,
                can_delete_users,
                    can_nodes,
                    can_node_credentials,
                    can_technical,
                    can_vpn_config,
                    can_documentation,
                    can_user_transport,
                created_at,
                created_by_account_id
            FROM web_admins
            ORDER BY is_owner DESC, id ASC
            """
        ).fetchall()
    return [public_admin(row) for row in rows]


def admin_list_payments(limit=250):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.payment_id,
                p.tg_id,
                p.amount,
                p.status,
                p.created_at,
                p.chat_id,
                p.message_id,
                p.notified_at,
                u.tg_name,
                u.username,
                wa.email
            FROM payments p
            LEFT JOIN users u ON u.tg_id = p.tg_id
            LEFT JOIN web_accounts wa
              ON wa.linked_tg_id = p.tg_id
              OR wa.web_tg_id = p.tg_id
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "paymentId": row["payment_id"],
            "tgId": row["tg_id"],
            "amount": int(row["amount"] or 0),
            "status": row["status"] or "",
            "createdAt": row["created_at"],
            "notifiedAt": row["notified_at"],
            "name": row["tg_name"] or "",
            "username": row["username"] or "",
            "email": row["email"] or "",
        }
        for row in rows
    ]


MOSCOW_OFFSET = timedelta(hours=3)


def moscow_day_start_utc(dt):
    local = dt + MOSCOW_OFFSET
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight - MOSCOW_OFFSET


def add_months(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def moscow_month_start_utc(dt, delta_months=0):
    local = dt + MOSCOW_OFFSET
    year, month = add_months(local.year, local.month, delta_months)
    local_month = datetime(year, month, 1, tzinfo=timezone.utc)
    return local_month - MOSCOW_OFFSET


def payment_analytics_period(query):
    now = utc_now()
    range_key = (query.get("range") or ["week"])[0] or "week"
    if range_key == "month":
        return range_key, moscow_day_start_utc(now - timedelta(days=29)), now, "day"
    if range_key == "year":
        return range_key, moscow_month_start_utc(now, -11), now, "month"
    if range_key == "custom":
        start = parse_admin_datetime((query.get("start") or [""])[0])
        end = parse_admin_datetime((query.get("end") or [""])[0]) or now
        if not start or start >= end:
            start = end - timedelta(days=7)
        bucket = "month" if (end - start).days > 90 else "day"
        return range_key, start, end, bucket
    return "week", moscow_day_start_utc(now - timedelta(days=6)), now, "day"


def payment_bucket_label(dt, bucket):
    local = dt + MOSCOW_OFFSET
    if bucket == "month":
        return local.strftime("%m.%Y")
    return local.strftime("%d.%m")


def next_payment_bucket_start(dt, bucket):
    if bucket == "month":
        return moscow_month_start_utc(dt + MOSCOW_OFFSET + timedelta(days=32), 0)
    return dt + timedelta(days=1)


def admin_payments_analytics_payload(query):
    range_key, start, end, bucket = payment_analytics_period(query)
    buckets = []
    cursor = start
    while cursor < end and len(buckets) < 370:
        next_cursor = next_payment_bucket_start(cursor, bucket)
        buckets.append(
            {
                "start": cursor,
                "end": next_cursor,
                "label": payment_bucket_label(cursor, bucket),
                "amount": 0,
                "count": 0,
            }
        )
        cursor = next_cursor
    if not buckets:
        buckets.append(
            {
                "start": start,
                "end": end,
                "label": payment_bucket_label(start, bucket),
                "amount": 0,
                "count": 0,
            }
        )

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT amount, created_at
            FROM payments
            WHERE LOWER(COALESCE(status, '')) = 'succeeded'
            """
        ).fetchall()

    total_amount = 0
    total_count = 0
    for row in rows:
        created_at = parse_iso(row["created_at"])
        if not created_at or created_at < start or created_at > end:
            continue
        amount = int(row["amount"] or 0)
        total_amount += amount
        total_count += 1
        for item in buckets:
            if item["start"] <= created_at < item["end"]:
                item["amount"] += amount
                item["count"] += 1
                break

    return {
        "ok": True,
        "range": range_key,
        "bucket": bucket,
        "totalAmount": total_amount,
        "totalCount": total_count,
        "series": [
            {
                "label": item["label"],
                "amount": item["amount"],
                "count": item["count"],
            }
            for item in buckets
        ],
    }


def admin_list_dns():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                d.sub_id,
                d.ip,
                d.assigned_at,
                d.last_checked,
                d.location,
                u.tg_id,
                u.tg_name,
                u.username,
                wa.email
            FROM dns_assignments d
            LEFT JOIN users u ON u.sub_id = d.sub_id
            LEFT JOIN web_accounts wa
              ON wa.linked_tg_id = u.tg_id
              OR wa.web_tg_id = u.tg_id
            ORDER BY d.assigned_at DESC
            """
        ).fetchall()
    return [
        {
            "subId": row["sub_id"],
            "shortSubId": str(row["sub_id"] or "").split("-", 1)[0],
            "ip": row["ip"],
            "assignedAt": row["assigned_at"],
            "lastChecked": row["last_checked"],
            "location": row["location"] or "",
            "tgId": row["tg_id"],
            "name": row["tg_name"] or "",
            "username": row["username"] or "",
            "email": row["email"] or "",
        }
        for row in rows
    ]


def admin_list_support_conversations(limit=200):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                a.id AS account_id,
                a.email,
                a.name,
                a.linked_tg_id,
                a.web_tg_id,
                (
                    SELECT message_text
                    FROM web_support_messages m
                    WHERE m.account_id = a.id
                    ORDER BY m.id DESC
                    LIMIT 1
                ) AS last_text,
                (
                    SELECT created_at
                    FROM web_support_messages m
                    WHERE m.account_id = a.id
                    ORDER BY m.id DESC
                    LIMIT 1
                ) AS last_at,
                (
                    SELECT COUNT(*)
                    FROM web_support_messages m
                    WHERE m.account_id = a.id
                ) AS message_count
            FROM web_accounts a
            WHERE EXISTS (
                SELECT 1
                FROM web_support_messages m
                WHERE m.account_id = a.id
            )
            ORDER BY last_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        {
            "accountId": row["account_id"],
            "email": row["email"] or "",
            "name": row["name"] or row["email"] or f"Аккаунт {row['account_id']}",
            "tgId": row["linked_tg_id"] or row["web_tg_id"],
            "lastText": row["last_text"] or "",
            "lastAt": row["last_at"],
            "messageCount": int(row["message_count"] or 0),
            "typing": support_typing_state(row["account_id"], "admin"),
        }
        for row in rows
    ]


def get_support_account(account_id):
    with connect() as conn:
        return conn.execute(
            """
            SELECT id, email, name, linked_tg_id, web_tg_id
            FROM web_accounts
            WHERE id = ?
            LIMIT 1
            """,
            (int(account_id),),
        ).fetchone()


def admin_list_promos():
    path = promo_db_path()
    if not os.path.exists(path):
        return []
    with connect_sqlite(path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT code, amount, created_at, used_at
            FROM promos
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [
        {
            "code": row["code"],
            "amount": int(row["amount"] or 0),
            "createdAt": row["created_at"],
            "usedAt": row["used_at"],
        }
        for row in rows
    ]


def admin_create_promo(code, amount):
    amount = int(amount or 0)
    if amount <= 0:
        raise ValueError("invalid_amount")
    code = re.sub(r"[^A-Z0-9_-]+", "", str(code or "").upper())[:32]
    if not code:
        code = "KL-" + secrets.token_hex(4).upper()
    path = promo_db_path()
    with connect_sqlite(path, timeout=10) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS promos(
                code TEXT PRIMARY KEY,
                amount INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                used_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO promos (code, amount, created_at)
            VALUES (?, ?, ?)
            """,
            (code, amount, iso_now()),
        )
        conn.commit()
    return code


def admin_delete_promo(code):
    code = str(code or "").strip().upper()
    if not code:
        raise ValueError("invalid_promo")
    with connect_sqlite(promo_db_path(), timeout=10) as conn:
        conn.execute("DELETE FROM promos WHERE code = ?", (code,))
        conn.commit()


def _referral_items(invited_json):
    try:
        value = json.loads(invited_json or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _public_ref_user(conn, tg_id):
    row = conn.execute(
        """
        SELECT
            u.tg_id,
            u.tg_name,
            u.username,
            wa.email
        FROM users u
        LEFT JOIN web_accounts wa
          ON wa.linked_tg_id = u.tg_id
          OR wa.web_tg_id = u.tg_id
        WHERE u.tg_id = ?
        LIMIT 1
        """,
        (int(tg_id),),
    ).fetchone()
    if not row:
        return {"tgId": int(tg_id), "name": "", "username": "", "email": ""}
    return {
        "tgId": row["tg_id"],
        "name": row["tg_name"] or "",
        "username": row["username"] or "",
        "email": row["email"] or "",
    }


def admin_referrals_for(tg_id):
    tg_id = int(tg_id)
    path = ref_db_path()
    result = {"tgId": tg_id, "ownCode": "", "invited": [], "invitedBy": None}
    if not os.path.exists(path):
        return result
    with connect_sqlite(path, timeout=10) as ref_conn:
        ref_conn.row_factory = sqlite3.Row
        own = ref_conn.execute(
            """
            SELECT inviter_tg_id, ref_code, invited_json
            FROM referrals
            WHERE inviter_tg_id = ?
            LIMIT 1
            """,
            (tg_id,),
        ).fetchone()
        all_rows = ref_conn.execute(
            "SELECT inviter_tg_id, ref_code, invited_json FROM referrals"
        ).fetchall()
    with connect() as conn:
        if own:
            result["ownCode"] = own["ref_code"] or ""
            for item in _referral_items(own["invited_json"]):
                invited_tg_id = int(item.get("tg_id") or 0)
                if not invited_tg_id:
                    continue
                public = _public_ref_user(conn, invited_tg_id)
                public["rewarded"] = int(item.get("rewarded") or 0)
                result["invited"].append(public)
        for row in all_rows:
            for item in _referral_items(row["invited_json"]):
                if int(item.get("tg_id") or 0) == tg_id:
                    inviter = _public_ref_user(conn, int(row["inviter_tg_id"]))
                    inviter["code"] = row["ref_code"] or ""
                    inviter["rewarded"] = int(item.get("rewarded") or 0)
                    result["invitedBy"] = inviter
                    break
            if result["invitedBy"]:
                break
    return result


def get_or_create_referral_code(tg_id):
    tg_id = int(tg_id)
    path = ref_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with connect_sqlite(path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_tg_id INTEGER UNIQUE NOT NULL,
                ref_code TEXT UNIQUE NOT NULL,
                invited_json TEXT DEFAULT '[]'
            )
            """
        )
        row = conn.execute(
            "SELECT ref_code FROM referrals WHERE inviter_tg_id = ? LIMIT 1",
            (tg_id,),
        ).fetchone()
        if row and row["ref_code"]:
            return row["ref_code"]
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(8))
            exists = conn.execute(
                "SELECT 1 FROM referrals WHERE ref_code = ? LIMIT 1",
                (code,),
            ).fetchone()
            if not exists:
                break
        conn.execute(
            """
            INSERT INTO referrals (inviter_tg_id, ref_code, invited_json)
            VALUES (?, ?, '[]')
            ON CONFLICT(inviter_tg_id) DO UPDATE SET
                ref_code = excluded.ref_code
            """,
            (tg_id, code),
        )
        conn.commit()
        return code


def public_referrals_for(tg_id):
    result = admin_referrals_for(tg_id)
    if not result.get("ownCode"):
        result["ownCode"] = get_or_create_referral_code(tg_id)
    return result


def admin_bootstrap_payload(account):
    access = admin_access_for_account(account) or {}
    permissions = access.get("permissions") or {}
    is_owner = bool(access.get("isOwner"))
    return {
        "ok": True,
        "admin": {
            "accountId": account["id"],
            "email": public_email(account["email"]) or account["name"] or "Telegram",
            "tgId": account["linked_tg_id"] or account["web_tg_id"],
            "isOwner": is_owner,
            "permissions": permissions,
            "canManageAdmins": is_owner,
        },
        "users": admin_list_users(),
        "admins": admin_list_admins() if is_owner else [],
        "payments": admin_list_payments() if is_owner or permissions.get("payments") else [],
        "promos": admin_list_promos() if is_owner or permissions.get("promos") else [],
        "dns": admin_list_dns() if is_owner or permissions.get("dns") else [],
        "support": (
            admin_list_support_conversations()
            if is_owner or permissions.get("support")
            else []
        ),
    }


def admin_broadcast_recipients(channel):
    channel = str(channel or "both").strip().lower()
    recipients = {"emails": [], "telegram": []}
    with connect() as conn:
        if channel in {"email", "both"}:
            email_rows = conn.execute(
                """
                SELECT email
                FROM web_accounts
                WHERE email_verified = 1
                  AND COALESCE(email_announcements_enabled, 1) = 1
                  AND email IS NOT NULL
                  AND TRIM(email) <> ''
                ORDER BY id ASC
                """
            ).fetchall()
            seen_emails = set()
            for row in email_rows:
                email = normalize_email(row["email"])
                if email and email not in seen_emails:
                    seen_emails.add(email)
                    recipients["emails"].append(email)
        if channel in {"telegram", "both"}:
            tg_rows = conn.execute(
                """
                SELECT DISTINCT tg_id
                FROM users
                WHERE tg_id > 0
                  AND COALESCE(is_blocked, 0) = 0
                ORDER BY tg_id ASC
                """
            ).fetchall()
            recipients["telegram"] = [int(row["tg_id"]) for row in tg_rows if row["tg_id"]]
    return recipients


def admin_send_broadcast(subject, text, channel):
    subject = str(subject or "Сообщение").strip()[:120] or "Сообщение"
    text = str(text or "").strip()
    channel = str(channel or "both").strip().lower()
    if channel not in {"email", "telegram", "both"}:
        raise ValueError("invalid_channel")
    if not text:
        raise ValueError("message_required")

    recipients = admin_broadcast_recipients(channel)
    result = {
        "emailSent": 0,
        "emailFailed": 0,
        "telegramSent": 0,
        "telegramFailed": 0,
    }

    if channel in {"email", "both"}:
        html = broadcast_email_html(subject, text)
        headers = {
            "List-Unsubscribe": f"<{public_site_url()}/>",
        }
        for email in recipients["emails"]:
            try:
                send_mail(email, subject, text, html, headers=headers)
                result["emailSent"] += 1
            except Exception:
                logger.exception("Broadcast email failed email=%s", email)
                result["emailFailed"] += 1

    if channel in {"telegram", "both"}:
        telegram_text = escape(text)
        for tg_id in recipients["telegram"]:
            try:
                send_telegram_message(tg_id, telegram_text)
                result["telegramSent"] += 1
            except Exception:
                logger.exception("Broadcast telegram failed tg_id=%s", tg_id)
                result["telegramFailed"] += 1

    return result


def email_paragraphs(text, color="#eadfe2"):
    paragraphs = []
    for part in str(text or "").strip().split("\n\n"):
        html = escape(part.strip()).replace("\n", "<br>")
        if html:
            paragraphs.append(
                f'<p style="margin:0 0 12px;color:{color};font-size:16px;line-height:1.65;">{html}</p>'
            )
    return "".join(paragraphs) or f'<p style="margin:0;color:{color};font-size:16px;line-height:1.65;"></p>'


def admin_message_email_html(text, subject="Сообщение"):
    return email_shell(
        str(subject or "Сообщение").strip()[:120] or "Сообщение",
        "Сообщение в личном кабинете Kingline Proxy.",
        email_paragraphs(text),
        footer_text="",
    )


def broadcast_email_html(subject, text):
    return email_shell(
        str(subject or "Сообщение").strip()[:120] or "Сообщение",
        "Важное объявление Kingline Proxy.",
        email_paragraphs(text),
        footer_text="",
    )


def send_magic_link_email(email, link, expires_at):
    text = (
        "\n".join(
            [
                f"Это ссылка для входа в личный кабинет {BRAND_NAME}.",
                "",
                link,
                "",
                f"Ссылка действует до {expires_at} UTC и может быть использована один раз.",
                "Если вы не запрашивали вход, просто проигнорируйте это письмо.",
            ]
        )
    )
    html = email_shell(
        "Вход в кабинет",
        "Откройте одноразовую ссылку, чтобы войти в аккаунт.",
        f"<p>Ссылка действует до <b>{escape(format_msk(expires_at))}</b> и может быть использована один раз.</p>",
        "Войти",
        link,
    )
    send_mail(email, f"Вход в {BRAND_NAME}", text, html)


def create_email_verification(account_id, email, client_ip, user_agent):
    token = secrets.token_urlsafe(36)
    token_digest = hash_token(token)
    expires_at = (utc_now() + timedelta(minutes=EMAIL_VERIFY_MINUTES)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM web_email_verifications
            WHERE account_id = ? AND consumed_at IS NULL
            """,
            (account_id,),
        )
        conn.execute(
            """
            DELETE FROM web_email_verifications
            WHERE expires_at <= ?
            """,
            (iso_now(),),
        )
        conn.execute(
            """
            INSERT INTO web_email_verifications (
                token_hash,
                account_id,
                email,
                expires_at,
                ip,
                user_agent
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token_digest,
                account_id,
                email,
                expires_at,
                client_ip,
                (user_agent or "")[:300],
            ),
        )
        conn.commit()
    return f"{public_site_url()}/api/auth/verify-email?{urlencode({'token': token})}", expires_at


def consume_email_verification(token):
    token_digest = hash_token(token or "")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            """
            SELECT token_hash, account_id, email, expires_at, consumed_at
            FROM web_email_verifications
            WHERE token_hash = ?
            LIMIT 1
            """,
            (token_digest,),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("email_verify_invalid")
        if row["consumed_at"]:
            conn.rollback()
            raise ValueError("email_verify_used")
        expires = parse_iso(row["expires_at"])
        if not expires or expires <= utc_now():
            cur.execute(
                "UPDATE web_email_verifications SET consumed_at = ? WHERE token_hash = ?",
                (iso_now(), token_digest),
            )
            conn.commit()
            raise ValueError("email_verify_expired")

        cur.execute(
            """
            UPDATE web_accounts
            SET email_verified = 1,
                updated_at = ?,
                last_login_at = ?
            WHERE id = ? AND email = ?
            """,
            (iso_now(), iso_now(), row["account_id"], row["email"]),
        )
        cur.execute(
            "UPDATE web_email_verifications SET consumed_at = ? WHERE token_hash = ?",
            (iso_now(), token_digest),
        )
        conn.commit()

        account = conn.execute(
            f"SELECT {account_select()} FROM web_accounts WHERE id = ?",
            (row["account_id"],),
        ).fetchone()
        if not account:
            raise ValueError("email_verify_invalid")
        return account


def create_password_reset(account_id, email, client_ip, user_agent):
    token = secrets.token_urlsafe(40)
    token_digest = hash_token(token)
    expires_at = (utc_now() + timedelta(minutes=PASSWORD_RESET_MINUTES)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM web_password_resets
            WHERE account_id = ? AND consumed_at IS NULL
            """,
            (account_id,),
        )
        conn.execute(
            """
            DELETE FROM web_password_resets
            WHERE expires_at <= ?
            """,
            (iso_now(),),
        )
        conn.execute(
            """
            INSERT INTO web_password_resets (
                token_hash,
                account_id,
                email,
                expires_at,
                ip,
                user_agent
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token_digest,
                int(account_id),
                email,
                expires_at,
                client_ip,
                (user_agent or "")[:300],
            ),
        )
        conn.commit()
    return f"{public_site_url()}/?{urlencode({'reset_token': token})}", expires_at


def consume_password_reset(token, password):
    if len(str(password or "")) < 8:
        raise ValueError("weak_password")

    token_digest = hash_token(str(token or ""))
    password_digest = hash_password(str(password))
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            """
            SELECT token_hash, account_id, email, expires_at, consumed_at
            FROM web_password_resets
            WHERE token_hash = ?
            LIMIT 1
            """,
            (token_digest,),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("password_reset_invalid")
        if row["consumed_at"]:
            conn.rollback()
            raise ValueError("password_reset_used")
        expires = parse_iso(row["expires_at"])
        if not expires or expires <= utc_now():
            cur.execute(
                "UPDATE web_password_resets SET consumed_at = ? WHERE token_hash = ?",
                (iso_now(), token_digest),
            )
            conn.commit()
            raise ValueError("password_reset_expired")

        cur.execute(
            """
            UPDATE web_accounts
            SET password_hash = ?,
                email_verified = 1,
                updated_at = ?,
                last_login_at = ?
            WHERE id = ? AND email = ?
            """,
            (
                password_digest,
                iso_now(),
                iso_now(),
                int(row["account_id"]),
                row["email"],
            ),
        )
        if cur.rowcount != 1:
            conn.rollback()
            raise ValueError("password_reset_invalid")
        cur.execute(
            "DELETE FROM web_sessions WHERE account_id = ?",
            (int(row["account_id"]),),
        )
        cur.execute(
            "UPDATE web_password_resets SET consumed_at = ? WHERE token_hash = ?",
            (iso_now(), token_digest),
        )
        conn.commit()

        account = conn.execute(
            f"SELECT {account_select()} FROM web_accounts WHERE id = ?",
            (int(row["account_id"]),),
        ).fetchone()
        if not account:
            raise ValueError("password_reset_invalid")
        return account


def registration_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def delete_pending_registration(email):
    with connect() as conn:
        conn.execute(
            "DELETE FROM web_pending_registrations WHERE email = ?",
            (normalize_email(email),),
        )
        conn.commit()


def remove_unverified_account(cur, account):
    if not account or int(account["email_verified"] or 0) == 1:
        return
    account_id = int(account["id"])
    web_tg_id = int(account["web_tg_id"] or -account_id)
    from vpn_preferences import forget
    forget(cur,web_tg_id)
    cur.execute("DELETE FROM web_sessions WHERE account_id = ?", (account_id,))
    cur.execute("DELETE FROM web_link_codes WHERE account_id = ?", (account_id,))
    cur.execute("DELETE FROM web_email_verifications WHERE account_id = ?", (account_id,))
    cur.execute("DELETE FROM web_password_resets WHERE account_id = ?", (account_id,))
    cur.execute("DELETE FROM users WHERE tg_id = ?", (web_tg_id,))
    cur.execute("DELETE FROM web_accounts WHERE id = ?", (account_id,))


def create_pending_registration(email, name, password_hash, client_ip, user_agent):
    code = registration_code()
    token = secrets.token_urlsafe(36)
    code_digest = hash_token(code)
    token_digest = hash_token(token)
    expires_at = (utc_now() + timedelta(minutes=EMAIL_VERIFY_MINUTES)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        account = cur.execute(
            """
            SELECT id, email_verified, web_tg_id
            FROM web_accounts
            WHERE email = ?
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        if account and int(account["email_verified"] or 0) == 1:
            conn.rollback()
            raise ValueError("email_exists")

        remove_unverified_account(cur, account)
        cur.execute(
            """
            DELETE FROM web_pending_registrations
            WHERE expires_at <= ? OR consumed_at IS NOT NULL
            """,
            (iso_now(),),
        )
        cur.execute(
            """
            INSERT INTO web_pending_registrations (
                email,
                name,
                password_hash,
                code_hash,
                token_hash,
                expires_at,
                attempts,
                ip,
                user_agent,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name = excluded.name,
                password_hash = excluded.password_hash,
                code_hash = excluded.code_hash,
                token_hash = excluded.token_hash,
                expires_at = excluded.expires_at,
                consumed_at = NULL,
                attempts = 0,
                ip = excluded.ip,
                user_agent = excluded.user_agent,
                updated_at = excluded.updated_at
            """,
            (
                email,
                name,
                password_hash,
                code_digest,
                token_digest,
                expires_at,
                client_ip,
                (user_agent or "")[:300],
                iso_now(),
            ),
        )
        conn.commit()

    link = f"{public_site_url()}/api/auth/verify-registration?{urlencode({'token': token})}"
    return link, code, expires_at


def create_account_from_pending(cur, row):
    account = cur.execute(
        """
        SELECT id, email_verified, web_tg_id
        FROM web_accounts
        WHERE email = ?
        LIMIT 1
        """,
        (row["email"],),
    ).fetchone()
    if account and int(account["email_verified"] or 0) == 1:
        raise ValueError("email_exists")

    remove_unverified_account(cur, account)
    cur.execute(
        """
        INSERT INTO web_accounts (
            email,
            name,
            password_hash,
            email_verified,
            updated_at,
            last_login_at
        )
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (row["email"], row["name"], row["password_hash"], iso_now(), iso_now()),
    )
    account_id = int(cur.lastrowid)
    web_tg_id = -account_id
    cur.execute(
        "UPDATE web_accounts SET web_tg_id = ? WHERE id = ?",
        (web_tg_id, account_id),
    )
    return account_id


def consume_pending_registration_by_token(token):
    token_digest = hash_token(token or "")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            """
            SELECT *
            FROM web_pending_registrations
            WHERE token_hash = ?
            LIMIT 1
            """,
            (token_digest,),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("registration_verify_invalid")
        account = finish_pending_registration(conn, cur, row)
        return account


def consume_pending_registration_by_code(email, code):
    email = normalize_email(email)
    code_digest = hash_token(str(code or "").strip())
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            """
            SELECT *
            FROM web_pending_registrations
            WHERE email = ?
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("registration_verify_invalid")
        if int(row["attempts"] or 0) >= REGISTRATION_CODE_ATTEMPTS:
            conn.rollback()
            raise ValueError("registration_verify_too_many")
        if not hmac.compare_digest(row["code_hash"], code_digest):
            cur.execute(
                """
                UPDATE web_pending_registrations
                SET attempts = COALESCE(attempts, 0) + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (iso_now(), row["id"]),
            )
            conn.commit()
            raise ValueError("registration_verify_invalid")
        account = finish_pending_registration(conn, cur, row)
        return account


def finish_pending_registration(conn, cur, row):
    if row["consumed_at"]:
        conn.rollback()
        raise ValueError("registration_verify_used")
    expires = parse_iso(row["expires_at"])
    if not expires or expires <= utc_now():
        cur.execute(
            """
            UPDATE web_pending_registrations
            SET consumed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (iso_now(), iso_now(), row["id"]),
        )
        conn.commit()
        raise ValueError("registration_verify_expired")

    account_id = create_account_from_pending(cur, row)
    cur.execute(
        """
        UPDATE web_pending_registrations
        SET consumed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (iso_now(), iso_now(), row["id"]),
    )
    conn.commit()
    return conn.execute(
        f"SELECT {account_select()} FROM web_accounts WHERE id = ?",
        (account_id,),
    ).fetchone()


def send_registration_email(email, code, link, expires_at):
    text = (
        "\n".join(
            [
                f"Подтвердите email, чтобы завершить регистрацию {BRAND_NAME}.",
                "",
                f"Код подтверждения: {code}",
                "",
                "Можно также нажать одноразовую ссылку:",
                link,
                "",
                f"Код и ссылка действуют до {expires_at} UTC.",
                "Если вы не создавали аккаунт, просто проигнорируйте это письмо.",
            ]
        )
    )
    html = email_shell(
        "Подтвердите почту",
        "Введите код на сайте или нажмите кнопку ниже, чтобы завершить регистрацию.",
        (
            f"<p>Код и ссылка действуют до <b>{escape(format_msk(expires_at))}</b>.</p>"
            "<p>Без подтверждения почты вход в кабинет закрыт.</p>"
        ),
        "Подтвердить email",
        link,
        code=code,
    )
    send_mail(email, f"Код подтверждения {BRAND_NAME}", text, html)


def validate_telegram_webapp_init_data(init_data):
    if not BOT_TOKEN:
        raise ValueError("telegram_auth_failed")

    raw = str(init_data or "").strip()
    if not raw:
        raise ValueError("telegram_auth_failed")

    try:
        pairs = parse_qsl(raw, keep_blank_values=True)
    except ValueError as exc:
        raise ValueError("telegram_auth_failed") from exc

    values = dict(pairs)
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise ValueError("telegram_auth_failed")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("telegram_auth_failed")

    try:
        auth_date = int(values.get("auth_date") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("telegram_auth_failed") from exc

    now = int(time.time())
    if (
        auth_date <= 0
        or now - auth_date > TELEGRAM_WEBAPP_AUTH_MAX_AGE_SECONDS
        or auth_date - now > 60
    ):
        raise ValueError("telegram_auth_failed")

    try:
        user = json.loads(values.get("user") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("telegram_auth_failed") from exc
    if not isinstance(user, dict):
        raise ValueError("telegram_auth_failed")
    try:
        tg_id = int(user.get("id") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("telegram_auth_failed") from exc
    if tg_id <= 0:
        raise ValueError("telegram_auth_failed")

    return user


def telegram_display_name(tg_user):
    first = str(tg_user.get("first_name") or "").strip()
    last = str(tg_user.get("last_name") or "").strip()
    name = " ".join(part for part in (first, last) if part).strip()
    if name:
        return name[:80]
    username = str(tg_user.get("username") or "").strip().lstrip("@")
    return (username or f"Telegram {int(tg_user.get('id'))}")[:80]


def telegram_username(tg_user):
    raw = str(tg_user.get("username") or "").strip().lstrip("@")
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "", raw)
    if cleaned:
        return cleaned[:48]
    return f"user{int(tg_user.get('id'))}"


def get_or_create_telegram_web_account(tg_user, client_ip, user_agent):
    tg_id = int(tg_user.get("id"))
    email = telegram_internal_email(tg_id)
    name = telegram_display_name(tg_user)
    username = telegram_username(tg_user)

    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            f"""
            SELECT {account_select()}
            FROM web_accounts
            WHERE linked_tg_id = ?
               OR web_tg_id = ?
               OR email = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (tg_id, tg_id, email),
        ).fetchone()
        if row:
            account_id = int(row["id"])
            cur.execute(
                """
                UPDATE web_accounts
                SET name = COALESCE(NULLIF(?, ''), name),
                    linked_tg_id = ?,
                    web_tg_id = ?,
                    telegram_bonus_claimed = 1,
                    updated_at = ?,
                    last_login_at = ?
                WHERE id = ?
                """,
                (name, tg_id, tg_id, iso_now(), iso_now(), account_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO web_accounts (
                    email,
                    name,
                    password_hash,
                    linked_tg_id,
                    web_tg_id,
                    email_verified,
                    telegram_bonus_claimed,
                    updated_at,
                    last_login_at
                )
                VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?)
                """,
                (
                    email,
                    name,
                    hash_password(secrets.token_urlsafe(32)),
                    tg_id,
                    tg_id,
                    iso_now(),
                    iso_now(),
                ),
            )
            account_id = int(cur.lastrowid)

        conn.commit()

    BillingDB.ensure_user_row(tg_id, name, username)
    with connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET tg_name = COALESCE(NULLIF(?, ''), tg_name),
                username = COALESCE(NULLIF(?, ''), username),
                updated_at = CURRENT_TIMESTAMP
            WHERE tg_id = ?
            """,
            (name, username, tg_id),
        )
        conn.commit()
    return get_account_by_id(account_id)


def create_email_link_request(account, email, name, password_hash, client_ip, user_agent):
    if not is_telegram_internal_email(account["email"]):
        raise ValueError("email_exists")

    code = registration_code()
    token = secrets.token_urlsafe(36)
    code_digest = hash_token(code)
    token_digest = hash_token(token)
    expires_at = (utc_now() + timedelta(minutes=EMAIL_VERIFY_MINUTES)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        existing = cur.execute(
            """
            SELECT id
            FROM web_accounts
            WHERE email = ? AND id != ?
            LIMIT 1
            """,
            (email, int(account["id"])),
        ).fetchone()
        if existing:
            conn.rollback()
            raise ValueError("email_exists")

        cur.execute(
            """
            DELETE FROM web_email_link_requests
            WHERE account_id = ? OR email = ? OR expires_at <= ? OR consumed_at IS NOT NULL
            """,
            (int(account["id"]), email, iso_now()),
        )
        cur.execute(
            """
            INSERT INTO web_email_link_requests (
                account_id,
                email,
                name,
                password_hash,
                code_hash,
                token_hash,
                expires_at,
                attempts,
                ip,
                user_agent,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                int(account["id"]),
                email,
                name,
                password_hash,
                code_digest,
                token_digest,
                expires_at,
                client_ip,
                (user_agent or "")[:300],
                iso_now(),
            ),
        )
        conn.commit()

    link = f"{public_site_url()}/api/auth/verify-email-link?{urlencode({'token': token})}"
    return link, code, expires_at


def finish_email_link_request(conn, cur, row):
    if row["consumed_at"]:
        conn.rollback()
        raise ValueError("email_link_used")
    expires = parse_iso(row["expires_at"])
    if not expires or expires <= utc_now():
        cur.execute(
            """
            UPDATE web_email_link_requests
            SET consumed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (iso_now(), iso_now(), row["id"]),
        )
        conn.commit()
        raise ValueError("email_link_expired")

    account = cur.execute(
        f"SELECT {account_select()} FROM web_accounts WHERE id = ? LIMIT 1",
        (int(row["account_id"]),),
    ).fetchone()
    if not account or not is_telegram_internal_email(account["email"]):
        conn.rollback()
        raise ValueError("email_link_invalid")

    existing = cur.execute(
        """
        SELECT id
        FROM web_accounts
        WHERE email = ? AND id != ?
        LIMIT 1
        """,
        (row["email"], int(row["account_id"])),
    ).fetchone()
    if existing:
        conn.rollback()
        raise ValueError("email_exists")

    cur.execute(
        """
        UPDATE web_accounts
        SET email = ?,
            name = ?,
            password_hash = ?,
            email_verified = 1,
            updated_at = ?,
            last_login_at = ?
        WHERE id = ?
        """,
        (
            row["email"],
            row["name"],
            row["password_hash"],
            iso_now(),
            iso_now(),
            int(row["account_id"]),
        ),
    )
    cur.execute(
        """
        UPDATE web_email_link_requests
        SET consumed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (iso_now(), iso_now(), row["id"]),
    )
    conn.commit()
    return conn.execute(
        f"SELECT {account_select()} FROM web_accounts WHERE id = ?",
        (int(row["account_id"]),),
    ).fetchone()


def consume_email_link_by_code(account_id, email, code):
    email = normalize_email(email)
    code_digest = hash_token(str(code or "").strip())
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            """
            SELECT *
            FROM web_email_link_requests
            WHERE account_id = ? AND email = ?
            LIMIT 1
            """,
            (int(account_id), email),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("email_link_invalid")
        if int(row["attempts"] or 0) >= EMAIL_LINK_CODE_ATTEMPTS:
            conn.rollback()
            raise ValueError("email_link_too_many")
        if not hmac.compare_digest(row["code_hash"], code_digest):
            cur.execute(
                """
                UPDATE web_email_link_requests
                SET attempts = COALESCE(attempts, 0) + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (iso_now(), row["id"]),
            )
            conn.commit()
            raise ValueError("email_link_invalid")
        return finish_email_link_request(conn, cur, row)


def consume_email_link_by_token(token):
    token_digest = hash_token(token or "")
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            """
            SELECT *
            FROM web_email_link_requests
            WHERE token_hash = ?
            LIMIT 1
            """,
            (token_digest,),
        ).fetchone()
        if not row:
            conn.rollback()
            raise ValueError("email_link_invalid")
        return finish_email_link_request(conn, cur, row)


def send_email_link_email(email, code, link, expires_at):
    text = (
        "\n".join(
            [
                f"Подтвердите email для личного кабинета {BRAND_NAME}.",
                "",
                f"Код подтверждения: {code}",
                "",
                "Можно также нажать одноразовую ссылку:",
                link,
                "",
                f"Код и ссылка действуют до {expires_at} UTC.",
                "Если вы не привязывали почту, просто проигнорируйте это письмо.",
            ]
        )
    )
    html = email_shell(
        "Привяжите почту",
        "Введите код на сайте или нажмите кнопку ниже, чтобы добавить email к аккаунту.",
        (
            f"<p>Код и ссылка действуют до <b>{escape(format_msk(expires_at))}</b>.</p>"
            "<p>После подтверждения можно будет входить и через Telegram, и через email с паролем.</p>"
        ),
        "Подтвердить email",
        link,
        code=code,
    )
    send_mail(email, f"Код подтверждения {BRAND_NAME}", text, html)


def send_verification_email(email, link, expires_at):
    text = (
        "\n".join(
            [
                f"Подтвердите email, чтобы войти в личный кабинет {BRAND_NAME}.",
                "",
                link,
                "",
                f"Ссылка действует до {expires_at} UTC.",
                "Если вы не создавали аккаунт, просто проигнорируйте это письмо.",
            ]
        )
    )
    html = email_shell(
        "Подтверждение email",
        "Откройте одноразовую ссылку, чтобы подтвердить почту и войти.",
        f"<p>Ссылка действует до <b>{escape(format_msk(expires_at))}</b>.</p>",
        "Подтвердить и войти",
        link,
    )
    send_mail(email, f"Подтверждение email {BRAND_NAME}", text, html)


def send_password_reset_email(email, link, expires_at):
    text = (
        "\n".join(
            [
                f"Вы запросили смену пароля для личного кабинета {BRAND_NAME}.",
                "",
                link,
                "",
                f"Ссылка действует до {expires_at} UTC и может быть использована один раз.",
                "Если вы не запрашивали смену пароля, просто проигнорируйте это письмо.",
            ]
        )
    )
    html = email_shell(
        "Смена пароля",
        "Откройте одноразовую ссылку и задайте новый пароль для входа.",
        (
            f"<p>Ссылка действует до <b>{escape(format_msk(expires_at))}</b> "
            "и может быть использована один раз.</p>"
            "<p>После смены пароля все старые сессии будут завершены.</p>"
        ),
        "Сменить пароль",
        link,
    )
    send_mail(email, f"Смена пароля {BRAND_NAME}", text, html)


def is_mail_configured():
    return bool(
        os.getenv("SMTP_HOST", "").strip()
        and (
            os.getenv("SMTP_FROM", "").strip()
            or os.getenv("SMTP_USER", "").strip()
            or os.getenv("MAIL_FROM", "").strip()
        )
    )


def redeem_promo(code):
    code = (code or "").strip().upper()
    if not code:
        return None

    path = os.environ.get("PROMOS_DB_PATH") or os.path.join(KINGLINE_DIR, "promos.db")
    with connect_sqlite(path, timeout=10) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("SELECT amount FROM promos WHERE code = ?", (code,))
        row = cur.fetchone()
        if not row:
            conn.commit()
            return None
        amount = int(row[0] or 0)
        cur.execute("DELETE FROM promos WHERE code = ?", (code,))
        conn.commit()
        return amount


def credit_succeeded_payment(payment_id):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            "SELECT tg_id, amount, status FROM payments WHERE payment_id = ?",
            (payment_id,),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return False, "payment_not_found", None

        tg_id = int(row["tg_id"])
        amount = int(row["amount"] or 0)
        status = str(row["status"] or "")
        if status == "succeeded":
            conn.commit()
            return False, "already_succeeded", amount

        cur.execute(
            """
            UPDATE payments
            SET status = 'succeeded'
            WHERE payment_id = ?
            """,
            (payment_id,),
        )
        cur.execute(
            """
            UPDATE users
            SET balance_rub = COALESCE(balance_rub, 0) + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE tg_id = ?
            """,
            (amount, tg_id),
        )
        conn.commit()
        return True, "credited", amount


def validate_trial_available(account):
    web_tg_id, _username = ensure_web_user(account)
    user = get_user_row(web_tg_id) or {}
    if has_active_premium(user):
        raise ValueError("premium_active")
    if int(user.get("free_used") or 0) == 1:
        raise ValueError("trial_used")


def account_payment_row(payment_id, tg_id):
    with connect() as conn:
        return conn.execute(
            """
            SELECT payment_id, tg_id, amount, status
            FROM payments
            WHERE payment_id = ? AND tg_id = ?
            LIMIT 1
            """,
            (str(payment_id), int(tg_id)),
        ).fetchone()


def yookassa_amount_rub(payment):
    amount = payment.get("amount") or {}
    if str(amount.get("currency") or "").upper() != "RUB":
        raise ValueError("payment_check_failed")
    try:
        return Decimal(str(amount.get("value") or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("payment_check_failed")


def validate_yookassa_payment(payment, payment_row):
    if str(payment.get("id") or "") != str(payment_row["payment_id"]):
        raise ValueError("payment_check_failed")
    metadata = payment.get("metadata") or {}
    try:
        metadata_tg_id = int(metadata.get("tg_id") or 0)
    except (TypeError, ValueError):
        metadata_tg_id = 0
    if not metadata_tg_id or metadata_tg_id != int(payment_row["tg_id"]):
        raise ValueError("payment_check_failed")
    expected_amount = Decimal(int(payment_row["amount"] or 0)).quantize(Decimal("0.01"))
    if yookassa_amount_rub(payment) != expected_amount:
        raise ValueError("payment_check_failed")


def account_value(account, key, default=None):
    try:
        value = account[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def can_send_telegram_link_offer(account):
    if not account_value(account, "email"):
        return False
    if int(account_value(account, "email_verified", 0) or 0) != 1:
        return False
    if int(account_value(account, "email_announcements_enabled", 1) or 0) != 1:
        return False
    if int(account_value(account, "telegram_bonus_claimed", 0) or 0) == 1:
        return False
    if account_value(account, "linked_tg_id"):
        return False
    return True


def schedule_telegram_link_email(account, tg_id, trigger_kind, trigger_at=None):
    if not tg_id or not can_send_telegram_link_offer(account):
        return
    trigger_dt = trigger_at
    if not isinstance(trigger_dt, datetime):
        trigger_dt = parse_iso(trigger_dt) or utc_now()
    due_at = trigger_dt + timedelta(hours=TELEGRAM_LINK_REMINDER_DELAY_HOURS)
    try:
        with connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO web_telegram_link_reminders (
                    account_id,
                    tg_id,
                    trigger_kind,
                    trigger_at,
                    due_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(account_value(account, "id")),
                    int(tg_id),
                    trigger_kind,
                    trigger_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    due_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
            )
            conn.commit()
    except Exception:
        logger.exception(
            "Telegram link email scheduling failed account_id=%s tg_id=%s",
            account_value(account, "id"),
            tg_id,
        )


async def delayed_xray_restart(reason):
    delay = max(0, int(XRAY_RESTART_DELAY_SECONDS or 0))
    if delay:
        logger.info("Xray restart delayed for %s seconds after web %s", delay, reason)
        await asyncio.sleep(delay)
    async with BillingDB.subscription_update_lock(f"site_xray_restart:{reason}"):
        await BillingDB.restart_xray_safe()


def schedule_delayed_xray_restart(reason):
    global XRAY_RESTART_PENDING

    with XRAY_RESTART_PENDING_LOCK:
        if XRAY_RESTART_PENDING:
            logger.info("Xray delayed restart already pending; skip duplicate reason=%s", reason)
            return
        XRAY_RESTART_PENDING = True

    def runner():
        global XRAY_RESTART_PENDING
        try:
            asyncio.run(delayed_xray_restart(reason))
        except Exception:
            logger.exception("Delayed Xray restart failed reason=%s", reason)
        finally:
            with XRAY_RESTART_PENDING_LOCK:
                XRAY_RESTART_PENDING = False

    threading.Thread(
        target=runner,
        name=f"kingline-delayed-xray-restart-{reason}",
        daemon=True,
    ).start()


@timed_async("subscription.trial", slow_ms=3000)
async def issue_trial(account):
    return await _activate_account_subscription(account, "trial")


@timed_async("subscription.premium", slow_ms=3000)
async def activate_premium(account):
    return await _activate_account_subscription(account, "premium")


async def _activate_account_subscription(account, kind):
    from handlers.subscription_service import activate_subscription

    web_tg_id, username = ensure_web_user(account)
    result = await activate_subscription(
        tg_id=web_tg_id, tg_name=account["name"] or account["email"],
        username=username, kind=kind,
    )
    # These notifications cannot undo an already committed subscription.
    try:
        schedule_delayed_xray_restart(kind)
        if kind == "trial" or result["first_paid_activation"]:
            schedule_telegram_link_email(account, web_tg_id, kind, utc_now())
    except Exception:
        logger.exception("Post-activation scheduling failed")
    return result["user"]


def get_account_by_id(account_id):
    with connect() as conn:
        return conn.execute(
            f"SELECT {account_select()} FROM web_accounts WHERE id = ?",
            (int(account_id),),
        ).fetchone()


def public_job(row):
    if not row:
        return None
    return {
        "id": row["job_id"],
        "action": row["action"],
        "status": row["status"],
        "stage": account_job_stage(row["job_id"], row["status"]),
        "error": row["error"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "finishedAt": row["finished_at"],
    }


def mark_account_job(job_id, status, error=None):
    finished_at = iso_now() if status in {"succeeded", "failed"} else None
    with connect() as conn:
        conn.execute(
            """
            UPDATE web_account_jobs
            SET status = ?,
                error = ?,
                updated_at = ?,
                finished_at = COALESCE(?, finished_at)
            WHERE job_id = ?
            """,
            (status, error, iso_now(), finished_at, job_id),
        )
        conn.commit()


def account_job_stage(job_id, status):
    from handlers.subscription_service import operation_stage
    return operation_stage("web:" + job_id, status)


def run_account_job(job_id, account_id, action):
    from handlers.subscription_service import request_key, SubscriptionPending
    token = request_key.set("web:" + job_id)
    try:
        mark_account_job(job_id, "running")
        account = get_account_by_id(account_id)
        if not account:
            raise ValueError("unauthorized")
        asyncio.run(issue_trial(account) if action == "trial" else activate_premium(account))
        mark_account_job(job_id, "succeeded")
    except SubscriptionPending:
        mark_account_job(job_id, "retrying")
    except Exception as exc:
        logger.exception("Web subscription job failed action=%s", action)
        mark_account_job(job_id, "failed", auth_errors(exc))
    finally:
        request_key.reset(token)


def create_account_job(account, action):
    if action not in {"trial", "premium"}:
        raise ValueError("operation_failed")

    account_id = int(account["id"])
    job_id = secrets.token_urlsafe(24)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        existing = cur.execute(
            """
            SELECT *
            FROM web_account_jobs
            WHERE account_id = ?
              AND action = ?
              AND status IN ('pending', 'running', 'retrying')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (account_id, action),
        ).fetchone()
        if existing:
            conn.commit()
            return public_job(existing)

        cur.execute(
            """
            INSERT INTO web_account_jobs (
                job_id,
                account_id,
                action,
                status,
                updated_at
            )
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (job_id, account_id, action, iso_now()),
        )
        conn.commit()

    thread = threading.Thread(
        target=run_account_job,
        args=(job_id, account_id, action),
        daemon=True,
        name=f"kingline-web-job-{action}-{account_id}",
    )
    thread.start()

    with connect() as conn:
        return public_job(
            conn.execute(
                "SELECT * FROM web_account_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        )


def get_account_job(account_id, job_id):
    with connect() as conn:
        return public_job(
            conn.execute(
                """
                SELECT *
                FROM web_account_jobs
                WHERE account_id = ? AND job_id = ?
                LIMIT 1
                """,
                (int(account_id), str(job_id or "")),
            ).fetchone()
        )


def set_email_reminders(account, enabled):
    with connect() as conn:
        conn.execute(
            """
            UPDATE web_accounts
            SET email_reminders_enabled = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (1 if enabled else 0, iso_now(), int(account["id"])),
        )
        conn.commit()
    return get_account_by_id(account["id"])


def set_email_announcements(account, enabled):
    with connect() as conn:
        conn.execute(
            """
            UPDATE web_accounts
            SET email_announcements_enabled = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (1 if enabled else 0, iso_now(), int(account["id"])),
        )
        conn.commit()
    return get_account_by_id(account["id"])


def send_expiry_email(account, user, reminder_kind, expires_dt, remaining_sec):
    plan = "Premium" if (user.get("tariff") or "").lower() == "premium" else "доступ"
    remaining = "примерно 24 часа" if reminder_kind == "24h" else "примерно 1 час"
    expires_text = format_msk(expires_dt)
    text = "\n".join(
        [
            f"{plan} скоро закончится.",
            "",
            f"Осталось {remaining}.",
            f"Действует до: {expires_text}.",
            "",
            f"Чтобы не остаться без Proxy, продлите доступ в кабинете {BRAND_NAME}:",
            public_site_url(),
        ]
    )
    html = email_shell(
        "Подписка скоро закончится",
        f"Осталось {remaining}. Продлите доступ заранее, чтобы подключение не оборвалось.",
        (
            f"<p><b>{escape(plan)}</b> действует до <b>{escape(expires_text)}</b>.</p>"
            "<p>В кабинете можно пополнить баланс, продлить Premium и обновить subscription-ссылку.</p>"
            "<p style=\"color:#756b6f;font-size:13px;\">Напоминания можно выключить в разделе «Доступ».</p>"
        ),
        "Открыть кабинет",
        public_site_url(),
    )
    send_mail(
        account["email"],
        f"{BRAND_NAME}: подписка заканчивается",
        text,
        html,
    )


def send_telegram_link_email(account):
    link_code = generate_link_code(account)["code"]
    telegram_url = f"{TELEGRAM_BOT_URL}?{urlencode({'start': f'link_{link_code}'})}"
    text = "\n".join(
        [
            "Привяжите Telegram к личному кабинету Kingline Proxy и получите +3 дня Premium.",
            "",
            "Кнопка откроет нашего Telegram-бота сразу с кодом привязки.",
            "После подтверждения бот объединит сайт и Telegram в одну подписку.",
            "",
            telegram_url,
        ]
    )
    html = email_shell(
        "Привяжите Telegram и получите +3 дня",
        "Откройте бота по кнопке. Код привязки уже будет внутри ссылки.",
        (
            "<p>Мы видим, что у вас уже есть доступ Kingline Proxy. "
            "Telegram-бот поможет быстро получать subscription-ссылку, уведомления и поддержку.</p>"
            f"<p>За привязку начислим <b>+{TELEGRAM_BONUS_DAYS} дня Premium</b>.</p>"
            "<p style=\"color:#756b6f;font-size:13px;\">"
            "Ссылка одноразовая и действует ограниченное время.</p>"
        ),
        "Привязать Telegram",
        telegram_url,
    )
    send_mail(
        account["email"],
        f"{BRAND_NAME}: +{TELEGRAM_BONUS_DAYS} дня за Telegram",
        text,
        html,
    )


def reserve_email_reminder(account_id, tg_id, expires_at, reminder_kind):
    try:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO web_email_reminders (
                    account_id,
                    tg_id,
                    expires_at,
                    reminder_kind,
                    sent_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(account_id), int(tg_id), str(expires_at), reminder_kind, iso_now()),
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False


def release_email_reminder(account_id, tg_id, expires_at, reminder_kind):
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM web_email_reminders
            WHERE account_id = ?
              AND tg_id = ?
              AND expires_at = ?
              AND reminder_kind = ?
            """,
            (int(account_id), int(tg_id), str(expires_at), reminder_kind),
        )
        conn.commit()


def cancel_telegram_link_reminder(reminder_id):
    with connect() as conn:
        conn.execute(
            """
            UPDATE web_telegram_link_reminders
            SET canceled_at = COALESCE(canceled_at, ?)
            WHERE id = ?
            """,
            (iso_now(), int(reminder_id)),
        )
        conn.commit()


def mark_telegram_link_reminder_sent(reminder_id):
    with connect() as conn:
        conn.execute(
            """
            UPDATE web_telegram_link_reminders
            SET sent_at = ?
            WHERE id = ? AND sent_at IS NULL AND canceled_at IS NULL
            """,
            (iso_now(), int(reminder_id)),
        )
        conn.commit()


def telegram_link_reminder_cycle(now):
    with connect() as conn:
        reminders = conn.execute(
            """
            SELECT
                r.id AS reminder_id,
                r.tg_id AS reminder_tg_id,
                a.id,
                a.email,
                a.name,
                a.linked_tg_id,
                a.web_tg_id,
                a.email_verified,
                a.telegram_bonus_claimed,
                COALESCE(a.email_reminders_enabled, 1) AS email_reminders_enabled,
                COALESCE(a.email_announcements_enabled, 1) AS email_announcements_enabled
            FROM web_telegram_link_reminders r
            JOIN web_accounts a ON a.id = r.account_id
            WHERE r.sent_at IS NULL
              AND r.canceled_at IS NULL
              AND r.due_at <= ?
            ORDER BY r.due_at ASC
            LIMIT 50
            """,
            (now.strftime("%Y-%m-%dT%H:%M:%SZ"),),
        ).fetchall()

    for account in reminders:
        reminder_id = account["reminder_id"]
        tg_id = account["linked_tg_id"] or account["web_tg_id"] or account["reminder_tg_id"]
        if not tg_id or not can_send_telegram_link_offer(account):
            cancel_telegram_link_reminder(reminder_id)
            continue

        user = get_user_row(int(tg_id))
        if not user or int(user.get("is_blocked") or 0) == 1 or not user.get("sub_id"):
            cancel_telegram_link_reminder(reminder_id)
            continue

        try:
            send_telegram_link_email(account)
            mark_telegram_link_reminder_sent(reminder_id)
            logger.info(
                "Telegram link email sent account_id=%s tg_id=%s reminder_id=%s",
                account["id"],
                tg_id,
                reminder_id,
            )
        except Exception:
            logger.exception(
                "Telegram link email failed account_id=%s tg_id=%s reminder_id=%s",
                account["id"],
                tg_id,
                reminder_id,
            )


def email_reminder_cycle():
    if not is_mail_configured():
        return

    now = utc_now()
    telegram_link_reminder_cycle(now)

    with connect() as conn:
        accounts = conn.execute(
            f"""
            SELECT {account_select()}
            FROM web_accounts
            WHERE email_verified = 1
              AND COALESCE(email_reminders_enabled, 1) = 1
            """
        ).fetchall()

    for account in accounts:
        tg_id = account["linked_tg_id"] or account["web_tg_id"]
        if not tg_id:
            continue
        user = get_user_row(int(tg_id))
        if not user or int(user.get("is_blocked") or 0) == 1:
            continue
        expires_at = user.get("subscription_expires_at") or user.get("key_expires_at")
        expires_dt = parse_iso(expires_at)
        if not expires_dt:
            continue
        remaining_sec = (expires_dt - now).total_seconds()
        if remaining_sec <= 0:
            continue

        for reminder_kind, target_sec, window_sec in EMAIL_REMINDER_WINDOWS:
            if not (target_sec - window_sec <= remaining_sec <= target_sec + window_sec):
                continue
            if not reserve_email_reminder(account["id"], tg_id, expires_at, reminder_kind):
                continue
            try:
                send_expiry_email(account, user, reminder_kind, expires_dt, remaining_sec)
                logger.info(
                    "Web expiry email sent account_id=%s tg_id=%s kind=%s expires=%s",
                    account["id"],
                    tg_id,
                    reminder_kind,
                    expires_at,
                )
            except Exception:
                logger.exception(
                    "Web expiry email failed account_id=%s tg_id=%s kind=%s",
                    account["id"],
                    tg_id,
                    reminder_kind,
                )
                release_email_reminder(account["id"], tg_id, expires_at, reminder_kind)


def email_reminder_loop():
    time.sleep(15)
    while True:
        try:
            email_reminder_cycle()
        except Exception:
            logger.exception("Web email reminder cycle failed")
        time.sleep(EMAIL_REMINDER_INTERVAL_SECONDS)


def start_email_reminder_thread():
    global EMAIL_REMINDER_THREAD_STARTED
    if EMAIL_REMINDER_THREAD_STARTED:
        return
    EMAIL_REMINDER_THREAD_STARTED = True
    thread = threading.Thread(
        target=email_reminder_loop,
        daemon=True,
        name="kingline-web-email-reminders",
    )
    thread.start()


def set_location(account, location):
    web_tg_id, _username = ensure_web_user(account)
    user = get_user_row(web_tg_id) or {}
    if (user.get("tariff") or "").lower() != "premium":
        raise ValueError("premium_required")

    location = normalize_location(location)
    bot_db.set_location_pref(web_tg_id, location)
    user = get_user_row(web_tg_id) or {}
    sub_id = (user.get("sub_id") or "").strip()
    if sub_id:
        locator = DNSUserLocator(db_path=DB_PATH, domains=["sub.kingline.online"])
        locator.force_set_user_location(sub_id, location)
    return get_user_row(web_tg_id)


def generate_link_code(account):
    web_tg_id, _username = ensure_web_user(account)
    user = get_user_row(web_tg_id) or {}
    if not user.get("sub_id"):
        raise ValueError("subscription_required")
    if int(account["telegram_bonus_claimed"] or 0) == 1:
        raise ValueError("bonus_claimed")

    code = secrets.token_hex(8).upper()
    expires_at = (utc_now() + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM web_link_codes
            WHERE account_id = ? AND used_at IS NULL
            """,
            (account["id"],),
        )
        conn.execute(
            """
            INSERT INTO web_link_codes (code, account_id, bonus_days, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (code, account["id"], TELEGRAM_BONUS_DAYS, expires_at),
        )
        conn.commit()
    return {"code": code, "expiresAt": expires_at, "bonusDays": TELEGRAM_BONUS_DAYS}


def auth_errors(exc):
    value = str(exc)
    if value in {
        "premium_active",
        "trial_used",
        "insufficient_balance",
        "premium_required",
        "subscription_required",
        "bonus_claimed",
        "password_reset_invalid",
        "password_reset_expired",
        "password_reset_used",
        "email_link_invalid",
        "email_link_expired",
        "email_link_used",
        "email_link_too_many",
        "telegram_auth_failed",
        "cannot_remove_self",
        "cannot_remove_owner",
        "cannot_edit_owner",
        "owner_required",
        "permission_denied",
        "invalid_user",
        "weak_password",
        "mail_not_configured",
        "body_too_large",
        "csrf_failed",
        "invalid_content_type",
        "account_locked",
        "admin_2fa_required",
        "admin_2fa_invalid",
        "admin_2fa_expired",
        "admin_2fa_too_many",
        "admin_2fa_unavailable",
        "payment_not_found",
        "referral_empty",
        "referral_not_found",
        "referral_own_code",
        "referral_mutual",
        "referral_already_used",
    }:
        return value
    return "operation_failed"


class Handler(RequestDiagnosticsMixin, BaseHTTPRequestHandler):
    server_version = "KinglineProxyAuth/2.0"

    def log_message(self, fmt, *args):
        print("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.json_response({"ok": True})
        if path == "/api/auth/me":
            return self.handle_me()
        if path == "/api/support/messages":
            return self.handle_support_messages()
        if path == "/api/admin/bootstrap":
            return self.handle_admin_bootstrap()
        if path == "/api/admin/online":
            return self.handle_admin_online()
        if path == "/api/admin/analytics":
            return self.handle_admin_analytics()
        if path == "/api/admin/analytics/live-sites":
            return self.handle_admin_analytics_live_sites()
        if path == "/api/admin/analytics/user-insights":
            return self.handle_admin_analytics_user_insights()
        if path == "/api/admin/analytics/site-history":
            return self.handle_admin_analytics_site_history()
        if path == "/api/admin/analytics/sessions":
            return self.handle_admin_analytics_sessions()
        if path == "/api/admin/payments/analytics":
            return self.handle_admin_payments_analytics()
        if path == "/api/admin/audit":
            return self.handle_admin_audit()
        if path == "/api/admin/referrals":
            return self.handle_admin_referrals()
        if path == "/api/admin/support/messages":
            return self.handle_admin_support_messages()
        if path == "/api/auth/magic":
            return self.redirect_home(error="magic_link_disabled")
        if path == "/api/auth/verify-email":
            return self.handle_email_verify_callback()
        if path == "/api/auth/verify-registration":
            return self.handle_registration_verify_callback()
        if path == "/api/auth/verify-email-link":
            return self.handle_email_link_verify_callback()
        return self.json_response({"ok": False, "error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        client_ip = self.client_ip()
        if not self.validate_post_request(path):
            return
        if path in SENSITIVE_POST_RATE_LIMIT_PATHS and is_rate_limited(client_ip, path):
            return self.json_response(
                {"ok": False, "error": "too_many_attempts"},
                HTTPStatus.TOO_MANY_REQUESTS,
            )

        routes = {
            "/api/auth/magic-link": self.handle_magic_link_disabled,
            "/api/auth/register": self.handle_register,
            "/api/auth/confirm-registration": self.handle_confirm_registration,
            "/api/auth/telegram-webapp": self.handle_telegram_webapp_auth,
            "/api/auth/password-reset": self.handle_password_reset_request,
            "/api/auth/password-reset-confirm": self.handle_password_reset_confirm,
            "/api/auth/login": self.handle_login,
            "/api/auth/logout": self.handle_logout,
            "/api/account/trial": self.handle_trial,
            "/api/account/premium": self.handle_premium,
            "/api/account/job-status": self.handle_job_status,
            "/api/account/promo": self.handle_promo,
            "/api/account/location": self.handle_location,
            "/api/account/email-reminders": self.handle_email_reminders,
            "/api/account/email-announcements": self.handle_email_announcements,
            "/api/account/email-link/start": self.handle_email_link_start,
            "/api/account/email-link/confirm": self.handle_email_link_confirm,
            "/api/account/topup": self.handle_topup,
            "/api/account/check-payment": self.handle_check_payment,
            "/api/account/link-code": self.handle_link_code,
            "/api/account/referral-code": self.handle_referral_code,
            "/api/support/send": self.handle_support_send,
            "/api/support/typing": self.handle_support_typing,
            "/api/admin/2fa/send": self.handle_admin_2fa_send,
            "/api/admin/2fa/verify": self.handle_admin_2fa_verify,
            "/api/admin/user/balance": self.handle_admin_user_balance,
            "/api/admin/user/block": self.handle_admin_user_block,
            "/api/admin/user/message": self.handle_admin_user_message,
            "/api/admin/user/delete": self.handle_admin_user_delete,
            "/api/admin/dns/location": self.handle_admin_dns_location,
            "/api/admin/support/send": self.handle_admin_support_send,
            "/api/admin/support/typing": self.handle_admin_support_typing,
            "/api/admin/audit/event": self.handle_admin_audit_event,
            "/api/admin/broadcast/send": self.handle_admin_broadcast_send,
            "/api/admin/admins/add": self.handle_admin_add_admin,
            "/api/admin/admins/remove": self.handle_admin_remove_admin,
            "/api/admin/admins/permissions": self.handle_admin_permissions,
            "/api/admin/promos/create": self.handle_admin_promo_create,
            "/api/admin/promos/delete": self.handle_admin_promo_delete,
        }
        handler = routes.get(path)
        if handler:
            return handler()
        return self.json_response({"ok": False, "error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", f"Content-Type, {POST_SECURITY_HEADER}")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.end_headers()

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return self.client_address[0]

    def is_https(self):
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def validate_post_request(self, path):
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != JSON_CONTENT_TYPE:
            return self.json_response(
                {"ok": False, "error": "invalid_content_type"},
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )

        if self.headers.get(POST_SECURITY_HEADER) != POST_SECURITY_VALUE:
            return self.json_response(
                {"ok": False, "error": "csrf_failed"},
                HTTPStatus.FORBIDDEN,
            )

        origin = self.headers.get("Origin") or ""
        referer = self.headers.get("Referer") or ""
        if origin and not self.is_allowed_origin(origin):
            return self.json_response(
                {"ok": False, "error": "csrf_failed"},
                HTTPStatus.FORBIDDEN,
            )
        if not origin and referer and not self.is_allowed_origin(referer):
            return self.json_response(
                {"ok": False, "error": "csrf_failed"},
                HTTPStatus.FORBIDDEN,
            )

        sec_fetch_site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if sec_fetch_site == "cross-site":
            return self.json_response(
                {"ok": False, "error": "csrf_failed"},
                HTTPStatus.FORBIDDEN,
            )
        return True

    def is_allowed_origin(self, value):
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return False
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        site = urlparse(public_site_url())
        allowed = {
            "https://kingline.online",
            "https://www.kingline.online",
        }
        if site.scheme and site.netloc:
            allowed.add(f"{site.scheme.lower()}://{site.netloc.lower()}")
        return origin in allowed

    def read_json(self):
        length_raw = self.headers.get("Content-Length") or "0"
        try:
            length = int(length_raw)
        except ValueError:
            raise ValueError("invalid_length")
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("body_too_large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("invalid_json")
        if not isinstance(payload, dict):
            raise ValueError("invalid_json")
        return payload

    def session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel else ""

    def set_session_cookie(self, token):
        secure = "; Secure" if self.is_https() else ""
        self.send_header(
            "Set-Cookie",
            (
                f"{COOKIE_NAME}={token}; Max-Age={SESSION_DAYS * 24 * 60 * 60}; "
                f"Path=/; HttpOnly; SameSite=Lax{secure}"
            ),
        )

    def clear_session_cookie(self):
        secure = "; Secure" if self.is_https() else ""
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax{secure}",
        )

    def redirect_home(self, token=None, error=None):
        location = "/"
        if error:
            location = f"/?{urlencode({'auth_error': error})}"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if token:
            self.set_session_cookie(token)
        self.end_headers()

    def json_response(self, payload, status=HTTPStatus.OK, extra_headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for name, value in extra_headers:
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return

    def require_account(self):
        account, token_digest = get_account_by_session(self.session_token())
        if not account:
            self.json_response({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return None, None
        return account, token_digest

    def auth_payload(self, account_row):
        web_tg_id, _username = ensure_web_user(account_row)
        user = get_user_row(web_tg_id)
        subscription = public_subscription(user)
        subscription["telegramBonusClaimed"] = bool(
            account_row["telegram_bonus_claimed"] or 0
        )
        subscription["linkedTgId"] = account_row["linked_tg_id"]
        subscription["referrals"] = public_referrals_for(web_tg_id)
        return {
            "ok": True,
            "account": public_account(account_row),
            "subscription": subscription,
            "topupAmounts": list(TOPUP_AMOUNTS),
        }

    def create_session(self, account_id):
        token = secrets.token_urlsafe(32)
        token_digest = hash_token(token)
        expires_at = (utc_now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO web_sessions (
                    token_hash,
                    account_id,
                    expires_at,
                    last_seen_at,
                    user_agent,
                    ip
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token_digest,
                    account_id,
                    expires_at,
                    iso_now(),
                    self.headers.get("User-Agent", "")[:300],
                    self.client_ip(),
                ),
            )
            conn.commit()
        return token

    def json_auth_with_session(self, account, status=HTTPStatus.OK):
        token = self.create_session(account["id"])
        data = json.dumps(self.auth_payload(account), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.set_session_cookie(token)
        self.end_headers()
        self.wfile.write(data)

    def validate_auth_input(self, payload, require_name=False):
        email = normalize_email(payload.get("email"))
        password = str(payload.get("password") or "")
        name = str(payload.get("name") or "").strip()

        if not EMAIL_RE.match(email):
            return None, None, None, "invalid_email"
        if len(password) < 8:
            return None, None, None, "weak_password"
        if require_name and len(name) < 2:
            return None, None, None, "invalid_name"
        return email, password, name, None

    def handle_password_auth_disabled(self):
        return self.json_response(
            {"ok": False, "error": "password_auth_disabled"},
            HTTPStatus.GONE,
        )

    def handle_magic_link_disabled(self):
        return self.json_response(
            {"ok": False, "error": "magic_link_disabled"},
            HTTPStatus.GONE,
        )

    def handle_magic_link_request(self):
        try:
            payload = self.read_json()
        except ValueError as exc:
            return self.json_response({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        email = normalize_email(payload.get("email"))
        name = str(payload.get("name") or "").strip()

        if not EMAIL_RE.match(email):
            return self.json_response({"ok": False, "error": "invalid_email"}, HTTPStatus.BAD_REQUEST)
        if name and len(name) < 2:
            return self.json_response({"ok": False, "error": "invalid_name"}, HTTPStatus.BAD_REQUEST)
        if is_rate_limited(self.client_ip(), f"/api/auth/magic-link:{email}"):
            return self.json_response(
                {"ok": False, "error": "too_many_attempts"},
                HTTPStatus.TOO_MANY_REQUESTS,
            )
        if not is_mail_configured():
            return self.json_response(
                {"ok": False, "error": "mail_not_configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

        magic_link, expires_at = create_magic_link(
            email,
            name,
            self.client_ip(),
            self.headers.get("User-Agent", ""),
        )
        try:
            send_magic_link_email(email, magic_link, expires_at)
        except RuntimeError as exc:
            logger.exception("Magic link email is not configured")
            return self.json_response(
                {"ok": False, "error": str(exc)},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Magic link email failed")
            return self.json_response(
                {"ok": False, "error": "email_send_failed"},
                HTTPStatus.BAD_GATEWAY,
            )

        return self.json_response(
            {
                "ok": True,
                "message": "magic_link_sent",
                "expiresAt": expires_at,
            }
        )

    def handle_magic_callback(self):
        query = parse_qs(urlparse(self.path).query)
        token = (query.get("token") or [""])[0]
        if not token:
            return self.redirect_home(error="magic_link_invalid")
        try:
            account = consume_magic_link(token)
            token = self.create_session(account["id"])
            return self.redirect_home(token=token)
        except ValueError as exc:
            return self.redirect_home(error=str(exc))
        except Exception:
            logger.exception("Magic link callback failed")
            return self.redirect_home(error="magic_link_invalid")

    def handle_email_verify_callback(self):
        query = parse_qs(urlparse(self.path).query)
        token = (query.get("token") or [""])[0]
        if not token:
            return self.redirect_home(error="email_verify_invalid")
        try:
            account = consume_email_verification(token)
            token = self.create_session(account["id"])
            return self.redirect_home(token=token)
        except ValueError as exc:
            return self.redirect_home(error=str(exc))
        except Exception:
            logger.exception("Email verification callback failed")
            return self.redirect_home(error="email_verify_invalid")

    def handle_registration_verify_callback(self):
        query = parse_qs(urlparse(self.path).query)
        token = (query.get("token") or [""])[0]
        if not token:
            return self.redirect_home(error="registration_verify_invalid")
        try:
            account = consume_pending_registration_by_token(token)
            token = self.create_session(account["id"])
            return self.redirect_home(token=token)
        except ValueError as exc:
            return self.redirect_home(error=str(exc))
        except Exception:
            logger.exception("Registration verification callback failed")
            return self.redirect_home(error="registration_verify_invalid")

    def handle_email_link_verify_callback(self):
        query = parse_qs(urlparse(self.path).query)
        token = (query.get("token") or [""])[0]
        if not token:
            return self.redirect_home(error="email_link_invalid")
        try:
            account = consume_email_link_by_token(token)
            token = self.create_session(account["id"])
            return self.redirect_home(token=token)
        except ValueError as exc:
            return self.redirect_home(error=str(exc))
        except Exception:
            logger.exception("Email link callback failed")
            return self.redirect_home(error="email_link_invalid")

    def handle_register(self):
        try:
            payload = self.read_json()
        except ValueError as exc:
            return self.json_response({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        email, password, name, error = self.validate_auth_input(payload, require_name=True)
        if error:
            return self.json_response({"ok": False, "error": error}, HTTPStatus.BAD_REQUEST)
        if not is_mail_configured():
            return self.json_response(
                {"ok": False, "error": "mail_not_configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

        password_hash = hash_password(password)
        try:
            verify_link, code, expires_at = create_pending_registration(
                email,
                name,
                password_hash,
                self.client_ip(),
                self.headers.get("User-Agent", ""),
            )
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": str(exc)},
                HTTPStatus.CONFLICT,
            )
        try:
            send_registration_email(email, code, verify_link, expires_at)
        except Exception:
            logger.exception("Registration confirmation email failed")
            delete_pending_registration(email)
            return self.json_response(
                {"ok": False, "error": "email_send_failed"},
                HTTPStatus.BAD_GATEWAY,
            )

        return self.json_response(
            {
                "ok": True,
                "registrationPending": True,
                "message": "registration_confirmation_sent",
                "email": email,
                "expiresAt": expires_at,
            },
            HTTPStatus.ACCEPTED,
        )

    def handle_confirm_registration(self):
        try:
            payload = self.read_json()
        except ValueError as exc:
            return self.json_response({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        email = normalize_email(payload.get("email"))
        code = str(payload.get("code") or "").strip().replace(" ", "")
        if not EMAIL_RE.match(email) or not re.fullmatch(r"\d{6}", code):
            return self.json_response(
                {"ok": False, "error": "registration_verify_invalid"},
                HTTPStatus.BAD_REQUEST,
            )

        try:
            account = consume_pending_registration_by_code(email, code)
            return self.json_auth_with_session(account, HTTPStatus.CREATED)
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": str(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Registration code confirmation failed")
            return self.json_response(
                {"ok": False, "error": "registration_verify_invalid"},
                HTTPStatus.BAD_REQUEST,
            )


    def handle_telegram_webapp_auth(self):
        try:
            payload = self.read_json()
            tg_user = validate_telegram_webapp_init_data(payload.get("initData"))
            account = get_or_create_telegram_web_account(
                tg_user,
                self.client_ip(),
                self.headers.get("User-Agent", ""),
            )
            if not account:
                raise ValueError("telegram_auth_failed")
            return self.json_auth_with_session(account)
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": str(exc)},
                HTTPStatus.UNAUTHORIZED,
            )
        except Exception:
            logger.exception("Telegram WebApp auth failed")
            return self.json_response(
                {"ok": False, "error": "telegram_auth_failed"},
                HTTPStatus.UNAUTHORIZED,
            )

    def handle_password_reset_request(self):
        try:
            payload = self.read_json()
        except ValueError as exc:
            return self.json_response({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        email = normalize_email(payload.get("email"))
        if not EMAIL_RE.match(email):
            return self.json_response(
                {"ok": False, "error": "invalid_email"},
                HTTPStatus.BAD_REQUEST,
            )
        if is_rate_limited(self.client_ip(), f"/api/auth/password-reset:{email}"):
            return self.json_response(
                {"ok": False, "error": "too_many_attempts"},
                HTTPStatus.TOO_MANY_REQUESTS,
            )
        if not is_mail_configured():
            return self.json_response(
                {"ok": False, "error": "mail_not_configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

        with connect() as conn:
            account = conn.execute(
                f"""
                SELECT {account_select()}
                FROM web_accounts
                WHERE email = ?
                  AND email_verified = 1
                LIMIT 1
                """,
                (email,),
            ).fetchone()

        if account:
            reset_link, expires_at = create_password_reset(
                account["id"],
                account["email"],
                self.client_ip(),
                self.headers.get("User-Agent", ""),
            )
            try:
                send_password_reset_email(account["email"], reset_link, expires_at)
            except Exception:
                logger.exception("Password reset email failed")
                return self.json_response(
                    {"ok": False, "error": "email_send_failed"},
                    HTTPStatus.BAD_GATEWAY,
                )

        return self.json_response(
            {
                "ok": True,
                "message": "password_reset_sent",
            },
            HTTPStatus.ACCEPTED,
        )

    def handle_password_reset_confirm(self):
        try:
            payload = self.read_json()
        except ValueError as exc:
            return self.json_response({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        token = str(payload.get("token") or "").strip()
        password = str(payload.get("password") or "")
        if not token:
            return self.json_response(
                {"ok": False, "error": "password_reset_invalid"},
                HTTPStatus.BAD_REQUEST,
            )
        try:
            account = consume_password_reset(token, password)
            return self.json_auth_with_session(account)
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Password reset confirmation failed")
            return self.json_response(
                {"ok": False, "error": "password_reset_invalid"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_login(self):
        try:
            payload = self.read_json()
        except ValueError as exc:
            return self.json_response({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

        email, password, _name, error = self.validate_auth_input(payload)
        if error:
            return self.json_response({"ok": False, "error": "invalid_credentials"}, HTTPStatus.UNAUTHORIZED)
        if login_lock_for_email(email):
            return self.json_response(
                {"ok": False, "error": "account_locked"},
                HTTPStatus.TOO_MANY_REQUESTS,
            )

        with connect() as conn:
            row = conn.execute(
                """
                SELECT id, email, name, password_hash, linked_tg_id, web_tg_id,
                       email_verified, telegram_bonus_claimed,
                       COALESCE(email_reminders_enabled, 1) AS email_reminders_enabled,
                       COALESCE(email_announcements_enabled, 1) AS email_announcements_enabled
                FROM web_accounts
                WHERE email = ?
                LIMIT 1
                """,
                (email,),
            ).fetchone()

            if not row or not verify_password(password, row["password_hash"]):
                record_login_failure(
                    email,
                    self.client_ip(),
                    self.headers.get("User-Agent", ""),
                )
                return self.json_response(
                    {"ok": False, "error": "invalid_credentials"},
                    HTTPStatus.UNAUTHORIZED,
                )

            if not int(row["email_verified"] or 0):
                if not is_mail_configured():
                    return self.json_response(
                        {"ok": False, "error": "mail_not_configured"},
                        HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                verify_link, expires_at = create_email_verification(
                    row["id"],
                    row["email"],
                    self.client_ip(),
                    self.headers.get("User-Agent", ""),
                )
                try:
                    send_verification_email(row["email"], verify_link, expires_at)
                except Exception:
                    logger.exception("Login verification email failed")
                    return self.json_response(
                        {"ok": False, "error": "email_send_failed"},
                        HTTPStatus.BAD_GATEWAY,
                    )
                return self.json_response(
                    {
                        "ok": False,
                        "error": "email_not_verified",
                        "verificationSent": True,
                        "expiresAt": expires_at,
                    },
                    HTTPStatus.FORBIDDEN,
                )

            web_tg_id = row["web_tg_id"] or -int(row["id"])
            conn.execute(
                """
                UPDATE web_accounts
                SET last_login_at = ?, updated_at = ?, web_tg_id = ?
                WHERE id = ?
                """,
                (iso_now(), iso_now(), web_tg_id, row["id"]),
            )
            conn.commit()
            account = conn.execute(
                f"SELECT {account_select()} FROM web_accounts WHERE id = ?",
                (row["id"],),
            ).fetchone()

        clear_login_failures(email)
        token = self.create_session(account["id"])
        data = json.dumps(self.auth_payload(account), ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.set_session_cookie(token)
        self.end_headers()
        self.wfile.write(data)

    def handle_me(self):
        account, _token_digest = get_account_by_session(self.session_token())
        if not account:
            return self.json_response({"ok": True, "account": None, "subscription": None})
        return self.json_response(self.auth_payload(account))

    def handle_logout(self):
        _account, token_digest = get_account_by_session(self.session_token())
        if token_digest:
            with connect() as conn:
                conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (token_digest,))
                conn.commit()

        data = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.clear_session_cookie()
        self.end_headers()
        self.wfile.write(data)

    def handle_trial(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            validate_trial_available(account)
            job = create_account_job(account, "trial")
            data = self.auth_payload(account)
            data["job"] = job
            return self.json_response(data, HTTPStatus.ACCEPTED)
        except ValueError as exc:
            return self.json_response({"ok":False,"error":auth_errors(exc)},HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            logger.exception("Web trial failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_premium(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            job = create_account_job(account, "premium")
            data = self.auth_payload(account)
            data["job"] = job
            return self.json_response(data, HTTPStatus.ACCEPTED)
        except Exception as exc:
            logger.exception("Web premium failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_job_status(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            job_id = str(payload.get("jobId") or "").strip()
            job = get_account_job(account["id"], job_id)
            if not job:
                return self.json_response(
                    {"ok": False, "error": "job_not_found"},
                    HTTPStatus.NOT_FOUND,
                )
            account = get_account_by_id(account["id"])
            data = self.auth_payload(account)
            data["job"] = job
            return self.json_response(data)
        except Exception as exc:
            logger.exception("Web job status failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_email_reminders(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            enabled = bool(payload.get("enabled"))
            account = set_email_reminders(account, enabled)
            return self.json_response(self.auth_payload(account))
        except Exception as exc:
            logger.exception("Web email reminders toggle failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_email_announcements(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            enabled = bool(payload.get("enabled"))
            account = set_email_announcements(account, enabled)
            return self.json_response(self.auth_payload(account))
        except Exception as exc:
            logger.exception("Web email announcements toggle failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_email_link_start(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            email, password, name, error = self.validate_auth_input(
                payload,
                require_name=True,
            )
            if error:
                return self.json_response(
                    {"ok": False, "error": error},
                    HTTPStatus.BAD_REQUEST,
                )
            if not is_mail_configured():
                return self.json_response(
                    {"ok": False, "error": "mail_not_configured"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            if is_rate_limited(self.client_ip(), f"/api/account/email-link:{email}"):
                return self.json_response(
                    {"ok": False, "error": "too_many_attempts"},
                    HTTPStatus.TOO_MANY_REQUESTS,
                )

            link, code, expires_at = create_email_link_request(
                account,
                email,
                normalize_name(name, email),
                hash_password(password),
                self.client_ip(),
                self.headers.get("User-Agent", ""),
            )
            send_email_link_email(email, code, link, expires_at)
            return self.json_response(
                {
                    "ok": True,
                    "emailLinkPending": True,
                    "email": email,
                    "expiresAt": expires_at,
                },
                HTTPStatus.ACCEPTED,
            )
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": str(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Email link start failed")
            return self.json_response(
                {"ok": False, "error": "email_send_failed"},
                HTTPStatus.BAD_GATEWAY,
            )

    def handle_email_link_confirm(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            email = normalize_email(payload.get("email"))
            code = str(payload.get("code") or "").strip().replace(" ", "")
            if not EMAIL_RE.match(email) or not re.fullmatch(r"\d{6}", code):
                return self.json_response(
                    {"ok": False, "error": "email_link_invalid"},
                    HTTPStatus.BAD_REQUEST,
                )
            account = consume_email_link_by_code(account["id"], email, code)
            return self.json_response(self.auth_payload(account))
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": str(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Email link confirm failed")
            return self.json_response(
                {"ok": False, "error": "email_link_invalid"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_support_messages(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            after_id = int((query.get("after_id") or ["0"])[0] or 0)
            return self.json_response(
                {
                    "ok": True,
                    "messages": list_support_messages(account["id"], after_id=after_id),
                    "typing": support_typing_state(account["id"], "user"),
                }
            )
        except Exception:
            logger.exception("Web support messages failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_support_send(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            text = str(payload.get("text") or "").strip()
            attachments = payload.get("attachments") or []
            if not text and not attachments:
                return self.json_response(
                    {"ok": False, "error": "message_required"},
                    HTTPStatus.BAD_REQUEST,
                )
            message = create_support_message(
                account["id"],
                "user",
                text,
                attachments=attachments,
                sender_name=account["name"] or account["email"],
            )
            notify_admin_about_support_async(account, message)
            return self.json_response(
                {
                    "ok": True,
                    "message": message,
                    "messages": list_support_messages(account["id"]),
                    "typing": support_typing_state(account["id"], "user"),
                }
            )
        except Exception:
            logger.exception("Web support send failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_support_reaction(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            set_support_reaction(
                account["id"],
                int(payload.get("messageId") or 0),
                payload.get("reaction"),
            )
            return self.json_response(
                {
                    "ok": True,
                    "messages": list_support_messages(account["id"]),
                    "typing": support_typing_state(account["id"], "user"),
                }
            )
        except Exception:
            logger.exception("Web support reaction failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_support_typing(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            set_support_typing(account["id"], "user")
            return self.json_response({"ok": True})
        except Exception:
            logger.exception("Web support typing failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def require_admin_account(self, require_2fa=True):
        account, token_digest = self.require_account()
        if not account:
            return None
        if not is_admin_account(account):
            self.json_response({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return None
        if require_2fa and not admin_session_verified(token_digest):
            self.json_response(
                {"ok": False, "error": "admin_2fa_required"},
                HTTPStatus.FORBIDDEN,
            )
            return None
        return account

    def require_admin_permission(self, permission):
        account = self.require_admin_account()
        if not account:
            return None
        if not admin_has_permission(account, permission):
            self.json_response(
                {"ok": False, "error": "permission_denied"},
                HTTPStatus.FORBIDDEN,
            )
            return None
        return account

    def require_owner_admin(self):
        account = self.require_admin_account()
        if not account:
            return None
        if not is_owner_admin(account):
            self.json_response(
                {"ok": False, "error": "owner_required"},
                HTTPStatus.FORBIDDEN,
            )
            return None
        return account

    def handle_admin_bootstrap(self):
        account, token_digest = self.require_account()
        if not account:
            return
        if not is_admin_account(account):
            return self.json_response({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
        if not admin_session_verified(token_digest):
            return self.json_response(admin_2fa_bootstrap(account, token_digest))
        try:
            return self.json_response(admin_bootstrap_payload(account))
        except Exception:
            logger.exception("Admin bootstrap failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_2fa_send(self):
        account, token_digest = self.require_account()
        if not account:
            return
        if not is_admin_account(account):
            return self.json_response({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
        if admin_session_verified(token_digest):
            return self.json_response(admin_bootstrap_payload(account))
        try:
            challenge = create_admin_2fa_challenge(
                account,
                token_digest,
                self.client_ip(),
                self.headers.get("User-Agent", ""),
            )
            return self.json_response(
                {
                    "ok": True,
                    "admin2faRequired": True,
                    "sent": True,
                    "telegramHint": challenge["sentTo"],
                    "expiresAt": challenge["expiresAt"],
                }
            )
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Admin 2FA send failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_2fa_verify(self):
        account, token_digest = self.require_account()
        if not account:
            return
        if not is_admin_account(account):
            return self.json_response({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
        try:
            payload = self.read_json()
            verify_admin_2fa_challenge(account, token_digest, payload.get("code"))
            log_admin_action(
                account,
                "admin_2fa_verified",
                target=f"account:{account['id']}",
                summary={},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Admin 2FA verify failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_online(self):
        account = self.require_admin_account()
        if not account:
            return
        try:
            return self.json_response(admin_online_snapshot())
        except Exception:
            logger.exception("Admin online snapshot failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_analytics(self):
        account = self.require_admin_permission("analytics")
        if not account:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            return self.json_response(admin_analytics_payload(query))
        except Exception:
            logger.exception("Admin analytics failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_analytics_live_sites(self):
        account = self.require_admin_permission("analytics")
        if not account:
            return
        try:
            return self.json_response(admin_live_sites_payload())
        except Exception:
            logger.exception("Admin live sites failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_analytics_user_insights(self):
        account = self.require_admin_permission("analytics")
        if not account:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            return self.json_response(admin_user_insights_payload(query))
        except ValueError:
            return self.json_response(
                {"ok": False, "error": "invalid_username"},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Admin user insights failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_analytics_site_history(self):
        account = self.require_admin_permission("analytics")
        if not account:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            return self.json_response(admin_user_site_history_payload(query))
        except ValueError:
            return self.json_response(
                {"ok": False, "error": "invalid_username"},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Admin site history failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_analytics_sessions(self):
        account = self.require_admin_permission("analytics")
        if not account:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            username = (query.get("username") or [""])[0]
            return self.json_response(online_session_history(username))
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": str(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Admin analytics sessions failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_payments_analytics(self):
        account = self.require_admin_permission("payments")
        if not account:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            return self.json_response(admin_payments_analytics_payload(query))
        except Exception:
            logger.exception("Admin payments analytics failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_audit(self):
        account = self.require_owner_admin()
        if not account:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            return self.json_response(admin_audit_payload(query))
        except Exception:
            logger.exception("Admin audit list failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_audit_event(self):
        account = self.require_admin_account()
        if not account:
            return
        try:
            payload = self.read_json()
            action = str(payload.get("action") or "admin_open").strip()[:80]
            target = str(payload.get("target") or "").strip()[:160]
            summary = payload.get("summary") or {}
            if not isinstance(summary, dict):
                summary = {}
            log_admin_action(
                account,
                action,
                target=target,
                summary=summary,
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response({"ok": True})
        except Exception:
            logger.exception("Admin audit event failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_referrals(self):
        account = self.require_admin_account()
        if not account:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            tg_id = int((query.get("tg_id") or ["0"])[0] or 0)
            if not tg_id:
                return self.json_response(
                    {"ok": False, "error": "invalid_user"},
                    HTTPStatus.BAD_REQUEST,
                )
            return self.json_response({"ok": True, "referrals": admin_referrals_for(tg_id)})
        except Exception:
            logger.exception("Admin referrals failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_user_balance(self):
        account = self.require_admin_permission("balance")
        if not account:
            return
        try:
            payload = self.read_json()
            tg_id = int(payload.get("tgId") or 0)
            delta = int(payload.get("delta") or 0)
            if not tg_id or delta == 0:
                return self.json_response(
                    {"ok": False, "error": "invalid_amount"},
                    HTTPStatus.BAD_REQUEST,
                )
            BillingDB.change_balance(tg_id, delta)
            log_admin_action(
                account,
                "balance_change",
                target=f"tg:{tg_id}",
                summary={"delta": delta},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except Exception:
            logger.exception("Admin balance change failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_user_block(self):
        account = self.require_admin_permission("block")
        if not account:
            return
        try:
            payload = self.read_json()
            tg_id = int(payload.get("tgId") or 0)
            blocked = bool(payload.get("blocked"))
            if not tg_id:
                return self.json_response(
                    {"ok": False, "error": "invalid_user"},
                    HTTPStatus.BAD_REQUEST,
                )
            bot_db.set_blocked(tg_id, blocked)
            log_admin_action(
                account,
                "user_block",
                target=f"tg:{tg_id}",
                summary={"blocked": blocked},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except Exception:
            logger.exception("Admin block toggle failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_user_message(self):
        account = self.require_admin_permission("message")
        if not account:
            return
        try:
            payload = self.read_json()
            channel = str(payload.get("channel") or "telegram").strip().lower()
            text = str(payload.get("text") or "").strip()
            subject = str(payload.get("subject") or "Сообщение").strip()[:120] or "Сообщение"
            tg_id = int(payload.get("tgId") or 0)
            email = normalize_email(payload.get("email"))
            if not text:
                return self.json_response(
                    {"ok": False, "error": "message_required"},
                    HTTPStatus.BAD_REQUEST,
                )
            if channel == "telegram":
                if tg_id <= 0:
                    return self.json_response(
                        {"ok": False, "error": "telegram_unavailable"},
                        HTTPStatus.BAD_REQUEST,
                    )
                send_telegram_message(
                    tg_id,
                    escape(text),
                )
            elif channel == "email":
                if not email:
                    return self.json_response(
                        {"ok": False, "error": "email_unavailable"},
                        HTTPStatus.BAD_REQUEST,
                    )
                try:
                    send_mail(email, subject, text, admin_message_email_html(text, subject))
                except RuntimeError as exc:
                    logger.exception("Admin personal email not configured")
                    return self.json_response(
                        {"ok": False, "error": auth_errors(exc)},
                        HTTPStatus.BAD_REQUEST,
                    )
                except Exception:
                    logger.exception("Admin personal email failed")
                    return self.json_response(
                        {"ok": False, "error": "email_send_failed"},
                        HTTPStatus.BAD_REQUEST,
                    )
            else:
                return self.json_response(
                    {"ok": False, "error": "invalid_channel"},
                    HTTPStatus.BAD_REQUEST,
                )
            log_admin_action(
                account,
                "user_message",
                target=f"{channel}:{tg_id or email}",
                summary={"channel": channel, "subject": subject if channel == "email" else "", "length": len(text)},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except Exception:
            logger.exception("Admin personal message failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_user_delete(self):
        account = self.require_admin_permission("delete_users")
        if not account:
            return
        try:
            payload = self.read_json()
            tg_id = int(payload.get("tgId") or 0)
            web_account_id = int(payload.get("webAccountId") or 0)
            delete_user_records(account, tg_id=tg_id, web_account_id=web_account_id)
            log_admin_action(
                account,
                "user_delete",
                target=f"tg:{tg_id}" if tg_id else f"web:{web_account_id}",
                summary={"tgId": tg_id, "webAccountId": web_account_id},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Admin user delete failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_dns_location(self):
        account = self.require_admin_permission("dns")
        if not account:
            return
        try:
            payload = self.read_json()
            location = normalize_location(payload.get("location"))
            sub_id = str(payload.get("subId") or "").strip()
            tg_id = int(payload.get("tgId") or 0)
            if not sub_id and tg_id:
                user = get_user_row(tg_id) or {}
                sub_id = str(user.get("sub_id") or "").strip()
            if not sub_id:
                return self.json_response(
                    {"ok": False, "error": "invalid_user"},
                    HTTPStatus.BAD_REQUEST,
                )
            if tg_id:
                bot_db.set_location_pref(tg_id, location)
            locator = DNSUserLocator(db_path=DB_PATH, domains=["sub.kingline.online"])
            locator.force_set_user_location(sub_id, location)
            log_admin_action(
                account,
                "dns_location",
                target=f"tg:{tg_id}" if tg_id else f"sub:{sub_id[:24]}",
                summary={"location": location},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except Exception:
            logger.exception("Admin DNS location failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_support_messages(self):
        account = self.require_admin_permission("support")
        if not account:
            return
        try:
            query = parse_qs(urlparse(self.path).query)
            account_id = int((query.get("account_id") or ["0"])[0] or 0)
            after_id = int((query.get("after_id") or ["0"])[0] or 0)
            target = get_support_account(account_id)
            if not target:
                return self.json_response(
                    {"ok": False, "error": "invalid_user"},
                    HTTPStatus.BAD_REQUEST,
                )
            return self.json_response(
                {
                    "ok": True,
                    "account": {
                        "accountId": target["id"],
                        "email": target["email"] or "",
                        "name": target["name"] or target["email"] or "",
                        "tgId": target["linked_tg_id"] or target["web_tg_id"],
                    },
                    "messages": list_support_messages(account_id, after_id=after_id),
                    "typing": support_typing_state(account_id, "admin"),
                }
            )
        except Exception:
            logger.exception("Admin support messages failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_support_send(self):
        account = self.require_admin_permission("support")
        if not account:
            return
        try:
            payload = self.read_json()
            account_id = int(payload.get("accountId") or 0)
            text = str(payload.get("text") or "").strip()
            attachments = payload.get("attachments") or []
            target = get_support_account(account_id)
            if not target:
                return self.json_response(
                    {"ok": False, "error": "invalid_user"},
                    HTTPStatus.BAD_REQUEST,
                )
            message = create_support_message(
                account_id,
                "admin",
                text,
                attachments=attachments,
                sender_name=SUPPORT_ADMIN_NAME,
            )
            log_admin_action(
                account,
                "support_send",
                target=f"account:{account_id}",
                summary={"length": len(text), "attachments": len(attachments or [])},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(
                {
                    "ok": True,
                    "message": message,
                    "messages": list_support_messages(account_id),
                    "support": admin_list_support_conversations(),
                    "typing": support_typing_state(account_id, "admin"),
                }
            )
        except Exception:
            logger.exception("Admin support send failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_support_reaction(self):
        account = self.require_admin_permission("support")
        if not account:
            return
        try:
            payload = self.read_json()
            account_id = int(payload.get("accountId") or 0)
            set_support_reaction(
                account_id,
                int(payload.get("messageId") or 0),
                payload.get("reaction"),
            )
            return self.json_response(
                {
                    "ok": True,
                    "messages": list_support_messages(account_id),
                    "support": admin_list_support_conversations(),
                    "typing": support_typing_state(account_id, "admin"),
                }
            )
        except Exception:
            logger.exception("Admin support reaction failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_support_typing(self):
        account = self.require_admin_permission("support")
        if not account:
            return
        try:
            payload = self.read_json()
            account_id = int(payload.get("accountId") or 0)
            if not get_support_account(account_id):
                return self.json_response(
                    {"ok": False, "error": "invalid_user"},
                    HTTPStatus.BAD_REQUEST,
                )
            set_support_typing(account_id, "admin")
            return self.json_response({"ok": True})
        except Exception:
            logger.exception("Admin support typing failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_broadcast_send(self):
        account = self.require_admin_permission("broadcast")
        if not account:
            return
        try:
            payload = self.read_json()
            result = admin_send_broadcast(
                payload.get("subject"),
                payload.get("text"),
                payload.get("channel"),
            )
            log_admin_action(
                account,
                "broadcast_send",
                target=str(payload.get("channel") or "both"),
                summary={
                    "subject": str(payload.get("subject") or "")[:120],
                    "length": len(str(payload.get("text") or "")),
                    "result": result,
                },
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            response = admin_bootstrap_payload(account)
            response["broadcast"] = result
            return self.json_response(response)
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Admin broadcast failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_add_admin(self):
        account = self.require_owner_admin()
        if not account:
            return
        try:
            payload = self.read_json()
            email = normalize_email(payload.get("email"))
            tg_raw = str(payload.get("tgId") or "").strip()
            tg_id = int(tg_raw) if tg_raw and tg_raw.lstrip("-").isdigit() else None
            if email and not EMAIL_RE.match(email):
                return self.json_response(
                    {"ok": False, "error": "invalid_email"},
                    HTTPStatus.BAD_REQUEST,
                )
            if not email and not tg_id:
                return self.json_response(
                    {"ok": False, "error": "invalid_admin"},
                    HTTPStatus.BAD_REQUEST,
                )
            with connect() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO web_admins (
                        email,
                        tg_id,
                        is_owner,
                        can_analytics,
                        can_balance,
                        can_message,
                        can_block,
                        can_promos,
                        can_dns,
                        can_support,
                        can_payments,
                        can_broadcast,
                        can_delete_users,
                        created_at,
                        created_by_account_id
                    )
                    VALUES (?, ?, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, ?, ?)
                    """,
                    (email or None, tg_id, iso_now(), int(account["id"])),
                )
                conn.commit()
            log_admin_action(
                account,
                "admin_add",
                target=email or f"tg:{tg_id}",
                summary={"email": email, "tgId": tg_id},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except Exception:
            logger.exception("Admin add failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_remove_admin(self):
        account = self.require_owner_admin()
        if not account:
            return
        try:
            payload = self.read_json()
            admin_id = int(payload.get("id") or 0)
            if not admin_id:
                return self.json_response(
                    {"ok": False, "error": "invalid_admin"},
                    HTTPStatus.BAD_REQUEST,
                )
            current_tg_ids = {
                int(value)
                for value in (account["linked_tg_id"], account["web_tg_id"])
                if value is not None
            }
            with connect() as conn:
                target = conn.execute(
                    "SELECT id, email, tg_id, is_owner FROM web_admins WHERE id = ?",
                    (admin_id,),
                ).fetchone()
                if not target:
                    return self.json_response(admin_bootstrap_payload(account))
                if bool(target["is_owner"] or 0):
                    return self.json_response(
                        {"ok": False, "error": "cannot_remove_owner"},
                        HTTPStatus.BAD_REQUEST,
                    )
                if normalize_email(target["email"]) == normalize_email(account["email"]) or (
                    target["tg_id"] is not None and int(target["tg_id"]) in current_tg_ids
                ):
                    return self.json_response(
                        {"ok": False, "error": "cannot_remove_self"},
                        HTTPStatus.BAD_REQUEST,
                    )
                conn.execute("DELETE FROM web_admins WHERE id = ?", (admin_id,))
                conn.commit()
            log_admin_action(
                account,
                "admin_remove",
                target=f"admin:{admin_id}",
                summary={"email": target["email"], "tgId": target["tg_id"]},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except Exception:
            logger.exception("Admin remove failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_permissions(self):
        account = self.require_owner_admin()
        if not account:
            return
        try:
            payload = self.read_json()
            admin_id = int(payload.get("id") or 0)
            permissions = payload.get("permissions") or {}
            if not isinstance(permissions, dict) or not admin_id:
                return self.json_response(
                    {"ok": False, "error": "invalid_admin"},
                    HTTPStatus.BAD_REQUEST,
                )
            updates = []
            values = []
            for key in ADMIN_PERMISSION_KEYS:
                if key in permissions:
                    updates.append(f"can_{key} = ?")
                    values.append(1 if permissions.get(key) else 0)
            if not updates:
                return self.json_response(admin_bootstrap_payload(account))
            with connect() as conn:
                target = conn.execute(
                    "SELECT id, is_owner FROM web_admins WHERE id = ? LIMIT 1",
                    (admin_id,),
                ).fetchone()
                if not target:
                    return self.json_response(
                        {"ok": False, "error": "invalid_admin"},
                        HTTPStatus.BAD_REQUEST,
                    )
                if bool(target["is_owner"] or 0):
                    return self.json_response(
                        {"ok": False, "error": "cannot_edit_owner"},
                        HTTPStatus.BAD_REQUEST,
                    )
                values.append(admin_id)
                conn.execute(
                    f"UPDATE web_admins SET {', '.join(updates)} WHERE id = ?",
                    tuple(values),
                )
                conn.commit()
            log_admin_action(
                account,
                "admin_permissions",
                target=f"admin:{admin_id}",
                summary={key: bool(permissions.get(key)) for key in ADMIN_PERMISSION_KEYS if key in permissions},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except Exception:
            logger.exception("Admin permissions update failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_promo_create(self):
        account = self.require_admin_permission("promos")
        if not account:
            return
        try:
            payload = self.read_json()
            code = payload.get("code")
            amount = payload.get("amount")
            admin_create_promo(code, amount)
            log_admin_action(
                account,
                "promo_create",
                target=str(code or "")[:80],
                summary={"amount": amount},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except sqlite3.IntegrityError:
            return self.json_response(
                {"ok": False, "error": "promo_exists"},
                HTTPStatus.CONFLICT,
            )
        except ValueError as exc:
            return self.json_response(
                {"ok": False, "error": str(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            logger.exception("Admin promo create failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_admin_promo_delete(self):
        account = self.require_admin_permission("promos")
        if not account:
            return
        try:
            payload = self.read_json()
            code = payload.get("code")
            admin_delete_promo(code)
            log_admin_action(
                account,
                "promo_delete",
                target=str(code or "")[:80],
                summary={},
                client_ip=self.client_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            return self.json_response(admin_bootstrap_payload(account))
        except Exception:
            logger.exception("Admin promo delete failed")
            return self.json_response(
                {"ok": False, "error": "operation_failed"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_promo(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            amount = redeem_promo(payload.get("code"))
            if amount is None:
                return self.json_response(
                    {"ok": False, "error": "invalid_promo"},
                    HTTPStatus.BAD_REQUEST,
                )
            web_tg_id, _username = ensure_web_user(account)
            BillingDB.change_balance(web_tg_id, int(amount))
            account, _ = get_account_by_session(self.session_token())
            data = self.auth_payload(account)
            data["creditedRub"] = int(amount)
            return self.json_response(data)
        except Exception as exc:
            logger.exception("Web promo failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_referral_code(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            referrals = apply_referral_code(account, payload.get("code"))
            account, _ = get_account_by_session(self.session_token())
            data = self.auth_payload(account)
            data["subscription"]["referrals"] = referrals
            return self.json_response(data)
        except Exception as exc:
            logger.exception("Web referral code failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_location(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            set_location(account, payload.get("location"))
            account, _ = get_account_by_session(self.session_token())
            return self.json_response(self.auth_payload(account))
        except Exception as exc:
            logger.exception("Web location failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_topup(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            amount = int(payload.get("amount") or 0)
            if amount not in TOPUP_AMOUNTS:
                return self.json_response({"ok": False, "error": "invalid_amount"}, HTTPStatus.BAD_REQUEST)
            web_tg_id, _username = ensure_web_user(account)

            async def create_payment():
                gateway = YooKassaGateway(
                    return_url="https://kingline.online/",
                    receipt_email=account["email"],
                )
                return await gateway.create_payment(
                    amount=amount,
                    description=f"Пополнение баланса {BRAND_NAME} на {amount} ₽",
                    tg_id=web_tg_id,
                    return_url="https://kingline.online/",
                )

            confirmation_url, payment_id = asyncio.run(create_payment())
            with connect() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO payments (payment_id, tg_id, amount, status)
                    VALUES (?, ?, ?, 'pending')
                    """,
                    (payment_id, web_tg_id, amount),
                )
                conn.commit()
            return self.json_response(
                {
                    "ok": True,
                    "paymentId": payment_id,
                    "confirmationUrl": confirmation_url,
                }
            )
        except Exception:
            logger.exception("Web topup failed")
            return self.json_response(
                {"ok": False, "error": "payment_unavailable"},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_check_payment(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            payload = self.read_json()
            payment_id = str(payload.get("paymentId") or "").strip()
            if not payment_id:
                return self.json_response({"ok": False, "error": "payment_not_found"}, HTTPStatus.BAD_REQUEST)
            web_tg_id, _username = ensure_web_user(account)
            payment_row = account_payment_row(payment_id, web_tg_id)
            if not payment_row:
                return self.json_response({"ok": False, "error": "payment_not_found"}, HTTPStatus.NOT_FOUND)

            async def check_payment():
                gateway = YooKassaGateway(receipt_email=account["email"])
                return await gateway.get_payment(payment_id)

            payment = asyncio.run(check_payment())
            validate_yookassa_payment(payment, payment_row)
            status = str(payment.get("status") or "")
            if status == "succeeded":
                credit_succeeded_payment(payment_id)
            else:
                with connect() as conn:
                    conn.execute(
                        "UPDATE payments SET status = ? WHERE payment_id = ? AND tg_id = ?",
                        (status or "pending", payment_id, web_tg_id),
                    )
                    conn.commit()
            account, _ = get_account_by_session(self.session_token())
            data = self.auth_payload(account)
            data["paymentStatus"] = status
            return self.json_response(data)
        except Exception as exc:
            logger.exception("Web payment check failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )

    def handle_link_code(self):
        account, _ = self.require_account()
        if not account:
            return
        try:
            code = generate_link_code(account)
            data = self.auth_payload(account)
            data["linkCode"] = code
            return self.json_response(data)
        except Exception as exc:
            logger.exception("Web link-code failed")
            return self.json_response(
                {"ok": False, "error": auth_errors(exc)},
                HTTPStatus.BAD_REQUEST,
            )


def main():
    configure_diagnostics("site-api")
    init_db()
    admin_online_snapshot()
    start_domain_category_preload_thread()
    start_email_reminder_thread()
    start_xui_analytics_thread()
    start_xui_access_import_thread()
    start_xui_insights_prewarm_thread()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"{BRAND_NAME} auth backend listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
