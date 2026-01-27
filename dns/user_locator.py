from __future__ import annotations

import sqlite3
import os
import time
import json
import socket
import logging
from typing import Optional, List, Tuple, Dict, Any

try:
    from db import SubscriptionDB
except ImportError:
    # Fallback для случая, когда db.py в той же директории
    from db import SubscriptionDB

try:
    from .dns_balancer import DNSBalancer
except ImportError:
    from dns_balancer import DNSBalancer

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))

# НАСТРОЙКА ЛОГГЕРА - ЭТО КРИТИЧЕСКИ ВАЖНО!
logger = logging.getLogger("dns_user_locator")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

def _is_ip(s: str) -> bool:
    try:
        socket.inet_aton(s)
        return True
    except Exception:
        return False

def _host_to_ip(host: str) -> Optional[str]:
    """
    Попытаться преобразовать host -> IP.
    """
    if not host:
        return None
    if _is_ip(host):
        return host

    panel_domain = os.getenv("KINGLINE_DNS_PANEL_DOMAIN", "").strip().strip(".")
    panel_ip = os.getenv("KINGLINE_DNS_PANEL_IP", "").strip()
    if panel_domain and host.endswith(panel_domain) and panel_ip:
        return panel_ip

    # check explicit env mappings
    env_lt = os.getenv("KINGLINE_LT_IP", "").strip()
    env_nl = os.getenv("KINGLINE_NL_IP", "").strip()
    xui_lt = os.getenv("XUI_LT_URL", "").strip().rstrip("/")
    xui_nl = os.getenv("XUI_NL_URL", "").strip().rstrip("/")

    try:
        from urllib.parse import urlparse
        if xui_lt:
            p = urlparse(xui_lt)
            if p.hostname and host.endswith(p.hostname) and env_lt:
                return env_lt
        if xui_nl:
            p = urlparse(xui_nl)
            if p.hostname and host.endswith(p.hostname) and env_nl:
                return env_nl
    except Exception:
        pass

    # try direct env match (rare)
    if env_lt and (host == env_lt or host.endswith(env_lt)):
        return env_lt
    if env_nl and (host == env_nl or host.endswith(env_nl)):
        return env_nl

    # try DNS resolution
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None

class DNSAssignmentDB:
    """
    База данных для хранения привязки sub_id к IP на 24 часа.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        logger.info(f"Инициализация DNSAssignmentDB с путем: {db_path}")
        self._create_table()

    def _migrate_table(self):
        """Миграция таблицы для добавления отсутствующих столбцов"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Проверяем существующие столбцы
                cursor = conn.execute("PRAGMA table_info(dns_assignments)")
                existing_columns = [column[1] for column in cursor.fetchall()]
                
                # Добавляем last_checked если отсутствует
                if 'last_checked' not in existing_columns:
                    logger.info("Добавляем столбец last_checked в таблицу dns_assignments")
                    conn.execute("ALTER TABLE dns_assignments ADD COLUMN last_checked INTEGER")
                    # Устанавливаем значение по умолчанию для существующих записей
                    conn.execute("UPDATE dns_assignments SET last_checked = assigned_at")
                    
                logger.info("Миграция таблицы dns_assignments завершена")
        except Exception as e:
            logger.error(f"Ошибка миграции таблицы dns_assignments: {e}")

    def _create_table(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS dns_assignments (
                        sub_id TEXT PRIMARY KEY,
                        ip TEXT NOT NULL,
                        assigned_at INTEGER NOT NULL,
                        last_checked INTEGER NOT NULL
                    )
                ''')
                logger.info("Таблица dns_assignments создана или уже существует")
                
                # Выполняем миграцию после создания таблицы
                self._migrate_table()
        except Exception as e:
            logger.error(f"Ошибка создания таблицы dns_assignments: {e}")
            raise

    def get_assignment(self, sub_id: str) -> Optional[Tuple[str, int, int]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT ip, assigned_at, last_checked FROM dns_assignments WHERE sub_id = ?",
                    (sub_id,)
                ).fetchone()
            return (row[0], row[1], row[2]) if row else None
        except Exception as e:
            logger.error(f"Ошибка получения привязки для {sub_id}: {e}")
            return None

    def set_assignment(self, sub_id: str, ip: str) -> None:
        """
        Сохраняет IP (обязательно IP, не домен).
        """
        logger.info(f"Попытка установить привязку для {sub_id} -> {ip}")
        
        if not _is_ip(ip):
            ip_conv = _host_to_ip(ip)
            if ip_conv:
                ip = ip_conv
                logger.info(f"Преобразовано в IP: {ip}")
            else:
                error_msg = f"Не удалось преобразовать '{ip}' в IP для записи в dns_assignments"
                logger.error(error_msg)
                raise ValueError(error_msg)
        
        assigned_at = int(time.time())
        last_checked = assigned_at
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO dns_assignments (sub_id, ip, assigned_at, last_checked) VALUES (?, ?, ?, ?)",
                    (sub_id, ip, assigned_at, last_checked)
                )
            logger.info(f"УСПЕШНО: Привязка для {sub_id} -> {ip} сохранена в БД")
        except Exception as e:
            logger.error(f"ОШИБКА сохранения привязки для {sub_id}: {e}")
            raise

    def update_last_checked(self, sub_id: str) -> None:
        """Обновляет время последней проверки привязки"""
        last_checked = int(time.time())
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE dns_assignments SET last_checked = ? WHERE sub_id = ?",
                    (last_checked, sub_id)
                )
        except Exception as e:
            logger.error(f"Ошибка обновления last_checked для {sub_id}: {e}")

    def is_assignment_valid(self, assigned_at: int) -> bool:
        """Базовая проверка: не прошло ли 24 часа"""
        return (time.time() - assigned_at) < 24 * 3600

class DNSUserLocator:
    def __init__(
        self,
        db_path: Optional[str] = None,
        domains: Optional[List[str]] = None,
        balancer: Optional[DNSBalancer] = None,
        traffic_data_file: Optional[str] = None,
    ) -> None:
        # Инициализация с подробным логированием
        db_path = db_path or os.getenv("DB_PATH", "vpn_bot.db")
        logger.info(f"Инициализация DNSUserLocator с DB_PATH: {db_path}")
        
        # Проверяем существование базы данных
        if not os.path.exists(db_path):
            logger.error(f"БАЗА ДАННЫХ НЕ НАЙДЕНА: {db_path}")
            logger.info(f"Создаем новую базу данных: {db_path}")
            # Создаем пустую базу если не существует
            try:
                with sqlite3.connect(db_path) as conn:
                    conn.execute("")
            except Exception as e:
                logger.error(f"Не удалось создать базу данных: {e}")
                raise
        
        try:
            self.db = SubscriptionDB(db_path)
            self.dns_assignments = DNSAssignmentDB(db_path)
            logger.info("Базы данных успешно инициализированы")
            
            # Проверяем соединение с базой
            test_result = self.dns_assignments.get_assignment("test_connection")
            logger.info("Тест соединения с БД: УСПЕХ")
            
        except Exception as e:
            logger.error(f"Ошибка инициализации баз данных: {e}")
            raise

        if domains is not None:
            doms = domains
        else:
            raw = os.getenv("KINGLINE_DNS_DOMAINS", "")
            doms = [d for d in raw.replace(";", ",").split(",") if d.strip()]

        self.domains: List[str] = [
            d.strip().strip(".").lower() for d in doms if d.strip()
        ]
        logger.info(f"Домены для обработки: {self.domains}")

        # Определяем файл с данными о трафике
        self.traffic_data_file = traffic_data_file or os.getenv("TRAFFIC_DATA_FILE") or os.path.join(MODULE_DIR, "traffic_data.json")
        logger.info(f"Файл трафика: {self.traffic_data_file}")

        if balancer is not None:
            self.balancer = balancer
        else:
            self.balancer = DNSBalancer(traffic_data_file=self.traffic_data_file)
        logger.info("Балансер инициализирован")

        # Настройки умного времени жизни
        self.MIN_ASSIGNMENT_AGE = 3600  # 1 час минимально
        self.MAX_ASSIGNMENT_AGE = 24 * 3600  # 24 часа максимально
        self.LOAD_DIFF_THRESHOLD = 100 * 1024 * 1024 / 8  # 100 Мбит/с в байтах/с
        self.SERVER_FAILURE_THRESHOLD = 300  # 5 минут отсутствия данных = отказ

        logger.info("DNSUserLocator успешно инициализирован")

    def _extract_sub_id_from_qname(self, qname: str) -> Optional[str]:
        name = qname.rstrip(".").lower()
        logger.debug(f"Извлечение sub_id из: {name}")

        if not self.domains:
            logger.debug("Нет доменов для поиска")
            return None

        for d in self.domains:
            base = d
            logger.debug(f"Проверка домена: {base}")
            
            if name == base:
                logger.debug("Точное совпадение с базовым доменом - не субдомен")
                return None
                
            if name.endswith("." + base):
                prefix = name[: -(len(base) + 1)]
                logger.debug(f"Найден субдомен, префикс: {prefix}")
                
                if not prefix:
                    logger.debug("Пустой префикс - не субдомен")
                    return None
                    
                # Берем последнюю часть как sub_id
                sub_id = prefix.split(".")[-1]
                logger.info(f"Извлечен sub_id: {sub_id} из qname: {name}")
                return sub_id or None

        logger.debug(f"Не найден подходящий домен для {name}")
        return None

    def _get_location_by_sub_id(self, sub_id: str) -> str:
        if not sub_id:
            return "auto"
        try:
            loc = self.db.get_location_by_sub_id(sub_id)
            logger.info(f"Для sub_id {sub_id} получена локация: {loc}")
            return loc if loc in ("auto", "lt", "nl") else "auto"
        except Exception as e:
            logger.warning(f"Ошибка получения локации для sub_id {sub_id}: {e}")
            return "auto"

    def _read_traffic_averages(self) -> Dict[str, float]:
        """
        Читает данные о трафике и рассчитывает средние значения ЗА ПОСЛЕДНИЙ ЧАС.
        Возвращает словарь: IP -> средняя скорость за последний час.
        """
        if not os.path.exists(self.traffic_data_file):
            logger.warning(f"Файл трафика не найден: {self.traffic_data_file}")
            return {}

        try:
            with open(self.traffic_data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.debug(f"Загружено {len(data)} записей из traffic_data.json")
        except Exception as e:
            logger.warning(f"Ошибка чтения файла трафика {self.traffic_data_file}: {e}")
            return {}

        sums: Dict[str, float] = {}
        counts: Dict[str, int] = {}
        now = time.time()
        cutoff = now - 3600  # 1 ЧАС вместо 24 часов

        for entry in data:
            if not isinstance(entry, dict):
                continue
            ts = entry.get("timestamp", 0)
            if ts < cutoff:
                continue  # Пропускаем данные старше 1 часа
            metrics = entry.get("metrics", {})
            if not isinstance(metrics, dict):
                continue
            for host, m in metrics.items():
                try:
                    val = float(m.get("speed_avg", 0.0) or 0.0)
                except Exception:
                    val = 0.0
                # host может быть доменом — переводим в IP для суммирования/сравнения
                ip = _host_to_ip(host) or host
                sums[ip] = sums.get(ip, 0.0) + val
                counts[ip] = counts.get(ip, 0) + 1

        averages: Dict[str, float] = {}
        for host, total in sums.items():
            cnt = counts.get(host, 1)
            averages[host] = total / cnt if cnt else 0.0

        logger.debug(f"Рассчитаны средние нагрузки: {averages}")
        return averages

    def _get_current_server_metrics(self) -> Dict[str, Dict[str, Any]]:
        """
        Получает самые свежие метрики серверов (не старше 5 минут).
        """
        if not os.path.exists(self.traffic_data_file):
            return {}

        try:
            with open(self.traffic_data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"Ошибка чтения файла трафика {self.traffic_data_file}: {e}")
            return {}

        current_metrics = {}
        now = time.time()
        cutoff = now - 300  # 5 минут

        # Ищем самые свежие записи для каждого сервера
        for entry in reversed(data):  # Идем от новых к старым
            if not isinstance(entry, dict):
                continue
                
            ts = entry.get("timestamp", 0)
            if ts < cutoff:
                break  # Достигли старых данных
                
            metrics = entry.get("metrics", {})
            for host, m in metrics.items():
                ip = _host_to_ip(host) or host
                if ip not in current_metrics:
                    current_metrics[ip] = {
                        'speed_avg': m.get('speed_avg', 0),
                        'online_clients': m.get('online_clients', 0),
                        'latency': m.get('latency', -1),
                        'timestamp': ts
                    }

        logger.debug(f"Текущие метрики серверов: {current_metrics}")
        return current_metrics

    def _should_reassign_early(self, current_ip: str, assignment_age: float) -> bool:
        """
        Определяет, нужно ли досрочно перепривязать пользователя.
        """
        # Не перепривязываем слишком часто
        if assignment_age < self.MIN_ASSIGNMENT_AGE:
            return False

        current_metrics = self._get_current_server_metrics()
        
        # Если нет данных о текущем сервере - считаем что он отказал
        if current_ip not in current_metrics:
            logger.warning(f"Сервер {current_ip} не найден в метриках - возможный отказ")
            return True

        server_metrics = current_metrics[current_ip]
        
        # Проверяем, не устарели ли метрики (больше 5 минут)
        metrics_age = time.time() - server_metrics.get('timestamp', 0)
        if metrics_age > self.SERVER_FAILURE_THRESHOLD:
            logger.warning(f"Метрики сервера {current_ip} устарели ({metrics_age:.0f} сек) - возможный отказ")
            return True

        # Проверяем разницу в нагрузке
        averages = self._read_traffic_averages()
        if averages:
            current_load = server_metrics.get('speed_avg', 0)
            min_load = min(averages.values()) if averages else 0
            
            if current_load - min_load > self.LOAD_DIFF_THRESHOLD:
                logger.info(f"Большая разница в нагрузке: {current_ip} ({current_load:.0f} B/s) vs минимальная ({min_load:.0f} B/s)")
                return True

        # Проверяем задержку (если больше 500ms - плохо)
        latency = server_metrics.get('latency', -1)
        if latency > 0.5:
            logger.warning(f"Высокая задержка на сервере {current_ip}: {latency:.3f}s")
            return True

        return False

    def _calculate_dynamic_ttl(self, current_ip: str) -> int:
        """
        Рассчитывает динамическое время жизни привязки на основе нагрузки.
        """
        averages = self._read_traffic_averages()
        if not averages or current_ip not in averages:
            return self.MAX_ASSIGNMENT_AGE

        current_load = averages[current_ip]
        avg_load = sum(averages.values()) / len(averages)
        
        # Чем выше нагрузка относительно средней, тем меньше TTL
        load_ratio = current_load / avg_load if avg_load > 0 else 1.0
        
        if load_ratio > 2.0:  # Нагрузка в 2 раза выше средней
            return 3600  # 1 час
        elif load_ratio > 1.5:
            return 7200  # 2 часа
        elif load_ratio > 1.2:
            return 14400  # 4 часа
        else:
            return self.MAX_ASSIGNMENT_AGE  # 24 часа

    def _choose_least_loaded_ip(self) -> Optional[str]:
        averages = self._read_traffic_averages()
        if not averages:
            logger.warning("Нет данных о нагрузке для выбора сервера")
            return None
        
        # Исключаем серверы, которые могут быть недоступны
        current_metrics = self._get_current_server_metrics()
        available_servers = {
            ip: load for ip, load in averages.items() 
            if ip in current_metrics and 
               (time.time() - current_metrics[ip].get('timestamp', 0)) <= self.SERVER_FAILURE_THRESHOLD
        }
        
        if not available_servers:
            logger.warning("Нет доступных серверов")
            return None
            
        best_ip = min(available_servers.keys(), key=lambda h: available_servers[h])
        logger.info(f"Выбран наименее загруженный сервер: {best_ip} с нагрузкой {available_servers[best_ip]:.0f} B/s")
        
        if _is_ip(best_ip):
            return best_ip
            
        ip = _host_to_ip(best_ip)
        return ip

    def _get_auto_ip_for_sub_id(self, sub_id: str) -> Optional[str]:
        logger.info(f"Автоматический выбор IP для sub_id: {sub_id}")
        assignment = self.dns_assignments.get_assignment(sub_id)
        current_time = time.time()
        
        if assignment:
            ip, assigned_at, last_checked = assignment
            assignment_age = current_time - assigned_at
            logger.info(f"Найдена существующая привязка: {sub_id} -> {ip} (возраст: {assignment_age/3600:.1f} ч)")
            
            # Обновляем время последней проверки (не чаще чем раз в минуту)
            if current_time - last_checked > 60:
                self.dns_assignments.update_last_checked(sub_id)
            
            # Проверяем базовую валидность и необходимость досрочного переприсвоения
            if (self.dns_assignments.is_assignment_valid(assigned_at) and 
                not self._should_reassign_early(ip, assignment_age)):
                logger.info(f"Используем существующую привязку для {sub_id}: {ip}")
                return ip
            else:
                logger.info(f"Привязка для {sub_id} требует обновления (возраст: {assignment_age/3600:.1f} ч)")
        else:
            logger.info(f"Существующая привязка для {sub_id} не найдена")

        # Выбираем новый IP
        new_ip = self._choose_least_loaded_ip()
        
        if not new_ip:
            # Fallback на случайный выбор
            logger.warning("Не удалось выбрать IP по нагрузке, используем случайный выбор")
            new_ip = self.balancer.ip_provider.get_random_ip()
            if new_ip and not _is_ip(new_ip):
                new_ip_conv = _host_to_ip(new_ip)
                if new_ip_conv:
                    new_ip = new_ip_conv
            logger.info(f"Случайно выбран IP: {new_ip}")

        if new_ip:
            try:
                self.dns_assignments.set_assignment(sub_id, new_ip)
                logger.info(f"ОБНОВЛЕНА ПРИВЯЗКА ДЛЯ {sub_id}: {new_ip}")
                
                # Логируем причину обновления
                if not assignment:
                    logger.debug(f"Новая привязка для {sub_id}")
                else:
                    old_ip, assigned_at, _ = assignment
                    logger.info(f"Перепривязка {sub_id}: {old_ip} -> {new_ip}")
            except Exception as e:
                logger.error(f"Ошибка сохранения привязки для {sub_id}: {e}")
                # Возвращаем новый IP даже если не удалось сохранить
                return new_ip

        return new_ip

    def choose_ip_for_qname(self, qname: str) -> Tuple[Optional[str], str, str, Optional[str]]:
        """
        Основной метод для выбора IP по доменному имени.
        Возвращает: (ip, loc_pref, loc_name, sub_id)
        """
        logger.info(f"Обработка DNS запроса для: {qname}")
        
        sub_id = self._extract_sub_id_from_qname(qname)
        if sub_id:
            loc_pref = self._get_location_by_sub_id(sub_id)
            logger.info(f"Для sub_id {sub_id} установлена локация: {loc_pref}")
        else:
            loc_pref = "auto"
            logger.info("Sub_id не извлечен, используем авто-режим")

        ip = None
        loc_name = "Неизвестно"

        try:
            if loc_pref == "auto" and sub_id:
                logger.info(f"Автоматический выбор IP для sub_id: {sub_id}")
                ip = self._get_auto_ip_for_sub_id(sub_id)
            else:
                logger.info(f"Выбор IP по локации: {loc_pref}")
                ip = self.balancer.choose_ip(loc_pref)

            if ip:
                loc_name = self.balancer.describe_ip(ip)
                logger.info(f"Выбран IP: {ip} ({loc_name}) для qname: {qname}")
            else:
                loc_name = "Нет доступных серверов"
                logger.warning(f"Не удалось выбрать IP для qname: {qname}")
                
        except Exception as e:
            logger.error(f"Ошибка выбора IP для {qname}: {e}")
            # Fallback на случайный IP
            ip = self.balancer.ip_provider.get_random_ip()
            loc_name = "Ошибка выбора"
            logger.info(f"Fallback IP: {ip}")

        logger.info(f"ФИНАЛЬНЫЙ РЕЗУЛЬТАТ: {qname} -> {ip} ({loc_name}), sub_id: {sub_id}")
        return ip, loc_pref, loc_name, sub_id