from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app


def test_healthz_reaches_the_database(database_url: str) -> None:
    with TestClient(create_app(Settings(database_url=SecretStr(database_url)))) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
