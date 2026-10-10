"""プロジェクト共通の設定とパス解決。

すべてのパスはプロジェクトルートを基準として解決する。
PC固有の絶対パスはコードへ埋め込まない。
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "app.db"
CONTACTS_IMAGE_DIR = DATA_DIR / "contacts"
CHATS_DIR = DATA_DIR / "chats"
GENERATION_HISTORY_DIR = DATA_DIR / "generation_history"
BACKUPS_DIR = DATA_DIR / "backups"

KNOWLEDGE_RULES_DIR = PROJECT_ROOT / "knowledge" / "rules"
KNOWLEDGE_REFERENCES_DIR = PROJECT_ROOT / "knowledge" / "references"
KNOWLEDGE_TRAINING_DIR = PROJECT_ROOT / "knowledge" / "training"

TRAINING_EXAMPLES_DIR = PROJECT_ROOT / "training" / "examples"
TRAINING_SESSIONS_DIR = PROJECT_ROOT / "training" / "sessions"

CONFIG_DIR = PROJECT_ROOT / "config"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "app.log"

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

APP_BUILD_VERSION = "learned-reply-v4.0"
PROMPT_VERSION = "v4.0"

DEFAULT_PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_FALLBACK_PROVIDER = "gemini"
DEFAULT_FALLBACK_MODEL = "gemini-3.1-flash-lite"
DEFAULT_TEMPERATURE = 0.8
DEFAULT_MAX_TOKENS = 1024
DEFAULT_HISTORY_LIMIT = 50


def ensure_dirs() -> None:
    """必要なフォルダをすべて作成する。"""
    for d in (
        DATA_DIR,
        CONTACTS_IMAGE_DIR,
        CHATS_DIR,
        GENERATION_HISTORY_DIR,
        BACKUPS_DIR,
        KNOWLEDGE_RULES_DIR,
        KNOWLEDGE_REFERENCES_DIR,
        KNOWLEDGE_TRAINING_DIR,
        TRAINING_EXAMPLES_DIR,
        TRAINING_SESSIONS_DIR,
        CONFIG_DIR,
        LOG_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    ensure_dirs()
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
