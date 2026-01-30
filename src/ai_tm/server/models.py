from __future__ import annotations
from pydantic import BaseModel


class Health(BaseModel):
    ok: bool = True
    service: str = "box_ai_tm"
    version: str = "0.1.0"


class WorkspaceInfo(BaseModel):
    workspace: str
    config_exists: bool
    config_path: str
    targets_exist: bool
    targets_path: str
