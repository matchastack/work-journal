"""GitHub's OAuth web flow, protected by `state` and PKCE.

GitHub's guide: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
The app asks for no scopes: it only needs to know who signed in, and it doesn't keep the token.
"""

import base64
import hashlib
from collections.abc import Awaitable
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx2
from pydantic import BaseModel, SecretStr, ValidationError

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
API_VERSION = "2026-03-10"
TIMEOUT_S = 10.0


class GitHubError(Exception):
    """GitHub couldn't be reached or didn't confirm the sign-in. The message never holds a
    code or a token, so it's safe to log."""


class GitHubAccount(BaseModel):
    id: int
    login: str


class _TokenReply(BaseModel):
    """GitHub answers with status 200 either way: a token, or an error code such as
    `bad_verification_code` when the code is wrong, used or expired."""

    access_token: str | None = None
    error: str | None = None


def code_challenge(verifier: str) -> str:
    """PKCE's S256 challenge: the verifier's SHA-256, base64url-encoded without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class GitHubOAuth:
    client_id: str
    client_secret: SecretStr
    redirect_uri: str
    http: httpx2.AsyncClient

    def authorize_url(self, state: str, verifier: str) -> str:
        """Where to send the browser to ask GitHub who's signing in."""
        query = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
            "allow_signup": "false",
        }
        return f"{AUTHORIZE_URL}?{urlencode(query)}"

    async def account(self, code: str, verifier: str) -> GitHubAccount:
        """The account that approved the sign-in, from the code GitHub sent back."""
        return await self._fetch_account(await self._exchange(code, verifier))

    async def _exchange(self, code: str, verifier: str) -> str:
        form = {
            "client_id": self.client_id,
            "client_secret": self.client_secret.get_secret_value(),
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
        }
        headers = {"Accept": "application/json"}
        body = await _json(self.http.post(TOKEN_URL, data=form, headers=headers), "token")
        try:
            reply = _TokenReply.model_validate(body)
        except ValidationError:
            reply = _TokenReply()
        if not reply.access_token:
            raise GitHubError(f"GitHub refused the code: {(reply.error or 'no token')[:64]}")
        return reply.access_token

    async def _fetch_account(self, token: str) -> GitHubAccount:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
        }
        body = await _json(self.http.get(USER_URL, headers=headers), "user")
        try:
            account = GitHubAccount.model_validate(body)
        except ValidationError:
            account = None
        if account is None:
            raise GitHubError("GitHub's user response had no id and login")
        return account


async def _json(sending: Awaitable[httpx2.Response], name: str) -> object:
    try:
        response = await sending
        response.raise_for_status()
        return response.json()
    except httpx2.HTTPStatusError as error:
        status = error.response.status_code
        raise GitHubError(f"GitHub's {name} request failed with status {status}") from error
    except (httpx2.HTTPError, ValueError) as error:
        raise GitHubError(f"GitHub's {name} request failed: {type(error).__name__}") from error
