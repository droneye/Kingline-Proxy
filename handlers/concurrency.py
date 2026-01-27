# concurrency.py
import os
import asyncio

# Параллельность запросов к 3X-UI (API панели)
XUI_MAX_CONCURRENCY = int(os.getenv("XUI_MAX_CONCURRENCY", "8"))
XUI_SEM = asyncio.Semaphore(XUI_MAX_CONCURRENCY)

# Параллельность исходящих сообщений (рассылки и т.п.)
SEND_MAX_CONCURRENCY = int(os.getenv("SEND_MAX_CONCURRENCY", "30"))
SEND_SEM = asyncio.Semaphore(SEND_MAX_CONCURRENCY)


async def bounded_gather(coros, sem: asyncio.Semaphore):
    """
    Запускает корутины параллельно, но не более N одновременно (по семафору).
    Возвращает список результатов/исключений.
    """
    async def _run(coro):
        async with sem:
            return await coro
    return await asyncio.gather(*(_run(c) for c in coros), return_exceptions=True)
