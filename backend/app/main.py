import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.schema import initialize_schema_async
from app.routes import (
    ask,
    diagnose,
    health,
    ingest,
    retest,
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
    logger.info("Initializing run database...")
    await initialize_schema_async()
    yield
    logger.info("App shutdown")


app = FastAPI(title="APIFix AI", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.get("/", tags=["health"])
def landing_page():
    return {"message": "APIFix AI backend", "docs": "/docs"}


app.include_router(health.router)
app.include_router(health.router, prefix="/api")
app.include_router(test.router, prefix="/api")
app.include_router(diagnose.router, prefix="/api")
app.include_router(ask.router, prefix="/api")
app.include_router(retest.router, prefix="/api")

for router in (upload.router, ingest.router, search.router):
    app.include_router(router)
    app.include_router(router, prefix="/api")
