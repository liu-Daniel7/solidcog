from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.security import verify_local_request
from app.services import translation

router = APIRouter()


class TranslateRequest(BaseModel):
    target_lang: str = Field(default="en", max_length=16)
    force: bool = False
    sections: list[str] | None = None


@router.get("/drawings/{drawing_id}/translations")
def get_translations(drawing_id: int, target_lang: str = "en"):
    return translation.get_translation_payload(drawing_id, target_lang)


@router.post("/drawings/{drawing_id}/translations", dependencies=[Depends(verify_local_request)])
def create_translations(drawing_id: int, request: TranslateRequest):
    return translation.translate_drawing(
        drawing_id,
        target_lang=request.target_lang,
        force=request.force,
        sections=request.sections,
    )


@router.get("/drawings/{drawing_id}/translations.csv")
def download_translations(drawing_id: int, target_lang: str = "en"):
    content, filename = translation.export_csv(drawing_id, target_lang)
    encoded = quote(filename, safe="")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"drawing-{drawing_id}-{target_lang}.csv\"; "
                f"filename*=UTF-8''{encoded}"
            )
        },
    )
