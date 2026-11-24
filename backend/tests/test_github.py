"""GitHub connector: status, read tools, routing, and honest errors."""

import base64

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
        if "/contents/" in path:
            if path == "/repos/octocat/Hello-World/contents/src/main.py":
                body = "print('hello')\n" * 300  # 4,200 chars → truncated
                return httpx.Response(
                    200,
                    json={
                        "type": "file",
                        "encoding": "base64",
                        "size": len(body),
                        "content": base64.b64encode(body.encode()).decode(),
                        "html_url": "https://github.com/octocat/Hello-World/blob/main/src/main.py",
                    },
                )
            if path == "/repos/octocat/Hello-World/contents/src":
                return httpx.Response(
                    200,
                    json=[
                        {"name": "main.py", "type": "file", "size": 4200},
                        {"name": "utils", "type": "dir", "size": 0},
                    ],
                )
            if path == "/repos/octocat/Hello-World/contents/openapi.yaml":
                spec = (
                    "openapi: 3.0.0\n"
                    "info:\n  title: Demo API\n  version: 1.0.0\n"
                    "servers:\n  - url: https://api.example.test\n"
                    "paths:\n"
                    "  /users:\n"
                    "    get:\n      summary: List users\n"
                    "    post:\n      summary: Create a user\n"
                    "  /users/{id}:\n"
                    "    get:\n      summary: Get one user\n"
                )
                return httpx.Response(
                    200,
                    json={
                        "type": "file",
                        "encoding": "base64",
                        "size": len(spec),
                        "content": base64.b64encode(spec.encode()).decode(),
                        "html_url": "https://github.com/octocat/Hello-World/blob/main/openapi.yaml",
                    },
                )
            return httpx.Response(404, json={"message": "Not Found"})
        if path == "/repos/octocat/Hello-World/commits":
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": "abcdef1234567890",
                        "commit": {
                            "message": "Fix 401 handling in the orders endpoint\n\nBody",
                            "author": {"name": "Alice", "date": "2026-02-01T10:00:00Z"},
                        },
                        "author": {"login": "alice"},
                        "html_url": "https://github.com/octocat/Hello-World/commit/abcdef1",
                    }
                ],
            )
        if path == "/repos/octocat/Hello-World/actions/runs":
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "workflow_runs": [
                        {
                            "name": "CI",
                            "head_branch": "main",
                            "event": "push",
                            "status": "completed",
                            "conclusion": "failure",
                            "created_at": "2026-02-02T10:00:00Z",
                            "html_url": "https://github.com/octocat/Hello-World/actions/runs/1",
                        },
                        {
                            "name": "CI",
                            "head_branch": "main",
                            "event": "push",
                            "status": "completed",
                            "conclusion": "success",
                            "created_at": "2026-02-01T10:00:00Z",
                            "html_url": "https://github.com/octocat/Hello-World/actions/runs/2",
                        },
                    ],
                },
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


# ── Repository contents ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_decodes_and_truncates(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    result = await github.read_file("octocat/Hello-World", "src/main.py")

    assert result["kind"] == "file"
    assert result["truncated"] is True
    assert len(result["content"]) == github._MAX_FILE_CHARS
    assert result["lines"] == 301
    assert github_http[0]["path"] == "/repos/octocat/Hello-World/contents/src/main.py"


@pytest.mark.asyncio
async def test_read_file_lists_a_directory(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    result = await github.read_file("octocat/Hello-World", "src")

    assert result["kind"] == "directory"
    assert result["count"] == 2
    assert result["entries"][0]["name"] == "main.py"


@pytest.mark.asyncio
async def test_read_file_refuses_secret_files(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")

    with pytest.raises(github.GitHubError, match="secrets file"):
        await github.read_file("octocat/Hello-World", ".env")

    # Refused before any request: the credential never leaves the server.
    assert github_http == []


@pytest.mark.asyncio
async def test_read_file_allows_env_examples(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")

    with pytest.raises(github.GitHubError, match="was not found"):
        await github.read_file("octocat/Hello-World", ".env.example")

    assert github_http, "the example file should be requested, not blocked"


@pytest.mark.asyncio
async def test_read_file_missing_is_explained(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")

    with pytest.raises(github.GitHubError, match="was not found"):
        await github.read_file("octocat/Hello-World", "src/nope.py")


@pytest.mark.asyncio
async def test_read_file_rejects_path_traversal(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")

    with pytest.raises(github.GitHubError, match="leaves the repository"):
        await github.read_file("octocat/Hello-World", "../secrets.py")

    assert github_http == []


@pytest.mark.asyncio
async def test_openapi_spec_parses_yaml(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    result = await github.openapi_spec("octocat/Hello-World", "openapi.yaml")

    assert result["title"] == "Demo API"
    assert result["version"] == "1.0.0"
    assert result["spec_version"] == "3.0.0"
    assert result["servers"] == ["https://api.example.test"]
    assert result["count"] == 3
    pairs = {(item["method"], item["path"]) for item in result["endpoints"]}
    assert ("GET", "/users") in pairs
    assert ("POST", "/users") in pairs
    assert ("GET", "/users/{id}") in pairs


@pytest.mark.asyncio
async def test_openapi_spec_probes_common_paths(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    result = await github.openapi_spec("octocat/Hello-World")

    assert result["path"] == "openapi.yaml"
    assert result["count"] == 3


@pytest.mark.asyncio
async def test_openapi_spec_not_found_is_actionable(monkeypatch):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    _patch_http(monkeypatch, handler)
    with pytest.raises(github.GitHubError, match="No OpenAPI or Swagger file"):
        await github.openapi_spec("octocat/Hello-World")


@pytest.mark.asyncio
async def test_recent_commits(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    result = await github.recent_commits("octocat/Hello-World", "src/main.py", 5)

    assert result["count"] == 1
    assert result["path"] == "src/main.py"
    assert result["items"][0]["sha"] == "abcdef1"
    assert result["items"][0]["message"] == "Fix 401 handling in the orders endpoint"
    assert result["items"][0]["author"] == "alice"
    assert github_http[0]["path"] == "/repos/octocat/Hello-World/commits"
    assert github_http[0]["params"]["path"] == "src/main.py"
    assert github_http[0]["params"]["per_page"] == "5"


@pytest.mark.asyncio
async def test_workflow_runs_counts_failures(monkeypatch, github_http):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")
    result = await github.workflow_runs("octocat/Hello-World", "main", 5)

    assert result["count"] == 2
    assert result["failed"] == 1
    assert result["branch"] == "main"
    assert result["items"][0]["conclusion"] == "failure"
    assert github_http[0]["path"] == "/repos/octocat/Hello-World/actions/runs"
    assert github_http[0]["params"]["branch"] == "main"


@pytest.mark.asyncio
async def test_workflow_runs_missing_is_explained(monkeypatch):
    monkeypatch.setattr(settings, "github_pat", "github_pat_test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    _patch_http(monkeypatch, handler)
    with pytest.raises(github.GitHubError, match="Actions is not available"):
        await github.workflow_runs("octocat/Hello-World")


# ── Routing for repository contents ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_file_route_keeps_path_out_of_the_repo():
    decision = await route_question("read src/main.py in octocat/Hello-World")

    assert decision.route == "tool:github_file"
    assert decision.args == {
        "repo": "octocat/Hello-World",
        "path": "src/main.py",
    }


@pytest.mark.asyncio
async def test_github_spec_route():
    decision = await route_question(
        "show me the openapi spec in octocat/Hello-World"
    )

    assert decision.route == "tool:github_spec"
    assert decision.args["repo"] == "octocat/Hello-World"
    assert decision.args["path"] == ""


@pytest.mark.asyncio
async def test_github_commits_route_for_one_file():
    decision = await route_question(
        "show recent commits for src/main.py in octocat/Hello-World"
    )

    assert decision.route == "tool:github_commits"
    assert decision.args["path"] == "src/main.py"


@pytest.mark.asyncio
async def test_github_ci_route_and_branch(monkeypatch):
    monkeypatch.setattr(settings, "github_default_repo", "octocat/Hello-World")
    decision = await route_question("are the GitHub Actions checks passing on main?")

    assert decision.route == "tool:github_ci"
    assert decision.args["repo"] == "octocat/Hello-World"
    assert decision.args["branch"] == "main"


@pytest.mark.asyncio
async def test_file_question_uses_the_default_repo(monkeypatch):
    monkeypatch.setattr(settings, "github_default_repo", "octocat/Hello-World")
    decision = await route_question("what is in package.json?")

    assert decision.route == "tool:github_file"
    assert decision.args["repo"] == "octocat/Hello-World"
    assert decision.args["path"] == "package.json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "what is package.json?",
        "how do I write an OpenAPI spec for my API?",
        "what is CI/CD?",
        "what is the repository pattern?",
        "what is next.js?",
        "what is TCP/IP?",
        "difference between JSON/YAML",
    ],
)
async def test_github_content_docs_stay_in_rag(question, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)

    decision = await route_question(question)
    assert decision.route == "rag"


def test_new_github_tools_are_wired_into_ask():
    import app.routes.ask as ask_module

    for name in ("github_file", "github_spec", "github_commits", "github_ci"):
        assert name in ask_module._TOOL_EXECUTORS


def test_new_github_labels_are_short_and_real():
    import app.routes.ask as ask_module

    assert (
        ask_module._tool_label(
            "github_ci", {"repo": "o/r", "branch": "main", "count": 2, "failed": 1}
        )
        == "GitHub · 2 runs · main · 1 failed"
    )
    assert (
        ask_module._tool_label("github_spec", {"path": "openapi.yaml", "count": 3})
        == "GitHub · openapi.yaml · 3 endpoints"
    )
    assert (
        ask_module._tool_label(
            "github_file", {"path": "src/main.py", "kind": "file", "lines": 1}
        )
        == "GitHub · src/main.py · 1 line"
    )
    assert (
        ask_module._tool_label(
            "github_commits", {"repo": "o/r", "count": 1, "path": None}
        )
        == "GitHub · 1 commit"
    )


def test_spec_fallback_summary_lists_endpoints():
    text = format_tool_result(
        "github_spec",
        {
            "path": "openapi.yaml",
            "title": "Demo API",
            "version": "1.0.0",
            "endpoints": [{"method": "GET", "path": "/users"}],
        },
    )

    assert "Demo API 1.0.0" in text
    assert "GET /users" in text
