import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, database
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "data" / "backups")
    monkeypatch.setattr(config, "CONTACTS_IMAGE_DIR", tmp_path / "data" / "contacts")
    monkeypatch.setattr(config, "KNOWLEDGE_RULES_DIR", tmp_path / "knowledge" / "rules")
    monkeypatch.setattr(config, "KNOWLEDGE_REFERENCES_DIR", tmp_path / "knowledge" / "references")
    monkeypatch.setattr(config, "KNOWLEDGE_TRAINING_DIR", tmp_path / "knowledge" / "training")
    monkeypatch.setattr(config, "TRAINING_EXAMPLES_DIR", tmp_path / "training" / "examples")
    monkeypatch.setattr(config, "TRAINING_SESSIONS_DIR", tmp_path / "training" / "sessions")
    database.init_db()
    yield


@pytest.fixture
def client():
    return TestClient(app)
