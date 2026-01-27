from __future__ import annotations

import os
import logging
import socketserver
import threading
from typing import Optional

from dnslib import DNSRecord, QTYPE, RR, A

# ---------- Загрузка отдельного .env из папки dns ----------

try:
    from dotenv import load_dotenv  # тот же пакет, что у тебя в config.py
except ImportError:
    load_dotenv = None

BASE_DIR = os.path.dirname(__file__)
DNS_ENV_PATH = os.path.join(BASE_DIR, ".env")

if load_dotenv is not None and os.path.exists(DNS_ENV_PATH):
    load_dotenv(DNS_ENV_PATH)

# Балансер локаций
try:
    from .dns_balancer import DNSBalancer  # если файл в том же пакете dns/
except ImportError:  # pragma: no cover
    from dns_balancer import DNSBalancer   # fallback, если запускаешь как скрипт

# Локатор пользователя по sub_id
try:
    from .user_locator import DNSUserLocator
except ImportError:  # pragma: no cover
    from user_locator import DNSUserLocator

BALANCER = DNSBalancer()

# ---------- Конфиг из окружения (dns/.env и общий .env) ----------

RAW_IPS = os.getenv("KINGLINE_DNS_IPS", "")
IPS = [
    part.strip()
    for part in RAW_IPS.replace(";", ",").split(",")
    if part.strip()
]

RAW_DOMAINS = os.getenv("KINGLINE_DNS_DOMAINS", "")
DOMAINS = [
    d.strip().strip(".").lower()
    for d in RAW_DOMAINS.replace(";", ",").split(",")
    if d.strip()
]

PANEL_DOMAIN = os.getenv("KINGLINE_DNS_PANEL_DOMAIN", "").strip().strip(".").lower()
PANEL_IP = os.getenv("KINGLINE_DNS_PANEL_IP", "").strip()

TTL = int(os.getenv("KINGLINE_DNS_TTL", "30"))

# Апстрим-DNS для всех «чужих» доменов
UPSTREAM_DNS = os.getenv("DNS_UPSTREAM", "1.1.1.1").strip()
UPSTREAM_PORT = int(os.getenv("DNS_UPSTREAM_PORT", "53"))

LOG_LEVEL = os.getenv("DNS_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | dns_server | %(message)s",
)
logger = logging.getLogger("dns_server")

if not IPS:
    # если список IP не задан, берём из BALANCER.pool (на всякий случай)
    IPS = BALANCER.pool

if not IPS:
    logger.warning(
        "KINGLINE_DNS_IPS не задана или пуста — сервер будет отвечать NXDOMAIN для наших доменов."
    )
else:
    logger.info("Загружено %d IP для балансировки: %s", len(IPS), ", ".join(IPS))

if DOMAINS:
    logger.info("Ограничиваемся доменами: %s", ", ".join(DOMAINS))
else:
    logger.info("KINGLINE_DNS_DOMAINS не заданы — наши правила применяются ко ВСЕМ A-запросам.")

if PANEL_DOMAIN and PANEL_IP:
    logger.info(
        "Панель %s всегда будет резолвиться в %s (без балансировки)",
        PANEL_DOMAIN,
        PANEL_IP,
    )
else:
    logger.info(
        "Панельный домен / IP не заданы (KINGLINE_DNS_PANEL_DOMAIN / KINGLINE_DNS_PANEL_IP)."
    )

# Инициализируем локатор пользователей
DB_PATH = os.getenv("DB_PATH", "vpn_bot.db")
USER_LOCATOR = DNSUserLocator(
    db_path=DB_PATH, 
    domains=DOMAINS,
    traffic_data_file=os.getenv("TRAFFIC_DATA_FILE")  # Передаем путь к файлу трафика
)
logger.info("DNSUserLocator инициализирован (DB_PATH=%s)", DB_PATH)


def _match_domain(qname: str) -> bool:
    """
    Проверяем, подпадает ли имя под список доменов.
    Если DOMAINS пуст — считаем, что матчим всё.
    """
    if not DOMAINS:
        return True
    name = qname.rstrip(".").lower()
    for d in DOMAINS:
        if name == d or name.endswith("." + d):
            return True
    return False


def _is_panel_domain(qname: str) -> bool:
    """
    Проверяем, относится ли имя к домену панели.

    ВАЖНО: панель — это только СТРОГОЕ совпадение с PANEL_DOMAIN.
    Поддомены (типа <sub_id>.sub.kingline.online) не считаем панелью,
    чтобы они шли через USER_LOCATOR/балансер.
    """
    if not PANEL_DOMAIN:
        return False
    name = qname.rstrip(".").lower()
    return name == PANEL_DOMAIN  # без endswith("." + PANEL_DOMAIN)!


def _forward_to_upstream(request: DNSRecord) -> Optional[bytes]:
    """
    Форвардим DNS-запрос целиком на апстрим (1.1.1.1 / 8.8.8.8 и т.п.).
    Возвращаем сырые байты ответа или None при ошибке.
    """
    if not UPSTREAM_DNS:
        return None

    try:
        qname = str(request.q.qname)
        logger.info("↪️ Форвардим запрос %s на апстрим %s:%d", qname, UPSTREAM_DNS, UPSTREAM_PORT)
        resp_bytes = request.send(UPSTREAM_DNS, UPSTREAM_PORT, timeout=2)
        return resp_bytes
    except Exception as e:
        logger.warning("Ошибка при форвардинге на %s:%d: %s", UPSTREAM_DNS, UPSTREAM_PORT, e)
        return None


class DNSHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        client_ip, client_port = self.client_address

        try:
            request = DNSRecord.parse(data)
        except Exception as e:
            logger.warning("Не удалось распарсить запрос от %s: %s", client_ip, e)
            return

        q = request.q
        qname = str(q.qname)
        qtype = QTYPE.get(q.qtype, "UNKNOWN")

        logger.info("📥 Запрос от %s:%s — %s %s", client_ip, client_port, qname, qtype)

        # --- Наши домены: включаем магию с sub_id и локациями ---
        if qtype == "A" and _match_domain(qname):
            reply = request.reply()
            # авторитетный ответ, рекурсии не даём (для наших доменов)
            reply.header.aa = 1
            reply.header.ra = 0

            try:
                # 1) Статический IP для панели (если задан)
                if PANEL_DOMAIN and PANEL_IP and _is_panel_domain(qname):
                    ip = PANEL_IP
                    logger.info(
                        "📤 Ответ для ПАНЕЛИ %s (client=%s): статический IP %s (TTL=%s)",
                        qname,
                        client_ip,
                        ip,
                        TTL,
                    )
                    reply.add_answer(
                        RR(
                            rname=qname,
                            rtype=QTYPE.A,
                            rclass=1,
                            ttl=TTL,
                            rdata=A(ip),
                        )
                    )

                # 2) Все остальные: sub_id + user_locator + балансер
                elif IPS:
                    try:
                        result = USER_LOCATOR.choose_ip_for_qname(qname)

                        # Поддерживаем и (ip, loc_pref, loc_name),
                        # и (ip, loc_pref, loc_name, sub_id)
                        if not isinstance(result, tuple):
                            raise ValueError(
                                f"USER_LOCATOR вернул не tuple: {type(result)!r}"
                            )

                        if len(result) == 3:
                            ip, loc_pref, loc_name = result
                            sub_id_for_log = None
                        elif len(result) >= 4:
                            ip, loc_pref, loc_name, sub_id_for_log = (
                                result[0],
                                result[1],
                                result[2],
                                result[3],
                            )
                        else:
                            raise ValueError(
                                f"USER_LOCATOR вернул tuple странной длины: {len(result)}"
                            )

                    except Exception as e:
                        logger.exception(
                            "Ошибка в USER_LOCATOR для %s: %s — откатываемся к auto/первому IP",
                            qname,
                            e,
                        )
                        ip = IPS[0] if IPS else None
                        loc_pref = "auto"
                        loc_name = BALANCER.describe_ip(ip) if ip else "Нет IP"
                        sub_id_for_log = None

                    # fallback, если локатор вернул None
                    if not ip:
                        if IPS:
                            ip = IPS[0]
                            loc_pref = "auto"
                            loc_name = BALANCER.describe_ip(ip)
                            sub_id_for_log = None
                        else:
                            logger.info(
                                "NXDOMAIN для %s (client=%s): нет IP в пуле IPS даже для fallback",
                                qname,
                                client_ip,
                            )
                            reply.header.rcode = 3  # NXDOMAIN

                    if ip:
                        logger.info(
                            "📤 Ответ для %s (client=%s, loc_pref=%s, sub_id=%s): выбран IP %s (%s) (TTL=%s)",
                            qname,
                            client_ip,
                            loc_pref,
                            sub_id_for_log,
                            ip,
                            loc_name,
                            TTL,
                        )
                        reply.add_answer(
                            RR(
                                rname=qname,
                                rtype=QTYPE.A,
                                rclass=1,
                                ttl=TTL,
                                rdata=A(ip),
                            )
                        )

                else:
                    # Нет IP в пуле
                    logger.info(
                        "NXDOMAIN для %s (client=%s): нет IP в пуле IPS",
                        qname,
                        client_ip,
                    )
                    reply.header.rcode = 3  # NXDOMAIN

            except Exception as e:
                # Любая ошибка в нашей логике → логируем и шлём NXDOMAIN, чтобы не вешать клиента
                logger.exception("Исключение при обработке нашего домена %s: %s", qname, e)
                reply = request.reply()
                reply.header.aa = 1
                reply.header.ra = 0
                reply.header.rcode = 3  # NXDOMAIN

            try:
                sock.sendto(reply.pack(), self.client_address)
            except Exception as e:
                logger.warning("Ошибка отправки ответа клиенту %s: %s", client_ip, e)
            return

        # --- НЕ наши домены: форвардим на апстрим и возвращаем как есть ---
        try:
            upstream_resp = _forward_to_upstream(request)
        except Exception as e:
            logger.exception("Исключение при форвардинге %s: %s", qname, e)
            upstream_resp = None

        if upstream_resp is not None:
            try:
                sock.sendto(upstream_resp, self.client_address)
            except Exception as e:
                logger.warning("Ошибка отправки форварженного ответа клиенту %s: %s", client_ip, e)
            return

        # Если до сюда дошли — ни апстрима, ни нашего ответа → NXDOMAIN
        logger.info(
            "NXDOMAIN (без апстрима) для %s (client=%s): тип=%s",
            qname,
            client_ip,
            qtype,
        )
        fallback = request.reply()
        fallback.header.aa = 1
        fallback.header.ra = 0
        fallback.header.rcode = 3  # NXDOMAIN
        try:
            sock.sendto(fallback.pack(), self.client_address)
        except Exception as e:
            logger.warning("Ошибка отправки fallback-ответа клиенту %s: %s", client_ip, e)


class ThreadedUDPServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    allow_reuse_address = True


DNS_SERVER: ThreadedUDPServer | None = None
DNS_THREAD: threading.Thread | None = None


def create_dns_server() -> ThreadedUDPServer:
    """
    Создаёт (один раз) экземпляр DNS-сервера и возвращает его.
    """
    global DNS_SERVER

    if DNS_SERVER is not None:
        return DNS_SERVER

    host = os.getenv("DNS_BIND_HOST", "0.0.0.0")
    port = int(os.getenv("DNS_BIND_PORT", "53"))

    DNS_SERVER = ThreadedUDPServer((host, port), DNSHandler)
    logger.info(
        "DNS-балансер инициализирован на %s:%d (upstream=%s:%d)",
        host,
        port,
        UPSTREAM_DNS,
        UPSTREAM_PORT,
    )
    return DNS_SERVER


def run_dns_server_blocking():
    """
    Блокирующий запуск (для самостоятельного запуска как скрипта).
    """
    server = create_dns_server()
    logger.info("DNS-балансер запущен (blocking mode)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Останавливаем DNS-сервер (Ctrl+C)")
    finally:
        server.server_close()
        logger.info("DNS-сервер остановлен")


def start_dns_server_in_background():
    """
    Запускает DNS-сервер в отдельном потоке (НЕ блокирует основной поток).
    Удобно вызывать из main.py бота.
    """
    global DNS_THREAD

    server = create_dns_server()

    # если уже запущен — ничего не делаем
    if DNS_THREAD is not None and DNS_THREAD.is_alive():
        return server, DNS_THREAD

    DNS_THREAD = threading.Thread(target=server.serve_forever, daemon=True)
    DNS_THREAD.start()
    logger.info("DNS-балансер запущен в фоне (thread)")
    return server, DNS_THREAD


def stop_dns_server():
    """
    Аккуратная остановка сервера (используется из main.py).
    """
    global DNS_SERVER, DNS_THREAD

    if DNS_SERVER is not None:
        try:
            DNS_SERVER.shutdown()
            DNS_SERVER.server_close()
            logger.info("DNS-балансер корректно остановлен")
        except Exception as e:
            logger.warning("Ошибка при остановке DNS-сервера: %s", e)
        finally:
            DNS_SERVER = None

    if DNS_THREAD is not None:
        try:
            DNS_THREAD.join(timeout=1.0)
        except Exception:
            pass
        DNS_THREAD = None


if __name__ == "__main__":
    run_dns_server_blocking()
