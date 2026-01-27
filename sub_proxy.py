from __future__ import annotations

import os
import ssl
import base64
import logging
import threading
import http.server
import socketserver
import urllib.parse
import urllib.request
from typing import Dict, Tuple

# ======================= НАСТРОЙКИ =======================

XUI_SUB_BACKEND = os.getenv("XUI_SUB_BACKEND", "http://127.0.0.1:2053").rstrip("/")
# Пример: http://127.0.0.1:2053   (sub-порт 3x-ui без TLS)
# Или:    https://sub.kingline.online:8443

SUB_ORIGINAL_HOST = os.getenv("SUB_ORIGINAL_HOST", "sub.kingline.online").strip()
# То, что сейчас зашито в конфигах от 3x-ui (host, который будем заменять)

SUB_BASE_DOMAIN = os.getenv("SUB_BASE_DOMAIN", "sub.kingline.online").strip()
# Базовый домен в схеме <sub_id>.<SUB_BASE_DOMAIN>

PROXY_HOST = os.getenv("SUB_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.getenv("SUB_PROXY_PORT", "8080"))

VERIFY_SSL = os.getenv("SUB_BACKEND_VERIFY_SSL", "true").lower() not in (
    "0",
    "false",
    "no",
)

LOG_LEVEL = os.getenv("SUB_PROXY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | sub_proxy | %(message)s",
)
logger = logging.getLogger("sub_proxy")

SSL_CTX = None
if not VERIFY_SSL:
    SSL_CTX = ssl._create_unverified_context()

BACKEND_PARSED = urllib.parse.urlparse(XUI_SUB_BACKEND)

# Заголовки, которые важно прокинуть клиенту (для имени/трафика/экспирации профиля)
PASSTHROUGH_HEADER_NAMES = {
    "subscription-userinfo",
    "subscription-user-info",
    "profile-title",
    "profile-name",
    "profile-web-page-url",
    "profile-webpage-url",
    "profile-update-interval",
}


# ======================= ВСПОМОГАТЕЛЬНОЕ =======================

def _fetch_from_backend(path_and_query: str) -> Tuple[int, Dict[str, str], bytes]:
    """
    Ходим к 3x-ui по XUI_SUB_BACKEND + path_and_query.
    Возвращаем (status, headers_dict, body_bytes).
    """
    url = XUI_SUB_BACKEND + path_and_query
    logger.info("→ Запрос к 3x-ui: %s", url)

    headers: Dict[str, str] = {}
    if BACKEND_PARSED.hostname:
        headers["Host"] = BACKEND_PARSED.hostname

    req = urllib.request.Request(url, headers=headers)

    try:
        if BACKEND_PARSED.scheme == "https":
            resp = urllib.request.urlopen(req, context=SSL_CTX)
        else:
            resp = urllib.request.urlopen(req)
    except Exception as e:
        logger.exception("Ошибка при запросе к 3x-ui: %s", e)
        raise

    try:
        status = getattr(resp, "status", resp.getcode())
        resp_headers = {k: v for k, v in resp.headers.items()}
        body = resp.read()
    finally:
        resp.close()

    return status, resp_headers, body


def _rewrite_body(sub_id: str, body: bytes) -> bytes:
    """
    Аккуратно заменяет хост SUB_ORIGINAL_HOST → <sub_id>.<SUB_BASE_DOMAIN>
    в теле подписки, стараясь не ломать формат.

    Поддерживает:
      - обычный текст (vless://, vmess:// и т.п.);
      - вариант 3x-ui, когда всё тело — один base64-блок с набором ссылок.
    """
    user_host = f"{sub_id}.{SUB_BASE_DOMAIN}"
    target_host = SUB_ORIGINAL_HOST or SUB_BASE_DOMAIN

    # 1) Пытаемся трактовать тело как текст
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("latin-1", errors="ignore")

    # Если уже видим vless:// / vmess:// и т.п. — работаем по тексту напрямую
    if any(proto in text for proto in ("vless://", "vmess://", "trojan://", "ss://")):
        if target_host:
            patched = text.replace(target_host, user_host)
        else:
            patched = text.replace(SUB_BASE_DOMAIN, user_host)
        return patched.encode("utf-8")

    # 2) Иначе пробуем вариант "всё тело — один base64-блок"
    #    (классический encode=1 у 3x-ui)
    stripped = "".join(text.split())  # убираем пробелы/переносы для base64
    try:
        decoded = base64.b64decode(stripped.encode("ascii"), validate=True).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        # Не смогли декодировать base64 — fallback: байтовый replace (мало ли)
        logger.info("Тело не похоже на base64 — выполняем простой байтовый replace")
        if target_host:
            return body.replace(target_host.encode("utf-8"), user_host.encode("utf-8"))
        return body.replace(SUB_BASE_DOMAIN.encode("utf-8"), user_host.encode("utf-8"))

    # В декодированном тексте уже должны быть vless:// и имя/экспирация (#...).
    # Просто меняем хост и кодируем всё обратно в base64 ОДНИМ блоком.
    if target_host:
        decoded_patched = decoded.replace(target_host, user_host)
    else:
        decoded_patched = decoded.replace(SUB_BASE_DOMAIN, user_host)

    b64_new = base64.b64encode(decoded_patched.encode("utf-8")).decode("ascii")

    # Если исходный текст заканчивался переносом строки — сохраним его.
    if text.endswith("\n"):
        b64_new += "\n"

    return b64_new.encode("ascii")


# ======================= HTTP HANDLER =======================

class SubProxyHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SubProxy/1.0"

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_simple(
        self,
        code: int,
        text: str,
        content_type: str = "text/plain; charset=utf-8",
    ):
        body = text.encode("utf-8", errors="ignore")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_request(self, method: str):
        """
        Общая логика для GET/HEAD.
        Для HEAD отправляем только заголовки (без тела).
        """
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path or "/"

        # healthcheck
        if path in ("/", "/health"):
            if method == "HEAD":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._send_simple(200, "OK sub_proxy\n")

        # Ожидаем /sub/<sub_id>
        if not path.startswith("/sub/"):
            return self._send_simple(404, "Not found\n")

        sub_id = path[len("/sub/") :].strip("/")
        if not sub_id:
            return self._send_simple(400, "sub_id required\n")

        logger.info(
            "📥 Запрос подписки: sub_id=%s, qs='%s', from=%s, method=%s",
            sub_id,
            parsed.query,
            self.client_address[0],
            method,
        )

        # Собираем path+query для backend
        path_and_query = "/sub/" + sub_id
        if parsed.query:
            path_and_query += "?" + parsed.query

        try:
            status, backend_headers, body = _fetch_from_backend(path_and_query)
        except Exception as e:
            return self._send_simple(502, f"Upstream error: {e}\n")

        # Контент-тайп забираем из backend
        content_type = backend_headers.get(
            "Content-Type", "text/plain; charset=utf-8"
        )

        # Если не 200 — просто прокидываем как есть (но с нашими заголовками)
        if status != 200:
            logger.warning(
                "3x-ui вернул статус %s для sub_id=%s", status, sub_id
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)

            # Пробрасываем важные заголовки для профиля (если есть)
            for h, v in backend_headers.items():
                if h.lower() in PASSTHROUGH_HEADER_NAMES:
                    self.send_header(h, v)

            if method == "HEAD":
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 200 OK — переписываем host в теле
        patched_body = _rewrite_body(sub_id, body)

        logger.info(
            "✅ Подписка переписана для sub_id=%s → %s.%s",
            sub_id,
            sub_id,
            SUB_BASE_DOMAIN,
        )

        self.send_response(200)
        self.send_header("Content-Type", content_type)

        # Пробрасываем важные заголовки (трафик/экспирация/имя профиля и т.п.)
        for h, v in backend_headers.items():
            if h.lower() in PASSTHROUGH_HEADER_NAMES:
                self.send_header(h, v)

        if method == "HEAD":
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_header("Content-Length", str(len(patched_body)))
        self.end_headers()
        self.wfile.write(patched_body)

    def do_GET(self):
        self._proxy_request("GET")

    def do_HEAD(self):
        # Некоторые клиенты сначала делают HEAD, чтобы получить только Subscription-Userinfo
        self._proxy_request("HEAD")


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


SUB_SERVER: ThreadedHTTPServer | None = None
SUB_THREAD: threading.Thread | None = None


def start_sub_proxy_in_background():
    """
    Запускает HTTP-прокси подписки в отдельном потоке (НЕ блокирует основной поток).
    Вызывать из main.py.
    """
    global SUB_SERVER, SUB_THREAD

    if SUB_SERVER is not None:
        # уже запущен
        return SUB_SERVER, SUB_THREAD

    server_address = (PROXY_HOST, PROXY_PORT)
    SUB_SERVER = ThreadedHTTPServer(server_address, SubProxyHandler)
    SUB_THREAD = threading.Thread(target=SUB_SERVER.serve_forever, daemon=True)
    SUB_THREAD.start()

    logger.info(
        "Sub-proxy запущен на %s:%d, backend=%s, base_domain=%s",
        PROXY_HOST,
        PROXY_PORT,
        XUI_SUB_BACKEND,
        SUB_BASE_DOMAIN,
    )
    return SUB_SERVER, SUB_THREAD


def stop_sub_proxy():
    """
    Аккуратная остановка HTTP-прокси (вызывать из main.py).
    """
    global SUB_SERVER, SUB_THREAD

    if SUB_SERVER is not None:
        try:
            SUB_SERVER.shutdown()
            SUB_SERVER.server_close()
            logger.info("Sub-proxy корректно остановлен")
        except Exception as e:
            logger.warning("Ошибка при остановке sub-proxy: %s", e)
        finally:
            SUB_SERVER = None

    if SUB_THREAD is not None:
        try:
            SUB_THREAD.join(timeout=1.0)
        except Exception:
            pass
        SUB_THREAD = None


if __name__ == "__main__":
    start_sub_proxy_in_background()
    try:
        while True:
            pass
    except KeyboardInterrupt:
        stop_sub_proxy()
