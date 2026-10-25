#!/usr/bin/env python3
# cleanup_dns_assignments.py

import sqlite3
import os
import re
import logging
from typing import List

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("cleanup_dns_assignments")

def is_valid_ip(ip: str) -> bool:
    """Проверяет, является ли строка валидным IPv4 адресом"""
    if not ip or not isinstance(ip, str):
        return False
    
    ip_pattern = re.compile(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$')
    match = ip_pattern.match(ip)
    if not match:
        return False
    
    # Проверяем, что каждый октет в диапазоне 0-255
    for octet in match.groups():
        if not 0 <= int(octet) <= 255:
            return False
    
    return True

def get_db_path() -> str:
    """Получает путь к базе данных из переменных окружения"""
    db_path = os.getenv("DB_PATH", "vpn_bot.db")
    
    # Если путь относительный, ищем в текущей директории и родительских
    if not os.path.isabs(db_path):
        # Проверяем текущую директорию
        if os.path.exists(db_path):
            return db_path
        
        # Проверяем директорию скрипта
        script_dir = os.path.dirname(os.path.abspath(__file__))
        script_db_path = os.path.join(script_dir, db_path)
        if os.path.exists(script_db_path):
            return script_db_path
        
        # Проверяем родительскую директорию
        parent_dir = os.path.dirname(script_dir)
        parent_db_path = os.path.join(parent_dir, db_path)
        if os.path.exists(parent_db_path):
            return parent_db_path
    
    return db_path

def get_valid_ips_from_env() -> List[str]:
    """Получает список валидных IP-адресов из переменных окружения"""
    valid_ips = []
    
    # Основные IP
    lt_ip = os.getenv("KINGLINE_LT_IP", "").strip()
    nl_ip = os.getenv("KINGLINE_NL_IP", "").strip()
    
    if is_valid_ip(lt_ip):
        valid_ips.append(lt_ip)
    if is_valid_ip(nl_ip):
        valid_ips.append(nl_ip)
    
    # Дополнительные IP из KINGLINE_DNS_IPS
    dns_ips_raw = os.getenv("KINGLINE_DNS_IPS", "")
    dns_ips = [ip.strip() for ip in dns_ips_raw.split(",") if ip.strip()]
    
    for ip in dns_ips:
        if is_valid_ip(ip) and ip not in valid_ips:
            valid_ips.append(ip)
    
    return valid_ips

def cleanup_dns_assignments():
    """Очищает таблицу dns_assignments, оставляя только валидные IP из окружения"""
    db_path = get_db_path()
    
    if not os.path.exists(db_path):
        logger.error(f"База данных не найдена: {db_path}")
        return
    
    logger.info(f"Подключаемся к базе данных: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Получаем все записи из таблицы
        cursor.execute("SELECT sub_id, ip, assigned_at FROM dns_assignments")
        records = cursor.fetchall()
        
        logger.info(f"Найдено записей в таблице dns_assignments: {len(records)}")
        
        # Получаем список валидных IP из окружения
        valid_ips = get_valid_ips_from_env()
        logger.info(f"Валидные IP из окружения: {valid_ips}")
        
        # Определяем записи для удаления (IP не валидные или не в списке valid_ips)
        records_to_delete = [(sub_id, ip) for sub_id, ip, _ in records if ip not in valid_ips]
        
        logger.info(f"Записей для удаления: {len(records_to_delete)}")
        
        if not records_to_delete:
            logger.info("Удалять нечего. Все записи валидные.")
            return
        
        response = input(f"Найдено {len(records_to_delete)} записей, которые не валидны. Удалить их? (y/N): ")
        if response.lower() != 'y':
            logger.info("Отменено пользователем.")
            return
        
        # Удаляем все записи, где IP не валидный
        delete_count = 0
        for sub_id, ip in records_to_delete:
            cursor.execute("DELETE FROM dns_assignments WHERE sub_id = ?", (sub_id,))
            delete_count += cursor.rowcount
        
        conn.commit()
        logger.info(f"Удалено записей: {delete_count}")
        
        # Проверяем оставшиеся записи
        cursor.execute("SELECT sub_id, ip, assigned_at FROM dns_assignments")
        remaining_records = cursor.fetchall()
        logger.info(f"Оставшиеся записи ({len(remaining_records)}):")
        for sub_id, ip, assigned_at in remaining_records:
            logger.info(f"  sub_id={sub_id}, ip={ip}, assigned_at={assigned_at}")
        
    except sqlite3.Error as e:
        logger.error(f"Ошибка базы данных: {e}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def fix_single_assignment(sub_id: str, new_ip: str):
    """Исправляет конкретную запись по sub_id"""
    if not is_valid_ip(new_ip):
        logger.error(f"Некорректный IP: {new_ip}")
        return
    
    db_path = get_db_path()
    
    if not os.path.exists(db_path):
        logger.error(f"База данных не найдена: {db_path}")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Проверяем существование записи
        cursor.execute("SELECT ip FROM dns_assignments WHERE sub_id = ?", (sub_id,))
        existing = cursor.fetchone()
        
        if not existing:
            logger.error(f"Запись с sub_id={sub_id} не найдена")
            return
        
        old_ip = existing[0]
        logger.info(f"Обновление записи: sub_id={sub_id}, старый IP={old_ip}, новый IP={new_ip}")
        
        # Обновляем запись
        assigned_at = int(os.path.getmtime(db_path))  # Время модификации файла БД как пример
        cursor.execute(
            "UPDATE dns_assignments SET ip = ?, assigned_at = ? WHERE sub_id = ?",
            (new_ip, assigned_at, sub_id)
        )
        
        conn.commit()
        logger.info("Запись успешно обновлена")
        
    except sqlite3.Error as e:
        logger.error(f"Ошибка базы данных: {e}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Очистка некорректных записей в таблице dns_assignments")
    parser.add_argument("--fix", nargs=2, metavar=("SUB_ID", "IP"), 
                       help="Исправить конкретную запись: --fix <sub_id> <ip>")
    
    args = parser.parse_args()
    
    if args.fix:
        sub_id, ip = args.fix
        fix_single_assignment(sub_id, ip)
    else:
        cleanup_dns_assignments()