from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .main import app as api_app
from .main import initialize_application


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(os.getenv("PANELSTACK_STATIC_DIR", REPO_ROOT / "frontend" / "dist")).resolve()
INDEX_HTML = STATIC_DIR / "index.html"
BASE_PATH = os.getenv("PANELSTACK_BASE_PATH", "/panels").rstrip("/")


def create_passenger_app() -> FastAPI:
    initialize_application()
    app = FastAPI(title="Panel Stack", version="0.2.0")

    @app.middleware("http")
    async def strip_base_path(request, call_next):
        if BASE_PATH and request.scope["path"].startswith(f"{BASE_PATH}/"):
            request.scope["path"] = request.scope["path"][len(BASE_PATH) :] or "/"
        return await call_next(request)

    app.mount("/api", api_app)

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def serve_frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")
        if not INDEX_HTML.exists():
            raise HTTPException(status_code=500, detail="Frontend build is missing.")

        requested_path = (STATIC_DIR / path).resolve()
        if requested_path.is_file() and STATIC_DIR in requested_path.parents:
            return FileResponse(requested_path)
        return FileResponse(INDEX_HTML)

    return app


passenger_asgi_app = create_passenger_app()
