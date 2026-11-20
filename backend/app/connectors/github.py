"""GitHub connector for the Ask tab.

The personal access token lives in ``GITHUB_PAT`` in the backend ``.env``
and never leaves the server — the browser only sees connection status.

Scope in this phase: **read-only**. Listing issues, pull requests, and
repository details are safe, explainable actions. Write actions (create
an issue, comment on one) stay disabled until Ask has the same explicit
approval flow the request tester uses for POST/PATCH — a model must
never be able to mutate a user's repository on its own.

The API calls are fixed-path requests to api.github.com with a
server-side token, so there is no user-supplied URL and no SSRF surface
(the same reasoning as the debug tools in ``app.ai.tools``).
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from app.core.config import settings

GITHUB_API_VERSION = "2022-11-28"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Cache the /user lookup so opening the Ask tab does not hit GitHub every
# time. Errors are not cached, so fixing the token takes effect after a
# backend restart (settings are read once per process anyway).
_STATUS_TTL_S = 60.0
_status_cache: dict[str, Any] | None = None
_status_cached_at = 0.0


class GitHubError(Exception):
    """GitHub could not answer (bad token, missing repo, network down)."""


class GitHubNotConfigured(GitHubError):
    """No GITHUB_PAT is set, so there is nothing to connect with."""


# What the sidebar shows for GitHub. `available` is honest: read tools
# work today, write tools wait for the approval flow.
GITHUB_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_issues",
        "description": "List issues in a repository",
        "write": False,
        "available": True,
    },
    {
        "name": "list_pull_requests",
        "description": "List pull requests in a repository",
        "write": False,
        "available": True,
    },
    {
        "name": "repo_summary",
        "description": "Read stars, forks, language, and default branch",
        "write": False,
        "available": True,
    },
    {
        "name": "list_repos",
        "description": "List repositories the token can access",
        "write": False,
        "available": True,
    },
    {
        "name": "create_issue",
        "description": "Open a new issue",
        "write": True,
        "available": False,
    },
    {
        "name": "comment_on_issue",
        "description": "Post a comment on an issue",
        "write": True,
        "available": False,
    },
]


def _token() -> str:
    return (settings.github_pat or "").strip()


def is_configured() -> bool:
    """True when a token is present (it may still be invalid)."""
    return bool(_token())


def default_repo() -> str:
    """The optional GITHUB_DEFAULT_REPO, normalized to owner/name."""
    return (settings.github_default_repo or "").strip().strip("/")


def reset_status_cache() -> None:
    """Drop the cached /user result (tests and token changes)."""
    global _status_cache, _status_cached_at
    _status_cache = None
    _status_cached_at = 0.0


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """One authenticated GET against the GitHub REST API."""
    if not is_configured():
        raise GitHubNotConfigured(
            "GitHub is not connected. Add GITHUB_PAT to the backend .env, "
            "then restart the backend."
        )

    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "apifix-ai",
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.github_api_url.rstrip("/"),
            timeout=_TIMEOUT,
            trust_env=False,
        ) as client:
            response = await client.get(path, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise GitHubError(
            f"GitHub API is unreachable ({type(exc).__name__})."
        ) from exc

    if response.status_code == 401:
        raise GitHubError(
            "GitHub rejected the token (401). Check GITHUB_PAT — it may be "
            "expired or revoked — then restart the backend."
        )
    if response.status_code == 403:
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining == "0":
            raise GitHubError(
                "GitHub rate limit reached for this token. Try again later."
            )
        raise GitHubError(
            "GitHub denied this request (403). The token may lack the "
            "Issues/Pull requests permission for this repository."
        )
    if response.status_code == 404:
        raise GitHubError(
            "Repository not found (404). Check the owner/repo spelling and "
            "that the token has access to it."
        )
    if response.status_code >= 400:
        raise GitHubError(f"GitHub returned HTTP {response.status_code}.")

    try:
        return response.json()
    except ValueError as exc:
        raise GitHubError("GitHub returned a response that was not JSON.") from exc


async def connection_status() -> dict[str, Any]:
    """Honest status for the sidebar: configured? connected? which account?"""
    global _status_cache, _status_cached_at

    status: dict[str, Any] = {
        "configured": is_configured(),
        "connected": False,
        "account": None,
        "name": None,
        "avatar_url": None,
        "default_repo": default_repo() or None,
        "source": "personal_access_token",
        "error": None,
        "tools": GITHUB_TOOLS,
    }
    if not status["configured"]:
        return status

    now = time.monotonic()
    if _status_cache is not None and now - _status_cached_at < _STATUS_TTL_S:
        return _status_cache

    try:
        user = await _get("/user")
    except GitHubError as exc:
        status["error"] = str(exc)
        return status

    status.update(
        {
            "connected": True,
            "account": user.get("login"),
            "name": user.get("name"),
            "avatar_url": user.get("avatar_url"),
        }
    )
    _status_cache = status
    _status_cached_at = now
    return status


def normalize_repo(value: str) -> str:
    """Accept 'owner/repo', a github.com URL, or fall back to the default."""
    candidate = (value or "").strip()
    if not candidate:
        candidate = default_repo()
    candidate = re.sub(r"^https?://(www\.)?github\.com/", "", candidate, flags=re.I)
    candidate = candidate.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", candidate):
        raise GitHubError(
            "Tell me which repository as owner/repo, for example "
            "octocat/Hello-World. You can also set GITHUB_DEFAULT_REPO in "
            "the backend .env."
        )
    return candidate


# Common path-like words that are not repositories (application/json, GET/POST).
_REPO_STOPWORDS = {
    "and", "or", "vs", "to", "from", "in", "of", "the",
    "application", "text", "content", "multipart", "www", "api", "docs",
}
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_URL_REPO = re.compile(
    r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.IGNORECASE
)
_BARE_REPO = re.compile(
    r"\b([A-Za-z0-9][A-Za-z0-9_.-]{0,38})/([A-Za-z0-9_.-]{1,100})\b"
)


def parse_repo(text: str) -> str | None:
    """Find an explicit owner/repo reference in a question, if any."""
    url_match = _URL_REPO.search(text)
    if url_match:
        return f"{url_match.group(1)}/{url_match.group(2)}"

    for match in _BARE_REPO.finditer(text):
        owner, name = match.group(1), match.group(2)
        if owner.lower() in _REPO_STOPWORDS or name.lower() in _REPO_STOPWORDS:
            continue
        if owner.upper() in _HTTP_METHODS:
            continue
        if not re.search(r"[A-Za-z]", owner + name):
            continue
        return f"{owner}/{name}"
    return None


async def list_issues(repo: str, state: str = "open", limit: int = 10) -> dict:
    """Open/closed issues, newest activity first. PRs are excluded."""
    repo = normalize_repo(repo)
    state = state if state in {"open", "closed", "all"} else "open"
    limit = max(1, min(int(limit), 30))

    items = await _get(
        f"/repos/{repo}/issues",
        params={"state": state, "per_page": limit, "sort": "updated"},
    )
    issues = [item for item in items if "pull_request" not in item]
    return {
        "repo": repo,
        "state": state,
        "count": len(issues),
        "items": [
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "author": (item.get("user") or {}).get("login"),
                "labels": [label.get("name") for label in item.get("labels") or []],
                "comments": item.get("comments"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "url": item.get("html_url"),
            }
            for item in issues
        ],
    }


async def list_pull_requests(repo: str, state: str = "open", limit: int = 10) -> dict:
    """Open/closed pull requests, newest activity first."""
    repo = normalize_repo(repo)
    state = state if state in {"open", "closed", "all"} else "open"
    limit = max(1, min(int(limit), 30))

    items = await _get(
        f"/repos/{repo}/pulls",
        params={"state": state, "per_page": limit, "sort": "updated"},
    )
    return {
        "repo": repo,
        "state": state,
        "count": len(items),
        "items": [
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "draft": item.get("draft"),
                "author": (item.get("user") or {}).get("login"),
                "head": (item.get("head") or {}).get("ref"),
                "base": (item.get("base") or {}).get("ref"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "url": item.get("html_url"),
            }
            for item in items
        ],
    }


async def repo_summary(repo: str) -> dict:
    """A repository's public metadata."""
    repo = normalize_repo(repo)
    data = await _get(f"/repos/{repo}")
    return {
        "repo": data.get("full_name") or repo,
        "description": data.get("description"),
        "private": data.get("private"),
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "open_issues": data.get("open_issues_count"),
        "language": data.get("language"),
        "default_branch": data.get("default_branch"),
        "updated_at": data.get("updated_at"),
        "url": data.get("html_url"),
    }


async def list_repos(limit: int = 10) -> dict:
    """Repositories the token can access, most recently updated first."""
    limit = max(1, min(int(limit), 30))
    items = await _get(
        "/user/repos",
        params={
            "affiliation": "owner,collaborator,organization_member",
            "sort": "updated",
            "per_page": limit,
        },
    )
    return {
        "count": len(items),
        "items": [
            {
                "repo": item.get("full_name"),
                "description": item.get("description"),
                "private": item.get("private"),
                "stars": item.get("stargazers_count"),
                "language": item.get("language"),
                "updated_at": item.get("updated_at"),
                "url": item.get("html_url"),
            }
            for item in items
        ],
    }
