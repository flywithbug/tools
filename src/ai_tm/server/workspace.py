from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    root: Path
    config_path: Path
    targets_path: Path


def resolve_workspace(root: str) -> Workspace:
    r = Path(root).expanduser().resolve()
    # 先按约定：strings_i18n.yaml；后续再从 config 里读 targets 位置
    config_path = r / "strings_i18n.yaml"
    targets_path = r / "i18n"
    return Workspace(root=r, config_path=config_path, targets_path=targets_path)
