from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app


def test_healthz_without_a_database() -> None:
    with TestClient(create_app(Settings(database_url=None))) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "not configured"}


def test_healthz_reports_an_unreachable_database() -> None:
    nowhere = SecretStr("postgresql+asyncpg://nobody@127.0.0.1:9/nowhere")
    with TestClient(create_app(Settings(database_url=nowhere))) as client:
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable"}
