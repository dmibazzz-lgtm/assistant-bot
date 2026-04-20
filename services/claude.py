from __future__ import annotations

"""Вызовы LLM: Anthropic Claude и OpenRouter (DeepSeek).

Логика роутинга:
- pick_model() смотрит на длину и ключевые слова последнего user-сообщения
- Если запрос «тяжёлый» (длинный / про задачи / про анализ) → Sonnet
- Иначе если есть OpenRouter ключ → DeepSeek V3 (в 5-10 раз дешевле)
- Иначе fallback на Claude Haiku
- OpenRouter при сбое тоже падает на Haiku

Prompt caching (Anthropic) включён по умолчанию — системный промпт
кэшируется на 5 минут, повторные вызовы читают его из кэша по сниженной
цене (10% от обычной).
"""

import logging
import httpx

from config import (
    CLAUDE_API_KEY, OPENROUTER_API_KEY, WEBHOOK_URL,
    MODEL_SMART, MODEL_FAST_OPENROUTER, MODEL_FAST_CLAUDE,
    SMART_KEYWORDS,
    MAX_TOKENS_DEFAULT, MAX_TOKENS_VISION,
)


def pick_model(messages: list) -> tuple[str, str]:
    """Возвращает (model_id, provider). provider: 'claude' | 'openrouter'."""
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    if not isinstance(last, str):
        return MODEL_SMART, "claude"
    text = last.lower()
    if len(last) > 50 or any(kw in text for kw in SMART_KEYWORDS):
        return MODEL_SMART, "claude"
    if OPENROUTER_API_KEY:
        return MODEL_FAST_OPENROUTER, "openrouter"
    return MODEL_FAST_CLAUDE, "claude"


async def _call_openrouter(messages: list, system: str, model: str,
                           max_tokens: int = MAX_TOKENS_DEFAULT) -> str:
    """Вызов через OpenRouter (OpenAI-compatible API)."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": WEBHOOK_URL,
        "X-Title": "Nova Assistant",
    }
    oai_messages = [{"role": "system", "content": system}] + messages
    data = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": oai_messages,
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://openrouter.ai/api/v1/chat/completions",
                              headers=headers, json=data, timeout=45)
    result = r.json()
    if "choices" not in result or not result["choices"]:
        err = result.get("error", {}).get("message", str(result))
        logging.error(f"OpenRouter error: {err}")
        raise Exception(f"OpenRouter error: {err}")
    return result["choices"][0]["message"]["content"]


async def _call_claude_api(messages: list, system: str, model: str,
                           max_tokens: int = MAX_TOKENS_DEFAULT,
                           cache_system: bool = True) -> str:
    """Вызов Anthropic API с prompt caching по умолчанию.

    Для коротких system (<1024 токенов для Haiku / <2048 для Sonnet) кэш
    просто игнорируется API — это безопасно.
    """
    headers = {
        "x-api-key": CLAUDE_API_KEY.encode('ascii', 'ignore').decode('ascii'),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    system_payload = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if cache_system else system
    )
    data = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_payload,
        "messages": messages,
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              headers=headers, json=data, timeout=45)
    result = r.json()
    if "content" not in result:
        err = result.get("error", {}).get("message", str(result))
        logging.error(f"Claude API error: {err}")
        # На всякий: если API ругается на cache_control — повторяем без кэша
        if cache_system and "cache" in err.lower():
            return await _call_claude_api(messages, system, model, max_tokens, cache_system=False)
        raise Exception(f"Claude API error: {err}")
    try:
        usage = result.get("usage", {}) or {}
        c_read = usage.get("cache_read_input_tokens", 0)
        c_write = usage.get("cache_creation_input_tokens", 0)
        if c_read or c_write:
            logging.info(
                f"Claude usage: in={usage.get('input_tokens', 0)} "
                f"out={usage.get('output_tokens', 0)} "
                f"cache_read={c_read} cache_write={c_write}"
            )
    except Exception:
        pass
    return result["content"][0]["text"]


async def call_claude(messages: list, system: str,
                      model: str | None = None,
                      max_tokens: int = MAX_TOKENS_DEFAULT) -> str:
    """Главная точка входа. Сам решит куда идти (Sonnet / DeepSeek / Haiku)."""
    if model is None:
        model, provider = pick_model(messages)
    elif model == MODEL_SMART:
        provider = "claude"
    elif OPENROUTER_API_KEY:
        provider = "openrouter"
        model = MODEL_FAST_OPENROUTER
    else:
        provider = "claude"
    if provider == "openrouter":
        try:
            return await _call_openrouter(messages, system, model, max_tokens=max_tokens)
        except Exception as e:
            logging.warning(f"OpenRouter failed, fallback to Claude Haiku: {e}")
            return await _call_claude_api(messages, system, MODEL_FAST_CLAUDE, max_tokens=max_tokens)
    return await _call_claude_api(messages, system, model, max_tokens=max_tokens)


async def call_claude_vision(image_b64: str, system: str,
                             prompt: str = "Опиши что на фото и извлеки любые задачи, планы или важную информацию.") -> str:
    """Отдельная точка входа для анализа картинок (Claude Sonnet Vision).
    Используется в handle_photo, включая распознавание чеков для /finance."""
    headers = {
        "x-api-key": CLAUDE_API_KEY.encode('ascii', 'ignore').decode('ascii'),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    data = {
        "model": MODEL_SMART,
        "max_tokens": MAX_TOKENS_VISION,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    }
    async with httpx.AsyncClient() as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              headers=headers, json=data, timeout=60)
    return r.json()["content"][0]["text"]
