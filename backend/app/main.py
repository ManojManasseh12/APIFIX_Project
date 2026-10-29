from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_diagnose, routes_health, routes_retest, routes_test
from app.config import settings

app = FastAPI(title="APIFix AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(routes_health.router, prefix="/api")
app.include_router(routes_test.router, prefix="/api")
app.include_router(routes_diagnose.router, prefix="/api")
app.include_router(routes_retest.router, prefix="/api")
