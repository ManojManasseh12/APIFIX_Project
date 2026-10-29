import json
import sys
from pathlib import Path
from typing import Any

try:
    from .chunk_text import token_count
except ImportError:
    from chunk_text import token_count


def _read_spec(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        if path.suffix.lower() in {".yaml", ".yml"}:
            import yaml

            spec = yaml.safe_load(file)
        else:
            spec = json.load(file)
    if not isinstance(spec, dict):
        raise ValueError(f"{path} does not contain an OpenAPI object")
    return spec


def extract_schema_summary(schema: dict[str, Any]) -> str:
    """Summarizes request/response schema properties and required fields."""
    if not isinstance(schema, dict):
        return ""

    parts = []
    schema_type = schema.get("type", "object")
    parts.append(f"type: {schema_type}")

    required = schema.get("required", [])
    if required:
        parts.append(f"required: [{', '.join(required)}]")

    props = schema.get("properties", {})
    if props:
        prop_summaries = []
        for prop_name, prop_spec in list(props.items())[:8]:
            p_type = prop_spec.get("type", "any") if isinstance(prop_spec, dict) else "any"
            prop_summaries.append(f"{prop_name} ({p_type})")
        parts.append(f"fields: {', '.join(prop_summaries)}")

    return "; ".join(parts)


def operation_text(method: str, path: str, op: dict[str, Any], spec_title: str) -> str:
    """Formats an OpenAPI operation into dense, citable documentation prose."""
    lines = [f"{method.upper()} {path} ({spec_title})"]

    if op.get("summary"):
        lines.append(f"Summary: {op['summary']}")
    if op.get("description"):
        lines.append(f"Description: {op['description']}")

    # Security
    sec = op.get("security")
    if sec:
        sec_names = []
        for s in sec:
            if isinstance(s, dict):
                sec_names.extend(s.keys())
        if sec_names:
            lines.append(f"Security: {', '.join(sec_names)}")

    # Parameters
    params = op.get("parameters", [])
    if params:
        p_lines = []
        for p in params:
            if isinstance(p, dict):
                req = "required" if p.get("required") else "optional"
                p_lines.append(f"- {p.get('name')} in {p.get('in')} ({req}): {p.get('description', '')}")
        if p_lines:
            lines.append("Parameters:\n" + "\n".join(p_lines[:5]))

    # Request Body
    body = op.get("requestBody")
    if isinstance(body, dict):
        content = body.get("content", {})
        for content_type, details in content.items():
            schema = details.get("schema", {})
            schema_sum = extract_schema_summary(schema)
            req_flag = "required" if body.get("required") else "optional"
            lines.append(f"Request Body ({content_type}, {req_flag}): {schema_sum}")

    # Responses
    responses = op.get("responses", {})
    if responses:
        r_lines = []
        for code, resp in list(responses.items())[:5]:
            desc = resp.get("description", "") if isinstance(resp, dict) else str(resp)
            r_lines.append(f"- {code}: {desc}")
        lines.append("Responses:\n" + "\n".join(r_lines))

    return "\n".join(lines)


def parse_openapi(spec_path: str, license_name: str = "Apache-2.0") -> list[dict[str, Any]]:
    path_obj = Path(spec_path)
    spec = _read_spec(path_obj)

    info = spec.get("info", {})
    title = info.get("title", path_obj.stem)
    version = info.get("version", "1.0.0")

    chunks = []
    paths = spec.get("paths", {})

    for path_str, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method_str, op in methods.items():
            method_lower = method_str.lower()
            if method_lower not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue

            op_id = op.get("operationId") or f"{method_lower}_{path_str.replace('/', '_').strip('_')}"
            chunk_id = f"KB-API-{op_id[:24].upper()}"
            text = operation_text(method_str, path_str, op, title)
            cnt = token_count(text)

            server_url = spec.get("servers", [{}])[0].get("url", "")
            if server_url.startswith("/"):
                base_url = "https://petstore3.swagger.io" + server_url
            elif server_url.startswith("http://") or server_url.startswith("https://"):
                base_url = server_url
            else:
                base_url = "https://petstore3.swagger.io"
            full_url = base_url.rstrip("/") + "/" + path_str.lstrip("/")

            chunk = {
                "chunk_id": chunk_id,
                "source_type": "openapi_spec",
                "source_url": full_url,
                "source_title": f"{title} - {method_str.upper()} {path_str}",
                "api_name": title,
                "api_version": version,
                "method": method_str.upper(),
                "path": path_str,
                "section": f"operation: {op_id}",
                "page": "",
                "license": license_name,
                "retrieved_at": "2026-10-29",
                "token_count": cnt,
                "chunk_text": text,
            }
            chunks.append(chunk)

    return chunks


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_openapi.py <path_to_openapi.json>")
        sys.exit(1)
    results = parse_openapi(sys.argv[1])
    print(f"Parsed {len(results)} operation chunks.")
    for c in results[:3]:
        print(f"[{c['chunk_id']}] {c['source_title']} ({c['token_count']} tokens)")
