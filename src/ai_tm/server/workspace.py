from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from .models import I18nFormat, WorkspaceFormatHint


@dataclass(frozen=True)
class Workspace:
    root: Path
    config_path: Path
    targets_path: Path


def resolve_workspace(root: str) -> Workspace:
    r = Path(root).expanduser().resolve()
    # 你项目里目前常见：strings_i18n.yaml
    config_path = r / "strings_i18n.yaml"
    # 默认 targets：i18n
    targets_path = r / "i18n"
    return Workspace(root=r, config_path=config_path, targets_path=targets_path)


def detect_formats(ws: Workspace) -> List[WorkspaceFormatHint]:
    hints: List[WorkspaceFormatHint] = []

    tp = ws.targets_path
    if not tp.exists():
        return hints

    # strings: *.lproj/*.strings
    example_strings: List[str] = []
    for p in tp.glob("*.lproj/*.strings"):
        example_strings.append(str(p))
        if len(example_strings) >= 3:
            break
    if example_strings:
        hints.append(
            WorkspaceFormatHint(
                format=I18nFormat.strings,
                detected_by="found *.lproj/*.strings",
                example_paths=example_strings,
            )
        )

    # json: 常见模式
    example_json: List[str] = []
    for pattern in ["*.json", "locales/*.json", "i18n/*.json"]:
        for p in tp.glob(pattern):
            example_json.append(str(p))
            if len(example_json) >= 3:
                break
        if len(example_json) >= 3:
            break

    if example_json:
        hints.append(
            WorkspaceFormatHint(
                format=I18nFormat.json,
                detected_by="found *.json (common patterns)",
                example_paths=example_json,
            )
        )

    return hints
