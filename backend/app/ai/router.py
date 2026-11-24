"""Question router: send a question to RAG or to a real tool.

Layered decision, cheapest first:

1. Fast rules answer the obvious cases instantly (no network): clear
   API/documentation questions -> "rag"; unmistakable debug-helper
   requests (JWT decode, epoch, DNS, IP, time) -> their tool.
2. Anything ambiguous goes to the NVIDIA LLM (OpenAI-compatible NIM
   endpoint) with a tiny classification prompt and a short timeout.
   If NVIDIA is unavailable, unreachable, or returns garbage, the
   default is "rag" — the assistant never blocks on the router.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Literal

import httpx

Route = Literal[
    "rag",
    "tool:time",
    "tool:epoch",
    "tool:decode",
    "tool:dns",
    "tool:ip",
    "tool:github_issues",
    "tool:github_prs",
    "tool:github_repo",
    "tool:github_repos",
    "tool:github_file",
    "tool:github_spec",
    "tool:github_commits",
    "tool:github_ci",
    "tool:sentry_issues",
    "tool:sentry_event",
    "tool:sentry_events",
    "tool:github_write",
    "tool:gcal_events",
    "tool:gcal_search",
    "tool:gmail_search",
    "tool:google_write",
]

_TOOL_ROUTES = (
    "tool:time",
    "tool:epoch",
    "tool:decode",
    "tool:dns",
    "tool:ip",
    "tool:github_issues",
    "tool:github_prs",
    "tool:github_repo",
    "tool:github_repos",
    "tool:github_file",
    "tool:github_spec",
    "tool:github_commits",
    "tool:github_ci",
    "tool:sentry_issues",
    "tool:sentry_event",
    "tool:sentry_events",
    "tool:github_write",
    "tool:gcal_events",
    "tool:gcal_search",
    "tool:gmail_search",
    "tool:google_write",
)

_NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_NVIDIA_TIMEOUT = httpx.Timeout(8.0, connect=4.0)

# ── Fast rules ────────────────────────────────────────────────────────────────

_API_MARKERS = re.compile(
    r"\b(api|endpoint|http|https|status code|response|request|header|bearer|"
    r"auth|oauth|token|json|rest|graphql|webhook|pagination|rate limit|"
    r"curl|payload|cors|ssl|latency|docs?|documentation|idempoten|"
    r"put\b|patch\b|delete\b|422|401|403|404|405|429|500|502|503)\b",
    re.IGNORECASE,
)
# Decode requests are checked FIRST: a JWT question contains token-like words
# that would otherwise trip the API markers, and a URL inside the payload
# would trip "http".
_DECODE_MARKERS = re.compile(
    r"\bdecod\w*|\bbase64\b|\burl\s*encoded\b|\burl\s*decoded\b|\bunencode\w*\b|"
    r"\bjwt\b.*\b(?:claims?|header|payload|exp)\b|\beyJ[A-Za-z0-9_-]{6,}",
    re.IGNORECASE,
)
# DNS before API markers: "dns of api.github.com" contains "api".
_DNS_MARKERS = re.compile(
    r"\b(dns|resolve[sd]?\s+(?:to|as)|a\s+record|cname|nameserver|"
    r"dig\s|nslookup)\b|\bdns\s+(?:of|for|lookup)",
    re.IGNORECASE,
)
_IP_PHRASE_MARKERS = re.compile(
    r"\b(?:where\s+is|who\s+owns?|geolocat\w*|locate|hosted\s+at|hosting\s+of)\s+"
    r"(?:\d{1,3}\.){3}\d{1,3}\b",
    re.IGNORECASE,
)
_TIME_MARKERS = re.compile(
    r"\b(?:what(?:'s| is)? the time|what time|time in|current time|"
    r"local time|world clock|timezone of|time zone of)\b",
    re.IGNORECASE,
)
_EPOCH_MARKERS = re.compile(
    r"\b(?:unix\s+timestamp|epoch|posix\s+time|"
    r"timestamp\s+\d{5,}|convert\s+\d{5,})\b|\b\d{10,13}\b",
)
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")
_DOMAIN = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|org|net|io|dev|ai|in|co|gov|edu|app|me|uk|de|fr|jp|us)\b",
    re.IGNORECASE,
)

# GitHub: only route when the question asks for repository DATA. A plain
# "what is GitHub Actions?" stays in the knowledge base.
_GITHUB_MARKERS = re.compile(r"\bgithub\b|github\.com", re.IGNORECASE)
_GITHUB_ISSUE_MARKERS = re.compile(r"\bissues?\b|\bbugs?\b|\btickets?\b", re.IGNORECASE)
_GITHUB_PR_MARKERS = re.compile(
    r"\b(?:pull\s+requests?|prs?|merge\s+requests?)\b", re.IGNORECASE
)
_GITHUB_REPO_MARKERS = re.compile(
    r"\b(?:repos?|repositories?|stars?|forks?|default\s+branch|about)\b",
    re.IGNORECASE,
)
_GITHUB_LIST_REPOS = re.compile(
    r"\b(?:my|all|list|show)\b[^.!?]{0,24}\b(?:repos?|repositories?)\b",
    re.IGNORECASE,
)
_GITHUB_WRITE_MARKERS = re.compile(
    r"\b(?:create|file|submit|post|add|make|close|comment\s+on)\b"
    r"[^.!?]{0,40}\b(?:issues?|prs?|pull\s+requests?)\b"
    r"|\bopen\s+(?:a|an|new)\s+(?:issues?|prs?|pull\s+requests?)\b",
    re.IGNORECASE,
)
_GITHUB_CLOSED_STATE = re.compile(r"\bclosed\b|\bmerged\b|\bdone\b", re.IGNORECASE)
_GITHUB_ALL_STATE = re.compile(r"\ball\b|\bevery\b", re.IGNORECASE)

# Deeper repository questions: reading code, the API spec, history, and CI.
# These are the ones that answer "why is this endpoint broken?", so they only
# fire with a repository context — otherwise "what is package.json?" would
# leave the knowledge base.
_GITHUB_FILE_RE = re.compile(
    r"\b[\w.-]+(?:/[\w.-]+)*\.(?:py|ts|tsx|js|jsx|mjs|cjs|json|ya?ml|toml|ini|cfg|"
    r"conf|md|txt|go|rb|java|kt|php|cs|rs|sh|sql)\b",
    re.IGNORECASE,
)
# "show me X", "what's in X": wording that asks for file contents, not a
# definition. "what is package.json?" stays in RAG.
_GITHUB_FILE_QUESTION = re.compile(
    r"\b(?:read|show|open|print|display|cat|contents?\s+of|"
    r"what(?:'s| is)\s+(?:in|inside))\b",
    re.IGNORECASE,
)
_GITHUB_SPEC_MARKERS = re.compile(
    r"\b(?:openapi|swagger)\b|\bapi\s+spec(?:ification)?\b|\bspec\s+file\b"
    r"|\bendpoints?\s+(?:in|from|of)\b",
    re.IGNORECASE,
)
_GITHUB_COMMIT_MARKERS = re.compile(
    r"\b(?:commits?|git\s+log|recent\s+changes?|"
    r"changed\s+(?:recently|this\s+week|lately)|"
    r"last\s+\d+\s+(?:commits?|changes))\b",
    re.IGNORECASE,
)
_GITHUB_COMMIT_STRONG = re.compile(r"\b(?:commits?|git\s+log)\b", re.IGNORECASE)
_GITHUB_CI_MARKERS = re.compile(
    r"\b(?:ci|workflow\s+runs?|github\s+actions|pipeline|"
    r"build\s+(?:status|fail\w*|red|green)|"
    r"checks?\s+(?:status|fail\w*|red|green|pass\w*)|"
    r"failing\s+(?:build|ci|tests?))\b",
    re.IGNORECASE,
)
_GITHUB_CI_QUERY = re.compile(
    r"\b(?:status|state|pass\w*|fail\w*|red|green|broken|running|queued|runs?|"
    r"results?|conclusion|latest|last|recent)\b",
    re.IGNORECASE,
)
_GITHUB_REPO_HINT = re.compile(
    r"\b(?:repos?|repositories?|codebase|our\s+code|the\s+code)\b"
    r"|\b(?:our|my)\s+(?:api|service|backend|server|app)\b",
    re.IGNORECASE,
)
_GITHUB_DOCS_QUESTION = re.compile(
    r"^\s*(?:how|what|why|when|should|can|could|explain|difference|is|are|does|do)\b",
    re.IGNORECASE,
)
_GITHUB_BRANCH_RE = re.compile(
    r"\b(?:branch|on)\s+([A-Za-z0-9][A-Za-z0-9._/-]{1,40})\b", re.IGNORECASE
)
_GITHUB_BRANCH_WORDS = {
    "the", "my", "our", "a", "an", "this", "that", "default", "which", "what",
    "github", "git", "actions", "ci", "it",
}

# Sentry: production errors. Only route when Sentry is named, or when the
# question is clearly about recent production failures (or a concrete
# endpoint), so "what is a 500 error?" stays in the knowledge base.
_SENTRY_MARKERS = re.compile(r"\bsentry\b|\berror tracking\b", re.IGNORECASE)
# "What is Sentry?" is documentation; "what are the latest Sentry errors?" is
# data. The difference is whether Sentry is the subject of the question.
_SENTRY_DEFINITION = re.compile(
    r"^\s*(?:what(?:'s| is| are)|explain|define|how\s+does)\s+"
    r"(?:the\s+)?sentry\b",
    re.IGNORECASE,
)
_SENTRY_PROD = re.compile(
    r"\b(?:production|prod|live)\b[^.!?]{0,30}"
    r"\b(?:errors?|exceptions?|crashes?|failures?|fail(?:ing|ed)?)\b"
    r"|\b(?:errors?|exceptions?|crashes?|failures?|fail(?:ing|ed)?)\b"
    r"[^.!?]{0,30}\b(?:production|prod|live)\b",
    re.IGNORECASE,
)
_SENTRY_RECENT = re.compile(
    r"\b(?:latest|recent|unresolved|open|new|today|this week)\b", re.IGNORECASE
)
_SENTRY_ISSUE_ID = re.compile(r"\b(?:issue|event)\s*#?\s*(\d{3,})\b", re.IGNORECASE)
_SENTRY_STACK = re.compile(
    r"\bstack ?trace\b|\btraceback\b|\bexception\b", re.IGNORECASE
)
# A URL path, not a protocol or a date: "/api/orders", "/users/{id}".
_SENTRY_ENDPOINT = re.compile(r"(?<![\w:/])/(?:[\w{}.-]+/)*[\w{}.-]+")
_SENTRY_PERIODS = (
    (re.compile(r"\btoday\b|\blast 24 hours?\b", re.IGNORECASE), "24h"),
    (re.compile(r"\bthis week\b|\blast 7 days?\b", re.IGNORECASE), "7d"),
    (re.compile(r"\bthis month\b|\blast 30 days?\b", re.IGNORECASE), "30d"),
)

# Google Calendar / Gmail: only personal data questions. Anything about the
# APIs themselves (scopes, OAuth, integration) stays in the knowledge base.
_CAL_MARKERS = re.compile(
    r"\b(?:calendar|meetings?|appointments?|agenda|events?)\b", re.IGNORECASE
)
_CAL_PERSONAL = re.compile(
    r"\b(?:my|i|me|today|tomorrow|tonight|this week|next week|this month|"
    r"upcoming|next|busy|free|availability|am i)\b",
    re.IGNORECASE,
)
_GMAIL_MARKERS = re.compile(
    # "mail" on its own is how people usually say it ("check my mail").
    r"\b(?:gmail|e-?mails?|mail|inbox|mailbox|threads?)\b", re.IGNORECASE
)
_GMAIL_INTENT = re.compile(
    r"\b(?:find|search|show|list|any|latest|recent|unread|from|about|"
    r"regarding|received|sent|read|check|have|did)\b",
    re.IGNORECASE,
)
_GMAIL_PERSONAL = re.compile(
    # In this assistant there is only one mailbox, so "find emails about X"
    # means the user's own mail even without the word "my".
    r"\b(?:my|me|i|inbox|unread|from|to|received|sent|latest|recent|any|"
    r"e-?mails?|mails?)\b",
    re.IGNORECASE,
)
# Setup / documentation wording: "how do I integrate the Gmail API?" is RAG.
_SETUP_QUESTION = re.compile(
    r"\b(?:integrat\w*|set ?up|configure|oauth|scopes?|api key|credentials?|"
    r"documentation|docs|sdk|webhook)\b",
    re.IGNORECASE,
)
# Naming the product API ("Gmail API", "Calendar API") means the question is
# about the platform, not the user's mailbox — keep it in RAG.
_API_PRODUCT = re.compile(
    r"\b(?:gmail|calendar|google|workspace)\s+api\b", re.IGNORECASE
)
_GOOGLE_WRITE = re.compile(
    r"\b(?:create|schedule|add|book|delete|cancel|update|move|accept|decline|"
    r"respond to|send|draft|label|archive)\b[^.!?]{0,40}"
    r"\b(?:events?|meetings?|appointments?|e-?mails?|mails?|messages?|invites?|"
    r"invitations?)\b",
    re.IGNORECASE,
)
_CAL_TIME_WORDS = {
    "today", "tomorrow", "tonight", "this week", "next week", "this month",
    "the week", "the month", "my calendar", "my schedule",
}

# ── Argument extraction ──────────────────────────────────────────────────────

_CITY_FILLER = re.compile(
    r"\b(today|tomorrow|tonight|now|right now|currently|current|this week|"
    r"this weekend|please|tell me|give me|show me|the)\b",
    re.IGNORECASE,
)


@dataclass
class RouteDecision:
    route: Route
    via: str  # "rule" or "nvidia"
    args: dict = field(default_factory=dict)


def extract_city(question: str) -> str | None:
    """City name from a place phrase: 'time in Tokyo', 'clock for New York'."""
    match = re.search(
        r"\b(?:in|for|at|of)\s+([A-Za-z][A-Za-z .'-]{1,60})", question, re.IGNORECASE
    )
    if not match:
        return None
    candidate = _CITY_FILLER.sub(" ", match.group(1))
    candidate = re.split(r"\b(?:like|right|today|now)\b", candidate, flags=re.IGNORECASE)[0]
    candidate = candidate.strip(" .,?!")
    # A "city" longer than four words is almost always a parsed sentence.
    if not candidate or len(candidate.split()) > 4:
        return None
    return candidate


def extract_domain(question: str) -> str | None:
    """Domain name in the question, for DNS lookups."""
    match = _DOMAIN.search(question)
    if not match:
        return None
    return match.group(0).lower()


def extract_ip(question: str) -> str | None:
    """IPv4 address in the question, for geolocation."""
    match = _IPV4.search(question)
    return match.group(0) if match else None


def extract_epoch(question: str) -> str | None:
    """The largest 5-13 digit number, for the unix timestamp converter."""
    numbers = re.findall(r"\b(\d{5,13})\b", question)
    if not numbers:
        return None
    return max(numbers, key=len)


def extract_decode_payload(question: str) -> str | None:
    """The token/text to decode: prefer an eyJ… JWT, fall back to a quoted blob."""
    jwt = re.search(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.?[A-Za-z0-9_.-]*", question)
    if jwt:
        return jwt.group(0)
    quoted = re.search(r'''["'`]([^"'`]{12,})["'`]''', question)
    if quoted:
        return quoted.group(1)
    return None


def _github_state(question: str) -> str:
    if _GITHUB_CLOSED_STATE.search(question):
        return "closed"
    if _GITHUB_ALL_STATE.search(question):
        return "all"
    return "open"


def _github_file_path(question: str) -> str | None:
    """A repository-relative file path mentioned in the question, if any."""
    match = _GITHUB_FILE_RE.search(question)
    if not match:
        return None
    candidate = match.group(0).strip(".,;:!?`'\"")
    return candidate or None


def _github_branch(question: str) -> str:
    """The branch a CI question is about ("on main", "branch feature/x")."""
    if re.search(r"\bon\s+(?:the\s+)?main\b", question, re.IGNORECASE):
        return "main"
    if re.search(r"\bon\s+(?:the\s+)?master\b", question, re.IGNORECASE):
        return "master"
    match = _GITHUB_BRANCH_RE.search(question)
    if not match:
        return ""
    value = match.group(1).rstrip(".,?!")
    return "" if value.lower() in _GITHUB_BRANCH_WORDS else value


def _github_decision(question: str) -> RouteDecision | None:
    """Route GitHub data questions; leave GitHub documentation to RAG."""
    from app.connectors import github

    path = _github_file_path(question)
    # A file path is not a repository name: drop it before repo parsing so
    # "read src/main.py" does not look like owner "src", repo "main.py".
    repo_text = question.replace(path, " ") if path else question
    explicit_repo = github.parse_repo(repo_text)
    mentions_github = bool(_GITHUB_MARKERS.search(question))
    repo_hint = bool(_GITHUB_REPO_HINT.search(question))
    repo_context = mentions_github or explicit_repo is not None or repo_hint
    has_default = bool(github.default_repo())
    repo = explicit_repo or github.default_repo()

    # "show me the spec" asks for data; "how do I write an OpenAPI spec?"
    # asks for documentation. The verb decides.
    asks_for_data = bool(_GITHUB_FILE_QUESTION.search(question)) or bool(
        re.search(r"\bendpoints?\b", question, re.IGNORECASE)
    )
    docs_question = bool(_GITHUB_DOCS_QUESTION.search(question))

    # ── Repository contents, in priority order ──
    if _GITHUB_SPEC_MARKERS.search(question) and repo_context and not (
        docs_question and not asks_for_data and not mentions_github and explicit_repo is None
    ):
        return RouteDecision(
            route="tool:github_spec",
            via="rule",
            args={"repo": repo, "path": path or ""},
        )

    if (repo_context and _GITHUB_COMMIT_MARKERS.search(question)) or (
        has_default and _GITHUB_COMMIT_STRONG.search(question)
    ):
        return RouteDecision(
            route="tool:github_commits",
            via="rule",
            args={"repo": repo, "path": path or "", "limit": 10},
        )

    if (
        (repo_context or has_default)
        and _GITHUB_CI_MARKERS.search(question)
        and _GITHUB_CI_QUERY.search(question)
    ):
        return RouteDecision(
            route="tool:github_ci",
            via="rule",
            args={"repo": repo, "branch": _github_branch(question)},
        )

    if path and (repo_context or asks_for_data or has_default):
        return RouteDecision(
            route="tool:github_file",
            via="rule",
            args={"repo": repo, "path": path},
        )

    # ── Issues / pull requests / repository summary ──
    # These keep the original strict rule: GitHub or owner/repo must be named,
    # so "what are the open issues in my repository?" is not hijacked.
    if not (mentions_github or explicit_repo):
        return None

    wants_issue = bool(_GITHUB_ISSUE_MARKERS.search(question))
    wants_pr = bool(_GITHUB_PR_MARKERS.search(question))
    wants_repo = bool(_GITHUB_REPO_MARKERS.search(question))
    if not (wants_issue or wants_pr or wants_repo or repo):
        # "What is GitHub Actions?" — documentation, not repository data.
        return None

    if _GITHUB_WRITE_MARKERS.search(question):
        action = (
            "comment_on_issue"
            if re.search(r"\bcomment", question, re.IGNORECASE)
            else "create_issue"
        )
        return RouteDecision(
            route="tool:github_write",
            via="rule",
            args={"repo": repo, "action": action},
        )
    if wants_issue:
        return RouteDecision(
            route="tool:github_issues",
            via="rule",
            args={"repo": repo, "state": _github_state(question), "limit": 10},
        )
    if wants_pr:
        return RouteDecision(
            route="tool:github_prs",
            via="rule",
            args={"repo": repo, "state": _github_state(question), "limit": 10},
        )
    if not repo and _GITHUB_LIST_REPOS.search(question):
        return RouteDecision(route="tool:github_repos", via="rule", args={"limit": 10})
    return RouteDecision(route="tool:github_repo", via="rule", args={"repo": repo})


def _sentry_period(question: str) -> str:
    """The time window a Sentry question asks about (default: 14 days)."""
    for pattern, period in _SENTRY_PERIODS:
        if pattern.search(question):
            return period
    return "14d"


def _sentry_decision(question: str) -> RouteDecision | None:
    """Route production-error questions to Sentry; docs stay in RAG."""
    from app.connectors import sentry

    mentions = bool(_SENTRY_MARKERS.search(question))
    if not mentions and not sentry.is_configured():
        # Without a token, only an explicit "sentry" question is worth
        # routing — it produces the actionable "add SENTRY_AUTH_TOKEN" error.
        return None

    # "What is Sentry?" is a documentation question. It stays in RAG unless
    # the user also asks about a concrete issue or endpoint.
    if _SENTRY_DEFINITION.search(question) and not (
        _SENTRY_ENDPOINT.search(question) or _SENTRY_ISSUE_ID.search(question)
    ):
        return None

    issue_id = _SENTRY_ISSUE_ID.search(question)
    endpoint_match = _SENTRY_ENDPOINT.search(question)
    endpoint = endpoint_match.group(0) if endpoint_match else ""
    period = _sentry_period(question)

    if issue_id and (mentions or _SENTRY_STACK.search(question)):
        return RouteDecision(
            route="tool:sentry_event",
            via="rule",
            args={"issue_id": issue_id.group(1)},
        )

    if mentions:
        if endpoint:
            return RouteDecision(
                route="tool:sentry_events",
                via="rule",
                args={"endpoint": endpoint, "period": period},
            )
        return RouteDecision(
            route="tool:sentry_issues",
            via="rule",
            args={"query": "is:unresolved", "period": period, "limit": 10},
        )

    # No "sentry" word: require a production failure plus either a concrete
    # endpoint or "latest/recent". "How do I fix production errors?" stays RAG.
    if _SENTRY_PROD.search(question) and (
        endpoint or _SENTRY_RECENT.search(question)
    ):
        if endpoint:
            return RouteDecision(
                route="tool:sentry_events",
                via="rule",
                args={"endpoint": endpoint, "period": period},
            )
        return RouteDecision(
            route="tool:sentry_issues",
            via="rule",
            args={
                "query": "is:unresolved level:error",
                "period": period,
                "limit": 10,
            },
        )

    return None


def _calendar_range(question: str) -> str:
    if re.search(r"\btoday\b", question, re.IGNORECASE):
        return "today"
    if re.search(r"\btomorrow\b", question, re.IGNORECASE):
        return "tomorrow"
    if re.search(r"\bnext week\b", question, re.IGNORECASE):
        return "next_week"
    if re.search(r"\b(?:this|next) month\b", question, re.IGNORECASE):
        return "month"
    return "week"


def _calendar_query(question: str) -> str:
    """The free-text part of a calendar question ("meeting with Ariel")."""
    match = re.search(
        r"\b(?:about|with|regarding|titled|called|named)\s+([^?.!,]{2,60})",
        question,
        re.IGNORECASE,
    )
    if not match:
        return ""
    phrase = re.split(
        r"\b(?:today|tomorrow|tonight|this week|next week|this month|on|at|in)\b",
        match.group(1),
        flags=re.IGNORECASE,
    )[0]
    phrase = phrase.strip(" .,?!'\"")
    if not phrase or phrase.lower() in _CAL_TIME_WORDS or len(phrase.split()) > 6:
        return ""
    return phrase


_GMAIL_STOPWORDS = {
    "the", "a", "an", "my", "me", "i", "you", "your", "any", "all", "for",
    "from", "to", "in", "on", "of", "and", "or", "is", "are", "was", "were",
    "do", "does", "did", "have", "has", "had", "show", "find", "search",
    "list", "check", "read", "latest", "recent", "email", "emails", "mail",
    "gmail", "inbox", "about", "regarding", "please", "can", "could",
    "would", "what", "which", "who", "when", "that", "this", "with",
}


def _gmail_query(question: str) -> str:
    """Turn a plain-English mail question into Gmail query syntax."""
    parts: list[str] = []

    address = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", question)
    if address:
        prefix = "to" if re.search(r"\b(?:to|sent to)\b", question, re.IGNORECASE) else "from"
        parts.append(f"{prefix}:{address.group(0)}")

    if re.search(r"\bunread\b", question, re.IGNORECASE):
        parts.append("is:unread")
    if re.search(r"\bhas:attachment\b|\bwith attachments?\b|\battached\b", question, re.IGNORECASE):
        parts.append("has:attachment")

    subject = re.search(
        r"\b(?:about|regarding|subject)\s+([^?.!,]{2,40})", question, re.IGNORECASE
    )
    if subject:
        phrase = subject.group(1).strip(" .,?!'\"")
        # "about the Stripe API" → "Stripe API": Gmail matches quoted text
        # literally, so a leading article would hide real results.
        phrase = re.sub(r"^(?:the|a|an)\s+", "", phrase, flags=re.IGNORECASE)
        if phrase:
            parts.append(f'"{phrase}"')

    if re.search(r"\btoday\b", question, re.IGNORECASE):
        parts.append("newer_than:1d")
    elif re.search(r"\bthis week\b", question, re.IGNORECASE):
        parts.append("newer_than:7d")
    elif re.search(r"\bthis month\b", question, re.IGNORECASE):
        parts.append("newer_than:30d")

    # Mailbox qualifier: "check my mail today" means the inbox, while
    # "sent"/"drafts" must stay precise. An explicit from:/to: filter or a
    # quoted subject is already narrow enough on its own.
    if re.search(r"\bsent\b", question, re.IGNORECASE):
        parts.append("in:sent")
    elif re.search(r"\bdrafts?\b", question, re.IGNORECASE):
        parts.append("in:drafts")
    elif (
        _GMAIL_MARKERS.search(question)
        and not any(part.startswith(("in:", "from:", "to:", '"')) for part in parts)
    ):
        parts.append("in:inbox")

    if not parts:
        words = [
            word
            for word in re.findall(r"[A-Za-z0-9@._-]{3,}", question)
            if word.lower() not in _GMAIL_STOPWORDS
        ]
        parts.append(" ".join(words[:6]) if words else "in:inbox")

    return " ".join(parts)


def _google_decision(question: str) -> RouteDecision | None:
    """Route personal Calendar/Gmail questions; keep API questions in RAG."""
    if _SETUP_QUESTION.search(question) or _API_PRODUCT.search(question):
        # "How do I integrate the Gmail API?" is documentation, not mail.
        return None

    is_calendar = bool(_CAL_MARKERS.search(question))
    is_gmail = bool(_GMAIL_MARKERS.search(question))
    if not (is_calendar or is_gmail):
        return None

    write = _GOOGLE_WRITE.search(question)
    # "send me the latest email" is a read request, not a send.
    if write and not re.match(r"send\s+me\b", write.group(0), re.IGNORECASE):
        action = "event" if is_calendar else "email"
        return RouteDecision(
            route="tool:google_write", via="rule", args={"action": action}
        )

    if is_calendar and _CAL_PERSONAL.search(question):
        query = _calendar_query(question)
        if query:
            return RouteDecision(
                route="tool:gcal_search",
                via="rule",
                args={"query": query, "range": _calendar_range(question)},
            )
        return RouteDecision(
            route="tool:gcal_events",
            via="rule",
            args={"range": _calendar_range(question)},
        )

    if (
        is_gmail
        and _GMAIL_INTENT.search(question)
        and _GMAIL_PERSONAL.search(question)
    ):
        return RouteDecision(
            route="tool:gmail_search",
            via="rule",
            args={"query": _gmail_query(question), "limit": 10},
        )

    return None


# ── NVIDIA classification (for everything the rules cannot settle) ───────────

_ROUTE_PROMPT = (
    "You are the router of an API debugging assistant. Classify the user question "
    "into exactly one of: 'rag' (anything about APIs, HTTP, status codes, auth, "
    "webhooks, documentation, or the assistant's own knowledge base), "
    "'tool:time' (current time in a city), "
    "'tool:epoch' (converting a unix timestamp to a date), 'tool:decode' "
    "(decoding a JWT/base64/URL-encoded string), 'tool:dns' (DNS records for a "
    "domain), 'tool:ip' (geolocation of an IP address), 'tool:github_issues' "
    "(list a repository's issues), 'tool:github_prs' (list a repository's pull "
    "requests), 'tool:github_repo' (details of one named repository), "
    "'tool:github_repos' (list the user's repositories), "
    "'tool:github_file' (read a named file from a repository), "
    "'tool:github_spec' (read a repository's OpenAPI/Swagger spec), "
    "'tool:github_commits' (recent commits in a repository), "
    "'tool:github_ci' (recent GitHub Actions/CI runs), "
    "'tool:sentry_issues' (the project's open Sentry issues), "
    "'tool:sentry_event' (the latest event of one Sentry issue), "
    "'tool:sentry_events' (Sentry events matching an endpoint URL), "
    "'tool:gcal_events' (the user's own calendar events), "
    "'tool:gcal_search' (search the user's own calendar), or "
    "'tool:gmail_search' (search the user's own Gmail). Questions ABOUT GitHub "
    "itself (what Actions are, how webhooks work) are 'rag', and so are "
    "questions about the Google APIs (scopes, OAuth, integration) and about "
    "error tracking in general (what a stack trace is). Answer with "
    'ONLY a JSON object: {"route": "..."}. No explanation.'
)


async def _nvidia_route(question: str) -> Route | None:
    """Ask NVIDIA for the route; any failure returns None (caller falls back)."""
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    model = os.getenv("NVIDIA_MODEL", "").strip().split(",")[0]
    if not api_key or not model:
        return None
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ROUTE_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0,
        "max_tokens": 20,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=_NVIDIA_TIMEOUT, trust_env=False) as client:
            response = await client.post(
                _NVIDIA_URL,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if response.status_code != 200:
            return None
        content = response.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        route = json.loads(match.group(0)).get("route")
        if route in ("rag",) + _TOOL_ROUTES:
            return route
        return None
    except Exception:
        return None


def _nvidia_args(route: str, question: str) -> dict:
    """Best-effort arguments for a tool route chosen by NVIDIA."""
    if route == "tool:time":
        return {"city": extract_city(question) or ""}
    if route == "tool:epoch":
        return {"value": extract_epoch(question) or ""}
    if route == "tool:decode":
        return {"payload": extract_decode_payload(question) or ""}
    if route == "tool:dns":
        return {"host": extract_domain(question) or ""}
    if route == "tool:ip":
        return {"ip": extract_ip(question) or ""}
    if route in ("tool:github_issues", "tool:github_prs", "tool:github_repo"):
        from app.connectors.github import parse_repo

        args: dict = {"repo": parse_repo(question) or ""}
        if route in ("tool:github_issues", "tool:github_prs"):
            args["state"] = _github_state(question)
            args["limit"] = 10
        return args
    if route == "tool:github_repos":
        return {"limit": 10}
    if route in (
        "tool:github_file",
        "tool:github_spec",
        "tool:github_commits",
        "tool:github_ci",
    ):
        from app.connectors.github import parse_repo

        path = _github_file_path(question)
        repo_text = question.replace(path, " ") if path else question
        args = {"repo": parse_repo(repo_text) or ""}
        if route == "tool:github_file":
            args["path"] = path or ""
        elif route == "tool:github_spec":
            args["path"] = path or ""
        elif route == "tool:github_commits":
            args["path"] = path or ""
            args["limit"] = 10
        else:
            args["branch"] = _github_branch(question)
        return args
    if route == "tool:github_write":
        return {
            "repo": "",
            "action": "comment_on_issue"
            if re.search(r"\bcomment", question, re.IGNORECASE)
            else "create_issue",
        }
    if route == "tool:sentry_issues":
        return {
            "query": "is:unresolved",
            "period": _sentry_period(question),
            "limit": 10,
        }
    if route == "tool:sentry_event":
        match = _SENTRY_ISSUE_ID.search(question)
        return {"issue_id": match.group(1) if match else ""}
    if route == "tool:sentry_events":
        match = _SENTRY_ENDPOINT.search(question)
        return {
            "endpoint": match.group(0) if match else "",
            "period": _sentry_period(question),
        }
    if route == "tool:gcal_events":
        return {"range": _calendar_range(question)}
    if route == "tool:gcal_search":
        return {
            "query": _calendar_query(question),
            "range": _calendar_range(question),
        }
    if route == "tool:gmail_search":
        return {"query": _gmail_query(question), "limit": 10}
    if route == "tool:google_write":
        return {"action": "event" if _CAL_MARKERS.search(question) else "email"}
    return {}


async def route_question(question: str) -> RouteDecision:
    """Decide where a question goes. Never raises; never blocks long."""
    # Order matters: specific tool intents first, generic API words last.
    if _DECODE_MARKERS.search(question):
        return RouteDecision(
            route="tool:decode", via="rule", args={"payload": extract_decode_payload(question) or ""}
        )
    # Educational DNS questions with no domain ("what does DNS mean?") must
    # still reach the knowledge base, so only route when a domain is present.
    if _DNS_MARKERS.search(question) and extract_domain(question):
        return RouteDecision(
            route="tool:dns", via="rule", args={"host": extract_domain(question)}
        )
    if _IP_PHRASE_MARKERS.search(question):
        return RouteDecision(
            route="tool:ip", via="rule", args={"ip": extract_ip(question) or ""}
        )
    # GitHub data questions must beat the generic "request"/"token" API words.
    github = _github_decision(question)
    if github is not None:
        return github
    # Calendar/Gmail data questions also beat the generic API words.
    google = _google_decision(question)
    if google is not None:
        return google
    # Production-error questions ("why did /api/orders fail in prod?") must
    # beat the generic "api"/"endpoint" words too.
    sentry_decision = _sentry_decision(question)
    if sentry_decision is not None:
        return sentry_decision
    if _API_MARKERS.search(question):
        return RouteDecision(route="rag", via="rule")

    if _TIME_MARKERS.search(question):
        return RouteDecision(
            route="tool:time", via="rule", args={"city": extract_city(question) or ""}
        )
    if _EPOCH_MARKERS.search(question):
        return RouteDecision(
            route="tool:epoch", via="rule", args={"value": extract_epoch(question) or ""}
        )
    bare_ip = extract_ip(question)
    if bare_ip:
        return RouteDecision(route="tool:ip", via="rule", args={"ip": bare_ip})

    # Ambiguous: let NVIDIA decide, defaulting to the knowledge base.
    routed = await _nvidia_route(question)
    if routed and routed in _TOOL_ROUTES:
        return RouteDecision(route=routed, via="nvidia", args=_nvidia_args(routed, question))
    return RouteDecision(route="rag", via="nvidia" if routed == "rag" else "fallback")
