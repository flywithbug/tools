from __future__ import annotations

import fnmatch
import os
from pathlib import Path


DEFAULT_PATTERNS = [
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "dist",
    "build",
    "*.egg-info",
]


def _iter_targets(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)

        # 目录匹配
        for dn in list(dirnames):
            p = dpath / dn
            if any(fnmatch.fnmatch(dn, pat) for pat in DEFAULT_PATTERNS):
                yield p

        # 文件匹配
        for fn in filenames:
            p = dpath / fn
            if any(fnmatch.fnmatch(fn, pat) for pat in DEFAULT_PATTERNS):
                yield p


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        for child in path.iterdir():
            _remove(child)
        path.rmdir()
    else:
        path.unlink(missing_ok=True)


def cmd_clean(args) -> int:
    root = Path(args.path).expanduser().resolve()
    dry = bool(args.dry_run)

    if not root.exists():
        print(f"clean: path not found: {root}")
        return 2

    targets = sorted(set(_iter_targets(root)), key=lambda p: str(p))

    print("== box clean ==")
    print(f"root: {root}")
    print(f"dry-run: {dry}")
    print(f"match patterns: {', '.join(DEFAULT_PATTERNS)}")
    print(f"targets: {len(targets)}")

    for t in targets[:200]:
        print("  -", t)
    if len(targets) > 200:
        print(f"  ... ({len(targets)-200} more)")

    if dry:
        print("clean: dry-run done.")
        return 0

    # 真删前再提醒一下（但不搞交互卡住脚本）
    print("clean: deleting...")
    for t in targets:
        try:
            _remove(t)
        except Exception as e:
            print(f"warn: failed to remove {t}: {e}")

    print("clean: OK")
    return 0
