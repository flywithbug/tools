#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import argparse
from fnmatch import fnmatch

DEFAULT_EXCLUDES = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "__pycache__",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "out",
    ".next",
    ".DS_Store",
}


def is_hidden(name: str) -> bool:
    return name.startswith(".")


def should_exclude(
    name: str, exclude_set: set[str], exclude_patterns: list[str]
) -> bool:
    # ✅ 不展示隐藏文件/目录
    if is_hidden(name):
        return True
    # 原有排除逻辑
    if name in exclude_set:
        return True
    for p in exclude_patterns:
        if fnmatch(name, p):
            return True
    return False


def list_dir_tree(
    root: str, max_depth: int, exclude: set[str], exclude_patterns: list[str]
) -> str:
    root = os.path.abspath(root)
    lines: list[str] = []

    def walk(current: str, prefix: str, depth: int):
        if max_depth >= 0 and depth > max_depth:
            return

        try:
            entries = sorted(os.listdir(current))
        except PermissionError:
            lines.append(prefix + "⛔ (权限不足)")
            return

        filtered = []
        for e in entries:
            if should_exclude(e, exclude, exclude_patterns):
                continue
            full = os.path.join(current, e)
            if os.path.isdir(full) or os.path.isfile(full):  # ✅ 带上文件
                filtered.append(e)

        for i, name in enumerate(filtered):
            full = os.path.join(current, name)
            is_last = i == len(filtered) - 1
            branch = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")

            if os.path.isdir(full):
                lines.append(prefix + branch + name + "/")
                walk(full, next_prefix, depth + 1)
            else:
                lines.append(prefix + branch + name)

    lines.append(os.path.basename(root.rstrip(os.sep)) + "/")
    walk(root, "", 0)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="输出项目目录结构（带文件，隐藏项不展示）")
    ap.add_argument("path", nargs="?", default=".", help="项目根目录（默认当前目录）")
    ap.add_argument(
        "--max-depth", type=int, default=6, help="最大深度，-1 表示不限制（默认 6）"
    )
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="额外排除的名称（可重复使用），例如 --exclude .venv",
    )
    ap.add_argument(
        "--exclude-pattern",
        action="append",
        default=[],
        help="额外排除的通配符（可重复使用），例如 --exclude-pattern '*.log'",
    )
    args = ap.parse_args()

    exclude = set(DEFAULT_EXCLUDES)
    exclude.update(args.exclude)

    print(
        list_dir_tree(
            root=args.path,
            max_depth=args.max_depth,
            exclude=exclude,
            exclude_patterns=args.exclude_pattern,
        )
    )


if __name__ == "__main__":
    main()
