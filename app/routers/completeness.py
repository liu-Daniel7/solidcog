"""Routes for the standalone completeness-check workflow."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import config
from app.security import verify_local_request
from app.services import completeness
from app.services.completeness_rules import (
    BOM_COLUMN_CATALOG,
    TITLE_FIELD_CATALOG,
    default_rules,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(config.TEMPLATE_DIR))


class RulesRequest(BaseModel):
    required_title_fields: list[str] = Field(default_factory=list, max_length=30)
    bom_required_columns: list[str] = Field(default_factory=list, max_length=30)
    dimension_check: bool = True


class BatchCreateRequest(BaseModel):
    name: str = Field(default="", max_length=160)
    drawing_ids: list[int] = Field(
        min_length=1, max_length=config.MAX_COMPLETENESS_DRAWINGS
    )
    start_drawing_ids: list[int] = Field(
        default_factory=list, max_length=config.MAX_COMPLETENESS_DRAWINGS
    )
    rules: RulesRequest | None = None


class ReviewRequest(BaseModel):
    confirmed_status: str = Field(pattern="^(unconfirmed|confirmed|dismissed)$")
    note: str = Field(default="", max_length=500)


def _rules_from_form(form) -> dict:
    return {
        "required_title_fields": form.getlist("required_title_fields"),
        "bom_required_columns": form.getlist("bom_required_columns"),
        "dimension_check": form.get("dimension_check") == "on",
    }


def _redirect_error(path: str, message: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return RedirectResponse(
        f"{path}{separator}error={quote(str(message)[:500])}", status_code=303
    )


def _check_errors(exc: Exception):
    if isinstance(exc, KeyError):
        return HTTPException(404, "未找到检查批次或结果")
    if isinstance(exc, completeness.BatchRunningError):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    if isinstance(exc, completeness.CompletenessRunError):
        return HTTPException(500, str(exc))
    return HTTPException(500, "检查操作失败")


@router.get("/completeness")
def completeness_home(request: Request, error: str = "", message: str = ""):
    context = {
        "active_page": "completeness",
        "drawings": completeness.drawing_options(),
        "batches": completeness.list_batches_payload()["batches"],
        "default_rules": default_rules(),
        "title_catalog": TITLE_FIELD_CATALOG,
        "bom_catalog": BOM_COLUMN_CATALOG,
        "status_labels": completeness.STATUS_LABELS,
        "batch_status_labels": completeness.BATCH_STATUS_LABELS,
        "error": error,
        "message": message,
    }
    return templates.TemplateResponse(request, "completeness.html", context)


@router.get("/completeness/batches/{batch_id}")
def completeness_batch_page(request: Request, batch_id: int, error: str = ""):
    try:
        payload = completeness.batch_payload(batch_id)
    except KeyError:
        raise HTTPException(404, "未找到检查批次")
    return templates.TemplateResponse(
        request,
        "completeness.html",
        {
            **payload,
            "active_page": "completeness",
            "title_catalog": TITLE_FIELD_CATALOG,
            "bom_catalog": BOM_COLUMN_CATALOG,
            "error": error,
            "message": "",
        },
    )


@router.post(
    "/completeness/batches", dependencies=[Depends(verify_local_request)]
)
async def create_batch_form(request: Request):
    form = await request.form()
    try:
        batch_id = completeness.create_batch(
            str(form.get("name") or ""),
            form.getlist("drawing_ids"),
            form.getlist("start_drawing_ids"),
            _rules_from_form(form),
        )
    except ValueError as exc:
        return _redirect_error("/completeness", str(exc))
    return RedirectResponse(f"/completeness/batches/{batch_id}", status_code=303)


@router.post(
    "/completeness/batches/{batch_id}/start",
    dependencies=[Depends(verify_local_request)],
)
def start_batch_form(batch_id: int):
    try:
        completeness.run_batch(batch_id)
    except Exception as exc:
        return _redirect_error(f"/completeness/batches/{batch_id}", str(exc))
    return RedirectResponse(f"/completeness/batches/{batch_id}", status_code=303)


@router.post(
    "/completeness/batches/{batch_id}/rules",
    dependencies=[Depends(verify_local_request)],
)
async def update_rules_form(request: Request, batch_id: int):
    form = await request.form()
    try:
        completeness.update_rules(batch_id, _rules_from_form(form))
    except Exception as exc:
        return _redirect_error(f"/completeness/batches/{batch_id}", str(exc))
    return RedirectResponse(
        f"/completeness/batches/{batch_id}?message={quote('规则已保存，请重新检查以应用')}",
        status_code=303,
    )


@router.post(
    "/completeness/batches/{batch_id}/delete",
    dependencies=[Depends(verify_local_request)],
)
def delete_batch_form(batch_id: int):
    try:
        completeness.delete_batch(batch_id)
    except Exception as exc:
        return _redirect_error(f"/completeness/batches/{batch_id}", str(exc))
    return RedirectResponse("/completeness", status_code=303)


@router.get(
    "/completeness/batches/{batch_id}/report.csv",
    dependencies=[Depends(verify_local_request)],
)
def download_report(batch_id: int):
    try:
        content, filename = completeness.export_csv(batch_id)
    except KeyError:
        raise HTTPException(404, "未找到检查批次")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": completeness.content_disposition(
                filename, f"completeness-batch-{batch_id}.csv"
            )
        },
    )


@router.get(
    "/completeness/results/{result_id}/image",
    dependencies=[Depends(verify_local_request)],
)
def result_image(result_id: int):
    try:
        path = completeness.result_image(result_id)
    except KeyError:
        raise HTTPException(404, "未找到检查结果")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=600"},
    )


@router.get("/api/completeness/batches")
def api_list_batches():
    return completeness.list_batches_payload()


@router.post(
    "/api/completeness/batches", dependencies=[Depends(verify_local_request)]
)
def api_create_batch(request: BatchCreateRequest):
    try:
        batch_id = completeness.create_batch(
            request.name,
            request.drawing_ids,
            request.start_drawing_ids,
            request.rules.model_dump() if request.rules else None,
        )
    except Exception as exc:
        raise _check_errors(exc)
    return {"batch": completeness.batch_payload(batch_id), "id": batch_id}


@router.get("/api/completeness/batches/{batch_id}")
def api_get_batch(batch_id: int):
    try:
        return completeness.batch_payload(batch_id)
    except KeyError:
        raise HTTPException(404, "未找到检查批次")


@router.post(
    "/api/completeness/batches/{batch_id}/start",
    dependencies=[Depends(verify_local_request)],
)
def api_start_batch(batch_id: int):
    try:
        return completeness.run_batch(batch_id)
    except Exception as exc:
        raise _check_errors(exc)


@router.post(
    "/api/completeness/batches/{batch_id}/rules",
    dependencies=[Depends(verify_local_request)],
)
def api_update_rules(batch_id: int, request: RulesRequest):
    try:
        version = completeness.update_rules(batch_id, request.model_dump())
    except Exception as exc:
        raise _check_errors(exc)
    return {"success": True, "rules_version": version}


@router.delete(
    "/api/completeness/batches/{batch_id}",
    dependencies=[Depends(verify_local_request)],
)
def api_delete_batch(batch_id: int):
    try:
        completeness.delete_batch(batch_id)
    except Exception as exc:
        raise _check_errors(exc)
    return {"success": True}


@router.get("/api/completeness/results/{result_id}")
def api_get_result(result_id: int):
    try:
        from app.repositories import completeness as repository

        result = repository.get_result(result_id)
    except Exception:
        result = None
    if not result:
        raise HTTPException(404, "未找到检查结果")
    result["status_label"] = completeness.STATUS_LABELS.get(
        result["status"], result["status"]
    )
    result["kind_label"] = completeness.KIND_LABELS.get(
        result["kind"], result["kind"]
    )
    result["confirmation_label"] = completeness.CONFIRMATION_LABELS.get(
        result.get("confirmed_status"), ""
    )
    result["location"] = completeness.result_location(result)
    result["image_url"] = f"/completeness/results/{result_id}/image"
    if result.get("source_drawing_id"):
        result["ocr_url"] = f"/view-ocr/{result['source_drawing_id']}"
    else:
        result["ocr_url"] = ""
    return result


@router.post(
    "/api/completeness/results/{result_id}/review",
    dependencies=[Depends(verify_local_request)],
)
def api_review_result(result_id: int, request: ReviewRequest):
    try:
        return completeness.review_result(
            result_id, request.confirmed_status, request.note
        )
    except KeyError:
        raise HTTPException(404, "未找到检查结果")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
