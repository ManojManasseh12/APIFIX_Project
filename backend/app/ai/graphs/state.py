"""State shared by the LangGraph flows.

Everything in the state must be JSON-serializable: the graph may later be
compiled with a checkpointer, and LangSmith traces state as run inputs/outputs.
Convert Pydantic models to dicts at the node boundary and rebuild them here.
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.ai.rag.retriever import RetrievalResult
from app.core.models import Observation, RequestSpec


class DiagnoseState(TypedDict, total=False):
    # Inputs
    spec: dict[str, Any]
    run_id: str
    user_id: str | None

    # Approval
    approved: bool

    # Observation
    observation: dict[str, Any]

    # Retrieval
    query: str
    chunks: list[dict[str, Any]]
    insufficient: bool
    version_mismatch: bool
    other_versions: list[str]
    retrieval_attempts: int

    # Generation
    diagnosis: str
    provider: str | None
    citations: list[str]
    invalid_citations: list[str]
    patch: dict[str, Any] | None
    diagnosis_attempts: int
    repair: bool

    # Terminal
    outcome: str
    error_code: str | None
    error_message: str | None
    trace: dict[str, Any]


def spec_from_state(state: DiagnoseState) -> RequestSpec:
    return RequestSpec.model_validate(state["spec"])


def observation_from_state(state: DiagnoseState) -> Observation:
    return Observation.model_validate(state["observation"])


def retrieval_from_state(state: DiagnoseState) -> RetrievalResult:
    return RetrievalResult(
        chunks=state.get("chunks", []),
        insufficient=state.get("insufficient", False),
        version_mismatch=state.get("version_mismatch", False),
        other_versions=state.get("other_versions", []),
        query=state.get("query", ""),
    )
