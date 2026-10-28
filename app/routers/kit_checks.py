from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from app.schemas import KitCheckCreateRequest, KitCheckItemUpdateRequest
from app.security import verify_local_request
from app.services import kit_checks as service


router = APIRouter()


@router.post("/kit-checks", dependencies=[Depends(verify_local_request)])
def create_kit_check(payload: KitCheckCreateRequest):
    result = service.create_check(
        drawing_ids=payload.drawing_ids,
        mode=payload.mode,
        root_ids=payload.root_drawing_ids,
    )
    return JSONResponse(result, status_code=202)


@router.get("/kit-checks/recent")
def recent_kit_checks():
    from app.repositories import kit_checks as repository

    return {"checks": repository.list_recent(8)}


@router.get("/kit-checks/{check_id}")
def get_kit_check(check_id: int):
    return service.build_payload(check_id)


@router.post("/kit-checks/{check_id}/retry", dependencies=[Depends(verify_local_request)])
def retry_kit_check(check_id: int):
    return service.retry_check(check_id)


@router.patch("/kit-checks/{check_id}/items/{item_id}", dependencies=[Depends(verify_local_request)])
def update_kit_check_item(check_id: int, item_id: int, payload: KitCheckItemUpdateRequest):
    from app.repositories import kit_checks as repository

    item = repository.get_item(item_id)
    if not item or int(item.get("check_id") or 0) != int(check_id):
        raise HTTPException(404, "检查条目不存在")
    return service.update_item(item_id, payload.model_dump())


@router.get("/kit-checks/{check_id}/export.csv")
def export_kit_check(check_id: int):
    payload = service.build_payload(check_id)
    content = service.export_csv(check_id)
    filename = f"kit-check-{payload['id']}.csv"
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
