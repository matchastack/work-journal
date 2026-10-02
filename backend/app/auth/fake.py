"""A stand-in for GitHub's OAuth endpoints, for tests, as `app/llm/fake.py` stands in for Claude.

Pass `FakeGitHub().transport` to `create_app`. The addresses below are GitHub's, written out
again rather than imported, so a wrong address in `github.py` fails the tests.
"""

import base64
import hashlib
import itertools
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2

TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"


@dataclass(frozen=True)
class _Grant:
    """What GitHub remembers about a code: how it was asked for, and whose it is."""

    code_challenge: str
    redirect_uri: str
    account: dict[str, object]


class FakeGitHub:
    """GitHub's side of the OAuth web flow, behind `httpx2.MockTransport`.

    `approve` stands in for the person approving the sign-in on github.com. The token and user
    endpoints then answer as GitHub does, checking the client secret and the PKCE verifier.
    """

    client_id = "test-client-id"
    client_secret = "test-client-secret"

    def __init__(self) -> None:
        self.transport = httpx2.MockTransport(self._handle)
        self.requests: list[httpx2.Request] = []
        self.status: int | None = None
        """Answer every request with this status instead, e.g. 503 while GitHub is down."""
        self._numbers = itertools.count(1)
        self._codes: dict[str, _Grant] = {}
        self._tokens: dict[str, dict[str, object]] = {}

    def approve(self, authorize_url: str, account_id: int, login: str) -> str:
        """The callback address GitHub sends the browser to once the person approves."""
        query = parse_qs(urlsplit(authorize_url).query)
        code = f"code-{next(self._numbers)}"
        account: dict[str, object] = {"id": account_id, "login": login, "type": "User"}
        grant = _Grant(query["code_challenge"][0], query["redirect_uri"][0], account)
        self._codes[code] = grant
        return f"/auth/callback?code={code}&state={query['state'][0]}"

    def sign_in(self, client: httpx2.Client, account_id: int, login: str) -> httpx2.Response:
        """Sign in as a browser does: `/auth/login`, GitHub, then `/auth/callback`."""
        start = client.get("/auth/login", follow_redirects=False)
        callback = self.approve(start.headers["location"], account_id, login)
        return client.get(callback, follow_redirects=False)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status is not None:
            return httpx2.Response(self.status)
        url = str(request.url)
        if request.method == "POST" and url == TOKEN_URL:
            reply = self._token(parse_qs(request.content.decode()))
            if request.headers.get("Accept") == "application/json":
                return httpx2.Response(200, json=reply)
            # Without that header, GitHub answers in the form encoding.
            return httpx2.Response(200, text=urlencode(reply))
        if request.method == "GET" and url == USER_URL:
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if token not in self._tokens:
                return httpx2.Response(401, json={"message": "Bad credentials"})
            return httpx2.Response(200, json=self._tokens[token])
        return httpx2.Response(404, json={"message": "Not Found"})

    def _token(self, form: dict[str, list[str]]) -> dict[str, str]:
        """GitHub's answer to a token request, which comes with status 200 even for errors."""
        credentials = (form.get("client_id"), form.get("client_secret"))
        if credentials != ([self.client_id], [self.client_secret]):
            return {"error": "incorrect_client_credentials"}
        grant = self._codes.pop(form.get("code", [""])[0], None)
        verifier = form.get("code_verifier", [""])[0].encode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=")
        if grant is None or challenge.decode() != grant.code_challenge:
            return {"error": "bad_verification_code"}
        if form.get("redirect_uri") != [grant.redirect_uri]:
            return {"error": "redirect_uri_mismatch"}
        token = f"gho_test{next(self._numbers)}"
        self._tokens[token] = grant.account
        return {"access_token": token, "scope": "", "token_type": "bearer"}
