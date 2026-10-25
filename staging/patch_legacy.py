"""Repeatable, explicit adaptation of the sanitized legacy snapshot."""
from pathlib import Path
root = Path(__file__).parent
p = root / 'site/backend/server.py'
s = p.read_text().replace('__REDACTED_FOR_STAGING__', 'password_hash')
s = s.replace('COOKIE_NAME = "kingline_session"', 'COOKIE_NAME = "kingline_staging_session"')
s = s.replace('def send_mail(email, subject, text, html=None, headers=None):\n', '''def send_mail(email, subject, text, html=None, headers=None):
    # Staging captures mail locally; production recipients are never contacted.
    from staging_safety import capture_mail
    return capture_mail(email, subject, text, html)


def _unused_production_send_mail(email, subject, text, html=None, headers=None):
''')
s = s.replace('def send_telegram_message(chat_id, text):\n', '''def send_telegram_message(chat_id, text):
    if int(chat_id or 0) != int(ADMIN_ID):
        return None
''')
s = s.replace('def delete_telegram_message(chat_id, message_id):\n', '''def delete_telegram_message(chat_id, message_id):
    if int(chat_id or 0) != int(ADMIN_ID):
        return False
''')
s = s.replace('"status": row["status"],\n        "error": row["error"],', '''"status": row["status"],
        "stage": account_job_stage(row["job_id"], row["status"]),
        "error": row["error"],''')
start = s.index('def run_account_job(')
end = s.index('\n\ndef create_account_job(', start)
s = s[:start] + '''def account_job_stage(job_id, status):
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
''' + s[end:]
s = s.replace("AND status IN ('pending', 'running')\n              AND created_at >= datetime('now', '-15 minutes')", "AND status IN ('pending', 'running', 'retrying')")
p.write_text(s)
p = root / 'site/backend/domain_classifier.py'
p.write_text(p.read_text().replace('__REDACTED_FOR_STAGING__', '[a-zа-яё0-9]+'))
p = root / 'bot/handlers/billing_db.py'
s = p.read_text().replace('p.get("__REDACTED_FOR_STAGING__")', 'p.get("webBasePath")')
start = s.index('        """', s.index('    async def create_clients_for_user('))
end = s.index('\n    @staticmethod\n    async def delete_all_user_clients', start)
s = s[:start] + '''        from remnawave import Remnawave
        native = Remnawave()
        user = await asyncio.to_thread(native.ensure, sub_id, username_clean, expiry_time_ms)
        expiry = int(__import__('datetime').datetime.fromisoformat(user['expireAt'].replace('Z', '+00:00')).timestamp() * 1000)
        if user['status'] != 'ACTIVE' or abs(expiry - expiry_time_ms) > 1500:
            raise RuntimeError('Remnawave did not confirm subscription')
        return [user['vlessUuid']], expiry
''' + s[end:]
start = s.index('        """', s.index('    async def delete_all_user_clients('))
end = s.index('    # ===== РЕСТАРТ XRAY', start)
s = s[:start] + '''        from remnawave import Remnawave
        return await asyncio.to_thread(Remnawave().disable, sub_id)

''' + s[end:]
start = s.index('        """', s.index('    async def restart_xray_safe('))
s = s[:start] + '''        # Native API applies users without interrupting any active tunnels.
        return None
'''
p.write_text(s)
p = root / 'bot/referral/__init__.py'
p.write_text(p.read_text().replace('from .ref_db', 'import os\nfrom .ref_db').replace('path="ref.db"', 'path=os.environ["REFERRAL_DB_PATH"]'))
p = root / 'bot/handlers/billing_tariffs.py'
s = p.read_text().replace('from .subscription_service import activate_subscription', 'from .subscription_service import activate_subscription, SubscriptionPending')
s = s.replace('            except ValueError as exc:', '''            except SubscriptionPending:
                await context.bot.send_message(chat_id=chat_id, text="Доступ создаётся. При временном сбое повторим автоматически, без повторного списания.")
                return
            except ValueError as exc:''',1)
s = s.replace('        except ValueError as exc:', '''        except SubscriptionPending:
            await context.bot.send_message(chat_id=chat_id, text="Оплата проверена. Доступ создаётся; повторное списание не потребуется.")
            return
        except ValueError as exc:''',1) if False else s
# Target only the premium method, preserving the trial block indentation.
idx = s.index('    async def _activate_full_tariff(')
s = s[:idx] + s[idx:].replace('        except ValueError as exc:', '''        except SubscriptionPending:
            await context.bot.send_message(chat_id=chat_id, text="Оплата проверена. Доступ создаётся; повторим автоматически без повторного списания.")
            return
        except ValueError as exc:''',1)
s = s.replace('Оплата возвращена на баланс. Попробуй позже.', 'Проверь статус в кабинете или попробуй позже.')
p.write_text(s)
