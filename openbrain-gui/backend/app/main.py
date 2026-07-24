# app/main.py
"""FastAPI app entrypoint: mounts /api routes and serves the built React
frontend as static files (same-origin, single container -- design spec
section 4, "Architecture")."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routes import router as api_router

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="openbrain-gui-backend")
    app.include_router(api_router)
    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="frontend")
    return app

app = create_app()
