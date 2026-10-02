from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app

PAGE = "text/html,application/xhtml+xml,*/*;q=0.8"
"""What a browser accepts when it loads a page."""
NO_DATABASE = SecretStr("postgresql+asyncpg://nobody@127.0.0.1:9/nowhere")


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A stand-in for `npm run build`'s output."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log('app')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    return tmp_path


def client_for(dist: Path) -> TestClient:
    return TestClient(create_app(Settings(database_url=NO_DATABASE, web_dist_dir=dist)))


@pytest.mark.parametrize("path", ["/", "/inbox", "/profile/versions/3"])
def test_pages_get_the_app(dist: Path, path: str) -> None:
    with client_for(dist) as client:
        response = client.get(path, headers={"Accept": PAGE})
    assert response.status_code == 200
    assert response.text == '<div id="root"></div>'
    assert response.headers["cache-control"] == "no-cache"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_built_files_are_served_and_hashed_ones_cached(dist: Path) -> None:
    with client_for(dist) as client:
        script = client.get("/assets/index-abc123.js")
        icon = client.get("/favicon.svg")
    assert script.text == "console.log('app')"
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert icon.status_code == 200
    assert "immutable" not in icon.headers.get("cache-control", "")


@pytest.mark.parametrize("path", ["/assets/gone-123.js", "/api/nothing", "/nothing.json"])
def test_missing_files_and_api_paths_are_not_found(dist: Path, path: str) -> None:
    with client_for(dist) as client:
        response = client.get(path, headers={"Accept": "application/json"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_the_api_comes_before_the_app(dist: Path) -> None:
    with client_for(dist) as client:
        health = client.get("/healthz", headers={"Accept": PAGE})
        me = client.get("/auth/me", headers={"Accept": PAGE})
    assert health.json()["database"] == "unreachable"
    assert me.json() == {"detail": "Not signed in"}


def test_routes_added_later_still_come_first(dist: Path) -> None:
    api = create_app(Settings(database_url=NO_DATABASE, web_dist_dir=dist))

    @api.post("/api/later")
    async def later() -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        return {"ok": True}

    with TestClient(api) as client:
        response = client.post("/api/later", headers={"Accept": PAGE})
    assert response.json() == {"ok": True}


def test_the_app_stays_inside_its_folder(dist: Path) -> None:
    (dist.parent / "secret.txt").write_text("not for the web")
    with client_for(dist) as client:
        response = client.get("/..%2Fsecret.txt")
    assert "not for the web" not in response.text


def test_without_a_build_the_root_says_how_to_make_one(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.get("/", headers={"Accept": PAGE})
    assert response.status_code == 404
    assert "npm run build" in response.json()["detail"]
