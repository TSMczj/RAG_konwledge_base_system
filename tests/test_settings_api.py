"""T-5.8 设置页 Key API 测试（DPAPI 本地存储，不访问真实上游）。"""

from __future__ import annotations

from types import SimpleNamespace

import app.api.settings as settings_api
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_secret_crud_and_masking(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RAG_SECRETS_FILE", str(tmp_path / "secrets.dat"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    initial = client.get("/api/v1/settings/secrets")
    assert initial.status_code == 200
    assert initial.json()["complete"] is False
    assert initial.json()["providers"]["deepseek"]["configured"] is False

    response = client.put(
        "/api/v1/settings/secrets/deepseek",
        json={"key": "sk-test-deepseek-1234"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["source"] == "local"
    assert "sk-test-deepseek-1234" not in response.text
    assert payload["masked"].endswith("1234")
    assert response.json()["provider"] == "deepseek"

    status = client.get("/api/v1/settings/secrets").json()
    assert status["providers"]["deepseek"]["configured"] is True
    assert status["complete"] is False

    cleared = client.delete("/api/v1/settings/secrets/deepseek")
    assert cleared.status_code == 200
    assert cleared.json() == {"provider": "deepseek", "configured": False}


def test_secret_file_is_encrypted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RAG_SECRETS_FILE", str(tmp_path / "secrets.dat"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client.put("/api/v1/settings/secrets/deepseek", json={"key": "sk-plaintext-check-9876"})
    raw = (tmp_path / "secrets.dat").read_text(encoding="utf-8")
    assert "sk-plaintext-check-9876" not in raw
    assert "blob" in raw


def test_secret_test_requires_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RAG_SECRETS_FILE", str(tmp_path / "secrets.dat"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    response = client.post("/api/v1/settings/secrets/deepseek/test")
    assert response.status_code == 400


def test_secret_test_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RAG_SECRETS_FILE", str(tmp_path / "secrets.dat"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client.put("/api/v1/settings/secrets/deepseek", json={"key": "sk-test-deepseek-1234"})
    monkeypatch.setattr(
        settings_api.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200),
    )
    response = client.post("/api/v1/settings/secrets/deepseek/test")
    assert response.status_code == 200
    assert response.json()["ok"] is True
