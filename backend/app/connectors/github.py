"""GitHub connector for the Ask tab.

The personal access token lives in ``GITHUB_PAT`` in the backend ``.env``
and never leaves the server — the browser only sees connection status.

Scope in this phase: **read-only**. Listing issues, pull requests,
repository details, and reading the code itself (files, the OpenAPI
spec, commits, CI runs) are safe, explainable actions. Write actions
(create an issue, comment on one) stay disabled until Ask has the same
explicit approval flow the request tester uses for POST/PATCH — a model
must never be able to mutate a user's repository on its own.

Reading files is limited on purpose: secrets-looking paths (``.env``,
private keys, credential files) are refused, and file bodies are
truncated before they reach a model.

The API calls are fixed-path requests to api.github.com with a
server-side token, so there is no user-supplied URL and no SSRF surface
(the same reasoning as the debug tools in ``app.ai.tools``).
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

import httpx
import yaml

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
        "name": "read_file",
        "description": "Read a source or docs file from a repository",
        "write": False,
        "available": True,
    },
    {
        "name": "openapi_spec",
        "description": "Read the OpenAPI/Swagger spec and list its endpoints",
        "write": False,
        "available": True,
    },
    {
        "name": "recent_commits",
        "description": "List recent commits, optionally for one file",
        "write": False,
        "available": True,
    },
    {
        "name": "workflow_runs",
        "description": "Check recent CI runs and their conclusions",
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


async def _get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    missing_ok: bool = False,
) -> Any:
    """One authenticated GET against the GitHub REST API.

    ``missing_ok`` returns ``None`` on a 404 instead of raising: the OpenAPI
    probe has to try several candidate paths, and a missing file is not an
    error there.
    """
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
        if missing_ok:
            return None
        raise GitHubError(
            "Repository not found (404). Check the owner/repo spelling and "
            "that the token has access to it."
        )
    if response.status_code == 409:
        raise GitHubError("This repository is empty (409), so it has no history yet.")
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
    # Protocol / format pairs that look like owner/repo: "CI/CD", "TCP/IP".
    "ci", "cd", "cicd", "tcp", "udp", "ip", "tls", "ssl", "dns", "http",
    "https", "grpc", "json", "yaml", "xml", "csv", "html", "css", "sql",
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


# ── Repository contents (the tools that answer "why is this endpoint broken?") ──

# File bodies are truncated before they are sent to a model: a 3000-line
# generated client would otherwise fill the whole context window.
_MAX_FILE_CHARS = 4000

# Reading a repository is not permission to read its secrets. These names are
# refused so a token, private key, or credential file never reaches the LLM.
_SENSITIVE_FILE = re.compile(
    r"(?:^|/)"
    r"(?:\.env(?!\.(?:example|sample|template))(?:\.[\w.-]+)?"
    r"|\.npmrc|\.pypirc|\.netrc|\.git-credentials"
    r"|id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?"
    r"|[\w.-]+\.(?:pem|key|p12|pfx|jks|keystore)"
    r"|(?:secrets?|credentials?)[\w.-]*\.(?:json|ya?ml|toml|ini|txt|env))$",
    re.IGNORECASE,
)

# Where an OpenAPI document usually lives. Tried in order when the question
# does not name a file.
_SPEC_CANDIDATES = (
    "openapi.yaml",
    "openapi.yml",
    "openapi.json",
    "swagger.yaml",
    "swagger.yml",
    "swagger.json",
    "api/openapi.yaml",
    "api/openapi.yml",
    "api/openapi.json",
    "docs/openapi.yaml",
    "docs/openapi.yml",
    "docs/openapi.json",
    "spec/openapi.yaml",
    "spec/openapi.json",
    "public/openapi.json",
)

_SPEC_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_MAX_SPEC_ENDPOINTS = 60


def _clean_path(path: str) -> str:
    """Normalize a repository-relative path, refusing traversal."""
    cleaned = (path or "").strip().strip("`'\"").lstrip("/")
    if ".." in cleaned.split("/"):
        raise GitHubError("That path leaves the repository, so I won't read it.")
    return cleaned


async def _fetch_text(repo: str, path: str) -> str | None:
    """A repository file as text, or None when it is missing/not a file."""
    data = await _get(f"/repos/{repo}/contents/{path}", missing_ok=True)
    if not isinstance(data, dict) or not data.get("content"):
        return None
    try:
        return base64.b64decode(data["content"]).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return None


async def read_file(repo: str, path: str, ref: str = "") -> dict:
    """Read one file (or list a directory) from a repository."""
    repo = normalize_repo(repo)
    path = _clean_path(path)
    if not path:
        raise GitHubError(
            "Tell me which file to read, for example src/app/main.py."
        )
    if _SENSITIVE_FILE.search(path):
        raise GitHubError(
            f"I won't read {path}: it looks like a secrets file. Point me at "
            "the source file that uses the credential instead."
        )

    params = {"ref": ref.strip()} if (ref or "").strip() else None
    data = await _get(
        f"/repos/{repo}/contents/{path}", params=params, missing_ok=True
    )
    if data is None:
        raise GitHubError(
            f"{path} was not found in {repo}. Check the path and the branch."
        )

    if isinstance(data, list):
        entries = [
            {
                "name": entry.get("name"),
                "type": entry.get("type"),
                "size": entry.get("size"),
            }
            for entry in data[:40]
        ]
        return {
            "repo": repo,
            "path": path,
            "kind": "directory",
            "count": len(data),
            "entries": entries,
        }

    if data.get("encoding") != "base64" or not data.get("content"):
        raise GitHubError(
            f"{path} is too large for the GitHub contents API (limit 1 MB), "
            "or it is not stored as text. Try a specific source file instead."
        )

    text = base64.b64decode(data["content"]).decode("utf-8", "replace")
    truncated = len(text) > _MAX_FILE_CHARS
    return {
        "repo": repo,
        "path": path,
        "kind": "file",
        "size": data.get("size"),
        "lines": text.count("\n") + 1,
        "truncated": truncated,
        "content": text[:_MAX_FILE_CHARS],
        "url": data.get("html_url"),
    }


def _parse_spec_document(text: str, path: str) -> dict:
    """Parse JSON or YAML and insist on an object."""
    try:
        document = (
            json.loads(text)
            if path.lower().endswith(".json")
            else yaml.safe_load(text)
        )
    except (ValueError, yaml.YAMLError) as exc:
        raise GitHubError(
            f"{path} could not be parsed as JSON or YAML ({type(exc).__name__})."
        ) from exc
    if not isinstance(document, dict):
        raise GitHubError(f"{path} is not an OpenAPI document.")
    return document


def _spec_summary(document: dict, repo: str, path: str) -> dict:
    """The parts of a spec that matter for debugging: endpoints and servers."""
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise GitHubError(
            f"{path} does not look like an OpenAPI document: it has no 'paths'. "
            "Point me at openapi.yaml/json or swagger.json."
        )

    endpoints: list[dict[str, Any]] = []
    for route, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in _SPEC_METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            summary = operation.get("summary") or operation.get("description") or ""
            summary = str(summary).strip().splitlines()[0][:120] if summary else ""
            endpoints.append(
                {
                    "method": method.upper(),
                    "path": route,
                    "summary": summary or None,
                    "operation_id": operation.get("operationId"),
                }
            )

    servers = document.get("servers") or []
    server_urls = [
        server.get("url")
        for server in servers
        if isinstance(server, dict) and server.get("url")
    ]
    if not server_urls and document.get("host"):
        # Swagger 2.0 keeps the base URL in host + basePath.
        server_urls = [f"https://{document['host']}{document.get('basePath', '')}"]

    info = document.get("info") or {}
    return {
        "repo": repo,
        "path": path,
        "spec_version": document.get("openapi") or document.get("swagger"),
        "title": info.get("title"),
        "version": info.get("version"),
        "servers": server_urls[:5],
        "count": len(endpoints),
        "truncated": len(endpoints) > _MAX_SPEC_ENDPOINTS,
        "endpoints": endpoints[:_MAX_SPEC_ENDPOINTS],
    }


async def openapi_spec(repo: str, path: str = "") -> dict:
    """Read the repository's OpenAPI/Swagger document and list its endpoints."""
    repo = normalize_repo(repo)
    path = _clean_path(path)

    if path:
        text = await _fetch_text(repo, path)
        if text is None:
            raise GitHubError(f"{path} was not found in {repo}.")
        return _spec_summary(_parse_spec_document(text, path), repo, path)

    tried: list[str] = []
    for candidate in _SPEC_CANDIDATES:
        tried.append(candidate)
        text = await _fetch_text(repo, candidate)
        if not text:
            continue
        try:
            return _spec_summary(
                _parse_spec_document(text, candidate), repo, candidate
            )
        except GitHubError:
            # A YAML file that is not an API spec: keep looking.
            continue

    raise GitHubError(
        f"No OpenAPI or Swagger file found in {repo}. I tried: "
        + ", ".join(tried[:8])
        + '. Tell me the path, for example "read docs/openapi.yaml".'
    )


async def recent_commits(
    repo: str, path: str = "", limit: int = 10, ref: str = ""
) -> dict:
    """Recent commits, optionally narrowed to one file or branch."""
    repo = normalize_repo(repo)
    path = _clean_path(path)
    limit = max(1, min(int(limit), 30))

    params: dict[str, Any] = {"per_page": limit}
    if path:
        params["path"] = path
    if (ref or "").strip():
        params["sha"] = ref.strip()

    items = await _get(f"/repos/{repo}/commits", params=params)
    if not isinstance(items, list):
        raise GitHubError(f"GitHub returned an unexpected answer for {repo}'s commits.")

    return {
        "repo": repo,
        "path": path or None,
        "count": len(items),
        "items": [
            {
                "sha": (item.get("sha") or "")[:7],
                "message": (
                    ((item.get("commit") or {}).get("message") or "")
                    .splitlines()[0][:140]
                ),
                "author": (item.get("author") or {}).get("login")
                or (((item.get("commit") or {}).get("author") or {}).get("name")),
                "date": ((item.get("commit") or {}).get("author") or {}).get("date"),
                "url": item.get("html_url"),
            }
            for item in items
        ],
    }


async def workflow_runs(repo: str, branch: str = "", limit: int = 5) -> dict:
    """The most recent GitHub Actions runs, with their conclusions."""
    repo = normalize_repo(repo)
    limit = max(1, min(int(limit), 20))

    params: dict[str, Any] = {"per_page": limit}
    if (branch or "").strip():
        params["branch"] = branch.strip()

    data = await _get(
        f"/repos/{repo}/actions/runs", params=params, missing_ok=True
    )
    if data is None:
        raise GitHubError(
            f"GitHub Actions is not available for {repo} (404). The repository "
            "may be empty, or Actions may be disabled."
        )

    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    if not isinstance(runs, list):
        raise GitHubError(
            f"GitHub returned an unexpected answer for {repo}'s workflow runs."
        )

    items = [
        {
            "name": run.get("name")
            or (run.get("workflow") or {}).get("name")
            or run.get("display_title"),
            "branch": run.get("head_branch"),
            "event": run.get("event"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "created_at": run.get("created_at"),
            "url": run.get("html_url"),
        }
        for run in runs
    ]
    failed = sum(
        1
        for item in items
        if item["conclusion"]
        in {"failure", "timed_out", "startup_failure", "cancelled"}
    )
    return {
        "repo": repo,
        "branch": (branch or "").strip() or None,
        "count": len(items),
        "failed": failed,
        "items": items,
    }
