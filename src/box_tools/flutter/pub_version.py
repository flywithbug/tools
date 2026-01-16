from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

BOX_TOOL = {
    "id": "flutter.pub_version",          # 唯一标识（建议：类别.工具名）
    "name": "pub_version",                # 命令名（console script 名）
    "category": "flutter",                # 分类（可选）
    "summary": "升级 pubspec.yaml 的 version（支持交互选择 minor/patch）",
    "usage": [
        "pub_version",
        "pub_version minor",
        "pub_version patch --no-git",
        "pub_version minor --file path/to/pubspec.yaml",
    ],
    "options": [
        {"flag": "--file", "desc": "指定 pubspec.yaml 路径（默认 ./pubspec.yaml）"},
        {"flag": "--no-git", "desc": "只改版本号，不执行 git add/commit/push"},
    ],
    "examples": [
        {"cmd": "pub_version", "desc": "进入交互菜单选择升级级别"},
        {"cmd": "pub_version patch --no-git", "desc": "仅更新补丁号，不提交"},
    ],
    "docs": "src/box_tools/flutter/pub_version.md",  # 文档路径（相对仓库根）
}


def parse_version(version: str):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\+(.+))?$", version.strip())
    if not m:
        raise ValueError("version 格式应为 x.y.z 或 x.y.z+build")
    major, minor, patch = map(int, m.group(1, 2, 3))
    build = m.group(4)
    return major, minor, patch, build


def format_version(major: int, minor: int, patch: int, build: str | None):
    v = f"{major}.{minor}.{patch}"
    return f"{v}+{build}" if build else v


def bump(major: int, minor: int, patch: int, level: str):
    if level == "minor":
        return major, minor + 1, 0
    if level == "patch":
        return major, minor, patch + 1
    raise ValueError("level 必须是 minor 或 patch")


def read_version(content: str) -> str:
    m = re.search(r"^version:\s*([^\s]+)", content, re.MULTILINE)
    if not m:
        raise ValueError("pubspec.yaml 中未找到 version")
    return m.group(1)


def replace_version(content: str, new_version: str) -> str:
    return re.sub(
        r"^(version:\s*)([^\s]+)",
        lambda m: f"{m.group(1)}{new_version}",
        content,
        flags=re.MULTILINE,
    )

def git_commit(path: Path, version: str) -> bool:
    try:
        subprocess.run(["git", "add", str(path)], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: bump version to {version}"],
            check=True,
        )
        subprocess.run(["git", "push"], check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def choose_level_interactive(current_version: str, preview_minor: str, preview_patch: str) -> str:
    print(f"📦 当前版本: {current_version}")
    print("请选择升级级别：")
    print(f"1 - 次版本号（minor）升级 → {preview_minor}")
    print(f"2 - 补丁号（patch）升级 → {preview_patch}")
    print("0 - 退出")
    choice = input("请输入 0 / 1 / 2（或 q 退出）: ").strip().lower()

    if choice in ("0", "q", "quit", "exit"):
        raise SystemExit(0)  # 正常退出

    if choice == "1":
        return "minor"
    if choice == "2":
        return "patch"

    raise ValueError("无效输入（只能是 0/1/2 或 q）")

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pub_version",
        description="升级 Flutter pubspec.yaml 中的 version（支持交互选择）",
    )
    p.add_argument(
        "level",
        nargs="?",
        choices=["minor", "patch"],
        help="升级级别（不填则进入交互选择）",
    )
    p.add_argument("--file", default="pubspec.yaml", help="pubspec.yaml 路径（默认 ./pubspec.yaml）")
    p.add_argument("--no-git", action="store_true", help="不执行 git add/commit/push")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"❌ 文件不存在: {path}")
        return 2

    content = path.read_text(encoding="utf-8")
    try:
        old = read_version(content)
        major, minor, patch, build = parse_version(old)
    except Exception as e:
        print(f"❌ 解析失败: {e}")
        return 2

    # 预览两种升级后的版本（用于交互提示）
    minor_v = format_version(*bump(major, minor, patch, "minor"), build)
    patch_v = format_version(*bump(major, minor, patch, "patch"), build)

    level = args.level
    if not level:
        try:
            level = choose_level_interactive(old, minor_v, patch_v)
        except Exception as e:
            print(f"❌ {e}")
            return 2

    new_major, new_minor, new_patch = bump(major, minor, patch, level)
    new_version = format_version(new_major, new_minor, new_patch, build)

    print(f"🔼 {old} → {new_version}")

    path.write_text(replace_version(content, new_version), encoding="utf-8")
    print(f"✅ 已更新: {path}")

    if args.no_git:
        print("ℹ️ 已跳过 git 操作（--no-git）")
        return 0

    if git_commit(path, new_version):
        print("✅ git commit & push 完成")
        return 0

    print("⚠️ git 操作失败（版本已更新）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
