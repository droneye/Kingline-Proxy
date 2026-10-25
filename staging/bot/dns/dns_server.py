from __future__ import annotations

import json
import logging
import os
import socketserver
import threading
import time
from typing import Optional, Dict, Tuple

from dnslib import A, CAA, NS, RR, SOA, TXT, DNSRecord, QTYPE
from diagnostics import timed_sync

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DNS_ENV_PATH = os.path.join(BASE_DIR, ".env")

if load_dotenv is not None and os.path.exists(DNS_ENV_PATH):
    load_dotenv(DNS_ENV_PATH)

from .user_locator import DNSUserLocator

RAW_DOMAINS = os.getenv("KINGLINE_DNS_DOMAINS", "")
DOMAINS = [
    d.strip().strip(".").lower()
    for d in RAW_DOMAINS.replace(";", ",").split(",")
    if d.strip()
]
DOMAINS_TUPLE = tuple(DOMAINS)
DOMAINS_SUFFIX_TUPLE = tuple("." + d for d in DOMAINS)

PANEL_DOMAIN = os.getenv("KINGLINE_DNS_PANEL_DOMAIN", "").strip().strip(".").lower()
PANEL_IP = os.getenv("KINGLINE_DNS_PANEL_IP", "").strip()

TTL = int(os.getenv("KINGLINE_DNS_TTL", "5"))

UPSTREAM_DNS = os.getenv("DNS_UPSTREAM", "1.1.1.1").strip()
UPSTREAM_PORT = int(os.getenv("DNS_UPSTREAM_PORT", "53"))

ACME_STORE_PATH = os.getenv(
    "ACME_CHALLENGE_STORE",
    "/var/lib/kingline/acme_challenges.json",
).strip()
ACME_TTL = int(os.getenv("ACME_CHALLENGE_TTL", "60"))
ACME_ZONE_SUFFIX = os.getenv(
    "ACME_ZONE_SUFFIX",
    "sub.kingline.online",
).strip().strip(".").lower()
ACME_CAA_ISSUER = os.getenv("ACME_CAA_ISSUER", "letsencrypt.org").strip()

ZONE_NS_HOSTS = [
    host.strip().strip(".").lower()
    for host in os.getenv(
        "KINGLINE_DNS_NS",
        "ns1.kingline.online,ns2.kingline.online",
    ).replace(";", ",").split(",")
    if host.strip()
]
SOA_MNAME = os.getenv(
    "KINGLINE_DNS_SOA_MNAME",
    (ZONE_NS_HOSTS[0] if ZONE_NS_HOSTS else "ns1.kingline.online"),
).strip().strip(".").lower()
SOA_RNAME = os.getenv("KINGLINE_DNS_SOA_RNAME", "hostmaster.kingline.online").strip().strip(".").lower()
SOA_SERIAL = int(os.getenv("KINGLINE_DNS_SOA_SERIAL", "2026042901"))
SOA_REFRESH = int(os.getenv("KINGLINE_DNS_SOA_REFRESH", "300"))
SOA_RETRY = int(os.getenv("KINGLINE_DNS_SOA_RETRY", "120"))
SOA_EXPIRE = int(os.getenv("KINGLINE_DNS_SOA_EXPIRE", "86400"))
SOA_MINIMUM = int(os.getenv("KINGLINE_DNS_SOA_MINIMUM", "30"))

DEFAULT_DB_PATH = os.path.abspath(
    os.getenv("DB_PATH") or os.path.join(os.path.dirname(BASE_DIR), "vpn_bot.db")
)

# Производительность / кэш
DNS_RESPONSE_CACHE_TTL = int(os.getenv("DNS_RESPONSE_CACHE_TTL", "5"))
DNS_NEGATIVE_CACHE_TTL = int(os.getenv("DNS_NEGATIVE_CACHE_TTL", "3"))
DNS_UPSTREAM_CACHE_TTL = int(os.getenv("DNS_UPSTREAM_CACHE_TTL", "30"))
DNS_LOCATOR_CACHE_TTL = int(os.getenv("DNS_LOCATOR_CACHE_TTL", "5"))
DNS_CACHE_MAX_ITEMS = int(os.getenv("DNS_CACHE_MAX_ITEMS", "10000"))
DNS_CACHE_PRUNE_INTERVAL = int(os.getenv("DNS_CACHE_PRUNE_INTERVAL", "30"))

# Логи
LOG_LEVEL = os.getenv("DNS_LOG_LEVEL", "INFO").upper()
DNS_VERBOSE_QUERY_LOGS = os.getenv("DNS_VERBOSE_QUERY_LOGS", "0").lower() in ("1", "true", "yes")
DNS_DEBUG_TIMINGS = os.getenv("DNS_DEBUG_TIMINGS", "0").lower() in ("1", "true", "yes")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("dns_server")
DNS_SERVER_BUILD = "2026-04-16-udp-fast-cache-bot-v1"
logger.info("DNS_SERVER_BUILD=%s file=%s", DNS_SERVER_BUILD, __file__)

USER_LOCATOR = DNSUserLocator(
    db_path=DEFAULT_DB_PATH,
    domains=DOMAINS,
)
logger.info("DNSUserLocator initialized (DB_PATH=%s)", DEFAULT_DB_PATH)

if DOMAINS:
    logger.info("Ограничиваемся доменами: %s", ", ".join(DOMAINS))
else:
    logger.info("KINGLINE_DNS_DOMAINS не заданы — правила применяются ко всем A-запросам.")

if PANEL_DOMAIN and PANEL_IP:
    logger.info("Панель %s всегда будет резолвиться в %s", PANEL_DOMAIN, PANEL_IP)

logger.info(
    "ACME store: %s (TTL=%s), zone suffix for ACME: %s, CAA issuer: %s",
    ACME_STORE_PATH,
    ACME_TTL,
    ACME_ZONE_SUFFIX,
    ACME_CAA_ISSUER,
)

_acme_cache_mtime: float = 0.0
_acme_cache: dict[str, list[str]] = {}

# cache_key -> (expires_at, packed_dns_response)
_response_cache: Dict[Tuple[str, str], Tuple[float, bytes]] = {}

# qname -> (expires_at, (ip, loc_pref, loc_name, sub_id))
_locator_cache: Dict[str, Tuple[float, Tuple[Optional[str], str, str, str]]] = {}

_cache_lock = threading.Lock()
_cache_next_prune_ts = 0.0

DNS_SERVER = None
DNS_THREAD = None
DNS_TCP_SERVER = None
DNS_TCP_THREAD = None


def _now() -> float:
    return time.monotonic()


def _norm_fqdn(name: str) -> str:
    normalized = name.strip().lower()
    if not normalized.endswith("."):
        normalized += "."
    return normalized


def _qname_key(name: str) -> str:
    return name.rstrip(".").lower()


def _cache_prune_if_needed() -> None:
    global _cache_next_prune_ts

    now_ts = _now()
    if now_ts < _cache_next_prune_ts:
        return

    with _cache_lock:
        if now_ts < _cache_next_prune_ts:
            return

        expired_resp = [k for k, (exp, _) in _response_cache.items() if exp <= now_ts]
        for k in expired_resp:
            _response_cache.pop(k, None)

        expired_loc = [k for k, (exp, _) in _locator_cache.items() if exp <= now_ts]
        for k in expired_loc:
            _locator_cache.pop(k, None)

        if len(_response_cache) > DNS_CACHE_MAX_ITEMS:
            for k in list(_response_cache.keys())[: max(1, len(_response_cache) // 4)]:
                _response_cache.pop(k, None)

        if len(_locator_cache) > DNS_CACHE_MAX_ITEMS:
            for k in list(_locator_cache.keys())[: max(1, len(_locator_cache) // 4)]:
                _locator_cache.pop(k, None)

        _cache_next_prune_ts = now_ts + DNS_CACHE_PRUNE_INTERVAL


def _cache_get_response(cache_key: Tuple[str, str], request_id: int) -> Optional[bytes]:
    now_ts = _now()
    with _cache_lock:
        item = _response_cache.get(cache_key)
        if not item:
            return None

        expires_at, payload = item
        if expires_at <= now_ts:
            _response_cache.pop(cache_key, None)
            return None

    if len(payload) >= 2:
        return request_id.to_bytes(2, "big") + payload[2:]
    return payload


def _cache_set_response(cache_key: Tuple[str, str], payload: bytes, ttl: int) -> None:
    if ttl <= 0 or not payload:
        return

    with _cache_lock:
        _response_cache[cache_key] = (_now() + ttl, payload)

    _cache_prune_if_needed()


def _cache_get_locator(qname: str) -> Optional[Tuple[Optional[str], str, str, str]]:
    now_ts = _now()
    key = _qname_key(qname)

    with _cache_lock:
        item = _locator_cache.get(key)
        if not item:
            return None

        expires_at, value = item
        if expires_at <= now_ts:
            _locator_cache.pop(key, None)
            return None

        return value


def _cache_set_locator(qname: str, value: Tuple[Optional[str], str, str, str], ttl: int) -> None:
    if ttl <= 0:
        return

    with _cache_lock:
        _locator_cache[_qname_key(qname)] = (_now() + ttl, value)

    _cache_prune_if_needed()


def _load_acme_store() -> dict[str, list[str]]:
    global _acme_cache_mtime, _acme_cache

    try:
        stat_result = os.stat(ACME_STORE_PATH)
        if _acme_cache and stat_result.st_mtime == _acme_cache_mtime:
            return _acme_cache

        with open(ACME_STORE_PATH, "r", encoding="utf-8") as file:
            raw = json.load(file) or {}

        data: dict[str, list[str]] = {}
        for key, value in raw.items():
            fqdn = _norm_fqdn(str(key))
            if isinstance(value, list):
                values = [str(item) for item in value if str(item)]
            elif isinstance(value, str):
                values = [value]
            else:
                values = []

            if values:
                data[fqdn] = values

        _acme_cache = data
        _acme_cache_mtime = stat_result.st_mtime
        return data

    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("ACME store read error (%s): %s", ACME_STORE_PATH, exc)
        return {}


def _match_domain(qname: str) -> bool:
    if not DOMAINS_TUPLE:
        return True

    name = qname.rstrip(".").lower()
    return name in DOMAINS_TUPLE or name.endswith(DOMAINS_SUFFIX_TUPLE)


def _is_panel_domain(qname: str) -> bool:
    if not PANEL_DOMAIN:
        return False
    return qname.rstrip(".").lower() == PANEL_DOMAIN


def _is_zone_apex(qname: str) -> bool:
    name = qname.rstrip(".").lower()
    return name in DOMAINS_TUPLE


def _empty_authoritative_reply(request: DNSRecord) -> bytes:
    reply = request.reply()
    reply.header.aa = 1
    reply.header.ra = 0
    return reply.pack()


def _add_authority_soa(reply: DNSRecord, qname: str) -> None:
    zone = ACME_ZONE_SUFFIX or (DOMAINS[0] if DOMAINS else qname)
    reply.add_auth(
        RR(
            rname=zone + ".",
            rtype=QTYPE.SOA,
            rclass=1,
            ttl=SOA_MINIMUM,
            rdata=SOA(
                SOA_MNAME + ".",
                SOA_RNAME + ".",
                (SOA_SERIAL, SOA_REFRESH, SOA_RETRY, SOA_EXPIRE, SOA_MINIMUM),
            ),
        )
    )


def _answer_zone_ns(request: DNSRecord, qname: str, client_ip: str) -> bytes:
    reply = request.reply()
    reply.header.aa = 1
    reply.header.ra = 0

    for host in ZONE_NS_HOSTS:
        reply.add_answer(
            RR(
                rname=qname,
                rtype=QTYPE.NS,
                rclass=1,
                ttl=300,
                rdata=NS(host + "."),
            )
        )

    if not ZONE_NS_HOSTS:
        _add_authority_soa(reply, qname)

    if DNS_VERBOSE_QUERY_LOGS:
        logger.info("NS answer for %s (client=%s): %s", qname, client_ip, ZONE_NS_HOSTS)

    return reply.pack()


def _answer_zone_soa(request: DNSRecord, qname: str, client_ip: str) -> bytes:
    reply = request.reply()
    reply.header.aa = 1
    reply.header.ra = 0
    reply.add_answer(
        RR(
            rname=qname,
            rtype=QTYPE.SOA,
            rclass=1,
            ttl=300,
            rdata=SOA(
                SOA_MNAME + ".",
                SOA_RNAME + ".",
                (SOA_SERIAL, SOA_REFRESH, SOA_RETRY, SOA_EXPIRE, SOA_MINIMUM),
            ),
        )
    )

    if DNS_VERBOSE_QUERY_LOGS:
        logger.info("SOA answer for %s (client=%s)", qname, client_ip)

    return reply.pack()


def _forward_to_upstream(request: DNSRecord) -> Optional[bytes]:
    if not UPSTREAM_DNS:
        return None

    try:
        return request.send(UPSTREAM_DNS, UPSTREAM_PORT, timeout=2)
    except Exception as exc:
        logger.warning("Upstream error %s:%d: %s", UPSTREAM_DNS, UPSTREAM_PORT, exc)
        return None


def _maybe_answer_acme_txt(
    request: DNSRecord,
    qname: str,
    client_ip: str,
) -> Optional[bytes]:
    fqdn = _norm_fqdn(qname)

    if not fqdn.startswith("_acme-challenge."):
        return None

    if ACME_ZONE_SUFFIX and not fqdn.endswith(f".{ACME_ZONE_SUFFIX}."):
        return None

    store = _load_acme_store()
    values = store.get(fqdn)

    reply = request.reply()
    reply.header.aa = 1
    reply.header.ra = 0

    if not values:
        reply.header.rcode = 3
        if DNS_VERBOSE_QUERY_LOGS:
            logger.info("ACME TXT NXDOMAIN for %s (client=%s)", qname, client_ip)
        return reply.pack()

    for value in values:
        reply.add_answer(
            RR(
                rname=qname,
                rtype=QTYPE.TXT,
                rclass=1,
                ttl=ACME_TTL,
                rdata=TXT(value),
            )
        )

    if DNS_VERBOSE_QUERY_LOGS:
        logger.info("ACME TXT answer for %s (client=%s)", qname, client_ip)

    return reply.pack()


def _maybe_answer_caa(
    request: DNSRecord,
    qname: str,
    client_ip: str,
) -> Optional[bytes]:
    if not ACME_ZONE_SUFFIX:
        return None

    fqdn = _norm_fqdn(qname)
    zone = _norm_fqdn(ACME_ZONE_SUFFIX)

    if not (fqdn == zone or fqdn.endswith("." + zone)):
        return None

    reply = request.reply()
    reply.header.aa = 1
    reply.header.ra = 0

    issuer = ACME_CAA_ISSUER or "letsencrypt.org"

    reply.add_answer(
        RR(
            rname=qname,
            rtype=QTYPE.CAA,
            rclass=1,
            ttl=300,
            rdata=CAA(0, "issue", issuer),
        )
    )
    reply.add_answer(
        RR(
            rname=qname,
            rtype=QTYPE.CAA,
            rclass=1,
            ttl=300,
            rdata=CAA(0, "issuewild", issuer),
        )
    )

    if DNS_VERBOSE_QUERY_LOGS:
        logger.info("CAA answer for %s (client=%s): issuer=%s", qname, client_ip, issuer)

    return reply.pack()


def _choose_ip_cached(qname: str) -> Tuple[Optional[str], str, str, str]:
    cached = _cache_get_locator(qname)
    if cached is not None:
        return cached

    result = USER_LOCATOR.choose_ip_for_qname(qname)
    _cache_set_locator(qname, result, DNS_LOCATOR_CACHE_TTL)
    return result


def _process_dns_query(request: DNSRecord, client_ip: str) -> bytes:
    start_ts = time.perf_counter()

    question = request.q
    qname = str(question.qname)
    qtype = QTYPE.get(question.qtype, "UNKNOWN")
    qname_norm = _qname_key(qname)

    cache_key = (qname_norm, str(qtype))
    cached_response = _cache_get_response(cache_key, request.header.id)
    if cached_response is not None:
        if DNS_DEBUG_TIMINGS:
            elapsed_ms = (time.perf_counter() - start_ts) * 1000
            logger.debug("CACHE HIT %s %s from %s in %.2f ms", qtype, qname, client_ip, elapsed_ms)
        return cached_response

    # TXT
    if qtype == "TXT":
        response = _maybe_answer_acme_txt(request, qname, client_ip)
        if response is not None:
            _cache_set_response(cache_key, response, ACME_TTL)
            return response

    # CAA
    if qtype == "CAA":
        response = _maybe_answer_caa(request, qname, client_ip)
        if response is not None:
            _cache_set_response(cache_key, response, 300)
            return response

    # Authoritative zone metadata.
    if _match_domain(qname) and qtype == "NS" and _is_zone_apex(qname):
        response = _answer_zone_ns(request, qname, client_ip)
        _cache_set_response(cache_key, response, 300)
        return response

    if _match_domain(qname) and qtype == "SOA" and _is_zone_apex(qname):
        response = _answer_zone_soa(request, qname, client_ip)
        _cache_set_response(cache_key, response, 300)
        return response

    # Наши домены: кроме поддерживаемых типов отдаём пустой authoritative ответ с SOA.
    if _match_domain(qname) and qtype not in ("A", "TXT", "CAA", "NS", "SOA"):
        reply = request.reply()
        reply.header.aa = 1
        reply.header.ra = 0
        _add_authority_soa(reply, qname)
        response = reply.pack()
        _cache_set_response(cache_key, response, DNS_RESPONSE_CACHE_TTL)
        return response

    # A для наших доменов
    if qtype == "A" and _match_domain(qname):
        reply = request.reply()
        reply.header.aa = 1
        reply.header.ra = 0

        try:
            if PANEL_DOMAIN and PANEL_IP and _is_panel_domain(qname):
                reply.add_answer(
                    RR(
                        rname=qname,
                        rtype=QTYPE.A,
                        rclass=1,
                        ttl=TTL,
                        rdata=A(PANEL_IP),
                    )
                )
                if DNS_VERBOSE_QUERY_LOGS:
                    logger.info(
                        "📤 PANEL %s -> %s (client=%s, TTL=%s)",
                        qname,
                        PANEL_IP,
                        client_ip,
                        TTL,
                    )
                response = reply.pack()
                _cache_set_response(cache_key, response, TTL)
                return response

            ip, loc_pref, loc_name, sub_id = _choose_ip_cached(qname)

            if not ip:
                reply.header.rcode = 3
                _add_authority_soa(reply, qname)
                if DNS_VERBOSE_QUERY_LOGS:
                    logger.info("NXDOMAIN for %s (client=%s)", qname, client_ip)
                response = reply.pack()
                _cache_set_response(cache_key, response, DNS_NEGATIVE_CACHE_TTL)
                return response

            reply.add_answer(
                RR(
                    rname=qname,
                    rtype=QTYPE.A,
                    rclass=1,
                    ttl=TTL,
                    rdata=A(ip),
                )
            )

            if DNS_VERBOSE_QUERY_LOGS:
                logger.info(
                    "📤 %s -> %s (%s), client=%s, loc=%s, sub_id=%s, TTL=%s",
                    qname,
                    ip,
                    loc_name,
                    client_ip,
                    loc_pref,
                    sub_id,
                    TTL,
                )

            response = reply.pack()
            _cache_set_response(cache_key, response, TTL)
            return response

        except Exception as exc:
            logger.exception("A-query processing failed for %s: %s", qname, exc)
            reply = request.reply()
            reply.header.aa = 1
            reply.header.ra = 0
            reply.header.rcode = 3
            _add_authority_soa(reply, qname)
            response = reply.pack()
            _cache_set_response(cache_key, response, DNS_NEGATIVE_CACHE_TTL)
            return response

    # Чужие домены -> upstream
    upstream_response = _forward_to_upstream(request)
    if upstream_response is not None:
        _cache_set_response(cache_key, upstream_response, DNS_UPSTREAM_CACHE_TTL)
        return upstream_response

    fallback = request.reply()
    fallback.header.aa = 1
    fallback.header.ra = 0
    fallback.header.rcode = 3
    response = fallback.pack()
    _cache_set_response(cache_key, response, DNS_NEGATIVE_CACHE_TTL)

    if DNS_DEBUG_TIMINGS:
        elapsed_ms = (time.perf_counter() - start_ts) * 1000
        logger.debug("handled %s %s for %s in %.2f ms", qtype, qname, client_ip, elapsed_ms)

    return response


class DNSHandler(socketserver.BaseRequestHandler):
    @timed_sync("dns.udp", slow_ms=100)
    def handle(self):
        data, sock = self.request
        client_ip, _client_port = self.client_address

        try:
            request = DNSRecord.parse(data)
        except Exception as exc:
            logger.warning("Parse error from %s: %s", client_ip, exc)
            return

        try:
            response = _process_dns_query(request, client_ip)
            sock.sendto(response, self.client_address)
        except Exception as exc:
            logger.warning("DNS send/handle error to %s: %s", client_ip, exc)


def _recv_exact(sock, size: int) -> Optional[bytes]:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class DNSTCPHandler(socketserver.BaseRequestHandler):
    @timed_sync("dns.tcp", slow_ms=100)
    def handle(self):
        client_ip, _client_port = self.client_address
        sock = self.request

        try:
            while True:
                length_raw = _recv_exact(sock, 2)
                if not length_raw:
                    return

                packet_len = int.from_bytes(length_raw, "big")
                if packet_len <= 0 or packet_len > 4096:
                    logger.warning("Invalid DNS TCP packet length from %s: %s", client_ip, packet_len)
                    return

                data = _recv_exact(sock, packet_len)
                if not data:
                    return

                try:
                    request = DNSRecord.parse(data)
                except Exception as exc:
                    logger.warning("TCP parse error from %s: %s", client_ip, exc)
                    return

                response = _process_dns_query(request, client_ip)
                sock.sendall(len(response).to_bytes(2, "big") + response)
        except Exception as exc:
            logger.warning("DNS TCP handle error to %s: %s", client_ip, exc)


class ThreadedUDPServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True
    daemon_threads = True


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def create_dns_server() -> ThreadedUDPServer:
    global DNS_SERVER

    if DNS_SERVER is not None:
        return DNS_SERVER

    host = os.getenv("DNS_BIND_HOST", "0.0.0.0")
    port = int(os.getenv("DNS_BIND_PORT", "53"))

    DNS_SERVER = ThreadedUDPServer((host, port), DNSHandler)
    logger.info(
        "DNS UDP server initialized on %s:%d (upstream=%s:%d)",
        host,
        port,
        UPSTREAM_DNS,
        UPSTREAM_PORT,
    )
    return DNS_SERVER


def create_dns_tcp_server() -> ThreadedTCPServer:
    global DNS_TCP_SERVER

    if DNS_TCP_SERVER is not None:
        return DNS_TCP_SERVER

    host = os.getenv("DNS_TCP_BIND_HOST", "").strip()
    if not host:
        udp_host = os.getenv("DNS_BIND_HOST", "0.0.0.0").strip()
        host = PANEL_IP if udp_host in ("", "0.0.0.0", "::") and PANEL_IP else udp_host
    port = int(os.getenv("DNS_BIND_PORT", "53"))

    DNS_TCP_SERVER = ThreadedTCPServer((host, port), DNSTCPHandler)
    logger.info("DNS TCP server initialized on %s:%d", host, port)
    return DNS_TCP_SERVER


def run_dns_server_blocking():
    server = create_dns_server()
    tcp_server = create_dns_tcp_server()
    tcp_thread = threading.Thread(target=tcp_server.serve_forever, daemon=True)
    tcp_thread.start()
    logger.info("DNS TCP server started in background")
    logger.info("DNS UDP server started (blocking mode)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping DNS UDP server")
    finally:
        server.server_close()
        logger.info("DNS UDP server stopped")


def start_dns_server_in_background():
    global DNS_THREAD, DNS_TCP_THREAD

    server = create_dns_server()
    tcp_server = create_dns_tcp_server()

    if DNS_THREAD is not None and DNS_THREAD.is_alive():
        if DNS_TCP_THREAD is None or not DNS_TCP_THREAD.is_alive():
            DNS_TCP_THREAD = threading.Thread(target=tcp_server.serve_forever, daemon=True)
            DNS_TCP_THREAD.start()
            logger.info("DNS TCP server started in background")
        return server, DNS_THREAD

    DNS_THREAD = threading.Thread(target=server.serve_forever, daemon=True)
    DNS_THREAD.start()
    logger.info("DNS UDP server started in background")

    DNS_TCP_THREAD = threading.Thread(target=tcp_server.serve_forever, daemon=True)
    DNS_TCP_THREAD.start()
    logger.info("DNS TCP server started in background")
    return server, DNS_THREAD


def stop_dns_server():
    global DNS_SERVER, DNS_THREAD, DNS_TCP_SERVER, DNS_TCP_THREAD

    if DNS_SERVER is not None:
        try:
            DNS_SERVER.shutdown()
            DNS_SERVER.server_close()
            logger.info("DNS UDP server stopped cleanly")
        except Exception as exc:
            logger.warning("DNS stop error: %s", exc)
        finally:
            DNS_SERVER = None

    if DNS_THREAD is not None:
        try:
            DNS_THREAD.join(timeout=1.0)
        except Exception:
            pass
        DNS_THREAD = None

    if DNS_TCP_SERVER is not None:
        try:
            DNS_TCP_SERVER.shutdown()
            DNS_TCP_SERVER.server_close()
            logger.info("DNS TCP server stopped cleanly")
        except Exception as exc:
            logger.warning("DNS TCP stop error: %s", exc)
        finally:
            DNS_TCP_SERVER = None

    if DNS_TCP_THREAD is not None:
        try:
            DNS_TCP_THREAD.join(timeout=1.0)
        except Exception:
            pass
        DNS_TCP_THREAD = None


if __name__ == "__main__":
    run_dns_server_blocking()
