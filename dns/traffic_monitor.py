#!/usr/bin/env python3
# traffic_monitor.py
from __future__ import annotations

import os
import re
import asyncio
import time
import json
import logging
import socket  # Добавлен импорт socket в начало
import statistics
import aiohttp
from typing import Dict, Any, List, Optional, Tuple, Callable

# ---- Settings ----
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(MODULE_DIR, "traffic_data.json")
LOG_FILE = os.path.join(MODULE_DIR, "traffic_monitor.log")

SAMPLE_RATE = 5.0
WINDOW_SECS = 60.0
CONNECT_TIMEOUT = 1.5
LOG_LEVEL = "INFO"

# Создаем директорию для логов если нет
os.makedirs(MODULE_DIR, exist_ok=True)

# Настройка логирования
logging.basicConfig(
    level=LOG_LEVEL, 
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
LOG = logging.getLogger("traffic_monitor")

def _host_to_ip(host: str) -> Optional[str]:
    """Преобразует домен в IP для traffic monitor"""
    if not host:
        return None
        
    # Проверяем, это уже IP?
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass

    # Специфичные маппинги для ваших серверов
    env_lt = os.getenv("KINGLINE_LT_IP", "").strip()
    env_nl = os.getenv("KINGLINE_NL_IP", "").strip()
    
    if "vpn.kingline.online" in host and env_lt:
        return env_lt
    if "net.kingline.online" in host and env_nl:
        return env_nl

    # Пробуем DNS
    try:
        return socket.gethostbyname(host)
    except (socket.gaierror, OSError):
        return host  # Возвращаем домен как fallback

class TrafficMonitor:
    """
    Асинхронный монитор трафика для сбора метрик с X-UI панелей.
    """
    
    def __init__(
        self,
        *,
        poll_interval: float = 60.0,
        on_cycle_complete: Optional[Callable[[Dict[str, Dict[str, Any]]], None]] = None,
    ):
        self.servers = self._discover_panels()
        self._session: Optional[aiohttp.ClientSession] = None
        self._stop: bool = False
        self._last_cleanup: float = time.time()
        self._task: Optional[asyncio.Task] = None
        self._poll_interval = float(poll_interval)
        self._on_cycle_complete = on_cycle_complete
        
        LOG.info(f"TrafficMonitor инициализирован. Серверов: {len(self.servers)}, интервал: {poll_interval}с")

    def _discover_panels(self) -> Dict[str, Dict[str, str]]:
        """Обнаруживает панели из переменных окружения."""
        pattern = re.compile(r"^XUI_BASE_(\d+)$")
        servers: Dict[str, Dict[str, str]] = {}
        
        for k, v in list(os.environ.items()):
            m = pattern.match(k)
            if not m:
                continue
                
            idx = m.group(1)
            base = v.strip()
            cookie = os.environ.get(f"XUI_COOKIE_{idx}", "").strip()
            
            if not cookie:
                LOG.warning("Найден %s, но XUI_COOKIE_%s не задан — пропускаем", base, idx)
                continue

            try:
                from urllib.parse import urlparse
                parsed = urlparse(base)
                host = parsed.hostname or base.rstrip("/").split("/")[-1]
            except Exception as e:
                LOG.warning("Ошибка парсинга URL %s: %s", base, e)
                host = base.rstrip("/").split("/")[-1]

            name = host
            servers[name] = {"base": base, "cookie": cookie}
            LOG.debug("Добавлен сервер: %s -> %s", name, base)
        
        LOG.info("Обнаружено %d серверов для мониторинга: %s", 
                len(servers), ", ".join(servers.keys()))
        return servers

    async def _ensure_session(self) -> None:
        """Создает aiohttp сессию если ее нет."""
        if not self._session:
            self._session = aiohttp.ClientSession(
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                    "User-Agent": "traffic-monitor/1.0",
                },
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def _fetch_status(self, base: str, cookie: str, timeout: int = 10) -> Dict[str, Any]:
        """Запрашивает статус сервера."""
        await self._ensure_session()
        url = self._choose_status_url(base)
        
        try:
            async with self._session.get(
                url, 
                headers={"Cookie": cookie}, 
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                txt = await response.text()
                
                if response.status != 200:
                    LOG.warning("HTTP %d от %s: %s", response.status, url, txt[:200])
                    return {}
                    
                try:
                    data = json.loads(txt)
                except json.JSONDecodeError as e:
                    LOG.warning("Не JSON от %s: %s. Ошибка: %s", url, txt[:200], e)
                    return {}
                    
                if isinstance(data, dict) and "obj" in data and isinstance(data["obj"], dict):
                    return data["obj"]
                if isinstance(data, dict):
                    return data
                else:
                    LOG.warning("Неожиданный формат ответа от %s: %s", url, type(data))
                    return {}
                    
        except asyncio.TimeoutError:
            LOG.warning("Таймаут запроса к %s", url)
            return {}
        except Exception as e:
            LOG.warning("Ошибка запроса статуса %s: %s", url, e)
            return {}

    def _choose_status_url(self, base: str) -> str:
        """Выбирает правильный URL для запроса статуса."""
        b = base.rstrip("/")
        if b.endswith("/panel"):
            return f"{b}/api/server/status"
        return f"{b}/panel/api/server/status"

    @staticmethod
    def _extract_bytes(obj: Dict[str, Any]) -> Tuple[int, int]:
        """Извлекает значения переданных/принятых байт из объекта статуса."""
        up = down = 0
        try:
            nio = obj.get("netIO", {})
            if isinstance(nio, dict):
                up = int(nio.get("up", up) or up)
                down = int(nio.get("down", down) or down)
        except (ValueError, TypeError):
            pass
            
        if up == 0 and down == 0:
            try:
                nt = obj.get("netTraffic", {})
                if isinstance(nt, dict):
                    up = int(nt.get("sent", up) or up)
                    down = int(nt.get("recv", down) or down)
            except (ValueError, TypeError):
                pass
                
        return int(up or 0), int(down or 0)

    async def _get_online_clients(self, base: str, cookie: str) -> int:
        """Получает количество онлайн клиентов."""
        url = f"{base.rstrip('/')}/api/inbounds/onlines"
        try:
            await self._ensure_session()
            async with self._session.post(
                url, 
                headers={"Cookie": cookie}, 
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                txt = await response.text()
                
                if response.status != 200:
                    LOG.debug("HTTP %d при получении онлайн клиентов: %s", response.status, txt[:100])
                    return 0
                    
                try:
                    data = json.loads(txt)
                    if isinstance(data, dict) and "obj" in data and isinstance(data["obj"], list):
                        return len(data["obj"])
                    if isinstance(data, list):
                        return len(data)
                    else:
                        LOG.debug("Неожиданный формат онлайн клиентов: %s", type(data))
                        return 0
                except Exception as e:
                    LOG.debug("Ошибка парсинга онлайн клиентов: %s", e)
                    return 0
                    
        except Exception as e:
            LOG.debug("Ошибка получения онлайн клиентов %s: %s", url, e)
            return 0

    async def _measure_latency(self, host: str) -> float:
        """Измеряет задержку до сервера."""
        try:
            if "://" in host:
                host = host.split("://")[1].split("/")[0]
            host = host.split(":")[0]
            start = time.monotonic()
            fut = asyncio.open_connection(host, 443)
            coro = asyncio.wait_for(fut, timeout=CONNECT_TIMEOUT)
            reader, writer = await coro
            latency = time.monotonic() - start
            writer.close()
            await writer.wait_closed()
            return max(0.0, latency)
        except asyncio.TimeoutError:
            LOG.debug("Таймаут измерения задержки для %s", host)
            return -1.0
        except Exception as e:
            LOG.debug("Ошибка измерения задержки для %s: %s", host, e)
            return -1.0

    async def _measure_speed(self, cfg: Dict[str, str]) -> Dict[str, float]:
        """Измеряет скорость передачи данных."""
        samples: List[float] = []
        obj = await self._fetch_status(cfg["base"], cfg["cookie"])
        up0, down0 = self._extract_bytes(obj)
        t0 = time.monotonic()
        last_up, last_down = up0, down0
        last_t = t0
        end = t0 + 10

        while time.monotonic() < end and not self._stop:
            await asyncio.sleep(2)
            o = await self._fetch_status(cfg["base"], cfg["cookie"])
            upn, downn = self._extract_bytes(o)
            tn = time.monotonic()
            delta = max(0, (upn - last_up) + (downn - last_down))
            dt = max(1e-6, tn - last_t)
            inst = delta / dt
            samples.append(inst)
            last_up, last_down = upn, downn
            last_t = tn

        if not samples:
            return {"avg": 0.0, "peak": 0.0}

        return {"avg": statistics.mean(samples), "peak": max(samples) if samples else 0.0}

    async def collect_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Собирает метрики со всех серверов."""
        metrics: Dict[str, Dict[str, Any]] = {}
        
        if not self.servers:
            LOG.warning("Нет серверов для мониторинга!")
            return metrics
            
        LOG.info("Начинаем сбор метрик для %d серверов", len(self.servers))
        
        for host, cfg in self.servers.items():
            try:
                LOG.debug("Сбор метрик для %s", host)
                online_clients = await self._get_online_clients(cfg["base"], cfg["cookie"])
                speed_stats = await self._measure_speed(cfg)
                latency = await self._measure_latency(cfg["base"])
                
                # Преобразуем домен в IP для хранения
                host_ip = _host_to_ip(host) or host
                
                metrics[host_ip] = {  # Сохраняем по IP вместо домена
                    "online_clients": online_clients,
                    "speed_avg": speed_stats["avg"],
                    "speed_peak": speed_stats["peak"],
                    "latency": latency,
                    "timestamp": time.time(),
                    "original_host": host  # Сохраняем оригинальное имя для отладки
                }
                
                LOG.info("Собраны метрики для %s -> %s: %d клиентов, скорость=%.1f, задержка=%.3fs",
                         host, host_ip, online_clients, speed_stats["avg"], latency)
                         
            except Exception as e:
                LOG.error("Ошибка сбора метрик для %s: %s", host, e)
                host_ip = _host_to_ip(host) or host
                metrics[host_ip] = {
                    "online_clients": 0,
                    "speed_avg": 0.0,
                    "speed_peak": 0.0,
                    "latency": -1.0,
                    "timestamp": time.time(),
                    "original_host": host
                }

        return metrics

    def load_data(self) -> List[Dict[str, Any]]:
        """Загружает исторические данные из файла."""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        LOG.debug("Загружено %d записей из %s", len(data), DATA_FILE)
                        return data
        except Exception as e:
            LOG.error("Ошибка загрузки данных из %s: %s", DATA_FILE, e)
        return []

    def save_data(self, data: List[Dict[str, Any]]) -> None:
        """Сохраняет данные в файл."""
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            LOG.debug("Сохранено %d записей в %s", len(data), DATA_FILE)
        except Exception as e:
            LOG.error("Ошибка сохранения данных в %s: %s", DATA_FILE, e)

    def cleanup_old_data(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Очищает старые данные (старше 6 часов)."""
        now = time.time()
        six_hours_ago = now - 6 * 3600
        old_count = len(data)
        cleaned = [entry for entry in data if entry.get('timestamp', 0) > six_hours_ago]
        new_count = len(cleaned)
        
        if old_count != new_count:
            LOG.info("Очистка данных: %d -> %d записей (удалено %d старых)", 
                    old_count, new_count, old_count - new_count)
                    
        return cleaned

    async def run_monitoring_cycle(self) -> None:
        """Выполняет один цикл мониторинга."""
        LOG.info("Запуск цикла мониторинга...")
        
        try:
            metrics = await self.collect_metrics()
            data = self.load_data()

            current_time = time.time()
            if current_time - self._last_cleanup >= 6 * 3600:
                data = self.cleanup_old_data(data)
                self._last_cleanup = current_time

            entry = {"timestamp": current_time, "metrics": metrics}
            data.append(entry)
            self.save_data(data)

            if self._on_cycle_complete:
                try:
                    self._on_cycle_complete(metrics)
                except Exception as e:
                    LOG.warning("on_cycle_complete callback failed: %s", e)

            # Логируем статус
            LOG.info("Метрики собраны. Серверы: %s", list(metrics.keys()))
            
        except Exception as e:
            LOG.error("Критическая ошибка в цикле мониторинга: %s", e)

    async def _background_loop(self) -> None:
        """Основной цикл работы в фоне."""
        LOG.info("TrafficMonitor background loop started")
        cycle_count = 0
        
        while not self._stop:
            try:
                cycle_count += 1
                LOG.debug("Цикл мониторинга #%d", cycle_count)
                await self.run_monitoring_cycle()
            except Exception as e:
                LOG.error("Ошибка в цикле мониторинга #%d: %s", cycle_count, e)
                
            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                LOG.info("Фоновая задача отменена")
                break
                
        LOG.info("TrafficMonitor background loop exiting")

    def start_background(self) -> None:
        """Запускает мониторинг в фоне."""
        if self._task and not self._task.done():
            LOG.warning("TrafficMonitor уже запущен")
            return
            
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # Если нет текущего loop, создаем новый
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        self._stop = False
        self._task = loop.create_task(self._background_loop())
        LOG.info("Фоновая задача TrafficMonitor создана")

    async def stop(self) -> None:
        """Останавливает мониторинг."""
        LOG.info("Остановка TrafficMonitor...")
        self._stop = True
        
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
                LOG.info("Фоновая задача корректно завершена")
            except asyncio.TimeoutError:
                LOG.warning("Таймаут ожидания завершения фоновой задачи")
                self._task.cancel()
            except Exception as e:
                LOG.warning("Ошибка при остановке фоновой задачи: %s", e)
            finally:
                self._task = None

        if self._session:
            await self._session.close()
            self._session = None
            LOG.info("HTTP сессия закрыта")

__all__ = ["TrafficMonitor", "DATA_FILE"]