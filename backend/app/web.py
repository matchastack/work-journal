"""The web app: FastAPI serves the React build from `frontend/dist` at `/`.

The build answers only requests that no route matches, whenever the routes were added. Its files
are served as they are; the hashed ones in `assets/` are cached for a year. A page the browser
navigates to that isn't a file, such as `/inbox`, gets `index.html`, and the React router shows
the page. Other requests for missing paths, such as API calls, still get 404.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

INDEX_HEADERS = {
    "Cache-Control": "no-cache",
    "Content-Security-Policy": "default-src 'self'; base-uri 'self'; object-src 'none'; "
    "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
}
ASSET_CACHE = "public, max-age=31536000, immutable"


class WebApp(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or not _is_navigation(scope):
                raise
            path = "index.html"
            response = await super().get_response(path, scope)
        if path == "index.html":
            response.headers.update(INDEX_HEADERS)
        elif path.startswith("assets" + os.sep):
            response.headers["Cache-Control"] = ASSET_CACHE
        return response


def add_web_app(api: FastAPI, dist: Path) -> None:
    """Serve the build for every path that no route matches."""
    if (dist / "index.html").is_file():
        api.router.default = WebApp(directory=dist)
        return

    @api.get("/", include_in_schema=False)
    async def not_built() -> JSONResponse:
        detail = "The web app isn't built: run `npm run build` in frontend/, or `npm run dev`."
        return JSONResponse({"detail": detail}, status_code=404)


def _is_navigation(scope: Scope) -> bool:
    """Whether the browser is loading a page, rather than a script or data."""
    return "text/html" in Headers(scope=scope).get("accept", "")
