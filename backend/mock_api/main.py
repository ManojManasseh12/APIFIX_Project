import asyncio
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

app = FastAPI(title="APIFix Mock API")

STATE = {
    "modes": {},
    "search_calls": 0,
    "rate_after": 100,
    "orders": {},
}

TOKENS = {
    "valid-user-token": {"role": "user", "scopes": ["read:profile"]},
    "admin-token": {"role": "admin", "scopes": ["read:admin", "read:profile"]},
    "reports-token": {"role": "user", "scopes": []},
    "reports-read-token": {"role": "user", "scopes": ["read:reports"]},
    "suspended-token": {"role": "suspended", "scopes": []},
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def err(status: int, code: str, headers: dict | None = None):
    return JSONResponse({"error": code}, status_code=status, headers=headers or {})


def auth(request: Request):
    raw = request.headers.get("authorization", "")
    if not raw:
        return None, err(401, "missing_credentials")

    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer":
        return None, err(401, "wrong_scheme")

    token = token.strip()
    if token.endswith("\\n") or "\u200b" in token:
        return None, err(401, "malformed_token")
    if token == "expired-token":
        return None, err(401, "token_expired")
    if token == "bad-signature-token":
        return None, err(401, "invalid_signature")
    if token not in TOKENS:
        return None, err(401, "invalid_token")
    return TOKENS[token], None


# ---------------------------------------------------------------- auth routes


@app.get("/v1/profile")
async def profile(request: Request):
    user, error = auth(request)
    if error:
        return error
    if request.query_params.get("leak") == "key":
        return {"id": 42, "api_key": "sk_live_FAKE_DO_NOT_USE_123456"}
    return {"id": 42, "name": "Test User"}


@app.post("/v1/profile")
async def profile_post():
    return JSONResponse(
        {"error": "method_not_allowed"},
        status_code=405,
        headers={"Allow": "GET"},
    )


@app.get("/v1/admin/users")
async def admin_users(request: Request):
    user, error = auth(request)
    if error:
        return error
    if user["role"] != "admin":
        return err(403, "insufficient_permissions")
    return {"users": []}


@app.get("/v1/reports")
async def reports(request: Request):
    user, error = auth(request)
    if error:
        return error
    if "read:reports" not in user["scopes"]:
        return err(403, "missing_scope")

    mode = STATE["modes"].get("/v1/reports")
    if mode == "500":
        return err(500, "internal_error")
    if mode == "503":
        return err(503, "temporarily_unavailable", {"Retry-After": "30"})
    if mode == "502_html":
        return Response("<html><body>Bad Gateway</body></html>", status_code=502, media_type="text/html")
    return {"reports": []}


@app.delete("/v1/reports")
async def reports_delete():
    return JSONResponse(
        {"error": "method_not_allowed"},
        status_code=405,
        headers={"Allow": "GET"},
    )


# -------------------------------------------------------------- order routes


@app.post("/v1/orders")
async def create_order(request: Request):
    user, error = auth(request)
    if error:
        return error
    if not request.headers.get("content-type", "").startswith("application/json"):
        return err(415, "unsupported_media_type")

    raw = await request.body()
    if len(raw) > 1024:
        return err(413, "payload_too_large")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return err(400, "malformed_json")

    items = payload.get("items")
    if not isinstance(items, list):
        return err(422, "missing_items")
    for item in items:
        quantity = item.get("quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            return err(422, "quantity_must_be_integer")
        if quantity < 1:
            return err(422, "quantity_min_1")
    status = payload.get("status")
    if status is not None and status not in {"pending", "paid"}:
        return err(422, "invalid_status")

    order_id = str(len(STATE["orders"]) + 1)
    STATE["orders"][order_id] = payload
    return JSONResponse({"id": order_id, **payload}, status_code=201)


@app.get("/v1/orders")
async def list_orders(page: int = 1, limit: int = 20):
    if limit > 100:
        return err(400, "limit_exceeded")
    return {"page": page, "limit": limit, "items": list(STATE["orders"].values())}


@app.get("/v1/orders/42/items")
async def old_order_items():
    return err(410, "resource_gone")


@app.get("/v1/orders/42/line-items")
async def new_order_items():
    return {"items": []}


@app.get("/v1/orders/{order_id}")
async def get_order(order_id: str, request: Request):
    user, error = auth(request)
    if error:
        return error
    if order_id == "42":
        return {"id": 42, "status": "pending"}
    return err(404, "not_found")


@app.put("/v1/orders/{order_id}")
async def put_order(order_id: str):
    return JSONResponse(
        {"error": "method_not_allowed"},
        status_code=405,
        headers={"Allow": "PATCH"},
    )


@app.patch("/v1/orders/{order_id}")
async def patch_order(order_id: str, request: Request):
    user, error = auth(request)
    if error:
        return error
    return {"id": order_id, "updated": True}


@app.delete("/v1/orders/{order_id}")
async def delete_order(order_id: str, request: Request):
    user, error = auth(request)
    if error:
        return error
    if user["role"] == "suspended":
        return err(403, "account_suspended")
    return Response(status_code=204)


# ---------------------------------------------------- slow / fault endpoints


@app.post("/v1/slow-orders")
async def slow_orders(request: Request):
    user, error = auth(request)
    if error:
        return error
    await asyncio.sleep(15)
    order_id = str(len(STATE["orders"]) + 1)
    STATE["orders"][order_id] = {"slow": True}
    return JSONResponse({"id": order_id}, status_code=201)


@app.get("/v1/slow-report")
async def slow_report():
    await asyncio.sleep(15)
    return {"report": "done"}


@app.get("/v1/search")
async def search():
    STATE["search_calls"] += 1
    if STATE["search_calls"] > STATE["rate_after"]:
        return err(429, "rate_limited", {"Retry-After": "30"})
    return {"results": []}


@app.get("/v1/x")
async def x_endpoint():
    return JSONResponse(
        {"error": "internal_error", "code": "X-ERR-7731"},
        status_code=500,
        headers={"X-Error-Code": "X-ERR-7731"},
    )


@app.get("/v1/redirect-https")
async def redirect_https():
    return RedirectResponse("https://api.example.test/v1/profile", status_code=301)


@app.get("/v2/profile")
async def v2_profile():
    return err(404, "not_found")


@app.get("/v1/products")
async def products():
    return {"products": []}


@app.post("/v1/users")
async def create_user(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return err(400, "malformed_json")
    email = payload.get("email")
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return err(422, "invalid_email")
    return JSONResponse({"id": "u1", "email": email}, status_code=201)


# ---------------------------------------------------------- control endpoints


@app.post("/__control")
async def control(payload: dict):
    if "path" in payload:
        STATE["modes"][payload["path"]] = payload.get("mode")
    if "rate_after" in payload:
        STATE["rate_after"] = int(payload["rate_after"])
    return {"ok": True}


@app.post("/__reset")
async def reset():
    STATE["modes"].clear()
    STATE["search_calls"] = 0
    STATE["rate_after"] = 100
    STATE["orders"].clear()
    return {"ok": True}
