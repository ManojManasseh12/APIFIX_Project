"""Prompt template management (view / edit / reset the system prompts).

GET    /api/prompts            → all three prompt slots with their active text
PUT    /api/prompts/{purpose}  → save a custom template (body: {"text": ...})
DELETE /api/prompts/{purpose}  → remove the custom template (back to default)

Protected like every other router: authentication is enforced in main.py.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.ai import prompts as defaults
from app.core.auth import get_current_user
from app.db import prompt_templates

router = APIRouter(tags=["prompts"])


class PromptUpdate(BaseModel):
    text: str


# Map each purpose to its built-in default text.
_PURPOSE_DEFAULTS = {
    "diagnosis": defaults.SYSTEM_PROMPT,
    "ask": defaults.ASK_SYSTEM_PROMPT,
    "tool": defaults.TOOL_SYSTEM_PROMPT,
}


def _active_text(purpose: str) -> dict:
    """One prompt slot as the UI sees it: default or custom override."""
    override = prompt_templates.get_override(purpose)
    return {
        "purpose": purpose,
        "text": override["template"] if override else _PURPOSE_DEFAULTS[purpose],
        "is_custom": override is not None,
        "updated_at": override.get("updated_at") if override else None,
    }


@router.get("/prompts")
async def list_prompts(user: dict | None = Depends(get_current_user)) -> dict:
    return {"prompts": [_active_text(purpose) for purpose in prompt_templates.PURPOSES]}


@router.put("/prompts/{purpose}")
async def save_prompt(
    purpose: str, payload: PromptUpdate, user: dict | None = Depends(get_current_user)
) -> dict:
    if not prompt_templates.is_valid_purpose(purpose):
        raise HTTPException(status_code=404, detail=f"Unknown prompt '{purpose}'.")
    error = prompt_templates.validate_text(payload.text)
    if error:
        raise HTTPException(status_code=422, detail=error)
    saved = prompt_templates.save_override(purpose, payload.text)
    return {
        "purpose": purpose,
        "text": saved["template"],
        "is_custom": True,
        "updated_at": saved.get("updated_at"),
    }


@router.delete("/prompts/{purpose}")
async def reset_prompt(purpose: str, user: dict | None = Depends(get_current_user)) -> dict:
    if not prompt_templates.is_valid_purpose(purpose):
        raise HTTPException(status_code=404, detail=f"Unknown prompt '{purpose}'.")
    prompt_templates.delete_override(purpose)
    return {
        "purpose": purpose,
        "text": _PURPOSE_DEFAULTS[purpose],
        "is_custom": False,
        "updated_at": None,
    }
