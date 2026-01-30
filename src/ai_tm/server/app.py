from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import build_router
from .webui import mount_webui


def create_app(workspace: str, enable_webui: bool = True) -> FastAPI:
    app = FastAPI(title="box_ai_tm", version="0.1.0")

    # 本地工具：允许任意 origin，方便原生 H5 直连
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(build_router(workspace_root=workspace))

    if enable_webui:
        mount_webui(app)

    return app
