from __future__ import annotations

from fastapi import APIRouter

from .models import Health, WorkspaceInfo
from .workspace import resolve_workspace


def build_router(workspace_root: str) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/health", response_model=Health)
    def health():
        return Health(ok=True)

    @r.get("/workspace", response_model=WorkspaceInfo)
    def workspace():
        ws = resolve_workspace(workspace_root)
        return WorkspaceInfo(
            workspace=str(ws.root),
            config_exists=ws.config_path.exists(),
            config_path=str(ws.config_path),
            targets_exist=ws.targets_path.exists(),
            targets_path=str(ws.targets_path),
        )

    return r
