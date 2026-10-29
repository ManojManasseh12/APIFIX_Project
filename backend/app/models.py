from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class RequestSpec(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    url: HttpUrl
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None
    timeout_s: float = 10.0
    expected_status: int | None = None
    question: str = ""
    approved: bool = False


class Observation(BaseModel):
    request_id: str
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body_preview: str = ""
    latency_ms: int = 0
    error_type: str | None = None
    error_message: str | None = None
    redirect_location: str | None = None
    truncated: bool = False


class Patch(BaseModel):
    set_headers: dict[str, str] = Field(default_factory=dict)
    remove_headers: list[str] = Field(default_factory=list)
    set_body: Any | None = None
    set_url: str | None = None


class Diagnosis(BaseModel):
    run_id: str
    text: str
    citations: list[str] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)
    patch: Patch | None = None
    provider: str | None = None
    confidence: str | None = None
