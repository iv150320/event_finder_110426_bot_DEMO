"""Configuration for Event Finder Bot."""

import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS_RAW = os.getenv("ALLOWED_USERS", "").strip()
ALLOWED_USERS = tuple(
    int(item.strip())
    for item in ALLOWED_USERS_RAW.split(",")
    if item.strip()
)

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "")

TIMEPAD_API_KEY = os.getenv("TIMEPAD_API_KEY", "")

SCHEDULER_CITIES = [
    c.strip()
    for c in os.getenv("SCHEDULER_CITIES", "msk").split(",")
    if c.strip()
]
SCHEDULER_CLEANUP_DAYS = int(os.getenv("SCHEDULER_CLEANUP_DAYS", "60"))

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "deepseek-ai/deepseek-v3.2")

SCHEDULER_ALLOWED_TOPICS = [
    t.strip()
    for t in os.getenv(
        "SCHEDULER_ALLOWED_TOPICS",
        "бизнес,IT,AI,экономика,политика,история,английский язык,психология,литература",
    ).split(",")
    if t.strip()
]

VK_SERVICE_TOKEN = os.getenv("VK_SERVICE_TOKEN", "")
TELEGRAM_CHANNELS = [
    c.strip() for c in os.getenv("TELEGRAM_CHANNELS", "").split(",") if c.strip()
]
TIMEPAD_TRUSTED_ORGS = [
    o.strip() for o in os.getenv("TIMEPAD_TRUSTED_ORGS", "").split(",") if o.strip()
]
