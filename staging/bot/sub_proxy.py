from __future__ import annotations

import os
import ssl
import re
import json
import time
import base64
import logging
import threading
import http.server
import socketserver
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Dict, Tuple, List, Optional
from diagnostics import RequestDiagnosticsMixin, observe


# ======================= НАСТРОЙКИ =======================

# Основной backend — старый LT sub backend
XUI_SUB_BACKEND = os.getenv("XUI_SUB_BACKEND", "http://127.0.0.1:2053").rstrip("/")

# Дополнительный backend — FR backend для склейки
XUI_SUB_BACKEND_FR = os.getenv("XUI_SUB_BACKEND_FR", "").rstrip("/")

# Дополнительный backend — RU backend для склейки
XUI_SUB_BACKEND_EXTRA = os.getenv("XUI_SUB_BACKEND_EXTRA", "").rstrip("/")

SUB_ORIGINAL_HOST = os.getenv("SUB_ORIGINAL_HOST", "sub.kingline.online").strip()
SUB_BASE_DOMAIN = os.getenv("SUB_BASE_DOMAIN", "sub.kingline.online").strip()

# Для FR можно не задавать вообще: хост возьмётся из XUI_SUB_BACKEND_FR
SUB_FR_ORIGINAL_HOST = os.getenv("SUB_FR_ORIGINAL_HOST", "").strip()
SUB_FR_BASE_DOMAIN = os.getenv("SUB_FR_BASE_DOMAIN", "").strip()

SUB_EXTRA_ORIGINAL_HOST = os.getenv("SUB_EXTRA_ORIGINAL_HOST", "").strip()
SUB_EXTRA_BASE_DOMAIN = os.getenv("SUB_EXTRA_BASE_DOMAIN", "").strip()

PROXY_HOST = os.getenv("SUB_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.getenv("SUB_PROXY_PORT", "8080"))

VERIFY_SSL = os.getenv("SUB_BACKEND_VERIFY_SSL", "true").lower() not in (
    "0", "false", "no"
)
VERIFY_SSL_FR = os.getenv("SUB_BACKEND_FR_VERIFY_SSL", "true").lower() not in (
    "0", "false", "no"
)
VERIFY_SSL_EXTRA = os.getenv("SUB_BACKEND_EXTRA_VERIFY_SSL", "true").lower() not in (
    "0", "false", "no"
)

ANNOUNCEMENT_TEXT = os.getenv(
    "SUB_ANNOUNCEMENT_TEXT",
    "Mobile, PC, TV - Сервера с наименьшей задержкой.\nБС - Сервера, обходящие белые списки (для крайних случаев).",
).replace("\r", " ").replace("\n", " ").strip()

LOG_LEVEL = os.getenv("SUB_PROXY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | sub_proxy | %(message)s",
)
logger = logging.getLogger("sub_proxy")


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        return default


SUB_BACKEND_TIMEOUT_SECONDS = max(1, _env_int("SUB_BACKEND_TIMEOUT_SECONDS", 20))
SUB_OPTIONAL_BACKEND_TIMEOUT_SECONDS = max(
    1,
    _env_int("SUB_OPTIONAL_BACKEND_TIMEOUT_SECONDS", 5),
)
OPTIONAL_BACKEND_RETRY_SECONDS = 30
OPTIONAL_BACKEND_FAILURES = {}
OPTIONAL_BACKEND_LOCK = threading.Lock()
OPTIONAL_FETCH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="subscription-extra")
OPTIONAL_FETCH_SLOTS = threading.BoundedSemaphore(4)

SSL_CTX = None
if not VERIFY_SSL:
    SSL_CTX = ssl._create_unverified_context()

SSL_CTX_FR = None
if not VERIFY_SSL_FR:
    SSL_CTX_FR = ssl._create_unverified_context()

SSL_CTX_EXTRA = None
if not VERIFY_SSL_EXTRA:
    SSL_CTX_EXTRA = ssl._create_unverified_context()

BACKEND_PARSED = urllib.parse.urlparse(XUI_SUB_BACKEND)
BACKEND_FR_PARSED = (
    urllib.parse.urlparse(XUI_SUB_BACKEND_FR)
    if XUI_SUB_BACKEND_FR
    else None
)
BACKEND_EXTRA_PARSED = (
    urllib.parse.urlparse(XUI_SUB_BACKEND_EXTRA)
    if XUI_SUB_BACKEND_EXTRA
    else None
)

PASSTHROUGH_HEADER_NAMES = {
    "subscription-userinfo",
    "subscription-user-info",
    "profile-title",
    "profile-update-interval",
    "support-url",
    "profile-web-page-url",
    "profile-webpage-url",
    "routing-enable",
    "routing",
    "announce",
    "content-disposition",
}

PROTO_MARKERS = ("vless://", "vmess://", "trojan://", "ss://", "hy2://", "hysteria2://")

TIME_SUFFIX_PATTERNS = [
    re.compile(r"\s*[⌛⏳⏰🕒🕓🕔🕕🕖🕗🕘🕙🕚🕛]+\s*$", re.IGNORECASE),
    re.compile(
        r"\s*[-–—|]?\s*\d+\s*[dд](?:\s+\d+\s*[hчн])?(?:\s+\d+\s*[mм])?(?:\s+\d+\s*[sс])?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*[-–—|]?\s*\d+\s*[hчн](?:\s+\d+\s*[mм])?(?:\s+\d+\s*[sс])?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*[-–—|]?\s*\d+\s*[mм](?:\s+\d+\s*[sс])?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\s*[-–—|]?\s*\d+\s*[sс]\s*$",
        re.IGNORECASE,
    ),
]

# ======================= ВСПОМОГАТЕЛЬНОЕ =======================

def _b64_header_value(text: str) -> str:
    return "base64:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


def _build_replacement_host(sub_id: str, base_domain: str, *, fixed: bool) -> str:
    if not base_domain:
        return ""
    if fixed:
        return base_domain
    return f"{sub_id}.{base_domain}"


def _fetch_from_backend(
    backend_base: str,
    parsed_backend: urllib.parse.ParseResult,
    ssl_ctx,
    path_and_query: str,
    *,
    label: str,
    method: str = "GET",
    timeout: int = 20,
) -> Tuple[int, Dict[str, str], bytes]:
    optional = label in {"fr", "extra"}
    if optional:
        with OPTIONAL_BACKEND_LOCK:
            if OPTIONAL_BACKEND_FAILURES.get(backend_base, 0) > time.monotonic():
                raise ConnectionError(f"{label} backend is in retry cooldown")
    url = backend_base + path_and_query
    logger.debug("Subscription backend request label=%s", label)

    headers: Dict[str, str] = {}
    if parsed_backend.hostname:
        headers["Host"] = parsed_backend.hostname

    req = urllib.request.Request(url, headers=headers, method=method)
    started, error, status = time.monotonic(), None, None
    try:
        context = ssl_ctx if parsed_backend.scheme == "https" else None
        with urllib.request.urlopen(req, context=context, timeout=timeout) as resp:
            status = resp.status
            resp_headers = dict(resp.headers.items())
            body = resp.read() if method != "HEAD" else b""
    except urllib.error.HTTPError as e:
        status = e.code
        resp_headers = dict(e.headers.items())
        body = e.read() if method != "HEAD" else b""
        e.close()
        if optional and status >= 500:
            with OPTIONAL_BACKEND_LOCK:
                OPTIONAL_BACKEND_FAILURES[backend_base] = time.monotonic() + OPTIONAL_BACKEND_RETRY_SECONDS
    except Exception as e:
        error = type(e).__name__
        # A missing subscription is user-specific; only outages affect the host.
        if optional and (not isinstance(e, urllib.error.HTTPError) or e.code >= 500):
            with OPTIONAL_BACKEND_LOCK:
                OPTIONAL_BACKEND_FAILURES[backend_base] = time.monotonic() + OPTIONAL_BACKEND_RETRY_SECONDS
        logger.warning("Subscription backend unavailable label=%s error=%s", label, type(e).__name__)
        raise
    finally:
        observe("subscription.backend", (time.monotonic() - started) * 1000,
                target=label, status=status, error=error)
    if optional and status is not None and status < 500:
        with OPTIONAL_BACKEND_LOCK:
            OPTIONAL_BACKEND_FAILURES.pop(backend_base, None)

    return status, resp_headers, body


def _decode_body_to_text(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1", errors="ignore")


def _looks_like_plain_subscription(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and any(line.startswith(PROTO_MARKERS) for line in lines) and all(
        line.startswith(PROTO_MARKERS) or line.startswith("#") for line in lines
    )


def _try_decode_base64_subscription(text: str) -> Optional[str]:
    stripped = "".join(text.split())
    if not stripped:
        return ""

    variants = [stripped]
    pad = len(stripped) % 4
    if pad:
        variants.append(stripped + ("=" * (4 - pad)))

    for candidate in variants:
        try:
            decoded = base64.b64decode(candidate.encode("ascii")).decode(
                "utf-8", errors="replace"
            )
            if _looks_like_plain_subscription(decoded):
                return decoded
        except Exception:
            continue

    return None


def _split_subscription_lines(text: str) -> List[str]:
    lines: List[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
    return lines


def _rewrite_text_hosts(
    text: str,
    sub_id: str,
    original_host: str,
    base_domain: str,
    *,
    fixed: bool,
) -> str:
    if not original_host or not base_domain:
        return text

    user_host = _build_replacement_host(sub_id, base_domain, fixed=fixed)
    if not user_host:
        return text

    return text.replace(original_host, user_host)


def _strip_expiry_suffix(name: str) -> str:
    value = urllib.parse.unquote(name or "").strip()
    if not value:
        return value

    prev = None
    while prev != value:
        prev = value
        for pattern in TIME_SUFFIX_PATTERNS:
            value = pattern.sub("", value).rstrip(" -–—|,.;")

    return value.strip()


def _patch_generic_uri_line(
    line: str,
    *,
    sub_id: str,
    original_host: str,
    base_domain: str,
    fixed: bool,
) -> str:
    patched = _rewrite_text_hosts(
        line,
        sub_id,
        original_host,
        base_domain,
        fixed=fixed,
    )

    try:
        parsed = urllib.parse.urlsplit(patched)
    except Exception:
        return patched

    if not parsed.fragment:
        return patched

    clean_fragment = _strip_expiry_suffix(parsed.fragment)

    rebuilt = urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.query,
            urllib.parse.quote(
                clean_fragment,
                safe=" @()[]{}#+-_,.:;=|~!*'$",
            ),
        )
    )
    return rebuilt


def _patch_vmess_line(
    line: str,
    *,
    sub_id: str,
    original_host: str,
    base_domain: str,
    fixed: bool,
) -> str:
    prefix = "vmess://"
    payload = line[len(prefix):].strip()

    decoded_bytes = None
    variants = [payload]
    pad = len(payload) % 4
    if pad:
        variants.append(payload + ("=" * (4 - pad)))

    for candidate in variants:
        try:
            decoded_bytes = base64.b64decode(candidate.encode("ascii"))
            break
        except Exception:
            continue

    if decoded_bytes is None:
        return _rewrite_text_hosts(line, sub_id, original_host, base_domain, fixed=fixed)

    decoded_text = decoded_bytes.decode("utf-8", errors="replace")
    decoded_text = _rewrite_text_hosts(
        decoded_text,
        sub_id,
        original_host,
        base_domain,
        fixed=fixed,
    )

    try:
        obj = json.loads(decoded_text)
    except Exception:
        encoded = base64.b64encode(decoded_text.encode("utf-8")).decode("ascii")
        return prefix + encoded

    for key in ("ps", "name", "remark", "remarks"):
        if key in obj and isinstance(obj[key], str):
            obj[key] = _strip_expiry_suffix(obj[key])

    encoded = base64.b64encode(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    return prefix + encoded


def _patch_subscription_line(
    line: str,
    *,
    sub_id: str,
    original_host: str,
    base_domain: str,
    fixed: bool,
) -> str:
    low = line.lower()

    if low.startswith("vmess://"):
        return _patch_vmess_line(
            line,
            sub_id=sub_id,
            original_host=original_host,
            base_domain=base_domain,
            fixed=fixed,
        )

    if low.startswith(("vless://", "trojan://", "ss://", "hy2://", "hysteria2://")):
        return _patch_generic_uri_line(
            line,
            sub_id=sub_id,
            original_host=original_host,
            base_domain=base_domain,
            fixed=fixed,
        )

    return _rewrite_text_hosts(line, sub_id, original_host, base_domain, fixed=fixed)


def _parse_subscription_body(
    body: bytes,
    *,
    sub_id: str,
    original_host: str,
    base_domain: str,
    fixed: bool,
) -> Tuple[List[str], str, bool]:
    text = _decode_body_to_text(body)
    trailing_nl = text.endswith("\n")

    if _looks_like_plain_subscription(text):
        lines = [
            _patch_subscription_line(
                line,
                sub_id=sub_id,
                original_host=original_host,
                base_domain=base_domain,
                fixed=fixed,
            )
            for line in _split_subscription_lines(text)
        ]
        return lines, "plain", trailing_nl

    decoded = _try_decode_base64_subscription(text)
    if decoded is not None:
        lines = [
            _patch_subscription_line(
                line,
                sub_id=sub_id,
                original_host=original_host,
                base_domain=base_domain,
                fixed=fixed,
            )
            for line in _split_subscription_lines(decoded)
        ]
        return lines, "b64", trailing_nl

    raise ValueError("Unsupported subscription payload")


def _encode_subscription_lines(lines: List[str], *, mode: str, trailing_nl: bool) -> bytes:
    payload = "\n".join(lines)
    if lines and trailing_nl:
        payload += "\n"

    if mode == "b64":
        encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        if trailing_nl:
            encoded += "\n"
        return encoded.encode("ascii")

    return payload.encode("utf-8")


def _send_passthrough_headers(
    handler: http.server.BaseHTTPRequestHandler,
    backend_headers: Dict[str, str],
) -> None:
    for h, v in backend_headers.items():
        h_l = h.lower()

        if h_l not in PASSTHROUGH_HEADER_NAMES:
            continue

        if h_l == "announce":
            continue

        handler.send_header(h, v)

    if ANNOUNCEMENT_TEXT:
        handler.send_header("Announce", _b64_header_value(ANNOUNCEMENT_TEXT))


def _dedupe_lines(lines: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for line in lines:
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def _fetch_and_parse_subscription(
    *,
    backend_base: str,
    parsed_backend: urllib.parse.ParseResult,
    ssl_ctx,
    path_and_query: str,
    label: str,
    method: str,
    sub_id: str,
    original_host: str,
    base_domain: str,
    fixed: bool,
    timeout: int = SUB_BACKEND_TIMEOUT_SECONDS,
) -> Tuple[int, Dict[str, str], bytes, List[str], str, bool]:
    status, headers, body = _fetch_from_backend(
        backend_base,
        parsed_backend,
        ssl_ctx,
        path_and_query,
        label=label,
        method=method,
        timeout=timeout,
    )

    if method == "HEAD" or status != 200:
        return status, headers, body, [], "plain", False

    lines, mode, trailing_nl = _parse_subscription_body(
        body,
        sub_id=sub_id,
        original_host=original_host,
        base_domain=base_domain,
        fixed=fixed,
    )
    return status, headers, body, lines, mode, trailing_nl


def _try_fetch_optional_subscription(**kwargs) -> List[str]:
    try:
        status, _, _, lines, _, _ = _fetch_and_parse_subscription(
            **kwargs, method="GET", timeout=SUB_OPTIONAL_BACKEND_TIMEOUT_SECONDS,
        )
        return lines if status == 200 else []
    except Exception as error:
        logger.warning("Optional subscription skipped label=%s error=%s",
                       kwargs["label"], type(error).__name__)
        return []


def _fetch_optional_lines(path_and_query: str, sub_id: str) -> List[str]:
    candidates = [
        (XUI_SUB_BACKEND_FR, BACKEND_FR_PARSED, SSL_CTX_FR, "fr",
         SUB_FR_ORIGINAL_HOST, SUB_FR_BASE_DOMAIN, True),
        (XUI_SUB_BACKEND_EXTRA, BACKEND_EXTRA_PARSED, SSL_CTX_EXTRA, "extra",
         SUB_EXTRA_ORIGINAL_HOST or SUB_ORIGINAL_HOST,
         SUB_EXTRA_BASE_DOMAIN or SUB_BASE_DOMAIN, False),
    ]
    pending = []
    for backend, parsed, ssl_ctx, label, original, domain, fixed in candidates:
        if not backend or not parsed:
            continue
        with OPTIONAL_BACKEND_LOCK:
            if OPTIONAL_BACKEND_FAILURES.get(backend, 0) > time.monotonic():
                continue
        if not OPTIONAL_FETCH_SLOTS.acquire(blocking=False):
            continue
        try:
            future = OPTIONAL_FETCH_POOL.submit(
                _try_fetch_optional_subscription, backend_base=backend,
                parsed_backend=parsed, ssl_ctx=ssl_ctx,
                path_and_query=path_and_query, label=label, sub_id=sub_id,
                original_host=original or parsed.hostname or "",
                base_domain=domain or parsed.hostname or "", fixed=fixed,
            )
        except Exception:
            OPTIONAL_FETCH_SLOTS.release()
            raise
        future.add_done_callback(lambda _: OPTIONAL_FETCH_SLOTS.release())
        pending.append((future, backend))

    # socket timeouts do not bound DNS resolution. Bound the complete wait here;
    # a bounded worker pool also prevents a DNS outage spawning unlimited threads.
    done, _ = wait([future for future, _ in pending], timeout=SUB_OPTIONAL_BACKEND_TIMEOUT_SECONDS)
    lines = []
    for future, backend in pending:
        if future in done:
            lines.extend(future.result())
        else:
            with OPTIONAL_BACKEND_LOCK:
                OPTIONAL_BACKEND_FAILURES[backend] = time.monotonic() + OPTIONAL_BACKEND_RETRY_SECONDS
            future.cancel()
            logger.warning("Optional subscription deadline exceeded")
    return lines


# ======================= HTTP HANDLER =======================

class SubProxyHandler(RequestDiagnosticsMixin, http.server.BaseHTTPRequestHandler):
    server_version = "SubProxy/4.0"

    def log_message(self, fmt: str, *args) -> None:
        # Request paths contain subscription credentials; diagnostics record only the route.
        pass

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
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path or "/"

        if path in ("/", "/health"):
            if method == "HEAD":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._send_simple(200, "OK sub_proxy\n")

        if not path.startswith("/sub/"):
            return self._send_simple(404, "Not found\n")

        sub_id = path[len("/sub/"):].strip("/")
        if not sub_id:
            return self._send_simple(400, "sub_id required\n")

        logger.debug("Subscription request method=%s", method)

        path_and_query = "/sub/" + sub_id
        if parsed.query:
            path_and_query += "?" + parsed.query

        # primary обязателен
        try:
            (
                primary_status,
                primary_headers,
                primary_body,
                primary_lines,
                primary_mode,
                primary_trailing_nl,
            ) = _fetch_and_parse_subscription(
                backend_base=XUI_SUB_BACKEND,
                parsed_backend=BACKEND_PARSED,
                ssl_ctx=SSL_CTX,
                path_and_query=path_and_query,
                label="primary",
                # x-ui exposes subscriptions through GET only; emulate HEAD here.
                method="GET" if method == "HEAD" else method,
                sub_id=sub_id,
                original_host=SUB_ORIGINAL_HOST,
                base_domain=SUB_BASE_DOMAIN,
                fixed=False,
            )
        except Exception as e:
            logger.warning("Primary subscription unavailable error=%s", type(e).__name__)
            return self._send_simple(502, "Subscription upstream unavailable\n")

        content_type = primary_headers.get("Content-Type", "text/plain; charset=utf-8")

        # Если primary вернул не 200 — отдаем как есть
        if primary_status != 200:
            logger.warning("Primary subscription status=%s", primary_status)
            self.send_response(primary_status)
            self.send_header("Content-Type", content_type)
            _send_passthrough_headers(self, primary_headers)
            if method == "HEAD":
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_header("Content-Length", str(len(primary_body)))
            self.end_headers()
            self.wfile.write(primary_body)
            return

        # HEAD — только заголовки от primary
        if method == "HEAD":
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            _send_passthrough_headers(self, primary_headers)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        merged_lines = list(primary_lines)
        merged_lines.extend(_fetch_optional_lines(path_and_query, sub_id))

        merged_lines = _dedupe_lines(merged_lines)

        patched_body = _encode_subscription_lines(
            merged_lines,
            mode=primary_mode,
            trailing_nl=primary_trailing_nl,
        )

        logger.debug("Subscription assembled lines=%s", len(merged_lines))

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        _send_passthrough_headers(self, primary_headers)
        self.send_header("Content-Length", str(len(patched_body)))
        self.end_headers()
        self.wfile.write(patched_body)

    def do_GET(self):
        try:
            self._proxy_request("GET")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        try:
            self._proxy_request("HEAD")
        except (BrokenPipeError, ConnectionResetError):
            pass


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


SUB_SERVER: ThreadedHTTPServer | None = None
SUB_THREAD: threading.Thread | None = None


def start_sub_proxy_in_background():
    global SUB_SERVER, SUB_THREAD

    if SUB_SERVER is not None:
        return SUB_SERVER, SUB_THREAD

    server_address = (PROXY_HOST, PROXY_PORT)
    SUB_SERVER = ThreadedHTTPServer(server_address, SubProxyHandler)
    SUB_THREAD = threading.Thread(target=SUB_SERVER.serve_forever, daemon=True)
    SUB_THREAD.start()

    logger.info(
        "Sub-proxy запущен на %s:%d, primary=%s, fr=%s, extra=%s, base_domain=%s, extra_base_domain=%s",
        PROXY_HOST,
        PROXY_PORT,
        XUI_SUB_BACKEND,
        XUI_SUB_BACKEND_FR or "-",
        XUI_SUB_BACKEND_EXTRA or "-",
        SUB_BASE_DOMAIN or "-",
        SUB_EXTRA_BASE_DOMAIN or "-",
    )
    return SUB_SERVER, SUB_THREAD


def stop_sub_proxy():
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
            time.sleep(3600)
    except KeyboardInterrupt:
        stop_sub_proxy()
