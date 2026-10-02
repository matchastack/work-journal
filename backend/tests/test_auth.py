"""Sign-in up to the point where it needs the database; `tests/db/test_auth_db.py` does the rest."""

from http.cookies import Morsel, SimpleCookie
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth.fake import FakeGitHub
from app.auth.github import code_challenge
from app.auth.routes import STATE_COOKIE
from app.auth.sessions import CSRF_COOKIE, SESSION_COOKIE, is_allowed
from app.config import Settings
from app.main import create_app

BROWSER = "https://testserver"
"""Where the test client pretends to be. HTTPS, so it sends Secure cookies back."""
OWNER = "casey-example"
NO_DATABASE = SecretStr("postgresql+asyncpg://nobody@127.0.0.1:9/nowhere")
"""Never connected to: these tests finish before any query."""


def app_for(
    github: FakeGitHub, *, configured: bool = True, app_url: str = "https://journal.example.com"
) -> FastAPI:
    settings = Settings(
        database_url=NO_DATABASE,
        app_url=app_url,
        github_client_id=github.client_id if configured else None,
        github_client_secret=SecretStr(github.client_secret) if configured else None,
        allowed_github_logins=frozenset({OWNER}),
    )
    return create_app(settings, transport=github.transport)


def set_cookie(response: httpx2.Response, name: str) -> Morsel[str]:
    """The response's `Set-Cookie` for one cookie, with its attributes."""
    for header in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(header)
        if name in cookie:
            return cookie[name]
    raise AssertionError(f"the response doesn't set {name}")


def sets_cookie(response: httpx2.Response, name: str) -> bool:
    return any(h.startswith(f"{name}=") for h in response.headers.get_list("set-cookie"))


def start(client: TestClient) -> tuple[httpx2.Response, dict[str, str]]:
    response = client.get("/auth/login", follow_redirects=False)
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return response, {key: values[0] for key, values in query.items()}


def test_the_pkce_challenge_matches_rfc_7636() -> None:
    """The example in RFC 7636, appendix B."""
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_the_allowlist_ignores_case() -> None:
    settings = Settings(allowed_github_logins=frozenset({"Casey-Example"}))
    assert is_allowed("casey-example", settings)
    assert is_allowed("CASEY-EXAMPLE", settings)
    assert not is_allowed("casey-example2", settings)
    assert not is_allowed(None, settings)


def test_sign_in_needs_the_github_settings() -> None:
    github = FakeGitHub()
    with TestClient(app_for(github, configured=False), base_url=BROWSER) as client:
        response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 503
    assert "GITHUB_CLIENT_ID" in response.json()["detail"]


def test_login_sends_the_browser_to_github_with_state_and_pkce() -> None:
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        response, query = start(client)
    assert response.status_code == 303
    url = urlsplit(response.headers["location"])
    assert (url.scheme, url.netloc, url.path) == ("https", "github.com", "/login/oauth/authorize")
    assert query == {
        "client_id": github.client_id,
        "redirect_uri": "https://journal.example.com/auth/callback",
        "state": query["state"],
        "code_challenge": query["code_challenge"],
        "code_challenge_method": "S256",
        "allow_signup": "false",
    }, "no scopes: the app only needs to know who signed in"
    cookie = set_cookie(response, STATE_COOKIE)
    state, verifier = cookie.value.split(".")
    assert state == query["state"]
    assert len(state) >= 43
    assert code_challenge(verifier) == query["code_challenge"]
    assert (cookie["httponly"], cookie["secure"], cookie["samesite"]) == (True, True, "lax")
    assert (cookie["path"], cookie["max-age"]) == ("/", "600")
    assert response.headers["cache-control"] == "no-store"


def test_on_a_local_http_address_the_state_cookie_is_not_secure() -> None:
    """Browsers such as Safari keep no Secure cookie from http://localhost, so there the cookie
    goes without Secure and the `__Host-` prefix, which needs Secure."""
    github = FakeGitHub()
    local = "http://localhost:8000"
    with TestClient(app_for(github, app_url=local), base_url=local) as client:
        response, query = start(client)
    assert query["redirect_uri"] == "http://localhost:8000/auth/callback"
    cookie = set_cookie(response, "wj_oauth")
    assert (cookie["httponly"], cookie["secure"], cookie["samesite"]) == (True, "", "lax")
    assert github.requests == [], "the browser goes to GitHub, not the app"


@pytest.mark.parametrize(
    ("app_url", "started_at"),
    [
        ("http://localhost:8000", "http://127.0.0.1:8000"),
        ("http://127.0.0.1:8000", "http://localhost:8000"),
    ],
)
def test_a_local_sign_in_moves_to_the_app_urls_host_first(app_url: str, started_at: str) -> None:
    """GitHub sends the browser back to APP_URL, and `localhost` and `127.0.0.1` keep separate
    cookies. A sign-in started on the other host, such as the address uvicorn prints, would
    come back without its state cookie, so it starts again on APP_URL's host."""
    github = FakeGitHub()
    with TestClient(app_for(github, app_url=app_url), base_url=started_at) as client:
        moved = client.get("/auth/login", follow_redirects=False)
        assert moved.status_code == 303
        assert moved.headers["location"] == f"{app_url}/auth/login"
        assert moved.headers.get_list("set-cookie") == []
        response = client.get(moved.headers["location"], follow_redirects=False)
    assert urlsplit(response.headers["location"]).netloc == "github.com"
    assert sets_cookie(response, "wj_oauth")


def test_a_local_sign_in_through_a_proxy_on_another_port_is_not_moved() -> None:
    """The web app's dev server passes requests on with the API's own address as the host, so
    moving them to APP_URL would send the browser round in a loop."""
    github = FakeGitHub()
    proxied = app_for(github, app_url="http://127.0.0.1:5173")
    with TestClient(proxied, base_url="http://localhost:8000") as client:
        response, query = start(client)
    assert urlsplit(response.headers["location"]).netloc == "github.com"
    assert query["redirect_uri"] == "http://127.0.0.1:5173/auth/callback"


def test_over_https_a_sign_in_starts_on_any_host() -> None:
    """Behind a proxy, the app may see another host than APP_URL's; that's fine over HTTPS."""
    github = FakeGitHub()
    with TestClient(app_for(github), base_url="https://internal.example") as client:
        response, query = start(client)
    assert urlsplit(response.headers["location"]).netloc == "github.com"
    assert query["redirect_uri"] == "https://journal.example.com/auth/callback"


def test_each_sign_in_gets_its_own_state() -> None:
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        first, second = start(client)[1], start(client)[1]
    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]


def test_a_callback_this_browser_did_not_start_is_refused() -> None:
    """Login CSRF: another site sends the browser back with the other site's code and state."""
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        without_sign_in = client.get("/auth/callback?code=c&state=forged", follow_redirects=False)
        start(client)
        other_state = client.get("/auth/callback?code=c&state=forged", follow_redirects=False)
        no_state = client.get("/auth/callback?code=c", follow_redirects=False)
    for response in (without_sign_in, other_state, no_state):
        assert response.status_code == 400
        assert "Sign-in expired" in response.text
        assert not sets_cookie(response, SESSION_COOKIE)
    assert github.requests == []


def test_cancelling_on_github_shows_a_message() -> None:
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        _, query = start(client)
        url = f"/auth/callback?error=access_denied&state={query['state']}"
        response = client.get(url, follow_redirects=False)
        assert STATE_COOKIE not in client.cookies, "a state works once"
    assert response.status_code == 400
    assert "Sign-in cancelled" in response.text
    assert '<a href="/auth/login">Try again</a>' in response.text
    assert github.requests == []


def test_accounts_not_on_the_allowlist_see_a_clear_message() -> None:
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        response = github.sign_in(client, 2001, "stranger-example")
    assert response.status_code == 403
    assert "This journal is private" in response.text
    assert "<title>This journal is private | Work Journal</title>" in response.text
    assert response.text.isascii()
    assert "@stranger-example isn't one of them" in response.text
    assert "ALLOWED_GITHUB_LOGINS" in response.text
    assert not sets_cookie(response, SESSION_COOKIE)
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_the_page_escapes_what_github_sends() -> None:
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        response = github.sign_in(client, 2002, "<script>alert(1)</script>")
    assert response.status_code == 403
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_github_being_down_shows_a_page_to_try_again() -> None:
    github = FakeGitHub()
    github.status = 503
    with TestClient(app_for(github), base_url=BROWSER) as client:
        response = github.sign_in(client, 2003, OWNER)
    assert response.status_code == 502
    assert "GitHub didn't confirm who you are" in response.text
    assert not sets_cookie(response, SESSION_COOKIE)


def test_a_code_github_does_not_know_is_refused() -> None:
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        _, query = start(client)
        url = f"/auth/callback?code=made-up&state={query['state']}"
        response = client.get(url, follow_redirects=False)
    assert response.status_code == 502
    assert [str(request.url) for request in github.requests] == [
        "https://github.com/login/oauth/access_token"
    ]


def test_me_needs_a_session() -> None:
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not signed in"}


def test_logout_without_a_session_clears_the_cookies() -> None:
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        response = client.post("/auth/logout")
    assert response.status_code == 204
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        cookie = set_cookie(response, name)
        assert (cookie.value, cookie["max-age"], cookie["secure"]) == ("", "0", True)


@pytest.mark.parametrize("path", ["/auth/login", "/auth/callback", "/auth/me"])
def test_sign_in_routes_are_read_only(path: str) -> None:
    """Only logout changes something without a session, and it's harmless."""
    github = FakeGitHub()
    with TestClient(app_for(github), base_url=BROWSER) as client:
        assert client.post(path).status_code == 405
