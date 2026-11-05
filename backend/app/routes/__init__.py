import json


def sse(event: str, data: dict) -> str:
    """Format one server-sent event for the /api/diagnose and /api/ask streams."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
