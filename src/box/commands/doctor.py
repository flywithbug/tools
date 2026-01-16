from __future__ import annotations

import os
import shutil
import site
import sys
from pathlib import Path


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def cmd_doctor(args) -> int:
    print("== box doctor ==")
    print(f"python: {sys.executable}")
    print(f"version: {sys.version.split()[0]}")

    pipx = _which("pipx")
    print(f"pipx: {pipx or 'NOT FOUND'}")

    box_path = _which("box")
    print(f"box: {box_path or 'NOT FOUND'}")

    # PATH 检查（常见 pipx/--user bin）
    path = os.environ.get("PATH", "")
    candidates = [
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".pyenv" / "shims"),
    ]
    missing = [c for c in candidates if c not in path.split(":") and Path(c).exists()]
    if missing:
        print("warn: PATH 可能缺少以下目录（会导致命令找不到）：")
        for m in missing:
            print(f"  - {m}")

    # site 目录
    try:
        print(f"user-site: {site.getusersitepackages()}")
    except Exception:
        pass

    # 配置目录建议
    cfg = Path.home() / ".config" / "box"
    print(f"config dir: {cfg} ({'exists' if cfg.exists() else 'missing'})")

    print("doctor: OK（如果有 NOT FOUND / warn，请按提示处理）")
    return 0
