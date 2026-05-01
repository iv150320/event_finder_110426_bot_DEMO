"""Сервис интеграции с NVIDIA NIM API (DeepSeek V3.2) для анализа событий."""

import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "deepseek-ai/deepseek-v3.2")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_TIMEOUT = int(os.getenv("NVIDIA_TIMEOUT", "120"))
NVIDIA_MAX_TOKENS = int(os.getenv("NVIDIA_MAX_TOKENS", "8000"))


async def analyze_events_by_topic(events_text: str, topic: str) -> list[str]:
    """Отправляет список событий в LLM и получает номера релевантных теме событий.

    Args:
        events_text: Нумерованный список событий (одна строка на событие)
        topic: Тема для фильтрации (бизнес, экономика, психология, история и т.д.)

    Returns:
        Список ID событий (1-based), которые релевантны теме
    """
    if not NVIDIA_API_KEY:
        logger.warning("NVIDIA_API_KEY not set, LLM analysis unavailable")
        return []

    system_prompt = (
        "Ты — ассистент для классификации университетских мероприятий по темам.\n"
        "Тебе будет дан нумерованный список мероприятий и тема.\n"
        "Определи, какие мероприятия релевантны указанной теме, анализируя их смысл и содержание.\n\n"
        "ПРАВИЛА:\n"
        "- Возвращай ТОЛЬКО номера релевантных мероприятий через запятую (например: 1,5,12,37)\n"
        "- Если ни одно мероприятие не подходит — верни 0\n"
        "- Будь широким в интерпретации: например, семинар по экономической истории относится к «экономика» и «история»\n"
        "- Математические/статистические доклады относятся к «наука» и «технологии»\n"
        "- Психологические семинары — к «психология»\n"
        "- Дни открытых дверей, профориентация — к «образование»\n"
        "- Бизнес-встречи, стартапы — к «бизнес»\n"
        "- НЕ добавляй пояснения, только номера\n"
    )

    user_message = (
        f"Тема: {topic}\n\n"
        f"Список мероприятий:\n{events_text}\n\n"
        f"Верни номера релевантных теме «{topic}» мероприятий через запятую:"
    )

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
        "max_tokens": 512,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=NVIDIA_TIMEOUT) as client:
            resp = await client.post(
                f"{NVIDIA_BASE_URL}/chat/completions",
                headers=headers,
                json=data,
            )
            resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        logger.info(f"LLM response for topic '{topic}': {content}")

        if content == "0" or not content:
            return []

        ids = []
        for part in content.split(","):
            part = part.strip()
            try:
                ids.append(int(part))
            except ValueError:
                continue
        return ids

    except httpx.TimeoutException:
        logger.error("NVIDIA API timeout during event analysis")
        return []
    except Exception as e:
        logger.error(f"NVIDIA API error: {e}")
        return []

async def generate_hourly_llm_report(events_text: str) -> str:
    """Генерирует часовой отчет по новым событиям через LLM.

    Args:
        events_text: Текст с описанием новых событий для анализа.

    Returns:
        Сгенерированный Markdown отчет или пустая строка при ошибке.
    """
    if not NVIDIA_API_KEY:
        logger.warning("NVIDIA_API_KEY not set, LLM hourly report unavailable")
        return ""

    system_prompt = (
        "Ты — умный ассистент-куратор мероприятий. "
        "Твоя задача — проанализировать предоставленный список новых событий и составить красивую, "
        "читаемую выгрузку (сводку) только тех мероприятий, которые соответствуют актуальным темам: "
        "вузы, бизнес конференции, айтишные митапы (IT), история, психология, экономика и то, что с этим тесно связано. "
        "Игнорируй события, которые не подходят под эти темы (например, обычные концерты, стендапы, если они не связаны с указанными темами). "
        "Оформи ответ в виде красивого дайджеста с эмодзи. "
        "Не выдумывай события, используй только те, что есть в списке. "
        "Если подходящих событий нет, ответь: 'Нет новых релевантных событий'."
    )

    user_message = f"Новые события для анализа:\n\n{events_text}\n\nСделай выгрузку по актуальным темам:"

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }

    data = {
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
        "max_tokens": NVIDIA_MAX_TOKENS,
        "stream": False,
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=NVIDIA_TIMEOUT) as client:
            resp = await client.post(
                f"{NVIDIA_BASE_URL}/chat/completions",
                headers=headers,
                json=data,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            return content
    except Exception as e:
        logger.error(f"NVIDIA API error during hourly report: {e}")
        return ""
