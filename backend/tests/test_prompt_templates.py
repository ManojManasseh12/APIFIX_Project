"""LangChain PromptTemplates: prompts built from inputs, in code.

Every pipeline's data prompt is a LangChain PromptTemplate in
app/ai/prompts.py; formatting it with real inputs produces the prompt
the LLM receives. These tests pin the rendered output.
"""

from types import SimpleNamespace

from app.ai.prompts import (
    ASK_CONTEXT_PROMPT,
    DIAGNOSIS_CONTEXT_PROMPT,
    TOOL_RESULT_PROMPT,
    build_ask_context,
    build_context,
    build_tool_context,
    conversation_block,
)
from app.core.models import RequestSpec


def _spec(**overrides) -> RequestSpec:
    base = dict(
        method="GET",
        url="https://api.example.com/users/1",
        headers={"Accept": "application/json"},
        body=None,
        timeout_s=5,
        expected_status=None,
        question="Why 404?",
        approved=True,
    )
    base.update(overrides)
    return RequestSpec(**base)


def _retrieval(chunks=None) -> SimpleNamespace:
    return SimpleNamespace(
        chunks=chunks
        or [
            {
                "chunk_id": "chunk-1",
                "source_title": "REST guide",
                "section": "Errors",
                "chunk_text": "A 404 means the resource was not found.",
            }
        ]
    )


def test_diagnosis_context_template_fills_from_inputs():
    observation = SimpleNamespace(
        status=404,
        error_type="http_404",
        latency_ms=212.4,
        headers={"content-type": "application/json"},
        body_preview='{"error": "user not found"}',
        redirect_location=None,
    )
    prompt = build_context(_spec(), observation, _retrieval())

    # Variables are replaced with the real inputs.
    assert "Why 404?" in prompt
    assert "'status': 404" in prompt
    assert "'error_type': 'http_404'" in prompt
    assert "[chunk-1]" in prompt
    assert "A 404 means the resource was not found." in prompt
    # The template's own section headers are intact.
    assert prompt.startswith("USER QUESTION:\nWhy 404?")
    assert "OBSERVED REQUEST/RESPONSE (redacted):" in prompt
    assert "EVIDENCE:" in prompt


def test_diagnosis_template_keeps_question_default():
    observation = SimpleNamespace(
        status=200,
        error_type=None,
        latency_ms=50,
        headers={},
        body_preview="{}",
        redirect_location=None,
    )
    prompt = build_context(_spec(question=""), observation, _retrieval())
    assert "Why did this request fail?" in prompt


def test_ask_context_template_fills_from_conversation():
    history = [
        SimpleNamespace(role="user", content="list users"),
        SimpleNamespace(role="assistant", content="GET /users does that"),
    ]
    prompt = build_ask_context("how do I paginate?", history, _retrieval())

    assert "USER: list users" in prompt
    assert "ASSISTANT: GET /users does that" in prompt
    assert "how do I paginate?" in prompt
    assert prompt.startswith("CONVERSATION:")
    assert "[chunk-1]" in prompt


def test_empty_conversation_block_placeholder():
    assert conversation_block([]) == "(new conversation)"


def test_tool_prompt_template_fills_from_tool_data():
    prompt = build_tool_context(
        "weather", {"city": "Chennai"}, {"temperature_c": 31, "feels_like_c": 34}
    )
    assert "TOOL: weather" in prompt
    assert '"city": "Chennai"' in prompt
    assert '"temperature_c": 31' in prompt
    assert "Present this tool result to the user." in prompt


def test_json_braces_survive_template_rendering():
    # Prompt bodies contain JSON; the jinja2 template format must leave
    # every literal brace exactly as written (json.dumps escapes the
    # inner quotes, which is exactly what json.dumps does).
    prompt = build_tool_context("decode", {}, {"kind": "jwt", "raw": '{"sub":"1"}'})
    assert '{"kind": "jwt", "raw": "{\\"sub\\":\\"1\\"}"}' in prompt


def test_templates_are_langchain_prompt_templates():
    # The pipeline prompts are real LangChain objects, not plain strings.
    assert sorted(DIAGNOSIS_CONTEXT_PROMPT.input_variables) == [
        "evidence",
        "observed",
        "question",
    ]
    assert sorted(ASK_CONTEXT_PROMPT.input_variables) == [
        "conversation",
        "evidence",
        "question",
    ]
    assert sorted(TOOL_RESULT_PROMPT.input_variables) == [
        "arguments",
        "tool",
        "tool_result",
    ]
