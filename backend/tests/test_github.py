"""GitHub connector: status, read tools, routing, and honest errors."""

import httpx
import pytest
from fastapi.testclient import TestClient

# Captured before monkeypatching: the factory must build REAL clients.
_RealAsyncClient = httpx.AsyncClient

from app.ai.router import route_question
from app.ai.tools import ToolError, format_tool_result
from app.connectors import github
from app.core.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def clean_github(monkeypatch):
    """Every test starts unconfigured, with no cached status."""
    github.reset_status_cache()
    monkeypatch.setattr(settings, "github_pat", "")
    monkeypatch.setattr(settings, "github_default_repo", "")
    monkeypatch.setattr(settings, "github_api_url", "https://api.github.com")
    yield
    github.reset_status_cache()


def _patch_http(monkeypatch, handler):
    """Send the connector's requests to a MockTransport instead of GitHub."""

    def factory(**kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(github.httpx, "AsyncClient", factory)


@pytest.fixture
def github_http(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "auth": request.headers.get("authorization"),
                "api_version": request.headers.get("x-github-api-version"),
            }
        )
        path = request.url.path
        if path == "/user":
            return httpx.Response(
                200,
                json={
                    "login": "test-user",
                    "name": "Test User",
                    "avatar_url": "https://example.test/a.png",
                },
            )
        if path.endswith("/issues"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 7,
                        "title": "Fix 401 handling",
                        "state": "open",
                        "user": {"login": "alice"},
                        "labels": [{"name": "bug"}],
                        "comments": 2,
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-02T00:00:00Z",
                        "html_url": "https://github.com/o/r/issues/7",
                    },
                    {
                        # The issues endpoint also returns PRs; they must be filtered.
                        "number": 8,
                        "title": "A pull request",
                        "state": "open",
                        "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/8"},
                        "user": {"login": "bob"},
                        "labels": [],
                        "html_url": "https://github.com/o/r/pull/8",
                    },
                ],
            )
        if path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 3,
                        "title": "Add retries",
                        "state": "open",
                        "draft": False,
                        "user": {"login": "carol"},
                        "head": {"ref": "feature"},
                        "base": {"ref": "main"},
                        "html_url": "https://github.com/o/r/pull/3",
                    }
                ],
            )
        if path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "full_name": "test-user/api",
                        "description": "An API",
                        "private": True,
                        "stargazers_count": 5,
                        "language": "Python",
                        "updated_at": "2026-01-03T00:00:00Z",
                        "html_url": "https://github.com/test-user/api",
                    }
                ],
            )
        if path.startswith("/repos/"):
            return httpx.Response(
                200,
                json={
                    "full_name": "octocat/Hello-World",
                    "description": "Demo repository",
                    "private": False,
                    "stargazers_count": 42,
                    "forks_count": 7,
                    "open_issues_count": 3,
                    "language": "Go",
                    "default_branch": "main",
                    "updated_at": "2026-01-04T00:00:00Z",
                    "html_url": "https://github.com/octocat/Hello-World",
                },
            )
        return httpx.Response(404, json={"message": "Not Found"})

    _patch_http(monkeypatch, handler)
    return calls


# ── Status ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_is_honest_when_not_configured():
    status = await github.connection_status()
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["account"] is None
    assert status["error"] is None
    names = {tool["name"] for tool in status["tools"]}
    assert {"list_issues", "list_pull_requests", "repo_summary"} <= names
    write_tools = {tool["name"] for tool in status["tools"] if tool["write"]}
    assert write_tools == {"create_issue", "comment_on_issue"}
    assert all(not tool["available"] for tool in status["tools"] if tool["write"])


@pytest.mark.asyncio
async def test_status_reports_the_real_account(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    status = await github.connection_status()

    assert status["connected"] is True
    assert status["account"] == "test-user"
    assert status["name"] == "Test User"
    assert status["error"] is None
    assert github_http[0]["path"] == "/user"
    assert github_http[0]["auth"] == "Bearer github_pat_test"
    assert github_http[0]["api_version"] == "2022-11-28"


@pytest.mark.asyncio
async def test_status_success_is_cached(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    await github.connection_status()
    await github.connection_status()
    assert len(github_http) == 1


@pytest.mark.asyncio
async def test_bad_token_is_explained_not_hidden(monkeypatch):
    monkeypatch.setattr(settings, "github_pat", "bad-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    _patch_http(monkeypatch, handler)
    status = await github.connection_status()

    assert status["configured"] is True
    assert status["connected"] is False
    assert "GITHUB_PAT" in status["error"]


# ── Read tools ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_issues_filters_pull_requests_and_passes_params(
    monkeypatch, github_http
):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    result = await github.list_issues("octocat/Hello-World", "open", 5)

    assert result["repo"] == "octocat/Hello-World"
    assert result["count"] == 1
    assert result["items"][0]["number"] == 7
    assert result["items"][0]["author"] == "alice"
    assert result["items"][0]["labels"] == ["bug"]

    call = github_http[0]
    assert call["path"] == "/repos/octocat/Hello-World/issues"
    assert call["params"]["state"] == "open"
    assert call["params"]["per_page"] == "5"


@pytest.mark.asyncio
async def test_list_pull_requests(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    result = await github.list_pull_requests("octocat/Hello-World", "closed", 10)

    assert result["count"] == 1
    assert result["items"][0]["number"] == 3
    assert result["items"][0]["head"] == "feature"
    assert github_http[0]["params"]["state"] == "closed"


@pytest.mark.asyncio
async def test_repo_summary_and_repo_list(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")

    summary = await github.repo_summary("octocat/Hello-World")
    assert summary["stars"] == 42
    assert summary["default_branch"] == "main"

    repos = await github.list_repos(10)
    assert repos["count"] == 1
    assert repos["items"][0]["repo"] == "test-user/api"


@pytest.mark.asyncio
async def test_default_repo_is_used_when_none_is_named(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    monkeypatch.setattr(settings, "github_default_repo", "octocat/Hello-World")

    result = await github.list_issues("")
    assert result["repo"] == "octocat/Hello-World"


@pytest.mark.asyncio
async def test_missing_repo_error_tells_the_user_what_to_do(monkeypatch):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    with pytest.raises(github.GitHubError, match="owner/repo"):
        await github.list_issues("")


def test_parse_repo_ignores_content_types_and_http_methods():
    assert (
        github.parse_repo("https://github.com/octocat/Hello-World/issues")
        == "octocat/Hello-World"
    )
    assert github.parse_repo("list issues in octocat/Hello-World") == "octocat/Hello-World"
    assert github.parse_repo("application/json is a content type") is None
    assert github.parse_repo("use GET/POST here") is None


# ── Router ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_issues_route_beats_api_markers():
    decision = await route_question("list open issues in octocat/Hello-World")
    assert decision.route == "tool:github_issues"
    assert decision.via == "rule"
    assert decision.args == {
        "repo": "octocat/Hello-World",
        "state": "open",
        "limit": 10,
    }


@pytest.mark.asyncio
async def test_github_closed_pull_requests_route():
    decision = await route_question(
        "show closed pull requests for octocat/Hello-World"
    )
    assert decision.route == "tool:github_prs"
    assert decision.args["state"] == "closed"


@pytest.mark.asyncio
async def test_github_repo_route():
    decision = await route_question("how many stars does octocat/Hello-World have?")
    assert decision.route == "tool:github_repo"
    assert decision.args["repo"] == "octocat/Hello-World"


@pytest.mark.asyncio
async def test_github_uses_default_repo_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "github_default_repo", "octocat/Hello-World")
    decision = await route_question("show my open github issues")
    assert decision.route == "tool:github_issues"
    assert decision.args["repo"] == "octocat/Hello-World"


@pytest.mark.asyncio
async def test_github_documentation_stays_in_rag(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    decision = await route_question("what is GitHub Actions?")
    assert decision.route == "rag"


@pytest.mark.asyncio
async def test_write_request_routes_but_is_blocked_at_execution():
    import app.routes.ask as ask_module

    decision = await route_question("create an issue in octocat/Hello-World")
    assert decision.route == "tool:github_write"

    with pytest.raises(ToolError, match="read-only"):
        await ask_module._execute_tool("github_write", decision.args)


@pytest.mark.asyncio
async def test_connector_errors_become_clean_tool_errors(monkeypatch):
    import app.routes.ask as ask_module

    async def rejected(*args, **kwargs):
        raise github.GitHubError("GitHub rejected the token (401).")

    monkeypatch.setattr(github, "list_issues", rejected)
    with pytest.raises(ToolError, match="401"):
        await ask_module._execute_tool("github_issues", {"repo": "o/r"})


# ── Status endpoint + fallback formatting ────────────────────────────────────

def test_connections_endpoint_returns_honest_state():
    client = TestClient(app)
    response = client.get("/api/connections")
    assert response.status_code == 200

    payload = response.json()["github"]
    assert payload["configured"] is False
    assert payload["connected"] is False
    assert payload["source"] == "personal_access_token"
    assert payload["default_repo"] is None
    assert payload["tools"]


def test_github_results_read_like_a_sentence():
    issues = format_tool_result(
        "github_issues",
        {
            "repo": "octocat/Hello-World",
            "state": "open",
            "items": [
                {"number": 7, "title": "Fix 401 handling", "author": "alice"},
                {"number": 9, "title": "Docs", "author": None},
            ],
        },
    )
    assert "octocat/Hello-World" in issues
    assert "2 open issues" in issues
    assert "#7 Fix 401 handling (by alice)" in issues

    summary = format_tool_result(
        "github_repo",
        {
            "repo": "octocat/Hello-World",
            "stars": 42,
            "forks": 7,
            "language": "Go",
            "default_branch": "main",
            "description": "Demo repository",
        },
    )
    assert "★ 42" in summary and "Go" in summary and "main" in summary
