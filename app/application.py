import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import config
from app.database import init_database
from app.repositories import completeness as completeness_repository
from app.routers import ai, completeness, drawings, pages, settings, translations


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_database()
    completeness_repository.recover_interrupted_runs()
    app = FastAPI(title="工程数字图纸智能管理系统")
    app.mount("/uploads", StaticFiles(directory=str(config.UPLOAD_DIR)), name="uploads")
    app.include_router(pages.router)
    app.include_router(drawings.router)
    app.include_router(ai.router)
    app.include_router(settings.router)
    app.include_router(translations.router)
    app.include_router(completeness.router)
    return app
