"""Долгосрочная память клиентов через Mem0.

Mem0 — внешний сервис, который помнит о юзере между сессиями:
кто он, чем занимается, что ценит, какие темы уже обсуждали.

Используется в handle_message:
- перед ответом Новы: mem0_search подтягивает релевантные воспоминания
- после ответа: mem0_add сохраняет обменом user/assistant

Если MEM0_API_KEY не задан — функции тихо возвращают пустые результаты.
Это позволяет запускать бота без Mem0 (но долгосрочная память тогда не работает).
"""

import asyncio
import logging

from config import MEM0_API_KEY

_mem0_client = None


def get_mem0():
    """Ленивая инициализация клиента — один раз на процесс."""
    global _mem0_client
    if _mem0_client is not None:
        return _mem0_client
    if not MEM0_API_KEY:
        return None
    try:
        from mem0 import MemoryClient
        _mem0_client = MemoryClient(api_key=MEM0_API_KEY)
        return _mem0_client
    except Exception as e:
        logging.warning(f"Mem0 init failed: {e}")
        return None


async def mem0_add(uid: int, messages: list):
    """Сохраняем новые сообщения диалога в Mem0 (через executor — блокирующий SDK)."""
    mem = get_mem0()
    if not mem:
        return
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: mem.add(messages, user_id=str(uid)))
    except Exception as e:
        logging.warning(f"Mem0 add error: {e}")


async def mem0_search(uid: int, query: str) -> str:
    """Возвращает текстовый блок для вставки в системный промпт."""
    mem = get_mem0()
    if not mem:
        return ""
    try:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, lambda: mem.search(query, user_id=str(uid), limit=5)
        )
        if not results:
            return ""
        memories = [r.get("memory", "") for r in results if r.get("memory")]
        if not memories:
            return ""
        return "Долгосрочная память (из прошлых разговоров):\n" + "\n".join(f"• {m}" for m in memories)
    except Exception as e:
        logging.warning(f"Mem0 search error: {e}")
        return ""


def mem0_delete_all_user(uid: int) -> bool:
    """Полное удаление воспоминаний пользователя (для /forget → «забыть всё»)."""
    mem = get_mem0()
    if not mem:
        return False
    try:
        mem.delete_all(user_id=str(uid))
        return True
    except Exception as e:
        logging.warning(f"Mem0 delete_all failed for {uid}: {e}")
        return False
