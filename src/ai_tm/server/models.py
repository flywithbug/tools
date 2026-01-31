from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class I18nFormat(str, Enum):
    strings = "strings"
    json = "json"


class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"
    canceled = "canceled"


class FixAction(str, Enum):
    sort = "sort"
    dedupe = "dedupe"
    remove_redundant = "remove_redundant"
    normalize = "normalize"  # 预留


class TranslateMode(str, Enum):
    incremental = "incremental"
    full = "full"


class Scope(str, Enum):
    core = "core"
    noncore = "noncore"
    all = "all"


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "box_ai_tm"
    version: str = "0.1.0"


class WorkspacePaths(BaseModel):
    workspace: str
    config_path: str
    targets_path: str


class WorkspaceFormatHint(BaseModel):
    format: I18nFormat
    detected_by: str = Field(..., description="识别依据")
    example_paths: List[str] = []


class WorkspaceInfoResponse(BaseModel):
    paths: WorkspacePaths
    config_exists: bool = False
    config_valid: bool = False
    formats: List[WorkspaceFormatHint] = []
    default_format: Optional[I18nFormat] = None

    base_locale: Optional[str] = None
    locales: List[str] = []
    core_locales: List[str] = []
    noncore_locales: List[str] = []


class LocaleFileInfo(BaseModel):
    locale: str
    path: str
    exists: bool
    mtime: Optional[float] = None
    key_count: int = 0


class AnalyzeSummary(BaseModel):
    base_locale: str = "en"
    locales: List[str] = []

    total_keys_by_locale: Dict[str, int] = {}
    missing_keys_by_locale: Dict[str, List[str]] = {}
    redundant_keys_by_locale: Dict[str, List[str]] = {}
    duplicate_keys_by_locale: Dict[str, List[str]] = {}

    changed_keys: List[str] = []  # 预留：增量翻译会用


class AnalyzeResponse(BaseModel):
    format: I18nFormat
    files: List[LocaleFileInfo] = []
    summary: AnalyzeSummary = AnalyzeSummary()


class FixRequest(BaseModel):
    format: I18nFormat
    actions: List[FixAction] = [FixAction.sort, FixAction.dedupe]
    scope: Scope = Scope.all
    keys: List[str] = []  # remove_redundant 时用；空=全部冗余


class FixResult(BaseModel):
    locale: str
    path: str
    changed: bool
    message: str = ""


class FixResponse(BaseModel):
    ok: bool
    format: I18nFormat
    results: List[FixResult] = []
    message: str = ""


class TranslateRequest(BaseModel):
    format: I18nFormat
    mode: TranslateMode
    scope: Scope = Scope.all
    base_locale: Optional[str] = None
    target_locales: List[str] = []
    keys: List[str] = []
    incremental_keys: List[str] = []


class TaskRef(BaseModel):
    task_id: str
    status: TaskStatus
    message: str = ""


class TranslateResponse(BaseModel):
    ok: bool
    task: TaskRef


class TaskProgress(BaseModel):
    total: int = 0
    done: int = 0
    current: str = ""


class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    progress: TaskProgress = TaskProgress()
    message: str = ""
    error: Optional[str] = None
