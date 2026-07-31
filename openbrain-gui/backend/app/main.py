# app/main.py
"""FastAPI app entrypoint: mounts /api routes, runs the usage-ledger poller,
and serves the built React frontend as static files (same-origin, single
container -- design spec section 4, "Architecture")."""
import asyncio
import contextlib
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import LEDGER_POLL_SECONDS
from app.db import init_db
from app import ledger_store
from app.routes import router as api_router

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

async def _poll_forever() -> None:
    while True:
        # sqlite3 and the state.db file copy are both blocking, so keep them
        # off the event loop -- a tick must never stall a request. run_once
        # never raises, so a bad tick cannot kill this loop either.
        await asyncio.to_thread(ledger_store.run_once)
        await asyncio.sleep(LEDGER_POLL_SECONDS)

@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_poll_forever())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="openbrain-gui-backend", lifespan=_lifespan)
    app.include_router(api_router)

    @app.get("/health")
    def health():
        return {"ok": True}

    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="frontend")
    return app

app = create_app()
