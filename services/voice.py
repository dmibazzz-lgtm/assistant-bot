from __future__ import annotations

"""Распознавание голосовых сообщений через Groq Whisper.

Groq предоставляет бесплатный tier Whisper-large-v3 с очень быстрым ответом
(обычно <2 сек). Ключ бесплатный на console.groq.com. Без ключа голосовые
сообщения не принимаются — handle_voice проверяет его отдельно.
"""

import os
import httpx


async def call_groq_voice(audio_bytes: bytes) -> str | None:
    """Возвращает распознанный текст или None если ошибка/ключ не настроен."""
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    if not GROQ_API_KEY:
        return None
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": ("audio.ogg", audio_bytes, "audio/ogg")},
            data={"model": "whisper-large-v3", "language": "ru"},
            timeout=30,
        )
    if r.status_code == 200:
        return r.json().get("text")
    return None
