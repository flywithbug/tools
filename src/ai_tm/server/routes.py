from __future__ import annotations

import time
import uuid
from fastapi import APIRouter, HTTPException, Query

from .models import (
    AnalyzeResponse,
    FixRequest,
    FixResponse,
    HealthResponse,
    I18nFormat,
    TaskRef,
    TaskStatus,
    TaskStatusResponse,
    TranslateRequest,
    TranslateResponse,
    WorkspaceInfoResponse,
    WorkspacePaths,
)
from .workspace import resolve_workspace, detect_formats


# 轻量任务表（内存）：先占位，后面再抽到 tasks.py
_TASKS: dict[str, TaskStatusResponse] = {}


def build_router(workspace_root: str) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/health", response_model=HealthResponse)
    def health():
        return HealthResponse(ok=True)

    @r.get("/workspace", response_model=WorkspaceInfoResponse)
    def workspace():
        ws = resolve_workspace(workspace_root)
        formats = detect_formats(ws)

        # stub：先不解析 yaml
        base_locale = "en" if formats else None

        return WorkspaceInfoResponse(
            paths=WorkspacePaths(
                workspace=str(ws.root),
                config_path=str(ws.config_path),
                targets_path=str(ws.targets_path),
            ),
            config_exists=ws.config_path.exists(),
            config_valid=ws.config_path.exists(),  # 先粗略：存在当作 valid；后面会做校验
            formats=formats,
            default_format=formats[0].format if len(formats) == 1 else None,
            base_locale=base_locale,
            locales=[],
            core_locales=[],
            noncore_locales=[],
        )

    @r.get("/analyze", response_model=AnalyzeResponse)
    def analyze(format: I18nFormat = Query(...)):
        # stub：先返回空摘要
        return AnalyzeResponse(format=format)

    @r.post("/fix", response_model=FixResponse)
    def fix(req: FixRequest):
        # stub：不落盘，仅返回 ok
        return FixResponse(ok=True, format=req.format, results=[], message="stub: fix not implemented yet")

    @r.post("/translate", response_model=TranslateResponse)
    def translate(req: TranslateRequest):
        # stub：创建一个任务 id，直接标 success（后面再做真正队列/进度）
        task_id = uuid.uuid4().hex
        st = TaskStatusResponse(
            task_id=task_id,
            status=TaskStatus.success,
            message="stub: translate not implemented yet",
        )
        _TASKS[task_id] = st
        return TranslateResponse(ok=True, task=TaskRef(task_id=task_id, status=st.status, message=st.message))

    @r.get("/tasks/{task_id}", response_model=TaskStatusResponse)
    def task_status(task_id: str):
        st = _TASKS.get(task_id)
        if not st:
            raise HTTPException(status_code=404, detail="task not found")
        return st

    return r
