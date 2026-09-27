"""Matching Reply Assistant のFastAPIエントリポイント。

起動: uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import logging
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, database
from .routers import backup, contacts, generation, history, images, knowledge, like_bot, messages, profile, settings, training

APP_BUILD_VERSION = config.APP_BUILD_VERSION
PROMPT_VERSION = config.PROMPT_VERSION

config.setup_logging()
config.ensure_dirs()
database.init_db()

logger = logging.getLogger(__name__)

app = FastAPI(title="Matching Reply Assistant", version=APP_BUILD_VERSION)

# 開発時はVite(5173)からアクセスするためCORSを許可する
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(contacts.router)
app.include_router(messages.router)
app.include_router(generation.router)
app.include_router(settings.router)
app.include_router(profile.router)
app.include_router(knowledge.router)
app.include_router(history.router)
app.include_router(images.router)
app.include_router(backup.router)
app.include_router(training.router)
app.include_router(like_bot.router)


@app.on_event("startup")
def startup_event():
    db_abs = str(config.DB_PATH.resolve())
    pid = os.getpid()
    logger.info(
        "=== STARTUP Matching Reply Assistant (build=%s, prompt=%s, pid=%d, db=%s) ===",
        APP_BUILD_VERSION,
        PROMPT_VERSION,
        pid,
        db_abs,
    )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "build_version": APP_BUILD_VERSION,
        "prompt_version": PROMPT_VERSION,
        "pid": os.getpid(),
        "db_path": str(config.DB_PATH.resolve()),
    }


# ビルド済みフロントエンドがあれば配信する（APIルートが優先される）
if config.FRONTEND_DIST.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=config.FRONTEND_DIST, html=True),
        name="frontend",
    )
