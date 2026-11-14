import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.auth import get_current_user
from app.core.config import settings
from app.db import ask_chats, runs
from app.routes import (
    ask,
    policy,
    diagnose,
    health,
    ingest,
    retest,
    runs as runs_routes,
    search,
    test,
    upload,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    backend = "Supabase" if runs.uses_supabase() else "SQLite"
    logger.info("Initializing run history (%s backend)...", backend)
    await runs.initialize_async()
    logger.info("Initializing Ask history (%s backend)...", backend)
    await ask_chats.initialize_async()
    yield
    logger.info("App shutdown")


app = FastAPI(title="APIFix AI", version="0.1.0", lifespan=lifespan)

frontend_origins = [
    origin.strip().rstrip("/")
    for origin in settings.frontend_origin.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.get("/", tags=["health"])
def landing_page():
    return {"message": "APIFix AI backend", "docs": "/docs"}


# Every router except health requires a verified Supabase Auth token when
# SUPABASE_AUTH_JWKS_URL is configured (see app/core/auth.py); with the env
# var unset the dependency passes through so local development keeps working.
protected = [Depends(get_current_user)]

app.include_router(health.router, prefix="/api")
app.include_router(test.router, prefix="/api", dependencies=protected)
app.include_router(diagnose.router, prefix="/api", dependencies=protected)
app.include_router(ask.router, prefix="/api", dependencies=protected)
app.include_router(retest.router, prefix="/api", dependencies=protected)
app.include_router(runs_routes.router, prefix="/api", dependencies=protected)
app.include_router(policy.router, prefix="/api", dependencies=protected)
app.include_router(upload.router, prefix="/api", dependencies=protected)
app.include_router(ingest.router, prefix="/api", dependencies=protected)
app.include_router(search.router, prefix="/api", dependencies=protected)
