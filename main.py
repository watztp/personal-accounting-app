from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from auth_demo.session import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session,
    expected_credential,
    read_session,
)
from backend.app.db.db_categories import CategoryIdentity, get_or_create_category
from backend.app.db.db_items import (
    find_item_by_id,
    find_items_for_manual_assignment,
    find_items_needing_review,
    set_item_category,
)
from backend.app.db.sqlite_db import ensure_schema, get_connection
from backend.app.engine.read_payloads import dashboard, drilldown, drilldown_filters
from category_helper.utils.category_search_helper import load_categories
from category_helper.utils.category_search_helper import DEFAULT_CATEGORIES_PATH
from orchestra.workflow import ApplicationOrchestrator, WORKER_COUNT


PROJECT_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = PROJECT_ROOT / "frontend_receipt_model" / "template"
AUTH_DEMO_DIR = PROJECT_ROOT / "auth_demo"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
orchestrator = ApplicationOrchestrator()


class LoginBody(BaseModel):
    user_id: int
    credential: str


class ReviewCompleteBody(BaseModel):
    worker_encrypt_id: str
    reviewed_payload: dict[str, Any]
    create_item_rows: bool = True
    user_id: int | None = None


class ReviewCancelBody(BaseModel):
    worker_encrypt_id: str
    reason: str = "review canceled by user"


class CategoryAssignmentBody(BaseModel):
    category: str
    subcategory: str


def _ensure_demo_user(user_id: int) -> None:
    if user_id <= 0 or user_id > 2_147_483_647:
        raise ValueError("user_id must be between 1 and 2147483647")
    with get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, login_email, sso_id) VALUES (?, ?, ?)",
            (user_id, f"demo-{user_id}@local", f"demo-user-{user_id}"),
        )
        conn.commit()


def _set_login_cookie(response, user_id: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        create_session(user_id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def _authenticate(user_id: int, credential: str) -> None:
    if not hmac.compare_digest(credential, expected_credential()):
        raise ValueError("invalid credential")
    _ensure_demo_user(user_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await orchestrator.start()
    try:
        yield
    finally:
        await orchestrator.stop()


app = FastAPI(title="Accounting App Public", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def session_auth(request: Request, call_next):
    public_paths = {"/health", "/login", "/api/login", "/favicon.ico"}
    if request.url.path not in public_paths:
        user_id = read_session(request.cookies.get(COOKIE_NAME))
        if user_id is None:
            if request.url.path.startswith("/api/"):
                return JSONResponse(status_code=401, content={"detail": "login required"})
            return RedirectResponse("/login", status_code=303)
        request.state.user_id = user_id
    return await call_next(request)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "queue_size": orchestrator.queue.qsize(),
        "workers": WORKER_COUNT,
        "category_worker": orchestrator.category_watcher.status(),
    }


@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse(AUTH_DEMO_DIR / "login.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.post("/login")
def login_form(user_id: int = Form(...), credential: str = Form(...)):
    try:
        _authenticate(user_id, credential)
    except ValueError:
        return RedirectResponse("/login?error=1", status_code=303)
    response = RedirectResponse("/", status_code=303)
    _set_login_cookie(response, user_id)
    return response


@app.post("/api/login")
def login_api(body: LoginBody) -> JSONResponse:
    try:
        _authenticate(body.user_id, body.credential)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response = JSONResponse({"ok": True, "user_id": body.user_id})
    _set_login_cookie(response, body.user_id)
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/api/identity")
def identity(request: Request) -> dict[str, Any]:
    return {"user_id": request.state.user_id, "source": "local-demo-session"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "index.html")


@app.get("/drilldown")
@app.get("/drilldown.html")
def drilldown_page() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "drilldown.html")


@app.get("/review.html")
def review_page() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "review.html")


@app.get("/manage-category")
@app.get("/manage-category.html")
def manage_category_page() -> FileResponse:
    return FileResponse(TEMPLATE_DIR / "manage-category.html")


@app.post("/api/upload-receipt", status_code=202)
@app.post("/api/receipt/upload", status_code=202)
async def upload_receipt(request: Request, file: UploadFile | None = File(None), image: UploadFile | None = File(None)):
    upload = file or image
    if upload is None:
        raise HTTPException(status_code=400, detail="file or image is required")
    if upload.content_type and not upload.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="image file required")
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="image must be between 1 byte and 15 MB")
    try:
        return await orchestrator.submit(
            user_id=request.state.user_id,
            data=data,
            filename=upload.filename,
            content_type=upload.content_type,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@app.get("/api/jobs/{token}")
def job_status(token: str, request: Request):
    result = orchestrator.status(token=token, user_id=request.state.user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    return result


@app.get("/api/review/data")
def review_data(token: str, request: Request):
    result = orchestrator.review_data(token=token, user_id=request.state.user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="review not found")
    result["user_id"] = request.state.user_id
    result["image_url"] = f"/api/review/image/{token}"
    return result


@app.get("/api/review/image/{token}")
def review_image(token: str, request: Request):
    path = orchestrator.image_path(token=token, user_id=request.state.user_id)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(path)


@app.post("/api/review/complete")
async def review_complete(body: ReviewCompleteBody, request: Request):
    try:
        result = await orchestrator.complete(
            token=body.worker_encrypt_id,
            user_id=request.state.user_id,
            reviewed_payload=body.reviewed_payload,
            create_item_rows=body.create_item_rows,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="review not found or no longer editable")
    return {"status": "finalized", "db_result": result}


@app.post("/api/review/cancel")
def review_cancel(body: ReviewCancelBody, request: Request):
    if not orchestrator.cancel(token=body.worker_encrypt_id, user_id=request.state.user_id, reason=body.reason):
        raise HTTPException(status_code=404, detail="review not found or no longer editable")
    return {"status": "canceled"}


@app.get("/api/categories/manage")
def categories_for_manual_review() -> dict[str, Any]:
    worker_status = orchestrator.category_watcher.status()
    manual_mode = orchestrator.category_watcher.manual_assignment_enabled()
    with get_connection() as conn:
        ensure_schema(conn)
        items = (
            find_items_for_manual_assignment(conn)
            if manual_mode
            else find_items_needing_review(conn)
        )
    return {
        "items": items,
        "categories": load_categories(DEFAULT_CATEGORIES_PATH),
        "mode": "manual" if manual_mode else "review_only",
        "worker": {
            "state": worker_status["state"],
            "message": worker_status["manual_reason"],
            "restart_required": worker_status["restart_required"],
        },
    }


@app.post("/api/categories/items/{item_id}")
def assign_item_category(item_id: int, body: CategoryAssignmentBody) -> dict[str, Any]:
    category = body.category.strip()
    subcategory = body.subcategory.strip()
    category_tree = load_categories(DEFAULT_CATEGORIES_PATH)
    allowed_subcategories = (category_tree.get(category) or {}).get("sub_categories") or []
    if not category or subcategory not in allowed_subcategories:
        raise HTTPException(status_code=400, detail="invalid category or subcategory")

    with get_connection() as conn:
        ensure_schema(conn)
        item = find_item_by_id(conn, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        can_assign_unreviewed = (
            orchestrator.category_watcher.manual_assignment_enabled()
            and item["category_id"] is None
        )
        if not item["needs_review"] and not can_assign_unreviewed:
            raise HTTPException(status_code=409, detail="item is not available for manual assignment")

        category_row = get_or_create_category(
            conn,
            CategoryIdentity(category_name=category, sub_category_name=subcategory),
        )
        if not set_item_category(
            conn,
            item_id=item_id,
            category_id=category_row["category_id"],
            needs_review=False,
        ):
            raise HTTPException(status_code=409, detail="item could not be updated")

    return {
        "item_id": item_id,
        "category_id": category_row["category_id"],
        "category": category,
        "subcategory": subcategory,
        "needs_review": False,
    }


@app.post("/api/payloads")
async def payloads(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid request body")
    payload_type = body.get("payload_type", "dashboard")
    user_id = request.state.user_id
    try:
        if payload_type == "dashboard":
            return await asyncio.to_thread(
                dashboard, user_id=user_id, date_from=body.get("date_from"), date_to=body.get("date_to")
            )
        if payload_type == "user_drilldown":
            return await asyncio.to_thread(
                drilldown,
                user_id=user_id,
                date_from=body.get("date_from"), date_to=body.get("date_to"), year=body.get("year"),
                month=body.get("month"), category=body.get("category"), sub_category=body.get("sub_category"),
                limit=body.get("limit", 2000),
            )
        if payload_type == "user_drilldown_filters":
            return await asyncio.to_thread(drilldown_filters, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="unsupported payload_type")


@app.post("/api/dashboard")
async def dashboard_compat(request: Request):
    body = await request.json()
    return await asyncio.to_thread(
        dashboard,
        user_id=request.state.user_id,
        date_from=body.get("date_from"),
        date_to=body.get("date_to"),
    )


@app.post("/api/user-drilldown/fetch")
async def drilldown_compat(request: Request):
    body = await request.json()
    allowed = {"date_from", "date_to", "year", "month", "category", "sub_category", "limit"}
    kwargs = {key: value for key, value in body.items() if key in allowed}
    return await asyncio.to_thread(drilldown, user_id=request.state.user_id, **kwargs)


@app.post("/api/user-drilldown/filters")
async def filters_compat(request: Request):
    return await asyncio.to_thread(drilldown_filters, user_id=request.state.user_id)
