"""T-5.1 / T-5.2 FastAPI 骨架与健康检查测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "local"
    assert payload["version"]


def test_index_page() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "RAG 知识库" in response.text
    assert "/static/styles.css" in response.text
    assert "/static/app.js" in response.text
    assert "/static/assets/logo-mark.svg" in response.text


def test_swagger_available() -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_app_metadata() -> None:
    assert app.title == "RAG 本地知识库"
    assert app.version == "0.1.0"
